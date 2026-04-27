# ── Proxy ─────────────────────────────────────────────────────────────────────
# PROXY_PORT: the port this proxy listens on. Point your LLM client here
#   instead of directly at vLLM. For Qwen Code set the API base URL to
#   http://<this-machine>:<PROXY_PORT>
# VLLM_URL: find this in your vLLM startup output ("Uvicorn running on ...")
PROXY_PORT = 8001
VLLM_URL   = "http://localhost:8000"
LOG_FILE   = "logs/kids_conversations.jsonl"

# ── Review ────────────────────────────────────────────────────────────────────
# REVIEW_MODEL: must match the model ID shown by `curl http://<VLLM_URL>/v1/models`
REVIEW_MODEL = "qwen3coder"
REPORTS_DIR  = "reports"

# ── Daily digest ──────────────────────────────────────────────────────────────
DIGEST_TIME = "08:00"   # 24-hour local time HH:MM — when to run the daily review

# ── Email ─────────────────────────────────────────────────────────────────────
# For a local MTA (Postfix/Sendmail) use SMTP_HOST="localhost", port 25,
# no user/password, SMTP_USE_TLS=False.
#
# For Gmail (or any provider that requires authentication):
#   SMTP_HOST     = "smtp.gmail.com"
#   SMTP_PORT     = 587
#   SMTP_USER     = "you@gmail.com"
#   SMTP_PASSWORD = "<app password>"   # generate at myaccount.google.com/apppasswords
#   SMTP_USE_TLS  = True
#
# SMTP_USE_TLS uses STARTTLS (RFC 3207): the connection starts unencrypted on
# port 587 and is upgraded to TLS before credentials are sent. This is different
# from implicit TLS (port 465 / SMTPS) which is not supported here.
EMAIL_ENABLED = False
EMAIL_FROM    = "address@mail.tld"
EMAIL_TO      = ["address@mail.tld"]   # list of recipient addresses
SMTP_HOST     = "mail.mail.tld"
SMTP_PORT     = 587
SMTP_USER     = "address@mail.tld"        # leave empty if no auth required
SMTP_PASSWORD = "secretpassword"
SMTP_USE_TLS  = True
