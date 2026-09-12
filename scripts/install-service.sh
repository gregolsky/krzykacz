#!/usr/bin/env bash
# Installs krzykacz as a systemd service running as a dedicated, unprivileged
# `krzykacz` user (not root). Idempotent -- safe to re-run after an update.
#
# Usage: sudo ./scripts/install-service.sh
#
# Expects to be run from a checkout already deployed to its final location
# (default /home/pi/krzykacz, matching systemd/krzykacz.service's
# WorkingDirectory/ExecStart), with /etc/krzykacz.env already in place.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Must be run as root (sudo)." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="krzykacz"
UHUBCTL_LOC="${KRZYKACZ_UHUBCTL_LOC:-1-1}"
ENV_FILE="/etc/krzykacz.env"
UDEV_RULE_SRC="$REPO_DIR/systemd/52-krzykacz-uhubctl.rules"
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

# 3. udev rule granting write access to just this hub, for uhubctl.
HUB_SYSFS="/sys/bus/usb/devices/$UHUBCTL_LOC"
if [ ! -d "$HUB_SYSFS" ]; then
    echo "Warning: $HUB_SYSFS not found -- is KRZYKACZ_UHUBCTL_LOC=$UHUBCTL_LOC correct?" >&2
    echo "Skipping udev rule install; the light will not work as a non-root user until this is fixed." >&2
else
    vid="$(cat "$HUB_SYSFS/idVendor")"
    pid="$(cat "$HUB_SYSFS/idProduct")"
    sed -e "s/@VID@/$vid/" -e "s/@PID@/$pid/" "$UDEV_RULE_SRC" > "$UDEV_RULE_DST"
    chmod 644 "$UDEV_RULE_DST"
    udevadm control --reload-rules
    udevadm trigger --attr-match=idVendor="$vid" --attr-match=idProduct="$pid"
    echo "Installed udev rule for hub $UHUBCTL_LOC (vid=$vid pid=$pid)."
fi

# 4. Env file holds the ntfy topic -- the only thing gating who can trigger
# this box. Not secret from the service user, but no reason to leave it
# world-readable either.
if [ -f "$ENV_FILE" ]; then
    chown "root:$SERVICE_USER" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    echo "Tightened permissions on $ENV_FILE."
else
    echo "Warning: $ENV_FILE not found -- create it before starting the service." >&2
fi

# 5. Read access to code, voices, and assets. Report rather than chmod --
# these are shared directories (code checkout, voice models, sound assets)
# and blindly rewriting their permissions could surprise whoever manages them.
check_readable() {
    local path="$1"
    if [ ! -e "$path" ]; then
        echo "Note: $path does not exist yet." >&2
        return
    fi
    if sudo -u "$SERVICE_USER" test -r "$path"; then
        echo "OK: $SERVICE_USER can read $path"
    else
        echo "Warning: $SERVICE_USER cannot read $path -- fix permissions (e.g. 'chmod o+rX' or add $SERVICE_USER to the owning group) before starting the service." >&2
    fi
}
check_readable "$REPO_DIR"
check_readable "/var/lib/krzykacz/voices"
check_readable "${KRZYKACZ_ASSETS_DIR:-/home/pi/krzykacz-assets}"

# 6. Install and (re)start the unit. Explicit restart, not just
# `enable --now` -- if the service is already running (e.g. from a prior
# root-based install), `--now` alone would leave the old process running
# under its old User= until something else restarts it, defeating the
# whole point of this script.
cp "$REPO_DIR/systemd/krzykacz.service" /etc/systemd/system/krzykacz.service
systemctl daemon-reload
systemctl enable krzykacz
systemctl restart krzykacz

echo "Done. Check status with: systemctl status krzykacz"
echo "Logs: journalctl -u krzykacz -f"
