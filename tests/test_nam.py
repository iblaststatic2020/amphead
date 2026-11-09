"""The capture engine has to match the reference, not merely make a sound.

The golden file in fixtures/ was produced by the upstream `neural-amp-modeler`
PyTorch package from a small random graph we generated ourselves, so this checks
the maths without shipping anyone's capture or taking torch as a test dependency.
It is plain two-column text rather than a .npz so the numbers are readable in a diff.
"""

import json
import unittest
from pathlib import Path

try:
    import numpy as np
    from amphead.nam import NotSupported, WaveNet
except ModuleNotFoundError:  # pragma: no cover - the engine is an optional extra
    np = None

if np is None:  # pragma: no cover
    raise unittest.SkipTest("numpy is not installed")

FIXTURES = Path(__file__).parent / "fixtures"
NAM = FIXTURES / "tiny_wavenet.nam"
REF = FIXTURES / "tiny_wavenet_ref.txt"
A2_NAM = FIXTURES / "tiny_wavenet_a2.nam"
A2_REF = FIXTURES / "tiny_wavenet_a2_ref.txt"


def stream(model, x, block):
    model.reset()
    return np.concatenate([model.process(x[i:i + block]) for i in range(0, x.size, block)])


@unittest.skipIf(np is None, "numpy is not installed (pip install numpy in .engine)")
class NamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Plain text on purpose: a reviewer and the install scanner can read every number, and a
        # .npz is an archive the scanner has to treat as opaque.
        data = np.loadtxt(REF)
        cls.x, cls.y = data[:, 0].astype(np.float32), data[:, 1].astype(np.float32)

    def tmp(self, doc, name):
        import tempfile

        path = Path(tempfile.mkdtemp()) / name
        path.write_text(json.dumps(doc))
        return path

    def test_fixture_loads(self):
        model = WaveNet.load(NAM)
        doc = json.loads(NAM.read_text())
        self.assertEqual(model.receptive_field, 21)
        self.assertAlmostEqual(model.head_scale, doc["config"]["head_scale"], places=6)

    def test_streaming_matches_reference(self):
        for block in (32, 64, 128, 257, 1000):
            with self.subTest(block=block):
                model = WaveNet.load(NAM)
                got = stream(model, self.x, block)
                self.assertEqual(got.size, self.y.size)
                skip = model.receptive_field
                err = float(np.max(np.abs(got[skip:] - self.y[skip:])))
                self.assertLess(err, 1e-5)

    def test_a2_streaming_matches_reference(self):
        data = np.loadtxt(A2_REF)
        x, want = data[:, 0].astype(np.float32), data[:, 1].astype(np.float32)
        model = WaveNet.load(A2_NAM)
        self.assertEqual(model.receptive_field, 10)
        for block in (32, 64, 128, 257, 1000):
            with self.subTest(block=block):
                got = stream(model, x, block)
                self.assertEqual(got.size, want.size)
                err = float(np.max(np.abs(got - want)))
                self.assertLess(err, 1e-5)

    def test_a2_reset_makes_it_repeatable(self):
        model = WaveNet.load(A2_NAM)
        first = stream(model, self.x, 128)
        second = stream(model, self.x, 128)
        np.testing.assert_array_equal(first, second)

    def test_block_size_does_not_change_the_result(self):
        many = stream(WaveNet.load(NAM), self.x, 128)
        one = stream(WaveNet.load(NAM), self.x, self.x.size)
        self.assertLess(float(np.max(np.abs(many - one))), 1e-5)

    def test_reset_makes_it_repeatable(self):
        model = WaveNet.load(NAM)
        first = stream(model, self.x[:2000], 128)
        second = stream(model, self.x[:2000], 128)
        self.assertTrue(np.array_equal(first, second))

    def test_output_is_mono_float32_and_finite(self):
        got = stream(WaveNet.load(NAM), self.x, 128)
        self.assertEqual(got.dtype, np.float32)
        self.assertEqual(got.ndim, 1)
        self.assertTrue(np.all(np.isfinite(got)))

    def test_silence_gives_a_constant_offset_not_a_note(self):
        """A net with biases has a DC offset on silence; it must not ring or drift."""
        got = stream(WaveNet.load(NAM), np.zeros(8192, dtype=np.float32), 128)
        tail = got[4096:]
        self.assertLess(float(np.max(np.abs(tail - tail[0]))), 1e-5)

    def test_rejects_leftover_weights(self):
        doc = json.loads(NAM.read_text())
        doc["weights"] = doc["weights"] + [0.5]
        with self.assertRaisesRegex(NotSupported, "left over"):
            WaveNet.load(self.tmp(doc, "extra.nam"))

    def test_rejects_short_weights(self):
        doc = json.loads(NAM.read_text())
        doc["weights"] = doc["weights"][:-5]
        with self.assertRaises(NotSupported):
            WaveNet.load(self.tmp(doc, "short.nam"))

    def test_rejects_gated_activation(self):
        doc = json.loads(NAM.read_text())
        doc["config"]["layers"][0]["gated"] = True
        with self.assertRaisesRegex(NotSupported, "gated"):
            WaveNet.load(self.tmp(doc, "gated.nam"))

    def test_rejects_other_activations(self):
        doc = json.loads(NAM.read_text())
        doc["config"]["layers"][0]["activation"] = "ReLU"
        with self.assertRaisesRegex(NotSupported, "ReLU"):
            WaveNet.load(self.tmp(doc, "relu.nam"))

    def test_rejects_non_wavenet(self):
        doc = {"architecture": "LSTM", "config": {}, "weights": []}
        with self.assertRaisesRegex(NotSupported, "WaveNet"):
            WaveNet.load(self.tmp(doc, "lstm.nam"))

    def container(self, *submodels):
        return {
            "version": "0.7.0",
            "architecture": "SlimmableContainer",
            "config": {"submodels": list(submodels)},
            "weights": [],
            "sample_rate": 48000,
        }

    def test_slimmable_container_uses_the_full_submodel(self):
        full = json.loads(NAM.read_text())
        quiet = json.loads(NAM.read_text())
        quiet["config"]["head_scale"] = 0.0
        doc = self.container(
            {"max_value": 1.0, "model": full},
            {"max_value": 0.5, "model": quiet},
        )
        model = WaveNet.load(self.tmp(doc, "container.nam"))
        got = stream(model, self.x, 128)
        want = stream(WaveNet.load(NAM), self.x, 128)
        self.assertLess(float(np.max(np.abs(got - want))), 1e-5)

    def test_slimmable_container_can_hold_an_a2_submodel(self):
        full = json.loads(A2_NAM.read_text())
        quiet = json.loads(A2_NAM.read_text())
        quiet["config"]["head_scale"] = 0.0
        doc = self.container(
            {"max_value": 1.0, "model": full},
            {"max_value": 0.5, "model": quiet},
        )
        data = np.loadtxt(A2_REF)
        x = data[:, 0].astype(np.float32)
        got = stream(WaveNet.load(self.tmp(doc, "a2-container.nam")), x, 128)
        want = stream(WaveNet.load(A2_NAM), x, 128)
        self.assertLess(float(np.max(np.abs(got - want))), 1e-5)

    def test_slimmable_container_inherits_the_outer_sample_rate(self):
        child = json.loads(NAM.read_text())
        child.pop("sample_rate", None)
        model = WaveNet.load(
            self.tmp(self.container({"max_value": 1.0, "model": child}), "container.nam")
        )
        self.assertEqual(model.sample_rate, 48000)

    def test_rejects_a_container_with_no_submodels(self):
        with self.assertRaisesRegex(NotSupported, "no submodels"):
            WaveNet.load(self.tmp(self.container(), "empty-container.nam"))

    def test_rejects_a_container_submodel_that_is_not_wavenet(self):
        doc = self.container(
            {"max_value": 1.0, "model": {"architecture": "LSTM", "config": {}, "weights": []}}
        )
        with self.assertRaisesRegex(NotSupported, "LSTM"):
            WaveNet.load(self.tmp(doc, "lstm-container.nam"))

    def test_rejects_a_container_without_a_numeric_max(self):
        child = json.loads(NAM.read_text())
        doc = self.container({"max_value": "loud", "model": child})
        with self.assertRaisesRegex(NotSupported, "numeric max_value"):
            WaveNet.load(self.tmp(doc, "bad-container.nam"))

    def test_rejects_separate_output_head(self):
        doc = json.loads(NAM.read_text())
        doc["config"]["head"] = {"out_channels": 1, "kernel_size": 1, "bias": True}
        with self.assertRaisesRegex(NotSupported, "output head"):
            WaveNet.load(self.tmp(doc, "head.nam"))

    def test_single_tap_capture_is_a_tanh(self):
        """Pins tap order: a capture at delay zero with unit weight returns tanh(x)."""
        doc = {
            "version": "0.5.0",
            "architecture": "WaveNet",
            "config": {
                "layers": [
                    {
                        "input_size": 1,
                        "condition_size": 1,
                        "head_size": 1,
                        "channels": 1,
                        "kernel_size": 1,
                        "dilations": [1],
                        "activation": "Tanh",
                        "gated": False,
                        "head_bias": False,
                    }
                ],
                "head": None,
                "head_scale": 1.0,
            },
            # rechannel, conv w, conv b, mixer, 1x1 w, 1x1 b, head w, head scale
            "weights": [1.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0],
        }
        model = WaveNet.load(self.tmp(doc, "tap.nam"))
        x = np.array([0.0, 0.1, -0.2, 0.05], dtype=np.float32)
        self.assertTrue(np.allclose(stream(model, x, 2), np.tanh(x), atol=1e-6))


if __name__ == "__main__":
    unittest.main()
