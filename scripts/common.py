"""
Shared helpers for the UPSC PYQ Telegram bot.

No pandas anywhere (Termux-friendly). Only stdlib + `requests`.
"""

import csv
import hashlib
import json
import os
import random
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
STATE_DIR = os.path.join(ROOT, "state")

USED_QUESTIONS_FILE = os.path.join(STATE_DIR, "used_questions.json")
PENDING_FILE = os.path.join(STATE_DIR, "pending.json")

CSV_FILES = {
    "GS1": os.path.join(DATA_DIR, "gs_one_pyqs.csv"),
    "GS2": os.path.join(DATA_DIR, "gs_two_pyqs.csv"),
    "GS3": os.path.join(DATA_DIR, "gs_three_pyqs.csv"),
}

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# CSV reading (stdlib csv module only — Termux/pandas-free)
# ---------------------------------------------------------------------------

def read_questions(csv_path):
    """
    Read a PYQ csv with header: year,question,marks
    Returns a list of dicts: {"year": str, "question": str, "marks": str}
    """
    rows = []
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # normalize keys/values, guard against stray whitespace
            year = (row.get("year") or "").strip()
            question = (row.get("question") or "").strip()
            marks = (row.get("marks") or "").strip()
            if question:
                rows.append({"year": year, "question": question, "marks": marks})
    return rows


def question_hash(gs_paper, question_text):
    """
    Stable unique ID for a question, independent of row position.
    Hash is based on GS paper + normalized question text, so reordering
    or re-saving the CSV never causes a repeat or a false-new entry.
    """
    normalized = " ".join(question_text.strip().lower().split())
    raw = f"{gs_paper}::{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# State: used questions (never repeat) + pending (today's posted PYQ)
# ---------------------------------------------------------------------------

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return default
        return json.loads(content)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_used_questions():
    """Returns a set of question hashes that have already been posted."""
    data = load_json(USED_QUESTIONS_FILE, {"used": []})
    return set(data.get("used", []))


def mark_question_used(q_hash):
    data = load_json(USED_QUESTIONS_FILE, {"used": []})
    used = set(data.get("used", []))
    used.add(q_hash)
    save_json(USED_QUESTIONS_FILE, {"used": sorted(used)})


def load_pending():
    return load_json(PENDING_FILE, None)


def save_pending(pending_dict):
    save_json(PENDING_FILE, pending_dict)


def clear_pending():
    """Remove pending.json content after the model answer is posted."""
    save_json(PENDING_FILE, {})


def save_model_answer_to_pending(model_answer_text):
    """
    Store the freshly generated (raw, Gemini-Markdown) model answer into
    the same pending.json record, so it's on disk *before* we attempt to
    post it. If the Telegram post fails, the answer isn't lost — the
    next run can retry from this saved copy instead of re-calling Gemini.
    """
    pending = load_pending() or {}
    pending["model_answer"] = model_answer_text
    save_pending(pending)


# ---------------------------------------------------------------------------
# Schedule logic
# ---------------------------------------------------------------------------

# Sequence each day: GS1 -> GS2 -> GS3.
# We rotate through the day-of-week list to know which one paper to pick
# "today" so a single run only posts ONE question, and over 3 consecutive
# days the GS1/GS2/GS3 cycle completes, per the requested weekly pattern.
GS_ORDER = ["GS1", "GS2", "GS3"]

# Mon=0 ... Sun=6 (Python's weekday())
MARKS_BY_WEEKDAY = {
    6: 15,  # Monday
    1: 10,  # Tuesday
    2: 10,  # Wednesday
    3: 15,  # Thursday
    4: 15,  # Friday
    5: 15,  # Saturday
    # Sunday (6): no posting
}


def today_ist():
    return datetime.now(IST)


def get_today_plan(now=None):
    """
    Returns (gs_paper, marks) for today, or (None, None) if no posting
    today (Sunday) — based on IST weekday.

    Mon/Tue/Wed -> GS1/GS2/GS3 @ 10 marks (in that weekday order)
    Thu/Fri/Sat -> GS1/GS2/GS3 @ 15 marks (in that weekday order)
    Sunday      -> no question
    """
    now = now or today_ist()
    weekday = now.weekday()  # Mon=0 .. Sun=6

    if weekday not in MARKS_BY_WEEKDAY:
        return None, None

    marks = MARKS_BY_WEEKDAY[weekday]
    # Mon/Thu -> GS1, Tue/Fri -> GS2, Wed/Sat -> GS3
    gs_index = weekday % 3
    gs_paper = GS_ORDER[gs_index]
    return gs_paper, marks


# ---------------------------------------------------------------------------
# Question selection
# ---------------------------------------------------------------------------

def pick_unused_question(gs_paper, marks):
    """
    Pick a random question for the given GS paper + marks that hasn't
    been posted before. Returns dict with year/question/marks/hash, or
    None if the pool is exhausted.
    """
    csv_path = CSV_FILES[gs_paper]
    all_questions = read_questions(csv_path)

    # filter by requested marks (string-compare tolerant of "10" vs "10.0")
    def marks_match(q_marks):
        try:
            return float(q_marks) == float(marks)
        except (TypeError, ValueError):
            return q_marks.strip() == str(marks)

    candidates = [q for q in all_questions if marks_match(q["marks"])]

    used = load_used_questions()
    fresh = []
    for q in candidates:
        h = question_hash(gs_paper, q["question"])
        if h not in used:
            q["hash"] = h
            fresh.append(q)

    if not fresh:
        return None

    return random.choice(fresh)


# ---------------------------------------------------------------------------
# Telegram (plain REST, no python-telegram-bot SDK)
# ---------------------------------------------------------------------------

TELEGRAM_API_BASE = "https://api.telegram.org"


def telegram_send_message(bot_token, chat_id, text, thread_id=None, parse_mode="HTML"):
    """
    Send a message to a Telegram chat/supergroup, optionally into a
    specific forum topic (thread_id). Uses requests (REST), no SDK.
    """
    import requests

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = int(thread_id)

    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


TELEGRAM_MAX_MESSAGE_LEN = 4096


def split_text_for_telegram(text, limit=TELEGRAM_MAX_MESSAGE_LEN):
    """
    Split long text into Telegram-safe chunks, preferring to break on
    blank lines (paragraph boundaries) so we don't cut mid-sentence or
    mid-MarkdownV2-entity where avoidable.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > limit:
        # Prefer splitting at the last double-newline before the limit.
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def telegram_send_long_message(bot_token, chat_id, text, thread_id=None, parse_mode="HTML"):
    """
    Send `text`, automatically splitting into multiple messages if it
    exceeds Telegram's per-message character limit.
    """
    chunks = split_text_for_telegram(text)
    results = []
    for chunk in chunks:
        results.append(
            telegram_send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=chunk,
                thread_id=thread_id,
                parse_mode=parse_mode,
            )
        )
    return results


def escape_html(text):
    """Minimal HTML escaping for Telegram HTML parse_mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def spoiler(text):
    """Wrap text in Telegram's HTML spoiler tag (tg-spoiler)."""
    return f'<tg-spoiler>{text}</tg-spoiler>'


# ---------------------------------------------------------------------------
# Markdown conversion: Gemini's standard Markdown -> Telegram MarkdownV2
# ---------------------------------------------------------------------------

# Characters that MUST be escaped with a backslash in Telegram MarkdownV2,
# EXCEPT where they're part of actual markdown syntax we want to keep
# (e.g. the * in **bold**, the ` in `code`).
_MDV2_SPECIAL = r"_[]()~>#+-=|{}.!"


def _escape_mdv2_literal(text):
    """Escape MarkdownV2 special chars in plain (non-markup) text."""
    escaped = []
    for ch in text:
        if ch == "\\":
            escaped.append("\\\\")
        elif ch in _MDV2_SPECIAL:
            escaped.append("\\" + ch)
        else:
            escaped.append(ch)
    return "".join(escaped)


def gemini_markdown_to_telegram_mdv2(text):
    """
    Convert Gemini's plain-Markdown output into Telegram MarkdownV2.

    Handles the common subset Gemini actually produces:
      - **bold** / __bold__      -> *bold*      (Telegram bold)
      - *italic* / _italic_      -> _italic_     (Telegram italic)
      - `inline code`            -> `inline code` (kept, content unescaped)
      - ```code blocks```        -> ```code blocks``` (kept, content unescaped)
      - # / ## / ### headers     -> *Header* (bold line, since Telegram has no headers)
      - "- " / "* " bullet lists -> "• " bullet lists
      - everything else          -> escaped per MarkdownV2 rules

    This is a pragmatic line-by-line + regex converter (no external
    markdown library), kept dependency-free for Termux.
    """
    import re

    lines_out = []
    # Split out fenced code blocks first so we never escape/touch their content.
    code_block_pattern = re.compile(r"```(?:[a-zA-Z]*\n)?(.*?)```", re.DOTALL)

    placeholders = {}

    def stash_code_block(match):
        key = f"\x00CODEBLOCK{len(placeholders)}\x00"
        placeholders[key] = f"```\n{match.group(1)}```"
        return key

    text = code_block_pattern.sub(stash_code_block, text)

    # Stash inline code `...` too, so its content is never escaped.
    inline_code_pattern = re.compile(r"`([^`\n]+)`")

    def stash_inline_code(match):
        key = f"\x00INLINECODE{len(placeholders)}\x00"
        placeholders[key] = f"`{match.group(1)}`"
        return key

    text = inline_code_pattern.sub(stash_inline_code, text)

    for raw_line in text.split("\n"):
        line = raw_line

        # Headers (#, ##, ###...) -> bold line
        header_match = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if header_match:
            content = header_match.group(1).strip()
            content = _convert_inline_emphasis(content)
            lines_out.append(f"*{content}*")
            continue

        # Bullet list items: "- " or "* " (but not "**") at line start -> "• "
        bullet_match = re.match(r"^(\s*)[-*]\s+(?!\*)(.*)$", line)
        if bullet_match:
            indent, content = bullet_match.groups()
            content = _convert_inline_emphasis(content)
            lines_out.append(f"{indent}• {content}")
            continue

        # Numbered list items: "1. " etc. -> keep number, escape the dot
        numbered_match = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if numbered_match:
            indent, num, content = numbered_match.groups()
            content = _convert_inline_emphasis(content)
            lines_out.append(f"{indent}{num}\\. {content}")
            continue

        lines_out.append(_convert_inline_emphasis(line))

    result = "\n".join(lines_out)

    # Restore stashed code blocks / inline code verbatim.
    for key, value in placeholders.items():
        result = result.replace(key, value)

    return result


def _convert_inline_emphasis(line):
    """
    Convert **bold**/__bold__ -> *bold* and *italic*/_italic_ -> _italic_
    on a single line, escaping everything else as literal MarkdownV2 text.
    """
    import re

    # Tokenize: bold (**...** or __...__), italic (*...* or _..._), or plain text
    token_pattern = re.compile(
        r"(\*\*.+?\*\*|__.+?__|\*.+?\*|_.+?_)"
    )
    parts = token_pattern.split(line)

    out = []
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            inner = _escape_mdv2_literal(part[2:-2])
            out.append(f"*{inner}*")
        elif part.startswith("__") and part.endswith("__"):
            inner = _escape_mdv2_literal(part[2:-2])
            out.append(f"*{inner}*")
        elif part.startswith("*") and part.endswith("*"):
            inner = _escape_mdv2_literal(part[1:-1])
            out.append(f"_{inner}_")
        elif part.startswith("_") and part.endswith("_"):
            inner = _escape_mdv2_literal(part[1:-1])
            out.append(f"_{inner}_")
        else:
            out.append(_escape_mdv2_literal(part))
    return "".join(out)


# ---------------------------------------------------------------------------
# Gemini (plain REST, no google SDK — Termux-friendly)
# ---------------------------------------------------------------------------

def gemini_generate(api_key, prompt, model=None):
    """
    Call Gemini's generateContent REST endpoint directly with `requests`.
    No google-generativeai SDK dependency (keeps Termux installs light).

    Model defaults to GEMINI_MODEL env var if set, else a current
    cost-efficient flash model. Override anytime without touching code.
    """
    import requests

    model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ]
    }
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    try:
        candidates = data["candidates"]
        parts = candidates[0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        return text.strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response shape: {data}") from e
