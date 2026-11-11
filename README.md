# grace-stack

Everything that runs on grace (ASUS Ascent GX10, GB10, 128 GB unified), as compose modules.

    docker-compose.yml      include: one line per module
    .env                    secrets and per-machine knobs (gitignored); .env.example is the template
    models/                 vLLM endpoints       compose.yml, vllm/Dockerfile
    apps/                   Open WebUI, DGX dash compose.yml
    monitoring/             Prometheus + Grafana compose.yml, prometheus/, grafana/

Each module is a folder holding its own `compose.yml` and config; paths inside it are relative to the folder.
`include:` in the top-level file assembles them into one project (`grace-stack`), so every service shares a
network and container names resolve across modules. To redeploy a subset elsewhere, copy the folders you want
and list only those in `docker-compose.yml`.

## Services

| module     | service        | port                 | notes |
|------------|----------------|----------------------|-------|
| models     | vllm-gemma12   | 127.0.0.1:8000       | gemma-4-12b-it as `gemma4-12b`; profile `gemma12` |
| models     | vllm-qwen38    | 127.0.0.1:8001       | Qwen3.8-27B-FP8 as `qwen3.8-27b`; MTP spec decode; profile `qwen38` (opt-in) |
| models     | vllm-qwen36moe | 127.0.0.1:8002       | Qwen3.6-35B-A3B-NVFP4 as `qwen3.6-35b-a3b`; MTP spec decode; profile `qwen36moe` (default) |
| models     | vllm-ornith15  | 127.0.0.1:8003       | Ornith-1.5-35B-A3B NVFP4 (quant set by `ORNITH_MODEL`) as `ornith-1.5-35b-a3b`; MTP spec decode; profile `ornith15` (default) |
| apps       | open-webui     | 127.0.0.1:12000      | data in external volume `open-webui` |
| apps       | dgx-dashboard  | 127.0.0.1:8080       | start/stop buttons; holds the Docker socket |
| monitoring | grafana        | 127.0.0.1:3000       | the dashboards; login from .env |
| monitoring | prometheus     | internal             | 30 d retention |
| monitoring | dcgm-exporter  | internal             | GPU temps/power/util |
| monitoring | node-exporter  | internal             | host memory, CPU, hwmon temps, disk |
| monitoring | cadvisor       | internal             | per-container CPU/memory/restarts |

Model weights are in `~/.cache/huggingface` (`HF_CACHE`), outside the repo. `vllm-cache` keeps torch.compile output.
Ports on 127.0.0.1 are fronted by `tailscale serve`; a `0.0.0.0` bind on a port Tailscale also serves fails at boot
("address already in use"), so never give both the same port.

## Bring-up
    cp .env.example .env && nano .env        # HF_TOKEN, GRAFANA_ADMIN_PASSWORD, TAILNET_HOST, COMPOSE_PROFILES, ORNITH_MODEL
    chmod 600 .env
    docker volume create open-webui          # declared external, so compose won't create it
    python3 monitoring/grafana/build_dashboards.py   # dashboard JSON is generated, not committed
    docker compose build                     # grace/vllm from the official aarch64 image
    docker compose up -d
    docker compose ps                        # (starting) → (healthy); models take ~4 min

    sudo tailscale serve --bg --https=12000 http://127.0.0.1:12000   # Open WebUI
    sudo tailscale serve --bg --https=3000  http://127.0.0.1:3000    # Grafana
    sudo tailscale serve --bg --https=8000  http://127.0.0.1:8000    # gemma API
    sudo tailscale serve --bg --https=8001  http://127.0.0.1:8001    # qwen38 API (only if you run that profile)
    sudo tailscale serve --bg --https=8002  http://127.0.0.1:8002    # qwen36moe API
    sudo tailscale serve --bg --https=8003  http://127.0.0.1:8003    # ornith15 API
    sudo tailscale serve --bg --https=8080  http://127.0.0.1:8080    # DGX dash

URLs: `https://<machine>.<tailnet>.ts.net:<port>`, the name you set as `TAILNET_HOST`. HTTPS is bound to that
name (mic in Open WebUI needs it).

## Security
Nothing here is meant to face the internet. Loopback binds plus `tailscale serve` are the only access control
for most services, so every member of your tailnet can reach them:
- The vLLM endpoints run without `--api-key`.
- dgx-dashboard holds the Docker socket, which is root-equivalent on the host. Keep it off the LAN.
- cadvisor is `privileged`; cadvisor and node-exporter mount the host filesystem read-only.

## Operating
    docker compose up -d                        # apply edits; recreates only what changed
    docker compose up -d --build                # also rebuild grace/vllm after a Dockerfile edit
    docker compose --profile gemma12 up -d      # one model profile (or set COMPOSE_PROFILES in .env)
    docker compose stop vllm-ornith15           # free its memory; `start` reloads in ~4 min
    docker compose logs -f vllm-ornith15        # throughput + prefix-cache hit rate every 10 s
    docker compose config                       # the fully assembled file; check paths and ${VARS}
    docker compose down                         # keeps volumes (-v wipes vllm-cache, prometheus, grafana)

Plain `up -d` never rebuilds an image. A changed `command:` recreates that container.
A container that exists but lost its port mapping needs `up -d --force-recreate <service>`.

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

Auth is Grafana's own login. For passwordless tailnet login instead, Grafana's auth proxy can trust the
`Tailscale-User-Login` header that `tailscale serve` adds — only safe while Grafana stays on 127.0.0.1.

## Open WebUI
Admin → Settings → Connections: `http://vllm-gemma12:8000/v1`, `http://vllm-ornith15:8000/v1` and
`http://vllm-qwen36moe:8000/v1` (container port, not host mapping; key anything). Ollama API off. Saved connections in the volume override the env seeds.
Audio: STT Whisper (Local), TTS Web API.

## VS Code
Chat: Manage Language Models → `chatLanguageModels.json`:

    [{ "name": "Grace", "vendor": "customendpoint", "apiType": "chat-completions",
       "models": [{ "id": "ornith-1.5-35b-a3b", "name": "Ornith-1.5-35B-A3B",
                    "url": "https://<machine>.<tailnet>.ts.net:8003/v1",
                    "toolCalling": true, "vision": true,
                    "maxInputTokens": 246144, "maxOutputTokens": 16000,
                    "thinking": true, "supportsReasoningEffort": ["low","medium","high"],
                    "reasoningEffortFormat": "chat-completions" }] }]

`id` must equal `--served-model-name`. `supportsReasoningEffort` adds a Thinking Effort submenu (the > arrow in the
model picker); whether the model honours it is unverified — `--default-chat-template-kwargs '{"enable_thinking": false}'`
on the vLLM command is the lever that certainly works. First turn on a fresh workspace is a 20–40 s prefill.

## Performance (GB10)
- Decode is bandwidth-bound (273 GB/s). Qwen3.8-27B-FP8 single-stream: ~8 tok/s stock, ~17 with MTP (3 drafts),
  ~32 with the DFlash2 drafter — which needs `TRITON_ATTN` on vLLM ≤ 0.31 (prefix-cache garbling, issue #60174).
  The Models dashboard's acceptance-rate panel says whether the drafter is earning its keep.
- Never `--enforce-eager`: ~55% throughput loss on sm_121.
- GMU: gemma 0.30, ornith15 0.30, qwen36moe 0.25, qwen38 0.45. Keep the sum of running models under ~0.85, so
  qwen38 runs with at most one of the others; the overview Memory tile is the check.
- Tool parsers: `gemma4`, `qwen3_xml`. List: `docker compose exec vllm-gemma12 vllm serve --help=all | grep -A4 tool-call-parser`.

## Why the vLLM image is three lines
`vllm/vllm-openai:*-aarch64` ships vLLM, torch and FlashInfer built together. Pip-installing vLLM over an NVIDIA
base desyncs FlashInfer's Python package from its kernels ("Expected 27 but got 29 arguments");
`FLASHINFER_DISABLE_VERSION_CHECK=1` only hides it. Do not pip install vllm, torch or flashinfer on top.
Bump the tag deliberately: `docker run --rm quay.io/skopeo/stable list-tags docker://vllm/vllm-openai | grep aarch64 | tail`.

## Backup / restore Open WebUI
    docker run --rm -v open-webui:/d -v $PWD:/b alpine tar czf /b/open-webui-backup-$(date +%F).tgz -C /d .
    docker run --rm -v open-webui:/d -v $PWD:/b alpine sh -c 'cd /d && tar xzf /b/open-webui-backup-<date>.tgz'
