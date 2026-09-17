#!/usr/bin/env bash
# Downloads a few CC0 sound effect packs and unpacks the sound files (as-is --
# ogg, no conversion needed since krzykacz plays anything ffmpeg can decode)
# into the krzykacz assets directory, renamed to short soundboard-style names
# (no extension -- <fight> rather than <voiceover-fighter_fight.ogg>).
# Idempotent -- skips any file that's already there, so it's safe to rerun.
#
# Sources: kenney.nl (Voiceover Pack Fighter, Music Jingles) and
# opengameart.org (80 CC0 creature SFX by rubberduck). All CC0.
#
# Requires: curl, unzip.
set -euo pipefail

DEST="${1:-/var/lib/krzykacz/assets}"
mkdir -p "$DEST"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

declare -A PACKS=(
    [fighter]="https://kenney.nl/media/pages/assets/voiceover-pack-fighter/6ceb77c6f1-1677589837/kenney_voiceover-pack-fighter.zip"
    [jingles]="https://kenney.nl/media/pages/assets/music-jingles/f37e530b9e-1677590399/kenney_music-jingles.zip"
    [creature]="https://opengameart.org/sites/default/files/80-CC0-creature-SFX_0.zip"
)

# Maps a pack's original file stem (basename without extension) to a short
# soundboard name. Falls back to the stem itself, lowercased, for anything
# the per-pack rules don't touch.
rename_for_pack() {
    local pack="$1" stem="$2"
    case "$pack" in
        fighter)
            stem="$(tr 'A-Z' 'a-z' <<<"$stem" | tr -d "'")"
            if [[ "$stem" =~ ^[0-9]+$ ]]; then
                printf 'count%02d' "$stem"
                return
            fi
            ;;
        jingles)
            stem="$(sed 's/^jingles_//' <<<"$stem" | tr 'A-Z' 'a-z')"
            stem="$(sed -e 's/^nes/8bit/' -e 's/^pizzi/pizz/' <<<"$stem")"
            ;;
        creature)
            stem="$(sed -e 's/^barking/bark/' -e 's/_\([0-9]\)/\1/' <<<"$stem")"
            ;;
    esac
    printf '%s' "$stem"
}

for pack in "${!PACKS[@]}"; do
    url="${PACKS[$pack]}"
    zip_path="$WORKDIR/$pack.zip"
    echo "downloading pack: $pack"
    curl -fL --retry 3 -o "$zip_path" "$url"

    extract_dir="$WORKDIR/$pack"
    unzip -q "$zip_path" -d "$extract_dir"

    # Sound files live under an "Audio/" (or similarly cased) subfolder;
    # find them regardless of exact nesting/casing. Preview* files are pack
    # demo reels bundled by the source, not effects -- skip them.
    mapfile -d '' -t sources < <(
        find "$extract_dir" \( -iname '*.ogg' -o -iname '*.wav' \) \
            ! -iname 'preview*' -print0
    )
    for src in "${sources[@]}"; do
        stem="$(basename "$src")"
        stem="${stem%.*}"
        name="$(rename_for_pack "$pack" "$stem")"
        out="$DEST/$name"
        if [ -s "$out" ]; then
            continue
        fi
        cp "$src" "$out"
    done
done

echo "done: $DEST"
ls "$DEST" | wc -l
echo "sound files"
