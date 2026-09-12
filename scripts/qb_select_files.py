#!/usr/bin/env python3
"""For each torrent in a category, enable only files whose name contains one of
the target md5 hashes. Anna's Archive book packs name every file by its md5, so
substring matching is safe and cheap.
"""
import argparse, os, sys, requests

QB = os.environ.get("QB_URL", "http://qbittorrent:8080")
USER = os.environ.get("QB_USER", "admin")
PW = os.environ.get("QB_PASS", "adminadmin")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--md5-list", required=True)
    args = ap.parse_args()

    wanted = {l.strip().lower() for l in open(args.md5_list) if l.strip()}
    if not wanted:
        print("[qb_select_files] empty md5 list, nothing to do", file=sys.stderr)
        return

    s = requests.Session()
    s.post(f"{QB}/api/v2/auth/login", data={"username": USER, "password": PW})
    ts = s.get(f"{QB}/api/v2/torrents/info", params={"category": args.category}).json()

    for t in ts:
        h = t["hash"]
        files = s.get(f"{QB}/api/v2/torrents/files", params={"hash": h}).json()
        keep, skip = [], []
        for f in files:
            fname = f["name"].lower()
            if any(m in fname for m in wanted):
                keep.append(f["index"])
            else:
                skip.append(f["index"])
        if skip:
            s.post(f"{QB}/api/v2/torrents/filePrio",
                   data={"hash": h, "id": "|".join(map(str, skip)), "priority": 0})
        if keep:
            s.post(f"{QB}/api/v2/torrents/filePrio",
                   data={"hash": h, "id": "|".join(map(str, keep)), "priority": 1})
        print(f"[qb_select_files] {t['name'][:60]}: keep={len(keep)} skip={len(skip)}", file=sys.stderr)


if __name__ == "__main__":
    main()
