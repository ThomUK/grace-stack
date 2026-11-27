← [README](../README.md)

# Pixel Dog

A local voice assistant for the household: thin devices (an ESP32 dog, or a laptop dev client) stream
microphone audio over one WebSocket to an **orchestrator** on this machine, which does voice-activity
detection and endpointing on the CPU and then calls STT, LLM and TTS services over HTTP. The orchestrator
is the `pixel-dog` container in `apps/pixel-dog/compose.yaml`; its source lives in a separate private repo,
and that repo's `deploy/grace-stack/apps/pixel-dog/compose.yaml` is the master copy of the module here.

## What the module needs

| in `.env` | meaning |
|---|---|
| `PIXELDOG_DATA_DIR` | folder holding `profiles/` (per-person settings, device tokens, conversation logs). Personal data: stays on this machine, never in either repo |
| `PIXELDOG_UID`, `PIXELDOG_GID` | the container runs as this user (default 1000:1000) and must own the data folder |
| `PIXELDOG_PIPELINE` | `echo` (plays your speech back; the default), `fake` (STT→LLM→TTS with stubs), `full` (set here once the three backends run; `/readyz` then reports each) |
| `PIXELDOG_DEFAULT_LLM_URL` | which chat endpoint answers, until a dedicated voice model exists. Changing this one line changes the model: pixel-dog asks the endpoint for its served name (`PIXELDOG_DEFAULT_LLM_MODEL` overrides it; rarely needed) |
| `PIXELDOG_HOME_LAT`, `PIXELDOG_HOME_LON` | home coordinates for the weather tool (Open-Meteo); unset means no weather tool |
| `PIXELDOG_KIWIX_URL` | where the wikipedia tool finds kiwix; defaults to `http://kiwix:8080`, set it empty to switch the tool off |

The image `grace/pixel-dog:latest` is **not built by this repo**. It is built and deployed from the private
repo: `make update` there pulls the latest `main`, builds the image from `server/` (tagged with the git SHA as
well as `latest`), recreates the container in this stack and waits until `/healthz` reports the new SHA.
`make status` compares what is running with the repo, and `make rollback TAG=<sha>` re-tags an older build as
`latest` and restarts. None of these copy the compose module: if the master copy changes, copy it here by hand
and commit it. Profiles and tokens are also minted from that repo (`make profile`, `make token`).

## Port and exposure

8765 is the one port in the stack bound to `0.0.0.0` rather than loopback: ESP32s cannot run Tailscale, so
devices reach `ws://<grace-lan-ip>:8765/ws` directly over the LAN. That bind also answers on the tailnet
address, so it needs no `tailscale serve` line. Docker publishes it past ufw, so the LAN side is only as
private as the LAN. The first WebSocket message must carry a device token (random 32 bytes, stored hashed in
`profiles/tokens.yaml`); a wrong one gets an auth error and the socket is closed. `/healthz` and `/readyz`
are open and show only the pipeline mode, version and counts.

## Backends (pending)

`full` mode expects three services. The compose module already names them; two exist, the third is
stood in for by an existing chat model via `.env`:

| env var in the module | service | planned |
|---|---|---|
| `PIXELDOG_STT_URL=http://stt:8000/v1` | `stt` | **done**: `vllm-whisper` in `models/compose.yml` (port 8008, profile `whisper`), with `stt` as a network alias. Whether another server beats vLLM on latency is still open |
| `PIXELDOG_TTS_URL=http://kokoro:8880/v1` | `kokoro` | **done**: `models/kokoro/compose.yaml`, the upstream arm64 GPU image (CUDA 12.9 torch runs on GB10 with a capability warning), port 8880, always on, ~2 GiB; 6 s of speech in 0.2 s warm |
| `PIXELDOG_DEFAULT_LLM_URL=http://vllm-voice:8000/v1` | `vllm-voice` | **interim**: `.env` overrides the URL to any chat service here (tried so far: `vllm-qwen36moe`, `vllm-qwen38flash`, `vllm-inkling2mini`); pixel-dog sends `enable_thinking: false` per request, so the vLLM flags stay as they are. Inkling 2 Mini ignores that field and cannot stop thinking, so pixel-dog recognises it by name and instead sends `reasoning_effort: minimal`, waits 10 s rather than 5 s for the first word and adds 300 to `max_tokens` (the `MODEL_TRAITS` table in pixel-dog's `llm.py`): see [models.md](models.md). A dedicated small voice model is still the plan |

The voice set today is `vllm-qwen36moe` (GMU 0.25) + `vllm-whisper` (0.05) + `kokoro` (~2 GiB, no fraction), well inside the cap, so nothing else needed lowering; a dedicated voice LLM may change that; record
the chosen values in [models.md](models.md) when they land. Until then `echo` and `fake` work with nothing else
running.

## Wikipedia tool

Outline only; the design and its open questions are in `WIKIPEDIA_TOOL.md` in the private repo.

- The orchestrator offers the LLM one tool, `wiki_lookup`, which takes a few keyword search terms and returns a
  short plain-text extract of the best-matching article, or `not_found`.
- It reads the stack's own [kiwix](kiwix.md) at `PIXELDOG_KIWIX_URL` (default `http://kiwix:8080`), so lookups
  never leave this machine. With the variable empty, or kiwix unreachable, the tool is not offered.
- Book names carry a date, so the tool discovers them from kiwix's catalogue rather than hard-coding them.
- Each profile chooses its book (`wiki_book`: `en_mini` by default, `en_simple` for kids) and must list
  `wiki_lookup` in its `tools` allowlist.
- To do: measured lookup latency, and how to check from `/readyz` or the logs that the tool is registered.

## Day to day

    docker compose up -d pixel-dog        # (re)create after a .env or compose change
    docker compose logs -f pixel-dog      # one JSON timing record per turn
    curl localhost:8765/healthz           # {"status":"ok","git_sha":"a6c0604","pipeline":"full",...}

The switchboard ignores this service (it only lists `vllm serve` commands), and Prometheus does not scrape it:
the orchestrator has no `/metrics` endpoint yet.
