# Checkseal

**Your tests passed. Then the code changed.**

Checkseal tells you whether a passing check still applies to the files in your Git working tree. A tiny CLI for developers and coding agents. Python standard library only. MIT licensed.

![Checkseal demo: a passing check becomes stale after a source edit, then fails when rerun](assets/demo.gif)

```sh
checkseal run test -- npm test
# Make an edit...
checkseal check --require test
# test  STALE  Repository content changed after the check.
```

## Try it in 30 seconds

Requires **Python 3.10+ and Git**. Linux and macOS are the initial target platforms.

```sh
git clone https://github.com/tunakurucu/checkseal.git
cd checkseal
python3 checkseal.py demo
```

The demo creates a temporary repository, passes a test, changes the code, detects stale evidence, and reruns the now-failing test. It does not change your project. No install step or account needed.

To install the `checkseal` command from your clone:

```sh
python3 -m pip install .
```

Or keep it completely unpackaged: use `python3 /path/to/checkseal.py` wherever these examples say `checkseal`. **The package is not yet published to PyPI.** Do not install an unrelated package with the same name.

## The problem

You run tests. A developer or agent makes one more edit. The summary still says “tests passed.” That result may be true about an earlier version of the code.

Checkseal records the command, working directory, exit code, duration, and a SHA-256 fingerprint of local repository files before and after the run. When you check again, it compares the receipt with the current files and names the changed paths.

This is a **freshness check for local results**, not a test runner, sandbox, code-quality score, or signed attestation. It cannot determine whether your tests actually cover the bug.

## Use your existing commands

From the repository you want to check:

```sh
checkseal run test -- npm test
checkseal run lint -- npm run lint
checkseal check --require test --require lint
```

Or Python, Rust, Go, a shell script, or any executable:

```sh
checkseal run test -- python3 -m unittest discover -s tests
checkseal run test -- cargo test
checkseal run test -- go test ./...
```

Arguments after `--` go directly to the executable, without shell interpretation. For a compound shell command, explicitly use `sh -c 'command1 && command2'`. Commands run with your privileges and can modify files or access the network.

### Bind names to real commands

Add `checkseal.json` at your repository root:

```json
{
  "checks": {
    "test": ["npm", "test"],
    "lint": ["npm", "run", "lint"]
  }
}
```

Then:

```sh
checkseal run test
checkseal run lint
checkseal check
```

Configured commands run from the repository root, even if invoked in a subdirectory. `check` requires every configured label by default and verifies both the exact command arguments and the working directory. Running `true` under the label `test` cannot satisfy a configured `npm test` check.

Without configuration, `check` checks all recorded labels. Use `--require` for an explicit subset. An empty receipt store fails the check.

### Reports for review

```sh
checkseal check --format json
checkseal check --format markdown
checkseal check --format html > /tmp/checkseal-report.html
```

The HTML report is self-contained, works offline, and loads no scripts, fonts, or third-party resources. JSON includes the changed paths. Markdown is a compact review summary. **Exports are snapshots**, not live badges. Re-run `check` for current status.

Write reports outside the repository or to an ignored directory. Otherwise the new report itself is a content change.

### Timeouts and age limits

```sh
checkseal run --timeout 120 test -- npm test
checkseal check --require test --max-age 3600
```

Timeout defaults to 600 seconds. Put run options before the label. `--max-age` is in seconds and optionally expires old results even when the files match.

## What the statuses mean

| Status | Meaning |
| --- | --- |
| `FRESH` | Command exited 0, covered files matched before and after, and still match now. |
| `STALE` | Covered files changed after the run. |
| `MUTATED` | Covered files changed while the command ran, such as an auto-formatter. |
| `FAILED` | The command returned a nonzero exit code. |
| `MISSING` | A required label has no receipt. |
| `MISMATCH` | The recorded command or directory does not match configuration. |
| `EXPIRED` | The receipt is outside `--max-age`. |
| `RUNNING` | A run is in progress or was interrupted before finalization. |
| `TIMEOUT` / `INTERRUPTED` / `ERROR` | The run did not complete normally. |
| `INVALID` | Receipt metadata is malformed or inconsistent. |

Exit codes: **0** for a fresh run or all requested checks fresh, **1** for a failed freshness gate, **2** for usage/repository errors. Command exit codes are recorded in receipts; they are not passed through. The self-contained `demo` exits 0 on successful demonstration, including its intentional failing test.

## Exactly what is covered

- All Git-tracked files, including tracked files that match ignore rules.
- Nonignored untracked files, so an uncommitted new source file counts.
- File bytes, presence, type, executable bits, and symlink text.
- Deleted, renamed, staged, and unstaged files, using their **working-tree content**.
- Separate receipt stores for linked Git worktrees.

Staging or committing unchanged content does not invalidate a receipt. The staged index is not independently verified: check the actual content you intend to commit, especially with partial staging.

**Not covered:** ignored files (such as dependencies or `.env`), environment variables, interpreter or compiler binaries, system packages, network services, databases, external symlink targets, timestamps, and file ownership. A dependency or environment change needs a rerun even if Checkseal says fresh. Symlink destinations inside the repository are covered only when those destination files are independently in the Git file inventory.

There are two observed snapshots, not continuous monitoring. A command that changes and restores a file during a run can evade this check. Parallel editors can also race the snapshots. Submodules, sparse checkouts, unresolved merge conflicts, and nested untracked repositories are rejected rather than silently treated as covered.

Receipts are local unsigned JSON in the worktree's Git metadata directory, usually `.git/checkseal/`. Anyone who can edit them can forge results. Checkseal is meant to catch stale evidence and mistakes, not defend against a malicious agent with write access. Commands, arguments and filenames are saved; source contents, child output and environment variables are not. Do not pass secrets as command arguments.

One recording command runs at a time per worktree. A killed parent process may leave `run.lock`; remove it only after confirming no check remains active. Windows is experimental and process-tree cleanup is less complete than on POSIX. Receipt/config JSON has a 16 MiB read limit, so very large repositories may exceed the first release's capacity.

## With a coding agent

Put a short instruction in your existing agent instructions:

> Run the configured checks with `checkseal run test` and `checkseal run lint`. After your last edit, run `checkseal check`. Report its actual status. If evidence is stale, rerun the relevant checks. Do not describe a previous pass as current validation.

This is an instruction, not an enforced security boundary. No plugin or hooks are installed. [Integration examples](docs/integrations.md) include local scripts and CI.

## Related work

This problem has prior art. [gstack](https://github.com/garrytan/gstack) includes a verification-evidence ledger in a broader agent workflow. [ProofRun](https://github.com/yebiguo/ProofRun) also provides local verification receipts for coding agents. [SameBuild](https://github.com/scking21/samebuild) records results for explicit CommonJS artifact/test pairs.

Checkseal explores a small, standalone Python implementation: one runtime file, no third-party runtime dependencies, Git working-tree coverage, required-command binding, and offline reports. Evaluate those projects too. This release makes no claim to be the first or most comprehensive tool in the category.

## Development

```sh
python3 -B -m unittest discover -s tests -v
```

Tests use real disposable Git repositories. They cover fresh/stale transitions, command mutation, failure and timeout handling, config binding, corrupted receipts, worktree isolation, and report escaping.

See [CONTRIBUTING.md](CONTRIBUTING.md). If you try Checkseal, a report about a confusing status or a missed change is more useful than a generic “works for me.” If you want to follow the project, star the repository.

MIT license. Behavior is checked by the included integration suite.
