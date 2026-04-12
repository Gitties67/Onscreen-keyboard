#!/usr/bin/env python3
"""
Dictionary-based word predictor using bisect for fast prefix lookups.
Loads /usr/share/dict/american-english and returns top-N suggestions
for a given prefix, sorted by word length (shorter = shown first).
"""

import bisect

DICT_PATH = "/usr/share/dict/american-english"

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
    "off", "need", "house", "try", "again", "animal", "point", "mother",
    "world", "near", "build", "self", "earth", "father", "head", "stand",
    "own", "page", "should", "country", "found", "answer", "school",
    "plant", "cover", "food", "sun", "four", "between", "state", "keep",
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
    "during", "hundred", "five", "remember", "step", "early", "hold",
    "west", "ground", "interest", "reach", "fast", "verb", "sing", "listen",
    "six", "table", "travel", "less", "morning", "ten", "simple", "several",
    "vowel", "toward", "war", "lay", "against", "pattern", "slow", "center",
    "love", "person", "money", "serve", "appear", "road", "map", "rain",
    "rule", "govern", "pull", "cold", "notice", "voice", "unit", "power",
    "town", "fine", "drive", "lead", "cry", "dark", "machine", "note",
    "wait", "plan", "figure", "star", "box", "noun", "field", "rest",
    "able", "pound", "done", "beauty", "drive", "stood", "contain",
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
    "hunt", "probable", "bed", "brother", "egg", "ride", "cell", "believe",
    "fraction", "forest", "sit", "race", "window", "store", "summer",
    "train", "sleep", "prove", "lone", "leg", "exercise", "wall", "catch",
    "mount", "wish", "sky", "board", "joy", "winter", "sat", "written",
    "wild", "instrument", "kept", "glass", "grass", "cow", "job",
    "edge", "sign", "visit", "past", "soft", "fun", "bright", "gas",
    "weather", "month", "million", "bear", "finish", "happy", "hope",
    "flower", "clothe", "strange", "gone", "jump", "baby", "eight",
    "village", "meet", "root", "buy", "raise", "solve", "metal", "whether",
    "push", "seven", "paragraph", "third", "shall", "held", "hair",
    "describe", "cook", "floor", "either", "result", "burn", "hill",
    "safe", "cat", "century", "consider", "type", "law", "bit", "coast",
    "copy", "phrase", "silent", "tall", "sand", "soil", "roll", "temperature",
    "finger", "industry", "value", "fight", "lie", "beat", "excite",
    "natural", "view", "sense", "capital", "except", "expect", "sister",
    "charge", "possible", "rather", "until", "mouth",
}


class WordPredictor:
    def __init__(self, dict_path: str = DICT_PATH):
        self._words: list[str] = []
        self._common: set[str] = COMMON_WORDS
        self._load(dict_path)

    def _load(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read().splitlines()
        except FileNotFoundError:
            print(f"[predictor] Dictionary not found: {path}")
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
        print(f"[predictor] Loaded {len(self._words):,} words")

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
            if len(candidates) >= 200:   # cap scan window for speed
                break

        if not candidates:
            return []

        # Sort: common words first, then by length, then alphabetically
        def sort_key(w: str) -> tuple:
            return (0 if w in self._common else 1, len(w), w)

        candidates.sort(key=sort_key)
        return candidates[:n]
