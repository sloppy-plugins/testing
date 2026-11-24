"""Check distribution from an isolated copy of this example."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import manage


class PackageTests(unittest.TestCase):
    def test_missing_cli_has_a_setup_action_instead_of_a_raw_os_error(self):
        result = subprocess.run(
            [
                sys.executable,
                str(manage.ROOT / "manage.py"),
                "installed-deps",
                "--cli",
                "/missing/sloppy-test-cli",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Sloppy CLI", result.stderr)
        self.assertIn("SLOPPY=/path/to/sloppy", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_standalone_package_excludes_environments_and_preserves_launchers(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "testing"
            shutil.copytree(
                manage.ROOT,
                checkout,
                ignore=shutil.ignore_patterns(
                    ".venv",
                    "build",
                    "__pycache__",
                ),
            )
            (checkout / ".venv/bin").mkdir(parents=True)
            if os.name != "nt":
                (checkout / ".venv/bin/python").symlink_to(sys.executable)
            (checkout / "__pycache__").mkdir()
            (checkout / "__pycache__/testing.pyc").write_bytes(b"development cache")
            destination = checkout / "build/package"
            destination.mkdir(parents=True)
            (destination / "stale.py").touch()
            subprocess.run(
                ["make", "package", f"PYTHON={sys.executable}"], cwd=checkout, check=True
            )
            self.assertEqual(
                {file.name for file in destination.iterdir()}, set(manage.PACKAGE_FILES)
            )
            self.assertEqual(
                (checkout / "testing.py").read_bytes(), (destination / "testing.py").read_bytes()
            )
            if os.name != "nt":
                self.assertEqual((destination / "testing.py").stat().st_mode & 0o111, 0o111)
            self.assertTrue((checkout / "__pycache__/testing.pyc").exists())


if __name__ == "__main__":
    unittest.main()
