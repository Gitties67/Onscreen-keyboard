"""Tests for emojis.py text faces (":)" → 🙂). No GTK needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emojis import (EMOJI_DATA, EMOTICONS, emoticon, search,  # noqa: E402
                    set_country_flags, suggest)

PNG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "emoji_png")


def _png(char: str) -> str:
    """Picture file name, as keyboard._emoji_pixbuf builds it."""
    return "emoji_u" + "_".join(f"{ord(c):x}" for c in char if c != "\ufe0f") + ".png"


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


class FlagTests(unittest.TestCase):
    def tearDown(self):
        set_country_flags(True)

    def test_country_name_suggests_flag(self):
        set_country_flags(True)
        self.assertEqual(suggest("germany", 1), ["🇩🇪"])
        self.assertEqual(suggest("german", 1), ["🇩🇪"])
        self.assertIn("🇹🇷", suggest("turkey", 3))
        self.assertEqual(suggest("us", 1), [])           # the word "us" isn't the USA
        self.assertEqual(suggest("turkey", 1), ["🦃"])
        self.assertEqual(suggest("turkey", 1, capitalised=True), ["🇹🇷"])

    def test_switched_off(self):
        set_country_flags(False)
        self.assertEqual(suggest("germany", 1), [])
        self.assertNotIn("🇩🇪", search("germany"))
        self.assertIn("🏴‍☠️", search("pirate"))       # not a country flag


class PictureTests(unittest.TestCase):
    def test_every_emoji_has_a_picture(self):
        """Windows draws emoji from these pictures, so none may be missing."""
        missing = [c for c, _n, _k in EMOJI_DATA
                   if not os.path.exists(os.path.join(PNG_DIR, _png(c)))]
        for face in set(EMOTICONS.values()) | {"🍆💦"}:
            whole = os.path.exists(os.path.join(PNG_DIR, _png(face)))
            parts = all(os.path.exists(os.path.join(PNG_DIR, _png(c))) for c in face)
            if not (whole or parts):
                missing.append(face)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
