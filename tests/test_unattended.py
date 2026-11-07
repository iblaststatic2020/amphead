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
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Read-only or state-reading tools a caller may run unattended.
TOOLS = {
    "amphead_ping": ({}, "amphead2"),
    "amphead_status": ({}, "amphead2"),
    "amphead_ready": ({}, "amphead2"),
}

#: A live request with no confirm. It must be refused on the spot: this is the one tool that would
#: otherwise reach for REAPER's window, and a scheduled run must not be able to start a session by
#: leaving an argument out.
REFUSALS = {"amphead_live": ({"on": True}, "amphead2")}

#: The budget a scheduled caller gives a tool before calling it hung. Wall clock, cold interpreter
#: included, because that is what a cron slot actually spends.
BUDGET_SECONDS = 5.0

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
        started = time.monotonic()
        done = subprocess.run(
            [sys.executable, "-c", RUNNER, name, json.dumps(args)],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return done, time.monotonic() - started

    def test_each_tool_answers_with_stdin_closed(self):
        for name, (args, engine) in TOOLS.items():
            with self.subTest(tool=name):
                done, _ = self._run(name, args, engine)
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
                line = self._run(name, args, engine)[0].stdout.strip().lower()
                self.assertTrue(
                    any(word in line for word in ("not running", "not installed", "standby",
                                                  "verdict=", "absent", "ready")),
                    f"{name} said {line!r}, which names no state",
                )

    def test_each_answer_comes_back_inside_the_budget(self):
        """Five seconds, cold interpreter included. A tool that takes longer than that is a tool a
        cron slot spends its whole allowance waiting on, and the pilot is reading "no answer"."""
        for name, (args, engine) in TOOLS.items():
            with self.subTest(tool=name):
                done, elapsed = self._run(name, args, engine)
                self.assertEqual(done.returncode, 0)
                self.assertLess(elapsed, BUDGET_SECONDS,
                                f"{name} took {elapsed:.1f}s with no desktop attached")

    def test_a_live_request_without_confirm_is_refused_not_attempted(self):
        """The one tool that would reach for REAPER's window, called without its confirm.

        Refusal has to happen before anything is launched, which is why this runs with no engine
        at all: if the guard ever moves below the launch, this test starts a REAPER on the build
        machine instead of failing.

        The words are one of two, and which one depends on state: with no rig open the answer is
        NOT LIVE, "call amphead_setup first", because that is the next thing a person can act on;
        with a rig open and no confirm it is REFUSED. Both are refusals, so this pins the shape
        (a named state plus the next step) rather than one spelling.
        """
        for name, (args, engine) in REFUSALS.items():
            with self.subTest(tool=name):
                done, elapsed = self._run(name, args, engine)
                self.assertEqual(done.returncode, 0, f"{name} died: {done.stderr[-800:]}")
                self.assertNotIn("Traceback", done.stderr)
                line = done.stdout.strip()
                self.assertRegex(line, r"^(REFUSED|NOT LIVE)\b", f"{name} did not refuse: {line!r}")
                self.assertTrue(
                    any(next_step in line for next_step in ("confirm", "amphead_setup")),
                    f"{name} refused without saying what to do next: {line!r}",
                )
                self.assertLess(elapsed, BUDGET_SECONDS, f"{name} took {elapsed:.1f}s to refuse")

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
