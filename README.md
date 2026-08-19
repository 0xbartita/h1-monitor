# h1monitor

A self-hosted daemon that watches **HackerOne** for changes and pushes the ones you care
about to a **Telegram** chat — with live, in-chat configuration.

It watches two things:

1. **Programs your API key can access** (public + private you belong to) — scope
   additions/removals/modifications, bounty-eligibility & max-severity changes, program
   state/policy changes, and new private invites. *(Official REST Hacker API.)*
2. **The entire HackerOne public directory** — every newly launched public program
   platform-wide, announced with its launch date, whether or not you're invited.
   *(HackerOne directory GraphQL.)*

You choose what you receive live via a Telegram `/config` command, and you supply your
HackerOne API key **through the bot**.

> HackerOne has no webhooks for this, so h1monitor **polls** on an interval (default 30 min),
> snapshots state, diffs against the last snapshot, and delivers the changes.

## Quickstart

1. **Create a Telegram bot:** message [@BotFather](https://t.me/BotFather), send `/newbot`,
   follow the prompts, and copy the bot token it gives you.
2. **Install & run:**
   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -e .
   python -m h1monitor
   ```
   The **first time** it runs, it asks you to paste your bot token, saves it to a local
   `.env` file for you, and starts. You're never asked again. (No files to copy or edit by
   hand — though you *can* pre-set everything in `.env` if you prefer; see
   [`.env.example`](.env.example) for the full list of options.)
3. **In Telegram**, DM your bot:
   - `/start` — the bot captures your chat as the owner (only you can command it afterward).
   - `/setapikey <identifier> <token>` — set your HackerOne API credentials. **Your message
     is deleted immediately** after capture, and the credentials are stored encrypted.
     (Get the identifier/token from your HackerOne **API Token** settings page.)
   - `/config` — tap to toggle exactly which change types you receive.

The **directory feed needs no API key**, so new-public-program alerts start working even
before you set credentials.

> `.env.example` is just reference documentation of every available setting — handy when
> this repo goes on GitHub. You don't need to touch it; the tool writes your `.env` for you.

### First run is quiet on purpose
On the very first poll, h1monitor establishes a **silent baseline** (so you aren't spammed
with thousands of existing programs). You'll get one "Baseline established…" message per
source. Real change alerts begin from the next poll onward.

## Telegram commands

| Command | What it does |
|---|---|
| `/start` | Greet; capture owner chat; show setup status |
| `/setup` | Explain how to submit credentials |
| `/setapikey <id> <token>` | Set H1 credentials (message auto-deleted) |
| `/config` | Inline toggles for every change type + `exclude_paused` |
| `/programs` | Count of accessible programs being monitored |
| `/status` | Interval, whether creds are set, `exclude_paused` |
| `/help` | Command list |

## Change types (all default on, toggle in `/config`)

`new_public_program`, `scope_added`, `scope_removed`, `scope_modified`, `bounty_changed`,
`program_added` (you gained access), `program_removed`, `program_state`.

`exclude_paused` (default **on**) suppresses changes for programs currently `paused` — you
still get the single "went paused" message so you know why it went quiet.

## Configuration (environment)

| Var | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **yes** | The bot cannot receive its own token |
| `TELEGRAM_OWNER_CHAT_ID` | no | Auto-captured on first `/start` if omitted |
| `H1MON_SECRET_KEY` | no | Fernet key for encrypting stored creds; auto-generated to `./h1mon_secret.key` (0600) if omitted |
| `H1_API_USERNAME` / `H1_API_TOKEN` | no | Seed H1 creds via env instead of the bot |
| `H1_DIRECTORY_COOKIE` | no | Session cookie for the directory GraphQL, if anonymous access fails |
| `H1MON_DB_PATH` | no | SQLite path (default `./h1monitor.db`) |

## Security model

- **Owner-only bot** — after `/start` captures your chat, commands from any other chat are
  ignored.
- **Credentials encrypted at rest** (Fernet); DB and keyfile are `chmod 0600`.
- **The `/setapikey` message is deleted** the instant the key is captured.
- Secrets are never logged. `.env`, the DB, and the keyfile are git-ignored.
- **Residual risk:** submitting your key via Telegram means it briefly transits Telegram's
  servers and lives in chat history until auto-deletion. Prefer `H1_API_USERNAME` /
  `H1_API_TOKEN` env-seeding if you want zero chat exposure.

## Deployment

**Docker:**
```bash
docker build -t h1monitor .
docker run -d --env-file .env -v "$PWD/state:/app" --name h1monitor h1monitor
```

**systemd:** copy the project to `/opt/h1monitor`, put your `.env` there, then:
```bash
sudo cp systemd/h1monitor.service /etc/systemd/system/
sudo systemctl enable --now h1monitor
```

## Caveats

- The **directory feed** uses HackerOne's *undocumented* internal directory GraphQL — the
  same mechanism community tools use. It's less stable than the official REST API and may
  need occasional maintenance if HackerOne changes it. Everything else rides the official API.
- The Hacker API exposes bounty **eligibility** and **max severity**, not dollar bounty-table
  amounts — so `bounty_changed` reflects eligibility/severity, not exact payouts.

## Development

```bash
pip install -e '.[dev]'
pytest -q
```

See the design spec and implementation plan under `docs/superpowers/`.
