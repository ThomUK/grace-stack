# grace-stack

A self-hosted LLM stack for one NVIDIA GB10 box (here an ASUS Ascent GX10, the DGX Spark class: 128 GB of
unified memory shared by CPU and GPU). vLLM serves a handful of open models; a web **switchboard** starts,
stops and swaps them inside that one memory pool, behind its own login; Open WebUI gives you a chat window;
Grafana watches the GPU and the token rates. Everything binds to loopback and is reached over Tailscale.

It is for anyone with the same class of machine who wants the whole thing running in an afternoon, and for
anyone who wants to see how a one-box inference setup fits together: which quants fit beside which, why models
have to load one at a time, how to serve a model under one name while swapping the weights behind it.

<!-- ![the switchboard](docs/switchboard.png)   drop a screenshot at docs/switchboard.png and uncomment -->

This README and the `docs/` folder are also rendered at <https://thomuk.github.io/grace-stack/>.

## Layout

    docker-compose.yml      include: one line per module
    .env                    secrets and per-machine knobs (gitignored); .env.example is the template
    models/                 vLLM endpoints       compose.yml, vllm/Dockerfile
    apps/                   Open WebUI, switchboard compose.yml, switchboard/
    monitoring/             Prometheus + Grafana compose.yml, prometheus/, grafana/
    docs/                   the detail: models, switchboard, security, monitoring, clients

Each module is a folder with its own `compose.yml`; `include:` assembles them into one project, so every
service shares a network and container names resolve across modules. To run a subset elsewhere, copy the
folders you want and list only those in `docker-compose.yml`.

## What runs

| role | service(s) | port | notes |
|---|---|---|---|
| models | `vllm-*`, one per model variant | 127.0.0.1:8000–8007 | variants of one model share a port and a name; one of them at a time |
| chat | open-webui | 127.0.0.1:12000 | own accounts; data in the external volume `open-webui` |
| control | switchboard | 127.0.0.1:8080 | start / stop / swap models, presets; own login; holds the Docker socket |
| monitoring | grafana | 127.0.0.1:3000 | four dashboards; prometheus, dcgm-, node-exporter and cadvisor stay internal |

| port | served as | model |
|---|---|---|
| 8000 | `gemma4-12b` | Gemma 4 12B, audio in |
| 8001 | `qwen3.8-27b` | Qwen3.8 27B |
| 8002 | `qwen3.6-35b-a3b` | Qwen3.6 35B-A3B (MoE) |
| 8003 | `ornith-1.5-35b-a3b` | Ornith 1.5 35B-A3B (MoE), three quants |
| 8004 | `qwen3-coder-next` | Qwen3-Coder-Next 80B-A3B, two quants |
| 8005 | `qwen3.5-122b-a10b` | Qwen3.5 122B-A10B, vision in |
| 8006 | `nemotron-3-nano-omni-30b-a3b` | Nemotron 3 Nano Omni 30B-A3B, image + video in |
| 8007 | `qwen3.8-flash-next` | Qwen3.8-Flash-Next 125B-A6B, image + video in; community vLLM image, runs alone |

Which quant, how much memory each takes and the vLLM flags are in `models/compose.yml` and
[docs/models.md](docs/models.md); the switchboard shows size, context and origin for each one live.

## Quick start

    cp .env.example .env && nano .env        # HF_TOKEN, GRAFANA_ADMIN_PASSWORD, TAILNET_HOST
    chmod 600 .env
    docker volume create open-webui          # declared external, so compose won't create it
    python3 monitoring/grafana/build_dashboards.py   # dashboard JSON is generated, not committed
    docker compose build                     # grace/vllm from the official aarch64 image, grace/switchboard
    docker compose up -d                     # infra only; run from the repo root: the switchboard mounts $PWD
    docker logs switchboard                  # shows the one-time setup token for the switchboard login
    xdg-open http://127.0.0.1:8080           # set up login (token, password, scan QR), then pick a preset; models take ~4 min

Then publish the ports to your tailnet (Funnel stays off):

    for p in 3000 12000 8080 11000 8000 8001 8002 8003 8004 8005 8006 8007; do
      sudo tailscale serve --bg --https=$p http://127.0.0.1:$p
    done

URLs are `https://<machine>.<tailnet>.ts.net:<port>`, the name you set as `TAILNET_HOST`. 11000 is NVIDIA's
own DGX Dashboard (OS updates, settings), linked from the switchboard header. HTTPS on the tailnet name is what
lets the mic work in Open WebUI.

## The switchboard

One page: a **Running** strip with stop / restart / logs, presets that switch whole sets of models, and a
collapsed section per model maker with a card per variant. Variants of one model share a port and a served
name, so a card offers **swap** while a sibling holds the port, and clients never notice. It discovers the
services from `docker compose config`, refuses any start that would push the memory fractions past `MAX_GMU`,
and starts models one at a time, because two loading together mismeasure the shared pool. Each card links the
model to its origin on Hugging Face and shows size on disk, weights in memory and max context. The login is a
password plus an authenticator code, set up in the browser on first visit with a token from the container log.
Details, labels and the login design: [docs/switchboard.md](docs/switchboard.md).

## Security

Nothing here is meant to face the internet. Everything below is reachable by any device on the tailnet, so
each service's own login is the second layer:

| exposed | auth |
|---|---|
| Grafana :3000, Open WebUI :12000 | their own user accounts |
| DGX Dashboard :11000 | NVIDIA's own login |
| switchboard :8080 | password + TOTP; it holds the Docker socket, which is root-equivalent on the host |
| vLLM APIs :8000–:8007 | **none yet** (`--api-key` is the fix; pending) |

Restricting the admin ports to specific devices with Tailscale ACLs, and what the switchboard login does and
does not defend against: [docs/security.md](docs/security.md).

## Day to day

    docker compose up -d                        # apply edits; recreates only what changed, never a model
    docker compose up -d --build                # also rebuild grace/vllm after a Dockerfile edit
    docker compose --profile gemma12 up -d vllm-gemma12   # one model by hand (the switchboard does the same)
    docker compose stop vllm-ornith15-agentic   # free its memory; `start` reloads in ~4 min (or use the switchboard)
    docker compose logs -f vllm-ornith15-agentic   # throughput + prefix-cache hit rate every 10 s
    docker compose config                       # the fully assembled file; check paths and ${VARS}
    docker compose down                         # keeps volumes (-v wipes vllm-cache, prometheus, grafana)

Plain `up -d` never rebuilds an image and never touches a model (they are all behind profiles). A changed model
`command:` takes effect on its next start or restart from the switchboard, which passes the profile.
A container that exists but lost its port mapping needs `up -d --force-recreate <service>`.

## More

- [docs/models.md](docs/models.md): every variant with its quant and memory fraction, how to add a model, GB10
  performance notes, why the vLLM image is three lines.
- [docs/switchboard.md](docs/switchboard.md): what the page shows, the compose labels it reads, how the login works.
- [docs/security.md](docs/security.md): what is exposed, Tailscale ACL grants, the auth trade-offs.
- [docs/monitoring.md](docs/monitoring.md): the Grafana dashboards, how they are generated, metric names.
- [docs/clients.md](docs/clients.md): Open WebUI connections and backup, VS Code model entries.

MIT licence.
