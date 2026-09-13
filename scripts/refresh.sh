#!/bin/bash
# Refresh: pull new Korean EPUBs from each configured source.
# Sources are independent — failure of one doesn't stop the others.
set -uo pipefail

STATE=/state
mkdir -p "$STATE"
echo "[refresh] $(date -Iseconds) starting"

SOURCES="${SOURCES:-ia libgen zlib aa}"

run_one() {
  local name="$1" cmd="$2"
  echo "[refresh] --- source: $name ---"
  if bash -c "$cmd"; then
    echo "[refresh] $name ok"
  else
    echo "[refresh] $name failed (rc=$?) — continuing"
  fi
}

for src in $SOURCES; do
  case "$src" in
    ia)     run_one ia     "python3 /scripts/fetch_ia.py" ;;
    libgen) run_one libgen "python3 /scripts/fetch_libgen.py" ;;
    zlib)   run_one zlib   "python3 /scripts/fetch_zlib.py" ;;
    aa)     run_one aa     "python3 /scripts/fetch_aa.py" ;;
    *) echo "[refresh] unknown source: $src" ;;
  esac
done

# Then ingest whatever landed
/scripts/ingest.sh || true

echo "[refresh] $(date -Iseconds) done"
