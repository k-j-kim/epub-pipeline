#!/usr/bin/env python3
"""One-shot cleanup: scan the Calibre library for books whose EPUB is
not actually Korean and remove them via `calibredb remove`.

Idempotent. Safe to re-run.
"""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from korean_check import is_korean

LIBRARY = "/library"


def calibre_list():
    out = subprocess.check_output(
        ["calibredb", "--with-library", LIBRARY, "list",
         "--fields", "id,title,formats", "--for-machine"],
        text=True,
    )
    return json.loads(out)


def main():
    to_remove = []
    for row in calibre_list():
        bid = row["id"]
        title = row.get("title", "")
        for fmt in row.get("formats", []):
            if fmt.lower().endswith(".epub"):
                ok, why = is_korean(fmt)
                if not ok:
                    to_remove.append((bid, title, why))
                break
    if not to_remove:
        print("[purge] library is clean, nothing to remove")
        return
    print(f"[purge] removing {len(to_remove)} non-korean books:")
    for bid, title, why in to_remove:
        print(f"  id={bid}  {title[:60]}  ({why})")
    ids = ",".join(str(b[0]) for b in to_remove)
    subprocess.check_call(["calibredb", "--with-library", LIBRARY, "remove", ids])
    print(f"[purge] done")


if __name__ == "__main__":
    main()
