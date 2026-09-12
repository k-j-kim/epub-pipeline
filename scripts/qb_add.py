#!/usr/bin/env python3
"""Thin wrapper around qBittorrent WebUI API for add / pause / resume."""
import argparse, os, sys, requests

QB = os.environ.get("QB_URL", "http://qbittorrent:8080")
USER = os.environ.get("QB_USER", "admin")
PW = os.environ.get("QB_PASS", "adminadmin")


def login(s):
    r = s.post(f"{QB}/api/v2/auth/login", data={"username": USER, "password": PW})
    if r.text.strip() != "Ok.":
        raise SystemExit(f"qb login failed: {r.status_code} {r.text[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--category", required=True)
    ap.add_argument("--paused", default="false")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    s = requests.Session()
    login(s)

    if args.resume:
        r = s.get(f"{QB}/api/v2/torrents/info", params={"category": args.category})
        for t in r.json():
            s.post(f"{QB}/api/v2/torrents/resume", data={"hashes": t["hash"]})
        return

    if not args.file or not os.path.exists(args.file):
        print(f"[qb_add] no file {args.file}", file=sys.stderr)
        return
    magnets = [l.strip() for l in open(args.file) if l.strip()]
    if not magnets:
        return
    r = s.post(f"{QB}/api/v2/torrents/add", data={
        "urls": "\n".join(magnets),
        "category": args.category,
        "paused": args.paused,
        "savepath": f"/downloads/{args.category}",
    })
    print(f"[qb_add] added {len(magnets)} to {args.category}: {r.status_code}", file=sys.stderr)


if __name__ == "__main__":
    main()
