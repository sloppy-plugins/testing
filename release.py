"""Keep plugin and package versions aligned and create releases in the canonical GitHub repo."""

import argparse
import json
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_ID = "testing"
REPOSITORY = "github.com/sloppy-plugins/testing"
API = "repos/sloppy-plugins/testing"
STABLE = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def version_numbers(version):
    if not isinstance(version, str) or not re.fullmatch(STABLE, version):
        raise ValueError(f"Expected a stable X.Y.Z version, received {version!r}")
    return tuple(map(int, version.split(".")))


def release_version(root=ROOT, tag=None):
    plugin = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["plugin"]
    version = plugin["version"]
    version_numbers(version)
    if plugin["id"] != PLUGIN_ID:
        raise ValueError(f"Expected the {PLUGIN_ID} plugin")
    if (root / "package.json").exists():
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
        if package.get("name") != f"@sloppy-plugins/{PLUGIN_ID}":
            raise ValueError("Unexpected npm package name")
        versions = (package["version"], lock["version"], lock["packages"][""]["version"])
    else:
        package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        if package.get("name") != f"sloppy-{PLUGIN_ID}":
            raise ValueError("Unexpected Python package name")
        versions = (package["version"],)
    if any(item != version for item in versions):
        raise ValueError(f"Version mismatch: plugin={version}, packages={versions}")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"Release tag must be v{version}, received {tag!r}")
    return version


def bump_version(root=ROOT, part="patch", version=None):
    current = release_version(root)
    previous = version_numbers(current)
    if version is None:
        numbers = list(previous)
        index = ("major", "minor", "patch").index(part)
        numbers[index] += 1
        numbers[index + 1 :] = [0] * (2 - index)
        version = ".".join(map(str, numbers))
    if version_numbers(version) <= previous:
        raise ValueError(f"New version {version} must be greater than {current}")
    contents = {}
    for name in ("plugin.json", "package.json", "package-lock.json"):
        path = root / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if name == "plugin.json":
            data["plugin"]["version"] = version
        else:
            data["version"] = version
            if name == "package-lock.json":
                data["packages"][""]["version"] = version
        contents[path] = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path = root / "pyproject.toml"
    if path.exists():
        text, count = re.subn(
            r'(?ms)(^\[project\][ \t]*\n.*?^version[ \t]*=[ \t]*")[^"]+(\")',
            lambda match: match[1] + version + match[2],
            path.read_text(encoding="utf-8"),
            count=1,
        )
        if count != 1:
            raise ValueError('Expected a version = "X.Y.Z" field in [project]')
        contents[path] = text
    for path, text in contents.items():
        path.write_text(text, encoding="utf-8")
    return version


def run(command, root, context):
    try:
        return subprocess.run(
            command, cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
    except FileNotFoundError as error:
        raise RuntimeError(f"Install {command[0]} before releasing") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{context}: {error.stderr.strip() or error.stdout.strip()}") from error


def github_release(root=ROOT, dry_run=False):
    version = release_version(root)
    if run(["git", "status", "--porcelain"], root, "Cannot inspect the plugin checkout"):
        raise ValueError("Commit the plugin changes before creating a GitHub Release")
    head = run(["git", "rev-parse", "HEAD"], root, "Cannot find the plugin commit")
    tag = f"v{version}"
    command = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        REPOSITORY,
        "--target",
        head,
        "--title",
        tag,
        "--generate-notes",
    ]
    if dry_run:
        print(shlex.join(command))
        return

    run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        root,
        "Authenticate using gh auth login --hostname github.com",
    )

    def api(path):
        return json.loads(
            run(
                ["gh", "api", "--hostname", "github.com", f"{API}/{path}"],
                root,
                f"Cannot read GitHub {path}; check authentication and push/mirror the plugin commit to https://{REPOSITORY}",
            )
        )

    api(f"commits/{head}")
    references = api(f"git/matching-refs/tags/{tag}")
    for reference in references:
        if reference["ref"] != f"refs/tags/{tag}":
            continue
        target = reference["object"]
        while target["type"] == "tag":
            target = api(f"git/tags/{target['sha']}")["object"]
        if target["type"] != "commit" or target["sha"] != head:
            raise ValueError(f"GitHub tag {tag} points to a different commit; choose a new version")
    print(run(command, root, "Cannot create the GitHub Release"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-tag")
    check.add_argument("tag")
    bump = commands.add_parser("bump")
    bump.add_argument("--part", choices=("patch", "minor", "major"), default="patch")
    bump.add_argument("--version")
    release = commands.add_parser("github")
    release.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "check-tag":
            print(f"Releasing the plugin at {release_version(tag=args.tag)}")
        elif args.command == "bump":
            print(
                f"Updated plugin and package to {bump_version(part=args.part, version=args.version)}"
            )
        else:
            github_release(dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
