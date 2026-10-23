"""Checks for bin/land-it against throwaway git repos: `python3 -m unittest discover tests`."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "land-it")
_loader = importlib.machinery.SourceFileLoader("land_it", BIN)
LI = importlib.util.module_from_spec(importlib.util.spec_from_loader("land_it", _loader))
_loader.exec_module(LI)


def sh(cwd, *cmd):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


class Repo:
    def __init__(self, tc, cfg):
        self._tmp = tempfile.TemporaryDirectory()
        tc.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "t@example.com")
        sh(self.root, "git", "config", "user.name", "t")
        if cfg is not None:
            self.write(".claude/land-it.json", json.dumps(cfg))
        self.write("a.py", "x = 1\n")
        self.commit("init")

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def commit(self, msg):
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-qm", msg)

    def cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, BIN, *args], cwd=self.root, input=stdin, capture_output=True, text=True)

    def hook(self, event, **data):
        data.setdefault("cwd", self.root)
        data.setdefault("session_id", "s1")
        r = self.cli("hook", event, stdin=json.dumps(data))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else None

    def edit(self, rel, text, session="s1"):
        path = self.write(rel, text)
        return self.hook("post-edit", session_id=session, tool_input={"file_path": path})

    def pr_create(self):
        out = self.hook("pre-bash", tool_input={"command": "git push -u origin HEAD && gh pr create --fill"})
        return out and out["hookSpecificOutput"]["permissionDecision"]


class Glob(unittest.TestCase):
    def test_semantics(self):
        yes = [("*.md", "README.md"), ("*.md", "docs/a.md"), ("docs/**", "docs/x/y.txt"),
               (".github/workflows/**", ".github/workflows/ci.yml"), ("src/*.py", "src/a.py"),
               ("*.{ts,tsx}", "web/b.tsx"), ("src/billing", "src/billing/pay.py"), ("src/billing/", "src/billing/x")]
        no = [("src/*.py", "src/x/a.py"), ("*.{ts,tsx}", "b.js"), ("docs/**", "src/docs.py"), ("a.py", "a.pyc")]
        for pat, path in yes:
            self.assertTrue(LI.matches(pat, path), (pat, path))
        for pat, path in no:
            self.assertFalse(LI.matches(pat, path), (pat, path))


class Gate(unittest.TestCase):
    def test_same_size_edit_right_after_commit_changes_tree(self):
        # regression: a copied index with a fresh mtime hid racy entries from git,
        # so an edit in the commit's second looked unchanged once the clock ticked over
        r = Repo(self, {})
        r.write("a.py", "x = 2\n")
        time.sleep(1.1)
        self.assertNotEqual(LI.worktree_tree(r.root), LI.head_tree(r.root))

    def test_pr_create_needs_stamp_on_head_tree(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        self.assertEqual(r.pr_create(), "deny")
        self.assertEqual(r.cli("check").returncode, 0)
        self.assertEqual(r.cli("stamp").returncode, 0, "clean tree = HEAD tree")
        self.assertIsNone(r.pr_create())
        r.write("a.py", "x = 2\n")
        r.commit("change after stamp")
        self.assertEqual(r.pr_create(), "deny", "a stamp covers one tree only")

    def test_stamp_requires_green_tree(self):
        r = Repo(self, {"checks": {"bad": "false"}})
        self.assertEqual(r.cli("check").returncode, 1)
        self.assertNotEqual(r.cli("stamp").returncode, 0)

    def test_uncommitted_stamp_becomes_valid_once_committed(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        r.write("b.py", "y = 1\n")
        r.cli("check")
        self.assertEqual(r.cli("stamp").returncode, 0)
        self.assertEqual(r.pr_create(), "deny", "stamped content isn't committed yet")
        r.commit("commit the stamped tree")
        self.assertIsNone(r.pr_create())

    def test_silent_without_config(self):
        r = Repo(self, None)
        self.assertIsNone(r.pr_create())
        self.assertIsNone(r.hook("stop"))


class StopHook(unittest.TestCase):
    def test_blocks_red_lets_go_on_green(self):
        r = Repo(self, {"checks": {"t": "test -f ok"}})
        self.assertIsNone(r.hook("stop"), "no edits this session: nothing to prove")
        self.assertIsNone(r.edit("a.py", "x = 3\n"))
        out = r.hook("stop")
        self.assertEqual(out["decision"], "block")
        self.assertIn("== t: FAIL", out["reason"])
        self.assertIsNone(r.hook("stop", stop_hook_active=True), "same red tree: Claude may stop to ask")
        r.edit("ok", "")
        self.assertIn("green", r.hook("stop")["systemMessage"])
        self.assertIsNone(r.hook("stop"), "dirty mark cleared")
        self.assertIsNone(r.hook("stop", session_id="other"), "other sessions untouched")

    def test_gives_up_after_cap(self):
        r = Repo(self, {"checks": {"t": "false"}})
        for i in range(LI.MAX_STOP_BLOCKS):
            r.edit("a.py", "x = %d\n" % i)
            self.assertEqual(r.hook("stop")["decision"], "block")
        r.edit("a.py", "x = -1\n")
        self.assertIn("over to you", r.hook("stop")["systemMessage"])

    def test_off_switch(self):
        r = Repo(self, {"checks": {"t": "false"}, "stop_gate": False})
        r.edit("a.py", "x = 9\n")
        self.assertIsNone(r.hook("stop"))


class OnEdit(unittest.TestCase):
    def test_failing_per_file_command_blocks(self):
        r = Repo(self, {"on_edit": {"*.py": "grep -q GOOD {file}"}})
        out = r.edit("a.py", "bad\n")
        self.assertEqual(out["decision"], "block")
        self.assertIn("a.py", out["reason"])
        self.assertIsNone(r.edit("a.py", "GOOD\n"))
        self.assertIsNone(r.edit("notes.txt", "bad\n"), "glob doesn't match")


if __name__ == "__main__":
    unittest.main()
