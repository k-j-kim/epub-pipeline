#!/usr/bin/env python3
"""Scan Anna's Archive metadata dumps for Korean EPUBs and emit an md5 list."""
import argparse, glob, io, json, os, sqlite3, subprocess, sys

KO_HINTS = {"ko", "kor", "korean", "한국어"}


def stream_zst(path):
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE)
    for line in io.TextIOWrapper(p.stdout, encoding="utf-8", errors="replace"):
        yield line


def is_korean(rec):
    for k in ("language_codes", "search_only_fields"):
        v = rec.get(k)
        if isinstance(v, dict):
            v = v.get("search_most_likely_language_code") or v.get("language_codes")
        if isinstance(v, str) and v.lower() in KO_HINTS:
            return True
        if isinstance(v, list) and any(str(x).lower() in KO_HINTS for x in v):
            return True
    return False


def is_epub(rec):
    ext = (rec.get("extension") or rec.get("filetype") or "").lower()
    if ext == "epub":
        return True
    sof = rec.get("search_only_fields") or {}
    return (sof.get("search_extension") or "").lower() == "epub"


def filesize(rec):
    return int(rec.get("filesize") or (rec.get("search_only_fields") or {}).get("search_filesize") or 0)


def md5(rec):
    return rec.get("md5") or (rec.get("search_only_fields") or {}).get("search_md5")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata-glob", required=True)
    ap.add_argument("--min-kb", type=int, default=200)
    ap.add_argument("--max-mb", type=int, default=50)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("""CREATE TABLE IF NOT EXISTS korean_epubs (
        md5 TEXT PRIMARY KEY, filesize INTEGER, title TEXT, author TEXT,
        sent INTEGER DEFAULT 0, added_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    files = sorted(glob.glob(args.metadata_glob, recursive=True))
    if not files:
        print(f"[select_korean] no metadata files at {args.metadata_glob}", file=sys.stderr)
        sys.exit(0)

    min_bytes = args.min_kb * 1024
    max_bytes = args.max_mb * 1024 * 1024
    kept = 0

    for path in files:
        print(f"[select_korean] scanning {path}", file=sys.stderr)
        for line in stream_zst(path):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not is_epub(rec) or not is_korean(rec):
                continue
            fs = filesize(rec)
            if fs and not (min_bytes <= fs <= max_bytes):
                continue
            m = md5(rec)
            if not m:
                continue
            title = rec.get("title") or (rec.get("search_only_fields") or {}).get("search_title") or ""
            author = rec.get("author") or (rec.get("search_only_fields") or {}).get("search_author") or ""
            conn.execute(
                "INSERT OR IGNORE INTO korean_epubs(md5, filesize, title, author) VALUES (?, ?, ?, ?)",
                (m, fs, title[:500], author[:500]),
            )
            kept += 1
    conn.commit()

    with open(args.out, "w") as f:
        for (m,) in conn.execute("SELECT md5 FROM korean_epubs ORDER BY md5"):
            f.write(m + "\n")

    print(f"[select_korean] recorded {kept} rows; unique md5s={conn.execute('SELECT COUNT(*) FROM korean_epubs').fetchone()[0]}", file=sys.stderr)


if __name__ == "__main__":
    main()
