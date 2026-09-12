#!/usr/bin/env python3
"""Fetch Korean EPUBs from libgen.li's fiction database.

libgen.li's `req=` doesn't support field operators (no `language:korean`),
but the *fiction* topic tab combined with a Korean seed word returns rows
overwhelmingly tagged as EPUB. We cycle through several Korean seeds to
get variety, parse the results table to extract only rows whose extension
column contains 'epub', then resolve each md5 to a direct download URL.
"""
import argparse, os, re, sqlite3, sys, time
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).parent))
from quality_check import assess as check_epub

HOST = os.environ.get("LIBGEN_HOST", "libgen.li")
UA   = "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"

# Korean seed words to rotate through — each returns ~25 rows.
SEEDS = ["한국", "소설", "이야기", "문학", "역사", "사랑", "인생",
         "김", "이", "박", "최", "정", "장", "조"]

# Match a table row that has an epub extension cell + md5 link.
# libgen.li rows look like:  <td>epub</td> ... <td><a ... href="/ads.php?md5=XXX...">
ROW_RE = re.compile(
    r"<td>epub</td>.{0,300}?href=\"[^\"]*ads\.php\?md5=([0-9a-fA-F]{32})",
    re.S | re.I,
)
GET_LINK_RE = re.compile(
    r'href="(get\.php\?md5=[0-9a-fA-F]{32}[^"]+)"',
    re.I,
)


def db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS fetched(
        md5 TEXT PRIMARY KEY, title TEXT, path TEXT, size INTEGER,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return con


def search_epubs(s, host, seed, page=1):
    r = s.get(f"https://{host}/index.php", params={
        "req": seed, "res": 100, "covers": "on",
        "topics[]": "f", "curtab": "f", "page": page,
    }, timeout=30)
    r.raise_for_status()
    return list({m.lower() for m in ROW_RE.findall(r.text)})


def resolve_download(s, host, md5):
    # ads.php returns an empty body without a Referer header; the header is set
    # on the session, so this works.
    r = s.get(f"https://{host}/ads.php", params={"md5": md5}, timeout=60)
    r.raise_for_status()
    m = GET_LINK_RE.search(r.text)
    if not m:
        return None
    return f"https://{host}/{m.group(1)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/downloads/kbfm-libgen")
    ap.add_argument("--state-db", default="/state/libgen.sqlite")
    ap.add_argument("--max-per-run", type=int, default=int(os.environ.get("LIBGEN_MAX_PER_RUN", "20")))
    ap.add_argument("--min-kb", type=int, default=int(os.environ.get("MIN_EPUB_KB", "200")))
    ap.add_argument("--max-mb", type=int, default=int(os.environ.get("MAX_EPUB_MB", "50")))
    args = ap.parse_args()

    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    con = db(args.state_db)

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": f"https://{HOST}/",
    })
    fetched = 0

    for seed in SEEDS:
        if fetched >= args.max_per_run:
            break
        try:
            md5s = search_epubs(s, HOST, seed)
        except Exception as e:
            print(f"[libgen] search '{seed}' failed: {e}", file=sys.stderr); continue
        print(f"[libgen] seed '{seed}': {len(md5s)} epub rows")

        for md5 in md5s:
            if fetched >= args.max_per_run:
                break
            if con.execute("SELECT 1 FROM fetched WHERE md5=?", (md5,)).fetchone():
                continue
            try:
                url = resolve_download(s, HOST, md5)
            except Exception as e:
                print(f"[libgen] resolve fail {md5}: {e}", file=sys.stderr); continue
            if not url:
                con.execute("INSERT OR IGNORE INTO fetched(md5,path,size) VALUES(?,?,?)", (md5, "", 0))
                con.commit(); continue

            dest = out / f"{md5}.epub"
            try:
                with s.get(url, stream=True, timeout=90) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as fp:
                        for chunk in r.iter_content(1 << 15):
                            fp.write(chunk)
                sz = dest.stat().st_size
                if not (args.min_kb * 1024 <= sz <= args.max_mb * 1024 * 1024):
                    dest.unlink()
                    con.execute("INSERT OR IGNORE INTO fetched(md5,path,size) VALUES(?,?,?)", (md5, "", sz))
                    con.commit(); continue
                ok, why, _stats = check_epub(dest)
                if not ok:
                    dest.unlink()
                    con.execute("INSERT OR IGNORE INTO fetched(md5,path,size) VALUES(?,?,?)", (md5, "not-korean", 0))
                    con.commit()
                    print(f"[libgen] rejected {md5} ({why})", file=sys.stderr)
                    continue
                con.execute("INSERT OR IGNORE INTO fetched(md5,path,size) VALUES(?,?,?)", (md5, str(dest), sz))
                con.commit()
                fetched += 1
                print(f"[libgen] {fetched}/{args.max_per_run}  {md5}  ({sz//1024} KB)")
                time.sleep(1.0)
            except Exception as e:
                try: dest.unlink()
                except FileNotFoundError: pass
                print(f"[libgen] download fail {md5}: {e}", file=sys.stderr)

    print(f"[libgen] done, fetched {fetched} new EPUBs to {out}")


if __name__ == "__main__":
    main()
