"""Cut-decision rules: validation, lead-in, word stutter, retake merge, retake candidates."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import apply_word_stutter as stutter
import retake_candidates as rc
import scan_retake_residue as residue
from compress_breath import VOICE_NOTE, apply_compress_breath, compress_keep_words, sync_voice_review
from decisions_common import (
    apply_lead_in,
    decisions_problems,
    fill_decision_text,
    materialize_keeps,
    prune_orphaned_empty_keeps,
    recompute_timeline,
)
from run_summary import build_run_summary, merge_review_into_summary, write_run_summary
from merge_retake_judgments import apply_proposed_drop, merge_judgments


def decisions(keep, drop=(), lead_in=0.12):
    data = {
        "version": 1,
        "source": {"path": "a.mp4", "duration": 600.0},
        "breath": {"maxKeep": 0.4, "leadIn": lead_in},
        "keep": [
            {"id": f"k{i:03d}", "sourceStart": s, "sourceEnd": e, "reason": r}
            for i, (s, e, r) in enumerate(keep, 1)
        ],
        "drop": [
            {"id": f"d{i:03d}", "sourceStart": s, "sourceEnd": e, "reason": r, "text": ""}
            for i, (s, e, r) in enumerate(drop, 1)
        ],
        "flags": [],
    }
    recompute_timeline(data)
    return data


def spans(data, key="keep"):
    return [(item["sourceStart"], item["sourceEnd"]) for item in sorted(data[key], key=lambda x: x["sourceStart"])]


def loud_peaks(duration, spans, step=0.05):
    n = int(round(duration / step))
    peaks = [0.0] * n
    for start, end in spans:
        for i in range(max(0, int(start / step)), min(n, int(round(end / step)))):
            peaks[i] = 0.6
    return peaks


class Validation(unittest.TestCase):
    def test_clean_file_passes(self):
        data = decisions([(0, 4, "speech"), (6, 9, "speech")], [(4, 6, "retake")])
        self.assertEqual(decisions_problems(data), ([], []))

    def test_manual_is_a_valid_reason(self):
        data = decisions([(0, 4, "speech")], [(4, 6, "manual")])
        self.assertEqual(decisions_problems(data)[0], [])

    def test_range_both_kept_and_dropped_is_an_error(self):
        data = decisions([(0, 5, "speech")], [(4, 6, "retake")])
        errors, _ = decisions_problems(data)
        self.assertEqual(len(errors), 1)
        self.assertIn("both kept and dropped", errors[0])

    def test_overlapping_drops_only_warn(self):
        data = decisions([(0, 4, "speech")], [(4, 7, "retake"), (6, 8, "manual")])
        errors, warnings = decisions_problems(data)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)

    def test_stale_timeline_is_an_error(self):
        data = decisions([(0, 4, "speech"), (6, 9, "speech")])
        data["keep"][1]["timelineStart"] = 5.0
        self.assertTrue(decisions_problems(data)[0])


class LeadIn(unittest.TestCase):
    def test_start_moves_back_into_silence(self):
        data = decisions([(0, 4, "speech"), (6, 9, "speech")])
        self.assertEqual(apply_lead_in(data), 1)
        self.assertEqual(spans(data)[1], (5.88, 9))

    def test_never_reaches_into_a_drop(self):
        data = decisions([(0, 4, "speech"), (6, 9, "speech")], [(4, 5.95, "retake")])
        apply_lead_in(data)
        self.assertEqual(spans(data)[1], (5.95, 9))

    def test_stops_at_the_end_of_the_previous_keep(self):
        data = decisions([(0, 5.9, "speech"), (5.95, 9, "speech")])
        apply_lead_in(data)
        self.assertEqual(spans(data), [(0, 5.9), (5.9, 9)])
        self.assertEqual(decisions_problems(data)[0], [])


class Breath(unittest.TestCase):
    def test_long_gap_keeps_the_inhale_and_drops_the_rest(self):
        keeps, drops = compress_keep_words([(0, 1), (3, 4)], 0.4, 0, 4)
        self.assertEqual(keeps, [(0, 1, "speech"), (2.6, 3, "breath"), (3, 4, "speech")])
        self.assertEqual(drops, [(1, 2.6)])

    def test_short_gap_is_left_alone(self):
        keeps, drops = compress_keep_words([(0, 1), (1.3, 2)], 0.4, 0, 2)
        self.assertEqual(keeps, [(0, 2, "speech")])
        self.assertEqual(drops, [])

    def test_leading_and_trailing_silence_are_cut(self):
        keeps, drops = compress_keep_words([(1, 2), (4, 5)], 0.4, 0, 6)
        self.assertEqual(keeps[0][0], 1)
        self.assertEqual(keeps[-1][1], 5)
        self.assertIn((0, 1), drops)
        self.assertIn((5, 6), drops)
        self.assertIn((2, 3.6), drops)

    def test_touching_keeps_are_merged_before_cutting(self):
        data = decisions([(0, 2.6, "speech"), (2.6, 4, "breath")])
        apply_compress_breath(data, [(0.0, 1.0), (3.0, 4.0)])
        self.assertEqual(spans(data, "drop"), [(1, 2.6)])
        self.assertAlmostEqual(data["keep"][-1]["timelineEnd"], 2.4)

    def test_word_head_pad_is_kept_in_front_of_the_first_word(self):
        data = decisions([(0, 3, "speech")])
        apply_compress_breath(data, [(1.0, 2.0)])
        breaths = [item for item in data["keep"] if item["reason"] == "breath"]
        self.assertEqual(len(breaths), 1)
        self.assertAlmostEqual(breaths[0]["sourceStart"], 0.88)
        self.assertAlmostEqual(breaths[0]["sourceEnd"], 1.0)
        self.assertIn((0.0, 0.88), spans(data, "drop"))
        self.assertEqual(decisions_problems(data)[0], [])

    def test_gap_between_keeps_uses_the_same_breath(self):
        data = decisions([(0, 1, "speech"), (3, 4, "speech")])
        apply_compress_breath(data, [(0.0, 1.0), (3.0, 4.0)])
        breaths = [item for item in data["keep"] if item["reason"] == "breath"]
        self.assertEqual([(b["sourceStart"], b["sourceEnd"]) for b in breaths], [(2.6, 3.0)])
        self.assertIn((1.0, 2.6), spans(data, "drop"))
        self.assertEqual(decisions_problems(data)[0], [])

    def test_short_gap_between_keeps_stays_whole(self):
        data = decisions([(0, 1, "speech"), (1.2, 2, "speech")])
        apply_compress_breath(data, [(0.0, 1.0), (1.2, 2.0)])
        breaths = [item for item in data["keep"] if item["reason"] == "breath"]
        self.assertEqual([(b["sourceStart"], b["sourceEnd"]) for b in breaths], [(1.0, 1.2)])
        self.assertEqual(spans(data, "drop"), [])

    def test_pad_after_a_content_cut_stops_at_the_drop(self):
        data = decisions([(0, 1, "speech"), (5, 6, "speech")], [(1, 3, "retake")])
        apply_compress_breath(data, [(0.0, 1.0), (5.0, 6.0)])
        breaths = [item for item in data["keep"] if item["reason"] == "breath"]
        self.assertEqual([(b["sourceStart"], b["sourceEnd"]) for b in breaths], [(4.88, 5.0)])
        self.assertIn((1.0, 3.0), spans(data, "drop"))
        self.assertIn((3.0, 4.88), spans(data, "drop"))
        self.assertEqual(decisions_problems(data)[0], [])

    def test_breath_pass_does_not_stack(self):
        data = decisions([(0, 1, "speech"), (3, 4, "speech")])
        words = [(0.0, 1.0), (3.0, 4.0)]
        apply_compress_breath(data, words)
        once = spans(data)
        apply_compress_breath(data, words)
        self.assertEqual(spans(data), once)
        self.assertEqual(decisions_problems(data)[0], [])

    def test_apply_writes_long_pause_and_shortens_timeline(self):
        data = decisions([(0, 4, "speech")])
        apply_compress_breath(data, [(0.0, 1.0), (3.0, 4.0)])
        self.assertEqual(spans(data), [(0, 1), (2.6, 3), (3, 4)])
        self.assertEqual(spans(data, "drop"), [(1, 2.6)])
        self.assertEqual(data["drop"][0]["reason"], "long-pause")
        self.assertAlmostEqual(data["keep"][-1]["timelineEnd"], 2.4)
        self.assertEqual(decisions_problems(data), ([], []))

    def test_speech_without_a_word_stays_in_the_keep(self):
        data = decisions([(0, 4, "speech")])
        words = [(0.0, 1.0), (3.0, 4.0)]
        peaks = loud_peaks(4, [(1.0, 1.4)])
        apply_compress_breath(data, words, peaks, 4)
        self.assertIn((1.0, 1.4), spans(data))
        self.assertTrue(any(item.get("untranscribed") for item in data["keep"]))
        self.assertEqual(data["flags"][0]["note"], VOICE_NOTE)
        self.assertEqual(data["flags"][0]["sourceStart"], 1.0)
        self.assertTrue(any(abs(a - 1.4) < 0.02 and abs(b - 2.6) < 0.02 for a, b in spans(data, "drop")))
        self.assertEqual(decisions_problems(data)[0], [])
        once = spans(data)
        apply_compress_breath(data, words, peaks, 4)
        self.assertEqual(spans(data), once)
        self.assertEqual(len(data["flags"]), 1)
        self.assertEqual(decisions_problems(data)[0], [])

    def test_a_short_dip_inside_untranscribed_speech_stays_one_span(self):
        data = decisions([(0, 4, "speech")])
        peaks = [0.0] * 400
        for i in range(100, 115):
            peaks[i] = 0.6
        for i in range(124, 145):
            peaks[i] = 0.6
        apply_compress_breath(data, [(0.0, 1.0), (3.0, 4.0)], peaks, 4)
        self.assertTrue(any(a <= 1.01 and b >= 1.44 for a, b in spans(data)))
        self.assertEqual(len([item for item in data["keep"] if item.get("untranscribed")]), 1)
        self.assertEqual(decisions_problems(data)[0], [])

    def test_room_tone_is_still_a_long_pause(self):
        data = decisions([(0, 4, "speech")])
        apply_compress_breath(data, [(0.0, 1.0), (3.0, 4.0)], [0.04] * 80, 4)
        self.assertEqual(spans(data), [(0, 1), (2.6, 3), (3, 4)])
        self.assertEqual(data["flags"], [])

    def test_voice_after_a_content_cut_stays_cut(self):
        data = decisions([(0, 1, "speech"), (5, 6, "speech")], [(1, 3, "retake")])
        apply_compress_breath(data, [(0.0, 1.0), (5.0, 6.0)], loud_peaks(6, [(3.0, 3.4)]), 6)
        self.assertFalse(any(item.get("untranscribed") for item in data["keep"]))
        self.assertEqual(data["flags"], [])
        self.assertEqual(decisions_problems(data)[0], [])

    def test_a_loud_gap_away_from_the_word_survives_prune(self):
        data = decisions([(0, 4, "speech")])
        apply_compress_breath(data, [(0.0, 1.0), (3.0, 4.0)], loud_peaks(4, [(1.8, 2.2)]), 4)
        self.assertIn((1.8, 2.2), spans(data))
        words = [(0.0, 1.0, "前"), (3.0, 4.0, "后")]
        self.assertEqual(prune_orphaned_empty_keeps(data, words), 0)
        self.assertIn((1.8, 2.2), spans(data))
        self.assertEqual(decisions_problems(data)[0], [])

    def test_voice_flag_is_appended_to_review(self):
        data = decisions([(0, 4, "speech")])
        data["flags"] = [{"id": "f001", "sourceStart": 9.0, "reason": "uncertain", "note": "已有的听点"}]
        apply_compress_breath(data, [(0.0, 1.0), (3.0, 4.0)], loud_peaks(4, [(1.0, 1.4)]), 4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.md"
            path.write_text("# 听点\n\n- f001 1.0s 已有的听点\n", encoding="utf-8")
            self.assertEqual(sync_voice_review(path, data), 1)
            text = path.read_text(encoding="utf-8")
            self.assertIn(VOICE_NOTE, text)
            self.assertIn("f001 1.0s 已有的听点", text)
            self.assertEqual(sync_voice_review(path, data), 0)


class KeepText(unittest.TestCase):
    def test_span_gets_only_the_words_inside_it(self):
        data = decisions([(0, 1, "speech"), (3, 4, "speech")])
        data["keep"][0]["text"] = "整句被复制到每一段"
        data["keep"][1]["text"] = "整句被复制到每一段"
        words = [(0.0, 1.0, "前"), (3.0, 4.0, "后")]
        self.assertEqual(fill_decision_text(data, words), 2)
        self.assertEqual([k["text"] for k in data["keep"]], ["前", "后"])

    def test_breath_keep_has_no_text(self):
        data = decisions([(0, 1, "speech"), (1, 1.4, "breath"), (1.4, 2, "speech")])
        fill_decision_text(data, [(0.0, 1.0, "字"), (1.4, 2.0, "下")])
        self.assertEqual(data["keep"][1]["text"], "")


class OrphanEmptyKeep(unittest.TestCase):
    def test_empty_keep_in_a_deleted_stretch_is_dropped(self):
        data = decisions(
            [(0, 2, "speech"), (3.3, 3.7, "speech"), (4.5, 4.9, "speech"), (6, 9, "speech")],
            [(2, 3.3, "long-pause"), (3.7, 4.5, "stutter"), (4.9, 6, "stutter")],
        )
        words = [
            (0.0, 2.0, "可以试试"),
            (3.7, 4.5, "可以试试"),
            (4.9, 5.4, "用"),
            (6.0, 9.0, "用这条命令"),
        ]
        self.assertEqual(prune_orphaned_empty_keeps(data, words), 2)
        self.assertEqual(spans(data), [(0, 2), (6, 9)])
        self.assertIn((3.3, 3.7), spans(data, "drop"))
        self.assertIn((4.5, 4.9), spans(data, "drop"))
        self.assertEqual(decisions_problems(data)[0], [])

    def test_inhale_that_still_touches_kept_speech_stays(self):
        data = decisions([(0, 2, "speech"), (2, 2.4, "breath"), (2.4, 5, "speech")])
        words = [(0.0, 2.0, "前"), (2.4, 5.0, "后")]
        self.assertEqual(prune_orphaned_empty_keeps(data, words), 0)
        self.assertEqual(spans(data), [(0, 2), (2, 2.4), (2.4, 5)])
        self.assertEqual(data["keep"][1]["reason"], "breath")

    def test_empty_keep_with_no_words_is_a_long_pause(self):
        data = decisions([(0, 2, "speech"), (3.3, 3.7, "speech")])
        apply_compress_breath(data, [(0.0, 2.0)])
        self.assertEqual(spans(data), [(0, 2)])
        self.assertIn((3.3, 3.7), spans(data, "drop"))
        self.assertEqual(data["drop"][-1]["reason"], "long-pause")


class RunSummary(unittest.TestCase):
    def test_cut_ratio_and_reason_counts(self):
        data = decisions([(0, 4, "speech")], [(4, 6, "retake"), (6, 8, "stutter")])
        data["source"]["duration"] = 10.0
        summary = build_run_summary(
            run_dir=Path("."),
            before_sec=8.0,
            after=data,
            coverage_missing=0,
            residue={"repeats": 0, "bridgeStutter": 1, "suspiciousDrops": 0},
        )
        self.assertEqual(summary["pipeline"]["afterSec"], 4.0)
        self.assertEqual(summary["pipeline"]["cutRatio"], 0.6)
        self.assertEqual(summary["pipeline"]["dropByReason"], {"retake": 1, "stutter": 1})
        self.assertIsNone(summary["review"])

    def test_review_save_fills_the_review_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            data = decisions([(0, 4, "speech")])
            write_run_summary(run, build_run_summary(run_dir=run, before_sec=4, after=data, coverage_missing=None, residue={}))
            merge_review_into_summary(run, {"stats": {"dropsRestoredCount": 2, "dropsAddedCount": 1, "timelineDeltaSec": 1.5}, "flags": {"unheard": [{}]}, "session": {"savedAt": "t"}})
            got = json.loads((run / "run_summary.json").read_text())
            self.assertEqual(got["review"]["dropsRestoredCount"], 2)
            self.assertEqual(got["review"]["flagsUnheard"], 1)


class Residue(unittest.TestCase):
    def test_failed_opening_already_in_next_keep_is_not_flagged(self):
        keep = [{"id": "k1", "sourceStart": 3.0, "sourceEnd": 6.0, "reason": "speech"}]
        drop = {"id": "d1", "sourceStart": 0.0, "sourceEnd": 2.0, "reason": "stutter", "text": "自动剪口播的完整流程并不是"}
        words = [
            (0.0, 2.0, "自动剪口播的完整流程并不是"),
            (3.0, 6.0, "自动剪口播的完整流程并不是把一段口播直接丢给Jev"),
        ]
        self.assertTrue(residue.drop_covered_by_next_keep(drop, keep, words))

    def test_reworded_opening_is_covered(self):
        keep = [{"id": "k2", "sourceStart": 11.0, "sourceEnd": 20.0, "reason": "speech"}]
        drop = {"id": "d1", "sourceStart": 8.9, "sourceEnd": 11.0, "reason": "stutter"}
        words = [
            (8.9, 11.0, "最近Jev的讨论度很高"),
            (11.0, 20.0, "最近Jev模型的讨论度很高，如果用几个标签"),
        ]
        self.assertTrue(residue.drop_is_failed_opening("最近Jev的讨论度很高", "最近Jev模型的讨论度很高，如果用几个标签"))
        self.assertTrue(residue.drop_covered_by_next_keep(drop, keep, words))

    def test_distant_keep_is_not_a_bridge(self):
        keep = [
            {"id": "k1", "sourceStart": 0.0, "sourceEnd": 2.0, "reason": "speech"},
            {"id": "k2", "sourceStart": 120.0, "sourceEnd": 130.0, "reason": "speech"},
        ]
        drops = [{"id": "d1", "sourceStart": 10.0, "sourceEnd": 12.0, "reason": "stutter", "text": "自动剪口播的完整流程并不是把一段"}]
        words = [
            (10.0, 12.0, "自动剪口播的完整流程并不是把一段"),
            (120.0, 130.0, "别的段落"),
        ]
        self.assertEqual(residue.scan_bridge_stutter_drops(keep, drops, words), [])

    def test_stuttered_copy_later_in_keep_is_not_suspicious(self):
        keep = [{"id": "k2", "sourceStart": 20.0, "sourceEnd": 30.0, "reason": "speech"}]
        drops = [
            {
                "id": "d1",
                "sourceStart": 0.0,
                "sourceEnd": 8.0,
                "reason": "stutter",
                "text": "自动剪口播的完整流程并不是把一段自动剪口播的完整流程并不是把一段口播直接丢给Jev",
            }
        ]
        words = [
            (0.0, 8.0, "自动剪口播的完整流程并不是把一段自动剪口播的完整流程并不是把一段口播直接丢给Jev"),
            (20.0, 30.0, "二有限解。自动剪口播的完整流程并不是把一段口播直接丢给Jev"),
        ]
        self.assertEqual(residue.scan_suspicious_drops(keep, drops, words), [])


class WordStutter(unittest.TestCase):
    def setUp(self):
        self.function, self.words = stutter.load_stutter_classes(stutter.REFERENCES_DIR)

    def find(self, tokens, gap=0.05):
        words, t = [], 0.0
        for tok in tokens:
            words.append((t, t + 0.2, tok))
            t += 0.2 + gap
        return [
            h["text"]
            for h in stutter.find_stutter_drops(
                words, function_chars=self.function, repeat_words=self.words
            )
        ]

    def test_repeated_char_drops_the_first(self):
        self.assertEqual(self.find(["我", "的", "的", "想法"]), ["的"])
        self.assertEqual(self.find(["就", "就", "是"]), ["就"])

    def test_reduplication_split_into_chars_is_kept(self):
        self.assertEqual(self.find(["你", "看", "看", "这个"]), [])
        self.assertEqual(self.find(["密", "密", "麻", "麻"]), [])
        self.assertEqual(self.find(["拜", "拜"]), [])
        self.assertEqual(self.find(["去", "找", "找"]), [])
        self.assertEqual(self.find(["迟", "迟", "没"]), [])
        self.assertEqual(self.find(["空", "荡", "荡"]), [])

    def test_repeated_multichar_word_is_cut(self):
        self.assertEqual(self.find(["结果", "结果"]), ["结果"])

    def test_whitelisted_repeat_word_is_kept(self):
        self.assertEqual(self.find(["为什么", "为什么"]), [])

    def test_long_gap_is_not_a_stutter(self):
        self.assertEqual(self.find(["的", "的"], gap=0.6), [])

    def test_sentence_start_said_again_after_a_pause_is_cut(self):
        words = [
            (40.6, 41.16, "况。"),
            (41.16, 41.52, "所"),
            (41.52, 41.81, "以"),
            (43.54, 43.73, "所"),
            (43.73, 43.81, "以"),
            (43.81, 43.99, "经"),
            (43.99, 44.19, "常"),
        ]
        hits = stutter.find_restarted_openings(words)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["sourceStart"], 41.16)
        self.assertEqual(hits[0]["sourceEnd"], 41.81)
        self.assertEqual(hits[0]["text"], "所以")


class ProposedDrop(unittest.TestCase):
    def test_drop_inside_keep_splits_it(self):
        data = decisions([(0, 10, "speech")])
        status, _ = apply_proposed_drop(data, ds=3, de=5, text="重", reason="retake", kept_instead=None, jev_id="j1")
        self.assertEqual(status, "applied")
        self.assertEqual(spans(data), [(0, 3), (5, 10)])
        self.assertEqual(spans(data, "drop"), [(3, 5)])
        self.assertEqual(decisions_problems(data), ([], []))

    def test_drop_never_spills_into_the_next_keep(self):
        data = decisions([(105.21, 113.695, "speech"), (113.695, 113.97, "breath"), (114.5, 116, "speech")])
        status, _ = apply_proposed_drop(data, ds=113.51, de=113.72, text="择", reason="stutter", kept_instead=None, jev_id="w")
        self.assertEqual(status, "applied")
        self.assertEqual(decisions_problems(data)[0], [])

    def test_stutter_across_touching_keeps_is_cut(self):
        data = decisions([(0, 5, "speech"), (5, 5.4, "breath"), (5.4, 9, "speech")])
        status, _ = apply_proposed_drop(data, ds=4.8, de=5.6, text="难", reason="stutter", kept_instead=None, jev_id="w")
        self.assertEqual(status, "applied")
        self.assertEqual(spans(data), [(0, 4.8), (5.6, 9)])
        self.assertEqual(decisions_problems(data), ([], []))

    def test_range_across_a_real_gap_is_skipped(self):
        data = decisions([(0, 5, "speech"), (6, 9, "speech")])
        status, detail = apply_proposed_drop(data, ds=4.5, de=6.5, text="", reason="retake", kept_instead=None, jev_id="j")
        self.assertEqual((status, detail), ("skipped", "range not inside any keep"))

    def test_range_already_dropped_is_left_alone(self):
        data = decisions([(0, 3, "speech"), (5, 10, "speech")], [(3, 5, "retake")])
        before = copy.deepcopy(data)
        status, detail = apply_proposed_drop(data, ds=3, de=5, text="", reason="retake", kept_instead=None, jev_id="j1")
        self.assertEqual((status, detail), ("skipped", "already fully in drop"))
        self.assertEqual(data, before)

    def test_partial_overlap_with_a_drop_is_skipped(self):
        data = decisions([(0, 3, "speech"), (5, 10, "speech")], [(3, 5, "retake")])
        status, detail = apply_proposed_drop(data, ds=4, de=6, text="", reason="retake", kept_instead=None, jev_id="j1")
        self.assertEqual((status, detail), ("skipped", "partly overlaps existing drop"))

    def test_flag_judgment_adds_a_flag_and_cuts_nothing(self):
        data = decisions([(0, 10, "speech")])
        judgment = {
            "id": "j1",
            "candidateId": "c1",
            "panelAction": "flag",
            "note": "ambiguous",
            "candidate": {"earlier": {"sourceStart": 2.0}},
        }
        merge_judgments(data, [judgment])
        self.assertEqual(len(data["flags"]), 1)
        self.assertEqual(spans(data), [(0, 10)])


class RetakeCandidates(unittest.TestCase):
    @staticmethod
    def clause(text, start=0.0):
        return {"text": text, "norm": rc.normalize_zh(text), "sourceStart": start, "sourceEnd": start + 1}

    def test_failed_opening_then_full_sentence_is_a_candidate(self):
        a = self.clause("虽然大模型也能做分类，")
        b = self.clause("虽然大模型也能做分类筛选、回答yesorno这类任务，", 2)
        self.assertIsNotNone(rc.score_clause_pair(a, b))

    def test_longer_first_take_then_shorter_is_not_a_candidate(self):
        a = self.clause("虽然大模型也能做分类筛选、回答yesorno这类任务，")
        b = self.clause("虽然大模型也能做分类，", 2)
        self.assertIsNone(rc.score_clause_pair(a, b))

    def test_clauses_split_on_punctuation_with_word_times(self):
        words = [(0.0, 0.5, "第一句，"), (0.6, 1.0, "第二"), (1.0, 1.4, "句话。")]
        got = [(c["text"], c["sourceStart"], c["sourceEnd"]) for c in rc.split_clauses(words)]
        self.assertEqual(got, [("第一句，", 0.0, 0.5), ("第二句话。", 0.6, 1.4)])

    def test_same_pair_keeps_only_the_best_score(self):
        base = {"kind": "intra_clause", "earlier": {"sourceStart": 1.0}, "later": {"sourceStart": 3.0}}
        uniq = rc.dedupe_candidates([{**base, "score": 50}, {**base, "score": 90}])
        self.assertEqual([c["score"] for c in uniq], [90])


class MaterializeKeeps(unittest.TestCase):
    def test_drop_splits_keeps_and_a_pause_does_not(self):
        data = {
            "version": 1,
            "source": {"path": "a.mp4", "duration": 20},
            "drop": [{"sourceStart": 1.0, "sourceEnd": 2.0, "reason": "retake"}],
            "flags": [{"sourceStart": 1.2, "reason": "uncertain", "note": "听后遍"}],
        }
        words = [(0.0, 0.4, "前"), (0.6, 0.9, "句"), (1.1, 1.8, "重说"), (2.2, 2.6, "后"), (4.0, 4.4, "面")]
        self.assertEqual(materialize_keeps(data, words), 2)
        self.assertEqual([(k["sourceStart"], k["sourceEnd"]) for k in data["keep"]], [(0.0, 0.9), (2.2, 4.4)])
        self.assertEqual(data["keep"][0]["timelineStart"], 0.0)
        self.assertEqual(data["drop"][0]["id"], "d001")
        self.assertEqual(data["flags"][0]["id"], "f001")
        self.assertEqual(data["breath"]["maxKeep"], 0.4)
        errors, _ = decisions_problems(data)
        self.assertEqual(errors, [])

    def test_existing_keep_is_not_rebuilt(self):
        data = decisions([(0, 1, "speech")], [(2, 3, "retake")])
        self.assertEqual(materialize_keeps(data, [(0, 1, "前"), (4, 5, "后")]), 0)
        self.assertEqual(len(data["keep"]), 1)

    def test_midpoint_inside_a_drop_removes_the_word(self):
        data = {"version": 1, "source": {"duration": 10}, "drop": [{"sourceStart": 0.5, "sourceEnd": 1.5, "reason": "retake"}]}
        materialize_keeps(data, [(0.0, 0.4, "留"), (0.8, 1.2, "删"), (1.6, 2.0, "留")])
        self.assertEqual([(k["sourceStart"], k["sourceEnd"]) for k in data["keep"]], [(0.0, 0.4), (1.6, 2.0)])


class ResidueReport(unittest.TestCase):
    def test_empty_report_has_no_table(self):
        text = residue.render_md([], [], "run")
        self.assertIn("没有需要复查的行", text)
        self.assertNotIn("像完整句", text)
        self.assertNotIn("|", text)

    def test_bridge_row_is_kept(self):
        text = residue.render_md([], [{"sourceStart": 1.2, "dropId": "d001", "prevKeep": "k001", "nextKeep": "k002", "clause": "不过是"}], "run")
        self.assertIn("切断了前后句", text)
        self.assertNotIn("像完整句", text)

    def test_empty_rows_skip_review_unless_coverage_is_missing(self):
        import pipeline

        self.assertIn("跳过第 5 步", pipeline.residue_followup({"repeats": 0, "bridgeStutter": 0}, None))
        self.assertIn("跳过第 5 步", pipeline.residue_followup({"repeats": 0, "bridgeStutter": 0}, 0))
        self.assertIn("script_coverage.md", pipeline.residue_followup({"repeats": 0, "bridgeStutter": 0}, 2))
        self.assertIn("第 5 步", pipeline.residue_followup({"repeats": 1, "bridgeStutter": 0}, 0))
        self.assertNotIn("跳过", pipeline.residue_followup({"repeats": 1, "bridgeStutter": 0}, 0))


class ScriptCoverageSections(unittest.TestCase):
    def test_heading_anchor_required_in_corpus(self):
        import tempfile
        import validate_script_coverage as cov

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "脚本.md"
            script.write_text(
                "开场\n\n## WeLink 的使用说明\n\n搜 WeLink 就能找到。\n",
                encoding="utf-8",
            )
            sections = cov.parse_script_sections(script)
            self.assertEqual(len(sections), 2)
            self.assertEqual(sections[1]["id"], "H1")
            self.assertTrue(sections[1]["is_heading_section"])
            anchor = sections[1]["heading_anchor"]
            self.assertIn("welink", anchor.lower())
            self.assertTrue(cov.anchor_in_corpus(anchor, "fork之后的welink的使用说明"))
            self.assertFalse(cov.anchor_in_corpus(anchor, "只有welink没有使用说明"))

    def test_proper_noun_repeat_filtered_from_residue(self):
        norms = residue.load_proper_noun_norms()
        self.assertTrue(residue.repeat_is_proper_noun_only("Obsidian", norms))


if __name__ == "__main__":
    unittest.main()
