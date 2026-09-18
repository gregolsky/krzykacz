# krzykacz 📢

Listens on an ntfy topic, blinks a USB lamp 💡 (via `uhubctl`), and reads messages
aloud in Polish 🗣️ (Piper TTS) through the audio jack 🔊.

## Protocol 📡

The ntfy message body is the literal text to read aloud. Parameters (voice,
repeat count, replay) ride in the `Tags` header as `key=value` entries, not in
the body -- ntfy does **not** forward arbitrary custom HTTP headers to
subscribers, but `Tags` is one of the documented fields that does survive:

```bash
curl -H "Tags: voice=justyna,repeat=2" -d "Testy padły" https://ntfy.sh/<your-topic>
```

Recognized keys:

| Key | Meaning |
|---|---|
| `voice=<name>` | Piper voice name (see table below) |
| `repeat=<n>` | speak the body `n` times, separated by `" Powtarzam! "` ("Repeating!"), capped at 10 (`MAX_REPEAT_COUNT`) |
| `replay=<index>` | replay message `<index>` from the last 10 messages instead of speaking the body (`-1` = most recent, `-2` = second most recent); the body is ignored when this is set |
| `speed=<x>` | tempo, `1.0` = normal, `1.5` = half again as fast, `0.7` = slower (clamped to 0.5–2.0) |
| `variation=<x>` | how far the voice strays from its average -- pitch/timbre wobble. Piper's default is ~`0.667`; lower is flatter and more monotone, higher is livelier but can wobble (clamped to 0.0–1.5) |
| `rhythm=<x>` | how far per-syllable timing strays from the predicted durations. Piper's default is ~`0.8` (clamped to 0.0–1.5) |

Tags without `=` (ntfy also uses tags for plain emoji/text markers) and
unrecognized keys are ignored. A body with no `Tags` header at all is spoken
as plain text with the default voice, spoken once.

`speed`, `variation` and `rhythm` are this protocol's names for Piper's
`--length_scale`, `--noise_scale` and `--noise_w`. `speed` is the *inverse*
of `length_scale` (a caller thinks in "1.5x faster", not "0.67x the phoneme
duration"); the other two pass through unchanged. An out-of-range value is
clamped rather than rejected, and a non-numeric one is ignored -- none of
them is worth failing an alert over. Left unset, no flag is passed at all
and Piper uses its own defaults, so an untagged message sounds exactly as it
did before these existed. Instance-wide defaults live in
`KRZYKACZ_PIPER_SPEED` / `_VARIATION` / `_RHYTHM`; a tag overrides them per
message. The live ranges are in `GET /v1/limits`.

`voice` (allowed characters: letters, digits, `-`, `_`, max 64 chars) selects a
Piper voice by name from `KRZYKACZ_PIPER_VOICES`. A missing tag or unknown
name falls back to the default voice (`KRZYKACZ_PIPER_DEFAULT_VOICE`).

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

If the body starts with `<filename>`, a file of that name is played from
`KRZYKACZ_ASSETS_DIR` before the rest of the text is read (any format `ffmpeg`
can decode -- mp3, ogg, wav, ...), e.g.:

```bash
curl -d "<boom.mp3> Tests failed" https://ntfy.sh/<your-topic>
```

The filename can't contain `/` or `..` (protects against escaping the assets
directory). A missing effect file doesn't block reading the text -- the effect
is simply skipped.

A message longer than 800 bytes (`MAX_CONTENT_BYTES`, roughly a minute of
speech) is truncated -- this is meant to be read aloud on the spot, not
archived. The fully-joined text after `repeat` duplication is separately
capped at 1600 bytes (`MAX_SPOKEN_BYTES`), so a high `repeat` on a long message
can't run for minutes either.

### Full example

```bash
curl -H "Tags: voice=justyna,repeat=2" \
  -d "<game_over> Tests failed" \
  https://ntfy.sh/<your-topic>
```

The `game_over` effect plays once, then, in the `justyna`
voice: "Tests failed. Powtarzam! Tests failed."

### Sending from the command line ⌨️

`scripts/krzykacz.sh` wraps the protocol above.

```bash
export KRZYKACZ_TOPIC=<your-topic>
./scripts/krzykacz.sh "Backup finished"
./scripts/krzykacz.sh --voice justyna --repeat 2 "Tests failed"
./scripts/krzykacz.sh --effect game_over "Something broke"
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

### Rate limiting and audit logging

Every call that actually triggers the light/speaker -- `POST /v1/publish`
over HTTP, and the `send_message`/`play_recent_message` MCP tools -- is
limited to one call per source IP per `KRZYKACZ_RATE_LIMIT_INTERVAL`
seconds (default `10`); a caller over that gets `429` (HTTP) or a
"rate limited" text result (MCP) instead of being queued. The limit is
shared between the two transports, so one IP can't get two calls in by
mixing them. Set it to `0` to disable. Read-only calls (every `GET`, and the
`list_voices`/`list_effects` MCP tools) are never rate-limited.

Every HTTP request and MCP tool call is also logged one line at a time under
the `krzykacz.audit` logger name (`ip=... action=... ...`), so
`journalctl -u krzykacz | grep krzykacz.audit` shows who called what and
whether it was queued, dropped, rate-limited, or rejected.

Both the limiter and the audit log key on the raw TCP/ASGI source address --
there's no `X-Forwarded-For` support. Putting a reverse proxy in front of
krzykacz (e.g. to add TLS) would make every real caller show up as the
proxy's own address, collapsing "one call per IP" into one shared budget for
the whole service; this is a home-LAN tool with no reverse proxy in its
supported setup, so that's accepted rather than worked around.

### HTTP

Set `KRZYKACZ_HTTP_ENABLED=1`. The HTTP API is versioned under `/v1`.

#### `POST /v1/publish`

Accepts the exact same body+`Tags` shape as the ntfy protocol above:

```bash
curl -H "Authorization: Bearer <token>" \
  -H "Tags: voice=justyna" \
  -d "hello" \
  http://192.168.1.50:8123/v1/publish
```

Responds `202 {"status": "queued"}`, or `503 {"status": "dropped"}` if the
announcer's queue is full (see `KRZYKACZ_QUEUE_SIZE`), `401` if the token is
missing/wrong, or `429 {"error": "rate limited"}` if this source IP already
published within `KRZYKACZ_RATE_LIMIT_INTERVAL` seconds (see below). Every
`GET` below is read-only and not rate-limited.

#### `GET /v1/queue`

Reports what's currently playing and what's still waiting behind it:

```bash
curl -H "Authorization: Bearer <token>" http://192.168.1.50:8123/v1/queue
```

```json
{
  "playing": {"content": "Uwaga, obiad gotowy", "voice": "justyna", "repeat": 1},
  "pending": [{"content": "Backup zakonczony", "voice": null, "repeat": 1}]
}
```

`playing` is `null` when the announcer is idle. A queued `replay` request
serializes as `{"replay": -1}` instead of `content`/`voice`/`repeat`.

#### `GET /v1/voices`

What can be passed as `voice`:

```bash
curl -H "Authorization: Bearer <token>" http://192.168.1.50:8123/v1/voices
```

```json
{
  "tts": "piper",
  "voices": ["darkman", "justyna", "jarvis", "meski", "zenski", "gosia", "bass", "mc_speech"],
  "default_voice": "darkman"
}
```

`tts` tells you how to interpret `voice`: under `piper` the names come from the
configured voice map, under `espeak` they're espeak-ng language codes. Voices
backed by a multi-speaker model appear here as ordinary names, one per speaker.

#### `GET /v1/effects`

What can be named in a `<file>` tag:

```bash
curl -H "Authorization: Bearer <token>" http://192.168.1.50:8123/v1/effects
```

```json
{"effects": ["fight", "game_over", "bark01", "8bit00", "..."]}
```

Read fresh from disk on each request, so effects added by
`download_effects.sh` show up without a restart.

#### `GET /v1/limits`

The numbers a client would otherwise hardcode from this README -- how long a
message may be, how far `repeat` and `replay` reach, how often it may call:

```bash
curl -H "Authorization: Bearer <token>" http://192.168.1.50:8123/v1/limits
```

```json
{
  "max_content_bytes": 800,
  "max_spoken_bytes": 1600,
  "max_repeat": 10,
  "history_size": 10,
  "queue_size": 10,
  "rate_limit_interval": 10.0
}
```

### MCP

Set `KRZYKACZ_MCP_ENABLED=1`. Exposes an MCP server over Streamable HTTP at
`http://<host>:8124/mcp`, with two tools that speak:

- `send_message(content, voice=None, repeat=None, speed=None, variation=None, rhythm=None)` -- speak new text
- `play_recent_message(number=-1)` -- replay one of the last few messages
  instead of resubmitting its text

and two read-only ones that just report, mirroring `GET /v1/voices` and
`GET /v1/effects` (not rate-limited, since they touch neither the lamp nor
the speaker):

- `list_voices()` -- voice names, the default, and the TTS backend
- `list_effects()` -- sound-effect filenames usable in a `<file>` tag

The speaking tools' descriptions (and the server's `instructions`) are
generated from this instance's actual configuration -- the real list of
voice names, the default voice, the TTS backend -- so an MCP client sees
what it can pass to `voice` without guessing or re-reading this README, and
can call `list_voices`/`list_effects` for anything added since the session
started.

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
| `KRZYKACZ_PIPER_VOICES` | *(empty)* | extra voices: `name=/path.onnx,name2=/path2.onnx`; append `:<speaker index>` to a value to select one embedded speaker out of a multi-speaker model, e.g. `staszczyk=/path/pl_PL-tts-pl.onnx:0` -- see "Piper voices" below |
| `KRZYKACZ_ESPEAK_VOICE` | `pl` | espeak-ng voice (fallback backend) |
| `KRZYKACZ_ALSA_DEVICE` | *(unset = system default)* | ALSA device for `aplay` -- **check `aplay -l` on your Pi: the default card may be HDMI, not the jack, in which case you need something like `plughw:1,0`** |
| `KRZYKACZ_PIPER_SPEED` | *(unset = piper's default)* | instance-wide `speed` (see Protocol above); a `speed` tag on a message overrides this |
| `KRZYKACZ_PIPER_VARIATION` | *(unset = piper's default)* | instance-wide `variation`; overridden per message by a `variation` tag |
| `KRZYKACZ_PIPER_RHYTHM` | *(unset = piper's default)* | instance-wide `rhythm`; overridden per message by a `rhythm` tag |
| `KRZYKACZ_EFFECTS` | `ffmpeg` | `ffmpeg` or `null` |
| `KRZYKACZ_ASSETS_DIR` | `/var/lib/krzykacz/assets` | directory holding sound effect files |
| `KRZYKACZ_HISTORY` | `10` | how many recent messages to keep in memory |
| `KRZYKACZ_QUEUE_SIZE` | `10` | max number of messages waiting to be played; anything beyond that is dropped (with a log warning) rather than queued indefinitely |
| `KRZYKACZ_HTTP_ENABLED` | `0` | set to `1` to enable the HTTP endpoints (`POST /v1/publish`, plus `GET /v1/voices`, `/v1/effects`, `/v1/limits`, `/v1/queue`) |
| `KRZYKACZ_HTTP_HOST` | `0.0.0.0` | HTTP endpoint bind address |
| `KRZYKACZ_HTTP_PORT` | `8123` | HTTP endpoint port |
| `KRZYKACZ_MCP_ENABLED` | `0` | set to `1` to enable the MCP endpoint (requires `pip install mcp`, Python >= 3.10) |
| `KRZYKACZ_MCP_HOST` | `0.0.0.0` | MCP endpoint bind address |
| `KRZYKACZ_MCP_PORT` | `8124` | MCP endpoint port |
| `KRZYKACZ_AUTH_TOKEN` | *(unset = no auth)* | bearer token required by the HTTP and MCP endpoints when set |
| `KRZYKACZ_RATE_LIMIT_INTERVAL` | `10` | seconds between calls that trigger the light/speaker, per source IP; shared by HTTP and MCP; `0` disables it |
| `KRZYKACZ_CACHE_DIR` | `/var/cache/krzykacz` | where synthesized audio is cached (see "Audio cache" below) |
| `KRZYKACZ_CACHE_TTL` | `86400` (24h) | seconds a cache entry stays valid; `0` disables caching entirely |
| `KRZYKACZ_CACHE_MAX_MB` | `200` | cache directory size cap; oldest entries are evicted first once it's exceeded |

## Audio cache 💾

Synthesizing is the slow part of announcing a message -- `piper`'s process
start, model load and inference are what causes the multi-second gap between
the light turning on and sound starting (see `Announcer._announce`). A
`replay`, a nightly-identical alert, or simply the same message sent twice
pays that cost again for byte-identical output, so the second time it's
served from `KRZYKACZ_CACHE_DIR` instead.

The cache key covers everything that decides the audio: the text, the
resolved model/speaker (not just the voice *name* -- `KRZYKACZ_PIPER_VOICES`
can remap a name, and an unknown one falls back to the default), and the
`speed`/`variation`/`rhythm` knobs. A `piper` crash (empty output) is never
cached, so a transient failure doesn't serve silence for the rest of the
TTL. Entries older than `KRZYKACZ_CACHE_TTL` are dropped on their next
lookup; the directory is also swept for size roughly once an hour, oldest
first, once it exceeds `KRZYKACZ_CACHE_MAX_MB`.

Every write and read failure (full disk, missing directory, permissions) is
logged and falls back to synthesizing directly -- a broken cache degrades
speed, never breaks playback. The directory can be deleted at any time; it's
rebuilt on demand. Set `KRZYKACZ_CACHE_TTL=0` to disable caching altogether.

`repeat` gets a related optimization on Piper (raw PCM concatenates
cleanly): the message and `" Powtarzam! "` are each synthesized once and the
requested number of copies are joined as audio, instead of synthesizing the
whole joined text every time. The separator is cached too, and it's the same
entry across every message in a given voice, so after the first repeated
message it's always a cache hit. This also means a long message with a high
`repeat` gets truncated to whole copies rather than cut off mid-word.
espeak-ng (the no-hardware fallback) keeps the old joined-text behavior --
its WAV output doesn't concatenate.

## Piper voices 🎙️

Eight ready-made single-speaker Polish voices, Piper format (`.onnx` +
`.onnx.json`), all 22050 Hz (see below for 8 more from one multi-speaker model):

- **`darkman`** (default), **`gosia`**, **`bass`**, **`mc_speech`** --
  [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices), the
  official Piper voice collection on HuggingFace.
- **`justyna`**, **`jarvis`**, **`meski`**, **`zenski`** --
  [`csukuangfj/vits-piper-pl_PL-*_wg_glos-medium`](https://huggingface.co/csukuangfj),
  the same Piper format, packaged via the sherpa-onnx mirror (the "wg_glos" voice family).

(`rhasspy/piper-voices` also has `mls_6892` for Polish, but it's a lower-quality
16 kHz model -- our fixed 22050 Hz pipeline doesn't support it out of the box, so
it's left out.)

### Multi-speaker: 8 more voices from one model

[`hvsr-robotics/tts-pl-piper-v2`](https://huggingface.co/hvsr-robotics/tts-pl-piper-v2)
bakes 8 named speakers into a single 22050 Hz `.onnx` file (fine-tuned from
`pl_PL-darkman-medium` on Wolne Lektury audiobook narration, CC BY-SA 4.0).
`download_voices.sh` fetches it as `pl_PL-tts-pl.onnx`, but unlike the voices
above, one file isn't one voice here -- each speaker needs its own
`KRZYKACZ_PIPER_VOICES` entry pointing at the *same* file with a different
`:<speaker index>` suffix (see `krzykacz.tts.VoiceSpec` /
`krzykacz.config._parse_voices`, which parse that suffix and pass it to
`piper --speaker <index>` at synthesis time):

```bash
KRZYKACZ_PIPER_VOICES="staszczyk=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:0,\
krzyzowski=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:1,\
masiak=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:2,\
bielenia=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:3,\
proszek=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:4,\
faszczewska=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:5,\
glogowski=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:6,\
kopa=/var/lib/krzykacz/voices/pl_PL-tts-pl.onnx:7"
```

(Index-to-name mapping straight from the model card: `0` Jan Staszczyk, `1`
Radosław Krzyżowski, `2` Wojciech Masiak, `3` Bartosz Bielenia, `4` Marek
Proszek, `5` Katarzyna Faszczewska, `6` Bartosz Głogowski, `7` Piotr Kopa;
this repo's names above drop diacritics and use surnames only, to stay
consistent with the plain-ASCII, single-word style of the other voice
names and to avoid the two Bartoszes colliding.) These voices then show up
in `GET /v1/voices` and the MCP tool descriptions exactly like the eight
above -- one flat list of voice names, regardless of how many `.onnx` files
back them.

Download (idempotent -- skips files already on disk):

```bash
./scripts/download_voices.sh
# or to a different directory:
./scripts/download_voices.sh /path/to/voices
```

The default target directory is `/var/lib/krzykacz/voices`, matching the default
paths in `KRZYKACZ_PIPER_MODEL` / `KRZYKACZ_PIPER_VOICES` above.

## Sound effects (CC0) 💥

Three packs, all CC0 (public domain, no attribution required): the Voiceover
Pack (Fighter) and Music Jingles from [kenney.nl](https://kenney.nl), and
[80 CC0 creature SFX](https://opengameart.org/content/80-cc0-creature-sfx) by
rubberduck on OpenGameArt. 211 `.ogg` files total, stored **without an
extension** so the tag you type is short -- `<fight>`, `<8bit00>`, `<bark01>`.
Full list of names in [`SOUNDS.md`](SOUNDS.md), or read live from
`GET /v1/effects` (the `list_effects` MCP tool reports the same thing).

Download (idempotent, copies files without conversion -- `KRZYKACZ_EFFECTS=ffmpeg`
plays any format `ffmpeg` can decode):

```bash
./scripts/download_effects.sh
# or to a different directory:
./scripts/download_effects.sh /path/to/assets
```

The default target directory is `/var/lib/krzykacz/assets`, matching the default
`KRZYKACZ_ASSETS_DIR` above. Any file you drop into that directory by hand also
becomes usable in a `<...>` tag under its own filename.

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
