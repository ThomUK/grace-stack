#!/usr/bin/env python3
"""Generate the four Grafana dashboards as JSON.

    python3 build_dashboards.py          # writes dashboards/*.json

Edit this file, not the JSON. Grafana loads the JSON at start (and re-reads
every 10s), so after regenerating: docker compose restart grafana.

The JSON is build output and gitignored, because links in it use TAILNET_HOST
(from the environment, else the repo's .env). Unset, links point at localhost.
"""
import json
import os
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
DS = {"type": "prometheus", "uid": "prometheus"}


def setting(key):
    """Return key from the environment, else from .env, else an empty string."""
    if os.environ.get(key):
        return os.environ[key]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == key:
                return value.strip().strip("'\"")
    return ""


TAILNET_HOST = setting("TAILNET_HOST")
DGX_URL = f"https://{TAILNET_HOST}:8080" if TAILNET_HOST else "http://localhost:8080"
WEBUI_URL = f"https://{TAILNET_HOST}:12000" if TAILNET_HOST else "http://localhost:12000"

# ---------------------------------------------------------------- helpers

_id = 0


def nid():
    global _id
    _id += 1
    return _id


def target(expr, legend="", instant=False):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(65 + nid() % 26)}
    if instant:
        t["instant"] = True
        t["range"] = False
    return t


def grid(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def stat(title, expr, pos, unit="short", decimals=None, thresholds=None, link=None, legend=""):
    p = {
        "id": nid(), "type": "stat", "title": title, "datasource": DS, "gridPos": grid(*pos),
        "targets": [target(expr, legend, instant=False)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "area", "textMode": "auto", "orientation": "auto"},
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "thresholds"},
                                     "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "blue", "value": None}]}},
                        "overrides": []},
    }
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    if link:
        p["links"] = [{"title": link[0], "url": link[1], "targetBlank": False}]
    return p


def ts(title, targets, pos, unit="short", min_=None, max_=None, stack=False, fill=10, desc=""):
    p = {
        "id": nid(), "type": "timeseries", "title": title, "datasource": DS, "gridPos": grid(*pos),
        "description": desc,
        "targets": [target(e, l) for e, l in targets],
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"},
                                     "custom": {"lineWidth": 1, "fillOpacity": fill, "showPoints": "never",
                                                "stacking": {"mode": "normal" if stack else "none"}}},
                        "overrides": []},
    }
    if min_ is not None:
        p["fieldConfig"]["defaults"]["min"] = min_
    if max_ is not None:
        p["fieldConfig"]["defaults"]["max"] = max_
    return p


def gauge(title, expr, pos, unit="percent", max_=100, thresholds=None):
    return {
        "id": nid(), "type": "gauge", "title": title, "datasource": DS, "gridPos": grid(*pos),
        "targets": [target(expr)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "showThresholdLabels": False, "showThresholdMarkers": True},
        "fieldConfig": {"defaults": {"unit": unit, "min": 0, "max": max_,
                                     "thresholds": {"mode": "absolute", "steps": thresholds or
                                                    [{"color": "green", "value": None}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}},
                        "overrides": []},
    }


def table(title, targets, pos, transforms=None, overrides=None):
    return {
        "id": nid(), "type": "table", "title": title, "datasource": DS, "gridPos": grid(*pos),
        "targets": [target(e, l, instant=True) | {"format": "table"} for e, l in targets],
        "transformations": transforms or [],
        "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "name"}]},
        "fieldConfig": {"defaults": {}, "overrides": overrides or []},
    }


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False, "gridPos": grid(0, y, 24, 1), "panels": []}


def text(content, pos):
    return {"id": nid(), "type": "text", "title": "", "gridPos": grid(*pos),
            "options": {"mode": "markdown", "content": content}}


def dashboard(uid, title, panels, tags=(), refresh="15s", time_from="now-1h", links=()):
    return {
        "uid": uid, "title": title, "tags": list(tags), "timezone": "browser", "editable": True,
        "graphTooltip": 1, "refresh": refresh, "schemaVersion": 39, "version": 1,
        "time": {"from": time_from, "to": "now"},
        "links": [{"title": t, "url": u, "type": "link", "icon": "dashboard", "keepTime": True} for t, u in links],
        "panels": panels,
    }


NAV = [("Overview", "/d/grace-index"), ("System", "/d/grace-system"),
       ("Containers", "/d/grace-containers"), ("Models", "/d/grace-models")]

# ---------------------------------------------------------------- expressions

GPU_TEMP = 'max(DCGM_FI_DEV_GPU_TEMP)'
GPU_POWER = 'max(DCGM_FI_DEV_POWER_USAGE)'
GPU_UTIL = 'max(DCGM_FI_DEV_GPU_UTIL)'
MEM_USED = 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes'
MEM_PCT = f'100 * ({MEM_USED}) / node_memory_MemTotal_bytes'
SYS_TEMP = 'max(node_hwmon_temp_celsius) or max(node_thermal_zone_temp)'
MODELS_UP = 'count(up{job="vllm"} == 1) or vector(0)'
CONTAINERS_UP = 'count(container_last_seen{name!=""})'
# vLLM: counters are per model_name; rate over 1m for "now", sum for aggregate.
GEN_TPS = 'sum(rate(vllm:generation_tokens_total[1m]))'
GEN_TPS_BY = 'sum by (model_name) (rate(vllm:generation_tokens_total[1m]))'
PROMPT_TPS_BY = 'sum by (model_name) (rate(vllm:prompt_tokens_total[1m]))'
REQ_1H = 'sum(increase(vllm:request_success_total[1h]))'
RUNNING_BY = 'sum by (model_name) (vllm:num_requests_running)'
WAITING_BY = 'sum by (model_name) (vllm:num_requests_waiting)'
KV_BY = 'max by (model_name) (vllm:kv_cache_usage_perc or vllm:gpu_cache_usage_perc) * 100'
PREFIX_HIT_BY = ('100 * sum by (model_name) (rate(vllm:prefix_cache_hits_total[5m])) '
                 '/ sum by (model_name) (rate(vllm:prefix_cache_queries_total[5m]))')
PREEMPT_BY = 'sum by (model_name) (increase(vllm:num_preemptions_total[5m]))'
TTFT_P50 = 'histogram_quantile(0.5, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))'
TTFT_P95 = 'histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))'
TPOT_P50 = 'histogram_quantile(0.5, sum by (le, model_name) (rate(vllm:time_per_output_token_seconds_bucket[5m])))'
E2E_P95 = 'histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:e2e_request_latency_seconds_bucket[5m])))'
SPEC_ACCEPT = ('100 * sum by (model_name) (rate(vllm:spec_decode_num_accepted_tokens_total[5m])) '
               '/ sum by (model_name) (rate(vllm:spec_decode_num_draft_tokens_total[5m]))')
SPEC_PER_STEP = ('sum by (model_name) (rate(vllm:spec_decode_num_accepted_tokens_total[5m])) '
                 '/ sum by (model_name) (rate(vllm:spec_decode_num_drafts_total[5m]))')

TEMP_T = [{"color": "green", "value": None}, {"color": "orange", "value": 70}, {"color": "red", "value": 85}]
PCT_T = [{"color": "green", "value": None}, {"color": "orange", "value": 75}, {"color": "red", "value": 90}]

# ---------------------------------------------------------------- 1. index

index = dashboard("grace-index", "grace — overview", [
    stat("GPU temp", GPU_TEMP, (0, 0, 4, 4), "celsius", 0, TEMP_T, ("System", "/d/grace-system")),
    stat("GPU power", GPU_POWER, (4, 0, 4, 4), "watt", 0, None, ("System", "/d/grace-system")),
    stat("GPU util", GPU_UTIL, (8, 0, 4, 4), "percent", 0, PCT_T, ("System", "/d/grace-system")),
    stat("Memory used", MEM_PCT, (12, 0, 4, 4), "percent", 0, PCT_T, ("System", "/d/grace-system")),
    stat("Models up", MODELS_UP, (16, 0, 4, 4), "short", 0,
         [{"color": "red", "value": None}, {"color": "green", "value": 1}], ("Models", "/d/grace-models")),
    stat("Containers", CONTAINERS_UP, (20, 0, 4, 4), "short", 0, None, ("Containers", "/d/grace-containers")),

    stat("Generation tok/s (now)", GEN_TPS, (0, 4, 6, 4), "short", 1, None, ("Models", "/d/grace-models")),
    stat("Requests, last hour", REQ_1H, (6, 4, 6, 4), "short", 0, None, ("Models", "/d/grace-models")),
    stat("Requests running", 'sum(vllm:num_requests_running) or vector(0)', (12, 4, 6, 4), "short", 0, None, ("Models", "/d/grace-models")),
    stat("Requests waiting", 'sum(vllm:num_requests_waiting) or vector(0)', (18, 4, 6, 4), "short", 0,
         [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 4}], ("Models", "/d/grace-models")),

    ts("Generation tokens/s by model", [(GEN_TPS_BY, "{{model_name}}")], (0, 8, 12, 8)),
    ts("GPU temp & power", [(GPU_TEMP, "temp °C"), (GPU_POWER, "power W")], (12, 8, 12, 8), fill=0),

    text("Click any tile to drill down. **[System](/d/grace-system)** · **[Containers](/d/grace-containers)** · **[Models](/d/grace-models)** · "
         f"[DGX dashboard (start/stop)]({DGX_URL}) · [Open WebUI]({WEBUI_URL})", (0, 16, 24, 2)),
], tags=["grace"], links=NAV[1:])

# ---------------------------------------------------------------- 2. system

system = dashboard("grace-system", "grace — system", [
    row("GPU", 0),
    gauge("GPU temp", GPU_TEMP, (0, 1, 4, 6), "celsius", 100, TEMP_T),
    gauge("GPU util", GPU_UTIL, (4, 1, 4, 6)),
    gauge("Power", GPU_POWER, (8, 1, 4, 6), "watt", 140,
          [{"color": "green", "value": None}, {"color": "orange", "value": 100}, {"color": "red", "value": 130}]),
    ts("Temperatures", [(GPU_TEMP, "GPU"), (SYS_TEMP, "system")], (12, 1, 12, 6), "celsius", 0, 100, fill=0),
    ts("GPU utilisation", [(GPU_UTIL, "SM util"), ('max(DCGM_FI_DEV_MEM_COPY_UTIL)', "mem copy util")], (0, 7, 12, 7), "percent", 0, 100),
    ts("Power & clocks", [(GPU_POWER, "power W"), ('max(DCGM_FI_DEV_SM_CLOCK)', "SM MHz")], (12, 7, 12, 7), fill=0),

    row("Memory (unified pool — the one number that matters on GB10)", 14),
    gauge("Used", MEM_PCT, (0, 15, 6, 6), "percent", 100, PCT_T),
    ts("Memory", [(MEM_USED, "used"), ('node_memory_MemAvailable_bytes', "available"),
                  ('node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes', "swap used")], (6, 15, 18, 6), "bytes", 0),

    row("Host", 21),
    ts("CPU", [('100 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100', "busy %")], (0, 22, 8, 6), "percent", 0, 100),
    ts("Disk /", [('node_filesystem_avail_bytes{mountpoint="/host",fstype!="tmpfs"} or node_filesystem_avail_bytes{mountpoint="/",fstype!="tmpfs"}', "free")], (8, 22, 8, 6), "bytes", 0),
    ts("Network", [('rate(node_network_receive_bytes_total{device=~"en.*|eth.*"}[1m])', "rx {{device}}"),
                   ('rate(node_network_transmit_bytes_total{device=~"en.*|eth.*"}[1m])', "tx {{device}}")], (16, 22, 8, 6), "Bps", fill=0),
    stat("Uptime", 'time() - node_boot_time_seconds', (0, 28, 6, 3), "s", 0),
    stat("Load (1m)", 'node_load1', (6, 28, 6, 3), "short", 2),
], tags=["grace"], links=NAV)

# ---------------------------------------------------------------- 3. containers

containers = dashboard("grace-containers", "grace — containers", [
    table("Containers", [
        ('container_last_seen{name!=""}', ""),
        ('container_memory_working_set_bytes{name!=""}', ""),
        ('sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[1m])) * 100', ""),
        ('time() - container_start_time_seconds{name!=""}', ""),
    ], (0, 0, 24, 9), transforms=[
        {"id": "joinByField", "options": {"byField": "name", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "Time 3": True, "Time 4": True,
                              "__name__": True, "id": True, "image 1": True, "image 2": True, "image 3": True, "image 4": True,
                              "instance 1": True, "instance 2": True, "instance 3": True, "instance 4": True,
                              "job 1": True, "job 2": True, "job 3": True, "job 4": True,
                              "service": True, "Value #A": True},
            "renameByName": {"Value #B": "memory", "Value #C": "cpu %", "Value #D": "uptime", "image": "image"}}},
    ], overrides=[
        {"matcher": {"id": "byName", "options": "memory"}, "properties": [{"id": "unit", "value": "bytes"}]},
        {"matcher": {"id": "byName", "options": "cpu %"}, "properties": [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 1}]},
        {"matcher": {"id": "byName", "options": "uptime"}, "properties": [{"id": "unit", "value": "s"}]},
    ]),
    ts("Memory by container", [('container_memory_working_set_bytes{name!=""}', "{{name}}")], (0, 9, 12, 8), "bytes", 0, stack=True),
    ts("CPU by container", [('sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[1m])) * 100', "{{name}}")], (12, 9, 12, 8), "percent", 0),
    ts("Restarts (start time changes)", [('changes(container_start_time_seconds{name!=""}[1h])', "{{name}}")], (0, 17, 12, 6), "short", 0, fill=0,
       desc="A step up means the container restarted in the last hour."),
    ts("Network by container", [('sum by (name) (rate(container_network_receive_bytes_total{name!=""}[1m]))', "rx {{name}}"),
                                ('sum by (name) (rate(container_network_transmit_bytes_total{name!=""}[1m]))', "tx {{name}}")], (12, 17, 12, 6), "Bps", fill=0),
    text(f"Start / stop / restart lives on the [DGX dashboard]({DGX_URL}). "
         "A stopped vLLM container takes ~4 min to come back; watch it on the Models page.", (0, 23, 24, 2)),
], tags=["grace"], links=NAV)

# ---------------------------------------------------------------- 4. models

models = dashboard("grace-models", "grace — models", [
    row("Now", 0),
    stat("Endpoints up", MODELS_UP, (0, 1, 4, 4), "short", 0, [{"color": "red", "value": None}, {"color": "green", "value": 1}]),
    stat("Running", 'sum(vllm:num_requests_running) or vector(0)', (4, 1, 4, 4), "short", 0),
    stat("Waiting", 'sum(vllm:num_requests_waiting) or vector(0)', (8, 1, 4, 4), "short", 0,
         [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 4}]),
    stat("Gen tok/s", GEN_TPS, (12, 1, 4, 4), "short", 1),
    stat("Prompt tok/s", 'sum(rate(vllm:prompt_tokens_total[1m]))', (16, 1, 4, 4), "short", 0),
    stat("Preemptions (5m)", 'sum(increase(vllm:num_preemptions_total[5m])) or vector(0)', (20, 1, 4, 4), "short", 0,
         [{"color": "green", "value": None}, {"color": "red", "value": 1}]),

    row("Throughput", 5),
    ts("Generation tokens/s", [(GEN_TPS_BY, "{{model_name}}")], (0, 6, 12, 7), "short", 0,
       desc="Decode speed. Single-stream ceiling on GB10 is bandwidth-bound; speculative decoding is the lever."),
    ts("Prompt tokens/s (prefill)", [(PROMPT_TPS_BY, "{{model_name}}")], (12, 6, 12, 7), "short", 0,
       desc="Spikes = an agent re-sending its whole context. Prefix cache hits should keep these short."),

    row("Latency", 13),
    ts("Time to first token", [(TTFT_P50, "p50 {{model_name}}"), (TTFT_P95, "p95 {{model_name}}")], (0, 14, 8, 7), "s", 0, fill=0,
       desc="Prefill time as the user feels it. A fresh 40k context on the 27B is 20–40 s; cached turns should be ~1 s."),
    ts("Time per output token", [(TPOT_P50, "p50 {{model_name}}")], (8, 14, 8, 7), "s", 0, fill=0,
       desc="Inverse of per-stream decode speed. 0.06 s ≈ 17 tok/s."),
    ts("End-to-end request latency p95", [(E2E_P95, "{{model_name}}")], (16, 14, 8, 7), "s", 0, fill=0),

    row("Cache & scheduling", 21),
    ts("KV cache usage", [(KV_BY, "{{model_name}}")], (0, 22, 8, 7), "percent", 0, 100,
       desc="Near 100% with requests waiting means preemptions next. Raise GMU or lower max-model-len / max-num-seqs."),
    ts("Prefix cache hit rate", [(PREFIX_HIT_BY, "{{model_name}}")], (8, 22, 8, 7), "percent", 0, 100,
       desc="Agent harnesses should sit high here. Near 0 means something upstream is mutating the prompt head."),
    ts("Queue", [(RUNNING_BY, "running {{model_name}}"), (WAITING_BY, "waiting {{model_name}}")], (16, 22, 8, 7), "short", 0, stack=True),
    ts("Preemptions (5m)", [(PREEMPT_BY, "{{model_name}}")], (0, 29, 8, 6), "short", 0, fill=0,
       desc="A request evicted from the GPU and re-prefilled from scratch. Any non-zero rate during use is a KV-cache sizing problem."),

    row("Speculative decoding", 35),
    ts("Draft acceptance rate", [(SPEC_ACCEPT, "{{model_name}}")], (0, 36, 12, 7), "percent", 0, 100,
       desc="Share of drafted tokens the target model accepted. MTP on Qwen3.8 typically 60–80%. Falling = switch the drafter or lower num_speculative_tokens."),
    ts("Accepted tokens per step", [(SPEC_PER_STEP, "{{model_name}}")], (12, 36, 12, 7), "short", 0, fill=0,
       desc="Effective speed-up factor. 1.0 = no gain over plain decoding."),
], tags=["grace"], links=NAV)

# ---------------------------------------------------------------- write

OUT.mkdir(exist_ok=True)
for name, d in [("index", index), ("system", system), ("containers", containers), ("models", models)]:
    (OUT / f"{name}.json").write_text(json.dumps(d, indent=1))
    print(f"wrote {name}.json ({len(d['panels'])} panels)")
