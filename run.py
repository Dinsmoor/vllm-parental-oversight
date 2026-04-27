"""
Daemon that keeps the vLLM proxy running and sends a daily safety-review
digest by email at the configured time.

Usage:
    python run.py
"""

import datetime
import email.mime.multipart
import email.mime.text
import smtplib
import ssl
import subprocess
import sys
import time
import threading

import config
import review_sessions


# ── Proxy watchdog ────────────────────────────────────────────────────────────

def _proxy_thread():
    cmd = [sys.executable, "proxy.py"]
    while True:
        print(f"[proxy] starting …")
        proc = subprocess.Popen(cmd)
        proc.wait()
        if proc.returncode == 0:
            print("[proxy] exited cleanly, stopping watchdog")
            break
        print(f"[proxy] crashed (exit {proc.returncode}), restarting in 5s …")
        time.sleep(5)


# ── Email ─────────────────────────────────────────────────────────────────────

def _smtp_host() -> str:
    """Return a bare hostname, stripping any protocol prefix the user may have included."""
    host = config.SMTP_HOST
    for prefix in ("smtps://", "smtp://", "https://", "http://"):
        if host.lower().startswith(prefix):
            return host[len(prefix):]
    return host

def send_report_email(report_path):
    body = report_path.read_text()
    report_date = report_path.stem.split("_")[0]  # e.g. "2026-04-27"

    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["Subject"] = f"Kids AI Safety Report — {report_date}"
    msg["From"]    = config.EMAIL_FROM
    msg["To"]      = ", ".join(config.EMAIL_TO)
    msg.attach(email.mime.text.MIMEText(body, "plain"))

    try:
        ctx = ssl.create_default_context()
        smtp = smtplib.SMTP(_smtp_host(), config.SMTP_PORT)
        smtp.ehlo()
        if config.SMTP_USE_TLS:
            smtp.starttls(context=ctx)
            smtp.ehlo()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.sendmail(config.EMAIL_FROM, config.EMAIL_TO, msg.as_string())
        smtp.quit()
        print(f"[digest] email sent to {config.EMAIL_TO}")
    except Exception as exc:
        print(f"[digest] email failed: {exc}")


# ── Email preflight ───────────────────────────────────────────────────────────

def check_email_auth():
    """Connect and authenticate against the SMTP server; raise on failure."""
    ctx = ssl.create_default_context()
    smtp = smtplib.SMTP(_smtp_host(), config.SMTP_PORT, timeout=10)
    try:
        smtp.ehlo()
        if config.SMTP_USE_TLS:
            smtp.starttls(context=ctx)
            smtp.ehlo()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
    finally:
        smtp.quit()


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _parse_digest_time() -> tuple[int, int]:
    h, m = config.DIGEST_TIME.split(":")
    return int(h), int(m)


def _scheduler_thread():
    target_hour, target_minute = _parse_digest_time()
    last_run_date = None

    while True:
        now = datetime.datetime.now()
        today = now.date()

        if (
            now.hour == target_hour
            and now.minute == target_minute
            and last_run_date != today
        ):
            last_run_date = today
            print(f"[digest] running review for {today} …")
            try:
                report_path = review_sessions.run(today)
                if report_path and config.EMAIL_ENABLED:
                    send_report_email(report_path)
            except Exception as exc:
                print(f"[digest] error: {exc}")

        # Sleep until the next minute boundary
        seconds_until_next_minute = 60 - now.second
        time.sleep(seconds_until_next_minute)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Starting kids LLM safety daemon")
    print(f"  proxy  → :{config.PROXY_PORT} → {config.VLLM_URL}")
    print(f"  digest → daily at {config.DIGEST_TIME} local time")
    print(f"  email  → {'enabled → ' + ', '.join(config.EMAIL_TO) if config.EMAIL_ENABLED else 'disabled'}")

    print("[vllm] checking connection …")
    try:
        import httpx
        resp = httpx.get(f"{config.VLLM_URL}/v1/models", timeout=5)
        resp.raise_for_status()
        available = [m["id"] for m in resp.json().get("data", [])]
    except Exception as exc:
        raise SystemExit(f"[vllm] not reachable at {config.VLLM_URL}: {exc}")

    if config.REVIEW_MODEL in available:
        print(f"[vllm] OK (model: {config.REVIEW_MODEL})")
    elif available:
        import review_sessions as _rs
        _rs.REVIEW_MODEL = available[0]
        print(f"[vllm] WARNING: '{config.REVIEW_MODEL}' not found, using '{available[0]}'")
        print(f"[vllm] available models: {', '.join(available)}")
    else:
        raise SystemExit("[vllm] no models loaded")

    if config.EMAIL_ENABLED:
        print("[email] checking SMTP credentials …")
        try:
            check_email_auth()
            print("[email] OK")
        except Exception as exc:
            raise SystemExit(f"[email] authentication failed: {exc}")

    proxy = threading.Thread(target=_proxy_thread, daemon=True, name="proxy")
    scheduler = threading.Thread(target=_scheduler_thread, daemon=True, name="scheduler")

    proxy.start()
    scheduler.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[run] shutting down")


if __name__ == "__main__":
    main()
