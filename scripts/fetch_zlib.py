#!/usr/bin/env python3
"""Fetch Korean EPUBs from Z-Library.

Flow:
  1. POST /rpc.php action=user/loginByEmail  → session cookies
  2. GET  /s/?e=1&language=korean&extension=epub&page=N  → HTML with book links
  3. GET  /book/<id>/<hash>/<slug>  → detail page with download URL
  4. GET  /dl/<id>/<hash>  → follow redirect → the actual .epub

Env vars:
  ZLIB_EMAIL, ZLIB_PASS  — account credentials
  ZLIB_HOSTS             — space-separated mirror list (fallback order)
  ZLIB_MAX_PER_RUN       — how many books to attempt per run (default 8;
                            free tier quota is 10/day)

If Cloudflare's "I Am Human" challenge kicks in, we surface it through the
mgmt UI's bypass panel — paste request headers copied from a real browser
that has already passed the check, and the fetcher retries with them.
"""
import argparse, os, re, sqlite3, sys, time
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).parent))
from quality_check import assess as check_epub

HOSTS = os.environ.get("ZLIB_HOSTS",
    "z-library.gs 1lib.sk z-lib.fm z-lib.gd z-lib.sk zliba.ru").split()
EMAIL = os.environ.get("ZLIB_EMAIL", "")
PASS  = os.environ.get("ZLIB_PASS", "")
UA    = "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"
HDR_FILE   = Path("/state/zlib_headers.txt")
BYPASS_FLAG = Path("/state/needs_bypass_zlib")

BOOK_LINK_RE = re.compile(r'href="(/book/(\d+)/([0-9a-f]+)/[^"]*)"')
DL_LINK_RE   = re.compile(r'href="(/dl/\d+/[0-9a-f]+)"')


def load_saved_headers():
    if HDR_FILE.exists() and HDR_FILE.stat().st_size > 0:
        out = {}
        for line in HDR_FILE.read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
        return out
    return None


def db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS fetched(
        book_id TEXT PRIMARY KEY, title TEXT, path TEXT, size INTEGER,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return con


def cloudflare_challenge(text):
    """Detect Cloudflare 'I Am Human' interstitial."""
    if not text: return False
    head = text[:2000]
    return "Just a moment" in head or "cf-mitigated" in head.lower() or \
           "cf-please-wait" in head or ("cloudflare" in head.lower() and "challenge" in head.lower())


def login(session, host):
    """Try to log in to a given host; return True on success."""
    if not (EMAIL and PASS):
        return False
    try:
        r = session.post(
            f"https://{host}/rpc.php",
            data={"isModal": "true", "email": EMAIL, "password": PASS,
                  "site_mode": "books", "action": "login", "isSingleLogin": "1"},
            timeout=30,
        )
        # Success signals: response is JSON with `user` or set-cookies contain login token
        if r.ok and "remix_userid" in session.cookies.get_dict():
            return True
        # Some mirrors use a different endpoint
        r = session.post(
            f"https://{host}/rpc.php",
            data={"action": "user/loginByEmail", "email": EMAIL, "password": PASS},
            timeout=30,
        )
        if "remix_userid" in session.cookies.get_dict():
            return True
    except Exception as e:
        print(f"[zlib] login {host} exception: {e}", file=sys.stderr)
    return False


def find_working_host(session):
    """Try each host in order until one accepts our login (or free search)."""
    for host in HOSTS:
        try:
            r = session.get(f"https://{host}/s/?e=1&language=korean&extension=epub",
                            timeout=20)
        except Exception as e:
            print(f"[zlib] {host} unreachable: {e}", file=sys.stderr); continue
        if not r.ok:
            print(f"[zlib] {host} → HTTP {r.status_code}", file=sys.stderr); continue
        if cloudflare_challenge(r.text):
            print(f"[zlib] {host} → Cloudflare challenge, skipping", file=sys.stderr); continue
        if EMAIL and login(session, host):
            print(f"[zlib] logged in via {host}")
            return host
        # Guest fallback (5 books/day) — still usable
        print(f"[zlib] using {host} as guest (no login)")
        return host
    return None


def search(session, host, page):
    r = session.get(f"https://{host}/s/", params={
        "e": 1, "language": "korean", "extension": "epub",
        "yearFrom": "", "yearTo": "", "page": page,
    }, timeout=30)
    if not r.ok or cloudflare_challenge(r.text):
        return None
    # Deduplicate by book_id
    seen = {}
    for full, book_id, book_hash in BOOK_LINK_RE.findall(r.text):
        seen.setdefault(book_id, (full, book_hash))
    return [(bid, full, h) for bid, (full, h) in seen.items()]


def resolve_dl(session, host, book_url):
    r = session.get(f"https://{host}{book_url}", timeout=30)
    if not r.ok or cloudflare_challenge(r.text):
        return None
    m = DL_LINK_RE.search(r.text)
    if not m:
        return None
    return f"https://{host}{m.group(1)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/downloads/kbfm-zlib")
    ap.add_argument("--state-db", default="/state/zlib.sqlite")
    ap.add_argument("--max-per-run", type=int, default=int(os.environ.get("ZLIB_MAX_PER_RUN", "8")))
    ap.add_argument("--min-kb", type=int, default=int(os.environ.get("MIN_EPUB_KB", "200")))
    ap.add_argument("--max-mb", type=int, default=int(os.environ.get("MAX_EPUB_MB", "50")))
    args = ap.parse_args()

    if not (EMAIL and PASS):
        print("[zlib] no ZLIB_EMAIL/ZLIB_PASS in env — running as guest (5/day)")

    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    con = db(args.state_db)

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    saved = load_saved_headers()
    if saved:
        print("[zlib] using saved bypass headers")
        s.headers.update(saved)

    host = find_working_host(s)
    if not host:
        BYPASS_FLAG.write_text("https://z-library.gs/s/?e=1&language=korean&extension=epub")
        print("[zlib] no working host — bypass flag set", file=sys.stderr)
        return

    fetched = 0
    for page in range(1, 20):
        if fetched >= args.max_per_run: break
        results = search(s, host, page)
        if not results:
            print(f"[zlib] search page {page} empty or blocked", file=sys.stderr); break

        for book_id, book_url, book_hash in results:
            if fetched >= args.max_per_run: break
            if con.execute("SELECT 1 FROM fetched WHERE book_id=?", (book_id,)).fetchone():
                continue
            dl = resolve_dl(s, host, book_url)
            if not dl:
                con.execute("INSERT OR IGNORE INTO fetched(book_id,path,size) VALUES(?,?,?)",
                            (book_id, "", 0))
                con.commit(); continue

            dest = out / f"zlib_{book_id}.epub"
            try:
                with s.get(dl, stream=True, timeout=120, allow_redirects=True) as r:
                    if not r.ok or cloudflare_challenge(r.text[:2000] if hasattr(r, "text") else ""):
                        print(f"[zlib] download {book_id} blocked (status {r.status_code})", file=sys.stderr)
                        continue
                    if "html" in r.headers.get("Content-Type", "").lower():
                        # Quota probably exhausted — Zlib serves an HTML page instead of the file
                        body = r.text[:500]
                        if "limit" in body.lower() or "download" in body.lower():
                            print(f"[zlib] daily limit reached; stopping this run", file=sys.stderr)
                            return
                        continue
                    with open(dest, "wb") as fp:
                        for chunk in r.iter_content(1 << 15):
                            fp.write(chunk)
                sz = dest.stat().st_size
                if not (args.min_kb * 1024 <= sz <= args.max_mb * 1024 * 1024):
                    dest.unlink()
                    con.execute("INSERT OR IGNORE INTO fetched(book_id,path,size) VALUES(?,?,?)",
                                (book_id, "", sz))
                    con.commit(); continue
                ok, why, _ = check_epub(dest)
                if not ok:
                    dest.unlink()
                    con.execute("INSERT OR IGNORE INTO fetched(book_id,path,size) VALUES(?,?,?)",
                                (book_id, "not-korean", 0))
                    con.commit()
                    print(f"[zlib] rejected {book_id} ({why})", file=sys.stderr)
                    continue
                con.execute("INSERT OR IGNORE INTO fetched(book_id,path,size) VALUES(?,?,?)",
                            (book_id, str(dest), sz))
                con.commit()
                fetched += 1
                print(f"[zlib] {fetched}/{args.max_per_run}  book_id={book_id}  ({sz//1024} KB)")
                time.sleep(2.0)
            except Exception as e:
                try: dest.unlink()
                except FileNotFoundError: pass
                print(f"[zlib] download fail {book_id}: {e}", file=sys.stderr)

    print(f"[zlib] done, fetched {fetched} new EPUBs to {out}")


if __name__ == "__main__":
    main()
