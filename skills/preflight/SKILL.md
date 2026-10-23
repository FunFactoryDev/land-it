---
name: preflight
description: Run the full pre-PR pipeline on the current change - checks with shown output, simplify, code review, security review, then stamp the exact tree that passed so `gh pr create` is allowed, and draft the PR body (Testing / Risk / How to revert). Use before opening or updating a pull request, or when the user asks to get a change ready to land.
argument-hint: "[commit]"
allowed-tools: Bash(land-it *), Bash(git status*), Bash(git diff*), Bash(git log*)
---

# land-it preflight

Talk to the user in their language. If `land-it` isn't on PATH, call
`python3 ${CLAUDE_PLUGIN_ROOT}/bin/land-it` instead. No `.claude/land-it.json` → offer `/land-it:init` and stop.

The rule underneath every step: **show evidence, don't claim it.** Paste the PASS/FAIL lines, the
finding counts, the commands. "Tests pass" without output is not a step done.

## 1. Checks

`land-it check`. Red → fix the cause, re-run, repeat. Never edit a test or a check to make it pass
unless the test is wrong, and then say so. Stop and ask after three failed attempts at the same failure.

## 2. Simplify

Run the `simplify` skill on the change (Skill tool). It applies its cleanups; then `land-it check` again.

## 3. Review in a fresh context

Run the `code-review` skill (Skill tool) on the change. It follows CLAUDE.md, which imports REVIEW.md.
If the skill is unavailable, spawn a general-purpose subagent with only the diff (`git diff` against the
base) and REVIEW.md, with no summary of your reasoning, and ask it for findings with `file:line` evidence.

Answer **every** finding, one of:
- **fixed**: make the fix. If it's a class of bug, grep for every instance and fix them all.
- **won't fix**: one-line reason (intended behavior, false positive with evidence).
- **pre-existing**: the line predates this branch (`git blame`); don't fix it in this change, list it for a follow-up.

## 4. Security review

Run the `security-review` skill. Treat its findings like step 3. A real security finding blocks the stamp until fixed.

## 5. Behavior check

If the change alters runtime behavior and the app can run here, run the `verify` skill (or start the app or
CLI yourself) and exercise the changed path. Record what you ran and what you saw.

## 6. Final checks and stamp

After the last edit: `land-it check` (must be green on the final tree), then `land-it stamp`.
The stamp binds the exact tree that passed: the `gh pr create` hook allows a PR only when HEAD's tree is
that tree. Untracked files count: commit them, gitignore them, or delete them before stamping.

## 7. PR body

Write the body to the `pr_body` path that `stamp` printed:

```
## What and why
<intent; for a bug fix, the root cause>

## Testing
<each command and its PASS line; the behavior check from step 5>

## Risk
<what could break, which areas the checks don't cover; review results: N fixed, N won't fix (reasons), N pre-existing>

## How to revert
<git revert <sha> / flag to turn off / migration to roll back>
```

## 8. Hand-off

If the tree is dirty: with the `commit` argument, commit everything that was stamped (one commit, message
from the intent). Without it, show `git status` and ask the user to commit or let you. Then the next
command is `gh pr create --body-file <pr_body>`. End with a short summary: check results, findings
fixed / won't fix / pre-existing, and anything a human must look at.
