#!/usr/bin/env python3
"""
Dictionary-based word predictor using bisect for fast prefix lookups.
Loads /usr/share/dict/american-english and returns top-N suggestions
for a given prefix, sorted by word length (shorter = shown first).
"""

import bisect
import sys as _sys
import os as _os


def _default_dict_path() -> str:
    if getattr(_sys, "frozen", False):
        p = _os.path.join(_sys._MEIPASS, "words.txt")
        if _os.path.exists(p):
            return p
    local = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "words.txt")
    if _os.path.exists(local):
        return local
    return "/usr/share/dict/american-english"


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
    def __init__(self, dict_path: str = None):
        self._words: list[str] = []
        self._common: set[str] = COMMON_WORDS
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

    def predict(self, prefix: str, n: int = 5) -> list[str]:
        """Return up to n word suggestions for the given prefix."""
        if not prefix or len(prefix) < 1:
            return []

        prefix = prefix.lower()
        words = self._words

        # Binary search to find where this prefix starts
        idx = bisect.bisect_left(words, prefix)

        # Collect all words that start with the prefix
        candidates: list[str] = []
        for i in range(idx, len(words)):
            w = words[i]
            if not w.startswith(prefix):
                break
            candidates.append(w)
            if len(candidates) >= 500:   # cap scan window for speed
                break

        if not candidates:
            return []

        # Sort: common words first, then by length, then alphabetically
        def sort_key(w: str) -> tuple:
            return (0 if w in self._common else 1, len(w), w)

        candidates.sort(key=sort_key)
        return candidates[:n]

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

    def predict_next(self, prev_word: str, n: int = 5) -> list[str]:
        """Predict the next word after prev_word using a bigram table,
        falling back to TOP_WORDS when no bigram entry exists."""
        key = prev_word.lower().strip()
        candidates = list(BIGRAMS.get(key, []))
        seen = set(candidates)
        for w in TOP_WORDS:
            if len(candidates) >= n:
                break
            if w not in seen:
                candidates.append(w)
                seen.add(w)
        return candidates[:n]

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
                scored.append((dist, 0 if word in self._common else 1,
                                -shared, wlen, word))

        scored.sort()
        return [w for _, _, _, _, w in scored[:n]]
