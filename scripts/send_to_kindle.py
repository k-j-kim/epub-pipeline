#!/usr/bin/env python3
"""Pick N unread Korean EPUBs from the Calibre library and email them to a Kindle.

State (which books have been sent) lives in /state/sent.db so we never repeat.
"""
import os, random, smtplib, sqlite3, subprocess, sys, time
from email.message import EmailMessage
from pathlib import Path

LIBRARY = "/library"
STATE_DB = "/state/sent.db"
N = int(os.environ.get("BOOKS_PER_WEEK", "5"))
KINDLE = os.environ["KINDLE_EMAIL"]
SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)


def calibre_list():
    """Return [(id, title, author, epub_path), ...] for every EPUB in the library."""
    out = subprocess.check_output([
        "calibredb", "--with-library", LIBRARY, "list",
        "--fields", "id,title,authors,formats", "--for-machine",
    ], text=True)
    import json
    rows = []
    for r in json.loads(out):
        for fmt in r.get("formats", []):
            if fmt.lower().endswith(".epub"):
                # calibredb --for-machine returns authors as a STRING (pre-joined),
                # not a list. Joining a string chars gives 'ㄱ, ㄴ, ...' garbage.
                a = r.get("authors")
                author = a if isinstance(a, str) else ", ".join(a or [])
                rows.append((r["id"], r["title"], author, fmt))
                break
    return rows


def already_sent():
    con = sqlite3.connect(STATE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS sent(id INTEGER PRIMARY KEY, sent_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    return con, {r[0] for r in con.execute("SELECT id FROM sent")}


def mail_one(path, title):
    msg = EmailMessage()
    msg["Subject"] = title[:100]
    msg["From"] = SMTP_FROM
    msg["To"] = KINDLE
    msg.set_content(f"Enjoy: {title}")
    with open(path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="epub+zip",
                           filename=Path(path).name)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


def main():
    con, sent = already_sent()
    books = [b for b in calibre_list() if b[0] not in sent]
    if not books:
        print("[send] no unread books in library — refresh may still be in progress")
        return
    picks = random.sample(books, min(N, len(books)))
    sent_count = 0
    for bid, title, author, path in picks:
        try:
            print(f"[send] {sent_count + 1}/{len(picks)}  {title} — {author}", flush=True)
            mail_one(path, f"{title} — {author}" if author else title)
            con.execute("INSERT OR REPLACE INTO sent(id) VALUES (?)", (bid,))
            con.commit()
            sent_count += 1
            # Gmail free tier: 500 recipients / 24h. Be gentle.
            time.sleep(3)
        except Exception as e:
            print(f"[send] FAILED {title}: {e}", file=sys.stderr, flush=True)
            time.sleep(10)  # back off harder on error
    print(f"[send] done: {sent_count}/{len(picks)} sent")


if __name__ == "__main__":
    main()
