#!/usr/bin/env python3
"""Heal the per-source fetch DBs.

Fetchers record every attempt in a sqlite: successes (path=on-disk) and
transient failures (path='' or 'not-korean'). This script:

  * Removes rows whose path is empty or a placeholder — so next fetch retries them.
  * Removes rows whose path is set but the file no longer exists on disk —
    happens when a book got purged from Calibre; we want to re-download it.
  * Removes rows for files that ingest markers no longer exist for — same reason.

Idempotent. Safe to run anytime.
"""
import os, sqlite3, sys
from pathlib import Path

STATE = Path("/state")
DBS = [
    (STATE / "ia.sqlite",     "identifier"),
    (STATE / "libgen.sqlite", "md5"),
    (STATE / "aa.sqlite",     "md5"),
]


def heal(db_path, key_col):
    table = "fetched"
    if not db_path.exists():
        return (0, 0, 0)
    con = sqlite3.connect(db_path)
    # Rows w/ no real path — clear so they can retry
    empty = con.execute(
        f"DELETE FROM {table} WHERE path IS NULL OR path = '' OR path IN ('not-korean','not-verified')"
    ).rowcount
    # Rows w/ path that no longer exists on disk
    missing = 0
    for row in list(con.execute(f"SELECT {key_col}, path FROM {table} WHERE path != ''").fetchall()):
        key, path = row
        if path and not os.path.exists(path):
            con.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (key,))
            missing += 1
    total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    con.commit()
    return empty, missing, total


def main():
    for db, key in DBS:
        name = db.stem
        try:
            empty, missing, total = heal(db, key)
            print(f"[heal] {name}: cleared_empty={empty}  cleared_missing_file={missing}  remaining={total}")
        except Exception as e:
            print(f"[heal] {name}: FAILED {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
