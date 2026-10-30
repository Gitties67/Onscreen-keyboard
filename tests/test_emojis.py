"""Tests for emojis.py text faces (":)" → 🙂). No GTK needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emojis import EMOJI_DATA, emoticon  # noqa: E402


class EmoticonTests(unittest.TestCase):
    def test_faces(self):
        self.assertEqual(emoticon(":)"), (":)", "🙂"))
        self.assertEqual(emoticon(":-)"), (":-)", "🙂"))   # longest wins
        self.assertEqual(emoticon(">:("), (">:(", "😠"))
        self.assertEqual(emoticon("<3"), ("<3", "❤️"))
        self.assertEqual(emoticon("XD"), ("XD", "😆"))

    def test_after_a_word(self):
        self.assertEqual(emoticon("hi:)"), (":)", "🙂"))
        self.assertIsNone(emoticon("eXD"))        # a word ending in XD
        self.assertIsNone(emoticon("https:/"))   # not a URL
        self.assertIsNone(emoticon("10:3"))
        self.assertIsNone(emoticon("abc"))

    def test_eggplant(self):
        self.assertEqual(emoticon("8=D"), ("8=D", "🍆"))
        self.assertEqual(emoticon("8=====D"), ("8=====D", "🍆"))
        self.assertEqual(emoticon("8==D~"), ("8==D~", "🍆💦"))

    def test_no_duplicate_emoji(self):
        chars = [c for c, _n, _k in EMOJI_DATA]
        self.assertEqual(len(chars), len(set(chars)))


if __name__ == "__main__":
    unittest.main()
