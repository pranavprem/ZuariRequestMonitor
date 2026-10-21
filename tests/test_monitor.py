"""Tests for parsing, the alert state machine, mail, and the offline command."""

from __future__ import annotations

import re
from email.message import EmailMessage
from pathlib import Path

import pytest

from monitor import (
    PROCESSES,
    RECOVERY_BODY,
    MonitorError,
    Reading,
    decide,
    fetch_process_page,
    format_alert,
    load_settings,
    main,
    parse_counts,
    send_email,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "process_execution_sample.html"
# Split so this file does not store the old iNotify host as one string.
LEGACY_HOST = b"119.226." + b"11.102"


def test_sample_page_counts_match_the_original_regex() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    raw = "".join(re.findall(r">[0-9]+<", html))
    expected = [int(part) for part in raw[1:-1].split("<>")]
    readings = parse_counts(html)
    assert [reading.count for reading in readings] == expected
    assert expected == [4, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert [reading.name for reading in readings] == [name for _, name in PROCESSES]


def test_parse_counts_rejects_a_missing_span() -> None:
    html = "<html><span id='ctl00_ContentPlaceHolder1_lblfertsocre'>4</span></html>"
    with pytest.raises(MonitorError, match="missing pending counts"):
        parse_counts(html)


def test_fixture_redacts_aspnet_session_tokens() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert 'id="__VIEWSTATE" value="redacted"' in text
    assert 'id="__EVENTVALIDATION" value="redacted"' in text
    assert "WebResource.axd?redacted" in text
    assert "wEPDwUJ" not in text


def test_workspace_does_not_contain_the_legacy_host() -> None:
    skipped = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__"}
    offenders: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in skipped for part in path.parts):
            continue
        if LEGACY_HOST in path.read_bytes():
            offenders.append(path)
    assert offenders == []


def test_quiet_when_every_count_is_within_the_threshold() -> None:
    readings = _readings(4, 50)
    decision = decide(readings, alerting=False, threshold=50)
    assert decision.send_alert is False
    assert decision.send_recovery is False
    assert decision.alerting is False


def test_first_breach_alerts_once_and_includes_every_high_count() -> None:
    readings = _readings(51, 0)
    readings = readings[:2] + (Reading(readings[2].name, 80),) + readings[3:]
    decision = decide(readings, alerting=False, threshold=50)
    assert decision.send_alert is True
    assert decision.send_recovery is False
    assert decision.alerting is True
    assert [reading.count for reading in decision.breaches] == [51, 80]
    assert "Fertilizer SO and delivery creation  51" in format_alert(decision.breaches)
    assert "Fertilizer SO delivery confirmation  80" in format_alert(decision.breaches)

    held = decide(readings, alerting=True, threshold=50)
    assert held.send_alert is False
    assert held.alerting is True


def test_breach_on_the_last_process_does_not_recover_in_the_same_check() -> None:
    readings = _readings(0, 0)
    readings = readings[:-1] + (Reading(readings[-1].name, 51),)
    decision = decide(readings, alerting=False, threshold=50)
    assert decision.send_alert is True
    assert decision.send_recovery is False
    assert decision.alerting is True


def test_recovery_is_sent_only_after_a_later_quiet_check() -> None:
    high = _readings(60, 0)
    quiet = _readings(10, 0)
    entered = decide(high, alerting=False, threshold=50)
    recovered = decide(quiet, alerting=entered.alerting, threshold=50)
    assert recovered.send_recovery is True
    assert recovered.send_alert is False
    assert recovered.alerting is False
    assert "Values are normal again" in RECOVERY_BODY


def test_load_settings_reads_recipients_and_rejects_a_bad_interval() -> None:
    settings = load_settings(
        {
            "INOTIFY_URL": "https://example.test/iNotify",
            "ALERT_TO": "one@example.test, two@example.test",
            "ALERT_FROM": "alerts@example.test",
            "ALERT_THRESHOLD": "40",
            "POLL_INTERVAL_SECONDS": "60",
        }
    )
    assert settings.alert_to == ("one@example.test", "two@example.test")
    assert settings.threshold == 40
    assert settings.poll_seconds == 60
    assert settings.smtp_host == "smtp.gmail.com"
    with pytest.raises(MonitorError, match="POLL_INTERVAL_SECONDS"):
        load_settings({"POLL_INTERVAL_SECONDS": "soon"})


def test_fetch_posts_the_login_form_and_follows_process_execution() -> None:
    page = FIXTURE.read_text(encoding="utf-8")
    login_html = """
    <html><body>
      <form action="login.aspx" method="post">
        <input type="hidden" name="__VIEWSTATE" value="abc" />
        <input name="txtUserName" value="" />
        <input type="password" name="txtPassword" value="" />
        <input type="submit" name="btnLogin" value="Login" />
      </form>
    </body></html>
    """
    home_html = '<html><a href="frmProcessExecution.aspx">Process Execution</a></html>'
    session = _FakeSession(login_html, home_html, page)

    fetched = fetch_process_page(
        session, "https://example.test/iNotify", "user", "secret"
    )

    assert "lblfertsocre" in fetched
    posted_url, posted = session.posts[0]
    assert posted_url == "https://example.test/iNotify/login.aspx"
    assert posted["txtUserName"] == "user"
    assert posted["txtPassword"] == "secret"
    assert posted["__VIEWSTATE"] == "abc"
    assert posted["btnLogin"] == "Login"
    assert session.gets[-1].endswith("/frmProcessExecution.aspx")


def test_fetch_accepts_a_response_that_is_already_the_process_page() -> None:
    page = FIXTURE.read_text(encoding="utf-8")
    login_html = """
    <form action="login.aspx">
      <input name="txtUserName" />
      <input name="txtPassword" />
    </form>
    """
    session = _FakeSession(login_html, page, "<html></html>")
    fetched = fetch_process_page(
        session, "https://example.test/iNotify", "user", "secret"
    )
    assert "lblfertsocre" in fetched
    assert len(session.gets) == 1


def test_fetch_rejects_a_javascript_postback_link() -> None:
    login_html = """
    <form action="login.aspx">
      <input name="txtUserName" />
      <input name="txtPassword" />
    </form>
    """
    home_html = "<a href=\"javascript:__doPostBack('menu','')\">Process Execution</a>"
    session = _FakeSession(login_html, home_html, "<html></html>")
    with pytest.raises(MonitorError, match="JavaScript postback"):
        fetch_process_page(session, "https://example.test/iNotify", "user", "secret")


def test_send_email_uses_starttls_and_every_recipient() -> None:
    settings = load_settings(
        {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "2525",
            "SMTP_USERNAME": "mailer",
            "SMTP_PASSWORD": "mail-secret",
            "ALERT_FROM": "alerts@example.test",
            "ALERT_TO": "a@example.test, b@example.test",
        }
    )
    sent: list[EmailMessage] = []
    logins: list[tuple[str, str]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert host == "smtp.example.test"
            assert port == 2525
            assert timeout == 30

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def ehlo(self) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            logins.append((username, password))

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    send_email(
        settings,
        "iNotify pending count alert",
        "FertilizerDSP creation  60\n",
        smtp_factory=FakeSMTP,
    )
    assert logins == [("mailer", "mail-secret")]
    assert sent[0]["From"] == "alerts@example.test"
    assert sent[0]["To"] == "a@example.test, b@example.test"
    assert sent[0]["Subject"] == "iNotify pending count alert"
    assert "FertilizerDSP creation  60" in sent[0].get_content()


def test_offline_sample_prints_counts_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--html", str(FIXTURE)], env={})
    captured = capsys.readouterr()
    assert code == 0
    assert "Fertilizer SO and delivery creation: 4" in captured.out
    assert "Fertilizer SO delivery confirmation: 1" in captured.out
    assert "alerting=0" in captured.out
    assert captured.err == ""


def test_dry_run_prints_an_alert_without_smtp(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--html", str(FIXTURE), "--threshold", "3", "--dry-run"], env={})
    captured = capsys.readouterr()
    assert code == 0
    assert "--- iNotify pending count alert ---" in captured.out
    assert "Fertilizer SO and delivery creation  4" in captured.out
    assert "Agri SO and delivery creation" not in captured.out.split("---", 1)[1]
    assert captured.err == ""


def test_live_check_requires_a_url(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--once"], env={})
    captured = capsys.readouterr()
    assert code == 2
    assert "INOTIFY_URL" in captured.err


def test_alert_without_mail_configuration_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--html", str(FIXTURE), "--threshold", "3"], env={})
    captured = capsys.readouterr()
    assert code == 1
    assert "Fertilizer SO and delivery creation  4" in captured.out
    assert "Email was not sent" in captured.err


def test_alert_text_uses_the_original_two_space_layout() -> None:
    message = format_alert((Reading("Fertilizer SO and delivery creation", 51),))
    assert message == "Fertilizer SO and delivery creation  51\n"


def _readings(first: int, rest: int) -> tuple[Reading, ...]:
    counts = [first, *([rest] * (len(PROCESSES) - 1))]
    return tuple(
        Reading(name, count) for (_, name), count in zip(PROCESSES, counts, strict=True)
    )


class _Response:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, login_html: str, home_html: str, page_html: str) -> None:
        self.login_html = login_html
        self.home_html = home_html
        self.page_html = page_html
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, timeout: int) -> _Response:
        self.gets.append(url)
        if len(self.gets) == 1:
            return _Response(self.login_html, url + "/")
        return _Response(self.page_html, url)

    def post(self, url: str, data: dict[str, str], timeout: int) -> _Response:
        self.posts.append((url, data))
        return _Response(self.home_html, "https://example.test/iNotify/home.aspx")
