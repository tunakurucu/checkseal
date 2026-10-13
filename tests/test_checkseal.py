"""Behavioral integration tests: each test uses a disposable real Git repository."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import checkseal


CLI = Path(checkseal.__file__).resolve()


class ChecksealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.write(".gitignore", "__pycache__/\nignored/\n")
        self.write("app.py", "ANSWER = 42\n")
        self.repo = checkseal.Repository(self.root)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-B", str(CLI), *args], cwd=cwd or self.root, capture_output=True, text=True, timeout=20)

    def run_code(self, code="pass", label="test", timeout=10):
        return self.cli("run", "--timeout", str(timeout), label, "--", sys.executable, "-B", "-c", code)

    def status(self, label="test", **kwargs):
        return checkseal.inspect(self.repo, [label], **kwargs)["checks"][0]["status"]

    def test_success_then_untracked_edit_is_stale(self):
        self.assertEqual(self.run_code().returncode, 0)
        self.assertEqual(self.status(), "FRESH")
        self.write("app.py", "ANSWER = 0\n")
        self.assertEqual(self.status(), "STALE")

    def test_staged_file_worktree_edit_is_stale(self):
        self.git("add", ".")
        self.assertEqual(self.run_code().returncode, 0)
        self.write("app.py", "ANSWER = 0\n")
        self.assertEqual(self.status(), "STALE")

    def test_commit_without_content_change_stays_fresh(self):
        self.git("add", ".")
        self.run_code()
        self.git("commit", "-qm", "Initial")
        self.assertEqual(self.status(), "FRESH")

    def test_deleted_tracked_file_stales_receipt(self):
        self.git("add", ".")
        self.run_code()
        (self.root / "app.py").unlink()
        self.assertEqual(self.status(), "STALE")

    def test_new_untracked_file_stales_receipt(self):
        self.run_code()
        self.write("new.txt", "new")
        self.assertEqual(self.status(), "STALE")

    def test_ignored_files_are_out_of_scope(self):
        self.run_code()
        self.write("ignored/dependency.txt", "changed")
        self.assertEqual(self.status(), "FRESH")

    def test_tracked_ignored_files_are_covered(self):
        self.write("ignored/dependency.txt", "old")
        self.git("add", "-f", "ignored/dependency.txt")
        self.run_code()
        self.write("ignored/dependency.txt", "new")
        self.assertEqual(self.status(), "STALE")

    def test_failed_check_never_becomes_fresh_after_restore(self):
        self.run_code()
        self.assertEqual(self.run_code("raise SystemExit(3)").returncode, 1)
        self.assertEqual(self.status(), "FAILED")

    def test_mutation_during_command_cannot_be_fresh(self):
        result = self.run_code("from pathlib import Path; Path('app.py').write_text('changed')")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.status(), "MUTATED")

    def test_timeout(self):
        result = self.run_code("import time; time.sleep(5)", timeout=0.05)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.status(), "TIMEOUT")

    def test_missing_executable_invalidates_old_success(self):
        self.run_code()
        result = self.cli("run", "test", "--", "checkseal-command-that-does-not-exist")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.status(), "ERROR")

    def test_missing_required_and_empty_store_fail_closed(self):
        self.assertEqual(self.status(), "MISSING")
        self.assertEqual(self.cli("check").returncode, 1)
        self.run_code()
        self.assertEqual(self.cli("check", "--require", "lint").returncode, 1)

    def test_config_runs_root_command_even_from_subdirectory(self):
        command = [sys.executable, "-B", "-c", "from pathlib import Path; assert Path('app.py').exists()"]
        self.write("checkseal.json", json.dumps({"checks": {"test": command, "lint": command}}))
        (self.root / "sub").mkdir()
        self.assertEqual(self.cli("run", "test", cwd=self.root / "sub").returncode, 0)
        self.assertEqual(self.status(), "FRESH")
        self.assertEqual(self.cli("check").returncode, 1)
        self.assertEqual(self.cli("run", "lint").returncode, 0)
        self.assertEqual(self.cli("check").returncode, 0)

    def test_config_rejects_wrong_command_under_right_label(self):
        self.write("checkseal.json", json.dumps({"checks": {"test": [sys.executable, "-c", "print('real check')"]}}))
        self.run_code("pass")
        self.assertEqual(self.status(), "MISMATCH")

    def test_cwd_bound_to_config(self):
        command = [sys.executable, "-B", "-c", "pass"]
        self.write("checkseal.json", json.dumps({"checks": {"test": command}}))
        (self.root / "sub").mkdir()
        result = self.cli("run", "test", "--", *command, cwd=self.root / "sub")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.status(), "MISMATCH")

    def test_age_window(self):
        self.run_code()
        self.assertEqual(self.status(max_age=0.00001), "EXPIRED")

    def test_receipt_corruption(self):
        self.run_code()
        (self.repo.store / "test.json").write_text('{"bad":true}')
        self.assertEqual(self.status(), "INVALID")

    def test_inconsistent_fingerprint_is_invalid(self):
        self.run_code()
        path = self.repo.store / "test.json"
        value = json.loads(path.read_text())
        value["after"]["files"]["injected"] = {}
        path.write_text(json.dumps(value))
        self.assertEqual(self.status(), "INVALID")

    def test_interrupted_record_is_not_previous_success(self):
        self.run_code()
        path = self.repo.store / "test.json"
        value = json.loads(path.read_text())
        value["status"] = "RUNNING"
        path.write_text(json.dumps(value))
        self.assertEqual(self.status(), "RUNNING")

    def test_concurrent_run_is_rejected_and_check_is_not_green(self):
        self.run_code()
        with self.repo.lock():
            self.assertEqual(self.run_code().returncode, 2)
            self.assertEqual(self.status(), "RUNNING")
        self.assertEqual(self.status(), "FRESH")

    def test_no_raw_source_or_output_stored(self):
        secret = "canary-do-not-store-12345"
        self.write("app.py", "print(" + repr(secret) + ")")
        result = self.cli("run", "test", "--", sys.executable, "-B", "app.py")
        self.assertIn(secret, result.stdout)
        self.assertNotIn(secret, (self.repo.store / "test.json").read_text())

    def test_shell_metacharacters_are_literal_arguments(self):
        result = self.cli("run", "test", "--", sys.executable, "-B", "-c", "import sys; assert sys.argv[1] == '; touch owned'", "; touch owned")
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.root / "owned").exists())

    def test_labels_cannot_escape_store(self):
        self.assertEqual(self.run_code(label="../bad").returncode, 2)
        self.assertEqual(self.cli("check", "--require", "../bad").returncode, 2)

    def test_unusual_filename_supported_and_terminal_escaped(self):
        self.write("space and\ttab\nname.txt", "old")
        self.run_code()
        self.write("space and\ttab\nname.txt", "new")
        report = checkseal.inspect(self.repo)
        self.assertFalse(report["ok"])
        self.assertIn(r"space and\ttab\nname.txt", checkseal.render(report, "text"))

    def test_html_escapes_paths_and_commands(self):
        name = "<img src=x onerror=alert(1)>.txt"
        self.write(name, "old")
        self.run_code()
        self.write(name, "new")
        rendered = checkseal.render(checkseal.inspect(self.repo), "html")
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;img", rendered)
        self.assertIn("Content-Security-Policy", rendered)

    def test_json_report_exit_status(self):
        self.run_code()
        result = self.cli("check", "--format", "json")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_invalid_config_fails(self):
        self.write("checkseal.json", '{"checks":{"test":"npm test"}}')
        self.assertEqual(self.cli("run", "test").returncode, 2)
        self.assertEqual(self.cli("check").returncode, 2)

    def test_nonfinite_timeout_rejected(self):
        self.assertEqual(self.run_code(timeout="nan").returncode, 2)

    def test_sparse_checkout_is_explicitly_unsupported(self):
        self.git("config", "core.sparseCheckout", "true")
        self.assertEqual(self.run_code().returncode, 2)

    def test_submodule_gitlink_is_explicitly_unsupported(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")
        commit = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.git("update-index", "--add", "--cacheinfo", "160000," + commit + ",dependency")
        result = self.run_code()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Submodules", result.stderr)

    def test_unmerged_index_is_explicitly_unsupported(self):
        self.git("add", ".")
        blob = self.git("hash-object", "app.py").stdout.decode().strip()
        records = f"0 {'0' * 40}\tapp.py\n100644 {blob} 1\tapp.py\n100644 {blob} 2\tapp.py\n"
        subprocess.run(["git", "-C", str(self.root), "update-index", "--index-info"], input=records, text=True, check=True)
        result = self.run_code()
        self.assertEqual(result.returncode, 2)
        self.assertIn("merge conflicts", result.stderr)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_timeout_stops_descendant_process(self):
        child = "import time; from pathlib import Path; time.sleep(0.6); Path('leaked-child').write_text('bad')"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(10)"
        result = self.run_code(parent, timeout=0.2)
        self.assertEqual(result.returncode, 1)
        time.sleep(0.7)
        self.assertFalse((self.root / "leaked-child").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX symlink and mode semantics")
    def test_symlink_target_contents_are_not_read(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "private"
            target.write_text("private")
            os.symlink(target, self.root / "link")
            self.run_code()
            target.write_text("new private")
            self.assertEqual(self.status(), "FRESH")
            (self.root / "link").unlink()
            os.symlink("app.py", self.root / "link")
            self.assertEqual(self.status(), "STALE")

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_executable_bit_change(self):
        self.run_code()
        (self.root / "app.py").chmod(0o755)
        self.assertEqual(self.status(), "STALE")

    @unittest.skipUnless(os.name == "posix", "POSIX symlink semantics")
    def test_tracked_parent_symlink_escape_is_rejected(self):
        self.write("dir/private.txt", "original")
        self.git("add", ".")
        (self.root / "dir/private.txt").unlink()
        (self.root / "dir").rmdir()
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "private.txt").write_text("secret")
            os.symlink(outside, self.root / "dir")
            self.assertEqual(self.run_code().returncode, 2)

    def test_worktree_has_separate_receipt_store(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")
        self.run_code()
        with tempfile.TemporaryDirectory() as outside:
            worktree = Path(outside) / "work"
            self.git("worktree", "add", "-q", "-b", "other", str(worktree))
            other = checkseal.Repository(worktree)
            self.assertNotEqual(other.store, self.repo.store)
            self.assertFalse(checkseal.inspect(other)["ok"])
            self.git("worktree", "remove", "--force", str(worktree))

    def test_demo_is_self_contained(self):
        before = self.repo.snapshot()
        result = self.cli("demo")
        self.assertEqual(result.returncode, 0)
        self.assertIn("STALE", result.stdout)
        self.assertIn("FAILED", result.stdout)
        self.assertEqual(before, self.repo.snapshot())


if __name__ == "__main__":
    unittest.main()
