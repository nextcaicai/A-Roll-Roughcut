"""Constant-frame-rate rough cut uses the same grid as the XML. No ffmpeg."""

import unittest

import render_roughcut


class ConstantRate(unittest.TestCase):
    def test_bounds_match_the_xml_grid(self):
        keep = [
            {"sourceStart": 1.02, "sourceEnd": 1.40},
            {"sourceStart": 3.00, "sourceEnd": 3.50},
        ]
        bounds = render_roughcut.segment_bounds(keep, 25, 1)
        self.assertEqual(bounds[0], (1.04, 1.4, 9))
        self.assertEqual(bounds[1], (3.0, 3.52, 13))

    def test_each_piece_is_locked_to_the_subtitle_frame_count(self):
        graph = render_roughcut.filter_graph([(1.04, 1.40, 9), (3.0, 3.52, 13)], 25, True)
        self.assertIn("fps=25:eof_action=pass", graph)
        self.assertIn("trim=end_frame=9", graph)
        self.assertIn("trim=end_frame=13", graph)
        self.assertIn("tpad=stop_mode=clone:stop=-1", graph)
        self.assertIn("apad=pad_dur=1,atrim=end=0.360000", graph)
        self.assertIn("atrim=end=0.520000", graph)
        self.assertIn("trim=start=1.040000:end=1.400000", graph)
        self.assertIn("concat=n=2:v=1:a=1", graph)

    def test_progress_uses_written_time_and_speed(self):
        self.assertEqual(render_roughcut.parse_clock("00:01:02.500000"), 62.5)
        self.assertEqual(render_roughcut.parse_clock("N/A"), 0.0)
        self.assertEqual(render_roughcut.parse_clock("01:00:00.000000"), 3600.0)
        self.assertIsNone(render_roughcut.parse_speed("N/A"))
        self.assertEqual(render_roughcut.parse_speed("1.25x"), 1.25)
        mid = render_roughcut.progress_snapshot(50, 2, 100, False)
        self.assertEqual(mid["fraction"], 0.5)
        self.assertEqual(mid["remaining"], 25)
        early = render_roughcut.progress_snapshot(0, None, 100, False)
        self.assertEqual(early["fraction"], 0)
        self.assertIsNone(early["remaining"])
        done = render_roughcut.progress_snapshot(100, 1, 100, True)
        self.assertEqual(done["fraction"], 1.0)
        self.assertIsNone(done["remaining"])
        over = render_roughcut.progress_snapshot(120, 1, 100, False)
        self.assertEqual(over["fraction"], 0.99)


if __name__ == "__main__":
    unittest.main()
