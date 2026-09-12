# korean-books-for-mom

An all-in-one Docker Compose stack for a Synology NAS that:

1. Pulls Anna's Archive **monthly metadata dumps** via BitTorrent.
2. Filters for **Korean-language EPUBs** in a sane size range.
3. Torrents just those files (file-selection within larger book packs).
4. Ingests them into a **Calibre-web** library (dedupe + browse UI).
5. Emails **N random unread books per week** to a Kindle address.

Zero paid API keys. One home broadband line. Fully offline once the initial pull completes.

## Architecture

```
┌──────────────┐   monthly cron   ┌─────────────┐
│ AA torrents  │ ────────────────▶│  qBittorrent │
└──────────────┘                  └──────┬──────┘
                                         │ /downloads
                                         ▼
                                  ┌─────────────┐
                                  │   worker    │  cron: refresh / ingest / send
                                  │  (python)   │
                                  └──────┬──────┘
                                         │ calibredb add
                                         ▼
                                  ┌─────────────┐   SMTP    ┌──────────┐
                                  │ Calibre-web │──────────▶│  Kindle  │
                                  └─────────────┘           └──────────┘
```

Three containers:
- **qbittorrent** — LinuxServer image, WebUI on `:8080`.
- **calibre-web** — LinuxServer image + full Calibre for conversions, WebUI on `:8083`.
- **worker** — small Python/cron image built here; runs the refresh/ingest/send jobs.

## Prerequisites

- Synology DSM 7 with **Container Manager** (or SSH + `docker compose`).
- A dataset with at least **~200 GB free** (metadata + Korean EPUB slice + Calibre library).
- A **Gmail app-password** (or any SMTP creds) for send-to-Kindle.
- Your Kindle's `@kindle.com` address, with the SMTP sender added to Amazon's *Approved Personal Document E-mail List*.

## Install

```bash
# On the NAS, over SSH
cd /volume1/docker
git clone https://github.com/k-j-kim/korean-books-for-mom.git
cd korean-books-for-mom

cp .env.example .env
vi .env                       # fill in KINDLE_EMAIL + SMTP_* + DATA_DIR

mkdir -p "$(grep ^DATA_DIR .env | cut -d= -f2)"
docker compose up -d --build
```

First boot:

1. Open `http://<nas>:8080` (qBittorrent). Default login `admin` / `adminadmin`. Change the password, then update `QB_PASS` in `.env` and `docker compose up -d` to restart the worker.
2. Open `http://<nas>:8083` (Calibre-web). Point it at library path `/books` (this is the mounted `${DATA_DIR}/library`). Default login `admin` / `admin123` — change it.
3. The worker will kick off its first metadata pull automatically. This runs monthly at 03:00 on the 1st thereafter.

## Data flow & disk sizing

| Step | Size | Frequency |
|------|------|-----------|
| AA metadata dump (jsonl.seekable.zst) | ~50 GB | monthly |
| Korean EPUB slice (selected from book packs) | ~30–80 GB total, growing slowly | monthly diff, few GB |
| Calibre library (ingested EPUBs, deduped) | ~30–80 GB | grows over time |

Torrents outside the Korean slice are **file-selected to skip** inside qBittorrent, so you never actually download them — you only download the file-metadata (a few MB per pack).

## Cron schedule (`worker/crontab`)

- `0 3 1 * *` — monthly refresh: pull metadata, re-select, queue book torrents.
- `15 4 * * *` — daily ingest: move any newly-completed EPUBs into Calibre.
- `0 9 * * 0` — weekly Sunday 09:00: email `BOOKS_PER_WEEK` random unread books to the Kindle.

All times are in `TZ` from `.env` (default `Asia/Seoul`).

## What's fragile

- **AA torrent index schema.** `scripts/select_torrents.py` matches torrents by filename substring. If Anna's Archive renames their monthly torrents (rare — they've kept `aa_meta__aacid__file_info_and_search_history*` stable for years), update `METADATA_MATCHERS` / `BOOK_MATCHERS`.
- **AA metadata record shape.** `scripts/select_korean.py` tolerates both current AA field names and a few older aliases. If AA changes the JSONL schema, adjust `is_korean` / `is_epub` / `md5`.
- **Torrent seed health.** Older monthly torrents lose seeders. The stack always pulls the *latest* monthly, so you're generally fine; if a specific torrent stalls, remove it from qBittorrent and it'll be re-queued next refresh.
- **Send-to-Kindle attachment size.** Amazon caps at 50 MB per attachment — `MAX_EPUB_MB=50` in `.env` matches. Bump down if you hit rejections.
- **Korean-language detection quality.** AA's language field is derived, not always correct. Expect ~5% noise (books tagged `ko` that are actually EN/ZH with a Korean title). The Kindle picker will just skip anything that looks off — your mother can also delete on-device.

## Manual triggers

```bash
# Force an immediate refresh
docker exec kbfm-worker /scripts/refresh.sh

# Force an ingest
docker exec kbfm-worker /scripts/ingest.sh

# Send this week's picks now
docker exec kbfm-worker /scripts/send_to_kindle.py
```

## Uninstall

```bash
docker compose down
rm -rf ${DATA_DIR}   # careful — this deletes your library too
```

## Layout

```
korean-books-for-mom/
├── docker-compose.yml
├── .env.example
├── README.md
├── worker/
│   ├── Dockerfile
│   ├── crontab
│   └── entrypoint.sh
└── scripts/
    ├── refresh.sh            # monthly: pull metadata, select, queue book torrents
    ├── select_torrents.py    # picks which AA torrents to grab
    ├── select_korean.py      # scans AA metadata dumps → korean md5 list
    ├── qb_add.py             # qBittorrent WebUI: add / resume torrents
    ├── qb_wait.py            # block until a qb category finishes
    ├── qb_select_files.py    # inside a torrent, keep only files whose name contains a wanted md5
    ├── ingest.sh             # daily: move completed epubs into the Calibre library
    └── send_to_kindle.py     # weekly: pick N unread → email to Kindle
```

## License

Do whatever you want. No warranty. Copyright status of downloaded material varies by jurisdiction; that's on you.
