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

## HTTP and MCP endpoints 🌐

Besides ntfy, krzykacz can accept the same messages directly over the local
network -- no ntfy relay needed. Both are off by default and run inside the
existing process.

### Auth

Both endpoints are open by default, same trust model as ntfy (the topic
name is the only secret there). Set `KRZYKACZ_AUTH_TOKEN` to require a
bearer token (`Authorization: Bearer <token>`) on every request to either
endpoint -- the examples below include it; drop the header if you haven't
set a token.

### HTTP

Set `KRZYKACZ_HTTP_ENABLED=1`. The HTTP API is versioned under `/v1`.

#### `POST /v1/publish`

Accepts the exact same JSON body as the ntfy protocol above:

```bash
curl -H "Authorization: Bearer <token>" \
  -d '{"type": "msg", "content": "hello", "voice": "justyna"}' \
  http://192.168.1.50:8123/v1/publish
```

Responds `202 {"status": "queued"}`, or `503 {"status": "dropped"}` if the
announcer's queue is full (see `KRZYKACZ_QUEUE_SIZE`), or `401` if the
token is missing/wrong.

#### `GET /v1/metadata`

Reports what this particular instance can actually play -- the configured
voices and the sound effects present in `KRZYKACZ_ASSETS_DIR` -- so a client
doesn't have to hardcode the tables from this README:

```bash
curl -H "Authorization: Bearer <token>" http://192.168.1.50:8123/v1/metadata
```

```json
{
  "tts": "piper",
  "voices": ["darkman", "justyna", "jarvis", "meski", "zenski", "gosia", "bass", "mc_speech"],
  "default_voice": "darkman",
  "effects": ["digital-audio_alarm_001.ogg", "interface-sounds_error_001.ogg", "..."]
}
```

`tts` tells you how to interpret `voice`: under `piper` the names come from the
configured voice map, under `espeak` they're espeak-ng language codes. `effects`
lists the filenames usable in a `<file>` tag; it's read fresh from disk on each
request, so effects added by `download_effects.sh` show up without a restart.

### MCP

Set `KRZYKACZ_MCP_ENABLED=1`. Exposes an MCP server over Streamable HTTP at
`http://<host>:8124/mcp`, with two tools:

- `send_message(content, voice=None, repeat=None)`
- `repeat_message(number=-1)`

This uses the official `mcp` Python SDK, which is **not** in
`requirements.txt` (like `piper-tts`, it's an optional extra -- install it
only if you use this feature) and **requires Python >= 3.10** (Raspberry Pi
OS bullseye ships 3.9; use bookworm or newer for this feature):

```bash
pip install mcp
```

From an MCP client config (e.g. Claude Desktop / any client that supports a
remote Streamable HTTP server with custom headers):

```json
{
  "mcpServers": {
    "krzykacz": {
      "url": "http://192.168.1.50:8124/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Or talk to it directly with `curl` (raw MCP JSON-RPC over Streamable HTTP)
-- a session has to be opened with `initialize` first (most MCP clients do
this automatically), then reused via the `Mcp-Session-Id` response header
on every following call:

```bash
HOST=192.168.1.50:8124
TOKEN=<token>

SESSION=$(curl -si -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}' \
  "http://$HOST/mcp" | grep -i '^mcp-session-id:' | tr -d '\r' | cut -d' ' -f2)

curl -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"send_message","arguments":{"content":"hello"}}}' \
  "http://$HOST/mcp"
```

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
| `KRZYKACZ_ASSETS_DIR` | `/var/lib/krzykacz/assets` | directory holding sound effect files |
| `KRZYKACZ_HISTORY` | `10` | how many recent messages to keep in memory |
| `KRZYKACZ_QUEUE_SIZE` | `10` | max number of messages waiting to be played; anything beyond that is dropped (with a log warning) rather than queued indefinitely |
| `KRZYKACZ_HTTP_ENABLED` | `0` | set to `1` to enable the HTTP endpoints (`POST /v1/publish`, `GET /v1/metadata`) |
| `KRZYKACZ_HTTP_HOST` | `0.0.0.0` | HTTP endpoint bind address |
| `KRZYKACZ_HTTP_PORT` | `8123` | HTTP endpoint port |
| `KRZYKACZ_MCP_ENABLED` | `0` | set to `1` to enable the MCP endpoint (requires `pip install mcp`, Python >= 3.10) |
| `KRZYKACZ_MCP_HOST` | `0.0.0.0` | MCP endpoint bind address |
| `KRZYKACZ_MCP_PORT` | `8124` | MCP endpoint port |
| `KRZYKACZ_AUTH_TOKEN` | *(unset = no auth)* | bearer token required by the HTTP and MCP endpoints when set |

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

The default target directory is `/var/lib/krzykacz/assets`, matching the default
`KRZYKACZ_ASSETS_DIR` above. Each file on disk is named `<pack>_<original-name>`,
e.g. `interface-sounds_error_001.ogg` -- that's exactly the name you put in the
`<...>` tag.

## Local testing (no hardware) 🧪

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
KRZYKACZ_LIGHT=null KRZYKACZ_TTS=espeak KRZYKACZ_TOPIC=<your-topic> python -m krzykacz
```

## Deployment (systemd) 🚀

```bash
# on the Pi, after rsync-ing the repo to /home/pi/krzykacz and creating
# /etc/krzykacz.env (see Configuration above):
sudo ./scripts/install-service.sh
```

This creates a dedicated, unprivileged `krzykacz` system user (no login shell, no
sudo) and installs `systemd/krzykacz.service` to run as that user rather than root.
The only two things the service touches that normally require privilege -- the USB
hub (`uhubctl`, for the light) and the audio device (`aplay`) -- are granted via a
narrow udev rule and the `audio` group, both set up by the script. Re-run it any time
after pulling an update; it's idempotent.

The code is copied to `/opt/krzykacz`, which is what the service actually runs
from. Home directories are mode `0700` on Raspberry Pi OS, so a service user can't
traverse into one -- installing outside `/home` is better than loosening those
permissions, and it lets the unit use `ProtectHome=yes` to hide `/home` from the
service entirely. Voices and sound effects live under `/var/lib/krzykacz/` for the
same reason.

The venv is not managed by the script (onnxruntime wheels are slow to build on a
Pi, so it's preserved across runs). Create it once:

```bash
sudo python3 -m venv /opt/krzykacz/venv
sudo /opt/krzykacz/venv/bin/pip install -r /opt/krzykacz/requirements.txt piper-tts
# only if using the MCP endpoint (KRZYKACZ_MCP_ENABLED=1, needs Python >= 3.10):
sudo /opt/krzykacz/venv/bin/pip install mcp
```

```bash
systemctl status krzykacz
journalctl -u krzykacz -f
```

## Unit tests ✅

```bash
pip install pytest
python -m pytest
```
