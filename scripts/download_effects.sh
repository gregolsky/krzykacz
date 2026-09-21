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
# Also synthesizes `knock` (three knocks on a door) with ffmpeg -- there's no
# CC0 recording of it in the packs above, and generating it ourselves keeps the
# soundboard entirely CC0 with nothing more to download.
#
# Requires: curl, unzip, ffmpeg (for `knock`).
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

# Three decaying thumps (a ~150 Hz body plus a ~310 Hz overtone and a short
# noise click for the knuckle), 0.28 s apart. Written to a temp name first so
# an interrupted run never leaves a truncated `knock` that the -s check below
# would then treat as done.
if [ ! -s "$DEST/knock" ]; then
    echo "synthesizing: knock"
    ffmpeg -loglevel error -y -f lavfi -i "aevalsrc='if(lt(t,0.84), 0.8*exp(-32*mod(t,0.28))*(0.7*sin(2*PI*150*mod(t,0.28))+0.35*sin(2*PI*310*mod(t,0.28))+0.25*(random(0)*2-1)*exp(-260*mod(t,0.28))), 0)':s=44100:d=1.0" \
        -ac 1 -f wav "$WORKDIR/knock"
    cp "$WORKDIR/knock" "$DEST/knock"
fi

echo "done: $DEST"
ls "$DEST" | wc -l
echo "sound files"
