#!/usr/bin/env python3
"""Build ngrams.txt — a compact bigram/trigram phrase model for word prediction.

Source: Tatoeba English sentences, https://tatoeba.org
(eng_sentences.tsv.bz2 from https://downloads.tatoeba.org/exports/per_language/eng/),
licensed CC BY 2.0 FR (https://creativecommons.org/licenses/by/2.0/fr/).
The generated ngrams.txt is a derivative (aggregate word-sequence counts) and
carries the same attribution requirement.

Usage:
    curl -LO https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2
    python3 -I tools/build_ngrams.py eng_sentences.tsv.bz2

The input is treated purely as data: it is parsed, never executed. Use
`python3 -I` and run it from a directory that does not contain the download.

Pipeline: dedupe sentences -> lowercase -> split on . ! ? -> tokenize (alpha
words and a whitelist of apostrophe contractions; everything else, and any word
outside word_freq.txt, becomes <unk>) -> count bigrams/trigrams padded with a
sentence-start token -> prune (min count, top-K continuations per context).

Context totals are stored BEFORE pruning so probabilities stay correct.
"""
import bz2
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "ngrams.txt")

START = "<s>"
UNK = "<unk>"

# Tuning knobs (size/quality trade-off).
MIN_BI = 4          # drop bigram continuations seen fewer times
MIN_TRI = 5         # drop trigram continuations seen fewer times
MIN_CTX_BI = 20     # drop bigram contexts seen fewer times in total
MIN_CTX_TRI = 16    # drop trigram contexts seen fewer times in total
TOP_BI = 14         # max continuations kept per bigram context
TOP_TRI = 5        # max continuations kept per trigram context

# Contractions we allow as real tokens (everything else with an apostrophe,
# e.g. possessives, becomes <unk>).
CONTRACTIONS = set("""
i'm i'll i've i'd you're you'll you've you'd he's she's it's that's there's
what's who's let's we're we'll we've we'd they're they'll they've they'd
he'll she'll he'd she'd don't doesn't didn't can't couldn't won't wouldn't
shouldn't isn't aren't wasn't weren't haven't hasn't hadn't ain't here's
how's where's mustn't needn't
""".split())

# Tatoeba is dominated by a handful of example names; they would swamp the
# sentence-start statistics, so they are treated as unknown.
NAMES = {"tom", "mary", "john", "ken", "bob", "jim", "mike",
         "tatoeba", "algeria", "algerian", "mennad", "layla", "sami", "fadil",
         "maria", "alice", "boston"}

_SENT_SPLIT = re.compile(r"[.!?]+")
_TOKEN = re.compile(r"[a-z]+(?:'[a-z]+)?|[0-9]+|[^\sa-z0-9]")


def load_vocab() -> set[str]:
    vocab = set()
    with open(os.path.join(HERE, "word_freq.txt"), encoding="utf-8") as f:
        for line in f:
            w = line.strip()
            if w and not w.startswith("#") and w.isalpha():
                vocab.add(w)
    vocab |= CONTRACTIONS
    vocab -= NAMES
    return vocab


def sentences(path: str):
    opener = bz2.open if path.endswith(".bz2") else open
    seen: set[str] = set()
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[1] != "eng":
                continue
            text = parts[2].lower().replace("’", "'").replace("‘", "'")
            if text in seen:
                continue
            seen.add(text)
            for s in _SENT_SPLIT.split(text):
                if s.strip():
                    yield s


def tokenize(sentence: str, vocab: set[str]) -> list[str]:
    out = []
    for tok in _TOKEN.findall(sentence):
        if tok in vocab:
            out.append(tok)
        elif tok[0].isalnum():
            out.append(UNK)
        # bare punctuation (commas, quotes, ...) is transparent
    return out


def main(src: str) -> None:
    vocab = load_vocab()
    bi: Counter = Counter()
    tri: Counter = Counter()
    n_sent = n_tok = 0
    for s in sentences(src):
        toks = tokenize(s, vocab)
        if len(toks) < 2:
            continue
        n_sent += 1
        n_tok += len(toks)
        seq = [START] + toks
        for i in range(1, len(seq)):
            bi[(seq[i - 1], seq[i])] += 1
            if i >= 2:
                tri[(seq[i - 2], seq[i - 1], seq[i])] += 1
    print(f"{n_sent:,} sentences, {n_tok:,} tokens, "
          f"{len(bi):,} distinct bigrams, {len(tri):,} distinct trigrams")

    def emit(f, tag, counter, ctx_len, min_c, min_ctx, top_k):
        ctx_total: dict = defaultdict(int)
        conts: dict = defaultdict(list)
        for key, c in counter.items():
            ctx, w = key[:ctx_len], key[ctx_len]
            ctx_total[ctx] += c
            conts[ctx].append((w, c))
        lines = []
        for ctx, total in ctx_total.items():
            if total < min_ctx or UNK in ctx:
                continue
            kept = [(w, c) for w, c in conts[ctx] if w != UNK and c >= min_c]
            if not kept:
                continue
            kept.sort(key=lambda x: (-x[1], x[0]))
            kept = kept[:top_k]
            lines.append(" ".join(ctx) + "\t" + str(total) + "\t"
                         + " ".join(f"{w}:{c}" for w, c in kept))
        lines.sort()
        f.write(f"[{tag}]\n")
        f.write("\n".join(lines) + "\n")
        return len(lines)

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "# Word phrase model (bigrams + trigrams) for the on-screen keyboard.\n"
            "# Derived from Tatoeba English sentences, https://tatoeba.org,\n"
            "# CC BY 2.0 FR (https://creativecommons.org/licenses/by/2.0/fr/).\n"
            "# Regenerate with tools/build_ngrams.py.\n"
            "#\n"
            "# Format: a '[2]' or '[3]' section header, then one line per context:\n"
            "#   <context words, space-separated>TAB<context total>TAB<w:count w:count ...>\n"
            "# '<s>' is the sentence-start token. Context totals are counted before\n"
            "# pruning; continuations are pruned (min count, top-K) and sorted by count.\n"
        )
        n2 = emit(f, "2", bi, 1, MIN_BI, MIN_CTX_BI, TOP_BI)
        n3 = emit(f, "3", tri, 2, MIN_TRI, MIN_CTX_TRI, TOP_TRI)
    print(f"wrote {OUT}: {n2:,} bigram contexts, {n3:,} trigram contexts, "
          f"{os.path.getsize(OUT) / 1e6:.2f} MB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
