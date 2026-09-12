#!/usr/bin/env bash
# Installs krzykacz as a systemd service running as a dedicated, unprivileged
# `krzykacz` user (not root). Idempotent -- safe to re-run after an update.
#
# Usage: sudo ./scripts/install-service.sh
#
# Run it from wherever you rsync'd the repo (typically ~/krzykacz). The code
# is copied to /opt/krzykacz, which is what the service actually runs from --
# a home directory is mode 0700 on Raspberry Pi OS, so a service user can't
# traverse into it, and relocating is better than loosening those permissions.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Must be run as root (sudo)." >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/opt/krzykacz"
SERVICE_USER="krzykacz"
UHUBCTL_LOC="${KRZYKACZ_UHUBCTL_LOC:-1-1}"
ENV_FILE="/etc/krzykacz.env"
VOICES_DIR="/var/lib/krzykacz/voices"
ASSETS_DIR="/var/lib/krzykacz/assets"
UDEV_RULE_SRC="$SRC_DIR/systemd/52-krzykacz-uhubctl.rules"
UDEV_RULE_DST="/etc/udev/rules.d/52-krzykacz-uhubctl.rules"

# 1. Dedicated system user, no login, no home directory to worry about.
if id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "User $SERVICE_USER already exists, skipping useradd."
else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "Created system user $SERVICE_USER."
fi

# 2. /dev/snd access for aplay.
usermod -aG audio "$SERVICE_USER"

# 3. Copy the code into place. The venv is preserved across runs -- it holds
# piper and its onnxruntime wheels, which are slow to reinstall on a Pi.
mkdir -p "$INSTALL_DIR"
rsync -a --delete --exclude venv --exclude __pycache__ --exclude .git \
    --exclude .pytest_cache "$SRC_DIR/" "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
echo "Installed code to $INSTALL_DIR."

if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    echo "Note: no venv at $INSTALL_DIR/venv -- create it before starting:" >&2
    echo "  sudo python3 -m venv $INSTALL_DIR/venv" >&2
    echo "  sudo $INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt piper-tts" >&2
fi

# 4. udev rule granting write access to just this hub, for uhubctl.
HUB_SYSFS="/sys/bus/usb/devices/$UHUBCTL_LOC"
if [ ! -d "$HUB_SYSFS" ]; then
    echo "Warning: $HUB_SYSFS not found -- is KRZYKACZ_UHUBCTL_LOC=$UHUBCTL_LOC correct?" >&2
    echo "Skipping udev rule; the light will not work as a non-root user until this is fixed." >&2
else
    vid="$(cat "$HUB_SYSFS/idVendor")"
    pid="$(cat "$HUB_SYSFS/idProduct")"
    sed -e "s/@VID@/$vid/" -e "s/@PID@/$pid/" "$UDEV_RULE_SRC" > "$UDEV_RULE_DST"
    chmod 644 "$UDEV_RULE_DST"
    udevadm control --reload-rules
    udevadm trigger --attr-match=idVendor="$vid" --attr-match=idProduct="$pid"
    echo "Installed udev rule for hub $UHUBCTL_LOC (vid=$vid pid=$pid)."
fi

# 5. Env file holds the ntfy topic -- the only thing gating who can trigger
# this box. Not secret from the service user, but no reason to leave it
# world-readable either.
if [ -f "$ENV_FILE" ]; then
    chown "root:$SERVICE_USER" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    echo "Tightened permissions on $ENV_FILE."
else
    echo "Warning: $ENV_FILE not found -- create it before starting the service." >&2
fi

# 6. Voices and assets live outside /home so ProtectHome=yes can hide the
# home directories from the service entirely.
mkdir -p "$VOICES_DIR" "$ASSETS_DIR"
chmod 755 "$VOICES_DIR" "$ASSETS_DIR"
for dir in "$VOICES_DIR" "$ASSETS_DIR"; do
    if ! sudo -u "$SERVICE_USER" test -r "$dir"; then
        echo "Warning: $SERVICE_USER cannot read $dir -- check its permissions." >&2
    fi
done
if [ -z "$(ls -A "$ASSETS_DIR" 2>/dev/null)" ]; then
    echo "Note: $ASSETS_DIR is empty -- run scripts/download_effects.sh $ASSETS_DIR" >&2
fi

# 7. Install and (re)start the unit. Explicit restart, not just
# `enable --now` -- if the service is already running (e.g. from a prior
# root-based install), `--now` alone would leave the old process running
# under its old User= until something else restarts it, defeating the
# whole point of this script.
cp "$SRC_DIR/systemd/krzykacz.service" /etc/systemd/system/krzykacz.service
systemctl daemon-reload
systemctl enable krzykacz
systemctl restart krzykacz

echo "Done. Check status with: systemctl status krzykacz"
echo "Logs: journalctl -u krzykacz -f"
