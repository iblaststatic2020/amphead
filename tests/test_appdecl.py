"""The REAPER application declaration: one place that answers "is it here, and is it new enough".

The point of these tests is not that hermes_platform works. It is that AmpHead reads the host's
answer instead of hand-rolling its own, that it says something a person can act on for every
state the evaluator can return, and that a host without the library degrades to "could not
check" rather than "not installed".
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _host_library_available() -> bool:
    """True when hermes_platform can be imported, adding the Hermes install to sys.path if needed.

    The plugin runs inside the host, so the library is there in production but not in a bare
    checkout's test run. Finding it when it exists locally is what makes the parse test run for
    real here instead of skipping every time.
    """
    try:
        import hermes_platform  # noqa: F401
        return True
    except Exception:
        pass
    roots = [Path(os.environ.get("LOCALAPPDATA", "C:/nope")) / "hermes" / "hermes-agent",
             Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent"]
    for root in roots:
        if (root / "hermes_platform" / "declaration.py").is_file():
            sys.path.insert(0, str(root))
            try:
                import hermes_platform  # noqa: F401
                return True
            except Exception:
                return False
    return False


HOST_LIBRARY = _host_library_available()

from amphead import appdecl, launch, tools  # noqa: E402


class DeclarationShapeTests(unittest.TestCase):
    def test_the_declaration_parses_with_the_host_vocabulary(self):
        if not HOST_LIBRARY:
            self.skipTest("hermes_platform is not importable here")
        decl = appdecl.declaration()
        self.assertIsNotNone(decl)
        self.assertEqual(decl.name, "reaper")
        self.assertTrue(decl.requires_app)
        self.assertEqual(decl.min_version, appdecl.MIN_VERSION)
        self.assertIsNotNone(decl.app_for("win32"))

    def test_windows_block_names_the_usual_install_roots_and_a_version_source(self):
        win = appdecl.RAW_APP["win32"]
        joined = " ".join(str(loc) for loc in win["location"])
        self.assertIn("REAPER (x64)", joined)
        self.assertIn("uninstall_registry", joined)
        # min_version needs a real version source, not the default `none`.
        self.assertEqual(win["version"]["kind"], "pe_resource")

    def test_register_is_idempotent_and_never_raises(self):
        first = appdecl.register()
        second = appdecl.register()
        self.assertEqual(first, second)

    def test_register_is_quiet_when_the_library_is_missing(self):
        with mock.patch.object(appdecl, "declaration", return_value=None):
            self.assertFalse(appdecl.register())


class StateTests(unittest.TestCase):
    def test_state_is_a_known_word(self):
        found = appdecl.state()
        self.assertIn(found["state"],
                      {"available", "missing_app", "version_too_old", "unsupported_os",
                       "no_requirements", "unknown"})
        if found["state"] == "available":
            self.assertTrue(found["path"])
            self.assertTrue(Path(str(found["path"])).is_file())

    def test_missing_library_reads_as_could_not_check_not_as_missing(self):
        with mock.patch.object(appdecl, "declaration", return_value=None):
            found = appdecl.state()
            self.assertEqual(found["state"], "unknown")
            self.assertIn("did not resolve", appdecl.sentence(found))
            # "unknown" must not be reported as a reason to stop: we simply could not look.
            self.assertIsNone(appdecl.unavailable_reason())


    def test_the_host_itself_calls_an_absent_executable_missing_app(self):
        """Proof that the word comes from the host, not from our own if-chain.

        The declaration below points at a path nothing lives at, which is the same shape a machine
        without REAPER presents. If this ever reads `available`, the evaluator is being lied to by
        the locator and the plugin would send a player hunting for an install they do not have.
        """
        if not HOST_LIBRARY:
            self.skipTest("hermes_platform is not importable here")
        from hermes_platform import declaration as vocab
        from hermes_platform.resolver import availability as reader

        decl = vocab.parse_declaration(
            "reaper",
            raw_app={
                "win32": {
                    "presence": "executable",
                    "location": ["C:/nowhere/reaper.exe"],
                    "version": {"kind": "pe_resource"},
                }
            },
            raw_requires={"app": True, "min_version": "7.0"},
            where="test",
        )
        found = reader.availability(decl).as_dict()
        self.assertEqual(found["state"], "missing_app")
        self.assertIn("reaper.fm", appdecl.sentence(found))

    def test_a_moved_availability_reader_degrades_to_unknown(self):
        if not HOST_LIBRARY:
            self.skipTest("hermes_platform is not importable here")
        from hermes_platform.resolver import availability as reader

        with mock.patch.object(reader, "availability", side_effect=ImportError("moved")):
            found = appdecl.state()
        self.assertEqual(found["state"], "unknown")
        self.assertIn("detail", found)


class SentenceTests(unittest.TestCase):
    def test_available_names_the_version_and_the_path(self):
        said = appdecl.sentence({"state": "available", "version": "7.77",
                                 "path": r"C:\Program Files\REAPER (x64)\reaper.exe"})
        self.assertIn("7.77", said)
        self.assertIn("reaper.exe", said)

    def test_missing_app_sends_a_person_to_the_installer(self):
        said = appdecl.sentence({"state": "missing_app"})
        self.assertIn("not installed", said)
        self.assertIn("reaper.fm", said)

    def test_version_too_old_names_both_versions(self):
        said = appdecl.sentence({"state": "version_too_old", "version": "6.83"})
        self.assertIn("6.83", said)
        self.assertIn(appdecl.MIN_VERSION, said)

    def test_unsupported_os_names_this_platform_instead_of_a_traceback(self):
        said = appdecl.sentence({"state": "unsupported_os"})
        self.assertIn(sys.platform, said)
        self.assertIn("Windows only", said)

    def test_unreadable_version_still_produces_a_sentence(self):
        said = appdecl.sentence({"state": "version_too_old", "version": None})
        self.assertIn("unreadable", said)


class LaunchUsesTheDeclarationTests(unittest.TestCase):
    def test_the_declared_path_is_used_before_our_own_candidate_list(self):
        found = Path(sys.executable)
        with mock.patch.dict("os.environ", {"REAPER_EXE": ""}), \
             mock.patch.object(appdecl, "located", return_value=str(found)), \
             mock.patch.object(launch, "WINDOWS_CANDIDATES", []):
            self.assertEqual(launch.find_reaper(), found)

    def test_no_copy_anywhere_reports_the_declarations_sentence(self):
        with mock.patch.dict("os.environ", {"REAPER_EXE": ""}), \
             mock.patch.object(appdecl, "located", return_value=None), \
             mock.patch.object(appdecl, "unavailable_reason",
                               return_value="REAPER is not installed on this machine"), \
             mock.patch.object(launch, "WINDOWS_CANDIDATES", []), \
             mock.patch.object(launch.shutil, "which", return_value=None):
            with self.assertRaises(launch.NoReaperInstall) as caught:
                launch.find_reaper()
        self.assertIn("not installed", str(caught.exception))

    def test_a_portable_copy_on_path_still_wins_over_a_refusal(self):
        with mock.patch.dict("os.environ", {"REAPER_EXE": ""}), \
             mock.patch.object(appdecl, "located", return_value=None), \
             mock.patch.object(launch, "WINDOWS_CANDIDATES", []), \
             mock.patch.object(launch.shutil, "which", side_effect=[None, sys.executable]):
            self.assertEqual(launch.find_reaper(), Path(sys.executable))


class ToolsUseTheDeclarationTests(unittest.TestCase):
    def test_the_no_install_reason_is_the_declaration_sentence(self):
        with mock.patch.object(appdecl, "unavailable_reason", return_value="REAPER 6.83 is older"):
            self.assertEqual(tools.no_install_reason(), "REAPER 6.83 is older")

    def test_the_no_install_reason_falls_back_when_the_library_is_absent(self):
        with mock.patch.object(appdecl, "unavailable_reason", return_value=None):
            self.assertIn("REAPER_EXE", tools.no_install_reason())


if __name__ == "__main__":
    unittest.main()
