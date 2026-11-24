#!/usr/bin/env bash
# Waits for a "deploy-krzykacz" message on an ntfy topic, then runs ansible-pull
# against main (local.yml). Runs as root from systemd/krzykacz-deploy.service.
#
# The topic is the only thing gating who can trigger a deploy, so it has no
# default here: set KRZYKACZ_DEPLOY_TOPIC to a random name in /etc/krzykacz.env.
# A deploy only ever installs what's on main of DEPLOY_REPO, and deploys closer
# together than KRZYKACZ_DEPLOY_MIN_INTERVAL seconds are skipped, so flooding
# the topic can't keep krzykacz restarting.

set -uo pipefail

SERVER="${KRZYKACZ_NTFY_SERVER:-https://ntfy.sh}"
TOPIC="${KRZYKACZ_DEPLOY_TOPIC:?set KRZYKACZ_DEPLOY_TOPIC (a random topic name) in /etc/krzykacz.env}"
REPO="${KRZYKACZ_DEPLOY_REPO:-https://github.com/gregolsky/krzykacz.git}"
CHECKOUT="${KRZYKACZ_DEPLOY_CHECKOUT:-/var/lib/krzykacz-deploy/checkout}"
MIN_INTERVAL="${KRZYKACZ_DEPLOY_MIN_INTERVAL:-300}"
SIGNAL="deploy-krzykacz"

last_deploy=0
while true; do
    echo "Listening on $SERVER/<deploy topic>"
    # /raw streams one line per message (blank lines are keepalives, every
    # ~45 s); --speed-time drops a connection that went silently dead.
    while IFS= read -r line; do
        [ "$line" = "$SIGNAL" ] || continue
        now=$(date +%s)
        if [ $((now - last_deploy)) -lt "$MIN_INTERVAL" ]; then
            echo "Deploy requested again within ${MIN_INTERVAL}s, skipped" >&2
            continue
        fi
        last_deploy=$now
        echo "Deploy requested, running ansible-pull from $REPO (main)"
        if ansible-pull -U "$REPO" -C main -d "$CHECKOUT" local.yml; then
            echo "Deploy finished"
        else
            echo "Deploy FAILED" >&2
        fi
    done < <(curl -fsSN --speed-limit 1 --speed-time 120 "$SERVER/$TOPIC/raw")
    echo "Stream ended, reconnecting in 5 s" >&2
    sleep 5
done
