# h1monitor

Watch **HackerOne** for new programs and scope / bounty / status changes, delivered to your
**Telegram**. Public programs need no API key; add yours to watch private ones too. Set up
entirely in-chat.

```
🎯 X / xAI · 🔒 Private
➕ Scope added                    URL: console.x.ai
✏️ Scope updated · out of scope  AI_MODEL: Roster Agent
✏️ Scope changed                 URL: t.co — Bounty eligible: no → yes
```

## Install

**One command** — builds a venv, installs, asks for your bot token, runs it 24/7 via systemd
(needs **Python 3.11+** and **git**):

```bash
curl -fsSL https://raw.githubusercontent.com/0xbartita/h1-monitor/main/install.sh | bash
```

**Or Docker** — no Python, no clone:

```bash
docker run -d --name h1monitor --restart unless-stopped \
  -e TELEGRAM_BOT_TOKEN=<your-token> -v h1monitor:/data \
  ghcr.io/0xbartita/h1-monitor
```

*(or drop [`docker-compose.yml`](docker-compose.yml) in a folder with a `.env` and run
`docker compose up -d` — that setup **auto-updates** itself as new versions ship)*

No token yet? Message [@BotFather](https://t.me/BotFather) → `/newbot`.

## Then, in Telegram

- **`/start`** — claim the bot (only you can command it afterward)
- **`/setup <username> <token>`** — connect HackerOne for private programs
  ([get key](https://hackerone.com/settings/api_token/edit); the message is auto-deleted).
  Skip it for public-only.
- **`/config`** — choose alerts & check intervals   ·   **`/status`** — what's watched

The first check is a silent baseline; real alerts start from the next one. Private programs
fill in gradually — their scopes are fetched one at a time to stay under HackerOne's rate
limit, so `/status` shows the count climbing for ~10–15 min.

## Manage it

Installed with the one-liner (systemd user service, state in `~/h1-monitor`):

```bash
systemctl --user status h1monitor     # is it running?
journalctl --user -u h1monitor -f     # watch it work
systemctl --user restart h1monitor    # after editing .env
```

Docker (state in the `h1monitor` volume):

```bash
docker logs -f h1monitor
docker restart h1monitor
```

**Updating.** Re-run the install one-liner — it pulls and restarts in place. On Docker,
`docker pull ghcr.io/0xbartita/h1-monitor` then recreate the container; compose users with the
bundled Watchtower need do nothing, new versions arrive on their own.

## Notes

- Owner-only bot; credentials encrypted at rest and never logged.
- **One token, one copy.** Telegram lets a single instance poll a bot token, so a second
  machine needs its own bot from [@BotFather](https://t.me/BotFather) — otherwise the two
  fight and one dies with `Conflict: terminated by other getUpdates request`.
- Each install keeps its own state, so a fresh box starts from a clean baseline and needs
  `/start` and `/setup` again.
- Manual install, Docker, and all settings: see [`install.sh`](install.sh) and
  [`.env.example`](.env.example).
