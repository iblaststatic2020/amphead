"""Install/uninstall of the REAPER bridge, against a fake resource path.

Every test uses a temporary directory as REAPER's resource folder, so the real
%APPDATA%/REAPER is never touched by the suite.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from amphead import install_bridge as ib


class _FakeProfile(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.res = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.startup = ib.startup_script(self.res)
        self.bdir = ib.bridge_dir(self.res)

    def user_startup(self, text: str) -> None:
        self.startup.parent.mkdir(parents=True, exist_ok=True)
        self.startup.write_text(text, encoding="utf-8")

    def blocks(self) -> int:
        return ib._read(self.startup).count(ib.BLOCK_BEGIN)


class TestInstall(_FakeProfile):
    def test_creates_bridge_and_startup(self) -> None:
        result = ib.install(self.res)

        self.assertEqual(result.startup_action, "created")
        target = self.bdir / "bridge.lua"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), ib.source_bridge().read_bytes())
        self.assertEqual(result.bridge_bytes, ib.source_bridge().stat().st_size)
        self.assertIn(ib.BLOCK_BEGIN, self.startup.read_text(encoding="utf-8"))
        self.assertIn("bridge.lua", self.startup.read_text(encoding="utf-8"))

    def test_second_install_changes_nothing(self) -> None:
        ib.install(self.res)
        before = self.startup.read_text(encoding="utf-8")

        result = ib.install(self.res)

        self.assertEqual(result.startup_action, "unchanged")
        self.assertEqual(self.startup.read_text(encoding="utf-8"), before)
        self.assertEqual(self.blocks(), 1)

    def test_appends_to_a_user_script_and_backs_it_up(self) -> None:
        original = "-- my own startup\nreaper.ShowConsoleMsg('hello')\n"
        self.user_startup(original)

        result = ib.install(self.res)

        self.assertEqual(result.startup_action, "appended")
        self.assertIsNotNone(result.backup)
        self.assertEqual(result.backup.read_text(encoding="utf-8"), original)
        text = self.startup.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original))
        self.assertEqual(self.blocks(), 1)

    def test_user_edits_outside_the_block_survive_reinstall(self) -> None:
        self.user_startup("-- mine\n")
        ib.install(self.res)
        with self.startup.open("a", encoding="utf-8") as fh:
            fh.write("-- added later\n")

        result = ib.install(self.res)

        text = self.startup.read_text(encoding="utf-8")
        self.assertIn("-- mine", text)
        self.assertIn("-- added later", text)
        self.assertEqual(self.blocks(), 1)
        self.assertEqual(result.startup_action, "unchanged")

    def test_a_damaged_block_is_replaced_in_place(self) -> None:
        ib.install(self.res)
        damaged = self.startup.read_text(encoding="utf-8").replace(
            "local _amphead_ok", "local broken_ok"
        )
        self.startup.write_text(damaged, encoding="utf-8")

        result = ib.install(self.res)

        text = self.startup.read_text(encoding="utf-8")
        self.assertEqual(result.startup_action, "updated")
        self.assertIn("local _amphead_ok", text)
        self.assertNotIn("local broken_ok", text)
        self.assertEqual(self.blocks(), 1)

    def test_a_second_hand_written_block_is_collapsed(self) -> None:
        block = ib.block_text()
        self.user_startup("-- mine\n" + block + "\n" + block)

        ib.install(self.res)

        self.assertEqual(self.blocks(), 1)
        self.assertIn("-- mine", self.startup.read_text(encoding="utf-8"))

    def test_missing_resource_path_names_the_env_var(self) -> None:
        missing = self.res / "nope"
        with self.assertRaises(ib.InstallError) as caught:
            ib.install(missing)
        self.assertIn("REAPER_RESOURCE_PATH", str(caught.exception))

    def test_client_root_mismatch_is_a_warning_not_a_failure(self) -> None:
        with mock.patch.dict(os.environ, {"HERMES_REAPER_BRIDGE": str(self.res / "elsewhere")}):
            result = ib.install(self.res)
        self.assertTrue(any("HERMES_REAPER_BRIDGE" in w for w in result.warnings))

    def test_an_unmanaged_autoload_is_flagged(self) -> None:
        """Two loaders is a real double load: REAPER would start two bridges that fight
        over the one request slot. That case still has to be called out."""
        ib.install(self.res)                        # writes the managed block
        self.startup.write_text(
            self.startup.read_text(encoding="utf-8")
            + "\nlocal p = reaper.GetResourcePath()\n"
            "dofile(p .. '/Scripts/hermes_bridge/bridge.lua')\n",
            encoding="utf-8",
        )

        result = ib.install(self.res)

        self.assertTrue(any("twice" in w for w in result.warnings))

    def test_a_clean_install_has_no_warnings(self) -> None:
        self.assertEqual(ib.install(self.res).warnings, [])

    def test_a_startup_script_that_already_loads_the_bridge_is_adopted(self) -> None:
        """The profile this was built for already had a hand-written autoload. Adding our
        block on top would load the bridge twice, and two live bridges fight over the one
        request slot, so the installer must leave the file alone and say it did."""
        mine = (
            "-- Hermes bridge autoload\n"
            "local sep = package.config:sub(1, 1)\n"
            "local path = reaper.GetResourcePath() .. sep .. \"Scripts\" .. sep .. "
            "\"hermes_bridge\" .. sep .. \"bridge.lua\"\n"
            "pcall(dofile, path)\n"
        )
        self.user_startup(mine)

        result = ib.install(self.res)

        self.assertEqual(result.startup_action, "adopted")
        self.assertEqual(result.warnings, [], "adopting is the quiet path, not a warning")
        self.assertEqual(self.startup.read_text(encoding="utf-8"), mine)
        self.assertIsNone(result.backup)
        self.assertTrue((ib.bridge_dir(self.res) / "bridge.lua").exists())

    def test_an_adopted_install_still_uninstalls_cleanly(self) -> None:
        mine = "pcall(dofile, reaper.GetResourcePath() .. \"/Scripts/hermes_bridge/bridge.lua\")\n"
        self.user_startup(mine)
        ib.install(self.res)

        result = ib.uninstall(self.res)

        self.assertFalse(ib.bridge_dir(self.res).exists())
        self.assertEqual(self.startup.read_text(encoding="utf-8"), mine, "the user's line is theirs")

    def test_the_block_only_defines_locals(self) -> None:
        """The hand-installed version defined globals (path, ok, err) that could collide
        with a user's own startup script. Every name we introduce is local now."""
        block = ib.block_text()
        body = [line for line in block.splitlines() if line and not line.lstrip().startswith("--")]
        assigned = [line for line in body if "=" in line and not line.lstrip().startswith("local")]
        self.assertEqual(assigned, [], f"the block assigns globals: {assigned}")
        self.assertIn("pcall(dofile", block)


class TestUninstall(_FakeProfile):
    def test_after_a_clean_install_the_startup_file_goes_away(self) -> None:
        ib.install(self.res)

        result = ib.uninstall(self.res)

        self.assertEqual(result.startup_action, "file deleted")
        self.assertFalse(self.startup.exists())
        self.assertFalse(self.bdir.exists())

    def test_a_user_script_is_restored_from_the_backup(self) -> None:
        original = "-- my own startup\nreaper.ShowConsoleMsg('hello')\n"
        self.user_startup(original)
        ib.install(self.res)

        result = ib.uninstall(self.res)

        self.assertEqual(result.startup_action, "backup restored")
        self.assertEqual(self.startup.read_text(encoding="utf-8"), original)
        self.assertFalse(self.bdir.exists())

    def test_user_content_added_after_install_keeps_its_file(self) -> None:
        ib.install(self.res)
        with self.startup.open("a", encoding="utf-8") as fh:
            fh.write("-- added later\n")

        result = ib.uninstall(self.res)

        self.assertEqual(result.startup_action, "block removed")
        text = self.startup.read_text(encoding="utf-8")
        self.assertIn("-- added later", text)
        self.assertNotIn(ib.BLOCK_BEGIN, text)

    def test_foreign_files_in_the_bridge_folder_are_kept(self) -> None:
        ib.install(self.res)
        stray = self.bdir / "notes.txt"
        stray.write_text("mine", encoding="utf-8")

        result = ib.uninstall(self.res)

        self.assertTrue(stray.exists())
        self.assertIn(stray, result.kept)
        self.assertNotIn(self.bdir, result.removed)

    def test_response_files_are_removed_but_foreign_ones_kept(self) -> None:
        ib.install(self.res)
        out = self.bdir / "out"
        out.mkdir(exist_ok=True)
        (out / "resp_abc.json").write_text("{}", encoding="utf-8")
        keep = out / "keep.txt"
        keep.write_text("mine", encoding="utf-8")

        result = ib.uninstall(self.res)

        self.assertFalse((out / "resp_abc.json").exists())
        self.assertTrue(keep.exists())
        self.assertIn(keep, result.kept)

    def test_uninstall_on_a_clean_profile_is_quiet(self) -> None:
        result = ib.uninstall(self.res)
        self.assertEqual(result.startup_action, "no block")
        self.assertEqual(result.removed, [])

    def test_a_locked_file_is_reported_and_the_rest_still_goes(self) -> None:
        """Windows pins a file for as long as any handle is open on it. An uninstall that
        dies halfway is worse than one that names the file it could not remove."""
        ib.install(self.res)
        locked = self.bdir / "request.lock"
        locked.write_text("", encoding="utf-8")
        real_unlink = Path.unlink

        def fake_unlink(self_path: Path, missing_ok: bool = False):  # noqa: ANN001
            if self_path.name == "request.lock":
                raise PermissionError(32, "The process cannot access the file", str(self_path))
            return real_unlink(self_path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", fake_unlink):
            result = ib.uninstall(self.res)

        self.assertTrue(any("request.lock" in w for w in result.warnings))
        self.assertIn(locked, result.kept)
        self.assertFalse((self.bdir / "bridge.lua").exists())


class TestStatusAndCli(_FakeProfile):
    def test_status_proves_the_installed_copy_matches_the_repo(self) -> None:
        ib.install(self.res)
        info = ib.status(self.res)
        self.assertTrue(info["bridge_installed"])
        self.assertTrue(info["bridge_matches_repo"])
        self.assertTrue(info["startup_block"])

    def test_status_sees_a_stale_copy(self) -> None:
        ib.install(self.res)
        (self.bdir / "bridge.lua").write_text("-- old build", encoding="utf-8")
        info = ib.status(self.res)
        self.assertTrue(info["bridge_installed"])
        self.assertFalse(info["bridge_matches_repo"])

    def test_cli_round_trip(self) -> None:
        self.assertEqual(ib.main(["install", "--resource", str(self.res)]), 0)
        self.assertTrue((self.bdir / "bridge.lua").exists())
        self.assertEqual(ib.main(["status", "--resource", str(self.res)]), 0)
        self.assertEqual(ib.main(["--uninstall", "--resource", str(self.res)]), 0)
        self.assertFalse((self.bdir / "bridge.lua").exists())

    def test_cli_reports_a_missing_resource_without_a_traceback(self) -> None:
        import contextlib
        import io

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = ib.main(["install", "--resource", str(self.res / "nope")])
        self.assertEqual(code, 1)
        self.assertIn("REAPER_RESOURCE_PATH", err.getvalue())


if __name__ == "__main__":
    unittest.main()
