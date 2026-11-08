"""A file that will not load gets a sentence that names the next step, not an exception class.

The engine still raises the truth; the fix rides beside it. These tests use the real exceptions
the loaders raise, so the wording is checked against what actually comes out, not against a
made-up message.
"""

import unittest

try:
    from amphead import faults, nam
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("amphead not importable")


class TestWhatThePlayerIsTold(unittest.TestCase):
    def test_a_capture_of_the_wrong_architecture_says_export_it_as_wavenet(self):
        exc = nam.NotSupported("only WaveNet captures are supported, this one is 'LSTM'")
        said = faults.explain(exc, faults.CAPTURE, "C:/packs/thing.nam")
        self.assertIn("thing.nam", said)
        self.assertIn("WaveNet", said)
        self.assertIn("export", said.lower())

    def test_a_moved_file_names_the_settings_commands(self):
        exc = FileNotFoundError(2, "No such file or directory", "C:/gone/lead.nam")
        said = faults.explain(exc, faults.CAPTURE, "C:/gone/lead.nam")
        self.assertIn("lead.nam", said)
        self.assertIn("amphead_settings action=find", said)
        self.assertIn("action=set", said)

    def test_a_cabinet_that_is_not_pcm_says_what_to_export(self):
        said = faults.explain(ValueError("unsupported wav sample width: 5 bytes"),
                              faults.CAB, "C:/irs/odd.wav")
        self.assertIn("odd.wav", said)
        self.assertIn("16 or", said)
        self.assertIn("24 bit", said)

    def test_an_empty_impulse_says_it_has_no_audio(self):
        said = faults.explain(ValueError("an impulse response needs at least two samples"),
                              faults.CAB, "C:/irs/empty.wav")
        self.assertIn("no audio", said)

    def test_a_locked_file_says_windows_held_it(self):
        said = faults.explain(PermissionError(13, "Permission denied", "C:/x/a.nam"),
                              faults.CAPTURE, "C:/x/a.nam")
        self.assertIn("Close whatever has it open", said)

    def test_a_truncated_capture_says_to_copy_it_again(self):
        exc = nam.NotSupported("weights ended at 130 of 1000 while reading 200 values")
        self.assertIn("truncated", faults.explain(exc, faults.CAPTURE, "a.nam"))

    def test_something_unrecognised_gets_no_padding(self):
        """A sentence invented for an unknown failure is worse than the exception text."""
        self.assertEqual("", faults.explain(RuntimeError("something odd"), "", ""))

    def test_no_dashes_in_anything_it_says(self):
        samples = [
            (nam.NotSupported("only WaveNet captures are supported, this one is 'LSTM'"),
             faults.CAPTURE, "a.nam"),
            (FileNotFoundError(2, "no", "b.nam"), faults.CAPTURE, "b.nam"),
            (ValueError("unsupported wav sample width: 5 bytes"), faults.CAB, "c.wav"),
            (PermissionError(13, "denied", "d.nam"), faults.CAPTURE, "d.nam"),
        ]
        for exc, kind, path in samples:
            said = faults.explain(exc, kind, path)
            self.assertNotIn("\u2014", said)
            self.assertNotIn(" - ", said)


if __name__ == "__main__":
    unittest.main()
