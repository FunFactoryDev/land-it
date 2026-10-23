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



def branch(r, name="feature"):
    sh(r.root, "git", "checkout", "-qb", name)


class Risk(unittest.TestCase):
    def test_tiers(self):
        cfg = {"risk": {"high": ["src/billing/**"], "low": ["src/gen/**", ".claude/land-it.json"]}}
        cases = {"README.md": "low", "docs/a/b.txt": "low", "src/a.py": "medium", "CLAUDE.md": "medium",
                 "src/billing/pay.py": "high", "src/gen/x.py": "low", ".github/workflows/ci.yml": "high",
                 ".claude/land-it.json": "high"}
        for path, tier in cases.items():
            self.assertEqual(LI.file_tier(path, cfg), tier, path)
        self.assertEqual(LI.classify([], cfg)[0], "low")
        self.assertEqual(LI.classify(["README.md", "src/a.py"], cfg)[0], "medium")

    def test_cli_counts_committed_uncommitted_and_untracked(self):
        r = Repo(self, {})
        branch(r)
        r.write("docs/guide.md", "hi\n")
        r.commit("docs")
        self.assertEqual(json.loads(r.cli("risk").stdout)["tier"], "low")
        r.write("src/new.py", "x = 1\n")
        out = json.loads(r.cli("risk").stdout)
        self.assertEqual(out["tier"], "medium")
        self.assertEqual(set(out["files"]), {"docs/guide.md", "src/new.py"})


    def test_review_effort_defaults_and_override(self):
        r = Repo(self, {"review_effort": {"high": "max"}})
        out = json.loads(r.cli("risk").stdout)["review_effort"]
        self.assertEqual(out, {"low": "low", "medium": "medium", "high": "max"})

class Rounds(unittest.TestCase):
    def test_diff_only_after_first_round_and_cap(self):
        r = Repo(self, {"max_rounds": 2})
        branch(r)
        first = json.loads(r.cli("round").stdout)
        self.assertEqual((first["round"], first["target"], first["over_cap"]), (1, "", False))
        self.assertTrue(json.loads(r.cli("round").stdout)["already_reviewed"], "same tree, same round")
        r.write("a.py", "x = 'fixed'\n")
        second = json.loads(r.cli("round").stdout)
        self.assertEqual(second["round"], 2)
        start, end = second["target"].split("..")
        self.assertEqual(sh(r.root, "git", "diff", "--name-only", start, end).split(), ["a.py"])
        r.write("a.py", "x = 'again'\n")
        self.assertTrue(json.loads(r.cli("round").stdout)["over_cap"])
        r.cli("round", "--reset")
        self.assertEqual(json.loads(r.cli("round").stdout)["round"], 1)


class Findings(unittest.TestCase):
    def test_pre_existing_dedupe_and_every_finding_answered(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        branch(r)
        r.write("a.py", "x = 1\ny = 2\n")
        r.commit("add y")
        found = [{"file": "a.py", "line": 1, "summary": "x is wrong"},
                 {"file": os.path.join(r.root, "a.py"), "line": "2", "summary": "y is wrong"}]
        added = json.loads(r.cli("findings", "add", stdin=json.dumps(found)).stdout)
        self.assertEqual([(f["id"], f["status"]) for f in added], [("F1", "pre-existing"), ("F2", "open")])
        again = json.loads(r.cli("findings", "add", stdin=json.dumps(found[1:])).stdout)
        self.assertEqual(again, [], "a finding is never posted twice")
        r.cli("check")
        refused = r.cli("stamp")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("F2", refused.stderr)
        self.assertNotEqual(r.cli("findings", "resolve", "F2", "--status", "wontfix").returncode, 0, "needs a reason")
        self.assertEqual(r.cli("findings", "resolve", "F2", "--status", "wontfix", "--reason", "intended").returncode, 0)
        self.assertEqual(r.cli("stamp").returncode, 0)

    def test_stamp_tier_can_rise_never_drop(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        branch(r)
        r.write(".github/workflows/ci.yml", "on: push\n")
        r.cli("check")
        self.assertNotEqual(r.cli("stamp", "--tier", "low").returncode, 0)
        self.assertEqual(json.loads(r.cli("stamp", "--tier", "high").stdout)["tier"], "high")


class MergeGate(unittest.TestCase):
    def merge(self, r, cmd):
        out = r.hook("pre-bash", tool_input={"command": cmd})
        return out and out["hookSpecificOutput"]["permissionDecision"]

    def test_only_low_stamped_head_merges_without_a_human(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        branch(r)
        r.write("docs/a.md", "x\n")
        r.commit("docs")
        r.cli("check")
        r.cli("stamp")
        self.assertIsNone(self.merge(r, "gh pr merge --squash --delete-branch"))
        self.assertEqual(self.merge(r, "gh pr merge 12 --squash"), "ask", "another PR: can't vouch for it")
        r.write("src/a.py", "x\n")
        r.commit("code")
        r.cli("check")
        r.cli("stamp")
        self.assertEqual(self.merge(r, "gh pr merge --squash"), "ask", "medium needs a person")

    def test_agent_never_approves(self):
        r = Repo(self, {})
        self.assertEqual(self.merge(r, "gh pr review 5 --approve -b lgtm"), "deny")
        self.assertIsNone(self.merge(r, "gh pr review 5 --comment -b 'see line 3'"))
        self.assertIsNone(self.merge(r, "git status"))


class StopLowTier(unittest.TestCase):
    def test_docs_only_turn_skips_checks(self):
        r = Repo(self, {"checks": {"t": "false"}})
        r.edit("README.md", "words\n")
        self.assertIsNone(r.hook("stop"))



class Evals(unittest.TestCase):
    def setUp(self):
        self.r = r = Repo(self, {})
        r.write("a.py", "x = 1\ny = None\nz = 3\n")
        r.commit("bug lands on line 2")
        self.bug = sh(r.root, "git", "rev-parse", "HEAD").strip()
        r.write("b.py", "ok = True\n")
        r.commit("clean change")
        self.clean = sh(r.root, "git", "rev-parse", "HEAD").strip()
        add = r.cli("eval", "add", "--commit", self.bug, "--file", "a.py", "--line", "2", "--summary", "y is None")
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertEqual(r.cli("eval", "add", "--commit", self.clean, "--clean", "--id", "clean1").returncode, 0)
        r.write("REVIEW.md", "RULE-X\n")  # today's rules, uncommitted: the eval must use them

    def stub(self, on_bug, on_clean="Nothing to fix."):
        # a fake reviewer that only "sees" when the copied REVIEW.md is present
        return ("grep -q RULE-X REVIEW.md || exit 3; case {range} in *%s) echo '%s';; *) echo '%s';; esac"
                % (self.bug, on_bug, on_clean))

    def run_eval(self, *args):
        out = self.r.cli("eval", "run", *args)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_scores_and_verdicts(self):
        self.assertIn("baseline recorded", self.run_eval("--reviewer", self.stub("Important: a.py:2 y is None")))
        self.assertIn("no change", self.run_eval("--reviewer", self.stub("a.py:3 near the bug")))
        self.assertIn("more noise", self.run_eval("--reviewer", self.stub("a.py:2", "b.py:1 looks odd")))
        self.assertIn("worse", self.run_eval("--reviewer", self.stub("src/other.py:2")))
        with open(os.path.join(self.r.root, LI.EVAL_DIR, "history.jsonl")) as f:
            runs = [json.loads(ln) for ln in f]
        self.assertEqual([(x["recall"], x["noise"]) for x in runs], [(1.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)])
        self.assertEqual(sh(self.r.root, "git", "worktree", "list").count("\n"), 1, "eval worktrees cleaned up")

    def test_only_is_not_recorded_and_errors_block_comparison(self):
        self.assertIn("not recorded", self.run_eval("--reviewer", self.stub("a.py:2"), "--only", "clean1"))
        self.assertFalse(os.path.exists(os.path.join(self.r.root, LI.EVAL_DIR, "history.jsonl")))
        self.assertIn("failed to run", self.run_eval("--reviewer", "exit 1"))

    def test_bug_case_needs_location(self):
        self.assertNotEqual(self.r.cli("eval", "add", "--commit", self.bug).returncode, 0)


if __name__ == "__main__":
    unittest.main()
