#!/usr/bin/env python3
"""Build the word list and phrase model for a non-English language.

    words_<code>.txt   most-common-first words, in their natural casing
                       (German nouns capitalised: "Haus")
    ngrams_<code>.txt  bigram/trigram phrase model, same format as ngrams.txt

Sources (both treated purely as data, never executed):
  * FrequencyWords by Hermit Dave (OpenSubtitles 2018), CC BY-SA 4.0
    https://github.com/hermitdave/FrequencyWords   (<fw>_50k.txt)
  * Tatoeba sentences, CC BY 2.0 FR, https://tatoeba.org
    https://downloads.tatoeba.org/exports/per_language/<tat>/<tat>_sentences.tsv.bz2

Usage (from a directory that does not contain the downloads):
    python3 -I tools/build_lang.py de /path/de_50k.txt /path/deu_sentences.tsv.bz2

Word list = the 50k subtitle words (frequency order), then words that occur
at least MIN_TATOEBA times in Tatoeba but not in the subtitles.  A word's
casing comes from Tatoeba, counting only mid-sentence occurrences.
"""
import bz2
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ngrams import START, UNK, NAMES, write_model  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# code → Tatoeba language tag (column 2 of the sentences file)
TATOEBA_TAG = {"de": "deu", "fr": "fra", "es": "spa", "it": "ita", "pt": "por", "nl": "nld"}
MIN_TATOEBA = 3
MAX_LEN = 15

_SENT_SPLIT = re.compile(r"[.!?¡¿…]+")
# Letters only; an apostrophe or hyphen ends a token.  French/Italian elided
# particles (l', d', j', dell'...) therefore come out as separate tokens and
# are dropped, the same way the keyboard treats "l'" + "homme".
_TOKEN = re.compile(r"[^\W\d_]+'?|\d+")


# Leftovers of HTML entities in the subtitle text: "voc&ecirc;" → "vocecirc"
_HTML_JUNK = re.compile(r"(acute|grave|circ|uml|tilde|cedil|amp|quot)")


def _fold(w: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", w.replace("ß", "ss"))
                   if not unicodedata.combining(c))


def _ok(w: str) -> bool:
    return w.isalpha() and 2 <= len(w) <= MAX_LEN


def sentences(path: str, tag: str):
    seen: set[str] = set()
    with bz2.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[1] != tag:
                continue
            text = parts[2].replace("’", "'").replace("‘", "'")
            if text in seen:
                continue
            seen.add(text)
            for s in _SENT_SPLIT.split(text):
                if s.strip():
                    yield s


def main(code: str, fw_path: str, tat_path: str) -> None:
    tag = TATOEBA_TAG[code]
    sents = [[t for t in _TOKEN.findall(s) if not t.endswith("'")]
             for s in sentences(tat_path, tag)]

    # Casing evidence (mid-sentence only) and plain counts from Tatoeba
    cap, low, tat_count = Counter(), Counter(), Counter()
    for toks in sents:
        for i, t in enumerate(toks):
            w = t.lower()
            if not _ok(w):
                continue
            tat_count[w] += 1
            if i == 0:
                continue
            if t == w:
                low[w] += 1
            elif t[0].isupper() and t[1:] == w[1:]:
                cap[w] += 1

    fw_count: dict[str, int] = {}
    with open(fw_path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            w = parts[0].lower() if parts else ""
            if _ok(w) and w not in fw_count:
                fw_count[w] = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1

    # Subtitles are full of accent slips ("tres" for "très", "nao" for "não").
    # Among spellings that differ only in accents, keep the most common one;
    # keep a rarer one only if Tatoeba uses it as a real word too (not just
    # the odd typo: at least 1/50 as often as the main spelling there).
    by_fold = defaultdict(list)
    for w, c in fw_count.items():
        by_fold[_fold(w)].append((c, w))
    dropped = {w for w in fw_count if _HTML_JUNK.search(w) and not tat_count[w]}
    # The main spelling is the one Tatoeba uses most (it's curated); with no
    # Tatoeba evidence, the most common subtitle spelling.  It takes the
    # group's best (earliest) rank: "perche" was ranked above "perché".
    winner_at: dict[str, str] = {}          # first-seen group member → winner
    for group in by_fold.values():
        if len(group) > 1:
            main_tat = max(tat_count[w] for _, w in group)
            win = (max(group, key=lambda cw: (tat_count[cw[1]], cw[0]))[1] if main_tat >= 2
                   else max(group)[1])
            dropped.update(w for c, w in group if w != win
                           and (tat_count[w] < 2 or tat_count[w] * 50 < main_tat))
            first = min((w for _, w in group), key=list(fw_count).index)
            if first != win:
                winner_at[first] = win
    words = []
    placed = set()
    for w in fw_count:
        w = winner_at.get(w, w)
        if w not in dropped and w not in placed:
            placed.add(w)
            words.append(w)
    n_fw = len(words)
    seen = set(words) | dropped             # dropped slips stay out
    folded_seen = {_fold(w) for w in words}
    for w, c in tat_count.most_common():
        if c < MIN_TATOEBA:
            break
        # Tatoeba-only words are added unless they're an accent variant of a
        # word we already have (its typos: "perche" next to "perché").
        if w not in seen and w not in NAMES and _fold(w) not in folded_seen:
            seen.add(w)
            folded_seen.add(_fold(w))
            words.append(w)

    def natural(w):
        return w[0].upper() + w[1:] if cap[w] >= 2 and cap[w] > 4 * low[w] else w

    dest = os.path.join(HERE, f"words_{code}.txt")
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# Most-common-first {code} words for the on-screen keyboard.\n"
                "# From FrequencyWords by Hermit Dave (CC BY-SA 4.0) and Tatoeba\n"
                "# sentences (CC BY 2.0 FR); casing from Tatoeba. tools/build_lang.py\n")
        f.write("\n".join(natural(w) for w in words) + "\n")
    print(f"wrote {dest}: {len(words):,} words ({n_fw:,} subtitle + "
          f"{len(words) - n_fw:,} Tatoeba; {len(dropped):,} accent slips dropped), "
          f"{sum(1 for w in words if natural(w) != w):,} capitalised")

    vocab = set(words) - NAMES
    bi, tri = Counter(), Counter()
    for toks in sents:
        seq = [START]
        for t in toks:
            w = t.lower()
            seq.append(w if w in vocab else UNK)
        if len(seq) < 3:
            continue
        for i in range(1, len(seq)):
            bi[(seq[i - 1], seq[i])] += 1
            if i >= 2:
                tri[(seq[i - 2], seq[i - 1], seq[i])] += 1
    write_model(os.path.join(HERE, f"ngrams_{code}.txt"), bi, tri,
                f"Tatoeba {tag} sentences")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in TATOEBA_TAG:
        sys.exit(__doc__)
    main(*sys.argv[1:])
