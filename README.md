# llm-safety

A lightweight logging proxy and daily safety-review system for a local vLLM server. Intended for parents who run a local LLM for their kids and want visibility into what is being discussed.

## What it does

- **Proxy** — sits between the client (e.g. Qwen Code) and your vLLM server, logging all conversations to a JSONL file. Internal agent scaffolding calls are filtered out; only the actual human-readable exchanges are kept. Each session (30 min inactivity = new session) is stored as a single up-to-date entry — earlier versions of the same conversation are replaced rather than duplicated.
- **Daily digest** — at a configured time each morning, reviews the previous day's sessions using the local model and writes a Markdown safety report.
- **Email** — sends the report to a configured address via SMTP.

## Requirements

**Python 3.10+** (uses `match`-free but relies on `X | Y` type hints and `date.fromisoformat`).

**python dependencies:**
(either in a venv or install these via your system packages via python3-whatever)
```
pip install httpx uvicorn starlette
```

**System dependencies:**
- [vLLM](https://docs.vllm.ai/en/latest/getting_started/installation.html) — must be running before you start the proxy. The model referenced by `REVIEW_MODEL` in `config.py` must be loaded.
- *(Optional)* An MTA or SMTP relay if you want email delivery. Options:
  - Local: `sudo apt install postfix` (choose "Internet Site" or "Local only")
  - Remote: any SMTP provider (Gmail, Fastmail, etc.) — see the email section in `config.py`
  - If `EMAIL_ENABLED = False`, reports are still written to `reports/` every morning — no mail setup needed.

## Setup

1. Copy and edit the config:

```python
# config.py
PROXY_PORT   = 8001
VLLM_URL     = "http://localhost:8000"
LOG_FILE     = "logs/kids_conversations.jsonl"
REVIEW_MODEL = "qwen3coder"
DIGEST_TIME  = "08:00"       # 24h local time
EMAIL_ENABLED = True
EMAIL_FROM   = "proxy@home.lan"
EMAIL_TO     = ["parent@example.com"]
SMTP_HOST    = "localhost"
SMTP_PORT    = 25
SMTP_USER    = ""
SMTP_PASSWORD = ""
SMTP_USE_TLS  = False
```

2. Point the client at the proxy port instead of vLLM directly. For Qwen Code, set the base URL to `http://<server>:8001`.

3. Start the daemon:

```bash
python run.py
```

This starts the proxy (with automatic restart on crash) and the daily digest scheduler in a single process. Stop it with Ctrl-C.

## Running as a background service

Two systemd unit files are included. Edit the `User=` and `WorkingDirectory=` / `ExecStart=` paths in whichever you use before installing.

### System service (starts at boot, runs as a specific user)

```bash
sudo cp llm-safety.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llm-safety
sudo journalctl -u llm-safety -f   # follow logs
```

### User service (starts on login, no root required)

```bash
mkdir -p ~/.config/systemd/user
cp llm-safety.user.service ~/.config/systemd/user/llm-safety.service
systemctl --user daemon-reload
systemctl --user enable --now llm-safety
journalctl --user -u llm-safety -f   # follow logs
```

To have the user service start at boot without needing to log in first (recommended for a server):

```bash
sudo loginctl enable-linger $USER
```

## Manual review

To review a specific date without waiting for the scheduler:

```bash
python review_sessions.py              # today
python review_sessions.py 2026-04-27  # specific date
```

Reports are written to `reports/YYYY-MM-DD_kids_local.md`. After a successful review, the reviewed sessions are moved from `logs/kids_conversations.jsonl` to `logs/kids_conversations.archive.jsonl` so reruns don't duplicate reviews. Sessions that failed to review (model error, network issue) stay in the main log and will be picked up on the next run.

## File layout

```
config.py                          — all settings
run.py                             — daemon (proxy watchdog + scheduler + email)
proxy.py                           — Starlette proxy
review_sessions.py                 — review runner (also usable standalone)
llm-safety.service                 — systemd system service unit
llm-safety.user.service            — systemd user service unit
logs/
  kids_conversations.jsonl         — pending (unreviewed) sessions
  kids_conversations.archive.jsonl — reviewed sessions
reports/
  YYYY-MM-DD_kids_local.md         — daily safety reports
```
