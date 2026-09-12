#!/bin/bash
set -e

# Persist env for cron (cron scrubs the environment)
printenv | grep -E '^(KINDLE_EMAIL|SMTP_|BOOKS_PER_WEEK|QB_|MIN_EPUB_KB|MAX_EPUB_MB|TZ)=' \
  > /etc/environment

mkdir -p /state /metadata /downloads /library

# First-boot bootstrap: if no metadata dump exists yet, trigger a refresh immediately
if [ ! -f /state/bootstrapped ]; then
  echo "[bootstrap] first boot — running initial refresh"
  /scripts/refresh.sh >> /state/refresh.log 2>&1 || echo "[bootstrap] refresh failed, will retry on cron"
  touch /state/bootstrapped
fi

echo "[worker] starting cron"
exec cron -f
