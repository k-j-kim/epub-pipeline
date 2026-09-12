#!/bin/bash
# Monthly refresh: pull Anna's Archive metadata, select Korean EPUBs, queue torrents.
set -euo pipefail

META_DIR=/metadata
STATE=/state
mkdir -p "$META_DIR" "$STATE"

echo "[refresh] $(date -Iseconds) starting"

# Anna's Archive publishes a monthly torrent index at annas-archive.org/torrents.
# The metadata torrent we want is the "aa_meta" jsonl.seekable.zst family.
# We resolve the *current* torrent list dynamically so this survives monthly rotation.
AA_HOSTS="${AA_HOSTS:-annas-archive.gs annas-archive.org annas-archive.se}"
FLARESOLVERR_URL="${FLARESOLVERR_URL:-}"

fetch() {
  # $1 = url, $2 = out file. Returns 0 on real success (JSON, not challenge HTML).
  local url="$1" out="$2"
  # Try direct curl first
  if curl -fsSL --max-time 60 -A "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0" "$url" -o "$out"; then
    # Reject JS-challenge pages: real torrent index is >100 KB JSON starting with [ or {
    if head -c 1 "$out" | grep -qE '[\[{]' && [ "$(stat -c%s "$out")" -gt 10000 ]; then
      return 0
    fi
    echo "[refresh] direct fetch got challenge/short body from $url"
  fi
  # Fallback: flaresolverr
  if [ -n "$FLARESOLVERR_URL" ]; then
    echo "[refresh] trying flaresolverr at $FLARESOLVERR_URL"
    local resp
    resp=$(curl -sS --max-time 120 -H 'Content-Type: application/json' \
      -d "{\"cmd\":\"request.get\",\"url\":\"$url\",\"maxTimeout\":90000}" \
      "$FLARESOLVERR_URL/v1")
    # Response is JSON envelope; the actual body is in .solution.response
    echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.stdout.write(d.get('solution',{}).get('response',''))" > "$out"
    if head -c 1 "$out" | grep -qE '[\[{]' && [ "$(stat -c%s "$out")" -gt 10000 ]; then
      return 0
    fi
    echo "[refresh] flaresolverr also returned unexpected body"
  fi
  return 1
}

ok=0
for host in $AA_HOSTS; do
  echo "[refresh] trying $host"
  if fetch "https://$host/torrents.json" "$STATE/torrents.json"; then
    echo "[refresh] fetched torrent index from $host ($(stat -c%s "$STATE/torrents.json") bytes)"
    ok=1; break
  fi
done
if [ "$ok" != "1" ]; then
  echo "[refresh] all AA hosts failed — will retry next cycle"
  echo "[refresh] set AA_HOSTS + FLARESOLVERR_URL in .env"
  exit 0
fi

python3 /scripts/select_torrents.py \
  --index "$STATE/torrents.json" \
  --out-metadata "$STATE/metadata_torrents.txt" \
  --out-books "$STATE/book_torrents.txt"

# Push the metadata torrents to qBittorrent (small, fast).
python3 /scripts/qb_add.py --file "$STATE/metadata_torrents.txt" --category kbfm-meta --paused false

echo "[refresh] waiting for metadata torrents to complete (up to 6h)"
python3 /scripts/qb_wait.py --category kbfm-meta --timeout 21600 || {
  echo "[refresh] metadata not complete — will run selection on partial data anyway"
}

# Decompress + query for Korean EPUBs → produces md5.list
python3 /scripts/select_korean.py \
  --metadata-glob '/downloads/kbfm-meta/**/*.jsonl.seekable.zst' \
  --min-kb "${MIN_EPUB_KB:-200}" \
  --max-mb "${MAX_EPUB_MB:-50}" \
  --out "$STATE/korean_md5.list" \
  --db "$STATE/korean.sqlite"

echo "[refresh] selected $(wc -l < "$STATE/korean_md5.list") Korean EPUBs"

# Queue book-pack torrents (file-selection happens after add via qb_select_files.py)
python3 /scripts/qb_add.py --file "$STATE/book_torrents.txt" --category kbfm-books --paused true
python3 /scripts/qb_select_files.py --category kbfm-books --md5-list "$STATE/korean_md5.list"
python3 /scripts/qb_add.py --resume --category kbfm-books

echo "[refresh] $(date -Iseconds) done"
