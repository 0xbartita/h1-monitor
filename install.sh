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
    # -s: keep the secret off the screen (and out of terminal scrollback and any
    # screenshot). Echo the newline ourselves, since a silent read swallows it.
    IFS= read -rs val < /dev/tty
    printf '\n' > /dev/tty
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
    info "Using this checkout: $INSTALL_DIR"
elif [ -f "$INSTALL_DIR/pyproject.toml" ]; then
    info "Updating existing install at $INSTALL_DIR ..."
    git -C "$INSTALL_DIR" pull --ff-only >/dev/null 2>&1 || warn "Couldn't update; using the code already there."
else
    info "Cloning into $INSTALL_DIR ..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# --- 3. virtualenv + install -----------------------------------------------
info "Building virtualenv and installing (this can take a minute) ..."
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
    info "Setting up the systemd user service (runs 24/7, restarts on crash) ..."
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
        warn "Service installed but not active — inspect with: systemctl --user status h1monitor"
        manage="systemctl --user status h1monitor"
    fi
else
    warn "No systemd user session here — start it yourself:"
    manage="cd $INSTALL_DIR && ./.venv/bin/python -m h1monitor"
fi

# --- 6. what's left (the two Telegram DMs) ---------------------------------
# The bot answers nobody until someone claims it with this code, which never
# leaves this machine. Printing it here saves the user digging through a log.
claim="$(./.venv/bin/python -m h1monitor --claim-code 2>/dev/null | tail -1)"
case "$claim" in
    *[!0-9a-f]* | "") claim_step="/start <your code>              claim the bot — see the code with:
                                  cd $INSTALL_DIR && ./.venv/bin/python -m h1monitor --claim-code" ;;
    *)                claim_step="/start $claim              claim the bot (only you can, with this code)" ;;
esac

echo
ok "Setup complete."
cat <<EOF

Last step — open Telegram and DM your bot:

  1. ${claim_step}
  2. /setup <username> <token>    connect HackerOne, for your private programs
                                  (both are on https://hackerone.com/settings/api_token/edit)

Public-program alerts need no key. The first check is a silent baseline; real
change alerts start from the next one.

Manage it:
  ${manage}
EOF
