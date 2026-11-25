import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.write(
            "plugin.json",
            {"plugin": {"id": "testing", "version": "1.2.3"}, "actions": {"preserved": True}},
        )
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "sloppy-testing"\nversion = "1.2.3"\n\n[tool.example]\nversion = "99.0.0"\n',
            encoding="utf-8",
        )
        self.head = "a" * 40
        self.calls = []

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data), encoding="utf-8")

    def fake_run(self, command, _root, _context):
        self.calls.append(command)
        if command[0] == "git":
            return self.head if command[1] == "rev-parse" else ""
        if command[1] == "auth":
            return ""
        if command[1] == "api":
            if "/commits/" in command[-1]:
                return json.dumps({"sha": self.head})
            if "/git/tags/" in command[-1]:
                return json.dumps({"object": {"type": "commit", "sha": self.head}})
            return json.dumps(self.references)
        return "https://github.com/sloppy-plugins/testing/releases/tag/v1.2.3"

    def test_tag_and_version_mismatches_are_rejected(self):
        self.assertEqual(release.release_version(self.root, "v1.2.3"), "1.2.3")
        for tag in ("1.2.3", "v1.2.4", "v1.2.3-rc.1", "v01.2.3"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.release_version(self.root, tag)
        self.write("plugin.json", {"plugin": {"id": "testing", "version": "1.2.4"}})
        with self.assertRaisesRegex(ValueError, "Version mismatch"):
            release.bump_version(self.root)

    def test_patch_minor_major_and_explicit_versions(self):
        for arguments, expected in [
            ({}, "1.2.4"),
            ({"part": "minor"}, "1.3.0"),
            ({"part": "major"}, "2.0.0"),
            ({"version": "3.4.5"}, "3.4.5"),
        ]:
            with self.subTest(arguments=arguments):
                self.setUp()
                self.assertEqual(release.bump_version(self.root, **arguments), expected)
                self.assertEqual(release.release_version(self.root), expected)
                self.assertTrue(
                    json.loads((self.root / "plugin.json").read_text())["actions"]["preserved"]
                )
                self.assertIn('version = "99.0.0"', (self.root / "pyproject.toml").read_text())

    def test_invalid_or_non_increasing_versions_leave_files_unchanged(self):
        original = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        for version in ("v1.2.4", "1.2.4-rc.1", "01.2.4", "1.2.3", "1.0.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.bump_version(self.root, version=version)
            self.assertEqual(original, {p: p.read_bytes() for p in original})

    def test_make_target_passes_bump_and_explicit_version(self):
        shutil.copyfile(release.ROOT / "Makefile", self.root / "Makefile")
        shutil.copyfile(release.__file__, self.root / "release.py")
        for options, expected in [
            ([], "1.2.4"),
            (["BUMP=minor"], "1.3.0"),
            (["VERSION=2.3.4"], "2.3.4"),
        ]:
            result = subprocess.run(
                ["make", "version-bump", f"PYTHON={sys.executable}", *options],
                cwd=self.root,
                check=False,
                env=dict(os.environ, BUMP="patch", VERSION=""),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(release.release_version(self.root), expected)

    def test_preview_never_contacts_github(self):
        with (
            patch.object(release, "run", side_effect=self.fake_run),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            release.github_release(self.root, dry_run=True)
        self.assertIn("--repo github.com/sloppy-plugins/testing", output.getvalue())
        self.assertIn(f"--target {self.head}", output.getvalue())
        self.assertTrue(all(call[0] == "git" for call in self.calls))

    def test_dirty_checkout_never_creates_a_release(self):
        with (
            patch.object(release, "run", return_value=" M README.md") as run,
            self.assertRaisesRegex(ValueError, "Commit the plugin changes"),
        ):
            release.github_release(self.root)
        self.assertEqual(run.call_count, 1)

    def test_release_uses_canonical_github_and_exact_local_commit(self):
        self.references = []
        with (
            patch.object(release, "run", side_effect=self.fake_run),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            release.github_release(self.root)
        command = self.calls[-1]
        self.assertEqual(command[:4], ["gh", "release", "create", "v1.2.3"])
        self.assertEqual(command[command.index("--target") + 1], self.head)
        self.assertEqual(command[command.index("--repo") + 1], release.REPOSITORY)
        self.assertTrue(any(f"{release.API}/commits/{self.head}" in call for call in self.calls))

    def test_existing_tag_for_a_different_commit_is_not_released(self):
        self.references = [
            {"ref": "refs/tags/v1.2.3", "object": {"type": "commit", "sha": "b" * 40}}
        ]
        with (
            patch.object(release, "run", side_effect=self.fake_run),
            self.assertRaisesRegex(ValueError, "different commit"),
        ):
            release.github_release(self.root)
        self.assertFalse(any(call[1:3] == ["release", "create"] for call in self.calls))

    def test_annotated_tag_is_resolved_to_its_commit(self):
        annotation = "b" * 40
        self.references = [
            {"ref": "refs/tags/v1.2.3", "object": {"type": "tag", "sha": annotation}}
        ]
        with (
            patch.object(release, "run", side_effect=self.fake_run),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            release.github_release(self.root)
        self.assertTrue(any(f"{release.API}/git/tags/{annotation}" in call for call in self.calls))
        self.assertEqual(self.calls[-1][1:3], ["release", "create"])

    def test_unpublished_commit_never_creates_a_release(self):
        def run(command, root, context):
            if command[1] == "api":
                raise RuntimeError("Commit is not on GitHub")
            return self.fake_run(command, root, context)

        with (
            patch.object(release, "run", side_effect=run),
            self.assertRaisesRegex(RuntimeError, "not on GitHub"),
        ):
            release.github_release(self.root)
        self.assertFalse(any(call[1:3] == ["release", "create"] for call in self.calls))
