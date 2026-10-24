"""Lexicon substitutions keep word times and skip ambiguous matches."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import apply_asr_lexicon as lexicon


def transcript(*words):
    items = []
    t = 0.0
    for text in words:
        items.append({"word": text, "start": round(t, 3), "end": round(t + 0.2, 3)})
        t += 0.2
    return {"segments": [{"text": "".join(words), "words": items}]}


class AsrLexicon(unittest.TestCase):
    def test_phrase_keeps_punctuation_and_times(self):
        rules = [(["初", "检"], ["粗", "剪"]), (["check", "out"], ["Chat", "Cut"])]
        out, applied, unresolved = lexicon.apply_rules(transcript("初", "检、", "check", "out？"), rules)
        words = [w["word"] for w in out["segments"][0]["words"]]
        self.assertEqual(words, ["粗", "剪、", "Chat", "Cut？"])
        self.assertEqual(out["segments"][0]["words"][0]["start"], 0.0)
        self.assertEqual(out["segments"][0]["words"][1]["end"], 0.4)
        self.assertEqual(len(applied), 2)
        self.assertEqual(unresolved, [])

    def test_single_character_outside_the_table_stays(self):
        rules = [(["初", "检"], ["粗", "剪"])]
        out, applied, _ = lexicon.apply_rules(transcript("初", "稿"), rules)
        self.assertEqual([w["word"] for w in out["segments"][0]["words"]], ["初", "稿"])
        self.assertEqual(applied, [])

    def test_already_correct_is_not_listed(self):
        rules = [(["json"], ["JSON"])]
        _, applied, _ = lexicon.apply_rules(transcript("JSON"), rules)
        self.assertEqual(applied, [])

    def test_same_length_conflict_is_left_alone(self):
        rules = [(["初", "检"], ["粗", "剪"]), (["初", "检"], ["初", "查"])]
        out, applied, unresolved = lexicon.apply_rules(transcript("初", "检"), rules)
        self.assertEqual([w["word"] for w in out["segments"][0]["words"]], ["初", "检"])
        self.assertEqual(applied, [])
        self.assertEqual(unresolved, ["初 检"])

    def test_longer_phrase_wins(self):
        rules = [(["初"], ["粗"]), (["初", "检"], ["粗", "剪"])]
        out, applied, _ = lexicon.apply_rules(transcript("初", "检"), rules)
        self.assertEqual([w["word"] for w in out["segments"][0]["words"]], ["粗", "剪"])
        self.assertEqual(len(applied), 1)

    def test_shipped_table_loads(self):
        rules = lexicon.load_rules(lexicon.REFERENCES)
        self.assertIn((["初", "检"], ["粗", "剪"]), rules)
        for src, dst in rules:
            self.assertEqual(len(src), len(dst))

    def test_writes_lexicon_transcript_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "transcript.json"
            src.write_text(json.dumps(transcript("初", "检")), encoding="utf-8")
            out = root / "transcript.lexicon.json"
            argv = sys.argv
            sys.argv = ["apply_asr_lexicon.py", "--transcript", str(src), "--output", str(out)]
            try:
                self.assertEqual(lexicon.main(), 0)
            finally:
                sys.argv = argv
            got = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual([w["word"] for w in got["segments"][0]["words"]], ["粗", "剪"])
            self.assertIn("初检", (root / "corrections.lexicon.md").read_text(encoding="utf-8").replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
