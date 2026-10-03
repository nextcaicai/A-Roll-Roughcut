"""Cut-timeline SRT from corrected words. No network."""

import unittest

import export_srt


def words(*rows):
    return [{"word": text, "start": start, "end": end} for text, start, end in rows]


class CueBuild(unittest.TestCase):
    def test_a_short_cut_still_joins_the_phrase(self):
        keep = [
            {"sourceStart": 1.0, "sourceEnd": 1.2},
            {"sourceStart": 1.4, "sourceEnd": 1.9},
        ]
        cues = export_srt.build_cues(
            words(("你", 1.0, 1.2), ("你", 1.2, 1.4), ("好。", 1.4, 1.8)),
            export_srt.with_timeline(keep),
        )
        self.assertEqual([cue["text"] for cue in cues], ["你好"])
        self.assertAlmostEqual(cues[0]["start"], 0.0, places=3)
        self.assertLess(cues[0]["end"], 0.7)

    def test_a_deleted_stretch_does_not_share_a_card(self):
        keep = [
            {"sourceStart": 1.0, "sourceEnd": 1.3},
            {"sourceStart": 5.0, "sourceEnd": 6.0},
        ]
        cues = export_srt.build_cues(
            words(("一", 1.0, 1.3), ("真", 5.0, 5.2), ("正", 5.2, 5.4)),
            export_srt.with_timeline(keep),
        )
        self.assertEqual([cue["text"] for cue in cues], ["一", "真正"])
        self.assertAlmostEqual(cues[1]["start"], 0.3, places=3)

    def test_sentence_punctuation_starts_a_new_cue(self):
        keep = [{"sourceStart": 0.0, "sourceEnd": 2.0}]
        cues = export_srt.build_cues(
            words(("好。", 0.0, 0.3), ("下", 0.3, 0.5), ("一", 0.5, 0.7)),
            export_srt.with_timeline(keep),
        )
        self.assertEqual([cue["text"] for cue in cues], ["好", "下一"])

    def test_line_breaks_past_eighteen_characters(self):
        keep = [{"sourceStart": 0.0, "sourceEnd": 5.0}]
        rows = [(char, index * 0.1, index * 0.1 + 0.1) for index, char in enumerate("一二三四五六七八九十")]
        cues = export_srt.build_cues(words(*rows), export_srt.with_timeline(keep), max_chars=4, hard_max=4)
        self.assertEqual([cue["text"] for cue in cues], ["一二三四", "五六七八", "九十"])

    def test_kept_breath_breaks_the_line(self):
        keep = [{"sourceStart": 0.0, "sourceEnd": 2.0}]
        cues = export_srt.build_cues(
            words(("你", 0.0, 0.2), ("好", 1.0, 1.2)),
            export_srt.with_timeline(keep),
            gap_break=0.35,
        )
        self.assertEqual([cue["text"] for cue in cues], ["你", "好"])
        self.assertAlmostEqual(cues[1]["start"], 1.0, places=3)

    def test_word_mostly_outside_keep_is_dropped(self):
        keep = [{"sourceStart": 1.0, "sourceEnd": 1.2}]
        cues = export_srt.build_cues(
            words(("删", 0.0, 0.9), ("留", 1.0, 1.2)),
            export_srt.with_timeline(keep),
        )
        self.assertEqual([cue["text"] for cue in cues], ["留"])
        self.assertAlmostEqual(cues[0]["start"], 0.0, places=3)

    def test_complete_clause_breaks_before_the_line_is_full(self):
        keep = [{"sourceStart": 0.0, "sourceEnd": 3.0}]
        cues = export_srt.build_cues(
            words(("你", 0.0, 0.2), ("好，", 0.2, 0.5), ("世", 0.5, 0.7), ("界。", 0.7, 1.0)),
            export_srt.with_timeline(keep),
        )
        self.assertEqual([cue["text"] for cue in cues], ["你好", "世界"])

    def test_question_mark_stays_on_the_card(self):
        keep = [{"sourceStart": 0.0, "sourceEnd": 2.0}]
        cues = export_srt.build_cues(
            words(("对", 0.0, 0.2), ("吗？", 0.2, 0.5)),
            export_srt.with_timeline(keep),
        )
        self.assertEqual([cue["text"] for cue in cues], ["对吗？"])

    def test_script_clause_breaks_without_changing_characters(self):
        spoken = list("如果用几个标签来概括它大概就是这些，")
        rows = [(char, index * 0.1, index * 0.1 + 0.1) for index, char in enumerate(spoken)]
        keep = [{"sourceStart": 0.0, "sourceEnd": 5.0}]
        cues = export_srt.build_cues(
            words(*rows),
            export_srt.with_timeline(keep),
            script="如果用几个标签来概括它，大概就是这些。",
        )
        self.assertEqual(
            [cue["text"] for cue in cues],
            ["如果用几个标签来概括它", "大概就是这些"],
        )
        self.assertEqual("".join(cue["text"] for cue in cues) + "，", "".join(spoken))

    def test_script_keeps_a_mismatched_phrase_and_still_preserves_characters(self):
        spoken = list("但它的价值可能比大家看到的热闹叙事里的描述要小一些。")
        rows = [(char, index * 0.1, index * 0.1 + 0.1) for index, char in enumerate(spoken)]
        keep = [{"sourceStart": 0.0, "sourceEnd": 8.0}]
        cues = export_srt.build_cues(
            words(*rows),
            export_srt.with_timeline(keep),
            script="但它的价值，可能比大家看到的热闹叙事里描述的，要小一些。",
        )
        joined = "".join(cue["text"] for cue in cues)
        self.assertEqual(joined + "。", "".join(spoken))
        self.assertEqual(
            [cue["text"] for cue in cues],
            ["但它的价值", "可能比大家看到的热闹叙事里的描述要小一些"],
        )
        transcript = {
            "segments": [
                {
                    "words": [
                        {"word": "你", "start": 0.0, "end": 0.2},
                        {"word": "好", "start": 0.6, "end": 0.8},
                    ]
                }
            ]
        }
        decisions = {
            "breath": {"maxKeep": 0.4},
            "keep": [{"sourceStart": 0.0, "sourceEnd": 1.0}],
        }
        text = export_srt.render_srt(transcript, decisions)
        self.assertIn("你", text)
        self.assertIn("好", text)
        self.assertEqual(text.split("\n\n")[0].splitlines()[-1], "你")


class Timestamp(unittest.TestCase):
    def test_srt_clock(self):
        self.assertEqual(export_srt.format_timestamp(65.5), "00:01:05,500")
        body = export_srt.format_srt([{"start": 0, "end": 1.25, "text": "早"}])
        self.assertTrue(body.startswith("1\n00:00:00,000 --> 00:00:01,250\n早\n"))


if __name__ == "__main__":
    unittest.main()
