#!/usr/bin/env bash
# Downloads a few CC0 sound effect packs from kenney.nl and unpacks the
# sound files (as-is -- ogg/wav, no conversion needed since krzykacz plays
# anything ffmpeg can decode) into the krzykacz assets directory.
# Idempotent -- skips any file that's already there, so it's safe to rerun.
#
# Requires: curl, unzip.
set -euo pipefail

DEST="${1:-/var/lib/krzykacz/assets}"
mkdir -p "$DEST"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# name -> zip URL. All from kenney.nl, all CC0 (https://kenney.nl/assets/<name> -> License: Creative Commons CC0).
declare -A PACKS=(
    [interface-sounds]="https://kenney.nl/media/pages/assets/interface-sounds/fa43c1dd4d-1677589452/kenney_interface-sounds.zip"
    [ui-audio]="https://kenney.nl/media/pages/assets/ui-audio/490d233f68-1677590494/kenney_ui-audio.zip"
    [digital-audio]="https://kenney.nl/media/pages/assets/digital-audio/216eac4753-1677590265/kenney_digital-audio.zip"
    [impact-sounds]="https://kenney.nl/media/pages/assets/impact-sounds/87b4ddecda-1677589768/kenney_impact-sounds.zip"
)

for pack in "${!PACKS[@]}"; do
    url="${PACKS[$pack]}"
    zip_path="$WORKDIR/$pack.zip"
    echo "downloading pack: $pack"
    curl -fL --retry 3 -o "$zip_path" "$url"

    extract_dir="$WORKDIR/$pack"
    unzip -q "$zip_path" -d "$extract_dir"

    # Sound files live under an "Audio/" (or similarly cased) subfolder in
    # every Kenney pack; find them regardless of exact nesting/casing.
    mapfile -d '' -t sources < <(find "$extract_dir" \( -iname '*.ogg' -o -iname '*.wav' \) -print0)
    for src in "${sources[@]}"; do
        out="$DEST/${pack}_$(basename "$src")"
        if [ -s "$out" ]; then
            continue
        fi
        cp "$src" "$out"
    done
done

echo "done: $DEST"
ls "$DEST" | wc -l
echo "sound files"
