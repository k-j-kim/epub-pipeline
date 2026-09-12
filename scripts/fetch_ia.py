#!/usr/bin/env python3
"""Fetch Korean EPUBs from archive.org (Internet Archive).

IA advancedsearch API is fully open — no auth, no rate limit games.
We paginate `mediatype:texts AND language:kor AND format:EPUB`, then for
each identifier hit /metadata/ to find the exact .epub file name, then
download via /download/<id>/<file>.
"""
import argparse, json, os, sqlite3, sys, time, urllib.parse
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).parent))
from korean_check import is_korean

SEARCH = "https://archive.org/advancedsearch.php"
META   = "https://archive.org/metadata/{id}"
DL     = "https://archive.org/download/{id}/{file}"

Q = "mediatype:texts AND language:(kor OR ko OR korean) AND format:EPUB"
UA = "epub-pipeline/1.0 (+https://github.com/k-j-kim/epub-pipeline)"


def db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS fetched (
        identifier TEXT PRIMARY KEY,
        title TEXT, creator TEXT, path TEXT, size INTEGER,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return con


def search(session, page, rows):
    params = {
        "q": Q, "fl[]": ["identifier", "title", "creator"],
        "rows": rows, "page": page, "output": "json",
        "sort[]": "publicdate desc",
    }
    r = session.get(SEARCH, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("response", {}).get("docs", [])


def pick_epub(files):
    """From an IA item's file list, pick the largest .epub."""
    epubs = [f for f in files if (f.get("name", "").lower().endswith(".epub"))]
    if not epubs:
        return None
    epubs.sort(key=lambda f: int(f.get("size", "0") or 0), reverse=True)
    return epubs[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/downloads/kbfm-ia")
    ap.add_argument("--state-db", default="/state/ia.sqlite")
    ap.add_argument("--max-per-run", type=int, default=int(os.environ.get("IA_MAX_PER_RUN", "40")))
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
        docs = search(s, page, 100)
        if not docs:
            break
        for d in docs:
            if fetched >= args.max_per_run:
                break
            ident = d.get("identifier")
            if not ident:
                continue
            if con.execute("SELECT 1 FROM fetched WHERE identifier=?", (ident,)).fetchone():
                continue
            try:
                meta = s.get(META.format(id=ident), timeout=30).json()
            except Exception as e:
                print(f"[ia] metadata fail {ident}: {e}", file=sys.stderr); continue
            f = pick_epub(meta.get("files", []))
            if not f:
                con.execute("INSERT OR IGNORE INTO fetched(identifier,title,creator,path,size) VALUES(?,?,?,?,?)",
                            (ident, d.get("title", ""), str(d.get("creator", "")), "", 0))
                con.commit(); continue
            sz = int(f.get("size", "0") or 0)
            if sz and not (args.min_kb * 1024 <= sz <= args.max_mb * 1024 * 1024):
                con.execute("INSERT OR IGNORE INTO fetched(identifier,title,creator,path,size) VALUES(?,?,?,?,?)",
                            (ident, d.get("title", ""), str(d.get("creator", "")), "", sz))
                con.commit(); continue

            url = DL.format(id=ident, file=urllib.parse.quote(f["name"]))
            dest = out / f"{ident}.epub"
            try:
                with s.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as fp:
                        for chunk in r.iter_content(1 << 15):
                            fp.write(chunk)
                actual = dest.stat().st_size
                if actual < args.min_kb * 1024:
                    dest.unlink(); print(f"[ia] too-small {ident}", file=sys.stderr); continue
                ok, why = is_korean(dest)
                if not ok:
                    dest.unlink()
                    con.execute("INSERT OR IGNORE INTO fetched(identifier,title,creator,path,size) VALUES(?,?,?,?,?)",
                                (ident, d.get("title", ""), str(d.get("creator", "")), "not-korean", 0))
                    con.commit()
                    print(f"[ia] rejected non-korean {ident} ({why})", file=sys.stderr)
                    continue
                con.execute("INSERT OR IGNORE INTO fetched(identifier,title,creator,path,size) VALUES(?,?,?,?,?)",
                            (ident, d.get("title", ""), str(d.get("creator", "")), str(dest), actual))
                con.commit()
                fetched += 1
                print(f"[ia] {fetched}/{args.max_per_run}  {ident}  {d.get('title','')[:60]}")
                time.sleep(0.5)  # be polite
            except Exception as e:
                try: dest.unlink()
                except FileNotFoundError: pass
                print(f"[ia] download fail {ident}: {e}", file=sys.stderr)
        page += 1

    print(f"[ia] done, fetched {fetched} new EPUBs to {out}")


if __name__ == "__main__":
    main()
