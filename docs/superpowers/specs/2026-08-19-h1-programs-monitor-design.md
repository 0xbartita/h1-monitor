# h1-programs-monitor — Design Spec

**Date:** 2026-08-19
**Status:** In review (planning)
**Author:** Hunter (with Claude)

## 1. Overview

A self-hosted Python daemon that monitors HackerOne programs for changes and pushes
the ones the operator has opted into to a Telegram chat. Two things are watched:

1. **Programs the operator's API key can access** (public + private they belong to) —
   scope additions/removals/modifications, bounty-eligibility/severity changes, program
   state/policy changes, and new private invites. Via the **official REST Hacker API**.
2. **The entire HackerOne public directory** — every newly launched public program
   platform-wide (whether or not the operator is invited), announced with its launch date.
   Via HackerOne's **directory GraphQL** endpoint.

HackerOne has **no webhooks** for any of this, so the tool **polls** on an interval,
**snapshots** state, **diffs** against the previous snapshot, and delivers changes to a
single Telegram chat with **per-category labels** (🆕 New Program, 🎯 Scope Change,
⏸ Program Status, 💰 Bounty, …). The operator controls **what they receive** live via a
Telegram `/config` command, and supplies their **HackerOne API credentials** through the
bot itself.

Single-operator / personal deployment. Secrets stay on the operator's host.

## 2. Goals & Non-Goals

### Goals
- Detect and report, per program, the change types in §5.
- Cover **both public and private** programs the operator's API key can see (source 1).
- Detect **new public programs launched platform-wide** across the full HackerOne
  directory, announced with launch date (source 2).
- Let the operator supply their **HackerOne API identifier + token via the Telegram bot**.
- Let the operator **toggle which change types** (and other preferences) they receive,
  live, via `/config`.
- **Exclude paused programs** from notifications by default (toggleable).
- Deliver to a **single Telegram chat**, each message tagged with a **category label**.
- Run as a single long-running daemon (poll loops + Telegram long-polling), runnable under
  systemd or Docker. Configurable poll interval (default 30 min).
- Protect secrets: encrypted-at-rest credentials, owner-only bot, auto-deletion of the
  message containing a submitted API key.

### Non-Goals (v1)
- Multi-user / multi-tenant service (one operator only).
- Detecting **dollar bounty-table amounts** — the API does not expose per-severity bounty
  amounts. We detect bounty *eligibility* and *max severity* only. Dollar-amount scraping
  is out of scope (fragile).
- **new-reports / hacktivity feed** (newly disclosed public reports) — deferred; not v1.
- Directory-wide *scope* monitoring for programs the operator is **not** a member of
  (the directory GraphQL can return scopes, but polling scopes for the entire directory is
  out of scope for v1 — see §14).
- A public web UI or webhook receiver; any write operations against HackerOne.

## 3. Users & Deployment

- **One operator.** Runs the daemon on a host they control (VPS, home server, container).
- Interaction is entirely through their private Telegram chat with the bot.
- The bot responds **only** to the configured owner chat ID; all other chats are ignored.

## 4. Data sources — verified facts

### 4a. Official REST Hacker API (source 1)
Base URL: `https://api.hackerone.com/v1/` — Auth: **HTTP Basic** (username = API identifier,
password = API token).

Endpoints used:
- `GET /hackers/programs` — paginated programs **visible to the key** (public + private the
  operator belongs to; **not** the full public directory). Attributes: `handle`, `name`,
  `currency`, `submission_state`, `policy`, `offers_bounties`.
- `GET /hackers/programs/{handle}` — single program details.
- `GET /hackers/programs/{handle}/structured_scopes` — paginated in-scope assets. Per-scope:
  `asset_type`, `asset_identifier`, `eligible_for_bounty` (bool), `eligible_for_submission`
  (bool), `instruction`, `max_severity` (critical/high/medium/low/none),
  `confidentiality_requirement`, `integrity_requirement`, `availability_requirement`,
  `created_at`, `updated_at`, `reference`.
- `GET /hackers/programs/{handle}/scope_exclusions` — paginated out-of-scope assets.

Pagination follows JSON:API `links.next`. `429` honored via `Retry-After`; `5xx`/network via
exponential backoff.

### 4b. HackerOne public directory — GraphQL (source 2)
Endpoint: `https://hackerone.com/graphql`. This is HackerOne's **unofficial internal API**
that powers `hackerone.com/directory/programs` — it is what community tools (e.g.
[bounty-targets](https://github.com/arkadiyt/bounty-targets/blob/main/lib/bounty-targets/hackerone.rb))
use to enumerate the full directory.

- **Query:** `teams(first: N, after: $cursor, secure_order_by: {started_accepting_at: {_direction: DESC}}, where: {...})`,
  paginated by cursor.
- **Fields per team:** `handle`, `id`, `name`, `offers_bounties`, `submission_state`,
  `started_accepting_at` (the "launched on" date), `url`, `website`, `allows_bounty_splitting`.
- **Auth:** a **session cookie + CSRF token** bootstrapped by GET `https://hackerone.com/directory/programs`
  (sent as `Cookie` + `X-Csrf-Token` headers). **No API token / Basic auth.** An anonymous
  session is expected to suffice for the public directory; this is confirmed via spike (§14).

**Caveat (accepted):** this endpoint is undocumented and may change without notice; the
directory feed carries maintenance risk that the official REST source does not.

## 5. Change types detected

All individually toggleable via `/config`; all default **on**. Column "Src" = data source.

| Key | Src | Change type | Source of truth |
|-----|-----|-------------|-----------------|
| `new_public_program` | 2 | **New public program launched platform-wide** (full directory) | `teams` directory set diff |
| `scope_added` | 1 | An in-scope asset appears | `structured_scopes` set diff |
| `scope_removed` | 1 | An in-scope asset disappears / moves to exclusions | `structured_scopes` + `scope_exclusions` diff |
| `scope_modified` | 1 | An existing asset's fields change (`max_severity`, `instruction`, `eligible_for_submission`, CIA) | per-field diff + `updated_at` |
| `bounty_changed` | 1 | `offers_bounties` (program) or `eligible_for_bounty` / `max_severity` (scope) changes | program + scope diff |
| `program_added` | 1 | **A program becomes accessible to you** (new private invite / your list grew) | `/hackers/programs` set diff |
| `program_removed` | 1 | A previously accessible program disappears | `/hackers/programs` set diff |
| `program_state` | 1 | `submission_state` or `policy` text changes | program object diff |

Note the two distinct "new program" signals: `new_public_program` (platform-wide, source 2)
vs `program_added` (you personally gained access, source 1).

`max_severity` changes surface under both `scope_modified` and `bounty_changed`; emitted once,
tagged with all applicable types (delivered if **any** tagged type is enabled).

## 6. Architecture

Single process, single asyncio event loop, three cooperating tasks:

1. **API poller** (`poller.py`) — every N minutes: REST fetch → normalize → diff → filter →
   group → notify → persist. (Source 1.)
2. **Directory poller** (`directory_poller.py`) — every N minutes: GraphQL directory fetch →
   diff known-handle set → emit `new_public_program` → filter → notify → persist. (Source 2.)
3. **Bot listener** (`bot.py`) — Telegram long-polling (no public URL/webhook). Handles
   commands, mutates preferences/credentials. Changes take effect next cycle.

Telegram library: **`python-telegram-bot`** (async, v20+) for inline-keyboard + callback
handling (`/config`), update-offset management, and Telegram-side retry/backoff.

### Module layout
- `models.py` — dataclasses: `Program`, `Scope`, `DirectoryProgram`, `Change`,
  `Preferences`, `Snapshot`.
- `config.py` — load/validate bootstrap env; manage Fernet key (env `H1MON_SECRET_KEY` or
  auto-generated `0600` keyfile).
- `h1_client.py` — REST Hacker API client (source 1): paginated programs / structured_scopes /
  scope_exclusions; Basic auth; `429`/`5xx` backoff.
- `directory_client.py` — directory GraphQL client (source 2): session+CSRF bootstrap,
  paginated `teams` query, backoff, resilient to schema drift (defensive field access).
- `store.py` — SQLite (WAL): API snapshot, directory snapshot (known handles + metadata),
  preferences, encrypted H1 credentials, poll metadata. Writes serialized.
- `differ.py` — **pure** functions: `diff_api(prev, curr) -> [Change]` and
  `diff_directory(prev_handles, curr) -> [Change]`. No I/O. TDD-covered core.
- `notifier.py` — format `Change`s into category-labeled Telegram messages, apply the
  preference filter (§7), send via the bot.
- `bot.py` — command/callback handlers; owner-only authorization; `/config` UI.
- `poller.py` / `directory_poller.py` — the interval loops.
- `main.py` — wiring; run all tasks concurrently; graceful shutdown.

### Data flow (per cycle)
```
# Source 1 (API poller)
h1_client.fetch_all() -> Snapshot ; store.load_previous_api()
differ.diff_api(prev, curr) -> [Change]
# Source 2 (directory poller)
directory_client.fetch_all() -> [DirectoryProgram] ; store.load_known_handles()
differ.diff_directory(prev_handles, curr) -> [Change(new_public_program)]
# shared
filter(changes, preferences)   # §7: enabled types, allow/deny, exclude_paused
group_by_category_and_program(changes)
notifier.send(grouped)         # single chat, category-labeled
store.save(...) ; store.record_poll(now)
```

## 7. Preferences & filtering

Preferences live in SQLite, edited live via `/config`. Defaults:

| Preference | Default | Meaning |
|------------|---------|---------|
| change-type toggles (`new_public_program`, `scope_added`, `scope_removed`, `scope_modified`, `bounty_changed`, `program_added`, `program_removed`, `program_state`) | all **on** | which change types are delivered |
| `exclude_paused` | **on** | suppress changes for programs whose current `submission_state` is `paused` |
| `poll_interval_minutes` | 30 | poll cadence (both pollers) |
| `allowlist` (program handles) | empty (= all) | if non-empty, only these handles are monitored |
| `denylist` (program handles) | empty | handles never monitored |

Filter order: program allow/deny → `exclude_paused` → change-type toggles.

`allowlist`/`denylist` apply to **source 1** (per-program monitoring). `new_public_program`
(source 2) is directory-wide by nature; it is gated only by its own toggle and `exclude_paused`
(a program launched directly into a paused state is suppressed).

`exclude_paused` semantics: when a program's **current** `submission_state == "paused"`, its
changes are suppressed — **except** the single `program_state` transition reporting it *became*
paused (so the operator knows why alerts went quiet). Silence while paused; resume on reopen.

## 8. Telegram bot: commands, delivery & message format

### Commands (all owner-only; non-owner updates ignored silently)
- `/start` — greet; auto-capture owner chat ID if unset; show setup status (creds configured?).
- `/setup` — guided credential entry (identifier, then token; each message auto-deleted).
- `/setapikey <identifier> <token>` — one-shot; command message **auto-deleted** after capture.
- `/config` — inline-keyboard toggles for every change type + `exclude_paused`, plus entry
  points for poll interval and allow/deny lists. Tapping re-renders via callback query.
- `/programs` — summary of monitored (source-1) programs, reflecting allow/deny + paused.
- `/status` — last poll time (per source), next-poll ETA, programs watched, creds set?, interval.
- `/help` — command reference.

### Delivery
Single Telegram chat. Every message begins with a **category label/emoji** header:
`🆕 New Program`, `🎯 Scope Change`, `💰 Bounty`, `⏸ Program Status`, `➕ New Access`,
`➖ Program Removed`. Changes are grouped per program per cycle; long messages are split to
respect Telegram's size limit.

### `new_public_program` message format (matches the reference Discord bot)
- Title: **`🆕 New Program: {name}`**
- Body: `{name} launched on {started_accepting_at:%Y-%m-%d} as a {kind}.` where `{kind}` is
  `Bug bounty program` when `offers_bounties` is true, else `vulnerability disclosure program`.
  When `started_accepting_at` is missing, use `{name} was newly observed as a {kind}.`
- Link: `Open program → https://hackerone.com/{handle}`

## 9. Security

- **Owner-only bot:** every handler checks the update's chat ID vs the stored owner chat ID.
- **Credential submission:** the message containing an API key is deleted via `deleteMessage`
  immediately after capture.
- **Encryption at rest:** H1 credentials stored Fernet-encrypted. Key = env `H1MON_SECRET_KEY`
  if set, else auto-generated to a `0600` keyfile beside the DB on first run.
- **File permissions:** SQLite DB and keyfile `chmod 0600`.
- **No secret logging:** credentials and bot token never written to logs.
- **`.gitignore`:** `.env`, SQLite DB, keyfile excluded from version control.
- **Directory GraphQL:** uses an anonymous session cookie + CSRF (no operator secret). Traffic
  respects a polite rate and identifies a stable User-Agent; polling cadence matches the
  configured interval. (Undocumented endpoint — see §4b caveat and §11.)
- **Residual risk (accepted):** submitting the API key via Telegram means the plaintext briefly
  transits Telegram servers and exists in chat history until auto-deleted — an explicit,
  operator-approved tradeoff. Env-seeding is available for operators who prefer zero chat exposure.

## 10. Configuration & secrets

- **Env (bootstrap):**
  - `TELEGRAM_BOT_TOKEN` — **required** (bot cannot receive its own token).
  - `TELEGRAM_OWNER_CHAT_ID` — optional; auto-captured on first `/start`.
  - `H1MON_SECRET_KEY` — optional Fernet key; auto-generated to keyfile if omitted.
  - `H1_API_USERNAME` / `H1_API_TOKEN` — optional convenience seed for H1 creds.
  - `H1_DIRECTORY_COOKIE` — optional override to supply a session cookie for the directory
    GraphQL, if the anonymous bootstrap proves insufficient (see §14).
  - `H1MON_DB_PATH` — optional; default `./h1monitor.db`.
- **Ships with:** `.env.example`, `README.md`, `systemd` unit, `Dockerfile`.
- **Bot-managed (encrypted where noted):** H1 identifier/token (encrypted), owner chat ID,
  all preferences (§7).

## 11. Error handling & edge cases

- **First run / empty snapshot (both sources):** establish baseline silently. The directory
  baseline is critical — do **not** announce the entire existing directory (thousands of
  programs) as new. Send one "Baseline established — watching N accessible programs, tracking
  M directory programs" message.
- **No H1 creds yet:** API poller idle and nudges the operator to `/setup` **once**; the
  **directory poller still runs** (it needs no API creds), so `new_public_program` alerts work
  even before creds are set. Bot stays responsive.
- **Per-program fetch failure (source 1):** retain that program's previous snapshot for the
  cycle (never emit false `scope_removed`/`program_removed` from a transient error).
- **Directory fetch failure (source 2):** retain previous known-handle set; log + alert the
  operator **once** (deduped) if it persists; never treat a failed fetch as "all removed."
- **H1 `429`:** honor `Retry-After`; `5xx`/network: capped exponential backoff. Persistent
  full-fetch failure skips that source's cycle and alerts once.
- **Telegram send failure:** retry with backoff; log on give-up. Long messages split.
- **Concurrency:** SQLite WAL; bot vs poller writes serialized via a single writer/lock.
- **Graceful shutdown:** on SIGINT/SIGTERM, finish in-flight writes and stop cleanly.

## 12. Testing strategy

- **`differ.py`** — primary TDD target: pure functions; exhaustive tests for every change type
  incl. `new_public_program`, first-run baselines (API **and** directory), no-op cycles, and
  `max_severity` dual-tagging.
- **Filtering (§7)** — allow/deny, `exclude_paused` incl. the "became paused" exception,
  per-type toggles, directory-wide gating of `new_public_program`.
- **`store.py`** — round-trip API snapshot / directory handles / preferences / credentials;
  encryption on/off; WAL write serialization.
- **`h1_client.py`** — mocked HTTP: pagination, `429`/`5xx`/backoff, auth header.
- **`directory_client.py`** — mocked GraphQL: session/CSRF bootstrap, cursor pagination,
  defensive parsing of missing fields, backoff.
- **`notifier.py`** — category labeling, `new_public_program` format (bounty vs VDP, with/without
  launch date), grouping, splitting (Telegram mocked).
- **`config.py`** — env validation, keyfile generation/permissions.
- **Manual smoke test** — real keys against a small allowlist; verify directory feed on first run
  establishes a silent baseline.

## 13. Deliverables

- Python package (`h1monitor/`) with the modules in §6.
- `pyproject.toml` (deps: `python-telegram-bot`, `httpx`, `cryptography`).
- `.env.example`, `README.md` (setup + Telegram walkthrough), `systemd` unit, `Dockerfile`.
- Test suite (`pytest`).

## 14. Open questions / future work

- **Spike (implementation):** confirm the directory GraphQL works with an **anonymous**
  session+CSRF, or whether a logged-in session cookie (`H1_DIRECTORY_COOKIE`) is required;
  confirm the exact `teams` query shape and `where` filter for "public, accepting submissions."
- Confirm `/hackers/programs` membership semantics via live spike (see §4a).
- Future (not v1): directory-wide *scope* monitoring for non-member public programs (source 2
  scopes), new-reports/hacktivity category, Telegram forum-topics delivery, per-program mute,
  daily digest mode, optional dollar-bounty scraping behind an explicit flag, multi-owner.
