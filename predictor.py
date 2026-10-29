#!/usr/bin/env python3
"""
Dictionary-based word predictor using bisect for fast prefix lookups.
Loads /usr/share/dict/american-english and returns top-N suggestions
for a given prefix, ranked by real-world word frequency (word_freq.txt),
then by length for words outside the frequency list.

Context-aware ranking: when the caller passes the words already typed in the
current sentence, candidates are scored with an interpolated trigram/bigram
phrase model (ngrams.txt, built from Tatoeba sentences — see
tools/build_ngrams.py) backed off to the unigram frequency rank, plus an
optional boost from the user's own typing history (UserModel).
"""

import bisect
import json
import re
import sys as _sys
import os as _os
import time


def _data_path(name: str) -> str | None:
    """Locate a bundled data file (PyInstaller bundle first, then next to this file)."""
    if getattr(_sys, "frozen", False):
        p = _os.path.join(_sys._MEIPASS, name)
        if _os.path.exists(p):
            return p
    local = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), name)
    return local if _os.path.exists(local) else None


def _default_dict_path() -> str:
    return _data_path("words.txt") or "/usr/share/dict/american-english"


def _load_freq_ranks() -> dict[str, int]:
    """word → rank (0 = most common) from word_freq.txt; empty if missing."""
    path = _data_path("word_freq.txt")
    if not path:
        print("[predictor] word_freq.txt not found — ranking by length only")
        return {}
    ranks: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if w and not w.startswith("#") and w not in ranks:
                    ranks[w] = len(ranks)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[predictor] Could not read word_freq.txt: {exc}")
        return {}
    return ranks


# Rank given to words outside the frequency list (sorts after every ranked word)
_UNRANKED = 1 << 30

START = "<s>"           # sentence-start token used in ngrams.txt
_ZIPF_C = 0.137         # unigram P(rank) ~= _ZIPF_C / (rank + 20); sums to ~1 over 30k words


def _load_ngrams(path: str | None = None) -> tuple[dict, dict]:
    """Load ngrams.txt → (bigram_table, trigram_table).

    Each table maps a context string ("w1" or "w1 w2") to (total, raw) where raw
    is the unparsed "w:c w:c ..." continuation string — parsed lazily per lookup
    so start-up stays fast and memory small. Returns empty tables if the file is
    missing or unreadable.
    """
    if path is None:
        path = _data_path("ngrams.txt")
    bi: dict = {}
    tri: dict = {}
    if not path:
        print("[predictor] ngrams.txt not found — no phrase model")
        return bi, tri
    table = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line[0] == "#":
                    continue
                if line == "[2]":
                    table = bi
                elif line == "[3]":
                    table = tri
                elif table is not None:
                    parts = line.split("\t")
                    if len(parts) == 3:
                        try:
                            total = int(parts[1])
                            if total > 0:   # guard against a corrupt/hand-edited file
                                table[parts[0]] = (total, parts[2])
                        except ValueError:
                            pass
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[predictor] Could not read ngrams.txt: {exc}")
        return {}, {}
    return bi, tri


def _parse_conts(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for tok in raw.split():
        w, _, c = tok.rpartition(":")
        if w and c.isdigit():
            out[w] = int(c)
    return out


_TOKEN_RE = re.compile(r"^[a-z]{1,20}$")


class UserModel:
    """Learns the user's own word usage: unigram, bigram and trigram counts.

    Privacy: only lowercase alphabetic words of at most 20 characters that the
    dictionary (or custom word list) knows are ever counted; digits, symbols and
    unknown strings break the context chain and are dropped. Raw text is never
    kept — only per-word counts. Everything stays in one local JSON file.

    Persistence: atomic (tmp file + os.replace), size-capped (counts are halved
    when MAX_ENTRIES is exceeded so rarely-used entries decay away), and loading
    tolerates a missing/corrupt/hand-edited file by starting empty.
    """

    MAX_ENTRIES = 20000
    MAX_FILE_BYTES = 8 * 1024 * 1024
    VERSION = 1

    def __init__(self, path: str | None, is_known=None):
        self.path = path
        self._is_known = is_known       # callable(word) -> bool, or None = accept all alpha
        self.u1: dict[str, int] = {}
        self.u2: dict[str, dict[str, int]] = {}
        self.u3: dict[str, dict[str, int]] = {}
        self.n1 = 0                     # total unigram events
        self._entries = 0
        self.dirty = False
        self.load()

    # ── learning ────────────────────────────────────────────────────────────
    def _ok(self, w: str) -> bool:
        if not _TOKEN_RE.match(w):
            return False
        return self._is_known is None or bool(self._is_known(w))

    def observe(self, word: str, context=()) -> bool:
        """Record `word` typed after `context` (earlier words of the sentence,
        oldest first). Returns True if anything was learned."""
        w = (word or "").lower()
        if not self._ok(w):
            return False
        # keep only the trailing run of valid context words (max 2)
        ctx: list[str] = []
        for c in reversed([x.lower() for x in (context or ())][-2:]):
            if not self._ok(c):
                break
            ctx.append(c)
        ctx.reverse()

        if w not in self.u1:
            self._entries += 1
        self.u1[w] = self.u1.get(w, 0) + 1
        self.n1 += 1
        if ctx:
            self._bump(self.u2, ctx[-1], w)
            if len(ctx) == 2:
                self._bump(self.u3, ctx[0] + " " + ctx[1], w)
        self.dirty = True
        if self._entries > self.MAX_ENTRIES:
            self._decay()
        return True

    def _bump(self, table: dict, key: str, w: str) -> None:
        d = table.setdefault(key, {})
        if w not in d:
            self._entries += 1
        d[w] = d.get(w, 0) + 1

    def _recount(self) -> None:
        self.n1 = sum(self.u1.values())
        self._entries = (len(self.u1) + sum(len(d) for d in self.u2.values())
                         + sum(len(d) for d in self.u3.values()))

    def _decay(self) -> None:
        """Halve every count and drop entries that reach zero."""
        for _ in range(4):
            self.u1 = {w: c // 2 for w, c in self.u1.items() if c // 2 > 0}
            for table in (self.u2, self.u3):
                for k in list(table):
                    d = {w: c // 2 for w, c in table[k].items() if c // 2 > 0}
                    if d:
                        table[k] = d
                    else:
                        del table[k]
            self._recount()
            if self._entries <= self.MAX_ENTRIES * 0.8:
                break

    # ── querying ────────────────────────────────────────────────────────────
    def weight(self) -> float:
        """How much to trust the user model vs the bundled one (0..0.6)."""
        return 0.6 * self.n1 / (self.n1 + 300.0)

    def prob(self, w: str, ctx: list[str]) -> float:
        """Interpolated user probability of w after ctx (real words only)."""
        if not self.n1:
            return 0.0
        p = 0.2 * self.u1.get(w, 0) / self.n1
        if ctx:
            t1 = self.u1.get(ctx[-1], 0)
            if t1:
                p += 0.3 * self.u2.get(ctx[-1], {}).get(w, 0) / t1
            if len(ctx) >= 2:
                d = self.u3.get(ctx[-2] + " " + ctx[-1])
                t2 = self.u2.get(ctx[-2], {}).get(ctx[-1], 0)
                if d and t2:
                    p += 0.5 * d.get(w, 0) / t2
        return p

    def successors(self, ctx: list[str]) -> set[str]:
        out: set[str] = set()
        if ctx:
            out.update(self.u2.get(ctx[-1], ()))
            if len(ctx) >= 2:
                out.update(self.u3.get(ctx[-2] + " " + ctx[-1], ()))
        return out

    def words_with_prefix(self, prefix: str, limit: int = 20) -> list[str]:
        hits = [w for w in self.u1 if w.startswith(prefix)]
        hits.sort(key=lambda w: -self.u1[w])
        return hits[:limit]

    # ── persistence ─────────────────────────────────────────────────────────
    def load(self) -> None:
        self.u1, self.u2, self.u3 = {}, {}, {}
        self.n1 = self._entries = 0
        if not self.path:
            return
        try:
            if _os.path.getsize(self.path) > self.MAX_FILE_BYTES:
                return
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            u1 = self._clean_counts(data["u1"])
            u2 = self._clean_nested(data["u2"], 1)
            u3 = self._clean_nested(data["u3"], 2)
        except (OSError, ValueError, KeyError, TypeError, AttributeError,
                UnicodeDecodeError, RecursionError):
            return   # missing or corrupt → start empty (next save overwrites it)
        self.u1, self.u2, self.u3 = u1, u2, u3
        self._recount()
        if self._entries > self.MAX_ENTRIES:
            self._decay()

    @staticmethod
    def _clean_counts(d) -> dict[str, int]:
        if not isinstance(d, dict):
            raise TypeError("counts must be an object")
        return {w: c for w, c in d.items()
                if isinstance(w, str) and _TOKEN_RE.match(w)
                and isinstance(c, int) and not isinstance(c, bool) and 0 < c < 10**9}

    @classmethod
    def _clean_nested(cls, d, ctx_words: int) -> dict[str, dict[str, int]]:
        if not isinstance(d, dict):
            raise TypeError("table must be an object")
        out = {}
        for k, sub in d.items():
            if not isinstance(k, str):
                continue
            parts = k.split(" ")
            if len(parts) != ctx_words or not all(_TOKEN_RE.match(p) for p in parts):
                continue
            sub = cls._clean_counts(sub)
            if sub:
                out[k] = sub
        return out

    def save(self, force: bool = False) -> bool:
        """Atomic write. No-op unless there are unsaved changes (or force)."""
        if not self.path or not (self.dirty or force):
            return False
        tmp = self.path + ".tmp"
        try:
            _os.makedirs(_os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": self.VERSION, "u1": self.u1,
                           "u2": self.u2, "u3": self.u3}, f, separators=(",", ":"))
                f.flush()
                _os.fsync(f.fileno())
            _os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[predictor] Could not save learned words: {exc}")
            try:
                _os.remove(tmp)
            except OSError:
                pass
            return False
        self.dirty = False
        return True

    def forget(self) -> None:
        """Erase everything learned, in memory and on disk."""
        self.u1, self.u2, self.u3 = {}, {}, {}
        self.n1 = self._entries = 0
        self.dirty = False
        if self.path:
            for p in (self.path, self.path + ".tmp"):
                try:
                    _os.remove(p)
                except OSError:
                    pass

    def count(self) -> int:
        return len(self.u1)


def _osa_distance(s: str, t: str, max_dist: int) -> int:
    """
    Optimal String Alignment distance (handles transpositions as 1 edit).
    Returns the distance, or max_dist+1 if it would exceed the threshold
    (early-exit optimisation — avoids filling the full matrix).
    """
    m, n = len(s), len(t)
    if abs(m - n) > max_dist:
        return max_dist + 1

    # Use a flat list rather than a list-of-lists for speed
    INF = max_dist + 1
    prev2 = list(range(n + 1))   # d[i-2]
    prev1 = list(range(n + 1))   # d[i-1]  (pre-filled as base case row 0)
    curr  = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        row_min = INF
        for j in range(1, n + 1):
            cost = 0 if s[i - 1] == t[j - 1] else 1
            val = min(
                prev1[j] + 1,        # deletion
                curr[j - 1] + 1,     # insertion
                prev1[j - 1] + cost, # substitution
            )
            # Transposition
            if i > 1 and j > 1 and s[i - 1] == t[j - 2] and s[i - 2] == t[j - 1]:
                val = min(val, prev2[j - 2] + cost)
            curr[j] = val
            if val < row_min:
                row_min = val
        if row_min > max_dist:
            return INF   # whole row exceeds threshold — no point continuing
        prev2, prev1 = prev1, curr
        curr = [0] * (n + 1)

    return prev1[n]

# Ordered fallback words shown when there are no prefix matches / after a space.
# Front of list = most likely to be useful.
TOP_WORDS = [
    "the", "a", "I", "to", "and", "is", "it", "in", "you", "that",
    "he", "was", "for", "on", "are", "with", "as", "his", "they", "be",
    "at", "one", "have", "this", "from", "or", "had", "by", "not", "but",
    "what", "all", "were", "we", "when", "your", "can", "said", "there",
    "use", "an", "each", "which", "she", "do", "how", "their", "if", "will",
    "up", "other", "about", "out", "many", "then", "them", "so", "some",
    "her", "would", "make", "like", "him", "into", "time", "has", "look",
    "two", "more", "go", "see", "no", "way", "could", "people", "my",
    "than", "first", "been", "its", "who", "now", "did", "get", "come",
    "made", "may", "part", "over", "new", "sound", "take", "only", "little",
    "work", "know", "place", "years", "live", "back", "give", "most", "very",
]

# Simple bigram table — words that commonly follow a given word.
# Used for next-word prediction after the user presses space.
BIGRAMS: dict[str, list[str]] = {
    "i":        ["am", "will", "have", "can", "think", "know", "want", "need", "feel", "was"],
    "the":      ["same", "best", "most", "first", "last", "only", "next", "new", "right", "time"],
    "a":        ["lot", "good", "new", "few", "big", "little", "great", "small", "long", "bit"],
    "is":       ["a", "the", "not", "very", "good", "really", "just", "so", "also", "still"],
    "it":       ["is", "was", "will", "has", "can", "should", "would", "might", "seems", "looks"],
    "you":      ["are", "can", "will", "have", "should", "know", "want", "need", "were", "might"],
    "he":       ["was", "is", "had", "has", "will", "would", "could", "did", "said", "went"],
    "she":      ["was", "is", "had", "has", "will", "would", "could", "did", "said", "went"],
    "they":     ["are", "were", "have", "had", "will", "would", "can", "could", "did", "said"],
    "we":       ["are", "were", "have", "had", "will", "can", "should", "need", "want", "went"],
    "and":      ["the", "a", "I", "it", "then", "also", "so", "they", "you", "we"],
    "to":       ["be", "get", "do", "go", "make", "have", "see", "take", "come", "know"],
    "in":       ["the", "a", "this", "my", "your", "our", "their", "some", "many", "that"],
    "for":      ["the", "a", "this", "that", "your", "my", "all", "some", "now", "years"],
    "that":     ["the", "a", "it", "this", "he", "she", "they", "I", "we", "is"],
    "with":     ["the", "a", "my", "your", "his", "her", "their", "me", "him", "them"],
    "on":       ["the", "a", "my", "your", "this", "that", "it", "top", "time", "each"],
    "was":      ["a", "the", "not", "very", "so", "just", "also", "still", "already", "now"],
    "have":     ["a", "the", "been", "had", "to", "never", "always", "just", "not", "also"],
    "this":     ["is", "was", "will", "has", "can", "could", "might", "seems", "would", "means"],
    "but":      ["the", "a", "I", "it", "not", "also", "still", "then", "now", "when"],
    "at":       ["the", "a", "this", "that", "least", "all", "home", "work", "first", "once"],
    "from":     ["the", "a", "my", "your", "this", "that", "there", "here", "now", "all"],
    "not":      ["the", "a", "only", "just", "very", "really", "too", "even", "always", "sure"],
    "can":      ["be", "get", "do", "go", "make", "have", "see", "take", "come", "help"],
    "if":       ["you", "the", "a", "it", "I", "we", "they", "he", "she", "that"],
    "my":       ["name", "life", "work", "time", "new", "best", "old", "own", "first", "last"],
    "your":     ["name", "life", "work", "time", "new", "best", "own", "first", "last", "email"],
    "what":     ["is", "are", "was", "do", "did", "would", "could", "should", "the", "a"],
    "when":     ["the", "a", "I", "you", "we", "they", "it", "this", "that", "he"],
    "how":      ["are", "is", "do", "did", "can", "much", "many", "long", "often", "well"],
    "do":       ["you", "not", "the", "it", "that", "this", "we", "they", "I", "some"],
    "so":       ["the", "a", "I", "it", "much", "many", "good", "far", "long", "well"],
    "all":      ["the", "of", "that", "this", "those", "these", "right", "over", "good", "well"],
    "would":    ["be", "have", "not", "like", "love", "make", "take", "give", "say", "tell"],
    "could":    ["be", "have", "not", "also", "still", "just", "really", "even", "always", "never"],
    "should":   ["be", "have", "not", "also", "just", "really", "always", "never", "still", "only"],
    "just":     ["a", "the", "be", "do", "get", "want", "need", "know", "say", "go"],
    "there":    ["is", "are", "was", "were", "will", "might", "could", "should", "have", "has"],
    "more":     ["than", "and", "of", "the", "a", "like", "about", "to", "time", "work"],
    "some":     ["of", "the", "people", "time", "good", "great", "new", "more", "other", "other"],
    "good":     ["morning", "night", "luck", "job", "idea", "time", "thing", "news", "day", "work"],
    "love":     ["you", "it", "this", "that", "the", "my", "your", "our", "how", "when"],
    "like":     ["a", "the", "this", "that", "it", "what", "how", "when", "you", "me"],
    "get":      ["the", "a", "it", "that", "this", "more", "better", "back", "out", "up"],
    "go":       ["to", "back", "out", "on", "up", "down", "ahead", "home", "away", "there"],
    "time":     ["to", "for", "is", "was", "will", "and", "the", "of", "now", "soon"],
    "know":     ["that", "what", "how", "when", "where", "why", "if", "it", "the", "you"],
    "think":    ["that", "about", "it", "the", "I", "you", "we", "this", "so", "of"],
    "said":     ["the", "a", "I", "it", "that", "this", "he", "she", "they", "we"],
    "hey":      ["there", "you", "buddy", "friend", "man", "girl", "guys", "everyone"],
    "hi":       ["there", "everyone", "guys", "all", "friend"],
    "hello":    ["there", "everyone", "world", "friend"],
    "thanks":   ["for", "so", "a", "you", "much", "again", "very"],
    "thank":    ["you", "god"],
    "please":   ["let", "make", "be", "do", "note", "send", "check", "help", "try"],
    "ok":       ["so", "now", "great", "good", "fine", "sure", "then", "well"],
    "okay":     ["so", "now", "great", "good", "fine", "sure", "then", "well"],
    "no":       ["one", "way", "more", "longer", "problem", "thanks", "doubt", "matter"],
    "yes":      ["please", "of", "that", "this", "it", "I", "you", "we"],
    "as":       ["a", "the", "well", "much", "many", "long", "soon", "far", "good"],
    "been":     ["a", "the", "very", "so", "quite", "really", "already", "always", "never"],
}

# Top 500 most common English words — boosted to the front of suggestions
COMMON_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "it",
    "for", "not", "on", "with", "he", "as", "you", "do", "at", "this",
    "but", "his", "by", "from", "they", "we", "say", "her", "she", "or",
    "an", "will", "my", "one", "all", "would", "there", "their", "what",
    "so", "up", "out", "if", "about", "who", "get", "which", "go", "me",
    "when", "make", "can", "like", "time", "no", "just", "him", "know",
    "take", "people", "into", "year", "your", "good", "some", "could",
    "them", "see", "other", "than", "then", "now", "look", "only", "come",
    "its", "over", "think", "also", "back", "after", "use", "two", "how",
    "our", "work", "first", "well", "way", "even", "new", "want", "because",
    "any", "these", "give", "day", "most", "us", "great", "between", "need",
    "large", "often", "hand", "high", "place", "hold", "turn", "help",
    "much", "before", "move", "right", "boy", "old", "too", "same", "tell",
    "does", "set", "three", "air", "play", "small", "end", "put", "home",
    "read", "port", "spell", "add", "land", "here", "must", "big", "such",
    "follow", "act", "why", "ask", "men", "change", "went", "light", "kind",
    "off", "house", "try", "again", "animal", "point", "mother",
    "world", "near", "build", "self", "earth", "father", "head", "stand",
    "own", "page", "should", "country", "found", "answer", "school",
    "plant", "cover", "food", "sun", "four", "state", "keep",
    "eye", "never", "last", "let", "thought", "city", "tree", "cross",
    "farm", "hard", "start", "might", "story", "saw", "far", "sea",
    "draw", "left", "late", "run", "while", "press", "close", "night",
    "real", "life", "few", "north", "open", "seem", "together", "next",
    "white", "children", "begin", "got", "walk", "example", "ease",
    "paper", "group", "always", "music", "those", "both", "mark",
    "book", "carry", "took", "science", "eat", "room", "friend", "began",
    "idea", "fish", "mountain", "stop", "once", "base", "hear", "horse",
    "cut", "sure", "watch", "color", "face", "wood", "main", "enough",
    "plain", "girl", "usual", "young", "ready", "above", "ever", "red",
    "list", "though", "feel", "talk", "bird", "soon", "body", "dog",
    "family", "direct", "pose", "leave", "song", "measure", "door",
    "product", "black", "short", "numeral", "class", "wind", "question",
    "happen", "complete", "ship", "area", "half", "rock", "order", "fire",
    "south", "problem", "piece", "told", "knew", "pass", "since", "top",
    "whole", "king", "space", "heard", "best", "hour", "better", "true",
    "during", "hundred", "five", "remember", "step", "early",
    "west", "ground", "interest", "reach", "fast", "verb", "sing", "listen",
    "six", "table", "travel", "less", "morning", "ten", "simple", "several",
    "vowel", "toward", "war", "lay", "against", "pattern", "slow", "center",
    "love", "person", "money", "serve", "appear", "road", "map", "rain",
    "rule", "govern", "pull", "cold", "notice", "voice", "unit", "power",
    "town", "fine", "drive", "lead", "cry", "dark", "machine", "note",
    "wait", "plan", "figure", "star", "box", "noun", "field", "rest",
    "able", "pound", "done", "beauty", "stood", "contain",
    "front", "teach", "week", "final", "gave", "green", "quick", "develop",
    "ocean", "warm", "free", "minute", "strong", "special", "behind",
    "clear", "tail", "produce", "fact", "street", "inch", "multiply",
    "nothing", "course", "stay", "wheel", "full", "force", "blue", "object",
    "decide", "surface", "deep", "moon", "island", "foot", "system",
    "busy", "test", "record", "boat", "common", "gold", "possible",
    "plane", "instead", "dry", "wonder", "laugh", "thousand", "ago",
    "ran", "check", "game", "shape", "equate", "miss", "brought", "heat",
    "snow", "tire", "bring", "yes", "distant", "fill", "east", "paint",
    "language", "among", "grand", "ball", "yet", "wave", "drop", "heart",
    "present", "heavy", "dance", "engine", "position", "arm", "wide",
    "sail", "material", "size", "vary", "settle", "speak", "weight",
    "general", "ice", "matter", "circle", "pair", "include", "divide",
    "syllable", "felt", "perhaps", "pick", "sudden", "count", "square",
    "reason", "length", "represent", "art", "subject", "region", "energy",
    "hunt", "probable", "bed", "brother", "egg", "ride", "cell",
    "fraction", "forest", "sit", "race", "window", "store", "summer",
    "train", "sleep", "prove", "lone", "leg", "exercise", "wall", "catch",
    "mount", "wish", "sky", "board", "joy", "winter", "sat", "written",
    "wild", "instrument", "kept", "glass", "grass", "cow", "job",
    "edge", "sign", "visit", "past", "soft", "fun", "bright", "gas",
    "weather", "month", "million", "bear", "finish", "happy", "hope",
    "flower", "clothe", "strange", "gone", "jump", "baby", "eight",
    "village", "meet", "root", "buy", "raise", "solve", "metal",
    "push", "seven", "paragraph", "third", "shall", "held", "hair",
    "describe", "cook", "floor", "either", "result", "burn", "hill",
    "safe", "cat", "century", "consider", "type", "law", "bit", "coast",
    "copy", "phrase", "silent", "tall", "sand", "soil", "roll", "temperature",
    "finger", "industry", "value", "fight", "lie", "beat", "excite",
    "natural", "view", "sense", "capital", "except", "expect", "sister",
    "charge", "rather", "mouth",
    # Frequently-misspelled words — boosted so fuzzy ranking finds them first
    "receive", "believe", "achieve", "relieve", "retrieve",
    "necessary", "separate", "definitely", "occurred", "occurrence",
    "accommodate", "beginning", "committee", "conscience", "conscientious",
    "convenient", "experience", "government", "independent", "immediately",
    "knowledge", "library", "license", "maintenance", "millennium",
    "occasion", "persistent", "possession", "privilege",
    "professional", "recommend", "referred", "relevant", "restaurant",
    "rhythm", "schedule", "secretary", "succeed", "successful",
    "surprise", "tendency", "therefore", "tomorrow", "transferred",
    "truly", "until", "usually", "vacuum", "whether", "writing",
}


class WordPredictor:
    def __init__(self, dict_path: str = None, ngrams_path: str = None):
        self._words: list[str] = []
        self._wordset: set[str] = set()
        self._common: set[str] = COMMON_WORDS
        self._rank: dict[str, int] = _load_freq_ranks()
        # Phrase model (ngrams.txt). Empty tables → falls back to BIGRAMS/TOP_WORDS.
        self._bi, self._tri = _load_ngrams(ngrams_path)
        self._user: UserModel | None = None
        self._prefix_cache: dict[str, list[str]] = {}
        self._top_cache: list[str] = []
        # Positional pseudo-frequency for the legacy fallback word list
        self._fallback_pos: dict[str, int] = {}
        for i, w in enumerate(TOP_WORDS):
            self._fallback_pos.setdefault(w.lower(), i)
        self._custom: list[str] = []   # user-defined words/phrases (original casing)
        self._by_first: dict[str, list[str]] = {}  # first-char → word list (for fuzzy)
        self.dict_missing: bool = False
        self._load(dict_path if dict_path is not None else _default_dict_path())

    def _load(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read().splitlines()
        except FileNotFoundError:
            print(f"[predictor] Dictionary not found: {path}")
            self.dict_missing = True
            return

        seen: set[str] = set()
        words: list[str] = []
        for w in raw:
            w = w.lower()
            # Keep only alphabetic words, 2–15 chars, no possessives/plurals edge cases
            if w.isalpha() and 2 <= len(w) <= 15 and w not in seen:
                seen.add(w)
                words.append(w)

        self._words = sorted(words)
        self._wordset = set(self._words)
        self._prefix_cache.clear()

        # Build first-character index for fast fuzzy candidate filtering
        by_first: dict[str, list[str]] = {}
        for w in self._words:
            by_first.setdefault(w[0], []).append(w)
        self._by_first = by_first

        print(f"[predictor] Loaded {len(self._words):,} words")

    def set_custom_words(self, words: list[str]) -> None:
        """Replace the custom word/phrase list."""
        self._custom = list(words)

    def custom_matches(self, prefix: str) -> list[str]:
        """Return custom entries whose text starts with prefix (case-insensitive)."""
        if not prefix:
            return []
        p = prefix.lower()
        return [w for w in self._custom if w.lower().startswith(p)]

    # ── User model glue ─────────────────────────────────────────────────────

    def is_known(self, word: str) -> bool:
        """True if word is a dictionary word or a custom word (case-insensitive)."""
        w = word.lower()
        return w in self._wordset or w in ("i", "a") or any(c.lower() == w for c in self._custom)

    def set_user_model(self, model: "UserModel | None") -> None:
        """Attach (or detach, with None) the learned-words model used for boosting."""
        self._user = model

    # ── Prediction ──────────────────────────────────────────────────────────

    def predict(self, prefix: str, n: int = 5, context=None) -> list[str]:
        """Return up to n completions of `prefix`, best first.

        context: words already typed in the current sentence (oldest first).
        None = no context information (plain frequency ranking). An empty
        sequence means "at the start of a sentence".
        """
        if not prefix:
            return []
        prefix = prefix.lower()
        if context is None and not (self._user and self._user.n1):
            return self._prefix_top(prefix, n)
        return self._rank_candidates(prefix, context, n)

    def predict_next(self, context=(), n: int = 5) -> list[str]:
        """Predict the next word given the sentence so far (oldest first).
        An empty context means the start of a sentence. A bare string is
        accepted as a one-word context (legacy call style)."""
        if isinstance(context, str):
            context = [context] if context.strip() else []
        return self._rank_candidates("", context, n)

    def _prefix_top(self, prefix: str, n: int, limit: int = 40) -> list[str]:
        """Dictionary words starting with prefix, by frequency rank (cached)."""
        cached = self._prefix_cache.get(prefix)
        if cached is None or len(cached) < n:
            words = self._words
            idx = bisect.bisect_left(words, prefix)
            # Slice the whole prefix range — capping the scan would only ever see
            # the alphabetically-first words and miss short common ones.
            end = bisect.bisect_left(words, prefix + "\uffff", idx)
            cands = words[idx:end]
            cands.sort(key=lambda w: (self._word_rank(w), len(w), w))
            cached = cands[:max(limit, n)]
            if len(self._prefix_cache) > 4000:
                self._prefix_cache.clear()
            self._prefix_cache[prefix] = cached
        return cached[:n]

    def _p_unigram(self, w: str) -> float:
        r = self._rank.get(w)
        if r is not None:
            return _ZIPF_C / (r + 20)
        pos = self._fallback_pos.get(w)
        if pos is not None:
            return 0.01 / (1 + pos)
        return 2e-6 if w in self._common else 1e-6

    @staticmethod
    def _lookup(table: dict, key: str):
        e = table.get(key)
        return (e[0], _parse_conts(e[1])) if e else None

    def _rank_candidates(self, prefix: str, context, n: int) -> list[str]:
        """Score candidates with interpolated trigram/bigram/unigram
        probabilities (+ user model) and return the best n."""
        # Normalise context → at least [<s>, w] or [<s>]; None = no context.
        ctx: list[str] | None = None
        if context is not None:
            ctx = [w.lower().strip() for w in context if w and w.strip()][-2:]
            if len(ctx) < 2:
                ctx = [START] + ctx

        t3 = t2 = None
        if ctx is not None:
            t2 = self._lookup(self._bi, ctx[-1])
            if len(ctx) >= 2:
                t3 = self._lookup(self._tri, ctx[-2] + " " + ctx[-1])
            if t2 is None and not self._bi and ctx[-1] in BIGRAMS:
                # Legacy fallback: hand-written table, pseudo-counts by position
                legacy = BIGRAMS[ctx[-1]]
                t2 = (100, {w.lower(): 10 - i for i, w in enumerate(legacy[:10])})
        c3 = t3[1] if t3 else {}
        c2 = t2[1] if t2 else {}
        a3 = t3[0] / (t3[0] + 2.0) if t3 else 0.0
        a2 = (t2[0] / (t2[0] + 8.0) if t2 else 0.0) if self._bi else 0.8
        T3 = t3[0] if t3 else 1
        T2 = t2[0] if t2 else 1

        user = self._user if (self._user and self._user.n1) else None
        real_ctx = [w for w in (ctx or []) if w != START]

        # ── candidate set ────────────────────────────────────────────────
        if prefix:
            cands = set(self._prefix_top(prefix, 30))
            # Phrase-model words must be real dictionary words (or contractions)
            # so one-letter tokens like "i"/"a" never complete a typed prefix.
            for table in (c3, c2):
                cands.update(w for w in table if w.startswith(prefix)
                             and (w in self._wordset or "'" in w))
            if user:
                cands.update(user.words_with_prefix(prefix))
        else:
            cands = set(c3)
            cands.update(c2)
            cands.update(self._top_words(60))
            if user:
                cands.update(user.successors(real_ctx))
        cands.discard(START)
        cands.discard("<unk>")

        mu = user.weight() if user else 0.0
        scored = []
        for w in cands:
            p = self._p_unigram(w)
            if ctx is not None:
                p = a2 * (c2.get(w, 0) / T2) + (1 - a2) * p
                p = a3 * (c3.get(w, 0) / T3) + (1 - a3) * p
            if user:
                p = (1 - mu) * p + mu * user.prob(w, real_ctx)
            scored.append((-p, len(w), w))
        scored.sort()
        out = [w for _, _, w in scored[:n]]
        if not prefix:
            # "i", "i'm", "i'll" … are stored lowercase in the model
            out = ["I" + w[1:] if (w == "i" or w.startswith("i'")) else w for w in out]
        return out

    def _top_words(self, k: int) -> list[str]:
        """The k most common words (by rank; legacy TOP_WORDS if no rank file)."""
        if self._rank:
            if len(self._top_cache) != k:
                self._top_cache = sorted(self._rank, key=self._rank.get)[:k]
            return self._top_cache
        return [w.lower() for w in TOP_WORDS[:k]]

    def _word_rank(self, w: str) -> int:
        r = self._rank.get(w)
        if r is not None:
            return r
        return _UNRANKED if w in self._common else _UNRANKED + 1

    def predict_padded(self, prefix: str, n: int = 5) -> list[str]:
        """Like predict() but always returns exactly n results, padding with
        common words when there aren't enough prefix matches."""
        results = self.predict(prefix, n) if prefix else []
        seen = set(results)
        for w in TOP_WORDS:
            if len(results) >= n:
                break
            if w not in seen:
                results.append(w)
                seen.add(w)
        return results[:n]

    def fuzzy_predict(self, prefix: str, n: int = 3) -> list[str]:
        """
        Spell-check suggestions for prefix using OSA distance.

        Compares prefix against the first len(prefix) characters of each
        candidate word so that "reciev" correctly matches "receive".
        Only activates for prefixes of 3+ characters.

        Edit-distance thresholds:
          3–4 chars → 1 allowed error
          5+  chars → 2 allowed errors
        """
        if len(prefix) < 3:
            return []

        prefix = prefix.lower()
        plen   = len(prefix)
        max_d  = 1 if plen <= 4 else 2

        # Candidates: same first letter, word length within max_d+1 of prefix length.
        # We compare the full typed prefix against the full word, so the length
        # difference alone bounds the minimum possible edit distance.
        bucket = self._by_first.get(prefix[0], [])

        scored: list[tuple[int, int, int, str]] = []
        for word in bucket:
            wlen = len(word)
            if abs(wlen - plen) > max_d + 1:
                continue
            # Compare prefix against the full word (not truncated).
            # This correctly handles "speling" → "spelling" (1 insertion)
            # whereas prefix-truncation gives distance 2.
            dist = _osa_distance(prefix, word, max_d)
            if dist == 0:
                continue   # exact match (shouldn't happen for fuzzy path, but guard)
            if dist <= max_d:
                # Count shared leading characters — words that share more of the
                # typed prefix rank higher (e.g. "receive" beats "racier" for "reciev")
                shared = 0
                for ca, cb in zip(prefix, word):
                    if ca != cb:
                        break
                    shared += 1
                scored.append((dist, self._word_rank(word),
                                -shared, wlen, word))

        scored.sort()
        return [w for _, _, _, _, w in scored[:n]]
