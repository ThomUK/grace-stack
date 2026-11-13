← [README](../README.md)

# Models

One vLLM service per model *variant* in `models/compose.yml`. Services that publish the same host port are
**siblings**: one model behind different quants or drafters, with the same served name, so clients never notice
which one is up. Only one sibling can run at a time; the switchboard swaps between them.

Every model service sits behind a profile named after it (minus `vllm-`), so `docker compose up -d` never starts
or recreates a model: they start from the switchboard (or `docker compose --profile X up -d vllm-X`) and come back
after a reboot through `restart: unless-stopped`.
Model weights are in `~/.cache/huggingface` (`HF_CACHE`), outside the repo. `vllm-cache` keeps torch.compile output.
Ports on 127.0.0.1 are fronted by `tailscale serve`; a `0.0.0.0` bind on a port Tailscale also serves fails at boot
("address already in use"), so never give both the same port.

## Adding a model
Add a `vllm serve` service to `models/compose.yml` with its own profile, a host port (shared with siblings, or a new
one), a `--served-model-name`, and `switchboard.*` labels (see [switchboard.md](switchboard.md#labels)). The
switchboard picks it up from `docker compose config` on its next request. Then add a scrape target in
`monitoring/prometheus/prometheus.yml`, a seed URL in `OPENAI_API_BASE_URLS` in `apps/compose.yml`, and, for a new
port, a `tailscale serve` line.

## What is wired in

| service | port | served name | quant / notes |
|---|---|---|---|
| vllm-gemma12 | 8000 | `gemma4-12b` | google bf16; audio in; GMU 0.30 |
| vllm-qwen38-nvfp4 | 8001 | `qwen3.8-27b` | nvidia modelopt NVFP4, MTP 3 drafts; GMU 0.35 |
| vllm-qwen38-fp8 | 8001 | `qwen3.8-27b` | Qwen FP8, MTP 3 drafts; GMU 0.45 |
| vllm-qwen36moe | 8002 | `qwen3.6-35b-a3b` | RedHatAI NVFP4; MoE, 3B active; MTP; GMU 0.25 |
| vllm-ornith15-nvfp4 | 8003 | `ornith-1.5-35b-a3b` | ornith-ai modelopt NVFP4; MTP 1 draft; GMU 0.30 |
| vllm-ornith15-agentic | 8003 | `ornith-1.5-35b-a3b` | primitive-ai NVFP4-FP8, GPTQ-calibrated on tool calls; GMU 0.30 |
| vllm-ornith15-bf16 | 8003 | `ornith-1.5-35b-a3b` | unquantised reference, 67 GiB; GMU 0.72, runs alone |
| vllm-codernext-nvfp4 | 8004 | `qwen3-coder-next` | RedHatAI NVFP4; non-thinking; GMU 0.45 |
| vllm-codernext-fp8 | 8004 | `qwen3-coder-next` | Qwen FP8, 75 GiB; GMU 0.72, runs alone |
| vllm-qwen35-122b | 8005 | `qwen3.5-122b-a10b` | RedHatAI NVFP4, 71 GiB; vision in; MTP 2 drafts; GMU 0.75, runs alone |
| vllm-nemotron-omni | 8006 | `nemotron-3-nano-omni-30b-a3b` | nvidia FP8; image + video in; reasoning; fp8 KV cache; GMU 0.45 |
| vllm-qwen38flash | 8007 | `qwen3.8-flash-next` | nvidia FP4 QAD of the 125B-A6B MoE, 95 GiB + n-gram table on disk; image + video in; MTP 4 drafts; GMU 0.80, runs alone; **community image**, see below |

The switchboard shows each card's size on disk, weights in memory and max context live, with a link to the
origin model card.

Tailscale serve, one line per port if you prefer them explicit:

    sudo tailscale serve --bg --https=8000  http://127.0.0.1:8000    # gemma API
    sudo tailscale serve --bg --https=8001  http://127.0.0.1:8001    # qwen38 API (only if you run one of those)
    sudo tailscale serve --bg --https=8002  http://127.0.0.1:8002    # qwen36moe API
    sudo tailscale serve --bg --https=8003  http://127.0.0.1:8003    # ornith15 API
    sudo tailscale serve --bg --https=8004  http://127.0.0.1:8004    # codernext API
    sudo tailscale serve --bg --https=8005  http://127.0.0.1:8005    # qwen3.5-122b API
    sudo tailscale serve --bg --https=8006  http://127.0.0.1:8006    # nemotron omni API
    sudo tailscale serve --bg --https=8007  http://127.0.0.1:8007    # qwen3.8-flash-next API

## Performance (GB10)
- Decode is bandwidth-bound (273 GB/s). Qwen3.8-27B-FP8 single-stream: ~8 tok/s stock, ~17 with MTP (3 drafts).
  The Models dashboard's acceptance-rate panel says whether the drafter is earning its keep.
- Never `--enforce-eager`: ~55% throughput loss on sm_121.
- GMU (`--gpu-memory-utilization`): gemma 0.30, ornith15 0.30 (nvfp4, agentic) or 0.72 (bf16), qwen36moe 0.25,
  qwen38 0.35 (nvfp4) or 0.45 (fp8), codernext 0.45 (nvfp4) or 0.72 (fp8), nemotron-omni 0.45, qwen35-122b 0.75.
  vLLM sees 121.6 GiB and takes weights + ~7 GiB overhead out of GMU × that; the rest is KV cache. Keep the
  running sum under ~0.85, so a qwen38, codernext-nvfp4 or nemotron-omni runs with at most one small model and
  the 0.72+ ones run alone; the switchboard enforces this, the overview Memory tile is the check.
- Models must start **one at a time**: two loading together each measure the shared pool while the other is still
  allocating, and one ends up with "No available memory for the cache blocks". The switchboard serialises this.
- Nemotron Omni accepts audio too, but that needs `pip install vllm[audio]` in the image, which this stack
  avoids (see below). Image and video in are enabled; `--kv-cache-dtype fp8` is the card's recommendation.
- Qwen3-Coder-Next is a hybrid (Gated DeltaNet + gated attention, 512 experts, 3B active). Neither quant ships MTP
  weights, so no speculative decoding; 3B active keeps decode quick anyway. Its KV cache is small (12 attention
  layers, 2 KV heads), which is why 262k context fits in GMU 0.45.
- Tool parsers: `gemma4`, `qwen3_xml`, `qwen3_coder`. List: `docker compose exec vllm-gemma12 vllm serve --help=all | grep -A4 tool-call-parser`.

## Qwen3.8-Flash-Next and the community image
`nvidia/Qwen3.8-Flash-Next-FP4-Local` is a 125B-parameter MoE (6B active) with a 51B-parameter n-gram embedding
table, quantised by NVIDIA for local Blackwell boxes. Upstream vLLM cannot load its NVFP4 scale format yet, so
`vllm-qwen38flash` is the one service not on `grace/vllm`: it runs `eugr/spark-vllm-b12x:nightly-20261102`, the community
DGX Spark build with the `b12x` kernels, with the environment, flags, extra caches and relaxed seccomp/apparmor
from the model card. The n-gram table is streamed from the HF cache on disk (`VLLM_PLE_TABLE_MEMORY=disk`), so keep
the cache on the NVMe. At GMU 0.80 nothing else fits beside it; the first start compiles and builds that table and
can take well over the usual four minutes, hence its longer health `start_period`. The tag is pinned to a dated nightly
on purpose; `latest` moves with the author's builds, so bump the date deliberately after testing.

## Why the vLLM image is three lines
`vllm/vllm-openai:*-aarch64` ships vLLM, torch and FlashInfer built together. Pip-installing vLLM over an NVIDIA
base desyncs FlashInfer's Python package from its kernels ("Expected 27 but got 29 arguments");
`FLASHINFER_DISABLE_VERSION_CHECK=1` only hides it. Do not pip install vllm, torch or flashinfer on top.
Bump the tag deliberately: `docker run --rm quay.io/skopeo/stable list-tags docker://vllm/vllm-openai | grep aarch64 | tail`.
