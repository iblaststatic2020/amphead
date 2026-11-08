"""Rank what you own against a rig the agent researched.

The agent's job is to turn "AC/DC" into words: an amp family, a cabinet, maybe a mic position.
This module's job is to find those words in the file names of captures and IRs already on the
machine, and to be honest when they are not there.

File names are the only index a capture carries. That is a thin signal, so every score reports the
words that matched and the words that did not. A match you cannot inspect is a guess with a
decimal point on it.
"""

from __future__ import annotations

import re

# A researched rig rarely uses the word a file does. These groups mean "any of these is that amp".
AMP_GROUPS = {
    "plexi": ("plexi", "superlead", "super lead", "1959", "jtm", "jmp", "marshall"),
    "jcm800": ("jcm", "800", "2203", "2204", "marshall"),
    "jvm": ("jvm", "marshall"),
    "rectifier": ("rectifier", "recto", "dualrec", "mesa", "boogie"),
    "mark": ("mark", "mk", "boogie", "mesa"),
    "5150": ("5150", "6505", "peavey", "evh"),
    "ac30": ("ac30", "ac", "vox"),
    "twin": ("twin", "fender", "deluxe", "princeton", "bassman"),
    "orange": ("orange", "thunderverb", "rockerverb", "tiny terror"),
    "soldano": ("soldano", "slo"),
    "friedman": ("friedman", "be", "brown eye"),
    "bogner": ("bogner", "uberschall", "ecstasy"),
    "diezel": ("diezel", "vht", "herbert", "vhl"),
}

# Speaker names are the sharpest thing in an IR file name, and the biggest single difference.
CAB_GROUPS = {
    # "1960" is deliberately not here: that Marshall box shipped with Greenbacks, with G12T-75s
    # and with V30s, so treating it as a Greenback synonym matched a V30 cab as exact.
    "greenback": ("greenback", "g12m", "g12m25"),
    "vintage30": ("vintage30", "v30", "vintage 30"),
    "creamback": ("creamback", "g12h", "g12h75"),
    "g12t75": ("g12t", "75"),
    "alnico": ("alnico", "blue", "gold"),
    "ev12l": ("ev", "evm", "electrovoice"),
}

# A close mic is what most recorded guitar is, so it outranks a room capture for this purpose.
MIC_WORDS = ("close", "sm57", "57", "cap", "cone", "edge", "bright", "dark")

CONFIDENCE_EXACT = "exact"
CONFIDENCE_FAMILY = "family"
CONFIDENCE_NEAREST = "nearest"


def words(text: str) -> list[str]:
    """Lowercase alphanumeric words. "Vintage 30 4x12 close" -> ["vintage","30","4x12","close"]."""
    return [w for w in re.split(r"[^a-z0-9]+", str(text).lower()) if w]


def _expand(value: str) -> tuple[str, ...]:
    """Turn a researched word into the group of file-name words that could stand for it."""
    key = "".join(words(value))
    for name, variants in AMP_GROUPS.items():
        if key == name or key in { "".join(words(v)) for v in variants }:
            return variants
    for name, variants in CAB_GROUPS.items():
        if key == name or key in { "".join(words(v)) for v in variants }:
            return variants
    return (str(value),)


def _squash(text: str) -> str:
    """All words run together: "Vintage 30 4x12 close" -> "vintage304x12close"."""
    return "".join(words(text))


def _hits(tokens: list[str], variants: tuple[str, ...]) -> list[str]:
    """Which variants actually appear in the file name.

    Four characters or more may match inside a run-together name, because files write "G12M" and
    "G12MGreenback" and "Superlead100". Three characters or fewer must be a whole word, because
    "30" inside "3000" and "ac" inside "acoustic" are how a matcher lies to you.
    """
    joined = "".join(tokens)
    found = []
    for variant in variants:
        squashed = _squash(variant)
        if not squashed:
            continue
        if len(squashed) >= 4:
            if squashed in joined:
                found.append(variant)
        elif squashed in tokens:
            found.append(variant)
    return found


def score(name: str, want: dict, kind: str = "captures") -> dict:
    """How well one file name answers one researched rig.

    `want` holds the human words: {"amp": "plexi", "cab": "greenback", "mic": "close"}.
    A capture is an amp, so only the amp word is scored against it; a cab is a speaker, so only the
    cab word is. Scoring both against both is how "Marshall 57" becomes a cabinet that answers a
    question about an amp.
    """
    tokens = words(name)
    amp = _hits(tokens, _expand(want.get("amp", ""))) if want.get("amp") and kind == "captures" else []
    cab = _hits(tokens, _expand(want.get("cab", ""))) if want.get("cab") and kind == "cabs" else []
    mic = [w for w in MIC_WORDS if w in tokens] if want.get("mic") else []

    useful = bool(amp or cab)
    points = 0.0
    points += 2.0 if amp else 0.0
    points += 1.5 if cab else 0.0
    points += 0.5 if mic else 0.0
    total = points / 4.0

    missing = []
    if want.get("amp") and kind == "captures" and not amp:
        missing.append(f"amp:{want['amp']}")
    if want.get("cab") and kind == "cabs" and not cab:
        missing.append(f"cab:{want['cab']}")

    if not missing and useful:
        confidence = CONFIDENCE_EXACT
    elif useful:
        confidence = CONFIDENCE_FAMILY
    else:
        confidence = CONFIDENCE_NEAREST

    return {
        "name": name,
        "score": round(total, 3),
        "useful": useful,
        "matched": amp + cab + mic,
        "missing": missing,
        "confidence": confidence,
    }


def rank(items: list, want: dict, limit: int = 3, kind: str = "captures") -> list[dict]:
    """Score a library listing and return the best few, with the reasoning attached.

    `items` may be dicts with a "name" key (what `library.scan` returns) or bare strings.
    A name that only matched a mic word is not a candidate, it is coincidence.
    """
    scored = []
    for item in items:
        name = item.get("name", "") if isinstance(item, dict) else str(item)
        if not name:
            continue
        entry = score(name, want, kind)
        if not entry["useful"]:
            continue
        if isinstance(item, dict):
            entry = {**item, **entry}
        scored.append(entry)

    scored.sort(key=lambda entry: (-entry["score"], entry["name"].lower()))
    return scored[:limit]


def verdict(found: list[dict], want: dict, kind: str = "captures") -> str:
    """One line for the face. Never says "this is X", only how far the library got."""
    noun = "cab" if kind == "cabs" else "capture"
    if not found:
        return f"no {noun} in the library answers that rig"
    best = found[0]
    if best["confidence"] == CONFIDENCE_EXACT:
        return f"{noun} matched: {best['name']}"
    if best["confidence"] == CONFIDENCE_FAMILY:
        return f"same family, not the same {noun}: {best['name']}"
    return f"only a neighbour: {best['name']}"
