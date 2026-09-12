#!/usr/bin/env python3
"""Fetch Korean EPUBs from libgen (fiction).

Approach: use libgen.li's HTML search filtered by language+extension. Each
result row exposes an md5. For each md5 we hit the download page, extract
the direct-download link (mirror rotates), and pull the file.

We DON'T bother with the multi-GB MySQL dump — the HTML paginated search
returns hundreds of Korean-language rows and is dramatically cheaper.
"""
import argparse, os, re, sqlite3, sys, time
from pathlib import Path
import requests

BASE = os.environ.get("LIBGEN_HOST", "libgen.li")
SEARCH = "https://{host}/index.php"
GETPAGE = "https://{host}/ads.php"
UA = "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"

MD5_RE = re.compile(r"md5=([0-9a-fA-F]{32})")
GET_RE = re.compile(r'href="([^"]*get\.php\?md5=[0-9a-fA-F]{32}[^"]*)"', re.I)


def db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS fetched(
        md5 TEXT PRIMARY KEY, title TEXT, path TEXT, size INTEGER,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return con


def search_page(s, host, page):
    # libgen.li's non-fiction search takes `req` and `topics[]=f` for fiction;
    # `columns[]=` = title/author, plus language + extension via extra params.
    params = {
        "req": "",
        "columns[]": ["t", "a"],
        "topics[]": "f",  # fiction
        "res": 100,
        "page": page,
        "covers": "on",
        "curtab": "f",
        "gmode": "on",
        "filesuns": "all",
        "language": "Korean",
        "extension": "epub",
    }
    r = s.get(SEARCH.format(host=host), params=params, timeout=30)
    r.raise_for_status()
    return r.text


def resolve_download(s, host, md5):
    r = s.get(GETPAGE.format(host=host), params={"md5": md5}, timeout=30)
    r.raise_for_status()
    m = GET_RE.search(r.text)
    if not m:
        return None
    href = m.group(1)
    if href.startswith("/"):
        return f"https://{host}{href}"
    if href.startswith("http"):
        return href
    return f"https://{host}/{href}"


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
    s.headers["User-Agent"] = UA
    fetched = 0
    page = 1

    while fetched < args.max_per_run:
        try:
            html = search_page(s, BASE, page)
        except Exception as e:
            print(f"[libgen] search page {page} failed: {e}", file=sys.stderr)
            break

        md5s = list({m.lower() for m in re.findall(r"[0-9a-fA-F]{32}", html)})
        if not md5s:
            print(f"[libgen] no md5s on page {page}, stopping", file=sys.stderr)
            break

        for md5 in md5s:
            if fetched >= args.max_per_run:
                break
            if con.execute("SELECT 1 FROM fetched WHERE md5=?", (md5,)).fetchone():
                continue
            try:
                url = resolve_download(s, BASE, md5)
            except Exception as e:
                print(f"[libgen] resolve fail {md5}: {e}", file=sys.stderr); continue
            if not url:
                con.execute("INSERT OR IGNORE INTO fetched(md5,title,path,size) VALUES(?,?,?,?)",
                            (md5, "", "", 0))
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
                    con.execute("INSERT OR IGNORE INTO fetched(md5,title,path,size) VALUES(?,?,?,?)",
                                (md5, "", "", sz))
                    con.commit(); continue
                con.execute("INSERT OR IGNORE INTO fetched(md5,title,path,size) VALUES(?,?,?,?)",
                            (md5, "", str(dest), sz))
                con.commit()
                fetched += 1
                print(f"[libgen] {fetched}/{args.max_per_run}  {md5}  ({sz//1024} KB)")
                time.sleep(1.0)
            except Exception as e:
                try: dest.unlink()
                except FileNotFoundError: pass
                print(f"[libgen] download fail {md5}: {e}", file=sys.stderr)
        page += 1
        if page > 20:
            break

    print(f"[libgen] done, fetched {fetched} new EPUBs to {out}")


if __name__ == "__main__":
    main()
