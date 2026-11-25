# Sloppy bubble testing plugin

A standalone bubble workbench for Sloppy users and plugin authors. It uses only
the public version-1 plugin manifest and `sloppy-sdk` WebSocket sessions.
The package can be copied to another directory or repository: it imports no
Sloppy implementation, native views, fixtures, build outputs, or internal tests.
Its examples are maintained in `catalog.py`.

No displayed command executes, no diff is applied, and no resource access is
granted. This plugin is the originating test application, so it can accept an
answer from Sloppy and publish authoritative completion itself.

## Install and run

Requirements: Python 3.11+, Make, and an installed `sloppy` CLI that supports
`sloppy plugins path`. On Windows, use a POSIX Make environment such as MSYS2
and set `PYTHON=python`. The TUI uses Textual on every platform.
The JSON editor uses the syntax parser pinned in `requirements.txt`. Tests cover
native parser reference counts and repeated large-document reloads on Python 3.14.
When updating from an older release, recreate or repair the installed environment
so it uses the current requirements.

This folder can be cloned or copied on its own. From this folder:

```sh
make deps
make check
make install
make enable
make run
```

Standalone installs use public `sloppy-sdk==0.1.0` from PyPI. That SDK must be
published before the default dependency setup can succeed. `make deps` creates
this checkout's `.venv`; `make install` creates a separate environment inside the
installed copy. Neither environment is copied into the distributable package.
Use `make installed-deps` to repair an already installed plugin. Set
`SLOPPY=/path/to/sloppy` to choose a CLI and `PYPI_INDEX` to choose a package index.

Start Sloppy and check **Enable Sloppy Service Server** in Settings before sending
bubbles. A missing `connection.json` means local service discovery is unavailable;
local adapter enablement does not start the server. `make run ARGS=--list` checks
the installed dependencies without a service or terminal. `make dev` runs the
checkout directly, and `make test TEST_ARGS='-k pattern'` selects individual tests.

If `make enable` or `make disable` reports an unconfirmed service update, the local
adapter setting was saved but the service's setting could not be confirmed. Start the
service or fix its connection, then rerun the same command. Permission and invalid
connection-file errors require fixing the file or its configured path rather
than simply turning the server on.

To test the public Python SDK from source, clone its repository separately and
select that checkout explicitly:

```sh
make deps SDK_SOURCE=/absolute/path/to/sdk/python
make install SDK_SOURCE=/absolute/path/to/sdk/python
```

`SDK_SOURCE` changes dependency setup only; it is not written into the package
manifest. The installed SDK is a normal copy, so the plugin continues to work
when the source checkout moves. `make package` stages regular distributable files
under `build/package`, excluding `.venv`, caches, and other development files.
The Unix launcher's executable mode is preserved; Windows uses `testing.cmd`.

`sloppy` copies the package at installation; subsequent source edits do not change
that copy. Installation starts disabled, and `make enable` explicitly trusts the
adapter's executable code. Installed versions are immutable: install a new version side by side, or stop and
remove an exact version with `sloppy plugins uninstall testing@VERSION` before
reinstalling it. Use `make dev` while iterating on this example.

## Menu actions

The manifest lists all four actions, categories, and input forms before installation.
Inspect it without executing code with `sloppy plugins inspect build/package`.
Enable **Plugin actions** in Sloppy Settings → Service, then start the workbench
with `make run`, or use `make run ARGS=--actions` for a headless action provider.
In Actions settings, choose **Add… → Bubble Testing → version/instance**:

- **Immediate:** Show message and Increment counter run when selected.
- **With input:** Greet and Add numbers show a form. They run after Send;
  dismissing the form cancels the action.

Actions can be placed in the main menu, dots, or assigned keyboard shortcuts.
Closing the provider leaves saved placements visible but unavailable; clicks are
never queued for execution after reconnect.

`make enable` registers this exact manifest and version with the selected Sloppy
instance. For source development, first register the checkout with
`sloppy plugins register .`, then enable `testing@VERSION` in Service settings.
An unregistered version is rejected with a version mismatch message and stops
reconnecting. A connecting descriptor cannot install or replace a manifest.

Multiple versions can run together using `sloppy testing@VERSION`. Each has its
own credentials, workbench history, provider slot, and saved action bindings.
`sloppy plugins use testing@VERSION` explicitly chooses what `sloppy testing`
launches; installing a newer version does not change that selection. Removing a
local executable retains host manifest metadata and saved action placements;
disable its host version in Service settings if it should no longer connect.

Integration checks install the package and launch its copied executables from
an unrelated working directory. They send/resolve every preset, drive the
installed Textual TUI through a real terminal, and exercise native-presenter
answers and both-side dismissal. CI also checks the TypeScript SDK and Copilot
host using the real service, including extension replacement and session ownership.

To pair with a specific service, use the host's discovery override:

```sh
sloppy --connection-file /path/to/connection.json testing
```

The TUI works on macOS, Linux, and Windows. Sloppy's graphical frontend is
currently macOS-only; on other platforms the same plugin can exercise the
portable SloppyService/CLI and another API client. The plugin does not add a
native frontend.

## Workbench

Use a terminal of at least **90 columns by 20 rows**.

The left pane lists 28 examples covering all eight content families, all seven
semantic kinds, the six response types through generic replies/questionnaires,
all four diff operations, determinate/indeterminate progress, and avatar
metadata. These are starting points, not an enumeration of every possible
parameter combination.

Enter an example, send its defaults, or edit its complete notification JSON.
The pending and resolved/history panes remain visible and refresh while composing
and editing. Sending never makes you wait for the previous bubble to complete.
Each pane scrolls independently, and the buttons provide mouse-accessible actions.
History updates preserve the selected record and never replace an editor draft.

| Key                      | Action                                                                |
| ------------------------ | --------------------------------------------------------------------- |
| Tab / Shift-Tab          | Switch between catalog/composer, pending, and history outside editing |
| Ctrl-1 / Ctrl-2 / Ctrl-3 | Focus composer, pending, or history, including during editing         |
| Up / Down                | Navigate lists; scroll a composed notification                        |
| Enter                    | Open an example, custom pending answer, or complete history record    |
| S                        | Send the selected example or the current composed notification        |
| E                        | Edit the current notification JSON                                    |
| I                        | Import a notification JSON file; enter its path as a JSON string      |
| W                        | Export the composition; enter a new file path as a JSON string        |
| D in composer            | Restore that example's original defaults                              |
| R in pending             | Resolve with the sample answer; complete informational bubbles        |
| A / Enter in pending     | Edit a custom answer before resolving                                 |
| D in pending             | Dismiss from the originating testing application                      |
| Escape                   | Return to the catalog or close pending actions                        |
| Q                        | Quit without dismissing pending bubbles                               |

The JSON editor has syntax highlighting, line numbers, a visible caret, scrolling,
mouse selection, multiline input, and undo/redo. Arrows, Home/End, Page Up/Down,
Backspace/Delete, and selection with Shift work normally. Tab indents while editing.
**F7** selects all; **Ctrl-Z / Ctrl-Y** undo/redo. **Ctrl-C / Ctrl-V** copy/paste
using Textual's clipboard; terminal paste also works.
**Ctrl-S** saves; **Ctrl-G** or Escape cancels. Invalid JSON stays in the editor
with a visible error. In a history record, arrows navigate read-only JSON.
Mouse clicks open catalog examples and history records. While editing, actions
that would replace the draft or submit another bubble are disabled; the pending
and history lists still refresh and remain browsable.

Exports refuse to overwrite existing files. Imported/custom payloads are ordinary
data, never code. The workbench supplies its own `origin` regardless of what an
imported payload says, so it cannot impersonate or resolve another plugin's
requests. Backend validation errors are visible, not reported as successful sends.

## Test your plugin's variants and parameters

Export a starting point and edit it with your preferred editor:

```sh
sloppy testing --export command --output command-test.json
sloppy testing --payload command-test.json
```

Change `kind`, title/context, typed body parameters, choices, response constraints,
and avatar state/tint. For example, test long diff text, absent previews, JSON
precision, many form fields, optional answers, numeric limits, different progress
states, or a short bubble followed by a long one. Service validation remains
authoritative; an invalid variant produces a visible error rather than a bubble.

Keep reusable cases in a separate JSON catalog:

```json
[
  {
    "id": "my-long-report",
    "name": "My plugin: long error report",
    "notification": {
      "kind": "error",
      "title": "A simulated failure",
      "message": "Reproduce my plugin's layout.",
      "requires_response": false,
      "bubble": {
        "type": "report",
        "parameters": {
          "sections": [{ "heading": "Evidence", "body": "Example output", "code": true }]
        }
      }
    }
  }
]
```

```sh
sloppy testing --catalog my-cases.json
sloppy testing --catalog my-cases.json --send my-long-report
```

Cases append to the built-in catalog and must have unique, nonempty IDs/names.
The catalog can live anywhere; no source checkout is required by the installed
adapter.

Preview your plugin's declarative layout manifest:

```sh
sloppy testing --layout-manifest /path/to/my-plugin/plugin.json --catalog my-cases.json
```

Only `plugin.layouts` is imported, under the **testing** identity. This does not
install, enable, execute, or modify the other plugin. Test per-kind widths,
templates, accents, placeholders, and details settings using the normal layout
contract. Imported settings apply to subsequently sent testing bubbles; the next
launch without this option restores testing's own layouts. Sloppy's user-resized
width can override a plugin's initial width.

## Both sides of the lifecycle

A Sloppy answer is a validated **proposal**, not completion. The workbench refreshes
its session's submitted records, accepts their first proposals, and sends
authoritative completion. They then leave pending and enter history with their
response and source intact. TUI answers go through the same validated session operation
before completion; a competing answer cannot overwrite a submitted proposal.

Use the pending pane's **Resolve**, **Answer...**, and **Dismiss** buttons to send
the sample response, edit an answer object, or dismiss the bubble:

```json
{ "selected_choice_ids": ["reject"], "value": null }
```

Questionnaire answers use `value` as an object keyed by question ID. The action
editor prepopulates a correctly typed example. Values and selected choice IDs are
validated by the service before they are accepted.

Dismissal from either side cancels the bubble without an answer. Dismissed entries appear in history
as **dismissed**, not falsely as answered. The API does not resolve informational
bubbles with an answer; completing them uses cancellation and retains its
source/reason. Sloppy's single **×** dismisses without reacting, including while
a response is submitting or the connection is unhealthy.

Only this history's owned session records are refreshed or completed. Other plugins'
bubbles and testing instances remain untouched. Multiple TUI processes must use
different `--state-dir` directories, enforced with a cross-platform file lock.

History persists across testing-plugin restarts, including still-pending requests
when the same service instance remains alive. Defaults:

- Unix: `$XDG_STATE_HOME/sloppy-testing`, otherwise `~/.local/state/sloppy-testing`.
- Windows: `%LOCALAPPDATA%\SloppyTesting`.
- Override: `--state-dir PATH` or `SLOPPY_TESTING_STATE_DIR`.

History includes notification text, answers, and private session resume credentials.
Treat it as private; use fake data when demonstrating the workbench. Its file is
written atomically with owner-only permissions on Unix. Never share the history
file: its resume capability grants access to this testing session. The service's
bearer token is never saved in history or printed.

The SDK handles discovery, heartbeats, reconnect, and idempotent operation retries.
The TUI stays usable while disconnected. SloppyService state is not
durable: after a service restart, lost active entries become **unavailable** in
history, not falsely resolved. Old requests and answers are never replayed;
newly sent bubbles use the recovered instance. Disabled mirroring is reported
and is never silently enabled.

## Automation and tests

The same package offers headless commands:

```sh
sloppy testing --list
sloppy testing --send typed-input
sloppy testing --sync
sloppy testing --resolve BUBBLE_UUID
sloppy testing --resolve BUBBLE_UUID --answer '{"value":{"enabled":true,"retries":3,"config":{}}}'
sloppy testing --dismiss BUBBLE_UUID
```

`--send`, `--payload`, `--sync`, `--resolve`, and `--dismiss` print JSON.
`--sync` performs one refresh/completion pass. Without a running TUI or repeated
`--sync`, a GUI proposal remains submitted until the originating tester returns.

Unit tests do not require Sloppy, an app, or a terminal:

```sh
make test
```

If using a private `.venv`, run these commands with that environment's Python
instead of `python3`; importing the test module does not switch interpreters.

For integration tests, supply existing portable service and CLI executables;
the tests do not import their source or their internal tests:

```sh
SLOPPY_TESTING_SERVICE=/absolute/path/to/SloppyService \
SLOPPY_TESTING_CLI=/absolute/path/to/sloppy \
PYTHONDONTWRITEBYTECODE=1 \
make test
```

On Windows use `python` and set those environment variables to the `.exe`
paths in PowerShell. Integration tests create an isolated service, catalog and
history; they do not change your installed plugins, active app or preferences.
Without binaries, these tests are explicitly skipped, not reported as executed.

Coverage includes every example, custom parameters/layouts, real Textual widgets
exercised with its headless Pilot (keyboard, mouse, cursor, selection, undo, live
updates, and resizing), editor/navigation,
origin-side response/dismissal, first-proposal arbitration, validation failures,
instance isolation, durable history, no replay after restart, executable install
and explicit trust, Unicode, and terminal bounds. The same suite can run
against externally supplied portable binaries on macOS, Linux and Windows; it does not claim
to test a Windows/Linux native Sloppy window. Actual GUI-side proposals, dismissal,
connection errors, and recovery are covered by Sloppy's native regression suites;
the standalone tests do not impersonate that GUI through another session.

Author: Marat Abdullin <ma@xrlt.net>.

## Install through Sloppy Settings

In Settings → Plugins, browse a catalogue or choose **Load Unpacked…**
and select this repository. Sloppy copies the plugin to its Application Support
folder, checks the declared runtimes, and prepares private dependencies with
progress and cancellation. Changes to this checkout do not alter installed code.
The development manifest pins its SDK to an exact Git commit; Git access uses your
normal credentials. Python/Node.js and Git must already be installed.

Use the version picker to install an older release or replace an installed one.
Sloppy previews saved Actions that will migrate or be removed; choosing an already
installed version merges the two installations. Catalogue maintainers can use the
SDK's `sloppy-catalogue add-plugin` and `add-version` commands after committing the
plugin manifest and source. The catalogue embeds the full manifest and commit.

## CI and releases

GitHub Actions runs `make deps check pack` on Linux, macOS, and Windows for
pushes to `main`, pull requests, and manual dispatch. Python 3.11–3.14 are covered.
The checks are standalone; Sloppy's service and native agent binaries are optional
integration-test inputs. Release tooling requires Python 3.11+ and an authenticated
GitHub CLI in addition to the build requirements above.

A published stable GitHub Release triggers `.github/workflows/release.yml`.
Its `vX.Y.Z` tag must match `plugin.json` and `pyproject.toml`. The build job
checks, tests, and saves the artifacts. Separate jobs publish those exact artifacts
and attach the installable ZIP to the GitHub Release. Prereleases do not publish.

Create a GitHub environment named `pypi` in `sloppy-plugins/testing`. Configure
a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/) for project
`sloppy-testing`, owner `sloppy-plugins`, repository `testing`, workflow filename
`release.yml`, and environment `pypi`. Use a pending publisher for the first release.
Publication uses OIDC and attestations; no PyPI API token secret is needed.

`make pack` creates a wheel and source distribution in `dist/`, plus
`build/sloppy-testing-VERSION.zip`. The PyPI distribution uses the `sloppy_testing`
Python namespace and includes the plugin manifest. The user command remains
`sloppy testing`; it adds no separate console command. Extract the ZIP into a
folder, then run `sloppy plugins install /path/to/extracted-folder` and explicitly
enable the installed plugin.

For subsequent releases, choose one version-bump command:

```sh
make version-bump                 # Increase the patch version
make version-bump BUMP=minor      # Or a minor version
make version-bump BUMP=major      # Or a major version
make version-bump VERSION=1.2.3   # Or an explicit greater stable version
# Review, commit, and push to github.com/sloppy-plugins/testing.
make github-release-dry-run      # Check, package, and preview locally
make github-release              # Create the stable GitHub Release
```

The bump keeps the plugin manifest and package metadata aligned without creating
a commit or tag. The first release can use the current version without a bump.
`github-release` requires a clean checkout, verifies the exact local commit is
available in the canonical GitHub repository, and rejects an existing tag that
points elsewhere. No registry upload happens during dependency setup, checks,
packaging, version bumps, or the release preview. If publication fails, rerun only
the failed job. Published package versions are immutable; different contents
require a version bump.
