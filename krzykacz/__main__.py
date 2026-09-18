from __future__ import annotations

import functools
import logging
import random
import threading

from .announcer import Announcer
from .config import Config
from .effects import Effects, FfmpegEffects, NullEffects
from .http_server import build_http_server
from .light import Light, NullLight, UhubctlLight
from .metadata import Describers, describe_effects, describe_limits, describe_voices
from .ntfy import listen
from .random_picks import INTENSITIES, STYLES, Pickers, random_curse_msg, random_sound_msg
from .ratelimit import IpRateLimiter
from .tts import CachedTts, EspeakTts, PiperTts, Tts

logger = logging.getLogger(__name__)


def _start_thread(name: str, target, *args) -> None:
    """Runs target in a daemon thread, logging any exception that escapes it.
    Without this a failed server start (e.g. the optional `mcp` package
    missing) would kill its thread silently while the service stayed up."""

    def run() -> None:
        try:
            target(*args)
        except Exception:
            logger.exception("%s failed", name)

    threading.Thread(target=run, daemon=True, name=name).start()


def build_light(cfg: Config) -> Light:
    if cfg.light_backend == "uhubctl":
        return UhubctlLight(cfg.uhubctl_location, cfg.uhubctl_port)
    if cfg.light_backend == "null":
        return NullLight()
    raise ValueError(f"Unknown KRZYKACZ_LIGHT backend: {cfg.light_backend!r}")


def build_tts(cfg: Config) -> Tts:
    if cfg.tts_backend == "piper":
        tts: Tts = PiperTts(
            cfg.piper_voices, cfg.piper_default_voice, cfg.alsa_device, prosody=cfg.prosody
        )
    elif cfg.tts_backend == "espeak":
        tts = EspeakTts(cfg.espeak_voice, cfg.alsa_device, prosody=cfg.prosody)
    else:
        raise ValueError(f"Unknown KRZYKACZ_TTS backend: {cfg.tts_backend!r}")

    if cfg.cache_ttl <= 0:
        return tts
    return CachedTts(tts, cfg.cache_dir, cfg.cache_ttl, cfg.cache_max_mb * 1024 * 1024)


def build_effects(cfg: Config) -> Effects:
    if cfg.effects_backend == "ffmpeg":
        return FfmpegEffects(cfg.alsa_device)
    if cfg.effects_backend == "null":
        return NullEffects()
    raise ValueError(f"Unknown KRZYKACZ_EFFECTS backend: {cfg.effects_backend!r}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.from_env()
    light = build_light(cfg)
    tts = build_tts(cfg)
    effects = build_effects(cfg)

    announcer = Announcer(
        light,
        tts,
        effects,
        assets_dir=cfg.assets_dir,
        history_size=cfg.history_size,
        queue_size=cfg.queue_size,
    )
    announcer.start()

    voices_info = list(cfg.piper_voices) if cfg.tts_backend == "piper" else [cfg.espeak_voice]
    default_voice = cfg.piper_default_voice if cfg.tts_backend == "piper" else cfg.espeak_voice
    voices = functools.partial(describe_voices, cfg.tts_backend, voices_info, default_voice)
    effects = functools.partial(describe_effects, cfg.assets_dir)
    limits = functools.partial(
        describe_limits,
        cfg.history_size,
        cfg.queue_size,
        cfg.rate_limit_interval,
        INTENSITIES,
        STYLES,
    )
    describers = Describers(voices=voices, effects=effects, limits=limits)

    rng = random.Random()
    pickers = Pickers(
        sound=functools.partial(random_sound_msg, rng, cfg.assets_dir),
        curse=functools.partial(random_curse_msg, rng, voices_info),
    )

    # Shared across HTTP and MCP so a caller's per-IP budget is the same
    # regardless of which transport it uses to trigger the light/speaker.
    rate_limiter = IpRateLimiter(cfg.rate_limit_interval)

    if cfg.http_enabled:
        # Binds here on the main thread, so a port clash fails loudly at
        # startup rather than inside the worker thread.
        http_server = build_http_server(
            cfg.http_host,
            cfg.http_port,
            cfg.auth_token,
            announcer.submit,
            describers,
            pickers,
            announcer.snapshot,
            rate_limiter,
        )
        _start_thread("http-server", http_server.serve_forever)
        logger.info("HTTP endpoint listening on %s:%d", cfg.http_host, cfg.http_port)

    if cfg.mcp_enabled:
        from .mcp_server import run_mcp_server

        _start_thread(
            "mcp-server",
            run_mcp_server,
            cfg.mcp_host,
            cfg.mcp_port,
            cfg.auth_token,
            announcer.submit,
            describers,
            pickers,
            rate_limiter,
        )
        logger.info("Starting MCP endpoint on %s:%d", cfg.mcp_host, cfg.mcp_port)

    logger.info(
        "krzykacz starting: server=%s topic=%s light=%s tts=%s (voices=%s, default=%s) "
        "effects=%s assets_dir=%s",
        cfg.ntfy_server,
        cfg.topic,
        cfg.light_backend,
        cfg.tts_backend,
        voices_info,
        default_voice,
        cfg.effects_backend,
        cfg.assets_dir,
    )

    listen(cfg.ntfy_server, cfg.topic, announcer.submit)


if __name__ == "__main__":
    main()
