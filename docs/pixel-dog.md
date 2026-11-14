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
| `PIXELDOG_PIPELINE` | `echo` (plays your speech back; the default), `fake` (STT→LLM→TTS with stubs), `full` |

The image `grace/pixel-dog:latest` is **not built by this repo**. Build it from the private repo's `server/`
folder (its `make build` tags it with the git SHA as well as `latest`), then `docker compose up -d pixel-dog`
here; `make deploy` there does both. Roll back by re-tagging an older SHA as `latest` and running `up -d` again.
Profiles and tokens are also minted from that repo (`make profile`, `make token`).

## Port and exposure

8765 is the one port in the stack bound to `0.0.0.0` rather than loopback: ESP32s cannot run Tailscale, so
devices reach `ws://<grace-lan-ip>:8765/ws` directly over the LAN. That bind also answers on the tailnet
address, so it needs no `tailscale serve` line. Docker publishes it past ufw, so the LAN side is only as
private as the LAN. The first WebSocket message must carry a device token (random 32 bytes, stored hashed in
`profiles/tokens.yaml`); a wrong one gets an auth error and the socket is closed. `/healthz` and `/readyz`
are open and show only the pipeline mode, version and counts.

## Backends (pending)

`full` mode expects three services that are not in the stack yet. The compose module already names them, so
adding each is a matter of a new module folder plus an `include:` line:

| env var in the module | service | planned |
|---|---|---|
| `PIXELDOG_STT_URL=http://stt:8000/v1` | `stt` | Whisper large-v3-turbo behind `/v1/audio/transcriptions`; which server runs fastest on GB10 is an open spike |
| `PIXELDOG_TTS_URL=http://kokoro:8880/v1` | `kokoro` | Kokoro-FastAPI, streamed PCM; aarch64 GPU image availability is the spike, CPU is the fallback |
| `PIXELDOG_DEFAULT_LLM_URL=http://vllm-voice:8000/v1` | `vllm-voice` | a small fast instruct model served as `voice-llm`, chosen for time to first sentence |

The GPU memory fractions of the existing models will need lowering so the three fit alongside them; record
the chosen values in [models.md](models.md) when they land. Until then `echo` and `fake` work with nothing else
running.

## Day to day

    docker compose up -d pixel-dog        # (re)create after an image rebuild or .env change
    docker compose logs -f pixel-dog      # one JSON timing record per turn
    curl localhost:8765/healthz           # {"status":"ok","pipeline":"echo",...}

The switchboard ignores this service (it only lists `vllm serve` commands), and Prometheus does not scrape it:
the orchestrator has no `/metrics` endpoint yet.
