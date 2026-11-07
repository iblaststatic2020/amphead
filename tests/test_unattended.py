"""The three read-only tools must answer with no desktop attached.

A scheduled run, a cron job or a headless tool call has no window, no terminal and nothing on
stdin. The failure this pins is the quiet one: a handler that blocks on a prompt, or throws a
traceback instead of saying "not running", looks fine in a chat and is broken at 6am.

Each tool runs in its own process with stdin closed and the rig pointed at a backend that has no
engine, so this never touches a real session, never launches anything and never moves a fader.
"""

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Read-only or state-reading tools a caller may run unattended.
TOOLS = {
    "amphead_ping": ({}, "amphead2"),
    "amphead_status": ({}, "amphead2"),
    "amphead_ready": ({}, "amphead2"),
}

RUNNER = (
    "import json, sys\n"
    "from amphead.tools import TOOLS\n"
    "name, args = sys.argv[1], json.loads(sys.argv[2])\n"
    "handler = next(h for n, s, h, e in TOOLS if n == name)\n"
    "out = handler(args)\n"
    "print(out)\n"
)


class Unattended(unittest.TestCase):
    def _run(self, name, args, engine):
        env = dict(os.environ)
        env["AMPHEAD_ENGINE"] = engine
        env.pop("DISPLAY", None)
        return subprocess.run(
            [sys.executable, "-c", RUNNER, name, json.dumps(args)],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_each_tool_answers_with_stdin_closed(self):
        for name, (args, engine) in TOOLS.items():
            with self.subTest(tool=name):
                done = self._run(name, args, engine)
                self.assertEqual(done.returncode, 0, f"{name} died: {done.stderr[-800:]}")
                self.assertNotIn("Traceback", done.stderr, f"{name} raised instead of answering")
                line = done.stdout.strip()
                self.assertTrue(line, f"{name} answered with silence")
                self.assertEqual(len(line.splitlines()), 1, f"{name} answered with a wall of text")
                self.assertGreater(len(line), 20, f"{name} answered with a fragment")

    def test_the_answer_names_a_state_a_person_can_act_on(self):
        """Not "None" or "error": the sentence has to say what the caller should do about it."""
        for name, (args, engine) in TOOLS.items():
            with self.subTest(tool=name):
                line = self._run(name, args, engine).stdout.strip().lower()
                self.assertTrue(
                    any(word in line for word in ("not running", "not installed", "standby",
                                                  "verdict=", "absent", "ready")),
                    f"{name} said {line!r}, which names no state",
                )

    def test_no_handler_waits_on_a_prompt(self):
        """A prompt is the one thing that turns a scheduled call into a hung job.

        Matched as a call, not a substring: ``set_input(`` is a function of ours, ``input(`` is
        a person waiting at a keyboard, and prose that says "the first input (mono channel 1)" is
        neither. So: no space before the paren, and comments are not code.
        """
        waiting = re.compile(r"(?<![\w.])input\(|msvcrt\.getch|getpass\.getpass\(")
        for path in sorted((ROOT / "amphead").glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                hit = waiting.search(line)
                self.assertIsNone(
                    hit, f"{path.name}:{number} would block waiting for a person: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
