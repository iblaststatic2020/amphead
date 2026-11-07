"""The tool-call answer is a JSON string, and it still carries the sentence a person reads.

The plugin guide (T:115) asks a handler to return `json.dumps(...)`, never a raw dict or a bare
line of prose. The panes, the tests and everything a player has read so far are built on the
sentence the handlers return, so the shape is wrapped at the boundary instead of rewritten: the
handler keeps answering as it always did, the function the host registers answers in JSON, and the
sentence rides inside it as `summary`. This file is the pin on both halves of that.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from amphead import tools

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "__init__.py"

RUNNER = (
    "import json, sys\n"
    "from amphead.tools import json_tools\n"
    "name = sys.argv[1]\n"
    "handler = next(h for n, s, h, e in json_tools() if n == name)\n"
    "print(handler({}))\n"
)


class EveryToolAnswersInJson(unittest.TestCase):
    def test_the_registered_set_is_the_same_set_of_tools(self):
        names = [name for name, schema, handler, emoji in tools.TOOLS]
        self.assertEqual([name for name, schema, handler, emoji in tools.json_tools()], names)

    def test_register_wires_the_json_handlers_not_the_raw_ones(self):
        source = INIT.read_text(encoding="utf-8")
        self.assertIn("json_tools()", source, "the plugin registers raw handlers again")
        self.assertNotIn("from .amphead.tools import TOOLS", source)

    def test_the_wrapper_does_not_touch_the_work(self):
        seen = []

        def handler(args):
            seen.append(args)
            return "changed=9 | already there=1 | 1.11s"

        answer = tools.call_shape("amphead_tone", handler)({"param": 3})
        self.assertIsInstance(answer, str, "a handler that returns a dict fails the guide")
        payload = json.loads(answer)
        self.assertEqual(seen, [{"param": 3}], "the wrapper changed the arguments")
        self.assertEqual(payload["tool"], "amphead_tone")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"], "changed=9 | already there=1 | 1.11s")
        self.assertEqual(payload["fields"], {"changed": "9", "already there": "1"})

    def test_a_refusal_is_marked_as_one(self):
        for sentence in ("REFUSED: going live arms the rig. Call again with confirm=true.",
                         "NOT LIVE: no project is open, call amphead_setup first",
                         "NOT RUNNING: REAPER is not open"):
            with self.subTest(sentence=sentence[:12]):
                payload = json.loads(tools.json_answer("amphead_live", sentence))
                self.assertFalse(payload["ok"], sentence)
                self.assertEqual(payload["summary"], sentence)

    def test_a_sentence_with_no_numbers_has_no_fields(self):
        payload = json.loads(tools.json_answer("amphead_ready", "READY: nothing is clipping"))
        self.assertNotIn("fields", payload)

    def test_the_wrapped_handler_answers_with_no_rig_attached(self):
        """End to end, in a cold process: the shape a cron job would actually receive."""
        env = dict(os.environ)
        env["AMPHEAD_ENGINE"] = "amphead2"
        env.pop("DISPLAY", None)
        done = subprocess.run(
            [sys.executable, "-c", RUNNER, "amphead_status"],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        payload = json.loads(done.stdout.strip())
        self.assertEqual(payload["tool"], "amphead_status")
        self.assertIsInstance(payload["summary"], str)
        self.assertTrue(payload["summary"].strip())


if __name__ == "__main__":
    unittest.main()
