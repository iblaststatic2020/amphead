"""Which plugin, which knob.

A plugin chain is user data, so resolving a name is a safety function, not a convenience. The
ranking is a ladder and each rung is a whole tier: exact name, then exact name with the format
prefix stripped, then every query word present in the name, then the query as a subsequence.
Within the best tier that matched, more than one hit is refused and the candidates are handed back
to the caller so the refusal sentence can name them. Taking the first fuzzy hit is how a plugin
edits the wrong control and sounds plausible while doing it.

One deliberate deviation from PHASE1.md: an exact name wins outright even when a longer name also
contains the query, so "gain" on a plugin with Gain and Output Gain resolves to Gain instead of
refusing. That is the rule the bridge already shipped and tested, and it is the one a player
expects when they type the exact name of a control they can see. Ambiguity is still refused
whenever two controls genuinely match at the same tier, which is the case that would otherwise
pick a coin flip.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import re
from typing import Any, Iterable, Sequence

from . import bridge

PREFIXES = ("vst3:", "vst:", "js:", "au:", "dx:", "clap:", "lv2:")

EXACT, TOKENS, FUZZY, SUBSEQUENCE = 0, 1, 2, 3


@dataclass(frozen=True)
class Resolved:
    """One control, named and indexed. The index is a hint; the name is the truth."""

    index: int
    name: str
    tier: int = EXACT

    def sentence(self) -> str:
        return f"{self.name} (#{self.index})"


@dataclass(frozen=True)
class Ambiguous:
    """Several controls matched equally well. Refusing is the only honest answer."""

    query: str
    candidates: tuple[tuple[int, str], ...]
    what: str = "control"

    def sentence(self) -> str:
        listed = ", ".join(f"#{i} {name}" for i, name in self.candidates)
        return (f"{self.query!r} matches {len(self.candidates)} {self.what}s: {listed}. "
                f"Name one of them exactly.")


@dataclass(frozen=True)
class NotFound:
    query: str
    suggestions: tuple[str, ...] = ()
    what: str = "control"

    def sentence(self) -> str:
        if self.suggestions:
            return (f"no {self.what} matches {self.query!r}. Closest names: "
                    + ", ".join(self.suggestions))
        return f"no {self.what} matches {self.query!r}"


Outcome = Resolved | Ambiguous | NotFound


def clean(name: Any) -> str:
    """Lowercase, single-spaced. REAPER hands back padded names often enough to matter."""
    return " ".join(str(name or "").split()).lower()


def strip_prefix(name: Any) -> str:
    text = clean(name)
    for prefix in PREFIXES:
        if text.startswith(prefix):
            return " ".join(text[len(prefix):].split())
    return text


def tokens(text: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _subsequence(needle: str, haystack: str) -> bool:
    """'proq' should find 'Pro-Q 4'. Squashed letters, in order."""
    wanted = re.sub(r"[^a-z0-9]", "", needle.lower())
    if not wanted:
        return False
    have = re.sub(r"[^a-z0-9]", "", haystack.lower())
    it = iter(have)
    return all(char in it for char in wanted)


def _fuzzy_words(wanted: set[str], name: str, floor: float = 0.8) -> bool:
    """Every query word appears in the name, allowing one misspelled vendor word."""
    have = tokens(name)
    for word in wanted:
        if word in have:
            continue
        if not any(difflib.SequenceMatcher(None, word, other).ratio() >= floor for other in have):
            return False
    return True


def _extra_words(query: str, name: str) -> int:
    """How much more the name says than the query. Closer names sort first in a refusal."""
    return len(tokens(name) - tokens(query))


def best_matches(items: Sequence[tuple[int, str]],
                 query: str) -> tuple[int, list[tuple[int, str]]]:
    """The tier that hit and the matches at that tier, ordered closest first."""
    want = clean(query)
    if not want:
        return EXACT, []

    exact = [(i, n) for i, n in items if clean(n) == want]
    if exact:
        return EXACT, exact

    stripped = [(i, n) for i, n in items if strip_prefix(n) == want]
    if stripped:
        return EXACT, stripped

    wanted_tokens = tokens(want)
    if wanted_tokens:
        contained = [(i, n) for i, n in items if wanted_tokens <= tokens(n)]
        if contained:
            return TOKENS, sorted(contained,
                                  key=lambda pair: (_extra_words(want, pair[1]), len(pair[1])))

        # Vendors misspell their own parameters: the real amp ships "Pedal Ouput Gain". One
        # letter off is still that word, so each query word may match a word within a ratio.
        fuzzy = [(i, n) for i, n in items if _fuzzy_words(wanted_tokens, n)]
        if fuzzy:
            return FUZZY, sorted(fuzzy,
                                 key=lambda pair: (_extra_words(want, pair[1]), len(pair[1])))

    return SUBSEQUENCE, sorted([(i, n) for i, n in items if _subsequence(want, strip_prefix(n))],
                               key=lambda pair: (_extra_words(want, pair[1]), len(pair[1])))


def _choose(query: str, items: Sequence[tuple[int, str]], what: str) -> Outcome:
    if not items:
        return NotFound(query=query, what=what)
    tier, hits = best_matches(items, query)
    if len(hits) == 1:
        return Resolved(index=hits[0][0], name=hits[0][1], tier=tier)
    if len(hits) > 1:
        return Ambiguous(query=query, candidates=tuple(hits), what=what)
    return NotFound(query=query, suggestions=_suggestions(query, items), what=what)


def _suggestions(query: str, items: Sequence[tuple[int, str]], limit: int = 3,
                 floor: float = 0.45) -> tuple[str, ...]:
    """Say what the plugin actually has, but only when it is close enough to be a typo.

    Ranking by shared words alone produced nonsense on the real amp: asking for "presence" on a
    plugin with Wet, Oomph and Delta offered all three as "closest". Character similarity is the
    honest measure, and below the floor the right answer is to suggest nothing.
    """
    want = clean(query)
    scored = []
    for _index, name in items:
        # A one-word query may be one letter off a word inside a long name. A multi-word query
        # has to be close as a whole, or "nam modern" gets offered "NAM Universal" just because
        # both start with nam.
        if len(want.split()) > 1:
            candidates = [strip_prefix(name)]
        else:
            candidates = [strip_prefix(name)] + re.findall(r"[a-z0-9]+", strip_prefix(name))
        ratio = max(difflib.SequenceMatcher(None, want, text).ratio() for text in candidates)
        if ratio >= floor:
            scored.append((ratio, name))
    scored.sort(key=lambda pair: -pair[0])
    seen: list[str] = []
    for _ratio, name in scored:
        if name not in seen:
            seen.append(name)
    return tuple(seen[:limit])


def chain_items(chain: Iterable[dict]) -> list[tuple[int, str]]:
    """(index, name) pairs from a chain, tolerating both FX_LIST and TRACK_INFO shapes."""
    items = []
    for position, fx in enumerate(chain):
        index = fx.get("i", fx.get("index", position))
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = position
        items.append((index, str(fx.get("name") or "")))
    return items


def param_items(params: Iterable[dict]) -> list[tuple[int, str]]:
    items = []
    for position, param in enumerate(params):
        index = param.get("i", position)
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = position
        items.append((index, str(param.get("name") or "")))
    return items


def resolve_fx(query: str, track: int = 1, chain: list[dict] | None = None,
               **kwargs: Any) -> Outcome:
    """Find one plugin on a track by name. Reads the chain unless the caller already has it."""
    if chain is None:
        chain = bridge.fx_list(track=track, **kwargs)
    return _choose(query, chain_items(chain), what="plugin")


def resolve_param(query: str, params: list[dict] | None = None, track: int = 1,
                  fx: int = 0, **kwargs: Any) -> Outcome:
    """Find one parameter on one plugin by name."""
    if params is None:
        params = bridge.fx_params(track=track, fx=fx, **kwargs)
    return _choose(query, param_items(params), what="parameter")


def recheck(chain: list[dict], resolved: Resolved) -> Outcome:
    """Confirm a name still lives at that index, because adding or removing FX shifts indices.

    Writing to index 2 because it used to be the amp is exactly the kind of wrong that sounds
    plausible, so the name gets confirmed against a fresh read before any write.
    """
    items = chain_items(chain)
    for index, name in items:
        if index == resolved.index and clean(name) == clean(resolved.name):
            return resolved
    still_there = [(i, n) for i, n in items if clean(n) == clean(resolved.name)]
    if len(still_there) == 1:
        return Resolved(index=still_there[0][0], name=still_there[0][1], tier=resolved.tier)
    if len(still_there) > 1:
        return Ambiguous(query=resolved.name, candidates=tuple(still_there), what="plugin")
    return NotFound(query=resolved.name, suggestions=_suggestions(resolved.name, items),
                    what="plugin")
