#!/usr/bin/env python3
"""Checkseal: local check results tied to Git working-tree content. MIT license."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

__version__ = "0.1.0"
SCHEMA = 1
LABEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
LIMIT = 16 * 1024 * 1024


class ChecksealError(Exception):
    """An actionable error, displayed without a traceback by the CLI."""


def git(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(cwd), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise ChecksealError("Git command failed: " + result.stderr.decode(errors="replace").strip())
    return result.stdout


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def safe(value: Any) -> str:
    """Prevent paths/command arguments from injecting terminal control sequences."""
    return str(value).encode("unicode_escape").decode("ascii")


def label_ok(label: str) -> str:
    if not LABEL.fullmatch(label):
        raise ChecksealError("Labels must be 1-64 letters, digits, dots, underscores or hyphens; start with a letter or digit.")
    return label


class Repository:
    def __init__(self, cwd: Path):
        self.root = Path(os.fsdecode(git(cwd, "rev-parse", "--show-toplevel")).strip()).resolve()
        self.gitdir = Path(os.fsdecode(git(self.root, "rev-parse", "--absolute-git-dir")).strip())
        self.store = self.gitdir / "checkseal"
        self.identity = digest(str(self.root))

    def head(self) -> str | None:
        try:
            return git(self.root, "rev-parse", "--verify", "HEAD").decode().strip()
        except ChecksealError:
            return None

    def snapshot(self) -> dict[str, Any]:
        # Reject submodules and conflicted indexes rather than silently claiming coverage.
        for entry in git(self.root, "ls-files", "--stage", "-z").split(b"\0"):
            if not entry:
                continue
            metadata, _ = entry.split(b"\t", 1)
            mode, _, stage = metadata.split()
            if mode == b"160000":
                raise ChecksealError("Submodules are not supported in v0.1. Run checks in a repository without submodules.")
            if stage != b"0":
                raise ChecksealError("Resolve merge conflicts before recording checks.")
        sparse = subprocess.run(
            ["git", "-C", str(self.root), "config", "--bool", "core.sparseCheckout"],
            capture_output=True, check=False,
        )
        if sparse.stdout.strip() == b"true":
            raise ChecksealError("Sparse checkouts are not supported in v0.1.")
        files: dict[str, Any] = {}
        names = set(git(self.root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0"))
        for raw in sorted(names - {b""}):
            name = os.fsdecode(raw)
            path = self.root / name
            # A replaced parent directory could otherwise make a tracked path read outside the repository.
            if not path.parent.resolve().is_relative_to(self.root):
                raise ChecksealError("Path escapes repository through a symlink: " + safe(name))
            try:
                info = path.lstat()
            except FileNotFoundError:
                files[name] = {"kind": "missing"}
                continue
            if stat.S_ISLNK(info.st_mode):
                # Only the link text is covered. External target contents are never read.
                files[name] = {"kind": "symlink", "sha256": hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()}
            elif stat.S_ISREG(info.st_mode):
                hasher = hashlib.sha256()
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                fd = os.open(path, flags)
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode):
                        raise ChecksealError("File type changed during fingerprinting: " + safe(name))
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(chunk)
                    after = os.fstat(stream.fileno())
                if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ChecksealError("File changed during fingerprinting; try again: " + safe(name))
                files[name] = {"kind": "file", "sha256": hasher.hexdigest(), "executable": bool(opened.st_mode & 0o111)}
            else:
                raise ChecksealError("Unsupported file or nested repository: " + safe(name))
        return {"sha256": digest(files), "files": files}

    def config(self) -> dict[str, list[str]]:
        path = self.root / "checkseal.json"
        if not path.exists():
            return {}
        value = read_json(path)
        if set(value) != {"checks"} or not isinstance(value["checks"], dict) or not value["checks"]:
            raise ChecksealError('checkseal.json must contain a nonempty "checks" object and no other keys.')
        for label, command in value["checks"].items():
            label_ok(label)
            validate_command(command)
        return value["checks"]

    def write(self, label: str, value: dict[str, Any]) -> None:
        self.store.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix="receipt-", suffix=".tmp", dir=self.store)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.store / (label_ok(label) + ".json"))
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    @contextlib.contextmanager
    def lock(self):
        self.store.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.store / "run.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ChecksealError("Another check is running, or a previous run was killed. If no run remains, remove " + safe(path)) from None
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(str(os.getpid()))
            yield
        finally:
            path.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > LIMIT:
        raise ChecksealError("JSON file exceeds 16 MiB: " + safe(path))
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (ValueError, UnicodeError) as exc:
        raise ChecksealError("Invalid JSON: " + safe(path)) from exc
    if not isinstance(value, dict):
        raise ChecksealError("Expected a JSON object: " + safe(path))
    return value


def validate_command(command: Any) -> None:
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x and "\0" not in x for x in command):
        raise ChecksealError("A command must be a nonempty array of nonempty strings without NUL characters.")


def changed_paths(before: dict, after: dict) -> list[str]:
    return sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))


def stop_process(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        # Kill remaining descendants in the group, even if its leader already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    process.wait()


def run_check(repo: Repository, label: str, command: list[str], timeout: float, cwd: Path) -> int:
    validate_command(command)
    label_ok(label)
    relative_cwd = cwd.resolve().relative_to(repo.root).as_posix()
    with repo.lock():
        before = repo.snapshot()
        started = time.time()
        receipt: dict[str, Any] = {
            "schema": SCHEMA, "version": __version__, "repository": repo.identity,
            "label": label, "command": command, "cwd": relative_cwd,
            "started": started, "head": repo.head(), "before": before,
            "status": "RUNNING", "exit_code": None,
        }
        # Invalidate previous success before the child starts, including interrupted runs.
        repo.write(label, receipt)
        print("checkseal: running " + safe(label) + " / " + safe(shlex.join(command)), file=sys.stderr, flush=True)
        began = time.monotonic()
        outcome = "COMPLETE"
        code = 127
        error = None
        try:
            process = subprocess.Popen(command, cwd=cwd, start_new_session=(os.name == "posix"))
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_process(process)
                outcome, code = "TIMEOUT", 124
            except KeyboardInterrupt:
                stop_process(process)
                outcome, code = "INTERRUPTED", 130
        except OSError as exc:
            outcome, error = "ERROR", str(exc)
        try:
            after = repo.snapshot()
            changed = changed_paths(before["files"], after["files"])
        except (OSError, ChecksealError) as exc:
            outcome, error, after, changed = "ERROR", str(exc), None, []
        receipt.update({
            "finished": time.time(), "duration_seconds": round(time.monotonic() - began, 3),
            "exit_code": code, "after": after, "changed_during_run": changed,
            "status": outcome, "error": error,
        })
        repo.write(label, receipt)
        state = outcome if outcome != "COMPLETE" else ("FAILED" if code else ("MUTATED" if changed else "FRESH"))
        print(f"checkseal: {safe(label)} {state} ({receipt['duration_seconds']:.3f}s)", file=sys.stderr)
        if error:
            print("checkseal: " + safe(error), file=sys.stderr)
        return 0 if state == "FRESH" else 1


def validate_receipt(value: dict, repo: Repository, label: str) -> None:
    if value.get("schema") != SCHEMA or value.get("label") != label or value.get("repository") != repo.identity:
        raise ChecksealError("Wrong receipt schema, label or repository.")
    validate_command(value.get("command"))
    if not isinstance(value.get("cwd"), str):
        raise ChecksealError("Missing command directory.")
    if value.get("status") not in {"COMPLETE", "RUNNING", "TIMEOUT", "INTERRUPTED", "ERROR"}:
        raise ChecksealError("Unknown receipt status.")
    for name in ("before", "after"):
        snapshot = value.get(name)
        if snapshot is None and (name == "after" and value["status"] != "COMPLETE"):
            continue
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), dict) or snapshot.get("sha256") != digest(snapshot["files"]):
            raise ChecksealError("Receipt fingerprint is missing or inconsistent.")
    if value["status"] != "RUNNING":
        for key in ("finished", "duration_seconds"):
            number = value.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                raise ChecksealError("Invalid receipt time.")
        if type(value.get("exit_code")) is not int:
            raise ChecksealError("Invalid receipt exit code.")


def inspect(repo: Repository, required: list[str] | None = None, max_age: float | None = None) -> dict:
    config = repo.config()
    labels = required or sorted(config) or sorted(p.stem for p in repo.store.glob("*.json"))
    if not labels:
        labels = ["checks"]
    current = repo.snapshot()
    now = time.time()
    rows = []
    running = (repo.store / "run.lock").exists()
    for label in labels:
        label_ok(label)
        row: dict[str, Any] = {"label": label, "status": "MISSING", "reason": "No recorded check.", "changed_paths": []}
        path = repo.store / (label + ".json")
        if path.exists():
            try:
                value = read_json(path)
                validate_receipt(value, repo, label)
                row.update({k: value.get(k) for k in ("command", "cwd", "exit_code", "finished", "duration_seconds")})
                if running or value["status"] == "RUNNING":
                    row.update(status="RUNNING", reason="A check is running or was interrupted before its result was written.")
                elif value["status"] != "COMPLETE":
                    row.update(status=value["status"], reason="The command did not complete normally.")
                elif value["exit_code"] != 0:
                    row.update(status="FAILED", reason=f"Command exited with code {value['exit_code']}.")
                elif value["before"]["sha256"] != value["after"]["sha256"]:
                    row.update(status="MUTATED", reason="Repository content changed during the check.", changed_paths=changed_paths(value["before"]["files"], value["after"]["files"]))
                elif label in config and (value["command"] != config[label] or value["cwd"] != "."):
                    row.update(status="MISMATCH", reason="Recorded command or directory differs from checkseal.json.")
                elif value["after"]["sha256"] != current["sha256"]:
                    row.update(status="STALE", reason="Repository content changed after the check.", changed_paths=changed_paths(value["after"]["files"], current["files"]))
                elif max_age is not None and (now - value["finished"] > max_age or value["finished"] > now):
                    row.update(status="EXPIRED", reason="Receipt is outside the allowed age window.")
                else:
                    row.update(status="FRESH", reason="Command passed; covered content matches the recorded run.")
            except (OSError, ChecksealError, ValueError, TypeError, KeyError) as exc:
                row.update(status="INVALID", reason=str(exc))
        rows.append(row)
    return {"schema": SCHEMA, "tool": "checkseal", "version": __version__, "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "fingerprint": current["sha256"], "covered_files": len(current["files"]), "ok": all(row["status"] == "FRESH" for row in rows), "checks": rows, "scope": "Tracked and nonignored untracked file content, file types and executable bits. Ignored dependencies, environment, external services and symlink targets are not verified. Local unsigned receipts are not attestations."}


def render(report: dict, kind: str) -> str:
    if kind == "json":
        return json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if kind == "html":
        esc = lambda value: html.escape(str(value), quote=True)
        cards = []
        for row in report["checks"]:
            paths = "".join("<li><code>" + esc(p) + "</code></li>" for p in row["changed_paths"])
            command = shlex.join(row.get("command", []))
            cards.append(f'<article><div class="row"><h2>{esc(row["label"])}</h2><b class="{esc(row["status"].lower())}">{esc(row["status"])}</b></div><p>{esc(row["reason"])}</p><code>{esc(command)}</code><ul>{paths}</ul></article>')
        return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>Checkseal | Check freshness report</title><style>
        :root{color-scheme:dark}*{box-sizing:border-box}body{background:#0b1220;color:#e7edf7;font:16px/1.6 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:60px 24px}header{border-bottom:1px solid #2b3548;padding-bottom:28px}small{color:#84dfbd;letter-spacing:2px;font-weight:700}h1{font-size:clamp(32px,6vw,56px);letter-spacing:-2px;line-height:1.12}p{color:#aab9ce}article{background:#141f31;border:1px solid #2b3548;border-radius:16px;padding:22px 26px;margin:18px 0}.row{display:flex;align-items:center;justify-content:space-between;gap:16px}h2{margin:0;font-size:20px}b{color:#ffbe78;font-size:13px;letter-spacing:1px}.fresh{color:#84dfbd}.failed,.error,.invalid{color:#ff8a93}code{font:14px/1.8 ui-monospace,monospace;overflow-wrap:anywhere}footer{border-top:1px solid #2b3548;margin-top:36px;padding-top:16px;font-size:13px}li{color:#ffbe78}a{color:#84dfbd}</style><header><small>CHECKSEAL / LOCAL EVIDENCE</small><h1>Do those tests still apply?</h1><p>''' + str(report["covered_files"]) + ' files fingerprinted. ' + esc(report["checked_at"]) + '</p></header>' + "".join(cards) + '<footer><p>' + esc(report["scope"]) + '</p><p>This exported report is a snapshot, not a live status. Re-run checkseal check before relying on it.</p><p>SHA-256 <code>' + esc(report["fingerprint"]) + '</code></p></footer></html>\n'
    if kind == "markdown":
        lines = ["## Checkseal", "", f"Checked at {report['checked_at']}. {report['covered_files']} covered files.", "", "| Check | Status | Reason |", "| --- | --- | --- |"]
        for row in report["checks"]:
            cells = [row["label"], row["status"], row["reason"]]
            lines.append("| " + " | ".join(html.escape(safe(c)).replace("|", "&#124;") for c in cells) + " |")
        lines += ["", report["scope"], "", "This is an exported snapshot. Re-run `checkseal check` for current status.", ""]
        return "\n".join(lines)
    lines = [f"Checkseal / {report['covered_files']} files / {report['fingerprint'][:12]}", ""]
    for row in report["checks"]:
        lines.append(f"{safe(row['label']):<20} {row['status']:<12} {safe(row['reason'])}")
        lines.extend("  changed: " + safe(p) for p in row["changed_paths"][:12])
        if len(row["changed_paths"]) > 12:
            lines.append(f"  ... {len(row['changed_paths']) - 12} more; use --format json")
    lines.extend(["", "All required checks are fresh." if report["ok"] else "Not ready: re-run the checks above.", "Scope: local file content; not environment, dependencies or remote services.", ""])
    return "\n".join(lines)


def positive(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive finite number") from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return result


def demo() -> int:
    with tempfile.TemporaryDirectory(prefix="checkseal-demo-") as temp:
        root = Path(temp)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (root / "price.py").write_text("def total(price, quantity):\n    return price * quantity\n", encoding="utf-8")
        (root / "test_price.py").write_text("from price import total\nassert total(7, 3) == 21\nprint('1 test passed')\n", encoding="utf-8")
        repo = Repository(root)
        print("\n1. Run a passing test.\n", flush=True)
        run_check(repo, "test", [sys.executable, "-B", "test_price.py"], 30, root)
        print(render(inspect(repo), "text"), flush=True)
        print("2. Change the code after the test.\n", flush=True)
        (root / "price.py").write_text("def total(price, quantity):\n    return price + quantity\n", encoding="utf-8")
        print(render(inspect(repo), "text"), flush=True)
        print("3. Re-run. The test now fails.\n", flush=True)
        run_check(repo, "test", [sys.executable, "-B", "test_price.py"], 30, root)
        print(render(inspect(repo), "text"), flush=True)
        print("Demo finished in a temporary repository. Your project was not changed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record checks. Catch stale results. No services or package dependencies.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="Run a named command and record its content fingerprint")
    run.add_argument("--timeout", type=positive, default=600, help="seconds (default: 600); place before label")
    run.add_argument("label")
    run.add_argument("command", nargs=argparse.REMAINDER)
    check = sub.add_parser("check", help="Verify recorded checks against the current working tree")
    check.add_argument("--require", action="append", default=[], metavar="LABEL")
    check.add_argument("--max-age", type=positive, help="Maximum receipt age in seconds")
    check.add_argument("--format", choices=["text", "json", "markdown", "html"], default="text")
    sub.add_parser("demo", help="Run a self-contained fresh → stale → failed demo")
    args = parser.parse_args(argv)
    try:
        if args.action == "demo":
            return demo()
        repo = Repository(Path.cwd())
        if args.action == "run":
            label_ok(args.label)
            command = args.command
            if command[:1] == ["--"]:
                command = command[1:]
            if command:
                cwd = Path.cwd()
            else:
                command = repo.config().get(args.label)
                cwd = repo.root
                if command is None:
                    raise ChecksealError("No configured command. Use: checkseal run LABEL -- COMMAND [ARGS...]")
            return run_check(repo, args.label, command, args.timeout, cwd)
        report = inspect(repo, args.require, args.max_age)
        print(render(report, args.format), end="")
        return 0 if report["ok"] else 1
    except (ChecksealError, OSError, ValueError) as exc:
        print("checkseal: " + safe(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("checkseal: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
