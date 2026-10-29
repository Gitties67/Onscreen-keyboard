"""Korean (Hangul) input for the on-screen keyboard — no external deps.

Keys use the standard 2-set (Dubeolsik) layout: each Latin letter key types a
jamo (consonant/vowel letter), and jamo join into syllable blocks:
ㅎ + ㅏ + ㄴ → 한.  HangulComposer holds the syllable being built (shown in the
suggestion bar, not yet typed into the app) and hands back finished syllables
for the keyboard to type.  Nothing in here touches GTK or the target app, so it
is unit-testable on its own.
"""

# Dubeolsik: Latin key → jamo, and the shifted (doubled / extra vowel) forms
DUBEOL = dict(zip("qwertyuiopasdfghjklzxcvbnm",
                  "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ"))
DUBEOL_SHIFT = {"q": "ㅃ", "w": "ㅉ", "e": "ㄸ", "r": "ㄲ", "t": "ㅆ", "o": "ㅒ", "p": "ㅖ"}

CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"                 # 19 initial consonants
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"           # 21 vowels
JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ",
        "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ",
        "ㅍ", "ㅎ"]                                           # 27 finals + none

VOWEL_PAIRS = {("ㅗ", "ㅏ"): "ㅘ", ("ㅗ", "ㅐ"): "ㅙ", ("ㅗ", "ㅣ"): "ㅚ",
               ("ㅜ", "ㅓ"): "ㅝ", ("ㅜ", "ㅔ"): "ㅞ", ("ㅜ", "ㅣ"): "ㅟ",
               ("ㅡ", "ㅣ"): "ㅢ"}
FINAL_PAIRS = {("ㄱ", "ㅅ"): "ㄳ", ("ㄴ", "ㅈ"): "ㄵ", ("ㄴ", "ㅎ"): "ㄶ",
               ("ㄹ", "ㄱ"): "ㄺ", ("ㄹ", "ㅁ"): "ㄻ", ("ㄹ", "ㅂ"): "ㄼ",
               ("ㄹ", "ㅅ"): "ㄽ", ("ㄹ", "ㅌ"): "ㄾ", ("ㄹ", "ㅍ"): "ㄿ",
               ("ㄹ", "ㅎ"): "ㅀ", ("ㅂ", "ㅅ"): "ㅄ"}
_SPLIT_VOWEL = {v: k for k, v in VOWEL_PAIRS.items()}
_SPLIT_FINAL = {v: k for k, v in FINAL_PAIRS.items()}


def jamo_for(key: str, shift: bool = False) -> str:
    """Jamo typed by a Latin letter key ("" if the key isn't a letter)."""
    k = key.lower()
    if shift and k in DUBEOL_SHIFT:
        return DUBEOL_SHIFT[k]
    return DUBEOL.get(k, "")


def compose(cho: str, jung: str, jong: str = "") -> str:
    """One syllable block from its parts (all three must be valid)."""
    return chr(0xAC00 + (CHO.index(cho) * 21 + JUNG.index(jung)) * 28 + JONG.index(jong))


class HangulComposer:
    """The syllable being typed: initial consonant, vowel, final consonant."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.cho = self.jung = self.jong = ""

    def preedit(self) -> str:
        """What's being built, as display text ("" when nothing)."""
        if self.cho and self.jung:
            return compose(self.cho, self.jung, self.jong)
        return self.cho + self.jung

    def flush(self) -> str:
        """Finish the current syllable: return it and start empty."""
        out = self.preedit()
        self.reset()
        return out

    def feed(self, j: str) -> str:
        """Add one jamo. Returns any text that is now final (to be typed)."""
        if j in JUNG:
            return self._vowel(j)
        if j in CHO:
            return self._consonant(j)
        return ""

    def _consonant(self, j: str) -> str:
        if self.cho and self.jung:
            if not self.jong:
                if j in JONG:                      # ㄸ ㅃ ㅉ can't be finals
                    self.jong = j
                    return ""
            elif (self.jong, j) in FINAL_PAIRS:
                self.jong = FINAL_PAIRS[(self.jong, j)]
                return ""
        out = self.flush()                         # start the next syllable
        self.cho = j
        return out

    def _vowel(self, j: str) -> str:
        if self.jong:
            # The final consonant moves to the new syllable: 한 + ㅏ → 하 + 나
            if self.jong in _SPLIT_FINAL:
                keep, move = _SPLIT_FINAL[self.jong]
            else:
                keep, move = "", self.jong
            self.jong = keep
            out = self.flush()
            self.cho, self.jung = move, j
            return out
        if self.jung:
            if (self.jung, j) in VOWEL_PAIRS:
                self.jung = VOWEL_PAIRS[(self.jung, j)]
                return ""
            out = self.flush()
            self.jung = j
            return out
        self.jung = j                              # after a consonant, or alone
        return ""

    def backspace(self) -> bool:
        """Remove the last jamo typed. False if there was nothing to remove."""
        if self.jong:
            self.jong = _SPLIT_FINAL.get(self.jong, ("",))[0]
        elif self.jung:
            self.jung = _SPLIT_VOWEL.get(self.jung, ("",))[0]
        elif self.cho:
            self.cho = ""
        else:
            return False
        return True
