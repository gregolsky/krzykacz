#!/usr/bin/env bash
# Waits for a "deploy-krzykacz" message on an ntfy topic, then runs ansible-pull
# against main (local.yml). Runs as root from systemd/krzykacz-deploy.service.
#
# Anyone who knows the topic can trigger a deploy, but a deploy only ever
# installs what's on main of DEPLOY_REPO -- it can't be fed other code.

set -uo pipefail

SERVER="${KRZYKACZ_NTFY_SERVER:-https://ntfy.sh}"
TOPIC="${KRZYKACZ_DEPLOY_TOPIC:-krzykacz-rpi-deploy}"
REPO="${KRZYKACZ_DEPLOY_REPO:-https://github.com/gregolsky/krzykacz.git}"
CHECKOUT="${KRZYKACZ_DEPLOY_CHECKOUT:-/var/lib/krzykacz-deploy/checkout}"
SIGNAL="deploy-krzykacz"

while true; do
    echo "Listening on $SERVER/$TOPIC"
    # /raw streams one line per message (blank lines are keepalives, every
    # ~45 s); --speed-time drops a connection that went silently dead.
    curl -fsSN --speed-limit 1 --speed-time 120 "$SERVER/$TOPIC/raw" |
        while IFS= read -r line; do
            [ "$line" = "$SIGNAL" ] || continue
            echo "Deploy requested, running ansible-pull from $REPO (main)"
            if ansible-pull -U "$REPO" -C main -d "$CHECKOUT" local.yml; then
                echo "Deploy finished"
            else
                echo "Deploy FAILED" >&2
            fi
        done
    echo "Stream ended, reconnecting in 5 s" >&2
    sleep 5
done
