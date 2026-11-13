← [README](../README.md)

# Switchboard

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

## Labels
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

## How it runs
The container runs `docker compose` against this repo, bind-mounted read-only at the same absolute path as on the host.
That keeps compose's config hashes identical to your shell's, so neither side recreates containers the other
started. After editing `apps/switchboard/switchboard.py`: `docker compose up -d --build switchboard` (which also
signs everyone out).

## Login
Standard library plus one QR library in the image, no extra container, nothing in `.env`. State (scrypt password
hash, TOTP secret) lives in the `switchboard-auth` volume at `/auth/auth.json`, mode 600.

- **First run**: the container log prints a setup token. The page asks for the token and a new password (six
  characters or more), then shows a QR code and the key for your authenticator app and finishes when you enter a
  code from it. Until that is done every request but `/health` is refused, and the token stops a stranger on the
  tailnet from claiming the page first.
- **Signing in**: password + six-digit code (RFC 6238; a code is accepted once, one step of clock skew either way).
  A random token goes in an `HttpOnly; Secure; SameSite=Strict` cookie; sessions end after 12 h idle or 7 days, and
  all of them on a container restart (`up -d --build` included).
- **Account** (link in the header): change the password, or replace the authenticator with a fresh QR; both need the
  current password and a current code.
- **Lost phone**: `docker compose run --rm switchboard --reset-auth`, then `docker compose restart switchboard`;
  the log prints a new setup token and the page starts over. Anyone who can run that has root on grace anyway.
- **Throttle**: token, password and code checks share one queue; from the third consecutive failure the delay
  doubles (capped at a minute) and twenty failures lock everything for fifteen minutes. Each failure is logged with
  the tailnet address `tailscale serve` forwards.
- **CSRF**: every POST must be same-origin (`Sec-Fetch-Site` / `Origin`), so a page you have open elsewhere cannot
  stop a model. The login, setup and account pages carry no scripts and load nothing from third parties.
