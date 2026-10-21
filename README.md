# Zuari Request Monitor

A small command-line monitor that reads pending-document counts from Zuari's iNotify **Process Execution** page and sends an email when a count crosses a threshold.

Pranav Prem wrote it during an internship at Zuari Industries. The first commit is 30 December 2013: a [mechanize](https://github.com/python-mechanize/mechanize) script that logged into iNotify and saved the page. The next day he added a Gmail alert. The README landed in September 2016. Nothing in the repository changed after that.

iNotify was an ASP.NET app (the saved page credits Mobien Technologies, 2009). The screen lists fertilizer and agribusiness steps — sales orders, deliveries, STA, and DSP — each with a pending count. The internship task was to watch those counts and tell people when one piled up above 50, then tell them again when the counts were back to normal.

## How it works

1. Log in. The monitor GETs `INOTIFY_URL`, posts the first form (`txtUserName`, `txtPassword`, and the hidden fields already on the form), and follows the **Process Execution** link. A `requests` session keeps the ASP.NET login cookie.
2. Read fifteen pending counts from known span ids on that page, in the same order as the 2013 script. The names in the email are the original labels (`Fertilizer SO and delivery creation`, and so on).
3. Compare each count with the threshold. A count **greater than** 50 alerts. A count of 50 does not.
4. Send mail only on a change of state:
   - the first poll that shows any count over the threshold sends one email listing every count that is over
   - later polls that are still over send nothing
   - the first later poll where every count is back within the threshold sends `Values are normal again`

The 2013 loop tried to do that, but the recovery check ran in the same pass as the alert and cleared the flag immediately. The email builder also applied a unary `+` to the message string, which raises `TypeError` in Python, so the SMTP call never ran. Both of those are fixed. Mail is sent with `smtplib` over STARTTLS, which is what the Gmail setup was aiming at.

The old script also scraped with the regex `>[0-9]+<`. On the saved page that happens to be these fifteen counts. The monitor now reads the span ids, so an unrelated number elsewhere on the page is not treated as a pending count.

## How to run it today

Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The saved Process Execution page is enough to see the monitor work. No account and no network:

```bash
python monitor.py --html fixtures/process_execution_sample.html
```

```
Fertilizer SO and delivery creation: 4
Agri SO and delivery creation: 0
Fertilizer SO delivery confirmation: 1
Agri SO delivery confirmation: 0
Fertilizer STA Creation: 0
Agri STA Creation: 0
Fertilizer STA Delivery: 0
Agri STA delivery: 0
Fertilizer STA goods receipt: 0
Agri STA goods receipt: 0
FertilizerDSP creation: 0
agri production consumption: 0
Fertilizer DSP delivery: 0
agri raw material grr: 0
Fertilizer DSP goods receipt: 0
alerting=0
```

That snapshot is under the threshold, so there is no email. Drop the threshold to see the alert text without sending anything:

```bash
python monitor.py --html fixtures/process_execution_sample.html --threshold 3 --dry-run
```

The alert section is:

```
--- iNotify pending count alert ---
Fertilizer SO and delivery creation  4
```

### Live checks

Copy `.env.example` to `.env`, fill it in, and export it. The program does not read `.env` by itself.

```bash
set -a && source .env && set +a
python monitor.py --once          # one poll
python monitor.py                 # poll every POLL_INTERVAL_SECONDS (default 300)
python monitor.py --save-html zuaa.html
```

`zuaa.html` is where the 2013 script wrote every page it fetched. That path is gitignored. Pass `--save-html` when you want a copy.

Gmail still fits the original design: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, an app password in `SMTP_PASSWORD`, and `ALERT_FROM` set to that mailbox. `ALERT_TO` is a comma-separated list, which is the "email several people" behavior the old README described. The connection is STARTTLS. Port 465 implicit TLS is not implemented.

## Tech stack

| Then (2013) | Now |
| --- | --- |
| Python 2 | Python 3.12 |
| mechanize | requests |
| regex over the raw page | Beautiful Soup, by span id |
| smtplib, login at import | smtplib STARTTLS, only when a message is sent |
| no tests | pytest, ruff, GitHub Actions |

Runtime pins are in `requirements.txt`. Dev pins (pytest, ruff) are in `requirements-dev.txt`.

## Project structure

```
monitor.py                                 command, fetch, parsing, alerts, mail
fixtures/process_execution_sample.html     2013 Process Execution page, tokens redacted
tests/test_monitor.py                      parsing, state machine, login, offline command
requirements.txt
requirements-dev.txt
pyproject.toml                             ruff and pytest
.github/workflows/ci.yml                   lint, format, test on Python 3.12
.env.example                               host, account, and mail variables
AGENTS.md                                  guide for changing the code
```

The interface is the command line. The iNotify screen it watches is the HTML fixture.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest
```

## Configuration

| Variable | Role | Default |
| --- | --- | --- |
| `INOTIFY_URL` | Login page for iNotify | none; required for a live check |
| `INOTIFY_USERNAME` | Form field `txtUserName` | none |
| `INOTIFY_PASSWORD` | Form field `txtPassword` | none |
| `SMTP_HOST` | Mail server | `smtp.gmail.com` |
| `SMTP_PORT` | STARTTLS port | `587` |
| `SMTP_USERNAME` | SMTP login; skipped when empty | none |
| `SMTP_PASSWORD` | SMTP password | none |
| `ALERT_FROM` | From address | none |
| `ALERT_TO` | Recipients, comma-separated | none |
| `ALERT_THRESHOLD` | Alert when a count is greater than this | `50` |
| `POLL_INTERVAL_SECONDS` | Delay between live polls | `300` |

Hostnames, usernames, and passwords stay in the environment. Do not commit them.
