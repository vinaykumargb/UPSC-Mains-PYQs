"""
Runs at 6 AM IST (via GitHub Actions cron).

1. Determines today's GS paper + marks from the weekly schedule.
2. Picks a random, never-before-used question from the matching CSV.
3. Posts just the question to Telegram.
4. Saves the picked question to state/pending.json (for the 9 PM job)
   and permanently marks it as used in state/used_questions.json.
"""

import os

import common


def main():
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    thread_id = os.environ.get("TELEGRAM_THREAD_ID")  # optional

    gs_paper, marks = common.get_today_plan()

    if gs_paper is None:
        print("No posting scheduled today (Sunday). Exiting cleanly.")
        return

    picked = common.pick_unused_question(gs_paper, marks)
    if picked is None:
        print(f"No unused {marks}-mark questions left in {gs_paper}. "
              f"Pool exhausted — nothing posted.")
        return

    year = picked["year"]
    question_text = picked["question"]
    q_hash = picked["hash"]

    safe_question = common.escape_html(question_text)

    message = (
        f"📝 <b>UPSC Mains PYQ — {gs_paper} | {marks} Marks | {year}</b>\n\n"
        f"{safe_question}\n\n"
        f"<i>Model answer drops at 9 PM. Try writing your own first! ✍️</i>"
    )

    common.telegram_send_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text=message,
        thread_id=thread_id,
    )
    print(f"Posted {gs_paper} {marks}-mark question ({year}) at 6 AM.")

    # Persist for the 9 PM job, and permanently mark as used so it's
    # never picked again, even across different days/papers.
    common.save_pending({
        "gs_paper": gs_paper,
        "year": year,
        "marks": marks,
        "question": question_text,
        "hash": q_hash,
    })
    common.mark_question_used(q_hash)


if __name__ == "__main__":
    main()
