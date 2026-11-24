"""Build and provision this example without a parent Sloppy repository."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parent
PACKAGE_FILES = (
    "plugin.json",
    "testing.py",
    "testing.cmd",
    "catalog.py",
    "actions.py",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "Makefile",
    "manage.py",
    "test_testing.py",
    "test_actions.py",
    "test_manage.py",
    "AUTHORS",
    "requirements-dev.txt",
    "pyproject.toml",
)


def package():
    destination = ROOT / "build/package"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        for name in PACKAGE_FILES:
            source = ROOT / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"Plugin package input must be a regular file: {source}")
            target = Path(staging) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staging, destination)
    print(f"Packaged testing plugin: {destination}")


def dependencies(directory, development=False):
    environment = directory / ".venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    interpreter = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    source = os.environ.get("SDK_SOURCE")
    local = [str(Path(source).resolve())] if source else []
    requirements = directory / ("requirements-dev.txt" if development else "requirements.txt")
    subprocess.run(
        [str(interpreter), "-m", "pip", "install", *local, "-r", str(requirements)], check=True
    )
    subprocess.run([str(interpreter), str(directory / "testing.py"), "--list"], check=True)
    print(f"Testing plugin dependencies ready: {environment}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("package")
    commands.add_parser("deps")
    commands.add_parser("clean")
    setup = commands.add_parser("installed-deps")
    setup.add_argument("--cli", required=True)
    args = parser.parse_args()
    if args.command == "package":
        package()
    elif args.command == "deps":
        dependencies(ROOT, development=True)
    elif args.command == "clean":
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        shutil.rmtree(ROOT / "__pycache__", ignore_errors=True)
    else:
        try:
            installed = subprocess.check_output(
                [
                    args.cli,
                    "plugins",
                    "path",
                    "testing@"
                    + json.loads((ROOT / "plugin.json").read_text())["plugin"]["version"],
                ],
                text=True,
            ).strip()
        except FileNotFoundError as error:
            raise ValueError(
                f"The Sloppy CLI {args.cli!r} was not found. Install or build the CLI, or set SLOPPY=/path/to/sloppy before running make installed-deps."
            ) from error
        except subprocess.CalledProcessError as error:
            raise ValueError(
                "Could not find the installed testing plugin. Review the CLI message above; if it is not installed, run make install first."
            ) from error
        directory = Path(installed)
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        if manifest["plugin"]["id"] != "testing":
            raise ValueError("Installed package is not the testing plugin")
        dependencies(directory)
        print("Start Sloppy, enable Sloppy Service Server in Settings, then run make run.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Testing plugin setup failed: {error}", file=sys.stderr)
        sys.exit(1)
