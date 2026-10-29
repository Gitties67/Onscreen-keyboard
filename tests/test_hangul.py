"""Tests for hangul.py (Korean syllable composition). No GTK needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hangul import HangulComposer, jamo_for  # noqa: E402


def type_keys(keys: str) -> tuple[str, str]:
    """Feed Latin keys (uppercase = shifted); return (typed text, preedit)."""
    c = HangulComposer()
    out = ""
    for k in keys:
        out += c.feed(jamo_for(k, k.isupper()))
    return out, c.preedit()


class HangulTests(unittest.TestCase):
    def test_words(self):
        for keys, want in {"gksrnr": "한국", "dkssudgktpdy": "안녕하세요",
                           "tkfkd": "사랑", "rkatkgkqslek": "감사합니다",
                           "dhk": "와", "dnjs": "원", "ekfr": "닭", "Tkd": "쌍"}.items():
            out, pre = type_keys(keys)
            self.assertEqual(out + pre, want, keys)

    def test_final_moves_to_next_syllable(self):
        self.assertEqual(type_keys("gks"), ("", "한"))
        self.assertEqual(type_keys("gksk"), ("하", "나"))
        self.assertEqual(type_keys("ekfrk"), ("달", "가"))   # ㄺ splits: 달 + 가

    def test_lone_jamo(self):
        self.assertEqual(type_keys("rr"), ("ㄱ", "ㄱ"))
        self.assertEqual(type_keys("k"), ("", "ㅏ"))
        self.assertEqual(type_keys("kk"), ("ㅏ", "ㅏ"))

    def test_double_consonant_is_not_a_final(self):
        self.assertEqual(type_keys("gkE"), ("하", "ㄸ"))

    def test_backspace_steps_back_one_jamo(self):
        c = HangulComposer()
        for k in "ekfr":                 # 닭
            c.feed(jamo_for(k))
        steps = []
        while c.backspace():
            steps.append(c.preedit())
        self.assertEqual(steps, ["달", "다", "ㄷ", ""])
        c.feed(jamo_for("d")); c.feed(jamo_for("h")); c.feed(jamo_for("k"))   # 와
        c.backspace()
        self.assertEqual(c.preedit(), "오")

    def test_flush(self):
        c = HangulComposer()
        for k in "gks":
            c.feed(jamo_for(k))
        self.assertEqual(c.flush(), "한")
        self.assertEqual(c.preedit(), "")
        self.assertFalse(c.backspace())

    def test_non_letter_keys_ignored(self):
        self.assertEqual(jamo_for("1"), "")
        self.assertEqual(HangulComposer().feed(""), "")


if __name__ == "__main__":
    unittest.main()
