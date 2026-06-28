"""
Runs at 9 PM IST (via GitHub Actions cron).

1. Reads state/pending.json (written by the 6 AM job) to know which
   question is "today's" question.
2. Asks Gemini for a full UPSC-style model answer (Gemini returns it in
   standard Markdown).
3. Stores that raw answer into state/pending.json FIRST (so it's saved
   to a file even before posting — if the Telegram call fails, the
   answer isn't lost and can be retried from disk).
4. Converts the Markdown into Telegram's MarkdownV2 syntax and posts it
   to the same thread (splitting into multiple messages if too long).
5. Clears state/pending.json (content removed after posting — the
   question itself stays permanently recorded in used_questions.json
   so it is never repeated).
"""

import os
import sys

import common


def build_model_answer_prompt(gs_paper, year, marks, question):
    word_limit = 150 if int(marks) == 10 else 250
    return (
        f"You are a UPSC Mains topper-level answer writer. Write a "
        f"high-quality model answer for the following {gs_paper} previous "
        f"year question ({marks} marks, {year}). Follow standard UPSC "
        f"answer structure: a brief intro, a well-organized body with "
        f"clear points/sub-headings, and a concise conclusion. Most importantaly address only explicitly asked dimentions for 10 marks question and at least one extra dimention if the question of 15 marks. MUST AND SHOULD give two newline space between points to make it readable easily. Use recommendations or facts from reliable sources to enrich the answer. Don't answer in paragraph format, each bullet point answer must be as short as possible. Format the "
        f"response in Markdown (use **bold**, bullet points, etc. where "
        f"helpful). Target approximately {word_limit} words.\n\n"
        f"Question: {question}"
    )


def main():
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    thread_id = os.environ.get("TELEGRAM_THREAD_ID")
    gemini_key = os.environ["GEMINI_API_KEY"]

    pending = common.load_pending()
    if not pending or not pending.get("question"):
        print("No pending question found (maybe Sunday, or 6 AM job "
              "found an exhausted pool). Nothing to post at 9 PM.")
        return

    gs_paper = pending["gs_paper"]
    year = pending["year"]
    marks = pending["marks"]
    question_text = pending["question"]

    # If a previous run already generated the answer but failed to post
    # (e.g. Telegram was down), reuse it instead of calling Gemini again.
    model_answer = pending.get("model_answer")
    if not model_answer:
        prompt = build_model_answer_prompt(gs_paper, year, marks, question_text)
        try:
            model_answer = common.gemini_generate(gemini_key, prompt)
        except Exception as e:
            print(f"Gemini call failed for model answer generation: {e}", file=sys.stderr)
            raise

        # Store the raw model answer in the temp file BEFORE attempting
        # to post, so it's never lost if the Telegram call fails.
        common.save_model_answer_to_pending(model_answer)

    # Gemini returns standard Markdown; convert it to Telegram's
    # MarkdownV2 syntax/escaping rules before sending.
    question_mdv2 = common._escape_mdv2_literal(question_text)
    answer_mdv2 = common.gemini_markdown_to_telegram_mdv2(model_answer)

    header = f"✅ *Model Answer — {gs_paper} \\| {marks} Marks \\| {year}*\n\n_{question_mdv2}_\n\n"
    message = header + answer_mdv2

    common.telegram_send_long_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text=message,
        thread_id=thread_id,
        parse_mode="MarkdownV2",
    )
    print(f"Posted model answer for {gs_paper} {marks}-mark question ({year}) at 9 PM.")

    # Remove content after posting, as requested — keeps testing easy
    # and the file small. The permanent "used" record already lives in
    # used_questions.json from the 6 AM job, so this is safe to clear.
    common.clear_pending()


if __name__ == "__main__":
    main()
