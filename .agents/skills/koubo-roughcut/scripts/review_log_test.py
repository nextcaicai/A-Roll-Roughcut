"""Review diff: frozen pipeline baseline, time-based diff, heard flags."""

import json
import tempfile
import unittest
from pathlib import Path

import review_server
from decisions_common import PIPELINE_SNAPSHOT, recompute_timeline
from review_log import build_review_log, diff_drops


def decisions(keep, drop, flags=None):
    data = {
        "version": 1,
        "source": {"path": "a.mp4", "duration": 20.0},
        "keep": [{"id": f"k{i:03d}", "sourceStart": s, "sourceEnd": e, "reason": "speech"} for i, (s, e) in enumerate(keep, 1)],
        "drop": [
            {"id": f"d{i:03d}", "sourceStart": s, "sourceEnd": e, "reason": r, "text": ""}
            for i, (s, e, r) in enumerate(drop, 1)
        ],
        "flags": flags or [],
    }
    recompute_timeline(data)
    return data


WORDS = [(1.0, 1.5, "我"), (1.5, 2.0, "说"), (5.0, 5.5, "重"), (5.5, 6.0, "说"), (9.0, 9.5, "好")]


class FrozenBaseline(unittest.TestCase):
    def test_first_open_freezes_pipeline_output_and_later_opens_reuse_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            pipeline = decisions([(0, 4), (8, 12)], [(4, 8, "retake")])
            (run / "cut_decisions.json").write_text(json.dumps(pipeline), encoding="utf-8")

            first, created = review_server.load_baseline(run)
            self.assertTrue(created)
            self.assertTrue((run / PIPELINE_SNAPSHOT).is_file())

            edited = decisions([(0, 12)], [])
            (run / "cut_decisions.json").write_text(json.dumps(edited), encoding="utf-8")
            second, created = review_server.load_baseline(run)
            self.assertFalse(created)
            self.assertEqual(first, second)
            self.assertEqual(len(second["drop"]), 1)

    def test_no_decisions_means_no_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(review_server.load_baseline(Path(tmp)), (None, False))


class TimeDiff(unittest.TestCase):
    def test_restoring_half_of_a_drop_is_one_restore_not_restore_plus_add(self):
        base = decisions([(0, 4), (8, 12)], [(4, 8, "retake")])
        final = decisions([(0, 6), (8, 12)], [(6, 8, "retake")])
        diff = diff_drops(base, final, words=WORDS)
        self.assertEqual(diff["dropsAddedCount"], 0)
        self.assertEqual(diff["dropsRestoredCount"], 1)
        got = diff["dropsRestored"][0]
        self.assertEqual((got["sourceStart"], got["sourceEnd"]), (4.0, 6.0))
        self.assertEqual(got["reason"], "retake")
        self.assertEqual(got["text"], "重说")

    def test_relabelling_or_splitting_a_drop_is_not_a_change(self):
        base = decisions([(0, 4), (8, 12)], [(4, 8, "retake")])
        final = decisions([(0, 4), (8, 12)], [(4, 6, "manual"), (6, 8, "retake")])
        diff = diff_drops(base, final, words=WORDS)
        self.assertEqual((diff["dropsRestoredCount"], diff["dropsAddedCount"]), (0, 0))

    def test_new_cut_is_reported_with_its_reason(self):
        base = decisions([(0, 12)], [])
        final = decisions([(0, 8.8), (9.6, 12)], [(8.8, 9.6, "manual")])
        diff = diff_drops(base, final, words=WORDS)
        self.assertEqual(diff["dropsAddedCount"], 1)
        self.assertEqual(diff["dropsAdded"][0]["reason"], "manual")
        self.assertEqual(diff["dropsAdded"][0]["text"], "好")

    def test_lead_in_jitter_is_ignored(self):
        base = decisions([(0, 4), (8, 12)], [(4, 8, "retake")])
        final = decisions([(0, 4), (7.95, 12)], [(4, 7.95, "retake")])
        diff = diff_drops(base, final, words=WORDS)
        self.assertEqual(diff["dropsRestoredCount"], 0)


class HeardFlags(unittest.TestCase):
    def test_log_lists_flags_nobody_opened(self):
        flags = [
            {"id": "f001", "sourceStart": 5.0, "note": "两遍都留着", "heard": True},
            {"id": "f002", "sourceStart": 9.0, "note": "专名口糊"},
        ]
        data = decisions([(0, 12)], [], flags)
        with tempfile.TemporaryDirectory() as tmp:
            log = build_review_log(run_dir=Path(tmp), baseline=data, final=data, session={})
        self.assertEqual(log["baseline"]["from"], PIPELINE_SNAPSHOT)
        self.assertEqual(log["flags"]["total"], 2)
        self.assertEqual(log["flags"]["heardCount"], 1)
        self.assertEqual([f["id"] for f in log["flags"]["unheard"]], ["f002"])


if __name__ == "__main__":
    unittest.main()
