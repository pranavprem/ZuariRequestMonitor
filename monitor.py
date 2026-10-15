"""Watch Zuari iNotify pending counts and email when they cross a threshold.

Python 3 refresh of Pranav Prem's 2013 internship script. The host, account,
and mail settings come from the environment. A saved Process Execution page
can be checked with --html, which is how the tool runs without that site.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_THRESHOLD = 50
DEFAULT_POLL_SECONDS = 300
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = "zuari-request-monitor"
ALERT_SUBJECT = "iNotify pending count alert"
RECOVERY_SUBJECT = "iNotify pending counts are normal again"
RECOVERY_BODY = "Values are normal again\n"
PROCESS_LINK_TEXT = "Process Execution"

# Span ids are the ASP.NET controls on frmProcessExecution.aspx.
# Names are the labels the 2013 script put in the alert, in page order.
PROCESSES: tuple[tuple[str, str], ...] = (
    (
        "ctl00_ContentPlaceHolder1_lblfertsocre",
        "Fertilizer SO and delivery creation",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblagreesocre",
        "Agri SO and delivery creation",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblfertsodelconfer",
        "Fertilizer SO delivery confirmation",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblagreesodelconfer",
        "Agri SO delivery confirmation",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblfertstacre",
        "Fertilizer STA Creation",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblagreestacreation",
        "Agri STA Creation",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblfertstadel",
        "Fertilizer STA Delivery",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblagreestadel",
        "Agri STA delivery",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblfertstagr",
        "Fertilizer STA goods receipt",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblagreestagr",
        "Agri STA goods receipt",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblfertdspcre",
        "FertilizerDSP creation",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblprodconsum",
        "agri production consumption",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblfertdspdel",
        "Fertilizer DSP delivery",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblrawmatgr",
        "agri raw material grr",
    ),
    (
        "ctl00_ContentPlaceHolder1_lblfertdspgr",
        "Fertilizer DSP goods receipt",
    ),
)


class MonitorError(Exception):
    """A check failed, or the configuration cannot run one."""


@dataclass(frozen=True)
class Reading:
    name: str
    count: int


@dataclass(frozen=True)
class Decision:
    """Edge-triggered alert state carried from one poll to the next."""

    alerting: bool
    send_alert: bool
    send_recovery: bool
    breaches: tuple[Reading, ...]


@dataclass(frozen=True)
class Settings:
    inotify_url: str
    username: str
    password: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    alert_from: str
    alert_to: tuple[str, ...]
    threshold: int
    poll_seconds: int

    @property
    def mail_configured(self) -> bool:
        return bool(self.alert_from and self.alert_to and self.smtp_host)


def parse_counts(html: str) -> tuple[Reading, ...]:
    """Read the fifteen pending-count spans from a Process Execution page."""

    soup = BeautifulSoup(html, "html.parser")
    readings: list[Reading] = []
    missing: list[str] = []
    for span_id, name in PROCESSES:
        span = soup.find(id=span_id)
        text = span.get_text(strip=True) if span is not None else ""
        if not text.isdigit():
            missing.append(span_id)
            continue
        readings.append(Reading(name, int(text)))
    if missing:
        joined = ", ".join(missing)
        raise MonitorError(
            f"Process Execution page is missing pending counts: {joined}"
        )
    return tuple(readings)


def decide(readings: tuple[Reading, ...], alerting: bool, threshold: int) -> Decision:
    """Return the alert transition for this poll.

    The 2013 loop cleared its flag in the same pass that raised it, because
    the recovery check reused a loop index that had already walked to the
    last row. A breach has to leave alerting set. The recovery note waits
    for a later poll where every count is back within the threshold.
    Counts equal to the threshold stay quiet; only a greater count alerts.
    """

    breaches = tuple(reading for reading in readings if reading.count > threshold)
    if breaches and not alerting:
        return Decision(True, True, False, breaches)
    if not breaches and alerting:
        return Decision(False, False, True, ())
    return Decision(alerting, False, False, breaches)


def format_alert(breaches: tuple[Reading, ...]) -> str:
    lines = [f"{reading.name}  {reading.count}" for reading in breaches]
    return "\n".join(lines) + "\n"


def format_status(readings: tuple[Reading, ...], alerting: bool) -> str:
    lines = [f"{reading.name}: {reading.count}" for reading in readings]
    lines.append(f"alerting={1 if alerting else 0}")
    return "\n".join(lines)


def _required_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise MonitorError(f"{name} must be an integer, got {raw!r}") from exc


def load_settings(env: Mapping[str, str]) -> Settings:
    threshold = _required_int(env, "ALERT_THRESHOLD", DEFAULT_THRESHOLD)
    poll_seconds = _required_int(env, "POLL_INTERVAL_SECONDS", DEFAULT_POLL_SECONDS)
    smtp_port = _required_int(env, "SMTP_PORT", DEFAULT_SMTP_PORT)
    if poll_seconds < 1:
        raise MonitorError("POLL_INTERVAL_SECONDS must be at least 1")
    if smtp_port < 1:
        raise MonitorError("SMTP_PORT must be at least 1")
    recipients = tuple(
        part.strip() for part in env.get("ALERT_TO", "").split(",") if part.strip()
    )
    return Settings(
        inotify_url=env.get("INOTIFY_URL", "").strip(),
        username=env.get("INOTIFY_USERNAME", ""),
        password=env.get("INOTIFY_PASSWORD", ""),
        smtp_host=env.get("SMTP_HOST", "").strip() or DEFAULT_SMTP_HOST,
        smtp_port=smtp_port,
        smtp_username=env.get("SMTP_USERNAME", ""),
        smtp_password=env.get("SMTP_PASSWORD", ""),
        alert_from=env.get("ALERT_FROM", "").strip(),
        alert_to=recipients,
        threshold=threshold,
        poll_seconds=poll_seconds,
    )


def _form_payload(form: Any) -> dict[str, str]:
    payload: dict[str, str] = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if not name:
            continue
        field_type = (field.get("type") or "text").lower()
        if field_type in {"submit", "button", "image", "reset"}:
            continue
        if field_type in {"checkbox", "radio"} and field.get("checked") is None:
            continue
        payload[str(name)] = field.get("value") or ""
    submit = form.find("input", attrs={"type": "submit"})
    if submit is not None and submit.get("name"):
        payload[str(submit["name"])] = submit.get("value") or ""
    return payload


def _process_link(soup: BeautifulSoup) -> str | None:
    for link in soup.find_all("a"):
        if link.get_text(strip=True) != PROCESS_LINK_TEXT:
            continue
        href = link.get("href")
        if not href:
            continue
        href = str(href)
        if href.lower().startswith("javascript:"):
            raise MonitorError(
                "Process Execution link is a JavaScript postback; "
                "this monitor does not run it."
            )
        return href
    return None


def fetch_process_page(session: Any, url: str, username: str, password: str) -> str:
    """Log in and return the Process Execution HTML."""

    if not url:
        raise MonitorError("INOTIFY_URL is not set")
    if username == "" or password == "":
        raise MonitorError(
            "INOTIFY_USERNAME and INOTIFY_PASSWORD are required for a live check"
        )

    try:
        login = session.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        login.raise_for_status()
        soup = BeautifulSoup(login.text, "html.parser")
        form = soup.find("form")
        if form is None:
            raise MonitorError("login page has no form")
        payload = _form_payload(form)
        if "txtUserName" not in payload or "txtPassword" not in payload:
            raise MonitorError("login form is missing txtUserName or txtPassword")
        payload["txtUserName"] = username
        payload["txtPassword"] = password
        action = urljoin(login.url, str(form.get("action") or ""))
        submitted = session.post(action, data=payload, timeout=HTTP_TIMEOUT_SECONDS)
        submitted.raise_for_status()
        home = BeautifulSoup(submitted.text, "html.parser")
        if home.find(id=PROCESSES[0][0]) is not None:
            return str(submitted.text)
        href = _process_link(home)
        if href is None:
            raise MonitorError(
                "Process Execution link not found. Check the account and INOTIFY_URL."
            )
        page = session.get(urljoin(submitted.url, href), timeout=HTTP_TIMEOUT_SECONDS)
        page.raise_for_status()
        return str(page.text)
    except requests.RequestException as exc:
        raise MonitorError(f"could not reach iNotify: {exc}") from exc


def send_email(
    settings: Settings,
    subject: str,
    body: str,
    *,
    smtp_factory: Callable[..., Any] | None = None,
) -> None:
    if not settings.mail_configured:
        raise MonitorError("Set ALERT_FROM and ALERT_TO before sending mail.")
    message = EmailMessage()
    message["From"] = settings.alert_from
    message["To"] = ", ".join(settings.alert_to)
    message["Subject"] = subject
    message.set_content(body)

    factory = smtp_factory or smtplib.SMTP
    with factory(
        settings.smtp_host, settings.smtp_port, timeout=HTTP_TIMEOUT_SECONDS
    ) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


def _deliver(settings: Settings, subject: str, body: str, dry_run: bool) -> None:
    if dry_run:
        print(f"--- {subject} ---")
        print(body, end="")
        return
    if not settings.mail_configured:
        print(f"--- {subject} ---")
        print(body, end="")
        raise MonitorError(
            "Email was not sent. Set ALERT_FROM and ALERT_TO "
            "(and SMTP_USERNAME and SMTP_PASSWORD when the server requires a login)."
        )
    send_email(settings, subject, body)
    print(f"sent: {subject}")


def check_once(html: str, *, alerting: bool, settings: Settings, dry_run: bool) -> bool:
    readings = parse_counts(html)
    decision = decide(readings, alerting, settings.threshold)
    print(format_status(readings, decision.alerting))
    if decision.send_alert:
        _deliver(settings, ALERT_SUBJECT, format_alert(decision.breaches), dry_run)
    elif decision.send_recovery:
        _deliver(settings, RECOVERY_SUBJECT, RECOVERY_BODY, dry_run)
    return decision.alerting


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Email when an iNotify pending count exceeds the threshold."
    )
    parser.add_argument(
        "--html",
        type=Path,
        help="Parse a saved Process Execution page instead of logging in.",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single check and exit."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the email that would be sent without contacting an SMTP server.",
    )
    parser.add_argument(
        "--save-html",
        type=Path,
        help="Write the fetched Process Execution page to this path.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help=(
            f"Pending count that triggers an alert, strictly greater than this value "
            f"(default {DEFAULT_THRESHOLD}, or ALERT_THRESHOLD)."
        ),
    )
    return parser


def _run_offline(args: argparse.Namespace, settings: Settings) -> int:
    try:
        html = args.html.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read {args.html}: {exc}", file=sys.stderr)
        return 1
    try:
        check_once(html, alerting=False, settings=settings, dry_run=args.dry_run)
    except MonitorError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _run_live(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.inotify_url:
        raise MonitorError(
            "Set INOTIFY_URL, or pass --html with a saved Process Execution page."
        )
    if settings.username == "" or settings.password == "":
        raise MonitorError(
            "Set INOTIFY_USERNAME and INOTIFY_PASSWORD for a live check."
        )

    alerting = False
    session = make_session()
    while True:
        try:
            html = fetch_process_page(
                session, settings.inotify_url, settings.username, settings.password
            )
            if args.save_html is not None:
                args.save_html.write_text(html, encoding="utf-8")
            alerting = check_once(
                html, alerting=alerting, settings=settings, dry_run=args.dry_run
            )
        except MonitorError as exc:
            print(str(exc), file=sys.stderr)
            if args.once:
                return 1
        except OSError as exc:
            print(f"could not save HTML: {exc}", file=sys.stderr)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(settings.poll_seconds)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = os.environ if env is None else env
    try:
        settings = load_settings(source)
        if args.threshold is not None:
            settings = replace(settings, threshold=args.threshold)
        if args.html is not None:
            return _run_offline(args, settings)
        return _run_live(args, settings)
    except MonitorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
