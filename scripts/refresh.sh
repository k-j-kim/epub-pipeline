#!/bin/bash
# Monthly refresh: pull Anna's Archive metadata, select Korean EPUBs, queue torrents.
#
# When the AA host serves an anti-bot interstitial instead of the real
# torrents.json, we STOP and write /state/needs_bypass with the failing URL.
# The mgmt UI surfaces that and lets a human paste headers (cookies + UA)
# copied from their real browser after clicking through. Those headers get
# saved to /state/aa_headers.txt and used on the next attempt.
set -euo pipefail

META_DIR=/metadata
STATE=/state
mkdir -p "$META_DIR" "$STATE"

echo "[refresh] $(date -Iseconds) starting"

AA_HOSTS="${AA_HOSTS:-annas-archive.gs annas-archive.org annas-archive.se}"
HDR_FILE="$STATE/aa_headers.txt"
BYPASS_FLAG="$STATE/needs_bypass"
CHALLENGE_BODY="$STATE/last_challenge.html"

# Clear the bypass flag; we'll re-set it if we hit a challenge below
rm -f "$BYPASS_FLAG"

# Common curl args (user headers file if present)
CURL_HEADERS=()
if [ -s "$HDR_FILE" ]; then
  echo "[refresh] using saved browser headers from $HDR_FILE"
  # Each line is a full HTTP header "Name: value"
  while IFS= read -r line; do
    [ -n "$line" ] && CURL_HEADERS+=(-H "$line")
  done < "$HDR_FILE"
fi

# Body-shape test: real torrents.json is JSON >10 KB starting with [ or {
looks_real() {
  local f="$1"
  [ -s "$f" ] || return 1
  local first
  first=$(head -c 1 "$f" 2>/dev/null || true)
  case "$first" in [\[{]) : ;; *) return 1 ;; esac
  [ "$(stat -c%s "$f")" -gt 10000 ]
}

fetch_json() {
  # $1 = url, $2 = out file
  local url="$1" out="$2"
  curl -fsSL --max-time 60 \
    -A "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0" \
    "${CURL_HEADERS[@]}" \
    "$url" -o "$out" || return 1
  if looks_real "$out"; then
    return 0
  fi
  # Save the challenge body for the mgmt UI to display
  cp -f "$out" "$CHALLENGE_BODY" 2>/dev/null || true
  return 1
}

ok=0
last_url=""
for host in $AA_HOSTS; do
  url="https://$host/torrents.json"
  last_url="$url"
  echo "[refresh] trying $url"
  if fetch_json "$url" "$STATE/torrents.json"; then
    echo "[refresh] fetched torrent index from $host ($(stat -c%s "$STATE/torrents.json") bytes)"
    ok=1; break
  else
    echo "[refresh] $host failed or returned bot-gate body"
  fi
done

if [ "$ok" != "1" ]; then
  echo "[refresh] every host failed — writing $BYPASS_FLAG for manual bypass"
  printf "%s\n" "$last_url" > "$BYPASS_FLAG"
  cat <<EOM
[refresh] MANUAL STEP REQUIRED:
  1. Open $last_url in a real browser (Chrome/Firefox).
  2. Click through the "Loading..." page until you see the real JSON/site.
  3. Open DevTools → Network → click the /torrents.json request → 'Copy as cURL'.
  4. Paste it into the mgmt UI's "bot bypass" box at http://<nas>:8484/#bypass
     — we'll extract the Cookie + User-Agent and reuse them.
EOM
  exit 0
fi

# --- normal pipeline continues below ---

python3 /scripts/select_torrents.py \
  --index "$STATE/torrents.json" \
  --out-metadata "$STATE/metadata_torrents.txt" \
  --out-books "$STATE/book_torrents.txt"

python3 /scripts/qb_add.py --file "$STATE/metadata_torrents.txt" --category kbfm-meta --paused false

echo "[refresh] waiting for metadata torrents to complete (up to 6h)"
python3 /scripts/qb_wait.py --category kbfm-meta --timeout 21600 || {
  echo "[refresh] metadata not complete — will run selection on partial data anyway"
}

python3 /scripts/select_korean.py \
  --metadata-glob '/downloads/kbfm-meta/**/*.jsonl.seekable.zst' \
  --min-kb "${MIN_EPUB_KB:-200}" \
  --max-mb "${MAX_EPUB_MB:-50}" \
  --out "$STATE/korean_md5.list" \
  --db "$STATE/korean.sqlite"

echo "[refresh] selected $(wc -l < "$STATE/korean_md5.list") Korean EPUBs"

python3 /scripts/qb_add.py --file "$STATE/book_torrents.txt" --category kbfm-books --paused true
python3 /scripts/qb_select_files.py --category kbfm-books --md5-list "$STATE/korean_md5.list"
python3 /scripts/qb_add.py --resume --category kbfm-books

echo "[refresh] $(date -Iseconds) done"
