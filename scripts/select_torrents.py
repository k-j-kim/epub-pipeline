#!/usr/bin/env python3
"""Pick which Anna's Archive torrents to grab.

Reads the AA /dyn/torrents.json index and splits torrents into:
  * metadata torrents — the latest `aa_derived_mirror_metadata` snapshot
    (small, decompresses to md5→language→format tables).
  * book torrents    — zlib + libgen fiction packs. Large; qBittorrent
    file-selection will keep only files whose md5 is in our Korean set.

Schema per entry (as of AA 2026):
  { "url": ".../*.torrent",
    "top_level_group_name": "managed_by_aa" | "external" | "other_aa",
    "group_name": "zlib" | "libgen_rs_fic" | "aa_derived_mirror_metadata" | ...,
    "display_name": "*.torrent",
    "btih": "hex40",
    "magnet_link": "magnet:?...",   (may be missing on some entries)
    "obsolete": bool, "embargo": bool, "partially_broken": bool,
    "data_size": int, "seeders": int, ... }
"""
import argparse, json, sys

METADATA_GROUPS = {"aa_derived_mirror_metadata"}

BOOK_GROUPS = {
    "zlib",
    "libgen_rs_fic",
    "libgen_li_fic",
    # Uncomment to widen coverage; these add tens of GB of torrent metadata:
    # "libgen_rs_non_fic", "libgen_li_non_fic",
    # "upload",  # user uploads on AA
}


def ref(entry):
    """Return a magnet or .torrent URL suitable for qBittorrent."""
    m = entry.get("magnet_link")
    if m:
        return m
    btih = entry.get("btih")
    name = entry.get("display_name") or ""
    if btih:
        return f"magnet:?xt=urn:btih:{btih}&dn={name}"
    return entry.get("url")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--out-metadata", required=True)
    ap.add_argument("--out-books", required=True)
    ap.add_argument("--metadata-latest-only", action="store_true", default=True)
    args = ap.parse_args()

    data = json.load(open(args.index))
    if isinstance(data, dict):
        data = data.get("torrents") or []

    meta_candidates = []
    books = []
    for e in data:
        if e.get("obsolete") or e.get("embargo") or e.get("partially_broken"):
            continue
        gn = e.get("group_name")
        if gn in METADATA_GROUPS:
            meta_candidates.append(e)
        elif gn in BOOK_GROUPS:
            r = ref(e)
            if r:
                books.append(r)

    if args.metadata_latest_only and meta_candidates:
        # display_name looks like "aa_derived_mirror_metadata_YYYYMMDD.torrent" —
        # sort by that suffix and keep only the newest.
        meta_candidates.sort(key=lambda e: e.get("display_name", ""), reverse=True)
        meta_candidates = [meta_candidates[0]]

    meta = [r for r in (ref(e) for e in meta_candidates) if r]

    with open(args.out_metadata, "w") as f:
        f.write("\n".join(meta))
    with open(args.out_books, "w") as f:
        f.write("\n".join(books))

    print(f"[select_torrents] metadata={len(meta)} books={len(books)}", file=sys.stderr)
    if meta_candidates:
        print(f"[select_torrents] latest metadata: {meta_candidates[0].get('display_name')}", file=sys.stderr)


if __name__ == "__main__":
    main()
