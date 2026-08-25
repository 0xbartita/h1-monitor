#!/usr/bin/env bash
#
# h1monitor one-shot installer.
#
#   curl -fsSL https://raw.githubusercontent.com/0xbartita/h1-monitor/main/install.sh | bash
#
# or, after cloning the repo:
#
#   ./install.sh
#
# It clones the repo (if needed), builds a virtualenv, installs h1monitor, asks
# once for your Telegram bot token, and — where systemd is available — sets it up
# to run 24/7 as a user service. Re-running it updates an existing install.
#
# Overridable via env vars: H1MON_REPO, H1MON_DIR, PYTHON.

set -euo pipefail

REPO_URL="${H1MON_REPO:-https://github.com/0xbartita/h1-monitor.git}"
INSTALL_DIR="${H1MON_DIR:-$HOME/h1-monitor}"
PYTHON="${PYTHON:-python3}"

# Set once we know the bot was already configured here, so a re-run reports
# an upgrade instead of replaying first-time setup instructions.
UPGRADE=0
OLD_VERSION=""

read_version() {
    sed -n 's/^__version__ = "\(.*\)"/\1/p' "$1/h1monitor/__init__.py" 2>/dev/null | head -1
}

info() { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# Read one line from the real terminal, even when the script is piped through
# `curl | bash` (in which case stdin is the script, not the keyboard).
prompt_tty() {
    local val=""
    [ -r /dev/tty ] || die "No terminal available for input. Re-run interactively, or preset TELEGRAM_BOT_TOKEN in $INSTALL_DIR/.env"
    printf '%s' "$1" > /dev/tty
    # Deliberately echoed. A hidden prompt gives no sign the paste landed, and a
    # half-pasted token looks exactly like a good one until the service fails to
    # start. The trade is that the token stays in scrollback -- clear it with
    # `clear` afterwards, and crop it out of any screenshot.
    IFS= read -r val < /dev/tty
    printf '%s' "$val"
}

# --- 1. prerequisites -------------------------------------------------------
command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found — install Python 3.11+ and re-run."
command -v git >/dev/null 2>&1 || die "git not found — install git and re-run."
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' \
    || die "Python 3.11+ required (found $("$PYTHON" -V 2>&1))."
ok "Prerequisites present ($("$PYTHON" -V 2>&1), git)."

# --- 2. locate or fetch the repo -------------------------------------------
if [ -f "./pyproject.toml" ] && grep -q 'name = "h1monitor"' ./pyproject.toml 2>/dev/null; then
    INSTALL_DIR="$(pwd)"
    OLD_VERSION="$(read_version "$INSTALL_DIR")"
    info "Using this checkout: $INSTALL_DIR"
elif [ -f "$INSTALL_DIR/pyproject.toml" ]; then
    OLD_VERSION="$(read_version "$INSTALL_DIR")"
    info "Updating existing install at $INSTALL_DIR ..."
    if ! git -C "$INSTALL_DIR" pull --ff-only >/dev/null 2>&1; then
        # A fast-forward can be refused for reasons that have nothing to do with
        # the network: the published history was rewritten, or the repo was
        # recreated. Warning and carrying on then leaves someone running old code
        # while the installer reports success -- the exact failure this is here to
        # prevent. An install directory holds no work worth keeping (.env, the
        # database and the key are all git-ignored, so a reset cannot touch them),
        # so take the published code.
        if git -C "$INSTALL_DIR" fetch -q --force --tags origin 2>/dev/null \
           && git -C "$INSTALL_DIR" reset -q --hard origin/main 2>/dev/null; then
            info "Local copy had diverged from the published code — reset to it."
        else
            warn "Couldn't fetch the latest code. Continuing with what is already"
            warn "here, which may be an older version. Check your connection, or"
            warn "reinstall from scratch: rm -rf $INSTALL_DIR"
        fi
    fi
else
    info "Cloning into $INSTALL_DIR ..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# A bot token already in .env is the honest signal that this machine has been
# set up before — more reliable than "the directory exists", which is equally
# true of a first run that died halfway.
if grep -qs '^TELEGRAM_BOT_TOKEN=.\+' .env 2>/dev/null; then
    UPGRADE=1
fi

# --- 3. virtualenv + install -----------------------------------------------
if [ "$UPGRADE" = "1" ]; then
    info "Upgrading h1monitor (this can take a minute) ..."
else
    info "Building virtualenv and installing (this can take a minute) ..."
fi
# Test for the pip binary, not for the directory. A venv that failed halfway --
# Debian and Ubuntu pass the version check above but ship no python3-venv, and
# Ctrl-C or a full disk do it too -- still leaves .venv/ behind. Checking only
# that the directory exists would treat that wreck as a finished venv on every
# later run, so the installer could never repair itself, even after the missing
# package was installed. Throw away anything unusable and build again.
if [ ! -x .venv/bin/pip ]; then
    rm -rf .venv
    "$PYTHON" -m venv .venv || die "Couldn't create the virtualenv.
    On Debian/Ubuntu/Kali, install the missing piece and re-run this installer:
        sudo apt install -y python3-venv"
    [ -x .venv/bin/pip ] || die "The virtualenv was created without pip.
    On Debian/Ubuntu/Kali, install the missing piece and re-run this installer:
        sudo apt install -y python3-venv"
fi
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e .
ok "Installed into $INSTALL_DIR/.venv"

# --- 4. Telegram bot token --------------------------------------------------
if grep -qs '^TELEGRAM_BOT_TOKEN=.\+' .env 2>/dev/null; then
    ok "Bot token already set in .env — leaving it as is."
else
    echo
    info "Create a Telegram bot: message @BotFather, send /newbot, and copy the token it gives you."
    token=""
    while [ -z "$token" ]; do token="$(prompt_tty 'Paste your Telegram bot token: ')"; done
    umask 077
    touch .env
    if grep -qs '^TELEGRAM_BOT_TOKEN=' .env; then
        sed -i.bak "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${token}|" .env && rm -f .env.bak
    else
        printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" >> .env
    fi
    chmod 600 .env
    ok "Saved token to .env (permissions 0600)."
fi

# --- 5. run it: 24/7 via systemd where possible, else print how to start ----
unit="$HOME/.config/systemd/user/h1monitor.service"
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    if [ "$UPGRADE" = "1" ]; then
        info "Restarting the service with the new version ..."
    else
        info "Setting up the systemd user service (runs 24/7, restarts on crash) ..."
    fi
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<EOF
[Unit]
Description=h1monitor — HackerOne change monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python -m h1monitor
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
EOF
    # Lingering is what makes the bot start at boot and keep running while you
    # are logged out. Without it a user service only lives inside a login
    # session. enable-linger can report success and still not take effect, so
    # confirm the state rather than trusting the exit code -- silently ending up
    # with a bot that dies at logout is the worst outcome here.
    loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
    if loginctl show-user "$(id -un)" --property=Linger 2>/dev/null | grep -q '=yes'; then
        ok "It will keep running after you log out, and start again at boot."
    else
        warn "The bot will run now, but it will stop when you log out and will"
        warn "not start at boot. To fix that, run:"
        warn "    sudo loginctl enable-linger $(id -un)"
    fi
    systemctl --user daemon-reload
    systemctl --user enable --now h1monitor >/dev/null 2>&1 || true
    systemctl --user restart h1monitor
    sleep 2
    if systemctl --user is-active --quiet h1monitor; then
        ok "h1monitor is running."
        manage=$'systemctl --user status h1monitor        # is it running?\njournalctl --user -u h1monitor -f        # watch it work'
    else
        warn "h1monitor is NOT running. Here is what it said:"
        echo
        journalctl --user -u h1monitor -n 15 --no-pager 2>/dev/null \
            | sed 's/^/    /' || true
        echo
        warn "The usual cause is a mistyped bot token. Fix it in $INSTALL_DIR/.env"
        warn "then run: systemctl --user restart h1monitor"
        manage=$'systemctl --user restart h1monitor       # after fixing .env\njournalctl --user -u h1monitor -f        # watch it work' 
    fi
else
    warn "No systemd user session here — start it yourself:"
    manage="cd $INSTALL_DIR && ./.venv/bin/python -m h1monitor"
fi

# --- 6. what's left --------------------------------------------------------
# An upgrade gets a one-line result. Replaying "open Telegram and send /start"
# to someone who did that months ago reads as though the run undid their setup.
echo
NEW_VERSION="$(read_version "$INSTALL_DIR")"
if [ "$UPGRADE" = "1" ]; then
    if [ -n "$OLD_VERSION" ] && [ -n "$NEW_VERSION" ] && [ "$OLD_VERSION" != "$NEW_VERSION" ]; then
        ok "Upgraded $OLD_VERSION → $NEW_VERSION."
    elif [ -n "$NEW_VERSION" ]; then
        ok "Already on $NEW_VERSION — nothing to change."
    else
        ok "Up to date."
    fi
    printf '\n%s\n' "Your bot token, HackerOne key and settings are unchanged."
else
    ok "Setup complete."
    cat <<EOF

Last step — open Telegram and DM your bot:

  1. /start                       claim the bot (only you can command it after that)
  2. /setup <username> <token>    connect HackerOne, for your private programs
                                  (both are on https://hackerone.com/settings/api_token/edit)

Public-program alerts need no key. The first check stays quiet while it records
what is already there; alerts start from the second one.

Manage it:
  ${manage}
EOF
fi
