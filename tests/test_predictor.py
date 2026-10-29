"""Tests for predictor.py (context-aware prediction + UserModel). No GTK needed.

Runs under pytest or plain unittest:
    python3 -m pytest -q tests/test_predictor.py
    python3 -m unittest -q tests.test_predictor
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import predictor  # noqa: E402
from predictor import UserModel, WordPredictor  # noqa: E402

_PRED = None


def shared_predictor() -> WordPredictor:
    """One real predictor for the read-only tests (loading takes ~0.2 s)."""
    global _PRED
    if _PRED is None:
        _PRED = WordPredictor()
    return _PRED


class ContextPredictionTests(unittest.TestCase):
    def setUp(self):
        self.p = shared_predictor()
        self.p.set_user_model(None)

    def test_phrase_model_loaded(self):
        self.assertTrue(self.p._bi and self.p._tri, "ngrams.txt should be bundled")

    def test_i_want_to_suggests_verbs(self):
        got = self.p.predict_next(["i", "want", "to"], n=5)
        self.assertGreaterEqual(len({"go", "be", "see", "do", "have", "get", "know"} & set(got)), 3, got)

    def test_thank_you(self):
        self.assertEqual(self.p.predict_next(["thank"], n=5)[0], "you")
        self.assertEqual(self.p.predict("y", 5, context=["thank"])[0], "you")

    def test_how_are_you(self):
        self.assertEqual(self.p.predict_next(["how", "are"], n=5)[0], "you")

    def test_context_changes_ranking(self):
        # plain frequency ranking vs. context-aware ranking for the same prefix
        with_ctx = self.p.predict("t", 5, context=["i", "want"])
        self.assertEqual(with_ctx[0], "to")
        self.assertNotEqual(with_ctx, self.p.predict("t", 5))

    def test_prefix_filtering(self):
        for ctx in (None, [], ["thank"], ["i", "want", "to"], ["how", "are"]):
            for prefix in ("a", "th", "wh", "go", "xyl"):
                for w in self.p.predict(prefix, 6, context=ctx):
                    self.assertTrue(w.startswith(prefix), (prefix, ctx, w))

    def test_no_prefix_empty_result(self):
        self.assertEqual(self.p.predict("", 5), [])
        self.assertEqual(self.p.predict("qqqqqq", 5, context=["the"]), [])

    def test_sentence_start_vs_mid_sentence(self):
        start = self.p.predict_next([], n=5)
        self.assertIn("I", start)
        self.assertNotEqual(start, self.p.predict_next(["want", "to"], n=5))

    def test_legacy_string_argument(self):
        self.assertEqual(self.p.predict_next("thank", n=3)[0], "you")

    def test_predict_without_context_is_plain_ranking(self):
        self.assertEqual(self.p.predict("th", 5), self.p._prefix_top("th", 5))

    def test_results_unique_and_bounded(self):
        got = self.p.predict_next(["i"], n=5)
        self.assertEqual(len(got), 5)
        self.assertEqual(len(set(got)), 5)


class MissingDataTests(unittest.TestCase):
    def test_missing_everything(self):
        p = WordPredictor("/nonexistent/words.txt", "/nonexistent/ngrams.txt")
        self.assertTrue(p.dict_missing)
        self.assertEqual(p.predict("th", 5, context=["i"]), [])
        # legacy fallback still gives something for the empty prefix
        self.assertTrue(p.predict_next(["thank"], 5))

    def test_missing_ngrams_falls_back_to_legacy_bigrams(self):
        p = WordPredictor(ngrams_path="/nonexistent/ngrams.txt")
        self.assertFalse(p._bi)
        self.assertEqual(p.predict_next(["thank"], 5)[0], "you")
        self.assertEqual(len(p.predict_next(["zzzz"], 5)), 5)

    def test_unreadable_ngrams(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ngrams.txt")
            with open(path, "wb") as f:
                f.write(b"\xff\xfe\x00garbage\n[2]\nx\tnotanumber\ty:1\n")
            p = WordPredictor(ngrams_path=path)
            self.assertTrue(p.predict_next(["thank"], 5))

    def test_ngrams_loader_missing_path(self):
        self.assertEqual(predictor._load_ngrams("/nonexistent/x"), ({}, {}))


class UserModelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "learned_words.json")
        self.p = WordPredictor()
        self.um = UserModel(self.path, self.p.is_known)

    def tearDown(self):
        self._tmp.cleanup()

    def test_learning_boosts_prefix_completion(self):
        before = self.p.predict("zo", 5, context=["i", "saw"])
        self.assertNotEqual(before[0], "zodiac")
        for _ in range(4):
            self.um.observe("zodiac", ["i", "saw"])
        self.p.set_user_model(self.um)
        after = self.p.predict("zo", 5, context=["i", "saw"])
        self.assertEqual(after[0], "zodiac")

    def test_learning_boosts_next_word(self):
        ctx = ["my", "favorite"]
        for _ in range(100):      # a strong habit, not a one-off
            self.um.observe("pizza", ctx)
        self.p.set_user_model(self.um)
        self.assertEqual(self.p.predict_next(ctx, 5)[0], "pizza")
        self.p.set_user_model(None)
        self.assertNotIn("pizza", self.p.predict_next(ctx, 5))

    def test_privacy_filters(self):
        for bad in ("abc123", "hello!", "x" * 21, "qzxjvk", "don't", "", "a b", "12345"):
            self.assertFalse(self.um.observe(bad, ["the"]), bad)
        self.assertEqual(self.um.n1, 0)
        self.assertTrue(self.um.observe("hello", ["the"]))

    def test_unknown_context_word_breaks_chain(self):
        self.um.observe("house", ["zzqx", "big"])           # "zzqx" unknown → only "big" kept
        self.assertIn("big", self.um.u2)
        self.assertEqual(self.um.u3, {})

    def test_custom_words_are_learnable(self):
        self.p.set_custom_words(["Dinglebob"])
        self.assertTrue(self.p.is_known("dinglebob"))
        self.assertTrue(UserModel(None, self.p.is_known).observe("Dinglebob"))

    def test_save_load_roundtrip_is_atomic(self):
        self.um.observe("hello", ["well"])
        self.um.observe("world", ["hello", "big"])
        self.assertTrue(self.um.save())
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        again = UserModel(self.path, self.p.is_known)
        self.assertEqual(again.u1, self.um.u1)
        self.assertEqual(again.u2, self.um.u2)
        self.assertEqual(again.u3, self.um.u3)
        self.assertFalse(again.dirty)

    def test_save_is_noop_when_clean(self):
        self.assertFalse(self.um.save())
        self.assertFalse(os.path.exists(self.path))

    def test_corrupt_json_recovery(self):
        for content in ("{not json", "", "[]", '{"u1": 5, "u2": {}, "u3": {}}',
                        '{"u1": {"ok": "many"}, "u2": [], "u3": {}}', "\x00\x01"):
            with open(self.path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(content)
            um = UserModel(self.path, self.p.is_known)
            self.assertEqual((um.u1, um.u2, um.u3, um.n1), ({}, {}, {}, 0), content)
            self.assertTrue(um.observe("hello"))
            self.assertTrue(um.save())
            self.assertEqual(UserModel(self.path).u1, {"hello": 1})

    def test_load_sanitises_hostile_entries(self):
        data = {"u1": {"fine": 3, "Bad1": 2, "x" * 30: 1, "neg": -4, "flt": 1.5, "t": True},
                "u2": {"a": {"b": 1}, "A B": {"c": 1}},
                "u3": {"one two": {"x": 2}, "just": {"x": 1}}}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        um = UserModel(self.path)
        self.assertEqual(um.u1, {"fine": 3})
        self.assertEqual(um.u2, {"a": {"b": 1}})
        self.assertEqual(um.u3, {"one two": {"x": 2}})

    def test_forget(self):
        self.um.observe("hello", ["well"])
        self.um.save()
        self.assertTrue(os.path.exists(self.path))
        self.um.forget()
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual((self.um.u1, self.um.u2, self.um.u3, self.um.n1), ({}, {}, {}, 0))
        self.assertFalse(self.um.dirty)
        self.assertEqual(self.um.weight(), 0.0)

    def test_size_cap_decays(self):
        um = UserModel(None)
        um.MAX_ENTRIES = 200
        words = ["w" + "".join(chr(97 + (i // 26 ** k) % 26) for k in range(3)) for i in range(2000)]
        for i, w in enumerate(words):
            um.observe(w, [words[i - 1]] if i else [])
            self.assertLessEqual(um._entries, um.MAX_ENTRIES + 3)
        # internal bookkeeping stays consistent with reality
        real = len(um.u1) + sum(len(d) for d in um.u2.values()) + sum(len(d) for d in um.u3.values())
        self.assertEqual(real, um._entries)

    def test_save_failure_is_reported_not_raised(self):
        um = UserModel(os.path.join(self.path, "sub", "x.json"))   # parent is not a dir
        with open(self.path, "w") as f:
            f.write("{}")
        um.observe("hello")
        self.assertFalse(um.save())


if __name__ == "__main__":
    unittest.main()
