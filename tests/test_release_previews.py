import unittest
from pipeline.release_previews import verify_timing


class PairedPreviewTests(unittest.TestCase):
    def test_irregular_source_times_are_retained(self):
        self.assertLess(verify_timing([0,.033,.084],[0,.0330001,.0840001]),.000001)

    def test_missing_or_duplicate_preview_frames_fail(self):
        for actual in [[],[0,.033],[0,.033,.033]]:
            with self.assertRaises(ValueError):verify_timing(actual,[0,.033,.084])

    def test_decoder_timing_shift_is_not_hidden(self):
        with self.assertRaises(ValueError):verify_timing([0,.043,.094],[0,.033,.084])
