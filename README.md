# h1monitor

**Watch HackerOne for scope changes and get them in Telegram, minutes after they land.**

New assets appear in a program's scope long before anyone writes about them. h1monitor polls
HackerOne, diffs each snapshot against the last, and pushes only what changed to your chat —
so a fresh target reaches you while it's still fresh.

It runs 24/7 on your own machine or server. Install it once and leave it — it keeps
checking on its own, starts itself again if it crashes, and comes back after a reboot.

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
| **Public directory** | no | Every public program on the platform |
| **Your private programs** | yes | The invite-only programs your API key can see |

Public refreshes every **30 min** by default, private every **2 h**. A full private sweep takes
**~10–15 min**.

## Install

**One command** — installs it, asks for your bot token, and starts it running 24/7
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

No token yet? Message [@BotFather](https://t.me/BotFather) → `/newbot`.

📣 Join [@h1_monitor](https://t.me/h1_monitor) to hear when a new version ships.

## Then, in Telegram

| Command | What it does |
|---|---|
| `/start <code>` | Claim the bot — the installer prints your code. From then on it only answers you |
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

Every one is its own switch in `/config`.

Each alert is tagged 🌐 Public or 🔒 Private, and an asset that turns up *out of scope* is
labelled as such, so you never chase a target that isn't one.

## Manage it

Installed with the one-liner — everything lives in `~/h1-monitor`:

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

## Update to a new version

The bot tells you on `/status` when a newer version is out, and every release is announced
in [@h1_monitor](https://t.me/h1_monitor). The steps are on the release page.

## Stop it

Stops the checks; everything is kept, and starting it again picks up where it left off.

```bash
systemctl --user stop h1monitor       # one-liner install
docker stop h1monitor                 # Docker
```

## Uninstall

**This deletes your bot token, your HackerOne key, and the snapshot history** — a fresh install
starts from a new silent baseline.

One-liner install:

```bash
systemctl --user disable --now h1monitor
rm -f ~/.config/systemd/user/h1monitor.service
systemctl --user daemon-reload
rm -rf ~/h1-monitor
```

Docker:

```bash
docker rm -f h1monitor
docker volume rm h1monitor
docker rmi ghcr.io/0xbartita/h1-monitor
```
