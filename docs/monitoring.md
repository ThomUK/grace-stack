← [README](../README.md)

# Monitoring

Prometheus (30 d retention) scrapes every vLLM service, dcgm-exporter (GPU temps / power / utilisation),
node-exporter (host memory, CPU, hwmon temps, disk) and cadvisor (per-container CPU / memory / restarts). None of
them are published on the host; Grafana is.

## Grafana
Four provisioned dashboards, home is **overview**: stat tiles that link to **system** (GPU/host),
**containers** (cadvisor) and **models** (vLLM: tok/s, TTFT, KV and prefix cache, preemptions, draft acceptance).

Dashboards are code, and the JSON is generated and gitignored because links in it use `TAILNET_HOST`.
Edit `monitoring/grafana/build_dashboards.py`, then

    python3 monitoring/grafana/build_dashboards.py && docker compose restart grafana

Provisioning has `allowUiUpdates: false`, so UI edits don't stick — that's deliberate. Metric names worth knowing:
`vllm:generation_tokens_total`, `vllm:time_to_first_token_seconds`, `vllm:kv_cache_usage_perc`,
`vllm:prefix_cache_hits_total`, `vllm:num_preemptions_total`, `vllm:spec_decode_num_accepted_tokens_total`;
`DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_POWER_USAGE`; `node_memory_MemAvailable_bytes`; `container_memory_working_set_bytes`.
Browse what's actually being scraped at `http://prometheus:9090` from inside the network, or add a temporary port.

Auth is Grafana's own login (`GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` in `.env`). For passwordless tailnet
login instead, Grafana's auth proxy can trust the `Tailscale-User-Login` header that `tailscale serve` adds — only
safe while Grafana stays on 127.0.0.1, and only for user-owned (not tagged) devices.

Prometheus has a static target per vLLM service in `monitoring/prometheus/prometheus.yml`; targets for models that
are down simply show as down. The vLLM dashboards key on `model_name` (the served name), so siblings of one model
share a line.
