#!/bin/bash
# Ingest completed EPUBs from /downloads/kbfm-<source>/ into Calibre.
# Each book gets a `source:<name>` tag so the library UI can show provenance.
set -euo pipefail

LIBRARY=/library
DOWNLOADS=/downloads
STATE=/state

mkdir -p "$LIBRARY" "$STATE/ingested"

# Initialize the library on first run
if [ ! -f "$LIBRARY/metadata.db" ]; then
  calibredb --with-library "$LIBRARY" list >/dev/null 2>&1 || true
fi

# Use a temp file for counters — bash `while` in a pipe runs in a subshell
# and counter variables can't propagate back to the parent shell.
counts=$(mktemp)
echo "0 0" > "$counts"

# `find … -print0 | while read -d ''` piping into a while loop puts the loop
# in a subshell. To keep counter increments visible, we use process substitution
# so the while loop runs in the current shell.
while IFS= read -r -d '' epub; do
  marker="$STATE/ingested/$(basename "$epub").done"
  [ -f "$marker" ] && continue

  parent=$(basename "$(dirname "$epub")")
  case "$parent" in
    kbfm-ia)     tag="source:ia" ;;
    kbfm-libgen) tag="source:libgen" ;;
    kbfm-aa)     tag="source:aa" ;;
    kbfm-zlib)   tag="source:zlib" ;;
    *)           tag="source:unknown" ;;
  esac

  if calibredb --with-library "$LIBRARY" add --tags "$tag" "$epub" >/dev/null 2>&1; then
    touch "$marker"
    read -r a s < "$counts"
    echo "$((a + 1)) $s" > "$counts"
    echo "[ingest] added ($tag) $(basename "$epub")"
  else
    touch "$marker"
    read -r a s < "$counts"
    echo "$a $((s + 1))" > "$counts"
    echo "[ingest] skipped $(basename "$epub")"
  fi
done < <(find "$DOWNLOADS" -type f -name '*.epub' -print0 2>/dev/null)

read -r added skipped < "$counts"
rm -f "$counts"
echo "[ingest] done  added=$added  skipped=$skipped"
