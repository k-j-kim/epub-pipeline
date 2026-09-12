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
AA_HOSTS="${AA_HOSTS:-annas-archive.org annas-archive.se annas-archive.li}"
ok=0
for host in $AA_HOSTS; do
  echo "[refresh] trying $host"
  if curl -fsSL --max-time 30 "https://$host/torrents.json" -o "$STATE/torrents.json"; then
    echo "[refresh] fetched torrent index from $host"
    ok=1; break
  fi
done
if [ "$ok" != "1" ]; then
  echo "[refresh] all AA hosts failed — will retry next cycle"
  echo "[refresh] set AA_HOSTS in .env to override (space-separated)"
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
