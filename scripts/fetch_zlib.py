#!/usr/bin/env python3
"""Fetch Korean EPUBs from Z-Library via their Personal Telegram Bot.

Uses Telethon to log in as a Telegram user (not a bot) and chat with
Z-Library's per-user bot. That bot is paired to a Z-Library account via
go-to-library.sk; downloads count against that account's daily quota
(10 free + 10 bot bonus = 20/day).

First-run bootstrap (one-shot, needs SMS code):
    docker exec -it kbfm-worker python3 /scripts/fetch_zlib.py --login

After that the session file /state/telegram.session persists indefinitely.
"""
import argparse, asyncio, os, re, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from quality_check import assess as check_epub

try:
    from telethon import TelegramClient
    from telethon.tl.types import DocumentAttributeFilename
except ImportError:
    print("[zlib] telethon not installed — rebuild the worker image", file=sys.stderr)
    sys.exit(1)

API_ID   = int(os.environ.get("TG_API_ID", "0") or "0")
API_HASH = os.environ.get("TG_API_HASH", "")
PHONE    = os.environ.get("TG_PHONE", "")
BOT      = os.environ.get("ZLIB_BOT", "")
SESSION  = "/state/telegram"
OUT_DIR  = Path(os.environ.get("ZLIB_OUT_DIR", "/downloads/kbfm-zlib"))
STATE_DB = Path(os.environ.get("ZLIB_STATE_DB", "/state/zlib.sqlite"))

MAX_PER_RUN = int(os.environ.get("ZLIB_MAX_PER_RUN", "8"))
MIN_KB      = int(os.environ.get("MIN_EPUB_KB", "200"))
MAX_MB      = int(os.environ.get("MAX_EPUB_MB", "50"))

# Seed queries to rotate through — same Korean seed words we use for libgen.
SEEDS = ["한국 소설", "한국 문학", "소설", "이광수", "김유정", "박경리",
         "이문열", "조정래", "황석영", "한강", "김영하", "정유정"]


def db():
    con = sqlite3.connect(STATE_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS fetched(
        book_id TEXT PRIMARY KEY, title TEXT, path TEXT, size INTEGER,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.execute("""CREATE TABLE IF NOT EXISTS cursor(k TEXT PRIMARY KEY, v INTEGER)""")
    return con


def cursor_get(con, k, d=0):
    r = con.execute("SELECT v FROM cursor WHERE k=?", (k,)).fetchone()
    return r[0] if r else d


def cursor_set(con, k, v):
    con.execute("INSERT INTO cursor(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=?", (k, v, v))
    con.commit()


def make_client():
    if not (API_ID and API_HASH):
        raise RuntimeError("TG_API_ID and TG_API_HASH must be set in .env")
    return TelegramClient(SESSION, API_ID, API_HASH)


async def cmd_login(force_sms=False):
    """Interactive one-shot login. Prompts for the SMS code."""
    if not PHONE:
        print("[zlib] TG_PHONE not set", file=sys.stderr); sys.exit(1)
    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        # Manually invoke send_code to get force_sms control
        from telethon.tl.functions.auth import SendCodeRequest, ResendCodeRequest
        from telethon.tl.types import CodeSettings
        try:
            sent = await client.send_code_request(PHONE, force_sms=force_sms)
            print(f"[zlib] code request accepted, type={sent.type.__class__.__name__}")
            if force_sms:
                print("[zlib] forced SMS fallback — check your phone's text messages")
            else:
                print("[zlib] Telegram will try in-app first; SMS fallback after ~2min")
                print("[zlib] to force SMS immediately, rerun with:  ... --login --sms")
        except Exception as e:
            print(f"[zlib] send_code failed: {e}", file=sys.stderr); return
        code = input("Enter the code: ").strip()
        try:
            await client.sign_in(PHONE, code)
        except Exception as e:
            # 2FA cloud password
            if "password" in str(e).lower() or "SessionPasswordNeededError" in type(e).__name__:
                pw = input("2FA cloud password: ").strip()
                await client.sign_in(password=pw)
            else:
                print(f"[zlib] sign_in failed: {e}", file=sys.stderr); return
    me = await client.get_me()
    print(f"[zlib] logged in as {me.first_name} @{me.username or '?'} (id={me.id})")
    print(f"[zlib] session saved to {SESSION}.session — future runs are non-interactive")
    await client.disconnect()


async def cmd_fetch():
    if not BOT:
        print("[zlib] ZLIB_BOT not set (e.g. @zlib20260913_bot)", file=sys.stderr); sys.exit(1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = db()

    client = make_client()
    await client.connect()
    if not await client.is_user_authorized():
        print("[zlib] not logged in — run: docker exec -it kbfm-worker python3 /scripts/fetch_zlib.py --login", file=sys.stderr)
        return
    print(f"[zlib] messaging {BOT}")

    seed_idx = cursor_get(con, "seed_idx", 0)
    fetched = 0

    # Rotate seeds — one seed per run, next run picks the next seed
    seed = SEEDS[seed_idx % len(SEEDS)]
    cursor_set(con, "seed_idx", (seed_idx + 1) % len(SEEDS))
    print(f"[zlib] search seed: {seed!r}")

    # Send the query; bot responds with a list + interactive buttons
    async with client:
        entity = await client.get_entity(BOT)
        # Clear old chat log so we see fresh responses
        await client.send_message(entity, seed)
        await asyncio.sleep(3)

        # Poll for bot responses. Bot may reply with several messages containing
        # inline buttons; each button click yields a document (the epub file).
        # We iterate the most recent bot messages and for any that contain a
        # button leading to an EPUB, click it.
        collected = []
        async for msg in client.iter_messages(entity, limit=40):
            if msg.sender_id != entity.id:
                continue
            if msg.date.timestamp() < time.time() - 60:
                break
            collected.append(msg)

        for msg in reversed(collected):
            if fetched >= MAX_PER_RUN: break
            # Case A: message already has a document attachment (bot sent the file)
            if msg.document:
                filename = _doc_filename(msg.document)
                if not filename or not filename.lower().endswith(".epub"):
                    continue
                book_id = str(msg.id)
                if con.execute("SELECT 1 FROM fetched WHERE book_id=?", (book_id,)).fetchone():
                    continue
                dest = OUT_DIR / f"tg_{book_id}.epub"
                print(f"[zlib] downloading {filename}")
                await client.download_media(msg, file=str(dest))
                if _accept(dest, con, book_id):
                    fetched += 1
                    print(f"[zlib] {fetched}/{MAX_PER_RUN}  {filename}")
                continue

            # Case B: message has inline-keyboard buttons pointing to books
            if msg.buttons:
                for row in msg.buttons:
                    if fetched >= MAX_PER_RUN: break
                    for btn in row:
                        if fetched >= MAX_PER_RUN: break
                        label = (getattr(btn, "text", "") or "").lower()
                        if "epub" not in label and "download" not in label and "책" not in label:
                            continue
                        book_id = f"{msg.id}:{label[:30]}"
                        if con.execute("SELECT 1 FROM fetched WHERE book_id=?", (book_id,)).fetchone():
                            continue
                        print(f"[zlib] clicking button: {label[:60]}")
                        try:
                            result = await btn.click()
                        except Exception as e:
                            print(f"[zlib] button click failed: {e}", file=sys.stderr); continue
                        # Wait for the bot to send the actual file
                        await asyncio.sleep(4)
                        # Look at the very newest message
                        newest = [m async for m in client.iter_messages(entity, limit=3)]
                        for nm in newest:
                            if nm.document and nm.date.timestamp() > time.time() - 15:
                                fn = _doc_filename(nm.document)
                                if fn and fn.lower().endswith(".epub"):
                                    dest = OUT_DIR / f"tg_{nm.id}.epub"
                                    await client.download_media(nm, file=str(dest))
                                    if _accept(dest, con, book_id):
                                        fetched += 1
                                        print(f"[zlib] {fetched}/{MAX_PER_RUN}  {fn}")
                                    break
                        time.sleep(2)   # polite pacing

    print(f"[zlib] done, fetched {fetched} new EPUBs to {OUT_DIR}")


def _doc_filename(doc):
    for a in doc.attributes:
        if isinstance(a, DocumentAttributeFilename):
            return a.file_name
    return None


def _accept(dest: Path, con, book_id: str):
    """Apply size + language + quality gate. Record result. Return True if kept."""
    if not dest.exists():
        return False
    sz = dest.stat().st_size
    if not (MIN_KB * 1024 <= sz <= MAX_MB * 1024 * 1024):
        dest.unlink()
        con.execute("INSERT OR IGNORE INTO fetched(book_id,path,size) VALUES(?,?,?)", (book_id, "", sz))
        con.commit(); return False
    ok, why, _ = check_epub(dest)
    if not ok:
        dest.unlink()
        con.execute("INSERT OR IGNORE INTO fetched(book_id,path,size) VALUES(?,?,?)", (book_id, "not-korean", 0))
        con.commit()
        print(f"[zlib] rejected {book_id} ({why})", file=sys.stderr); return False
    con.execute("INSERT OR IGNORE INTO fetched(book_id,path,size) VALUES(?,?,?)", (book_id, str(dest), sz))
    con.commit()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true", help="one-shot Telegram login")
    ap.add_argument("--sms", action="store_true", help="force SMS fallback for code delivery")
    args = ap.parse_args()
    if args.login:
        asyncio.run(cmd_login(force_sms=args.sms))
    else:
        asyncio.run(cmd_fetch())


if __name__ == "__main__":
    main()
