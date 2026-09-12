#!/usr/bin/env python3
"""Pick which Anna's Archive torrents to grab.

Reads the AA torrent index and splits torrents into:
  * metadata torrents — small jsonl.seekable.zst files we must fully download
  * book torrents — large EPUB packs we'll partially download by file-selection

The AA index schema evolves; we match on filename substrings that have been
stable for years. If AA changes their layout, update the MATCHERS below.
"""
import argparse, json, sys, re

METADATA_MATCHERS = [
    # These jsonl files carry per-md5 language + filesize + filetype.
    "aa_meta__aacid__file_info_and_search_history",
    "annas_archive_meta__aacid__file_info_and_search_history",
]

# Book packs likely to contain Korean EPUBs. libgen fiction + zlib are the
# highest-yield; we skip scan-heavy sources (scimag, duxiu, IA-scans).
BOOK_MATCHERS = [
    "zlib",
    "libgen_rs_fic",
    "libgen_li_fic",
    "libgenli_fic",
]

BOOK_SKIP = [
    "scimag", "duxiu", "magzdb", "isbndb", "ia_scan", "trantor",
]


def matches(name, needles):
    n = name.lower()
    return any(x in n for x in needles)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--out-metadata", required=True)
    ap.add_argument("--out-books", required=True)
    args = ap.parse_args()

    data = json.load(open(args.index))
    # AA's torrents.json is a list of {torrent_path, magnet, btih, top_level_group_name, ...}
    entries = data if isinstance(data, list) else data.get("torrents", [])

    meta, books = [], []
    for e in entries:
        name = e.get("torrent_path") or e.get("display_name") or ""
        magnet = e.get("magnet") or e.get("magnet_link")
        btih = e.get("btih") or e.get("infohash")
        if not (magnet or btih):
            continue
        ref = magnet if magnet else f"magnet:?xt=urn:btih:{btih}&dn={name}"

        if matches(name, METADATA_MATCHERS):
            meta.append(ref)
        elif matches(name, BOOK_MATCHERS) and not matches(name, BOOK_SKIP):
            books.append(ref)

    with open(args.out_metadata, "w") as f:
        f.write("\n".join(meta))
    with open(args.out_books, "w") as f:
        f.write("\n".join(books))

    print(f"[select_torrents] metadata={len(meta)} books={len(books)}", file=sys.stderr)


if __name__ == "__main__":
    main()
