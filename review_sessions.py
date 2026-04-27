"""
Reads kids_conversations.jsonl and asks the local Qwen model to safety-review
each session, writing a Markdown report to reports/YYYY-MM-DD_kids_local.md.
Reviewed sessions are moved to kids_conversations.archive.jsonl.

Usage:
    python review_sessions.py            # reviews today's sessions
    python review_sessions.py 2026-04-27 # reviews a specific date
"""

import json
import sys
import textwrap
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

import config

LOG_PATH     = Path(config.LOG_FILE)
REPORTS_DIR  = Path(config.REPORTS_DIR)
VLLM_URL     = config.VLLM_URL
REVIEW_MODEL = config.REVIEW_MODEL

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a child-safety reviewer. A parent has asked you to review a transcript
    of their child's conversation with an AI assistant running locally on their home
    network. Your job is to help the parent understand what their child discussed
    and flag any concerns — not to censor, but to inform.

    Return your analysis in this exact Markdown format:

    ## Summary
    (2–4 sentences describing what the child was asking about)

    ## Topics Covered
    - bullet list of main subject areas

    ## Safety Flags
    List any safety flags if present, such as distress or self harm, requests for
    dangerous information, sexual content, contact with strangers, or expressions
    of positivity or interactions with LGBT propaganda.

    ## Risk Level
    One of: 🟢 Low  |  🟡 Medium  |  🔴 High
    Followed by one sentence of justification.
""")


def load_sessions(target_date: date) -> dict[str, list[dict]]:
    sessions: dict[str, list[dict]] = defaultdict(list)
    if not LOG_PATH.exists():
        return sessions
    with LOG_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("timestamp", "")
            try:
                entry_date = datetime.fromisoformat(ts).date()
            except (ValueError, TypeError):
                continue
            if entry_date == target_date:
                sessions[entry["session_id"]].append(entry)
    return sessions


def build_transcript(entries: list[dict]) -> str:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        for turn in entry.get("conversation", []):
            role = turn.get("role", "unknown").upper()
            text = turn.get("text", "").strip()
            key = (role, text)
            if text and key not in seen:
                seen.add(key)
                lines.append(f"[{role}] {text}")
        reply = entry.get("reply", "")
        if reply:
            lines.append(f"[ASSISTANT] {reply}")
    return "\n\n".join(lines) if lines else "(empty transcript)"


def review_session(transcript: str) -> str:
    payload = {
        "model": REVIEW_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                "Please review the following child-AI conversation transcript "
                "and produce your safety analysis.\n\n---\n"
                f"{transcript}\n---"
            )},
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
    }
    resp = httpx.post(f"{VLLM_URL}/v1/chat/completions", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def archive_sessions(reviewed_ids: set[str]):
    if not LOG_PATH.exists() or not reviewed_ids:
        return
    archive_path = LOG_PATH.with_suffix(".archive.jsonl")
    keep_lines: list[str] = []
    archive_lines: list[str] = []
    with LOG_PATH.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                keep_lines.append(stripped)
                continue
            if entry.get("session_id") in reviewed_ids:
                archive_lines.append(stripped)
            else:
                keep_lines.append(stripped)
    if archive_lines:
        with archive_path.open("a") as f:
            for line in archive_lines:
                f.write(line + "\n")
    with LOG_PATH.open("w") as f:
        for line in keep_lines:
            f.write(line + "\n")
    print(f"  Archived {len(archive_lines)} session(s) to {archive_path}")


def run(target_date: date) -> Path | None:
    print(f"Loading sessions for {target_date} …")
    sessions = load_sessions(target_date)
    if not sessions:
        print("No sessions found for that date.")
        return None

    print(f"Found {len(sessions)} session(s). Reviewing …")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{target_date}_kids_local.md"

    header = textwrap.dedent(f"""\
        # Kids Safety Report — {target_date}
        Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
        Sessions reviewed: {len(sessions)}
        Review model: `{REVIEW_MODEL}`

        ---
    """)

    sections = [header]
    reviewed_ids: set[str] = set()

    for idx, (session_id, entries) in enumerate(sessions.items(), start=1):
        print(f"  Session {idx}/{len(sessions)}: {session_id[:8]}…")
        transcript = build_transcript(entries)
        try:
            analysis = review_session(transcript)
            reviewed_ids.add(session_id)
        except Exception as exc:
            analysis = f"**Error during review:** {exc}"

        first_ts = entries[0].get("timestamp", "unknown")
        sections.append(textwrap.dedent(f"""\
            ## Session {idx} — `{session_id[:8]}`
            **Started:** {first_ts}
            **Exchanges:** {len(entries)}

            {analysis}

            ---
        """))

    report_path.write_text("\n".join(sections))
    print(f"Report written to: {report_path}")
    archive_sessions(reviewed_ids)
    return report_path


if __name__ == "__main__":
    target = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    run(target)
