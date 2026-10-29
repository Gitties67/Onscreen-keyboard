#!/usr/bin/env python3
"""Build the regional word lists used for suggestions.

    words.txt        English (US)         = SCOWL american-english (wamerican)
    words_en_GB.txt  English (UK)         = SCOWL british-english  (wbritish)
    words_en_AU.txt  English (Australia)  = hunspell en_AU dictionary, expanded

All three come from SCOWL by Kevin Atkinson (permissive licence, notice in
THIRD_PARTY_NOTICES.txt).  On Debian/Ubuntu/Mint:

    sudo apt install wamerican wbritish hunspell-en-au
    python3 tools/build_dicts.py

The en_AU dictionary is stored as stems + affix flags; this expands it with a
minimal hunspell PFX/SFX implementation (enough for the en_* dictionaries).
"""
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_affixes(aff_path):
    rules = {}          # flag -> (kind, cross, [(strip, add, cond_regex)])
    skip_flags = set()  # NOSUGGEST / ONLYINCOMPOUND words are not suggestions
    with open(aff_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            if parts[0] in ("NOSUGGEST", "ONLYINCOMPOUND", "FORBIDDENWORD") and len(parts) > 1:
                skip_flags.add(parts[1])
            elif parts[0] in ("PFX", "SFX") and len(parts) >= 4:
                kind, flag = parts[0], parts[1]
                if flag not in rules:                # header: PFX A Y 1
                    rules[flag] = (kind, parts[2] == "Y", [])
                    continue
                strip = "" if parts[2] == "0" else parts[2]
                add = "" if parts[3] == "0" else parts[3].split("/")[0]
                cond = parts[4] if len(parts) > 4 else "."
                if cond == ".":
                    rx = None
                elif kind == "SFX":
                    rx = re.compile("(" + cond + ")$")
                else:
                    rx = re.compile("^(" + cond + ")")
                rules[flag][2].append((strip, add, rx))
    return rules, skip_flags


def _apply(word, kind, entries):
    out = []
    for strip, add, rx in entries:
        if rx is not None and not rx.search(word):
            continue
        if kind == "SFX":
            if strip and not word.endswith(strip):
                continue
            out.append((word[: len(word) - len(strip)] if strip else word) + add)
        else:
            if strip and not word.startswith(strip):
                continue
            out.append(add + (word[len(strip):] if strip else word))
    return out


def expand_hunspell(dic_path, aff_path):
    rules, skip_flags = _load_affixes(aff_path)
    words = set()
    with open(dic_path, encoding="utf-8", errors="replace") as f:
        next(f)                                      # first line = entry count
        for line in f:
            line = line.strip()
            if not line:
                continue
            stem, _, flags = line.partition("/")
            if any(fl in skip_flags for fl in flags):
                continue
            words.add(stem)
            sfx = [rules[fl] for fl in flags if fl in rules and rules[fl][0] == "SFX"]
            pfx = [rules[fl] for fl in flags if fl in rules and rules[fl][0] == "PFX"]
            suffixed = []
            for kind, cross, entries in sfx:
                for w in _apply(stem, kind, entries):
                    words.add(w)
                    if cross:
                        suffixed.append(w)
            for kind, cross, entries in pfx:
                words.update(_apply(stem, kind, entries))
                if cross:
                    for w in suffixed:
                        words.update(_apply(w, kind, entries))
    return words


def main():
    shutil.copyfile("/usr/share/dict/british-english", os.path.join(HERE, "words_en_GB.txt"))
    au = expand_hunspell("/usr/share/hunspell/en_AU.dic", "/usr/share/hunspell/en_AU.aff")
    au = sorted(w for w in au if w and not any(c.isdigit() for c in w))
    with open(os.path.join(HERE, "words_en_AU.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(au) + "\n")
    print(f"words_en_AU.txt: {len(au)} words")


if __name__ == "__main__":
    sys.exit(main())
