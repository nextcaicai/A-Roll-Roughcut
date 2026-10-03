"""Remembered export folder. No GUI."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import review_server


class RememberedFolder(unittest.TestCase):
    def test_missing_store_uses_the_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp)
            store = fallback / ".export-dir"
            self.assertEqual(review_server.remembered_export_dir(fallback, store), fallback)

    def test_saved_folder_is_the_next_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fallback = root / "run"
            chosen = root / "desk"
            fallback.mkdir()
            chosen.mkdir()
            store = root / ".export-dir"
            review_server.remember_export_dir(chosen, store)
            self.assertEqual(review_server.remembered_export_dir(fallback, store), chosen.resolve())

    def test_gone_folder_falls_back_to_the_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fallback = root / "run"
            fallback.mkdir()
            store = root / ".export-dir"
            store.write_text(str(root / "missing") + "\n", encoding="utf-8")
            self.assertEqual(review_server.remembered_export_dir(fallback, store), fallback)

    def test_basename_keeps_two_pieces_apart(self):
        run = Path("/work/codex多模型/runs/20260923-1618")
        self.assertEqual(
            review_server.export_basename(run, "fcpxml"),
            "codex多模型-20260923-1618.fcpxml",
        )
        self.assertEqual(
            review_server.export_basename(run, "srt"),
            "codex多模型-20260923-1618.srt",
        )

    def test_cancel_does_not_change_the_remembered_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fallback = root / "run"
            fallback.mkdir()
            store = root / ".export-dir"

            def canceled(*_args, **_kwargs):
                return SimpleNamespace(returncode=1, stdout="", stderr="User canceled. (-128)")

            with self.assertRaises(review_server.ExportCanceled):
                review_server.choose_export_file(fallback, "片.srt", "导出字幕", store=store, run=canceled)
            self.assertFalse(store.exists())

    def test_renamed_file_is_kept_and_its_folder_is_remembered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fallback = root / "run"
            desk = root / "desk"
            fallback.mkdir()
            desk.mkdir()
            chosen = desk / "新名字.srt"
            store = root / ".export-dir"
            seen = {}

            def picked(cmd, **_kwargs):
                seen["cmd"] = cmd
                return SimpleNamespace(returncode=0, stdout=str(chosen) + "\n", stderr="")

            got = review_server.choose_export_file(fallback, "旧名字.srt", "导出字幕", store=store, run=picked)
            self.assertEqual(got, chosen.resolve())
            self.assertEqual(store.read_text(encoding="utf-8").strip(), str(desk.resolve()))
            self.assertIn("旧名字.srt", seen["cmd"])
            self.assertIn("choose file name", seen["cmd"][2])


if __name__ == "__main__":
    unittest.main()
