#!/bin/bash
# Ingest completed EPUBs from /downloads into the Calibre library.
set -euo pipefail

LIBRARY=/library
DOWNLOADS=/downloads

mkdir -p "$LIBRARY"

# Initialize the library on first run
if [ ! -f "$LIBRARY/metadata.db" ]; then
  echo "[ingest] initializing empty Calibre library at $LIBRARY"
  calibredb --with-library "$LIBRARY" list >/dev/null 2>&1 || true
fi

# Find every .epub under downloads and add it. Calibre dedupes by title+authors.
find "$DOWNLOADS" -type f -name '*.epub' -print0 2>/dev/null | while IFS= read -r -d '' epub; do
  # Skip if we've seen this file before
  marker="/state/ingested/$(basename "$epub").done"
  [ -f "$marker" ] && continue
  mkdir -p /state/ingested

  if calibredb --with-library "$LIBRARY" add "$epub" >/dev/null 2>&1; then
    touch "$marker"
    echo "[ingest] added $(basename "$epub")"
  else
    echo "[ingest] skipped (dup?) $(basename "$epub")"
    touch "$marker"
  fi
done

echo "[ingest] library now has $(calibredb --with-library "$LIBRARY" list -f id 2>/dev/null | wc -l) records"
