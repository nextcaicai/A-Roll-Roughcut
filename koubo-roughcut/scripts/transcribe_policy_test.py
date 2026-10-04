"""Model-choice rules. No network."""

import unittest

import transcribe


class RejectEngine(unittest.TestCase):
    def test_default_paraformer_is_allowed(self):
        self.assertIsNone(transcribe.reject_engine("paraformer", "paraformer-v2"))
        self.assertIsNone(transcribe.reject_engine("bailian", "paraformer-v2"))

    def test_realtime_and_whisper_stop(self):
        for engine, model in (
            ("whisper", "paraformer-v2"),
            ("realtime", "paraformer-v2"),
            ("paraformer", "paraformer-realtime-v2"),
            ("paraformer", "fun-asr-realtime"),
            ("doubao", "whisper"),
        ):
            message = transcribe.reject_engine(engine, model)
            self.assertIsNotNone(message)
            self.assertIn("realtime", message)
            self.assertIn("Whisper", message)
            self.assertNotIn("pip install", message)

    def test_doubao_is_an_explicit_choice(self):
        self.assertIsNone(transcribe.reject_engine("doubao", "paraformer-v2"))


class DoubaoWords(unittest.TestCase):
    def test_word_times_stay_seconds_and_keep_punctuation(self):
        payload = {
            "result": {
                "text": "这是字节跳动，",
                "utterances": [
                    {
                        "start_time": 0,
                        "end_time": 1705,
                        "text": "这是字节跳动，",
                        "words": [
                            {"start_time": 740, "end_time": 860, "text": "这"},
                            {"start_time": 860, "end_time": 1020, "text": "是"},
                            {"start_time": 1020, "end_time": 1200, "text": "字"},
                            {"start_time": 1200, "end_time": 1400, "text": "节"},
                            {"start_time": 1400, "end_time": 1560, "text": "跳"},
                            {"start_time": 1560, "end_time": 1640, "text": "动"},
                        ],
                    }
                ],
            }
        }
        segments = transcribe.doubao_to_segments(payload)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start"], 0.0)
        self.assertEqual(segments[0]["end"], 1.705)
        self.assertEqual(segments[0]["words"][-1]["word"], "动，")
        self.assertEqual(segments[0]["words"][0]["start"], 0.74)


class ParaformerFailure(unittest.TestCase):
    def test_message_names_the_outage_and_does_not_switch(self):
        message = str(transcribe.paraformer_unavailable("提交转写 HTTP 500"))
        self.assertIn("暂时用不了", message)
        self.assertIn("实时模型", message)
        self.assertIn("Whisper", message)
        self.assertIn("--engine doubao", message)
        self.assertNotIn("pip install", message)


if __name__ == "__main__":
    unittest.main()
