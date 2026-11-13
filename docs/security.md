← [README](../README.md)

# Security

Nothing here is meant to face the internet (Funnel stays off). Everything below is reachable by any device on
the tailnet, so each service's own login is the second layer:

| exposed | auth |
|---|---|
| Grafana :3000, Open WebUI :12000 | their own user accounts |
| DGX Dashboard :11000 | NVIDIA's own login |
| switchboard :8080 | password + TOTP (see [switchboard.md](switchboard.md#login)). It holds the Docker socket, which is root-equivalent on the host |
| vLLM APIs :8000–:8006 | **none yet** (`--api-key` is the fix; pending) |

- cadvisor is `privileged`; cadvisor and node-exporter mount the host filesystem read-only.
- If your tailnet devices are *tagged* (common with auth keys), `tailscale serve` adds no user identity headers, so
  identity-based tricks (Grafana's auth proxy and the like) have nothing to read. Restrict by device instead, in the
  admin console's Access controls. A starting point, which only restricts once the default `"*" → "*"` rule is
  removed; give this machine its own tag (`tag:grace` below) rather than addressing it by IP:

      "grants": [
        // admin pages: your own laptop and phone only
        { "src": ["tag:laptop", "tag:phone"], "dst": ["tag:grace"], "ip": ["tcp:8080", "tcp:11000"] },
        // Grafana, Open WebUI and the model APIs: those two plus any machine that should call the models
        { "src": ["tag:laptop", "tag:phone", "tag:agents"], "dst": ["tag:grace"],
          "ip": ["tcp:3000", "tcp:12000", "tcp:8000-8006"] }
        // plus whatever else the tailnet needs (SSH to grace, your own devices to each other)
      ]

- Grafana's auth proxy could trust the `Tailscale-User-Login` header that `tailscale serve` adds for user-owned
  devices, giving passwordless tailnet login; only safe while Grafana stays on 127.0.0.1, and useless for tagged
  devices.
