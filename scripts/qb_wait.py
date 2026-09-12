#!/usr/bin/env python3
"""Block until every torrent in a category is >= 99% complete or timeout expires."""
import argparse, os, sys, time, requests

QB = os.environ.get("QB_URL", "http://qbittorrent:8080")
USER = os.environ.get("QB_USER", "admin")
PW = os.environ.get("QB_PASS", "adminadmin")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--timeout", type=int, default=21600)
    args = ap.parse_args()

    s = requests.Session()
    s.post(f"{QB}/api/v2/auth/login", data={"username": USER, "password": PW})

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        r = s.get(f"{QB}/api/v2/torrents/info", params={"category": args.category})
        ts = r.json() if r.ok else []
        if not ts:
            time.sleep(30); continue
        done = sum(1 for t in ts if t["progress"] >= 0.99)
        print(f"[qb_wait] {done}/{len(ts)} complete in {args.category}", file=sys.stderr)
        if done == len(ts):
            return 0
        time.sleep(60)
    print(f"[qb_wait] timeout waiting on {args.category}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
