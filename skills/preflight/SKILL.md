---
name: preflight
description: Run the pre-PR pipeline on the current change - risk tier, review round with a cap, checks with shown output, simplify, code review and security review at a depth set by the tier, every finding answered in a ledger, then stamp the exact tree that passed so `gh pr create` is allowed, and draft the PR body (Testing / Risk / How to revert). Use before opening or updating a pull request, or when the user asks to get a change ready to land.
argument-hint: "[commit]"
allowed-tools: Bash(land-it *), Bash(git status*), Bash(git diff*), Bash(git log*), Bash(git blame*)
---

# land-it preflight

Talk to the user in their language. If `land-it` isn't on PATH, call
`python3 ${CLAUDE_PLUGIN_ROOT}/bin/land-it` instead. No `.claude/land-it.json` → offer `/land-it:init` and stop.

The rule underneath every step: **show evidence, don't claim it.** Paste the PASS/FAIL lines, the
finding counts, the commands. "Tests pass" without output is not a step done.

## 1. Risk tier

`land-it risk` gives the floor from the changed paths. Read the diff and **raise** the tier (never lower it)
when the content is riskier than its path: auth, permissions, billing, data migrations or deletes, concurrency,
crypto, secrets, public API contracts, installers and auto-updaters, system prompts → high. Tell the user
the tier and the reason in one line.

## 2. Review round

`land-it round`:
- `over_cap: true` → **stop reviewing.** Run `land-it findings`, group what keeps coming back by class, and tell
  the user the reviewer isn't converging: the approach likely needs rethinking. Ask before going on. After
  the approach changes, `land-it round --reset` starts over.
- `already_reviewed: true` → this exact tree was reviewed in the previous round: skip steps 4–7.
- `target` → pass it to the review in step 5. Empty in round 1 (whole change); from round 2 it's a range
  of two snapshots, so the review sees only what changed since the last round.

## 3. Checks

`land-it check`. Red → fix the cause, re-run, repeat. Never edit a test or a check to make it pass
unless the test is wrong, and then say so. Stop and ask after three failed attempts at the same failure.

## 4. Depth by tier

| Tier | Simplify | Code review | Security review | Behavior check | Human |
|---|---|---|---|---|---|
| low | skip | `code-review` with `low <target>` | only if it touches input parsing, auth or secrets | skip | none once checks are green |
| medium | yes | `high <target>` | yes | if runtime behavior changes | one person reads the PR |
| high | yes | `max <target>` | yes | yes | a person reads every high-tier file; suggest the user run `/code-review ultra` (cloud, billed, they start it) |

Run the reviews through the Skill tool (`simplify`, `code-review`, `security-review`, `verify`); after
`simplify`, run `land-it check` again. `code-review` follows CLAUDE.md, which imports REVIEW.md. If a skill
is unavailable, spawn a general-purpose subagent with only the diff and REVIEW.md, none of your reasoning,
and ask it for findings with `file:line` evidence.

## 5. Findings ledger: every finding gets an answer

1. `land-it findings` shows what earlier rounds recorded. Drop new findings that repeat a recorded one
   (same issue, same place); don't re-argue a `wontfix`.
2. Record the rest, security findings included:
   `land-it findings add` with JSON on stdin:
   `[{"file": "src/a.py", "line": 42, "summary": "…", "severity": "important"}]` (`important` or `nit`).
   The script marks a finding `pre-existing` when blame puts the line on the base branch.
3. Answer each `open` one:
   - fix it, then `land-it findings resolve F3 --status fixed`. If it's a class of bug, grep for every
     instance and fix them all.
   - or `land-it findings resolve F3 --status wontfix --reason "…"` (intended behavior, false positive with evidence).
   - `pre-existing`: don't fix in this change; it goes into the PR body as a follow-up.
4. Nits: at most 3, and none while any Important finding is open.

`stamp` refuses while any finding is `open`.

## 6. Final checks and stamp

After the last edit: `land-it check` (green on the final tree), then `land-it stamp --tier <tier from step 1>`.
The stamp binds the exact tree that passed: Claude's `gh pr create` is allowed only when HEAD's tree is that
tree. Untracked files count: commit them, gitignore them, or delete them before stamping.

## 7. PR body

Write it to the `pr_body` path `stamp` printed:

```
## What and why
<intent; for a bug fix, the root cause>

## Testing
<each command and its PASS line; the behavior check>

## Risk
<tier and why; what could break and what the checks don't cover>
<review: round N, X fixed, Y won't fix (reasons), Z pre-existing (follow-ups)>
<high tier: the files a person must read>

## How to revert
<git revert <sha> / flag to turn off / migration to roll back>
```

## 8. Hand-off

If the tree is dirty: with the `commit` argument, commit everything that was stamped (one commit, message
from the intent). Without it, show `git status` and ask the user to commit or let you. Next:
`gh pr create --body-file <pr_body>`. Merging is the user's call: the hook asks the user for anything above
low tier, and Claude never approves PRs. End with a short summary: tier, check results, findings
fixed / won't fix / pre-existing, and what a human must look at.
