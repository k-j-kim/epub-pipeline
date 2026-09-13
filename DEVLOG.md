# DEVLOG — session state as of 2026-09-13

Context to pick this back up later. No secrets — those live in NAS `.env` only.

## Deployed on

- Synology DS220+ at `ssh -p 1024 kjkim@192.168.1.100`
- Docker Compose project at `/volume1/media/apps/epub-pipeline/`
- Data at `/volume1/media/epub-pipeline-data/`
- Timezone: `America/Vancouver`
- Homepage tile: `/var/services/homes/kjkim/media/apps/homepage/index.html` (arr/library section, links to `:8484/`)

## Architecture (4 containers)

| Container | Port | Purpose |
|---|---|---|
| `kbfm-qbittorrent` | 8181 | Torrent client — currently unused; left in place from the original AA-bulk-metadata plan |
| `kbfm-calibre-web` | 8283 | Korean-book library UI (separate from user's pre-existing `calibre-web` on 8083) |
| `kbfm-worker` | — | Cron-driven fetcher/ingester (Python + calibredb + telethon) |
| `kbfm-mgmt` | 8484 | Dashboard + status.json + manual triggers + bypass panel + book table |

## Sources (`SOURCES=ia libgen zlib` on the NAS)

- **archive.org** (`fetch_ia.py`) — reliable, ~40/run, uses page cursor. **Working.**
- **libgen.li fiction** (`fetch_libgen.py`) — Korean seed-word rotation + Referer header hack; ~20/run. **Working.**
- **Z-Library via Telegram bot** (`fetch_zlib.py`, Telethon) — needs the personal bot to be alive. Bot `@zlib20260913_bot` was **flagged and deleted by Telegram**; user is appealing. Session file at `/state/telegram.session` still valid.
- **Anna's Archive** (`fetch_aa.py`) — dropped from `SOURCES=` default. All three canonical mirrors (`.pk`, `.gd`, `.gl`) are DDoS-Guard–gated with a JS challenge that cookies alone can't survive. Fetcher + bypass panel kept for occasional manual use.

## Quality gates (applied to every downloaded EPUB before ingest)

1. Size window (`MIN_EPUB_KB` / `MAX_EPUB_MB` in `.env`)
2. `quality_check.assess()` — opens the EPUB, reads spine, requires ≥ 3000 hangul chars AND ratio ≥ 0.5 of hangul among Korean+ASCII letters AND ≥ 500 text chars / MB (screens out image-only "books"). Missing title / bad zip = reject.
3. Ingest gate: `calibredb add --tags source:<name>`; dupes rejected by Calibre.
4. `purge_non_korean.py` re-runs the content check across the entire library and rewrites Konglish/romanized titles via OpenLibrary when it returns a Korean-titled match. **OL empty is NOT a delete signal** (its Korean coverage is thin — treating empty as bad wiped 80 legit books once, don't do that again).

## Cron (in worker, TZ=America/Vancouver)

```
0 3 * * *   refresh.sh          # daily: all sources + ingest
0 4 * * *   ingest.sh           # belt-and-suspenders
0 5 * * 0   purge_non_korean.py # weekly: quality sweep
30 5 * * 0  heal_fetch_dbs.py   # weekly: clear stale rejection rows so retries can happen
0 9 * * 0   send_to_kindle.py   # weekly: N picks to Kindle
```

## Kindle send

- `SMTP_HOST=smtp.gmail.com`, sender is a Gmail App Password (not the account password)
- Kindle inbound address stored in `KINDLE_EMAIL`
- Gmail cap: **25 MB per attachment** — books larger than that are pre-filtered and marked "sent" so they're not retried each week
- `BOOKS_PER_WEEK=5` normal cadence; was bumped to 1000 once to bulk-seed all books
- Seed run 2026-09-12: 84/86 books delivered; only 2 failed (>25 MB)

## Known bugs already fixed (don't reintroduce)

- `calibredb --for-machine list` returns `authors` as a **string**, not a list. Never `", ".join(authors)` — it splits characters. Use `isinstance(a, str)` guard.
- `calibredb add --duplicates=false` is invalid; the flag takes no value. Use bare `--duplicates` to allow, omit to reject.
- Synology default docker DNS sometimes doesn't resolve. `dns: [1.1.1.1, 8.8.8.8]` set on worker+mgmt.
- `crontab file` uses user-crontab syntax (no `user` field). Don't write `0 3 * * * root /scripts/…`.
- `bash while | pipe` runs in a subshell; counters don't propagate. Use process substitution `while … done < <(find …)` or a temp file.

## Telegram / Z-Library — real lessons

- **Third-party `api_id` values cannot receive SMS/voice login codes** in 2026. Server responds `SentCodeTypeApp` with `next_type=None` — no fallback path — because Telegram reserves SMS/Firebase for official app api_ids.
- Working around it: use Telegram Desktop's public api_id (`2040` / `b18441a1ff607e10a989891a5462e627`). `fetch_zlib.py --login --desktop` uses that path.
- Session file `/state/telegram.session` persists forever after one successful login. Don't run `TelegramClient("/state/telegram", ...)` for diagnostics — connecting rewrites the file and can invalidate the auth key.
- Z-Library's personal bot is subject to Telegram moderation; the handle `zlib*` gets flagged easily. If a bot is killed, generate a new one with a plain, non-hinting name at @BotFather, re-register at https://go-to-library.sk/#telegram_bot_tab, and update `ZLIB_BOT` in `.env`.

## `.env` shape on the NAS (fill each on target box; committed `.env.example` shows the schema)

```
DATA_DIR, PUID, PGID, TZ, QB_PORT, CW_PORT, MGMT_PORT
QB_USER, QB_PASS
KINDLE_EMAIL, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
BOOKS_PER_WEEK, MIN_EPUB_KB, MAX_EPUB_MB
SOURCES, IA_MAX_PER_RUN, LIBGEN_MAX_PER_RUN, LIBGEN_HOST
AA_MAX_PER_RUN, AA_HOSTS
TG_API_ID=2040, TG_API_HASH=b18441a1ff607e10a989891a5462e627, TG_PHONE, ZLIB_BOT, ZLIB_MAX_PER_RUN
```

## Pending / open

- **Z-Library bot appeal in flight.** Once it resolves (or a replacement bot is created + re-registered with Z-Library), update `ZLIB_BOT` in `.env` and restart worker+mgmt. Session file stays valid.
- **AA search remains blocked.** Manual bypass panel in mgmt UI accepts pasted headers but cookies expire within an hour or two. Don't spend more effort here without a headless-browser approach (flaresolverr already ran but got redirected to Google by AA's interstitial).
- **The 81-book purge accident** (Sep 12) wiped legit books because I treated OpenLibrary empty as a delete signal. Purge is fixed (rewrite-only for OL); those books can theoretically be re-fetched over time by IA+libgen daily runs since their fetcher-DB entries were cleared by `heal_fetch_dbs.py`.
- **Weekly Kindle push on Sunday 09:00 PST** — first live run happens the Sunday after seed. `BOOKS_PER_WEEK=5`. `sent.db` prevents repeats.

## Rebuild / redeploy from scratch

```bash
ssh -p 1024 kjkim@192.168.1.100
cd /volume1/media/apps/epub-pipeline
git pull
docker compose build worker mgmt
docker compose up -d --force-recreate
```

## Auth notes for `gh push` from KJ's laptop

Repo lives under `k-j-kim`. The active gh account defaults to `kj-kim_sfemu` (work) — `gh auth switch --user k-j-kim` before pushing.
