# h1monitor

**Watch HackerOne for scope changes and get them in Telegram, minutes after they land.**

New assets appear in a program's scope long before anyone writes about them. h1monitor polls
HackerOne, diffs each snapshot against the last, and pushes only what changed to your chat —
so a fresh target reaches you while it's still fresh.

Public programs need no API key. Add yours and it watches your private invites too.

```
🎯 X / xAI · 🔒 Private
🔗 hackerone.com/x

➕ Scope added
     URL: console.x.ai
✏️ Scope updated · out of scope
     AI_MODEL: Roster Agent
✏️ Scope changed
     URL: t.co
     Bounty eligible: no → yes
```

## What it watches

| Source | Needs a key? | Covers |
|---|---|---|
| **Public directory** | no | Every public program on the platform — ~449 programs, ~11.5k scopes |
| **Your private programs** | yes | The invite-only programs your API key can see |

Public refreshes every **30 min** by default (~18 paged queries, no key). Private refreshes every
**2 h**: scope details cost one API call per program and HackerOne rate-limits that endpoint
under bursts, so h1monitor walks them **one at a time, self-throttling** — backing off the
moment HackerOne pushes back. A full private sweep takes **~10–15 min** and never trips the
limiter.

> HackerOne has no webhooks for any of this, so h1monitor polls, snapshots, and diffs.

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

| Command | What it does |
|---|---|
| `/start` | Claim the bot — from then on it only answers you |
| `/setup <username> <token>` | Connect HackerOne for private programs. The message is **deleted the instant** the key is captured |
| `/config` | Toggle each alert type, and set both check intervals |
| `/status` | Programs and scopes watched, intervals, whether the API key is connected |
| `/help` | The list above |

Get your API credentials from [hackerone.com/settings/api_token](https://hackerone.com/settings/api_token/edit).
Skip `/setup` entirely if you only care about public programs.

The first check is a **silent baseline** — otherwise you'd get thousands of alerts for
programs that were already there. Real alerts start from the next check. The private sweep
begins the moment you send `/setup` — no restart, no waiting for the next tick — and fills in
gradually from there, so `/status` shows the count climbing for ~10–15 min.

## What you get alerted about

Scope added · scope removed · scope changed (severity, eligibility, instructions) · bounty
eligibility · new public program launched · program paused or resumed · policy text edited ·
private program gained or lost.

Every type is individually switchable in `/config`. **Skip paused programs** is on by default —
you still get the one "went paused" alert so you know why a program went quiet.

Each alert is tagged 🌐 Public or 🔒 Private, and an asset that turns up *out of scope* is
labelled as such, so you never chase a target that isn't one.

## Manage it

Installed with the one-liner (systemd user service, state in `~/h1-monitor`):

```bash
systemctl --user status h1monitor     # is it running?
journalctl --user -u h1monitor -f     # watch it work
systemctl --user restart h1monitor    # after editing .env
```

Docker (state lives in the `h1monitor` volume):

```bash
docker logs -f h1monitor
docker restart h1monitor
```

**Updating.** Re-run the install one-liner — it pulls and restarts in place. On Docker,
`docker pull ghcr.io/0xbartita/h1-monitor` then recreate the container. Compose users with the
bundled Watchtower need do nothing.

## Configuration

Everything is configurable from `/config` in chat. These env vars exist for pre-seeding, and
all are optional except the first — see [`.env.example`](.env.example).

| Var | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | **Required.** The bot cannot receive its own token |
| `TELEGRAM_OWNER_CHAT_ID` | Auto-captured on first `/start` if omitted |
| `H1_API_USERNAME` / `H1_API_TOKEN` | Seed HackerOne creds instead of using `/setup` |
| `H1MON_SECRET_KEY` | Fernet key for stored creds; auto-generated to `h1mon_secret.key` (0600) |
| `H1MON_DB_PATH` | SQLite path (default `./h1monitor.db`) |
| `H1_DIRECTORY_COOKIE` | Session cookie for the directory query, if anonymous access ever fails |

## Security

- **Owner-only.** After `/start` captures your chat, commands from anyone else are ignored.
- **Credentials encrypted at rest** with Fernet; the database and keyfile are `0600`.
- **The `/setup` message is deleted** the moment the key is read, and secrets are never logged.
- **Residual risk:** sending your key over Telegram means it transits their servers and sits in
  chat history until that deletion. Seed `H1_API_USERNAME` / `H1_API_TOKEN` via env instead if
  you want zero chat exposure.
- **One token, one copy.** Telegram lets a single instance poll a bot token, so a second
  machine needs its own bot — otherwise the two fight and one dies with
  `Conflict: terminated by other getUpdates request`.

## Caveats

- The public directory feed uses HackerOne's **undocumented internal GraphQL** — the same
  mechanism community tools rely on. It's less stable than the official REST API and may need
  maintenance if HackerOne changes it. Everything else rides the official API.
- The Hacker API exposes bounty **eligibility** and **max severity**, not bounty-table amounts,
  so `bounty_changed` reflects those rather than exact payouts.
- Each install keeps its own state — a fresh box starts from a clean baseline and needs
  `/start` and `/setup` again.

## Development

```bash
pip install -e '.[dev]'
pytest -q
```

135 tests, no network access required — the HackerOne and Telegram clients are stubbed.
