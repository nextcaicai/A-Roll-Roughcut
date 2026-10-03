"""XML and SRT share one integer frame grid. No network."""

import unittest
from pathlib import Path

import export_fcpxml
import export_srt


class SequenceRate(unittest.TestCase):
    def test_peak_rate_becomes_25(self):
        self.assertEqual(export_fcpxml.sequence_rate(299 / 12), (25, 1))

    def test_common_rates_stay_put(self):
        self.assertEqual(export_fcpxml.sequence_rate(24), (24, 1))
        self.assertEqual(export_fcpxml.sequence_rate(30), (30, 1))
        self.assertEqual(export_fcpxml.sequence_rate(23.976), (24, 1))
        self.assertEqual(export_fcpxml.sequence_rate(29.97), (30, 1))
        self.assertEqual(export_fcpxml.sequence_rate(59.94), (60, 1))


class SameGrid(unittest.TestCase):
    def test_subtitle_starts_on_the_same_frame_as_the_clip(self):
        media = {
            "fps_n": 25,
            "fps_d": 1,
            "duration": 10,
            "width": 1920,
            "height": 1080,
            "has_audio": True,
            "audio_rate": 44100,
            "audio_channels": 2,
        }
        keep = [
            {"sourceStart": 1.02, "sourceEnd": 1.40, "text": "前"},
            {"sourceStart": 3.00, "sourceEnd": 3.50, "text": "后"},
        ]
        xml = export_fcpxml.build_fcpxml(
            source=Path("/tmp/a.mp4"),
            project_name="t",
            keep=keep,
            media=media,
        )
        self.assertIn('frameDuration="1/25s"', xml)
        start = export_fcpxml.to_frames(1.02, 25, 1)
        end = export_fcpxml.to_frames(1.40, 25, 1)
        dur = max(1, end - start)
        self.assertIn(f'offset="{export_fcpxml.fcpx_time(dur, 25, 1)}"', xml)
        text = export_srt.render_srt(
            {"segments": [{"words": [{"word": "后", "start": 3.0, "end": 3.2}]}]},
            {"keep": keep, "breath": {"maxKeep": 0.4}},
            fps=(25, 1),
        )
        self.assertIn(export_srt.format_timestamp(dur / 25), text)


if __name__ == "__main__":
    unittest.main()
