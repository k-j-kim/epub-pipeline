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
    "refresh":       ["/scripts/refresh.sh"],
    "fetch_ia":      ["python3", "/scripts/fetch_ia.py"],
    "fetch_libgen":  ["python3", "/scripts/fetch_libgen.py"],
    "fetch_aa":      ["python3", "/scripts/fetch_aa.py"],
    "ingest":        ["/scripts/ingest.sh"],
    "send":          ["/scripts/send_to_kindle.py"],
}

BYPASS_FLAG    = STATE / "needs_bypass"
HEADERS_FILE   = STATE / "aa_headers.txt"
CHALLENGE_BODY = STATE / "last_challenge.html"

# Headers we care about extracting from a pasted curl / raw block
IMPORTANT_HEADERS = {"cookie", "user-agent", "accept", "accept-language", "referer"}

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


def library_recent(limit=50):
    """Return the N most recently added books: title, author, added_at, sent."""
    if not (LIBRARY / "metadata.db").exists():
        return []
    try:
        con = sqlite3.connect(f"file:{LIBRARY}/metadata.db?mode=ro", uri=True)
        rows = con.execute("""
            SELECT b.id, b.title, b.timestamp, COALESCE(GROUP_CONCAT(a.name, ', '), '')
            FROM books b
            LEFT JOIN books_authors_link bal ON bal.book = b.id
            LEFT JOIN authors a ON a.id = bal.author
            GROUP BY b.id
            ORDER BY b.timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
    except Exception as e:
        return [{"error": str(e)}]

    # Cross-reference sent.db so the UI can flag already-emailed books
    sent_ids = set()
    sdb = STATE / "sent.db"
    if sdb.exists():
        try:
            scon = sqlite3.connect(f"file:{sdb}?mode=ro", uri=True)
            sent_ids = {r[0] for r in scon.execute("SELECT id FROM sent")}
        except Exception:
            pass
    return [{"id": r[0], "title": r[1], "added_at": r[2], "author": r[3], "sent": r[0] in sent_ids} for r in rows]


def source_counts():
    out = {}
    for name in ("ia", "libgen", "aa"):
        p = STATE / f"{name}.sqlite"
        out[name] = sqlite_count(p, "SELECT COUNT(*) FROM fetched WHERE size > 0") if p.exists() else 0
    return out


def status():
    sent   = STATE / "sent.db"
    bypass_url = None
    if BYPASS_FLAG.exists():
        try:
            bypass_url = BYPASS_FLAG.read_text().strip()
        except Exception:
            bypass_url = "(unreadable)"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "library_books":     library_count(),
            "sent_books":        sqlite_count(sent, "SELECT COUNT(*) FROM sent") if sent.exists() else 0,
        },
        "sources": source_counts(),
        "last_logs": {
            name: tail_log(STATE / f"{name}.log") for name in ("refresh", "ingest", "kindle")
        },
        "running": {name: (time.time() - t) for name, t in running.items()},
        "bypass": {
            "needed":       bypass_url is not None,
            "url":          bypass_url,
            "headers_saved": HEADERS_FILE.exists() and HEADERS_FILE.stat().st_size > 0,
            "challenge_body_len": CHALLENGE_BODY.stat().st_size if CHALLENGE_BODY.exists() else 0,
        },
        "env": {
            "BOOKS_PER_WEEK": os.environ.get("BOOKS_PER_WEEK"),
            "KINDLE_EMAIL":   os.environ.get("KINDLE_EMAIL", "").replace(os.environ.get("KINDLE_EMAIL","")[:3], "***", 1) if os.environ.get("KINDLE_EMAIL") else None,
            "TZ":             os.environ.get("TZ"),
        },
    }


def parse_headers(raw):
    """Extract useful HTTP headers from either:
       - a full 'curl -H ... -H ...' command copied from browser DevTools, or
       - a raw 'Name: value' block, one per line.
       Returns a list of 'Name: value' strings.
    """
    out = {}
    text = raw.strip()

    # Format 1: curl command. Find every -H '...' or -H "...".
    import re, shlex
    if text.lstrip().startswith("curl"):
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()
        it = iter(tokens)
        for tok in it:
            if tok in ("-H", "--header"):
                try:
                    hdr = next(it)
                except StopIteration:
                    break
                if ":" in hdr:
                    k, v = hdr.split(":", 1)
                    if k.strip().lower() in IMPORTANT_HEADERS:
                        out[k.strip()] = v.strip()
            elif tok in ("-b", "--cookie"):
                try:
                    out["Cookie"] = next(it).strip()
                except StopIteration:
                    break
            elif tok in ("-A", "--user-agent"):
                try:
                    out["User-Agent"] = next(it).strip()
                except StopIteration:
                    break
    else:
        # Format 2: raw header lines
        for line in text.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip().lower() in IMPORTANT_HEADERS:
                    out[k.strip()] = v.strip()

    # Cookie is the important one; a Cookie-less bypass is almost certainly wrong
    return [f"{k}: {v}" for k, v in out.items()]


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

  <details id="bypass-panel" style="display:none;margin-bottom:1rem">
    <summary style="cursor:pointer;color:var(--muted);font-size:.8rem;padding:.4rem 0">
      anna's archive is blocked by a bot-gate — click to unlock (optional, IA+libgen still work)
    </summary>
    <div class="card" style="margin-top:.4rem">
      <p style="margin:0 0 .5rem;font-size:.85rem;color:var(--muted)">
        Blocked URL: <code id="bypass-url"></code>. Open it in a real browser,
        DevTools → Network → the request → <b>Copy as cURL</b>, then paste below.
        Cookie is the important header.
      </p>
      <textarea id="bypass-raw" rows="5" style="width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:.5rem" placeholder="curl 'https://annas-archive.pk/search?...' -H 'user-agent: ...' -H 'cookie: ...'"></textarea>
      <div class="row" style="margin-top:.5rem">
        <button class="btn" id="bypass-save">save headers &amp; retry</button>
        <button class="btn" id="bypass-clear">clear</button>
        <span class="flash" id="bypass-flash"></span>
      </div>
    </div>
  </details>

  <div class="section-title">status</div>
  <div class="grid" id="stats"></div>

  <div class="section-title">links</div>
  <div class="grid">
    <a class="btn" id="qb-link" target="_blank" rel="noopener">▤ qBittorrent</a>
    <a class="btn" id="cw-link" target="_blank" rel="noopener">📚 Calibre-web</a>
  </div>

  <div class="section-title">sources</div>
  <div class="grid" id="sources"></div>

  <div class="section-title">manual triggers</div>
  <div class="row">
    <button class="btn" data-t="refresh">↻ refresh all sources</button>
    <button class="btn" data-t="fetch_ia">🏛 fetch archive.org</button>
    <button class="btn" data-t="fetch_libgen">📖 fetch libgen</button>
    <button class="btn" data-t="fetch_aa">📚 fetch anna's archive</button>
    <button class="btn" data-t="ingest">＋ ingest into library</button>
    <button class="btn" data-t="send">✉ send weekly picks now</button>
  </div>
  <div class="flash" id="flash"></div>

  <div class="section-title">
    library <span style="color:var(--muted);text-transform:none;letter-spacing:0;font-size:.75rem">
      — <a id="show-all" href="#" style="color:var(--muted)">show all</a>
    </span>
  </div>
  <div class="card" style="padding:0">
    <table id="books" style="width:100%;border-collapse:collapse;font-size:.85rem">
      <thead>
        <tr style="text-align:left;color:var(--muted);font-size:.7rem;text-transform:uppercase;letter-spacing:.08em">
          <th style="padding:.5rem .75rem">added</th>
          <th style="padding:.5rem .75rem">title</th>
          <th style="padding:.5rem .75rem">author</th>
          <th style="padding:.5rem .75rem;width:2ch">✉</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>

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

  // bypass panel
  const bp = document.getElementById("bypass-panel");
  if (s.bypass && s.bypass.needed) {
    bp.style.display = "";
    document.getElementById("bypass-url").textContent = s.bypass.url || "";
  } else {
    bp.style.display = "none";
  }

  const stats = document.getElementById("stats");
  stats.innerHTML = "";
  const cards = [
    ["books in library",             s.counts.library_books ?? 0],
    ["books already sent",           s.counts.sent_books ?? 0],
  ];
  for (const [l, n] of cards) {
    const c = document.createElement("div");
    c.className = "card stat";
    c.innerHTML = `<div class="n">${n}</div><div class="l">${l}</div>`;
    stats.appendChild(c);
  }

  // per-source cards
  const sources = document.getElementById("sources");
  sources.innerHTML = "";
  const labels = { ia: "archive.org", libgen: "libgen", aa: "anna's archive" };
  for (const [k, label] of Object.entries(labels)) {
    const c = document.createElement("div");
    c.className = "card stat";
    c.innerHTML = `<div class="n">${s.sources?.[k] ?? 0}</div><div class="l">${label}</div>`;
    sources.appendChild(c);
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

document.getElementById("bypass-save").addEventListener("click", async () => {
  const raw = document.getElementById("bypass-raw").value;
  const flash = document.getElementById("bypass-flash");
  flash.textContent = "saving…";
  const r = await fetch("/bypass", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { flash.textContent = "✗ " + (j.error || r.status); return; }
  flash.textContent = `✓ saved ${j.saved} headers (${j.headers_preview.join(", ")})${j.has_cookie ? "" : " — no Cookie found, that's usually wrong"}`;
  // Kick off a refresh with the new headers
  await fetch("/trigger/refresh", { method: "POST" });
  setTimeout(refresh, 500);
});

document.getElementById("bypass-clear").addEventListener("click", async () => {
  await fetch("/bypass/clear", { method: "POST" });
  document.getElementById("bypass-flash").textContent = "cleared";
  setTimeout(refresh, 300);
});

const tickClock = () => {
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};
tickClock(); setInterval(tickClock, 30000);

let bookLimit = 15;
async function refreshBooks() {
  try {
    const r = await fetch(`/library.json?limit=${bookLimit}`, { cache: "no-store" });
    const j = await r.json();
    const tbody = document.querySelector("#books tbody");
    tbody.innerHTML = "";
    if (!j.books || j.books.length === 0) {
      tbody.innerHTML = `<tr><td colspan="4" style="padding:1rem;color:var(--muted);text-align:center">no books yet — trigger a fetch above</td></tr>`;
      return;
    }
    for (const b of j.books) {
      const tr = document.createElement("tr");
      tr.style.borderTop = "1px solid var(--border)";
      const when = (b.added_at || "").slice(0, 10);
      tr.innerHTML =
        `<td style="padding:.45rem .75rem;color:var(--muted);font-variant-numeric:tabular-nums">${when}</td>` +
        `<td style="padding:.45rem .75rem">${escapeHtml(b.title || "(untitled)")}</td>` +
        `<td style="padding:.45rem .75rem;color:var(--muted)">${escapeHtml(b.author || "")}</td>` +
        `<td style="padding:.45rem .75rem;text-align:center;color:var(--ok)">${b.sent ? "✓" : ""}</td>`;
      tbody.appendChild(tr);
    }
  } catch (e) { /* ignore transient errors */ }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
document.getElementById("show-all").addEventListener("click", (e) => {
  e.preventDefault();
  bookLimit = bookLimit === 15 ? 500 : 15;
  e.target.textContent = bookLimit === 15 ? "show all" : "show fewer";
  refreshBooks();
});

refresh(); setInterval(refresh, 5000);
refreshBooks(); setInterval(refreshBooks, 15000);
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
        if self.path.startswith("/library.json"):
            # Optional ?limit=N (default 50, max 500)
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            try: n = min(int(q.get("limit", ["50"])[0]), 500)
            except ValueError: n = 50
            return self._json(200, {"books": library_recent(n)})
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

        if self.path == "/bypass":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw.lstrip().startswith("{") else {"raw": raw}
            except Exception:
                payload = {"raw": raw}
            headers = parse_headers(payload.get("raw", ""))
            if not headers:
                return self._json(400, {"error": "no usable headers found; paste a full curl or 'Name: value' lines"})
            has_cookie = any(h.lower().startswith("cookie:") for h in headers)
            HEADERS_FILE.write_text("\n".join(headers) + "\n")
            # Clear the flag; next refresh will retry with the saved headers
            try: BYPASS_FLAG.unlink()
            except FileNotFoundError: pass
            return self._json(200, {
                "saved": len(headers),
                "has_cookie": has_cookie,
                "headers_preview": [h.split(":",1)[0] for h in headers],
            })

        if self.path == "/bypass/clear":
            for p in (HEADERS_FILE, BYPASS_FLAG, CHALLENGE_BODY):
                try: p.unlink()
                except FileNotFoundError: pass
            return self._json(200, {"cleared": True})

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
