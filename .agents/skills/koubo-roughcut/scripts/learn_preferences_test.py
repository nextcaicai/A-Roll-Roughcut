"""Preference candidates from review_log.json."""

import json
import tempfile
import unittest
from pathlib import Path

from learn_preferences import events_from_log, find_review_logs, gather, render_md


def log_payload(*, restored=(), added=(), unheard=()):
    return {
        "diff": {"dropsRestored": list(restored), "dropsAdded": list(added)},
        "flags": {"unheard": list(unheard)},
    }


class FindLogs(unittest.TestCase):
    def test_finds_runs_and_skips_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "片子" / "runs" / "20260101-1200" / "review_log.json"
            skip = root / ".tmp-pipe" / "runs" / "x" / "review_log.json"
            keep.parent.mkdir(parents=True)
            skip.parent.mkdir(parents=True)
            keep.write_text("{}", encoding="utf-8")
            skip.write_text("{}", encoding="utf-8")
            self.assertEqual(find_review_logs(root), [keep])


class Events(unittest.TestCase):
    def test_reads_restored_added_and_unheard(self):
        path = Path("片子/runs/r1/review_log.json")
        events = events_from_log(
            path,
            log_payload(
                restored=[{"id": "d1", "sourceStart": 1, "sourceEnd": 3, "reason": "retake", "text": "再见"}],
                added=[{"id": "d2", "sourceStart": 8, "sourceEnd": 9, "reason": "manual", "text": "嗯"}],
                unheard=[{"id": "f1", "sourceStart": 12, "note": "两遍都先留"}],
            ),
        )
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds, ["restored", "added", "unheard"])
        self.assertEqual(events[0]["run"], "片子/r1")
        self.assertEqual(events[0]["duration"], 2.0)

    def test_old_log_without_flags_is_fine(self):
        events = events_from_log(Path("a/runs/r/review_log.json"), {"diff": {"dropsAdded": []}})
        self.assertEqual(events, [])


class Report(unittest.TestCase):
    def test_empty_logs_say_do_not_invent_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "片" / "runs" / "r"
            run.mkdir(parents=True)
            (run / "review_log.json").write_text(json.dumps(log_payload()), encoding="utf-8")
            events, used, empty = gather([run / "review_log.json"])
            md = render_md(events, used, empty, scanned=1)
            self.assertIn("没有可学的审片差异", md)
            self.assertIn("不要编规则", md)

    def test_groups_same_reason_and_prefix(self):
        events = events_from_log(
            Path("片/runs/r/review_log.json"),
            log_payload(
                restored=[
                    {"id": "d1", "sourceStart": 1, "sourceEnd": 2, "reason": "retake", "text": "希望对大家有启发啊"},
                    {"id": "d2", "sourceStart": 8, "sourceEnd": 9, "reason": "retake", "text": "希望对大家有启发吧"},
                ]
            ),
        )
        md = render_md(events, ["片/r"], [], scanned=1)
        self.assertIn("人恢复的删除", md)
        self.assertIn("retake × 2", md)
        self.assertIn("希望对大家有启发", md)
