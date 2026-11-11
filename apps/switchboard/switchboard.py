#!/usr/bin/env python3
"""switchboard — one page to start and stop the vLLM services of grace-stack.

Discovers the models from `docker compose config` (every service whose command is
`vllm serve ...`), reads each one's --gpu-memory-utilization, and refuses to start a
set whose fractions sum above MAX_GMU: on GB10 the GPU and the host share one pool, so
over-committing means swap, not an error. Presets (presets.json next to this file,
re-read on every request) switch whole sets: stop what is not wanted, then start.

Runs `docker compose` against the repo at STACK_DIR, which must be mounted at the same
path as on the host so compose computes the same config hashes as your shell does.
No auth: it holds the Docker socket, keep it on loopback + tailscale serve.
"""
import collections
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STACK_DIR = os.environ.get("STACK_DIR", "/stack")
MAX_GMU = float(os.environ.get("MAX_GMU", "0.85"))
PORT = int(os.environ.get("PORT", "8080"))
PROJECT = os.environ.get("COMPOSE_PROJECT", "grace-stack")
TAILNET_HOST = os.environ.get("TAILNET_HOST", "")
PRESETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets.json")
if not os.path.exists(PRESETS):                       # in the image the script lives in /app
    PRESETS = os.path.join(STACK_DIR, "apps", "switchboard", "presets.json")

# ----------------------------------------------------------------- docker

def run(args, timeout=900):
    p = subprocess.run(args, cwd=STACK_DIR, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def flag(cmd, name):
    """Value following `name` in a command list, else None."""
    for i, tok in enumerate(cmd):
        if tok == name and i + 1 < len(cmd):
            return cmd[i + 1]
        if tok.startswith(name + "="):
            return tok.split("=", 1)[1]
    return None


_models_cache = (0.0, None)
_models_lock = threading.Lock()


def models(refresh=False):
    """[{service, container, profile, model, served, port, gmu}] from the compose config."""
    global _models_cache
    with _models_lock:
        ts, cached = _models_cache
        if cached is not None and not refresh and time.time() - ts < 60:
            return cached
        rc, out = run(["docker", "compose", "--profile", "*", "config", "--format", "json"], timeout=60)
        if rc != 0:
            raise RuntimeError("docker compose config failed:\n" + out)
        found = []
        for name, svc in json.loads(out).get("services", {}).items():
            cmd = svc.get("command") or []
            if isinstance(cmd, str):
                cmd = cmd.split()
            if cmd[:2] != ["vllm", "serve"]:
                continue
            ports = svc.get("ports") or [{}]
            found.append({
                "service": name,
                "container": svc.get("container_name", name),
                "profile": (svc.get("profiles") or [None])[0],
                "model": cmd[2] if len(cmd) > 2 else "?",
                "served": flag(cmd, "--served-model-name") or "?",
                "port": ports[0].get("published"),
                "gmu": float(flag(cmd, "--gpu-memory-utilization") or 0.9),
                "ctx": flag(cmd, "--max-model-len"),
            })
        found.sort(key=lambda m: str(m["port"]))
        _models_cache = (time.time(), found)
        return found


HEALTH = re.compile(r"\((healthy|unhealthy|health: starting)\)")


def container_states():
    """{container_name: (state, status_text, health)} for this compose project."""
    rc, out = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}",
                   "--format", "{{json .}}"], timeout=30)
    states = {}
    for line in out.splitlines():
        if not line.startswith("{"):
            continue
        c = json.loads(line)
        m = HEALTH.search(c.get("Status", ""))
        health = {"healthy": "healthy", "unhealthy": "unhealthy", "health: starting": "starting"}.get(m.group(1)) if m else None
        for n in c["Names"].split(","):
            states[n] = (c["State"], c["Status"], health)
    return states


def presets():
    try:
        with open(PRESETS) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        return [{"name": "presets.json unreadable", "note": str(e), "services": []}]


def snapshot():
    ms = models()
    st = container_states()
    known = {m["service"] for m in ms}
    for m in ms:
        state, status, health = st.get(m["container"], ("absent", "never created", None))
        if state == "running":
            m["status"] = "up" if health == "healthy" else ("unhealthy" if health == "unhealthy" else "loading")
        else:
            m["status"] = "down"
        m["detail"] = status
        m["url"] = f"https://{TAILNET_HOST}:{m['port']}/v1" if TAILNET_HOST and m["port"] else f"http://127.0.0.1:{m['port']}/v1"
    running = {m["service"] for m in ms if m["status"] != "down"}
    gmu = {m["service"]: m["gmu"] for m in ms}
    used = sum(gmu[s] for s in running)
    ps = []
    for p in presets():
        unknown = [s for s in p.get("services", []) if s not in known]
        total = sum(gmu.get(s, 0) for s in p.get("services", []))
        ps.append({**p, "gmu": round(total, 2), "fits": total <= MAX_GMU + 1e-9 and not unknown,
                   "unknown": unknown, "active": set(p.get("services", [])) == running})
    return {"models": ms, "presets": ps, "gmu_used": round(used, 2), "max_gmu": MAX_GMU,
            "job": JOBS.current(), "history": JOBS.history(), "host": TAILNET_HOST, "stack_dir": STACK_DIR}

# ----------------------------------------------------------------- jobs (one at a time)


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self._current = None
        self._history = collections.deque(maxlen=12)

    def current(self):
        return self._current

    def history(self):
        return list(self._history)

    def submit(self, title, fn):
        with self.lock:
            if self._current:
                return False, f"busy: {self._current['title']}"
            self._current = {"title": title, "started": time.time(), "output": "", "ok": None}
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()
        return True, "started"

    def _run(self, fn):
        job = self._current
        try:
            job["ok"], job["output"] = fn(job)
        except Exception as e:  # noqa: BLE001
            job["ok"], job["output"] = False, f"{type(e).__name__}: {e}"
        job["finished"] = time.time()
        job["seconds"] = round(job["finished"] - job["started"], 1)
        with self.lock:
            self._history.appendleft(job)
            self._current = None
        models(refresh=True)


JOBS = Jobs()


def by_service():
    return {m["service"]: m for m in models()}


def compose_args(ms):
    args = ["docker", "compose"]
    for m in ms:
        if m["profile"]:
            args += ["--profile", m["profile"]]
    return args


def check_fit(start, keep_running):
    """Error text if starting `start` on top of `keep_running` overcommits, else None."""
    gmu = {m["service"]: m["gmu"] for m in models()}
    total = sum(gmu[s] for s in set(start) | set(keep_running))
    if total > MAX_GMU + 1e-9:
        return (f"refused: GMU would be {total:.2f} > {MAX_GMU} "
                f"(running: {', '.join(sorted(keep_running)) or 'nothing'}; wanted: {', '.join(start)}). "
                "Stop something first or pick a preset.")
    return None


def do_start(services):
    def fn(job):
        ms = by_service()
        running = {m["service"] for m in snapshot()["models"] if m["status"] != "down"}
        err = check_fit(services, running - set(services))
        if err:
            return False, err
        rc, out = run(compose_args([ms[s] for s in services]) + ["up", "-d", "--no-build", "--no-deps"] + services)
        return rc == 0, out or "ok"
    return fn


def do_stop(services):
    def fn(job):
        ms = by_service()
        rc, out = run(compose_args([ms[s] for s in services]) + ["stop"] + services)
        return rc == 0, out or "ok"
    return fn


def do_restart(services):
    def fn(job):
        ms = by_service()
        rc, out = run(compose_args([ms[s] for s in services]) + ["restart"] + services)
        return rc == 0, out or "ok"
    return fn


def do_preset(preset):
    wanted = list(preset.get("services", []))

    def fn(job):
        ms = by_service()
        unknown = [s for s in wanted if s not in ms]
        if unknown:
            return False, f"preset names unknown services: {unknown}"
        err = check_fit(wanted, [])
        if err:
            return False, err
        running = {m["service"] for m in snapshot()["models"] if m["status"] != "down"}
        log = []
        to_stop = sorted(running - set(wanted))
        if to_stop:
            rc, out = run(compose_args([ms[s] for s in to_stop]) + ["stop"] + to_stop)
            log.append(f"$ stop {' '.join(to_stop)}\n{out}")
            if rc != 0:
                return False, "\n".join(log)
        to_start = [s for s in wanted if s not in running]
        if to_start:
            rc, out = run(compose_args([ms[s] for s in to_start]) + ["up", "-d", "--no-build", "--no-deps"] + to_start)
            log.append(f"$ up -d {' '.join(to_start)}\n{out}")
            if rc != 0:
                return False, "\n".join(log)
        return True, "\n".join(log) or "already there"
    return fn

# ----------------------------------------------------------------- http

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>grace switchboard</title>
<style>
  :root { --bg:#0f1115; --card:#181b22; --line:#2a2f3a; --fg:#e6e8ee; --dim:#8b93a7;
          --up:#3fb950; --load:#d29922; --down:#6e7681; --bad:#f85149; --accent:#58a6ff; }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif }
  main { max-width:1100px; margin:0 auto; padding:20px 16px 60px }
  h1 { font-size:20px; margin:0 0 4px } h2 { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); margin:28px 0 10px }
  .sub { color:var(--dim); font-size:13px }
  .bar { height:10px; background:var(--line); border-radius:5px; overflow:hidden; margin:8px 0 4px; position:relative }
  .bar > i { display:block; height:100%; background:var(--up) } .bar.warn > i { background:var(--load) } .bar.over > i { background:var(--bad) }
  .bar > b { position:absolute; top:-3px; width:2px; height:16px; background:var(--fg); opacity:.6 }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px }
  .card .name { font-weight:600; font-size:16px; display:flex; align-items:center; gap:8px }
  .dot { width:10px; height:10px; border-radius:50%; background:var(--down); flex:none }
  .up .dot { background:var(--up) } .loading .dot { background:var(--load); animation:pulse 1s infinite alternate } .unhealthy .dot { background:var(--bad) }
  @keyframes pulse { from { opacity:.4 } to { opacity:1 } }
  .meta { color:var(--dim); font-size:12.5px; margin:6px 0 10px; word-break:break-all }
  .meta code { color:var(--fg); font-size:12px }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center }
  button { background:#242a35; color:var(--fg); border:1px solid var(--line); border-radius:7px; padding:6px 12px; font:inherit; font-size:13.5px; cursor:pointer }
  button:hover { border-color:var(--accent) } button:disabled { opacity:.4; cursor:default }
  button.primary { background:#1f4a7a; border-color:#2b6cb0 } button.danger { background:#4a1f23; border-color:#8b2d35 }
  .presets { display:flex; gap:10px; flex-wrap:wrap }
  .preset { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width:200px; flex:1 }
  .preset.active { border-color:var(--up) } .preset .note { color:var(--dim); font-size:12.5px; margin:2px 0 8px }
  pre { background:#0a0c10; border:1px solid var(--line); border-radius:8px; padding:10px 12px; font-size:12.5px; white-space:pre-wrap; max-height:320px; overflow:auto; margin:0 }
  .job { display:flex; justify-content:space-between; gap:12px; color:var(--dim); font-size:13px; margin:6px 0 }
  .ok { color:var(--up) } .fail { color:var(--bad) } .busy { color:var(--load) }
  #toast { position:fixed; bottom:16px; left:50%; transform:translateX(-50%); background:#242a35; border:1px solid var(--line); padding:8px 14px; border-radius:8px; display:none }
</style></head><body><main>
<h1>grace switchboard</h1>
<div class="sub" id="sub">loading…</div>
<div class="bar" id="bar"><i style="width:0"></i><b></b></div>
<div class="sub" id="gmu"></div>

<h2>Presets</h2><div class="presets" id="presets"></div>
<h2>Models</h2><div class="grid" id="models"></div>
<h2>Activity</h2><div id="jobs"></div>
<h2 id="logtitle" style="display:none">Logs</h2><pre id="logs" style="display:none"></pre>
<div id="toast"></div>
<script>
const $ = (id) => document.getElementById(id);
let state = null, logService = null;
function esc(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function toast(msg){ const t=$('toast'); t.textContent=msg; t.style.display='block'; setTimeout(()=>t.style.display='none', 3000); }
async function api(path, body){
  const r = await fetch(path, body ? {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(body)} : {});
  const j = await r.json(); if (!r.ok) toast(j.error || r.statusText); return j;
}
function act(path, body, confirmMsg){ if (confirmMsg && !confirm(confirmMsg)) return; api(path, body).then(j => { if (j.error) toast(j.error); else toast(j.message || 'ok'); refresh(); }); }
function render(){
  const s = state, busy = !!s.job;
  $('sub').textContent = `${s.models.filter(m=>m.status==='up').length} of ${s.models.length} models up · ${s.host || 'localhost'}`;
  const pct = Math.min(100, 100*s.gmu_used), bar=$('bar');
  bar.className = 'bar' + (s.gmu_used > s.max_gmu ? ' over' : s.gmu_used > s.max_gmu-0.15 ? ' warn' : '');
  bar.firstElementChild.style.width = pct+'%'; bar.lastElementChild.style.left = (100*s.max_gmu)+'%';
  $('gmu').textContent = `GPU memory fractions in use: ${s.gmu_used.toFixed(2)} of ${s.max_gmu} allowed (unified pool: the rest is the host's)`;
  $('presets').innerHTML = s.presets.map(p => `
    <div class="preset ${p.active?'active':''}">
      <div class="row" style="justify-content:space-between"><b>${esc(p.name)}</b><span class="sub">GMU ${p.gmu.toFixed(2)}</span></div>
      <div class="note">${esc(p.note||'')} ${p.unknown.length?'· unknown: '+esc(p.unknown.join(', ')):''}</div>
      <button class="primary" ${(busy||p.active||!p.fits)?'disabled':''} onclick="act('/api/preset',{name:${JSON.stringify(p.name)}}, 'Switch to preset ${esc(p.name)}? Running models not in it will be stopped.')">${p.active?'active':(p.fits?'switch':'does not fit')}</button>
    </div>`).join('');
  $('models').innerHTML = s.models.map(m => {
    const fits = (s.gmu_used + m.gmu) <= s.max_gmu + 1e-9;
    return `<div class="card ${m.status}">
      <div class="name"><span class="dot"></span>${esc(m.service)} <span class="sub" style="font-weight:400">· ${esc(m.status)}</span></div>
      <div class="meta">${esc(m.model)}<br>served as <code>${esc(m.served)}</code> · GMU ${m.gmu.toFixed(2)} · ctx ${esc(m.ctx||'?')}<br><code>${esc(m.url)}</code><br>${esc(m.detail)}</div>
      <div class="row">
        ${m.status==='down'
          ? `<button class="primary" ${(busy||!fits)?'disabled':''} title="${fits?'':'would exceed MAX_GMU'}" onclick="act('/api/start',{service:'${m.service}'})">${fits?'start':'start (no room)'}</button>`
          : `<button class="danger" ${busy?'disabled':''} onclick="act('/api/stop',{service:'${m.service}'}, 'Stop ${m.service}? Reload takes ~4 min.')">stop</button>
             <button ${busy?'disabled':''} onclick="act('/api/restart',{service:'${m.service}'}, 'Restart ${m.service}?')">restart</button>`}
        <button onclick="showLogs('${m.service}')">logs</button>
      </div></div>`;}).join('');
  const jobs = [];
  if (s.job) jobs.push(`<div class="job"><span class="busy">⟳ ${esc(s.job.title)}</span><span>${Math.round(Date.now()/1000 - s.job.started)} s</span></div>`);
  for (const j of s.history) jobs.push(`<div class="job"><span class="${j.ok?'ok':'fail'}">${j.ok?'✓':'✗'} ${esc(j.title)}</span><span>${new Date(j.finished*1000).toLocaleTimeString()} · ${j.seconds} s</span></div>` + (j.ok && j.output==='ok' ? '' : `<pre>${esc(j.output)}</pre>`));
  $('jobs').innerHTML = jobs.join('') || '<div class="sub">nothing yet</div>';
}
async function refresh(){ try { state = await api('/api/state'); render(); if (logService) loadLogs(); } catch(e) { $('sub').textContent = 'switchboard unreachable: '+e; } }
function showLogs(svc){ logService = svc; $('logtitle').style.display=''; $('logs').style.display=''; $('logtitle').textContent = 'Logs · '+svc; loadLogs(); $('logs').scrollIntoView({behavior:'smooth'}); }
async function loadLogs(){ const j = await api('/api/logs?service='+encodeURIComponent(logService)); $('logs').textContent = j.logs || j.error || ''; }
refresh(); setInterval(refresh, 4000);
</script></main></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "switchboard/1"

    def log_message(self, fmt, *args):  # quieter: only non-GET or errors
        if self.command != "GET" or (args and str(args[1])[:1] not in "23"):
            super().log_message(fmt, *args)

    def send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self.send(200, PAGE, "text/html")
            if u.path == "/api/state":
                return self.send(200, snapshot())
            if u.path == "/api/logs":
                svc = parse_qs(u.query).get("service", [""])[0]
                m = by_service().get(svc)
                if not m:
                    return self.send(404, {"error": f"unknown service {svc!r}"})
                rc, out = run(["docker", "logs", "--tail", "80", m["container"]], timeout=30)
                return self.send(200, {"logs": out})
            if u.path == "/health":
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad json"})
        u = urlparse(self.path)
        try:
            if u.path in ("/api/start", "/api/stop", "/api/restart"):
                svc = body.get("service")
                if svc not in by_service():
                    return self.send(404, {"error": f"unknown service {svc!r}"})
                verb = u.path.rsplit("/", 1)[1]
                if verb == "start":   # answer the obvious refusal now; the job re-checks before acting
                    running = {m["service"] for m in snapshot()["models"] if m["status"] != "down"}
                    err = check_fit([svc], running - {svc})
                    if err:
                        return self.send(409, {"error": err})
                fn = {"start": do_start, "stop": do_stop, "restart": do_restart}[verb]([svc])
                ok, msg = JOBS.submit(f"{verb} {svc}", fn)
            elif u.path == "/api/preset":
                p = next((p for p in presets() if p.get("name") == body.get("name")), None)
                if not p:
                    return self.send(404, {"error": f"unknown preset {body.get('name')!r}"})
                err = check_fit(p.get("services", []), [])
                if err:
                    return self.send(409, {"error": err})
                ok, msg = JOBS.submit(f"preset {p['name']}", do_preset(p))
            else:
                return self.send(404, {"error": "not found"})
            return self.send(202 if ok else 409, {"message": msg} if ok else {"error": msg})
        except Exception as e:  # noqa: BLE001
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    if not os.path.exists(os.path.join(STACK_DIR, "docker-compose.yml")):
        raise SystemExit(f"STACK_DIR={STACK_DIR} has no docker-compose.yml; mount the repo there")
    print(f"switchboard: stack {STACK_DIR}, project {PROJECT}, max GMU {MAX_GMU}, port {PORT}")
    for m in models():
        print(f"  {m['service']:<16} gmu {m['gmu']:<5} port {m['port']}  {m['model']}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
