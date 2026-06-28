# UPSC PYQ Telegram Bot

Fully automated GitHub Actions bot. No server, no pandas (Termux-friendly).

## What it does

- **6:00 AM IST** — Posts a random, never-repeated PYQ (just the question).
- **9:00 PM IST** — Generates the full model answer with Gemini (which
  replies in Markdown), saves it to a temp state file, converts it to
  Telegram's MarkdownV2 formatting, posts it, then clears that temp file.

## Schedule

| Day        | Marks | Order            |
|------------|-------|------------------|
| Mon/Tue/Wed| 10    | GS1 → GS2 → GS3  |
| Thu/Fri/Sat| 15    | GS1 → GS2 → GS3  |
| Sunday     | —     | no post          |

(Mon→GS1, Tue→GS2, Wed→GS3, Thu→GS1, Fri→GS2, Sat→GS3 — one question/day.)

## File structure

```
data/
  gs_one_pyqs.csv     # columns: year,question,marks
  gs_two_pyqs.csv
  gs_three_pyqs.csv
scripts/
  common.py           # CSV reading, hashing, Telegram + Gemini REST calls, schedule logic
  post_question.py     # 6 AM job
  post_answer.py        # 9 PM job
state/
  used_questions.json  # permanent record of posted question hashes — never repeats
  pending.json          # today's in-flight question; cleared to {} after 9 PM post
.github/workflows/
  upsc-pyq-schedule.yml
```

## Setup

1. **Add your CSVs** to `data/` with header `year,question,marks` (marks as `10` or `15`).
2. **Add GitHub repo secrets** (Settings → Secrets and variables → Actions):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` (e.g. `-100xxxxxxxxxx` for a supergroup)
   - `TELEGRAM_THREAD_ID` (optional — omit if not posting to a specific forum topic)
   - `GEMINI_API_KEY`
3. Push to GitHub. The workflow runs automatically on the cron schedule.

## Manual testing

Use the **Actions** tab → "UPSC PYQ Telegram Bot" → "Run workflow" → pick
`post_question` or `post_answer` from the dropdown to trigger either job
on demand, without waiting for the cron.

To test locally (e.g. in Termux):

```bash
pip install requests --break-system-packages
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export TELEGRAM_THREAD_ID=...   # optional
export GEMINI_API_KEY=...
cd scripts
python3 post_question.py
python3 post_answer.py
```

## How "never repeat" works

Each question is hashed as `sha256(GS_paper + normalized_question_text)`.
This means:
- Reordering rows in the CSV never causes a repeat or a skip.
- The hash is stored **permanently** in `state/used_questions.json` the
  moment it's posted at 6 AM — it stays there even after `pending.json`
  is cleared at 9 PM.

## How the 6 AM → 9 PM handoff works

`state/pending.json` holds the day's question between the two runs.
At 9 PM, the script:
1. Reads the pending question.
2. Asks Gemini for the model answer (returned in standard Markdown).
3. **Saves that raw answer into `pending.json` immediately** — before
   attempting to post it. If the Telegram call then fails (outage,
   rate limit, etc.), the answer isn't lost: the next run detects the
   saved `model_answer` field and retries posting it directly, without
   calling Gemini again.
4. Converts the Markdown to Telegram's MarkdownV2 syntax and posts it
   (auto-splitting into multiple messages if it exceeds Telegram's
   4096-character limit).
5. On success, clears `pending.json` back to `{}`.

Both state files are committed back to the repo automatically by the
workflow's last step, so state persists across separate Action runs.

## Notes

- The 6 AM question post uses Telegram's HTML `parse_mode`.
- The 9 PM model answer uses Telegram's **MarkdownV2** `parse_mode`,
  since Gemini's response is already in Markdown — `common.py` converts
  Gemini's Markdown (`**bold**`, `*italic*`, headers, lists, code blocks)
  into Telegram's MarkdownV2 syntax/escaping automatically.
- Gemini is called via plain REST (`generateContent` endpoint) with the
  `requests` library only — no `google-generativeai` SDK, no `pandas`.
- Default model is `gemini-2.5-flash`; override via a `GEMINI_MODEL`
  repo secret/env var if you want a different one.
- If a CSV's pool of a given marks-value is exhausted, that day's job
  logs it and exits cleanly without posting (no crash).
