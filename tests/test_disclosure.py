"""Rule 13 disclosure: the same paragraph in the catalog entry and the README.

The catalog asks a plugin to disclose network calls, reads outside its own data, shell commands,
long-running background processes and stored credentials. A reviewer reads whichever of the two
they open first, so the two copies drift the moment one is edited alone. This test is the pin.

The catalog description is a YAML folded scalar and the project has no YAML dependency on the
plugin side, so the reader here folds by hand: newlines become spaces, blank lines become
paragraph breaks. Wrapping is therefore free to differ; the words are not.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "plugin-catalog" / "amphead.yaml"
README = ROOT / "README.md"
HEADING = "## What this touches"
#: The first words of the disclosure, used to split the shared text from its own context.
OPENER = "AmpHead touches the machine in five ways."


def squash(text: str) -> str:
    """One line, one space between words, so wrapping never counts as a difference."""
    return " ".join(text.split())


def fold(lines: list[str]) -> str:
    """YAML's `>-`: line breaks fold to spaces, and a blank line folds to a newline."""
    out, blank = [], False
    for raw in lines:
        line = raw.strip()
        if not line:
            blank = bool(out)
            continue
        out.append(("\n" if blank else " ") + line)
        blank = False
    return "".join(out)


def catalog_description(path: Path | None = None) -> str:
    """The `description:` block of the catalog entry, folded the way YAML would fold it."""
    lines = (path or CATALOG).read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("description:"))
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(" "):
            break
        body.append(line)
    return fold(body)


def readme_section(path: Path | None = None) -> str:
    """Everything under the README's `## What this touches` heading, up to the next heading."""
    text = (path or README).read_text(encoding="utf-8")
    start = text.index(HEADING) + len(HEADING)
    return text[start:text.index("\n## ", start)]


class TheDisclosure(unittest.TestCase):
    def test_catalog_and_readme_carry_the_same_paragraph(self):
        self.assertIn(OPENER, catalog_description(), "the catalog must carry the disclosure")
        self.assertIn(OPENER, readme_section(), "the README must carry the disclosure")
        catalog = squash(catalog_description().split(OPENER, 1)[1])
        readme = squash(readme_section().split(OPENER, 1)[1])
        self.assertEqual(catalog, readme, "one paragraph, two copies, or they will drift")

    def test_shell_commands_are_named_not_implied(self):
        """Undisclosed PowerShell is the kind of thing a reviewer finds and turns into a request."""
        both = f"{catalog_description()} {readme_section()}"
        for fact in ("PowerShell", "-ExecutionPolicy Bypass", "launch it minimized",
                     "count running instances", "stop a REAPER it started"):
            self.assertIn(fact, both)

    def test_the_install_is_disclosed_down_to_the_file_it_edits(self):
        both = f"{catalog_description()} {readme_section()}"
        for fact in ("Lua bridge", "REAPER resource path", "__startup.lua", "removes both on uninstall"):
            self.assertIn(fact, both)

    def test_the_long_lived_channel_is_disclosed_with_its_rate(self):
        both = f"{catalog_description()} {readme_section()}"
        self.assertIn("long-lived background request channel", both)
        self.assertIn("150 ms", both, "the poll rate is the part that makes it a background process")

    def test_reads_outside_its_own_data_are_disclosed(self):
        both = f"{catalog_description()} {readme_section()}"
        for fact in ("track names", "FX chain", "parameters", "master fader", "project"):
            self.assertIn(fact, both)
        self.assertIn("from a session it did not create", both)

    def test_what_it_does_not_do_is_stated_as_plainly(self):
        both = f"{catalog_description()} {readme_section()}"
        for fact in ("never touches audio files", "stores no credentials", "no network calls"):
            self.assertIn(fact, both)


if __name__ == "__main__":
    unittest.main()
