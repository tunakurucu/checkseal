# Integrations

Checkseal does not install hooks, change agent configuration, send data, or post PR comments. These examples are explicit opt-in integrations.

## Local gate

Install Checkseal and commit a `checkseal.json` with the expected commands. Then create your own script:

```sh
#!/bin/sh
set -eu
checkseal run test
checkseal run lint
checkseal check
```

If lint uses auto-fix, it can change content and invalidate test evidence. Run formatters before recording validation; use read-only lint checks in the gate.

## GitHub Actions

Install Checkseal from a reviewed, pinned commit or vendor the single `checkseal.py` file. This example assumes the file is vendored at `tools/checkseal.py` and your normal dependency setup has already run:

```yaml
- name: Record tests
  run: python3 tools/checkseal.py run test -- npm test
- name: Check current evidence
  run: python3 tools/checkseal.py check --require test --format markdown >> "$GITHUB_STEP_SUMMARY"
```

A fresh checkout does not contain local receipts. Run the checks in CI; do not copy local receipts into it as proof. Normal CI remains the appropriate place for trusted branch checks. Checkseal mostly helps before CI and between steps that may modify the checkout.

## Pre-push gate

A pre-push hook may call `checkseal check` to refuse a stale local state. Install such a hook yourself after reviewing it. Hooks are bypassable and this gate does not prove that the pushed commit matches a partially staged working tree. It is a convenience, not a branch protection policy.

## Reports and privacy

Reports include command arguments and changed filenames. Review them before sharing. JSON/HTML output never includes source file contents, environment values, or captured child stdout/stderr. Secrets supplied as command arguments are still visible.

Redirect reports to an ignored folder or a path outside the repository. An unignored output file changes the fingerprint, including when shell redirection creates it immediately before `check` starts.
