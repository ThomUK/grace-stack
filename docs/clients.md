← [README](../README.md)

# Clients

## Open WebUI
Admin → Settings → Connections: one `http://<container>:8000/v1` per model service (container port, not host
mapping; key anything), all eleven seeded by `OPENAI_API_BASE_URLS` in `apps/compose.yml`. Siblings share a served
name but only one is up, so each model is listed once. Ollama API off. Saved connections in the volume override
the env seeds, so add new services there by hand.
Audio: STT Whisper (Local), TTS Web API. The mic needs HTTPS, which is why Open WebUI is reached through
`tailscale serve` on the tailnet name rather than by IP.

### Backup / restore
    docker run --rm -v open-webui:/d -v $PWD:/b alpine tar czf /b/open-webui-backup-$(date +%F).tgz -C /d .
    docker run --rm -v open-webui:/d -v $PWD:/b alpine sh -c 'cd /d && tar xzf /b/open-webui-backup-<date>.tgz'

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
`maxInputTokens` 262144, no reasoning-effort keys (the model has no thinking mode). `supportsReasoningEffort` adds
a Thinking Effort submenu (the > arrow in the model picker); whether the model honours it is unverified —
`--default-chat-template-kwargs '{"enable_thinking": false}'` on the vLLM command is the lever that certainly
works. First turn on a fresh workspace is a 20–40 s prefill.

## Anything else that speaks OpenAI
Base URL `https://<machine>.<tailnet>.ts.net:<port>/v1`, model id = the served name, any API key (there is none
yet; see [security.md](security.md)).
