from __future__ import annotations

import logging

from .announcer import Announcer
from .config import Config
from .effects import Effects, FfmpegEffects, NullEffects
from .light import Light, NullLight, UhubctlLight
from .ntfy import listen
from .tts import EspeakTts, PiperTts, Tts

logger = logging.getLogger(__name__)


def build_light(cfg: Config) -> Light:
    if cfg.light_backend == "uhubctl":
        return UhubctlLight(cfg.uhubctl_location, cfg.uhubctl_port)
    if cfg.light_backend == "null":
        return NullLight()
    raise ValueError(f"Unknown KRZYKACZ_LIGHT backend: {cfg.light_backend!r}")


def build_tts(cfg: Config) -> Tts:
    if cfg.tts_backend == "piper":
        return PiperTts(cfg.piper_voices, cfg.piper_default_voice, cfg.alsa_device)
    if cfg.tts_backend == "espeak":
        return EspeakTts(cfg.espeak_voice, cfg.alsa_device)
    raise ValueError(f"Unknown KRZYKACZ_TTS backend: {cfg.tts_backend!r}")


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
    logger.info(
        "krzykacz starting: server=%s topic=%s light=%s tts=%s (voices=%s, default=%s) "
        "effects=%s assets_dir=%s",
        cfg.ntfy_server,
        cfg.topic,
        cfg.light_backend,
        cfg.tts_backend,
        voices_info,
        cfg.piper_default_voice if cfg.tts_backend == "piper" else cfg.espeak_voice,
        cfg.effects_backend,
        cfg.assets_dir,
    )

    listen(cfg.ntfy_server, cfg.topic, announcer.submit)


if __name__ == "__main__":
    main()
