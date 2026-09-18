from __future__ import annotations

import logging
import random
from typing import Callable, NamedTuple, Optional, Sequence, Tuple

from .metadata import list_effects
from .protocol import Msg, build_msg

logger = logging.getLogger(__name__)

INTENSITIES: Tuple[str, ...] = ("mild", "medium", "strong")
STYLES: Tuple[str, ...] = ("staropolskie", "modern", "funny", "grim")

# Discrete steps rather than a continuous range: CachedTts keys its cache on
# the full (voice, prosody) fingerprint, so a fresh float on every call would
# never hit cache. A handful of steps keeps delivery varied while letting
# repeats land on already-cached audio. Comfortably inside SPEED_RANGE.
_SPEED_STEPS: Tuple[float, ...] = (0.85, 0.95, 1.05, 1.15, 1.3)


class Curse(NamedTuple):
    text: str
    intensity: str  # one of INTENSITIES
    style: str  # one of STYLES


# Researched, not invented -- see the plan's Sources section for citations.
# Every entry already ends in terminal punctuation (in
# announcer._TERMINAL_PUNCTUATION), so announcer._with_terminal_punctuation
# leaves it untouched and Piper keeps the exclamatory intonation.
CURSES: Tuple[Curse, ...] = (
    # funny -- mild euphemisms, the "won't send you to hell" family
    Curse("Motyla noga!", "mild", "funny"),
    Curse("Psia kostka!", "mild", "funny"),
    Curse("Kurza stopa!", "mild", "funny"),
    Curse("Kurczę blade!", "mild", "funny"),
    Curse("Kurczę pieczone!", "mild", "funny"),
    Curse("Kurcze pióro!", "mild", "funny"),
    Curse("Kurza melodia!", "mild", "funny"),
    Curse("Piernik jasny!", "mild", "funny"),
    Curse("Kurtka na wacie!", "mild", "funny"),
    Curse("Na krowie kopytko!", "mild", "funny"),
    Curse("Na rany koguta!", "mild", "funny"),
    Curse("Jasny gwint!", "mild", "funny"),
    Curse("Jasna anielka!", "mild", "funny"),
    Curse("Do jasnej anielki!", "mild", "funny"),
    Curse("Kurka wodna!", "mild", "funny"),
    Curse("Niech to gęś kopnie!", "mild", "funny"),
    Curse("Niech to kaczka kopnie!", "mild", "funny"),
    Curse("O kurna!", "mild", "funny"),
    Curse("W mordę jeża!", "medium", "funny"),
    Curse("Kurna olek!", "medium", "funny"),
    # staropolskie -- archaic; "dunder" = German Donner (piorun), "diasek" and
    # "kaduk" are old names for the devil, "psiajucha" = psiakrew
    Curse("Do diaska!", "mild", "staropolskie"),
    Curse("Do kroćset!", "mild", "staropolskie"),
    Curse("Do kroćset fur beczek!", "mild", "staropolskie"),
    Curse("Przebóg!", "mild", "staropolskie"),
    Curse("Dalibóg!", "mild", "staropolskie"),
    Curse("Tam do licha!", "mild", "staropolskie"),
    Curse("A niech to licho porwie!", "mild", "staropolskie"),
    Curse("Niech to dunder świśnie!", "medium", "staropolskie"),
    Curse("Niech cię dunder świśnie!", "medium", "staropolskie"),
    Curse("A niech cię kaduk porwie!", "medium", "staropolskie"),
    Curse("Do stu tysięcy kartaczy!", "medium", "staropolskie"),
    Curse("Psiajucha!", "medium", "staropolskie"),
    Curse("Psia jucha!", "medium", "staropolskie"),
    Curse("Psiakrew!", "medium", "staropolskie"),
    Curse("Psia mać!", "medium", "staropolskie"),
    Curse("Sakramencka sprawa!", "medium", "staropolskie"),
    Curse("Niech to piorun strzeli!", "medium", "staropolskie"),
    Curse("Bogdaj cię zabito!", "strong", "staropolskie"),
    # grim -- folk klątwy wishing harm; "bodaj" contracts "bóg daj"
    Curse("A bodajżeś nie dorósł!", "medium", "grim"),
    Curse("A bodajeś poleciał z czarnymi krukami!", "medium", "grim"),
    Curse("A żeby cię Pan Bóg skarał!", "medium", "grim"),
    Curse("Niech cię wszyscy diabli wezmą!", "medium", "grim"),
    Curse("A niech to wszyscy diabli!", "medium", "grim"),
    Curse("Niech mnie piekło pochłonie!", "medium", "grim"),
    Curse("Bodaj cię cholera wzięła!", "strong", "grim"),
    Curse("Bodajżeś zdechł!", "strong", "grim"),
    Curse("Bodaj cię szlag trafił!", "strong", "grim"),
    Curse("Żeby cię szlag trafił w samo serce!", "strong", "grim"),
    Curse("Bodajżeś się trząsł całe życie!", "strong", "grim"),
    Curse("Bodaj mu wrzód na ustach zaległ!", "strong", "grim"),
    Curse("Niech cię piekielne ognie pochłoną!", "strong", "grim"),
    Curse("Żeby cię ziemia nie przyjęła!", "strong", "grim"),
    # modern
    Curse("Cholera!", "mild", "modern"),
    Curse("Kurde!", "mild", "modern"),
    Curse("Kurde bele!", "mild", "modern"),
    Curse("Kurna!", "mild", "modern"),
    Curse("Cholera jasna!", "medium", "modern"),
    Curse("Cholera ciężka!", "medium", "modern"),
    Curse("Do cholery!", "medium", "modern"),
    Curse("Szlag by to!", "medium", "modern"),
    Curse("Niech to szlag!", "medium", "modern"),
    Curse("Niech to szlag trafi!", "medium", "modern"),
    Curse("Do diabła!", "medium", "modern"),
    Curse("Niech to diabli wezmą!", "medium", "modern"),
    Curse("Pieprzyć to!", "medium", "modern"),
    Curse("Kurwa mać!", "strong", "modern"),
    Curse("O kurwa!", "strong", "modern"),
    Curse("Kurwa jego mać!", "strong", "modern"),
    Curse("Do kurwy nędzy!", "strong", "modern"),
    Curse("Ja pierdolę!", "strong", "modern"),
)


class Pickers(NamedTuple):
    """The random actions this instance exposes. Sibling of
    krzykacz.metadata.Describers -- same bundling rationale (one argument per
    transport, both transports sharing one instance) -- but these trigger
    playback instead of describing state."""

    sound: Callable[[], Optional[Msg]]
    curse: Callable[[Optional[str], Optional[str]], Optional[Msg]]


def random_sound_msg(rng: random.Random, assets_dir: str) -> Optional[Msg]:
    """A Msg that plays one random curated sound and says nothing.

    "Curated" means no extension: scripts/download_effects.sh deliberately
    strips extensions from the soundboard it installs, so a bare name is a
    deliberately-chosen effect while a name with a dot is a leftover from a
    retired pack that pruning no longer removes. Filtering here keeps the
    random pool from being mostly footstep/UI-click noise; a leftover file is
    still playable by tagging it explicitly. Returns None if there is nothing
    curated to pick (e.g. an empty or unreadable assets directory).

    Also excludes any name containing "<" or ">" -- such a name would break
    out of the f"<{name}>" tag built below and get misparsed by
    protocol.split_effect as a different effect name plus stray spoken text."""
    names = [
        name
        for name in list_effects(assets_dir)
        if "." not in name and "<" not in name and ">" not in name
    ]
    if not names:
        return None
    return build_msg(f"<{rng.choice(names)}>")


def _matching(intensity: Optional[str], style: Optional[str]) -> Tuple[Curse, ...]:
    pool = CURSES
    if intensity is not None:
        pool = tuple(c for c in pool if c.intensity == intensity)
    if style is not None:
        pool = tuple(c for c in pool if c.style == style)
    return pool


def random_curse_msg(
    rng: random.Random,
    voices: Sequence[str],
    intensity: Optional[str] = None,
    style: Optional[str] = None,
) -> Optional[Msg]:
    """A Msg speaking one random przekleństwo in a random voice at a random
    speed. `intensity`/`style` narrow the pool (see INTENSITIES/STYLES); an
    unrecognized value, or a combination matching nothing, is logged and
    widened back to the full library rather than failing the call -- same
    never-fail-the-message stance as protocol._clean_voice. Returns None only
    if there's truly nothing to pick from (no voices configured)."""
    if not voices:
        return None

    pool = _matching(intensity, style)
    if not pool:
        logger.warning(
            "No curse matches intensity=%r style=%r, using the full library", intensity, style
        )
        pool = CURSES

    curse = rng.choice(pool)
    return build_msg(curse.text, voice=rng.choice(voices), speed=rng.choice(_SPEED_STEPS))
