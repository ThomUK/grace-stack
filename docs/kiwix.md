← [README](../README.md)

# kiwix

An offline copy of Wikipedia, served by [kiwix-serve](https://github.com/kiwix/kiwix-tools) as the `kiwix`
container in `apps/kiwix/compose.yaml`. Its one client is the wikipedia tool in [Pixel Dog](pixel-dog.md). The
master copy of the module is in the private pixel-dog repo (`deploy/grace-stack/apps/kiwix/compose.yaml`); if it
changes there, copy it here by hand and commit it.

This page is an outline.

## What the module needs

| in `.env` | meaning |
|---|---|
| `KIWIX_DATA_DIR` | folder of `.zim` files, mounted read-only at `/data`. Required: with it unset, `docker compose` refuses to run for the whole stack |

The image is the official `ghcr.io/kiwix/kiwix-serve`, pinned to 3.8.2 (the official images are on ghcr.io
only). CPU only, no GPU share, no profile: it starts with the rest of the stack.

## Port and exposure

kiwix listens on 8080 inside its container, and the module publishes **no host port**: 8080 on grace belongs to
the DGX dashboard. Pixel Dog reaches it at `http://kiwix:8080` on the stack's network, and nothing outside the
stack can. To browse it by hand, add `ports: ["8090:8080"]` to the module or use `tailscale serve`.

## ZIM files

The container serves every `.zim` in the folder (`*.zim`). Files come from library.kiwix.org or
download.kiwix.org; use the torrent or a resumable download (`wget -c`).

| file | use | size |
|---|---|---|
| `wikipedia_en_all_mini_<date>.zim` | default book: the lead section of every English article | about 14 GB |
| `wikipedia_en-simple_all_nopic_<date>.zim` | Simple English, for kids' profiles | about 1 GB |

- To update: download the new file beside the old one, restart the container, delete the old file.
- kiwix-serve refuses to start if any file in the folder is not a valid ZIM (an unfinished download, for
  instance), so keep partial downloads elsewhere until they are complete.
- To do: how to check a download against its published checksum.

## Day to day

    docker compose up -d kiwix            # (re)create after a .env or compose change
    docker compose restart kiwix          # pick up a new .zim
    docker compose logs kiwix             # lists the files it serves at start

Prometheus does not scrape it and the switchboard ignores it.
