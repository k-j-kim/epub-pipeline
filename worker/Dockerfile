FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      cron sqlite3 curl ca-certificates zstd calibre \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir requests

WORKDIR /scripts
COPY crontab /etc/cron.d/kbfm
RUN chmod 0644 /etc/cron.d/kbfm && crontab /etc/cron.d/kbfm

# entrypoint runs cron in foreground; also runs initial bootstrap on first boot
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
