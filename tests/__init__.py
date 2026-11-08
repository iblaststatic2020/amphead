"""Tests run against a scratch settings file.

AmpHead reads a settings file that belongs to the person running it. Without this, a
developer with a capture folder configured would see different menus than CI, and a test
that wrote settings would overwrite real ones. Each test process gets its own empty file.
"""

import os
import tempfile

os.environ.setdefault(
    "AMPHEAD_SETTINGS",
    os.path.join(tempfile.mkdtemp(prefix="amphead-tests-"), "settings.json"),
)
