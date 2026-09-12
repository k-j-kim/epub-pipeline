#!/usr/bin/env python3
"""Fetch Korean EPUBs from Anna's Archive search API.

Uses saved browser headers (see mgmt UI "bot bypass") when present.
Search HTML → md5 list → /md5/<md5> page → grab the first working
"External" or "Slow" download link → save the EPUB.

Rate-limited by AA (roughly a handful of slow-downloads per hour without
membership). Set AA_MAX_PER_RUN low; the cron cadence + persistence
across runs will grow the library steadily.
"""
import argparse, os, re, sqlite3, sys, time
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).parent))
from korean_check import is_korean

HOST = os.environ.get("AA_HOSTS", "annas-archive.pk").split()[0]
UA_DEFAULT = "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"
HDR_FILE = Path("/state/aa_headers.txt")

MD5_RE = re.compile(r"/md5/([0-9a-fA-F]{32})")
# AA's mirror-download page has links like:
#   /fast_download/<md5>/... (member only)
#   /slow_download/<md5>/... (free with wait)
#   http[s]://<external mirror>/... .epub
DL_RE = re.compile(
    r'href="([^"]*(?:slow_download|fast_download)/[0-9a-fA-F]{32}[^"]*)"'
    r'|href="(https?://[^"]+\.epub[^"]*)"',
    re.I,
)


def load_headers():
    if not HDR_FILE.exists() or HDR_FILE.stat().st_size == 0:
        return {"User-Agent": UA_DEFAULT}
    out = {}
    for line in HDR_FILE.read_text().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    out.setdefault("User-Agent", UA_DEFAULT)
    return out


def db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS fetched(
        md5 TEXT PRIMARY KEY, title TEXT, path TEXT, size INTEGER,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return con


def search_md5s(s, host, page):
    try:
        r = s.get(f"https://{host}/search", params={
            "lang": "ko", "ext": "epub", "sort": "newest", "page": page,
        }, timeout=30)
    except Exception as e:
        print(f"[aa] search network fail: {e}", file=sys.stderr)
        return None
    if r.status_code in (403, 429, 503):
        print(f"[aa] search returned {r.status_code} — bot gate", file=sys.stderr)
        return None
    if not r.ok:
        print(f"[aa] search unexpected status {r.status_code}", file=sys.stderr)
        return None
    body = r.text
    if len(body) < 5000 or "Loading" in body[:2000]:
        return None
    return list({m.lower() for m in MD5_RE.findall(body)})


def find_download(s, host, md5):
    r = s.get(f"https://{host}/md5/{md5}", timeout=30)
    if r.status_code != 200:
        return None
    for m in DL_RE.finditer(r.text):
        href = m.group(1) or m.group(2)
        if not href:
            continue
        if href.startswith("/"):
            href = f"https://{host}{href}"
        # Follow AA's mirror-picker page one more hop to reach the real file
        if "download/" in href and not href.endswith(".epub"):
            try:
                r2 = s.get(href, timeout=30, allow_redirects=True)
                if r2.status_code == 200:
                    m2 = re.search(r'href="(https?://[^"]+\.epub[^"]*)"', r2.text)
                    if m2:
                        return m2.group(1)
            except Exception:
                continue
        else:
            return href
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/downloads/kbfm-aa")
    ap.add_argument("--state-db", default="/state/aa.sqlite")
    ap.add_argument("--max-per-run", type=int, default=int(os.environ.get("AA_MAX_PER_RUN", "5")))
    ap.add_argument("--min-kb", type=int, default=int(os.environ.get("MIN_EPUB_KB", "200")))
    ap.add_argument("--max-mb", type=int, default=int(os.environ.get("MAX_EPUB_MB", "50")))
    args = ap.parse_args()

    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    con = db(args.state_db)

    s = requests.Session()
    s.headers.update(load_headers())

    fetched = 0
    page = 1
    while fetched < args.max_per_run and page <= 10:
        md5s = search_md5s(s, HOST, page)
        if md5s is None:
            Path("/state/needs_bypass").write_text(f"https://{HOST}/search?lang=ko&ext=epub")
            print("[aa] hit bot gate — /state/needs_bypass set for mgmt UI", file=sys.stderr)
            return
        if not md5s:
            break
        for md5 in md5s:
            if fetched >= args.max_per_run:
                break
            if con.execute("SELECT 1 FROM fetched WHERE md5=?", (md5,)).fetchone():
                continue
            try:
                url = find_download(s, HOST, md5)
            except Exception as e:
                print(f"[aa] page fail {md5}: {e}", file=sys.stderr); continue
            if not url:
                con.execute("INSERT OR IGNORE INTO fetched(md5,title,path,size) VALUES(?,?,?,?)",
                            (md5, "", "", 0))
                con.commit(); continue

            dest = out / f"{md5}.epub"
            try:
                with s.get(url, stream=True, timeout=120) as r:
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
                ok, why = is_korean(dest)
                if not ok:
                    dest.unlink()
                    con.execute("INSERT OR IGNORE INTO fetched(md5,title,path,size) VALUES(?,?,?,?)",
                                (md5, "", "not-korean", 0))
                    con.commit()
                    print(f"[aa] rejected non-korean {md5} ({why})", file=sys.stderr)
                    continue
                con.execute("INSERT OR IGNORE INTO fetched(md5,title,path,size) VALUES(?,?,?,?)",
                            (md5, "", str(dest), sz))
                con.commit()
                fetched += 1
                print(f"[aa] {fetched}/{args.max_per_run}  {md5}  ({sz//1024} KB)")
                time.sleep(3.0)  # AA rate-limits aggressively
            except Exception as e:
                try: dest.unlink()
                except FileNotFoundError: pass
                print(f"[aa] download fail {md5}: {e}", file=sys.stderr)
        page += 1

    print(f"[aa] done, fetched {fetched} new EPUBs to {out}")


if __name__ == "__main__":
    main()
