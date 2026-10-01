#!/usr/bin/env bash
# Sends a message to a krzykacz instance via ntfy, or mutes/unmutes it -- over
# its HTTP API when --http / KRZYKACZ_HTTP_URL is given, otherwise via ntfy.
#
# Usage: krzykacz.sh [options] "message"
#        krzykacz.sh --mute | --unmute
set -euo pipefail

SERVER="${KRZYKACZ_NTFY_SERVER:-https://ntfy.sh}"
TOPIC="${KRZYKACZ_TOPIC:-}"
HTTP_URL="${KRZYKACZ_HTTP_URL:-}"
AUTH_TOKEN="${KRZYKACZ_AUTH_TOKEN:-}"
ACTION=""
VOICE=""
REPEAT=""
PREPARE=""
EFFECTS=()

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] "message"

Options:
  --topic TOPIC   ntfy topic (default: \$KRZYKACZ_TOPIC)
  --server URL    ntfy server (default: \$KRZYKACZ_NTFY_SERVER or https://ntfy.sh)
  --voice NAME    voice name (e.g. justyna, jarvis, meski, zenski, or an espeak
                   one such as espeak_male, espeak_female, espeak_robot)
  --repeat N      speak the message N times, separated by "Powtarzam!" (capped at 10)
  --effect FILE   play a sound effect from KRZYKACZ_ASSETS_DIR before speaking;
                   repeat the flag to play several, in order, before the message
  --prepare       render the message into krzykacz's audio cache without playing
                   it; sending the same message again later then plays at once
  --mute          silence krzykacz: refuse new messages, drop the queue, and stop
                   the one playing after its current sound or sentence
  --unmute        let krzykacz speak again
  --http URL      HTTP API base URL for --mute/--unmute, e.g. http://krzykacz.local:8123
                   (default: \$KRZYKACZ_HTTP_URL; bearer token from \$KRZYKACZ_AUTH_TOKEN);
                   without one, --mute/--unmute go over ntfy (Tags: mute=1 / mute=0)
  -h, --help      show this help

Examples:
  $(basename "$0") "Backup finished"
  $(basename "$0") --voice justyna --repeat 2 "Tests failed"
  $(basename "$0") --effect game_over "Something broke"
  $(basename "$0") --prepare --voice justyna "Obiad gotowy"   # silent, later plays at once
  $(basename "$0") --effect fight --effect game_over "Multiple sounds, then speech"
  $(basename "$0") --topic other-topic "Hello from another topic"
  $(basename "$0") --http http://krzykacz.local:8123 --mute
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --topic)
            TOPIC="${2:?--topic requires a value}"
            shift 2
            ;;
        --server)
            SERVER="${2:?--server requires a value}"
            shift 2
            ;;
        --voice)
            VOICE="${2:?--voice requires a value}"
            shift 2
            ;;
        --repeat)
            REPEAT="${2:?--repeat requires a value}"
            shift 2
            ;;
        --effect)
            EFFECTS+=("${2:?--effect requires a value}")
            shift 2
            ;;
        --prepare)
            PREPARE="1"
            shift
            ;;
        --mute)
            ACTION="mute"
            shift
            ;;
        --unmute)
            ACTION="unmute"
            shift
            ;;
        --http)
            HTTP_URL="${2:?--http requires a value}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

if [ -n "$ACTION" ] && [ -z "$HTTP_URL" ]; then
    # No HTTP API in reach: krzykacz also takes mute over ntfy.
    if [ -z "$TOPIC" ]; then
        echo "Error: no HTTP URL or topic given (use --http, or --topic / KRZYKACZ_TOPIC)" >&2
        exit 1
    fi
    if [ "$ACTION" = "mute" ]; then MUTE_VALUE=1; else MUTE_VALUE=0; fi
    curl -fsS -X POST -H "Tags: mute=$MUTE_VALUE" "$SERVER/$TOPIC"
    echo
    exit 0
fi

if [ -n "$ACTION" ]; then
    auth=()
    if [ -n "$AUTH_TOKEN" ]; then
        auth=(-H "Authorization: Bearer $AUTH_TOKEN")
    fi
    # ${auth[@]+...}: an empty array is an "unbound variable" under set -u on bash < 4.4.
    curl -fsS -X POST ${auth[@]+"${auth[@]}"} "${HTTP_URL%/}/v1/$ACTION"
    echo
    exit 0
fi

MESSAGE="${1:-}"
if [ -z "$MESSAGE" ]; then
    echo "Error: message is required" >&2
    usage >&2
    exit 1
fi

if [ -z "$TOPIC" ]; then
    echo "Error: no topic given (use --topic or set KRZYKACZ_TOPIC)" >&2
    exit 1
fi

PREFIX=""
if [ "${#EFFECTS[@]}" -gt 0 ]; then
    for effect in "${EFFECTS[@]}"; do
        PREFIX="$PREFIX<$effect> "
    done
fi
CONTENT="$PREFIX$MESSAGE"

TAGS=""
if [ -n "$VOICE" ]; then
    TAGS="voice=$VOICE"
fi
if [ -n "$REPEAT" ]; then
    TAGS="${TAGS:+$TAGS,}repeat=$REPEAT"
fi
if [ -n "$PREPARE" ]; then
    TAGS="${TAGS:+$TAGS,}prepare=1"
fi

if [ -n "$TAGS" ]; then
    curl -fsS -H "Tags: $TAGS" -d "$CONTENT" "$SERVER/$TOPIC"
else
    curl -fsS -d "$CONTENT" "$SERVER/$TOPIC"
fi
echo
