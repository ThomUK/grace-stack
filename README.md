# grace-stack

Everything that runs on grace (ASUS Ascent GX10, GB10, 128 GB unified), as compose modules.

    docker-compose.yml      include: one line per module
    .env                    secrets and per-machine knobs (gitignored); .env.example is the template
    models/                 vLLM endpoints       compose.yml, vllm/Dockerfile
    apps/                   Open WebUI, switchboard compose.yml, switchboard/
    monitoring/             Prometheus + Grafana compose.yml, prometheus/, grafana/

Each module is a folder holding its own `compose.yml` and config; paths inside it are relative to the folder.
`include:` in the top-level file assembles them into one project (`grace-stack`), so every service shares a
network and container names resolve across modules. To redeploy a subset elsewhere, copy the folders you want
and list only those in `docker-compose.yml`.

## Services

| module     | service        | port                 | notes |
|------------|----------------|----------------------|-------|
| models     | vllm-gemma12   | 127.0.0.1:8000       | gemma-4-12b-it as `gemma4-12b`; audio in |
| models     | vllm-qwen38-nvfp4 ╷ -fp8 | 127.0.0.1:8001 | Qwen3.8-27B as `qwen3.8-27b`: nvidia NVFP4 or Qwen FP8, both with MTP; one at a time |
| models     | vllm-qwen36moe | 127.0.0.1:8002       | Qwen3.6-35B-A3B-NVFP4 as `qwen3.6-35b-a3b`; MTP spec decode |
| models     | vllm-ornith15-nvfp4 ╷ -agentic ╷ -bf16 | 127.0.0.1:8003 | Ornith-1.5-35B-A3B as `ornith-1.5-35b-a3b`: ornith-ai NVFP4, primitive-ai agentic NVFP4-FP8, or the unquantised bf16 (GMU 0.72, runs alone); one at a time |
| models     | vllm-codernext-nvfp4 ╷ -fp8 | 127.0.0.1:8004 | Qwen3-Coder-Next 80B-A3B as `qwen3-coder-next`: RedHatAI NVFP4 (GMU 0.45) or Qwen FP8 (0.72, runs alone); non-thinking; one at a time |
| models     | vllm-qwen35-122b | 127.0.0.1:8005     | Qwen3.5-122B-A10B RedHatAI NVFP4 as `qwen3.5-122b-a10b`; vision in; MTP; GMU 0.75, runs alone |
| models     | vllm-nemotron-omni | 127.0.0.1:8006   | Nemotron 3 Nano Omni 30B-A3B FP8 as `nemotron-3-nano-omni-30b-a3b`; image + video in; reasoning; GMU 0.45 |
| apps       | open-webui     | 127.0.0.1:12000      | data in external volume `open-webui` |
| apps       | switchboard    | 127.0.0.1:8080       | start/stop models and presets; holds the Docker socket |
| monitoring | grafana        | 127.0.0.1:3000       | the dashboards; login from .env |
| monitoring | prometheus     | internal             | 30 d retention |
| monitoring | dcgm-exporter  | internal             | GPU temps/power/util |
| monitoring | node-exporter  | internal             | host memory, CPU, hwmon temps, disk |
| monitoring | cadvisor       | internal             | per-container CPU/memory/restarts |

Every model service sits behind a profile named after it (minus `vllm-`), so `docker compose up -d` never starts
or recreates a model: they start from the switchboard (or `docker compose --profile X up -d vllm-X`) and come back
after a reboot through `restart: unless-stopped`. Services sharing a host port are *siblings*, one model behind
different quants or drafters; only one can run, and the switchboard swaps between them.
Model weights are in `~/.cache/huggingface` (`HF_CACHE`), outside the repo. `vllm-cache` keeps torch.compile output.
Ports on 127.0.0.1 are fronted by `tailscale serve`; a `0.0.0.0` bind on a port Tailscale also serves fails at boot
("address already in use"), so never give both the same port.

## Bring-up
    cp .env.example .env && nano .env        # HF_TOKEN, GRAFANA_ADMIN_PASSWORD, TAILNET_HOST
    chmod 600 .env
    docker volume create open-webui          # declared external, so compose won't create it
    python3 monitoring/grafana/build_dashboards.py   # dashboard JSON is generated, not committed
    docker compose build                     # grace/vllm from the official aarch64 image, grace/switchboard
    docker compose up -d                     # infra only; run from the repo root: the switchboard mounts $PWD
    xdg-open http://127.0.0.1:8080           # switchboard: pick a preset; models take ~4 min to (healthy)

    sudo tailscale serve --bg --https=12000 http://127.0.0.1:12000   # Open WebUI
    sudo tailscale serve --bg --https=3000  http://127.0.0.1:3000    # Grafana
    sudo tailscale serve --bg --https=8000  http://127.0.0.1:8000    # gemma API
    sudo tailscale serve --bg --https=8001  http://127.0.0.1:8001    # qwen38 API (only if you run one of those)
    sudo tailscale serve --bg --https=8002  http://127.0.0.1:8002    # qwen36moe API
    sudo tailscale serve --bg --https=8003  http://127.0.0.1:8003    # ornith15 API
    sudo tailscale serve --bg --https=8004  http://127.0.0.1:8004    # codernext API
    sudo tailscale serve --bg --https=8005  http://127.0.0.1:8005    # qwen3.5-122b API
    sudo tailscale serve --bg --https=8006  http://127.0.0.1:8006    # nemotron omni API
    sudo tailscale serve --bg --https=8080  http://127.0.0.1:8080    # switchboard
    sudo tailscale serve --bg --https=11000 http://127.0.0.1:11000   # NVIDIA DGX Dashboard (system updates, settings)

URLs: `https://<machine>.<tailnet>.ts.net:<port>`, the name you set as `TAILNET_HOST`. HTTPS is bound to that
name (mic in Open WebUI needs it).

## Security
Nothing here is meant to face the internet. Loopback binds plus `tailscale serve` are the only access control
for most services, so every member of your tailnet can reach them:
- The vLLM endpoints run without `--api-key`.
- switchboard holds the Docker socket, which is root-equivalent on the host, and has no login. Keep it off the LAN.
- cadvisor is `privileged`; cadvisor and node-exporter mount the host filesystem read-only.

## Operating
    docker compose up -d                        # apply edits; recreates only what changed
    docker compose up -d --build                # also rebuild grace/vllm after a Dockerfile edit
    docker compose --profile gemma12 up -d vllm-gemma12   # one model by hand (the switchboard does the same)
    docker compose stop vllm-ornith15-agentic   # free its memory; `start` reloads in ~4 min (or use the switchboard)
    docker compose logs -f vllm-ornith15-agentic   # throughput + prefix-cache hit rate every 10 s
    docker compose config                       # the fully assembled file; check paths and ${VARS}
    docker compose down                         # keeps volumes (-v wipes vllm-cache, prometheus, grafana)

Plain `up -d` never rebuilds an image and never touches a model (they are all behind profiles). A changed model
`command:` takes effect on its next start or restart from the switchboard, which passes the profile.
A container that exists but lost its port mapping needs `up -d --force-recreate <service>`.

## Switchboard
`https://<machine>.<tailnet>.ts.net:8080`: a **Running** strip (what is up, with stop / restart / logs), presets
that switch whole sets (stop what is not wanted, then start the rest), one collapsed section per model maker
holding a card per vLLM service, and links to the NVIDIA DGX Dashboard (OS updates and settings, port 11000 on
the host), Grafana and Open WebUI. A maker's summary line shows its logo, how many variants it has and one
status dot per variant; sections stay collapsed until opened, and toggles persist for the page session.
It discovers the models from `docker compose config`, so a new `vllm serve` service in `models/compose.yml`
appears by itself; the memory fraction shown is the service's `--gpu-memory-utilization`. It refuses any start
that would take the running sum above `MAX_GMU` (.env, default 0.85). Siblings (same host port) are shown
together; while one holds the port the others offer **swap**, which stops the holder and starts the chosen one.
Each card carries the maker's logo (its Hugging Face org avatar) and, when the repo owner is a different org,
the quantiser's logo next to it. The model id links to its origin card (and the drafter's, where spec decoding
loads one), so what is being served is one click from where it came from.
Under that: size on disk, weights in memory (the snapshot's safetensors bytes, which is what vLLM reports as
"Model loading took" before any KV cache), and max context. Sizes come from `~/.cache/huggingface`, mounted
read-only into the container; a model not yet downloaded says so.

Models start **one at a time**. Two loading together each measure the shared GB10 pool while the other is
still allocating, and one ends up with "No available memory for the cache blocks". A preset therefore starts
its services in sequence, waiting for each to turn healthy (~4 min), and start / swap are refused while
anything is still loading.

Per-model metadata is compose `labels` on the service, next to the command it describes:
- `switchboard.note`: one line on the card (who made the quant, size, drafter).
- `switchboard.name`: display name in the generated VS Code entry. The rest of that entry comes from the command
  line (`toolCalling` from `--enable-auto-tool-choice`, `thinking` from `--reasoning-parser`, `vision` from
  `--limit-mm-per-prompt`, `maxInputTokens` = `--max-model-len` minus 16000).
- `switchboard.vision: "true"`: for a multimodal model that has no `--limit-mm-per-prompt` flag.
- `switchboard.card`: origin URL, when the weights are not a Hugging Face repo.
- `switchboard.logo`: Hugging Face org of the model's maker, when the repo owner is only the quantiser (a RedHatAI
  or nvidia quant of a Qwen model says `Qwen`). Both avatars are shown, and the maker decides which section the
  card sits in.

`apps/switchboard/switchboard.json`, re-read on every request, holds the rest:
- `presets`: named sets of services (a preset naming two siblings is refused).
- `links`: the header links; `{host}` is `TAILNET_HOST`.

The container runs `docker compose` against this repo, bind-mounted read-only at the same absolute path as on the host.
That keeps compose's config hashes identical to your shell's, so neither side recreates containers the other
started. After editing `apps/switchboard/switchboard.py`: `docker compose up -d --build switchboard`.

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
Admin → Settings → Connections: one `http://<container>:8000/v1` per model service (container port, not host
mapping; key anything), all eleven seeded by `OPENAI_API_BASE_URLS` in `apps/compose.yml`. Siblings share a served
name but only one is up, so each model is listed once. Ollama API off. Saved connections in the volume override
the env seeds, so add new services there by hand.
Audio: STT Whisper (Local), TTS Web API.

## VS Code
Chat: Manage Language Models → `chatLanguageModels.json`. Each switchboard card has a copy button for this; by hand:

    [{ "name": "Grace", "vendor": "customendpoint", "apiType": "chat-completions",
       "models": [{ "id": "ornith-1.5-35b-a3b", "name": "Ornith-1.5-35B-A3B",
                    "url": "https://<machine>.<tailnet>.ts.net:8003/v1",
                    "toolCalling": true, "vision": true,
                    "maxInputTokens": 246144, "maxOutputTokens": 16000,
                    "thinking": true, "supportsReasoningEffort": ["low","medium","high"],
                    "reasoningEffortFormat": "chat-completions" }] }]

`id` must equal `--served-model-name`. For Qwen3-Coder-Next: id `qwen3-coder-next`, port 8004, `thinking: false`,
`maxInputTokens` 262144, no reasoning-effort keys (the model has no thinking mode). `supportsReasoningEffort` adds a Thinking Effort submenu (the > arrow in the
model picker); whether the model honours it is unverified — `--default-chat-template-kwargs '{"enable_thinking": false}'`
on the vLLM command is the lever that certainly works. First turn on a fresh workspace is a 20–40 s prefill.

## Performance (GB10)
- Decode is bandwidth-bound (273 GB/s). Qwen3.8-27B-FP8 single-stream: ~8 tok/s stock, ~17 with MTP (3 drafts).
  The Models dashboard's acceptance-rate panel says whether the drafter is earning its keep.
- Never `--enforce-eager`: ~55% throughput loss on sm_121.
- GMU: gemma 0.30, ornith15 0.30 (nvfp4, agentic) or 0.72 (bf16), qwen36moe 0.25, qwen38 0.35 (nvfp4) or 0.45
  (fp8), codernext 0.45 (nvfp4) or 0.72 (fp8), nemotron-omni 0.45, qwen35-122b 0.75. vLLM sees 121.6 GiB and
  takes weights + ~7 GiB overhead out of GMU × that; the rest is KV cache. Keep the running sum under ~0.85, so
  a qwen38, codernext-nvfp4 or nemotron-omni runs with at most one small model and the 0.72+ ones run alone;
  the switchboard enforces this, the overview Memory tile is the check.
- Nemotron Omni accepts audio too, but that needs `pip install vllm[audio]` in the image, which this stack
  avoids (see below). Image and video in are enabled; `--kv-cache-dtype fp8` is the card's recommendation.
- Qwen3-Coder-Next is a hybrid (Gated DeltaNet + gated attention, 512 experts, 3B active). Neither quant ships MTP
  weights, so no speculative decoding; 3B active keeps decode quick anyway. Its KV cache is small (12 attention
  layers, 2 KV heads), which is why 262k context fits in GMU 0.45.
- Tool parsers: `gemma4`, `qwen3_xml`, `qwen3_coder`. List: `docker compose exec vllm-gemma12 vllm serve --help=all | grep -A4 tool-call-parser`.

## Why the vLLM image is three lines
`vllm/vllm-openai:*-aarch64` ships vLLM, torch and FlashInfer built together. Pip-installing vLLM over an NVIDIA
base desyncs FlashInfer's Python package from its kernels ("Expected 27 but got 29 arguments");
`FLASHINFER_DISABLE_VERSION_CHECK=1` only hides it. Do not pip install vllm, torch or flashinfer on top.
Bump the tag deliberately: `docker run --rm quay.io/skopeo/stable list-tags docker://vllm/vllm-openai | grep aarch64 | tail`.

## Backup / restore Open WebUI
    docker run --rm -v open-webui:/d -v $PWD:/b alpine tar czf /b/open-webui-backup-$(date +%F).tgz -C /d .
    docker run --rm -v open-webui:/d -v $PWD:/b alpine sh -c 'cd /d && tar xzf /b/open-webui-backup-<date>.tgz'
