#!/bin/bash
# Ingest completed EPUBs from /downloads/kbfm-<source>/ into Calibre.
# Each book gets a `source:<name>` tag so the library UI can show provenance.
set -euo pipefail

LIBRARY=/library
DOWNLOADS=/downloads

mkdir -p "$LIBRARY" /state/ingested

# Initialize the library on first run
if [ ! -f "$LIBRARY/metadata.db" ]; then
  calibredb --with-library "$LIBRARY" list >/dev/null 2>&1 || true
fi

added=0
skipped=0
find "$DOWNLOADS" -type f -name '*.epub' -print0 2>/dev/null | while IFS= read -r -d '' epub; do
  marker="/state/ingested/$(basename "$epub").done"
  [ -f "$marker" ] && continue

  # Derive source from parent directory: /downloads/kbfm-ia/foo.epub → source:ia
  parent=$(basename "$(dirname "$epub")")
  case "$parent" in
    kbfm-ia)     tag="source:ia" ;;
    kbfm-libgen) tag="source:libgen" ;;
    kbfm-aa)     tag="source:aa" ;;
    *)           tag="source:unknown" ;;
  esac

  if calibredb --with-library "$LIBRARY" add --tags "$tag" "$epub" >/dev/null 2>&1; then
    touch "$marker"
    added=$((added + 1))
    echo "[ingest] added ($tag) $(basename "$epub")"
  else
    touch "$marker"
    skipped=$((skipped + 1))
    echo "[ingest] skipped (dup or bad) $(basename "$epub")"
  fi
done

echo "[ingest] done  added=$added  skipped=$skipped"
