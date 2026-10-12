# Changelog

## 0.1.0

Initial alpha release:

- Record arbitrary executable checks with before/after Git content fingerprints.
- Detect stale, failed, mutated, missing, interrupted, expired and mismatched evidence.
- Bind labels to exact commands using repository configuration.
- Export text, JSON, Markdown and offline HTML reports.
- Run a self-contained demonstration in a temporary repository.
- Keep receipts per worktree without modifying repository files or hooks.

First release targets Linux and macOS. Windows is experimental. Local receipts are unsigned and do not verify dependencies, external services, or environment state.
