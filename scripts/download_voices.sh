#!/usr/bin/env bash
# Downloads the Polish Piper voice models used by krzykacz. Idempotent --
# skips any file that's already present, so it's safe to rerun.
set -euo pipefail

DEST="${1:-/var/lib/krzykacz/voices}"
mkdir -p "$DEST"

fetch() {
    local url="$1" out="$2"
    if [ -s "$out" ]; then
        echo "already have: $out"
        return
    fi
    echo "downloading: $out"
    curl -fL --retry 3 -o "$out.part" "$url"
    mv "$out.part" "$out"
}

# rhasspy/piper-voices -- the official Piper voice collection on HuggingFace.
# name -> quality subdir (both parts of the .onnx filename too).
declare -A RHASSPY_VOICES=(
    [darkman]=medium
    [gosia]=medium
    [bass]=high
    [mc_speech]=medium
)
for voice in "${!RHASSPY_VOICES[@]}"; do
    quality="${RHASSPY_VOICES[$voice]}"
    BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/pl/pl_PL/${voice}/${quality}"
    fetch "$BASE/pl_PL-${voice}-${quality}.onnx" "$DEST/pl_PL-${voice}-${quality}.onnx"
    fetch "$BASE/pl_PL-${voice}-${quality}.onnx.json" "$DEST/pl_PL-${voice}-${quality}.onnx.json"
done

# justyna, jarvis, meski, zenski -- same Piper .onnx/.onnx.json format,
# mirrored by the sherpa-onnx project (csukuangfj "_wg_glos" voice family).
for voice in justyna jarvis meski zenski; do
    BASE="https://huggingface.co/csukuangfj/vits-piper-pl_PL-${voice}_wg_glos-medium/resolve/main"
    fetch "$BASE/pl_PL-${voice}_wg_glos-medium.onnx" "$DEST/pl_PL-${voice}_wg_glos-medium.onnx"
    fetch "$BASE/pl_PL-${voice}_wg_glos-medium.onnx.json" "$DEST/pl_PL-${voice}_wg_glos-medium.onnx.json"
done

# hvsr-robotics/tts-pl-piper-v2 -- one multi-speaker model (8 named speakers
# baked into a single file, fine-tuned from pl_PL-darkman-medium on Wolne
# Lektury audiobook narration) rather than 8 separate .onnx files. Selecting
# a speaker happens at synthesis time via KRZYKACZ_PIPER_VOICES's ":<index>"
# suffix -- see README's Piper voices section for the ready-made line.
BASE="https://huggingface.co/hvsr-robotics/tts-pl-piper-v2/resolve/main/export/piper_v2_target"
fetch "$BASE/pl_PL-tts-pl.onnx" "$DEST/pl_PL-tts-pl.onnx"
fetch "$BASE/pl_PL-tts-pl.onnx.json" "$DEST/pl_PL-tts-pl.onnx.json"

echo "done: $DEST"
ls -la "$DEST"
