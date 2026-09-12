#!/usr/bin/env python3
"""Verify + improve book metadata via OpenLibrary (free, no auth).

Given a title / author (possibly romanized Korean like 'yojauinamjakimha'),
searches OpenLibrary for a Korean-language record. Returns:
    {"verified": bool, "title": <hangul>, "author": <hangul>, "source": <str>}

Also detects "not a book" — search returns zero Korean matches AND title is
pure-ASCII (suggesting romanized garbage from IA identifier, not a real book).

Cache: results are keyed by (title, author) in a local sqlite so repeated
lookups don't re-hit the API.
"""
import argparse, os, re, sqlite3, sys, time, urllib.parse
from pathlib import Path
import requests

CACHE = Path(os.environ.get("META_CACHE", "/state/metadata_cache.sqlite"))
UA = "epub-pipeline/1.0 (metadata-verify)"
OL_SEARCH = "https://openlibrary.org/search.json"

HANGUL_RE = re.compile(r"[가-힯]")


def _cache():
    con = sqlite3.connect(CACHE)
    con.execute("""CREATE TABLE IF NOT EXISTS lookup(
        key TEXT PRIMARY KEY, verified INTEGER, title TEXT, author TEXT, source TEXT,
        ts TEXT DEFAULT CURRENT_TIMESTAMP)""")
    return con


def _has_hangul(s):
    return bool(HANGUL_RE.search(s or ""))


def _openlibrary(title, author, session):
    params = {"title": title, "language": "kor", "limit": 5}
    if author:
        params["author"] = author
    r = session.get(OL_SEARCH, params=params, timeout=20)
    r.raise_for_status()
    docs = (r.json() or {}).get("docs", [])
    # Prefer records that (a) list "kor" in language, (b) have a hangul title
    for d in docs:
        langs = d.get("language") or []
        t = d.get("title") or ""
        a = ", ".join(d.get("author_name") or [])
        if "kor" in langs and _has_hangul(t):
            return {"verified": True, "title": t, "author": a, "source": f"openlibrary/{d.get('key','?')}"}
    # Second pass: any hangul title even without a language tag
    for d in docs:
        t = d.get("title") or ""
        if _has_hangul(t):
            a = ", ".join(d.get("author_name") or [])
            return {"verified": True, "title": t, "author": a, "source": f"openlibrary/{d.get('key','?')}"}
    if docs:
        return {"verified": False, "title": title, "author": author, "source": "openlibrary/no-hangul-match"}
    return {"verified": False, "title": title, "author": author, "source": "openlibrary/empty"}


def lookup(title, author=""):
    """Return metadata dict; cache result forever (rerun to refresh)."""
    key = f"{title or ''}\x1f{author or ''}"
    con = _cache()
    row = con.execute("SELECT verified, title, author, source FROM lookup WHERE key=?", (key,)).fetchone()
    if row:
        return {"verified": bool(row[0]), "title": row[1], "author": row[2], "source": row[3]}

    s = requests.Session()
    s.headers["User-Agent"] = UA
    try:
        result = _openlibrary(title, author, s)
    except Exception as e:
        result = {"verified": False, "title": title, "author": author, "source": f"openlibrary-err:{e}"}

    con.execute("INSERT OR REPLACE INTO lookup(key, verified, title, author, source) VALUES (?,?,?,?,?)",
                (key, 1 if result["verified"] else 0, result["title"], result["author"], result["source"]))
    con.commit()
    time.sleep(0.5)   # polite to OL
    return result


def needs_verify(title):
    """True if the title looks like romanized Korean or English (no hangul)."""
    return bool(title) and not _has_hangul(title)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("title")
    ap.add_argument("--author", default="")
    args = ap.parse_args()
    print(lookup(args.title, args.author))
