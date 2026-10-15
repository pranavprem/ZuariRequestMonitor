# Agent guide

Zuari Request Monitor is a single-command Python program. It logs into the iNotify Process Execution page, reads fifteen pending counts, and sends one email when a count crosses the threshold and one more when they all come back down.

Read this file before changing the monitor. [CLAUDE.md](CLAUDE.md) points here.

## Architecture

`monitor.py` is the whole program. Keep it that way until a second responsibility shows up.

- `parse_counts` reads the fifteen ASP.NET span ids in `PROCESSES`. The names are the 2013 alert labels, in the order they appear on the page.
- `decide` is the alert state machine. It is edge-triggered: one alert when the poll enters the high state, nothing while it stays high, one recovery note when a later poll is fully quiet. A count equal to the threshold does not alert. Only a greater count does.
- `fetch_process_page` posts the first login form (`txtUserName`, `txtPassword`, plus hidden fields such as view state) and follows the `Process Execution` link. It does not run JavaScript postbacks.
- `send_email` opens an SMTP connection with STARTTLS only when a message is actually going out.
- `main` is the command. `--html` checks a saved page and does not touch the network. A live check loops until Ctrl-C, with `--once` for a single poll.

The saved page from the internship is `fixtures/process_execution_sample.html`. It is a fixture, not a runtime dump. Session tokens in it are replaced with `redacted`.

## Commands

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python monitor.py --html fixtures/process_execution_sample.html
python monitor.py --html fixtures/process_execution_sample.html --threshold 3 --dry-run

ruff check .
ruff format .
pytest
```

Live checks need the variables in `.env.example`. Copy it to `.env` (gitignored) and export it yourself, for example `set -a && source .env && set +a`. The program does not load `.env` on its own.

CI is `.github/workflows/ci.yml`: Python 3.12, ruff, ruff format, pytest.

## Conventions

- Python 3.12. Type hints on public functions. Format and lint with ruff (`pyproject.toml`).
- Pin new dependencies with `==` in `requirements.txt` or `requirements-dev.txt`.
- Pure functions (`parse_counts`, `decide`, `load_settings`) hold the behavior tests care about. The poll loop stays thin.
- Alert text stays close to the 2013 wording: `Process name  count` with two spaces, and `Values are normal again` for the recovery note.
- There is no license file. Do not add one.
- Do not add a web UI, a database, or a second mail backend.

## Gotchas

- The 2013 script cleared the alert flag in the same pass that raised it, so it could never stay in the alerting state, and it built the email with a unary plus on a string (`+usestr`), which raises `TypeError` before SMTP is used. `decide` and `send_email` are the fixes. Do not fold the recovery check back into the breach loop.
- The old extractor was the regex `>[0-9]+<` over the whole page. On the saved fixture that happens to be the fifteen counts, and a test locks that in. New pages should be read by span id. A missing span is an error, not a zero.
- Login needs the ASP.NET hidden fields from the live form. Dropping them will usually make the post fail.
- SMTP is STARTTLS on `SMTP_PORT` (Gmail's 587). Implicit TLS on 465 is not implemented.
- The default poll interval is 300 seconds. The original loop had no sleep and would have hammered the site.
- `zuaa.html` was the file the old script overwrote on every pass. It is gitignored. A live check writes a page only when `--save-html` is set.
- Keep the iNotify host, usernames, passwords, and SMTP secrets in environment variables. Do not put them back in source, fixtures, docs, or tests.
- Commits from 2013 on `master` still contain a company host address and the unredacted page, including ASP.NET view state and event validation. Do not copy those values back into the tree. This branch does not rewrite that history.
- `Monitor.py` and `monitor.py` cannot both exist. macOS and Windows filesystems are case-insensitive, so the entry point is only `monitor.py`.
