# h1monitor

A self-hosted daemon that watches **HackerOne** for changes and pushes the ones you care
about to a **Telegram** chat — with live, in-chat configuration.

It watches two sources:

1. **All public programs** — the entire HackerOne public directory, pulled in bulk with
   their scopes via the directory GraphQL (**no API key**, ~15 requests). Detects new
   programs launching platform-wide (announced with launch dates), plus scope, bounty, and
   state changes across every public program.
2. **Your private programs** — the invite-only (`soft_launched`) programs your API key can
   see. **All of them are monitored automatically**, both at the program level (new invite,
   paused, bounty on/off) and at the **scope level** (scope added/removed/changed) — nothing
   to opt into. Scope details need one API call per program, and HackerOne rate-limits that
   endpoint when it's hit in bursts — so h1monitor scans them **one at a time, gently
   self-throttled** (backs off the moment HackerOne pushes back, eases up when clear). A full
   sweep of a few hundred private programs takes **~10–15 min** and never trips the limiter;
   it runs quietly in the background.

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

The **private** baseline scans every private program's scopes one at a time, so it can take
**~10–15 min** to finish the first time (and on each later refresh). `/programs` shows the
private scope count climbing to its full total once the sweep completes.

## Telegram commands

| Command | What it does |
|---|---|
| `/start` | Greet; capture owner chat; show setup status |
| `/setup` | Explain how to submit credentials |
| `/setapikey <id> <token>` | Set H1 credentials (message auto-deleted) |
| `/config` | Inline toggles for every change type + `exclude_paused` |
| `/programs` | Counts of public + private programs and scopes monitored |
| `/status` | Intervals, whether creds are set, `exclude_paused` |
| `/help` | Command list |

**Public vs private scope monitoring:** every *public* program's scopes are always
monitored (they come free with the bulk directory query), and every *private* program's
scopes are always monitored too — no setup, no opt-in. Public
refreshes every `poll_interval_minutes` (default 30); private refreshes every
`private_interval_minutes` (default 120), since it uses your rate-limited API key.

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

**systemd (system service):** copy the project to `/opt/h1monitor`, put your `.env` there, then:
```bash
sudo cp systemd/h1monitor.service /etc/systemd/system/
sudo systemctl enable --now h1monitor
```

**systemd (user service — no sudo, recommended for a personal box):** runs 24/7,
survives closing the terminal, logging out, and reboot, and auto-restarts on crash. Create
`~/.config/systemd/user/h1monitor.service` with `WorkingDirectory` and `ExecStart` pointing
at your checkout and its venv (see `systemd/h1monitor.service` for the fields), then:
```bash
loginctl enable-linger "$USER"   # keeps it running while you're logged out
systemctl --user daemon-reload
systemctl --user enable --now h1monitor
```
Control it: `systemctl --user {status,restart,stop} h1monitor`. Watch it:
`tail -f h1monitor.log` (or `journalctl --user -u h1monitor -f`).

## Caveats

- The **directory feed** uses HackerOne's *undocumented* internal directory GraphQL — the
  same mechanism community tools use. It's less stable than the official REST API and may
  need occasional maintenance if HackerOne changes it. Everything else rides the official API.
- The Hacker API exposes bounty **eligibility** and **max severity**, not dollar bounty-table
  amounts — so `bounty_changed` reflects eligibility/severity, not exact payouts.
- HackerOne's per-program scope endpoint enforces a hidden burst rate-limit, so private
  scope-scanning is deliberately **sequential and slow** (~10–15 min per full sweep). This is
  by design — it's the price of never getting rate-limited. Private programs therefore refresh
  less often than public ones (`private_interval_minutes`, default 120).

## Development

```bash
pip install -e '.[dev]'
pytest -q
```

See the design spec and implementation plan under `docs/superpowers/`.
