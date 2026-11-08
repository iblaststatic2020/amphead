"""What the agent says, with a stand-in engine behind it.

Hermes is the amp, so the tools are the product: if a sentence in chat loads a capture, arms the
master and reports what the meter is doing, then the pane merely draws what the agent can already
do from a sentence.
"""

import unittest

from amphead import tools


class FakeRig:
    def __init__(self):
        self.calls = []
        self.db = -60.0
        self.armed = False
        self.open = False
        self.running = False
        self.capture = "Bugera 6262 Lead"
        self.capture_path = "C:/nam/bugera.nam"
        self.cab = "AURORA 212 GOV"
        self.cab_path = "C:/irs/aurora.wav"
        self.peak = 0.25
        self.clip = False
        self.input_channel = 1

    def state(self):
        return {"open": self.open, "running": self.running, "armed": self.armed,
                "master_db": self.db, "engine": "amphead2", "capture": self.capture,
                "capture_path": self.capture_path, "cab": self.cab,
                "cab_path": self.cab_path, "input_gain_db": 0.0, "output_gain_db": 0.0,
                "tone": {"bass": 5.0, "mid": 5.0, "treble": 5.0},
                "device": {"closed": not self.open, "inputs": 2, "mode": "wdm-ks",
                           "buffer": "512", "rate": "44100", "latency_out_ms": 5.1},
                "latency_ms": 10.2, "input_channel": self.input_channel}

    def meter(self, track=1):
        return {"l": self.peak, "r": self.peak, "clip": self.clip}

    def master(self):
        return {"db": self.db}

    def fx_list(self, track=1):
        return [{"i": 0, "name": "capture", "enabled": True},
                {"i": 1, "name": "tone", "enabled": True},
                {"i": 2, "name": "cab", "enabled": True}]

    def inputs(self):
        return {"count": 2, "channels": [{"i": 0, "name": "IN 1"}, {"i": 1, "name": "IN 2"}]}

    def library(self):
        return {"captures": [{"name": "Bugera 6262 Lead", "path": "C:/nam/bugera.nam"}],
                "cabs": [{"name": "AURORA 212 GOV", "path": "C:/irs/aurora.wav"}],
                "capture": "C:/nam/bugera.nam", "cab": "C:/irs/aurora.wav"}

    def ensure(self):
        self.calls.append(("ensure",))
        self.open = self.running = True
        return {"open": True, "master_db": self.master_db}

    def start(self, **kw):
        self.calls.append(("start",))
        self.open = self.running = True
        return {"open": True, "master_db": self.db}

    def arm(self, track=1, monitor=True, allow_loud=False):
        self.calls.append(("arm", self.db))
        self.armed = True
        return {"armed": True, "master_db": self.db}

    def disarm(self, track=1):
        self.calls.append(("disarm",))
        self.armed = False
        return {"armed": False}

    def set_master(self, db):
        self.calls.append(("master", db))
        self.db = db
        return {"db": db}

    def set_track_level(self, track=1, db=0.0):
        return self.set_master(db)

    def set_param(self, track=1, fx=1, param=None, value=None, mode="auto"):
        self.calls.append(("param", param, value))
        return {"param_name": str(param), "landed": value, "fmt": f"{float(value):+.1f} dB"}

    def set_input(self, track=1, spec=1):
        self.calls.append(("input", spec))
        self.input_channel = int(spec)
        return {"value": int(spec)}

    def open_capture(self, path):
        self.calls.append(("capture", path))
        self.capture_path = path
        return {"capture": path.split("/")[-1], "capture_path": path}

    def open_cab(self, path):
        self.calls.append(("cab", path))
        self.cab_path = path
        return {"cab": path.split("/")[-1], "cab_path": path}

    def fx_bypass(self, track=1, fx=1, on=False):
        self.calls.append(("bypass", fx, on))
        return {"fx": fx, "bypassed": on}


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.rig = FakeRig()
        self._saved = tools.RIG
        tools.RIG = self.rig
        self._saved_level = tools.RIG

    def tearDown(self):
        tools.RIG = self._saved

    def test_the_names_are_the_ones_the_manifest_sells(self):
        names = {name for name, _schema, _handler, _emoji in tools.TOOLS}
        for name in ("amphead_status", "amphead_setup", "amphead_teardown", "amphead_tone",
                     "amphead_live", "amphead_panic", "amphead_set", "amphead_settings"):
            self.assertIn(name, names)

    def test_ping_answers_even_with_nothing_running(self):
        said = tools.handle_ping({})
        self.assertIn("amphead", said.lower())


if __name__ == "__main__":
    unittest.main()
