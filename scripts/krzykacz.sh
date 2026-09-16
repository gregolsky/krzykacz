#!/usr/bin/env bash
# Sends a message to a krzykacz instance via ntfy.
#
# Usage: krzykacz.sh [options] "message"
set -euo pipefail

SERVER="${KRZYKACZ_NTFY_SERVER:-https://ntfy.sh}"
TOPIC="${KRZYKACZ_TOPIC:-}"
VOICE=""
REPEAT=""
EFFECT=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [options] "message"

Options:
  --topic TOPIC   ntfy topic (default: \$KRZYKACZ_TOPIC)
  --server URL    ntfy server (default: \$KRZYKACZ_NTFY_SERVER or https://ntfy.sh)
  --voice NAME    Piper voice name (e.g. justyna, jarvis, meski, zenski)
  --repeat N      speak the message N times, separated by "Powtarzam!" (capped at 10)
  --effect FILE   play a sound effect from KRZYKACZ_ASSETS_DIR before speaking
  -h, --help      show this help

Examples:
  $(basename "$0") "Backup finished"
  $(basename "$0") --voice justyna --repeat 2 "Tests failed"
  $(basename "$0") --effect interface-sounds_error_001.ogg "Something broke"
  $(basename "$0") --topic other-topic "Hello from another topic"
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
            EFFECT="${2:?--effect requires a value}"
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

CONTENT="$MESSAGE"
if [ -n "$EFFECT" ]; then
    CONTENT="<$EFFECT> $MESSAGE"
fi

TAGS=""
if [ -n "$VOICE" ]; then
    TAGS="voice=$VOICE"
fi
if [ -n "$REPEAT" ]; then
    TAGS="${TAGS:+$TAGS,}repeat=$REPEAT"
fi

if [ -n "$TAGS" ]; then
    curl -fsS -H "Tags: $TAGS" -d "$CONTENT" "$SERVER/$TOPIC"
else
    curl -fsS -d "$CONTENT" "$SERVER/$TOPIC"
fi
echo
