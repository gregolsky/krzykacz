# krzykacz 📢

Listens on an ntfy topic, blinks a USB lamp 💡 (via `uhubctl`), and reads messages
aloud in Polish 🗣️ (Piper TTS) through the audio jack 🔊.

## Protocol 📡

ntfy message body:

```json
{"type": "msg", "content": "text to read aloud", "voice": "justyna"}
{"type": "repeat", "number": -1}
```

`repeat.number` is an index into the last 10 messages (`-1` = most recent,
`-2` = second most recent). Plain text without JSON is treated as the `content`
of a message.

`voice` is optional (allowed characters: letters, digits, `-`, `_`, max 64 chars) --
selects a Piper voice by name from `KRZYKACZ_PIPER_VOICES`. A missing field or
unknown name falls back to the default voice (`KRZYKACZ_PIPER_DEFAULT_VOICE`).

Supported `voice` values in the default configuration (see "Piper voices" below):

| `voice` | Description |
|---|---|
| *(field absent)* | default voice -- `darkman` |
| `darkman` | default voice, male |
| `justyna` | female voice |
| `jarvis` | male voice |
| `meski` | male voice |
| `zenski` | female voice |
| `gosia` | female voice |
| `bass` | male voice, high quality |
| `mc_speech` | male voice |

A value outside this list isn't an error -- it silently falls back to the default
voice (with a warning in the log). The `espeak` backend (`KRZYKACZ_TTS=espeak`)
doesn't know these names -- there, `voice` is passed straight through as an
`espeak-ng` language/voice code (e.g. `pl`, `en`).

If `content` starts with `<filename>`, a file of that name is played from
`KRZYKACZ_ASSETS_DIR` before the rest of the text is read (any format `ffmpeg`
can decode -- mp3, ogg, wav, ...), e.g.:

```json
{"type": "msg", "content": "<boom.mp3> Tests failed"}
```

The filename can't contain `/` or `..` (protects against escaping the assets
directory). A missing effect file doesn't block reading the text -- the effect
is simply skipped.

`repeat` (optional, integer) repeats `content` that many times, inserting
`" Powtarzam! "` ("Repeating!") between copies. A missing field, a value `<= 1`,
or a non-numeric value leaves it unchanged (spoken once). The value is capped at
10 (`MAX_REPEAT_COUNT`) -- this is meant as an emphasis knob, not a way to force
minutes of playback. It only applies to the spoken part -- a sound effect from
`<file>` still plays once, before the repetitions.

### Full example

```bash
curl -d '{
  "type": "msg",
  "content": "<interface-sounds_error_001.ogg> Tests failed",
  "voice": "justyna",
  "repeat": 2
}' https://ntfy.sh/<your-topic>
```

The `interface-sounds_error_001.ogg` effect plays once, then, in the `justyna`
voice: "Tests failed. Powtarzam! Tests failed."

### Sending from the command line ⌨️

`scripts/krzykacz.sh` wraps the protocol above so you don't have to hand-write
JSON. Requires `jq`.

```bash
export KRZYKACZ_TOPIC=<your-topic>
./scripts/krzykacz.sh "Backup finished"
./scripts/krzykacz.sh --voice justyna --repeat 2 "Tests failed"
./scripts/krzykacz.sh --effect interface-sounds_error_001.ogg "Something broke"
./scripts/krzykacz.sh --topic other-topic --server https://ntfy.example.com "Hello"
```

Run `./scripts/krzykacz.sh --help` for the full flag list.

## Configuration (environment variables) ⚙️

| Variable | Default | Description |
|---|---|---|
| `KRZYKACZ_TOPIC` | *(required)* | ntfy topic name |
| `KRZYKACZ_NTFY_SERVER` | `https://ntfy.sh` | ntfy server |
| `KRZYKACZ_LIGHT` | `uhubctl` | `uhubctl` or `null` |
| `KRZYKACZ_UHUBCTL_LOC` | `1-1` | USB hub location |
| `KRZYKACZ_UHUBCTL_PORT` | `2` | port number |
| `KRZYKACZ_TTS` | `piper` | `piper` or `espeak` |
| `KRZYKACZ_PIPER_DEFAULT_VOICE` | `darkman` | default voice name (key in the voice map) |
| `KRZYKACZ_PIPER_MODEL` | `/var/lib/krzykacz/voices/pl_PL-darkman-medium.onnx` | path to the default voice's `.onnx` model |
| `KRZYKACZ_PIPER_VOICES` | *(empty)* | extra voices: `name=/path.onnx,name2=/path2.onnx` |
| `KRZYKACZ_ESPEAK_VOICE` | `pl` | espeak-ng voice (fallback backend) |
| `KRZYKACZ_ALSA_DEVICE` | *(unset = system default)* | ALSA device for `aplay` -- **check `aplay -l` on your Pi: the default card may be HDMI, not the jack, in which case you need something like `plughw:1,0`** |
| `KRZYKACZ_EFFECTS` | `ffmpeg` | `ffmpeg` or `null` |
| `KRZYKACZ_ASSETS_DIR` | `/home/pi/krzykacz-assets` | directory holding sound effect files |
| `KRZYKACZ_HISTORY` | `10` | how many recent messages to keep in memory |
| `KRZYKACZ_QUEUE_SIZE` | `10` | max number of messages waiting to be played; anything beyond that is dropped (with a log warning) rather than queued indefinitely |

## Piper voices 🎙️

Eight ready-made Polish voices, Piper format (`.onnx` + `.onnx.json`), all 22050 Hz:

- **`darkman`** (default), **`gosia`**, **`bass`**, **`mc_speech`** --
  [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices), the
  official Piper voice collection on HuggingFace.
- **`justyna`**, **`jarvis`**, **`meski`**, **`zenski`** --
  [`csukuangfj/vits-piper-pl_PL-*_wg_glos-medium`](https://huggingface.co/csukuangfj),
  the same Piper format, packaged via the sherpa-onnx mirror (the "wg_glos" voice family).

(`rhasspy/piper-voices` also has `mls_6892` for Polish, but it's a lower-quality
16 kHz model -- our fixed 22050 Hz pipeline doesn't support it out of the box, so
it's left out.)

Download (idempotent -- skips files already on disk):

```bash
./scripts/download_voices.sh
# or to a different directory:
./scripts/download_voices.sh /path/to/voices
```

The default target directory is `/var/lib/krzykacz/voices`, matching the default
paths in `KRZYKACZ_PIPER_MODEL` / `KRZYKACZ_PIPER_VOICES` above.

## Sound effects (CC0) 💥

Four packs from [kenney.nl](https://kenney.nl) (CC0 license -- public domain, no
attribution required): `interface-sounds`, `ui-audio`, `digital-audio`,
`impact-sounds`. 345 `.ogg` files total -- full list of names in
[`SOUNDS.md`](SOUNDS.md).

Download (idempotent, copies files without conversion -- `KRZYKACZ_EFFECTS=ffmpeg`
plays any format `ffmpeg` can decode):

```bash
./scripts/download_effects.sh
# or to a different directory:
./scripts/download_effects.sh /path/to/assets
```

The default target directory is `/home/pi/krzykacz-assets`, matching the default
`KRZYKACZ_ASSETS_DIR` above. Each file on disk is named `<pack>_<original-name>`,
e.g. `interface-sounds_error_001.ogg` -- that's exactly the name you put in the
`<...>` tag.

## Local testing (no hardware) 🧪

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
KRZYKACZ_LIGHT=null KRZYKACZ_TTS=espeak KRZYKACZ_TOPIC=<your-topic> python -m krzykacz
```

## Unit tests ✅

```bash
pip install pytest
python -m pytest
```
