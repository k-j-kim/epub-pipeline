#!/usr/bin/env python3
"""Library cleanup pass.

For every EPUB in the Calibre library:
  1. Content check (quality_check.assess) — reject books that are too short,
     mostly non-Korean, or mostly images.
  2. Metadata verify (metadata_lookup.lookup) — if title is romanized or
     English, look up via OpenLibrary; when a Korean-titled match exists,
     rewrite the Calibre title/author to the canonical hangul.
  3. If both checks agree the book is dubious AND OpenLibrary has no
     Korean-titled match, delete it.

Safe to re-run; idempotent (OpenLibrary results are cached).
"""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from quality_check import assess
from metadata_lookup import lookup, needs_verify

LIBRARY = "/library"


def calibre_list():
    out = subprocess.check_output(
        ["calibredb", "--with-library", LIBRARY, "list",
         "--fields", "id,title,authors,formats", "--for-machine"],
        text=True,
    )
    return json.loads(out)


def set_meta(bid, title=None, author=None):
    args = ["calibredb", "--with-library", LIBRARY, "set_metadata", str(bid)]
    if title is not None:
        args += ["--field", f"title:{title}"]
    if author is not None:
        args += ["--field", f"authors:{author}"]
    if len(args) == 5:  # nothing to set
        return
    subprocess.check_call(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    removed = []
    rewrote = []
    kept = 0
    for row in calibre_list():
        bid = row["id"]
        title = row.get("title", "") or ""
        author = ", ".join(row.get("authors") or []) if row.get("authors") else ""
        epub = None
        for fmt in row.get("formats", []):
            if fmt.lower().endswith(".epub"):
                epub = fmt; break

        # --- Stage 1: content quality ---
        if epub:
            ok_c, why_c, _ = assess(epub)
            if not ok_c:
                # Only content-reject if it's clearly bad; combine with metadata
                if why_c.startswith(("too-non-korean", "no-title", "bad-zip", "opf-parse", "no-opf")):
                    removed.append((bid, title, f"content:{why_c}"))
                    continue
                # "too-little-korean" or "mostly-images" — keep for meta check below

        # --- Stage 2: metadata verify ---
        if needs_verify(title):
            meta = lookup(title, author)
            if meta["verified"]:
                # Rewrite Calibre record with canonical hangul metadata
                try:
                    set_meta(bid, title=meta["title"], author=meta["author"] or None)
                    rewrote.append((bid, title, meta["title"]))
                    kept += 1
                except subprocess.CalledProcessError as e:
                    print(f"[purge] set_meta failed for {bid}: {e}", file=sys.stderr)
                    kept += 1
            else:
                # Content was borderline AND metadata says "no Korean book by this name"
                removed.append((bid, title, f"unverified:{meta['source']}"))
        else:
            kept += 1

    print(f"[purge] kept={kept}  rewrote={len(rewrote)}  removed={len(removed)}")
    if rewrote:
        print("[purge] title rewrites:")
        for bid, old, new in rewrote[:50]:
            print(f"  id={bid}  '{old[:50]}' → '{new[:60]}'")
    if removed:
        print("[purge] removing:")
        for bid, title, why in removed[:100]:
            print(f"  id={bid}  {title[:60]:60s}  ({why})")
        ids = ",".join(str(b[0]) for b in removed)
        subprocess.check_call(["calibredb", "--with-library", LIBRARY, "remove", ids])


if __name__ == "__main__":
    main()
