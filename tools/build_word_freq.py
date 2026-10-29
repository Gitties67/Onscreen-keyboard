#!/usr/bin/env python3
"""Build word_freq.txt — dictionary words ordered by how common they are.

Source: FrequencyWords by Hermit Dave (OpenSubtitles 2018, en_50k.txt),
https://github.com/hermitdave/FrequencyWords — content licensed CC BY-SA 4.0.
The generated word_freq.txt is a derivative and is therefore also CC BY-SA 4.0.

Usage:
    curl -LO https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt
    python3 tools/build_word_freq.py en_50k.txt
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_WORDS = 30000

# Subtitles over-represent swearing; keep these in the dictionary but don't
# boost them into the top suggestions.
NO_BOOST = {
    "fuck", "fucking", "fucked", "fucker", "fuckin", "shit", "shitty", "bitch",
    "bitches", "bastard", "ass", "asshole", "damn", "dick", "cock", "pussy",
    "whore", "slut", "crap", "piss", "pissed", "bloody", "hell", "goddamn",
    "motherfucker", "nigger", "nigga", "fag", "faggot", "cunt", "twat", "wanker",
    "retard", "retarded", "sex", "sexy", "porn", "boobs", "tits", "dumbass",
}


def main(src: str) -> None:
    with open(os.path.join(HERE, "words.txt"), encoding="utf-8") as f:
        dictionary = {w.strip().lower() for w in f if w.strip().isalpha()}

    out, seen = [], set()
    with open(src, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            w = parts[0].lower()
            if (w in seen or w in NO_BOOST or not w.isalpha() or w not in dictionary
                    or (len(w) == 1 and w not in ("a", "i"))):
                continue
            seen.add(w)
            out.append(w)
            if len(out) >= MAX_WORDS:
                break

    dest = os.path.join(HERE, "word_freq.txt")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("# Most-common-first English words. Derived from FrequencyWords by\n"
                "# Hermit Dave (https://github.com/hermitdave/FrequencyWords),\n"
                "# CC BY-SA 4.0. Regenerate with tools/build_word_freq.py.\n")
        f.write("\n".join(out) + "\n")
    print(f"wrote {len(out)} words to {dest}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "en_50k.txt")
