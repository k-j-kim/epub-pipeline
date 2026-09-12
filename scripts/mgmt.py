#!/usr/bin/env python3
"""Tiny management dashboard for the epub-pipeline stack.

Serves an HTML page + JSON status endpoint + POST trigger endpoints
on port 8484. Shares volumes with the worker container so it can read
state files (sqlite counts, log tails) and invoke the scripts directly.
"""
import json, os, sqlite3, subprocess, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = Path("/state")
LIBRARY = Path("/library")
DOWNLOADS = Path("/downloads")

TRIGGERS = {
    "refresh": ["/scripts/refresh.sh"],
    "ingest":  ["/scripts/ingest.sh"],
    "send":    ["/scripts/send_to_kindle.py"],
}

# Track in-flight background jobs so the UI can show "running"
running = {}
running_lock = threading.Lock()


def run_trigger(name):
    with running_lock:
        if running.get(name):
            return False, "already running"
        running[name] = time.time()
    def go():
        try:
            log = STATE / f"{name}.log"
            with open(log, "a") as f:
                f.write(f"\n=== manual trigger {datetime.now().isoformat()} ===\n")
                subprocess.call(TRIGGERS[name], stdout=f, stderr=subprocess.STDOUT)
        finally:
            with running_lock:
                running.pop(name, None)
    threading.Thread(target=go, daemon=True).start()
    return True, "started"


def sqlite_count(db, sql):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        return con.execute(sql).fetchone()[0]
    except Exception:
        return None


def library_count():
    if not (LIBRARY / "metadata.db").exists():
        return 0
    return sqlite_count(LIBRARY / "metadata.db", "SELECT COUNT(*) FROM books") or 0


def status():
    korean = STATE / "korean.sqlite"
    sent   = STATE / "sent.db"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "korean_candidates": sqlite_count(korean, "SELECT COUNT(*) FROM korean_epubs") if korean.exists() else None,
            "library_books":     library_count(),
            "sent_books":        sqlite_count(sent, "SELECT COUNT(*) FROM sent") if sent.exists() else 0,
        },
        "last_logs": {
            name: tail_log(STATE / f"{name}.log") for name in ("refresh", "ingest", "kindle")
        },
        "running": {name: (time.time() - t) for name, t in running.items()},
        "env": {
            "BOOKS_PER_WEEK": os.environ.get("BOOKS_PER_WEEK"),
            "KINDLE_EMAIL":   os.environ.get("KINDLE_EMAIL", "").replace(os.environ.get("KINDLE_EMAIL","")[:3], "***", 1) if os.environ.get("KINDLE_EMAIL") else None,
            "TZ":             os.environ.get("TZ"),
        },
    }


def tail_log(path, lines=8):
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.strip().splitlines()[-lines:])
    except Exception as e:
        return f"(err: {e})"


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>epub-pipeline</title>
<style>
  :root {
    --bg:#0b0b0b; --fg:#ececec; --muted:#8a8a8a; --card:#161616;
    --border:#2a2a2a; --accent:magenta; --ok:#3ecf6b; --bad:#d64545;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#fafafa; --fg:#111; --muted:#666; --card:#fff; --border:#e2e2e2; --accent:#0a7c2f; }
  }
  *{box-sizing:border-box} html,body{margin:0;padding:0}
  body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
  main{max-width:1100px;margin:0 auto;padding:1.25rem 1rem 3rem}
  header{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:1.25rem}
  header h1{font-size:1.15rem;font-weight:600;margin:0;letter-spacing:.02em}
  header .clock{color:var(--muted);font-variant-numeric:tabular-nums;font-size:.9rem}
  .section-title{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.12em;margin:1.5rem 0 .6rem}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.6rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1rem}
  .stat .n{font-size:1.8rem;font-weight:600;font-variant-numeric:tabular-nums}
  .stat .l{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-top:.2rem}
  .row{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.4rem}
  a.btn, button.btn{
    display:inline-flex;align-items:center;gap:.4rem;background:var(--card);border:1px solid var(--border);
    border-radius:10px;padding:.55rem .8rem;color:var(--fg);text-decoration:none;cursor:pointer;font:inherit;
  }
  a.btn:hover,button.btn:hover{border-color:var(--accent)}
  button.btn[disabled]{opacity:.5;cursor:not-allowed}
  pre.log{background:#000;color:#ddd;font-size:.75rem;border-radius:8px;padding:.7rem;overflow:auto;max-height:180px;margin:.4rem 0 0}
  .flash{margin-top:.5rem;font-size:.85rem;color:var(--muted)}
  @media (prefers-color-scheme: light){ pre.log{background:#111;color:#eee} }
</style>
</head>
<body>
<main>
  <header>
    <h1>epub-pipeline</h1>
    <span class="clock" id="clock"></span>
  </header>

  <div class="section-title">status</div>
  <div class="grid" id="stats"></div>

  <div class="section-title">links</div>
  <div class="grid">
    <a class="btn" id="qb-link" target="_blank" rel="noopener">▤ qBittorrent</a>
    <a class="btn" id="cw-link" target="_blank" rel="noopener">📚 Calibre-web</a>
  </div>

  <div class="section-title">manual triggers</div>
  <div class="row">
    <button class="btn" data-t="refresh">↻ refresh (pull metadata + queue)</button>
    <button class="btn" data-t="ingest">＋ ingest into library</button>
    <button class="btn" data-t="send">✉ send weekly picks now</button>
  </div>
  <div class="flash" id="flash"></div>

  <div class="section-title">recent logs</div>
  <div id="logs"></div>
</main>

<script>
const HOST = location.hostname;
document.getElementById("qb-link").href = `http://${HOST}:__QB_PORT__/`;
document.getElementById("cw-link").href = `http://${HOST}:__CW_PORT__/`;

async function refresh() {
  const r = await fetch("/status.json", { cache: "no-store" });
  const s = await r.json();

  const stats = document.getElementById("stats");
  stats.innerHTML = "";
  const cards = [
    ["korean candidates (metadata)", s.counts.korean_candidates ?? "—"],
    ["books in library",             s.counts.library_books ?? 0],
    ["books already sent",           s.counts.sent_books ?? 0],
  ];
  for (const [l, n] of cards) {
    const c = document.createElement("div");
    c.className = "card stat";
    c.innerHTML = `<div class="n">${n}</div><div class="l">${l}</div>`;
    stats.appendChild(c);
  }

  // buttons: disable if that trigger is running
  document.querySelectorAll("button.btn[data-t]").forEach(b => {
    const t = b.dataset.t;
    const running = s.running && (t in s.running);
    b.disabled = running;
    b.textContent = b.textContent.replace(/ • running \d+s$/, "");
    if (running) b.textContent += ` • running ${Math.round(s.running[t])}s`;
  });

  // logs
  const wrap = document.getElementById("logs");
  wrap.innerHTML = "";
  for (const [name, body] of Object.entries(s.last_logs)) {
    const h = document.createElement("div");
    h.className = "section-title";
    h.textContent = name;
    h.style.margin = "0.6rem 0 0.2rem";
    wrap.appendChild(h);
    const pre = document.createElement("pre");
    pre.className = "log";
    pre.textContent = body || "(no log yet)";
    wrap.appendChild(pre);
  }
}

document.querySelectorAll("button.btn[data-t]").forEach(b => {
  b.addEventListener("click", async () => {
    const t = b.dataset.t;
    const r = await fetch(`/trigger/${t}`, { method: "POST" });
    const j = await r.json().catch(() => ({}));
    document.getElementById("flash").textContent = `${t}: ${j.status || r.status}`;
    setTimeout(refresh, 400);
  });
});

const tickClock = () => {
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};
tickClock(); setInterval(tickClock, 30000);
refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = (INDEX_HTML
                    .replace("__QB_PORT__", os.environ.get("QB_PUBLIC_PORT", "8181"))
                    .replace("__CW_PORT__", os.environ.get("CW_PUBLIC_PORT", "8283"))
                    ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/status.json":
            return self._json(200, status())
        if self.path == "/healthz":
            return self._json(200, {"ok": True})
        self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/trigger/"):
            name = self.path.split("/", 2)[2]
            if name not in TRIGGERS:
                return self._json(404, {"error": "unknown trigger"})
            ok, msg = run_trigger(name)
            return self._json(200 if ok else 409, {"status": msg})
        self.send_error(404)

    def log_message(self, fmt, *args):
        # Silence default logging noise; only errors go to stderr
        pass


def main():
    port = int(os.environ.get("MGMT_PORT", "8484"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[mgmt] listening on :{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
