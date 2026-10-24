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

`land-it risk` gives the floor from the changed paths. If it says it can't find the base branch, stop and ask
which branch the PR targets (then set `"base"` in `.claude/land-it.json`): an unknown base is not an empty diff. Read the diff and **raise** the tier (never lower it)
when the content is riskier than its path: auth, permissions, billing, data migrations or deletes, concurrency,
crypto, secrets, public API contracts, installers and auto-updaters, system prompts → high. Tell the user
the tier and the reason in one line. If `circuit_breaker.tripped` is true, the floor is already one tier
higher because reverts on the base branch are above the threshold: say so, with the numbers.

## 2. Review round

`land-it round`:
- `over_cap: true` → **stop reviewing.** Run `land-it findings`, group what keeps coming back by class, and tell
  the user the reviewer isn't converging: the approach likely needs rethinking. Ask before going on. After
  the approach changes, `land-it round --reset` starts over.
- `reviewed: true` → this exact tree's code review already finished in this round (see `stages`): skip the
  review runs in step 4, keep steps 5–8. A round that only started proves nothing, so `reviewed` stays false.
- `target` → pass it to the review in step 5. Empty in round 1 (whole change); from round 2 it's a range
  of two snapshots, so the review sees only what changed since the last round.

## 3. Checks

`land-it check`. If it says the commands aren't trusted, stop: show the user the commands and ask them to
type `/land-it:trust`. Red → fix the cause, re-run, repeat. Never edit a test or a check to make it pass
unless the test is wrong, and then say so. Stop and ask after three failed attempts at the same failure.
If it says the checks changed files, a check is formatting or generating code: look at the change, keep it if
it's right, and run `land-it check` again. A tree is green only when the checks passed without touching it.

## 4. Depth by tier

| Tier | Simplify | Code review | Security review | Behavior check | Human |
|---|---|---|---|---|---|
| low | skip | `code-review` with `<effort> <target>` | only if it touches input parsing, auth or secrets | skip | none once checks are green |
| medium | yes | `<effort> <target>` | yes | if runtime behavior changes | one person reads the PR |
| high | yes | `<effort> <target>` | yes | yes | a person reads every high-tier file |

`<effort>` is `review_effort[tier]` from `land-it risk` (default low / medium / high). Don't go above it on
your own: `max` fans out to dozens of agents and takes the better part of an hour. For a high-tier change,
tell the user that `/code-review max` locally or `/code-review ultra` in the cloud (billed; they start it)
is available if one missed bug would cost more than that.

Run the steps in this order, through the Skill tool: `simplify` (then `land-it check` again),
`code-review`, `security-review`, `verify`. `code-review` follows CLAUDE.md, which imports REVIEW.md.

**Record each stage when it has actually run**, with its real outcome:
`land-it stage code --status completed --effort <effort>`, and the same for `simplify`, `security`, `verify`.
A stage that didn't run is `--status skipped` or `--status not_run` with `--reason "…"`. The stamp requires, per
tier: low `code`; medium `code` and `security`; high `code`, `security` and `verify`. Only `verify` may be
`not_run` (with a reason, e.g. a library with nothing to launch); every other required stage must have completed.
Never record `completed` for a review that errored, timed out or was cut short.

If a review skill is unavailable or fails (`security-review` needs `origin/HEAD`, so it fails in a repo without a
remote), don't substitute your own read of the code: spawn a general-purpose subagent with only the diff
(`git diff <merge base>`) and REVIEW.md, none of your reasoning, ask it for findings with `file:line` evidence,
record the stage as completed only when it returned, and say in the PR body which step ran this way. That
fallback is for **code and security review only**. A behavior check means running the thing: tests of the
changed path, the CLI, the app. Reading the diff is not a behavior check; if nothing can run, record
`verify --status not_run --reason "…"`.

## 5. Findings ledger: every finding gets an answer

1. `land-it findings` shows what earlier rounds recorded. Drop new findings that repeat a recorded one
   (same issue, same place); don't re-argue a `wontfix`.
2. Record the rest, security findings included:
   `land-it findings add` with JSON on stdin:
   `[{"file": "src/a.py", "line": 42, "summary": "…", "severity": "important"}]` (`important` or `nit`).
   Each finding comes back with a blame hint, `introduced`: `before` (the line is already on the base),
   `this-change` or `unknown`. It's a hint about the line's age, not a verdict: an old line can become a bug
   because this change now calls it with new input.
3. Answer each `open` one:
   - fix it, then `land-it findings resolve F3 --status fixed`. If it's a class of bug, grep for every
     instance and fix them all.
   - or `land-it findings resolve F3 --status wontfix --reason "…"` (intended behavior, false positive with evidence).
   - or, when the defect really predates this change, `--status pre-existing --reason "…"`: say why, e.g. the
     same failure reproduces on the base, or nothing this change touches reaches the line. Don't fix it here;
     it goes into the PR body as a follow-up.
4. Nits: at most 3, and none while any Important finding is open.

`stamp` refuses while any finding is `open`, and `wontfix` / `pre-existing` need a reason.

## 6. Final checks and stamp

After the last edit: `land-it check` (green on the final tree), then `land-it stamp --tier <tier from step 1>`.
It refuses until the tier's stages are recorded. Its output says `changed_after_review` when the tree moved after
the review (the fixes): name those fixes in the PR body.
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
`gh pr create --body-file <pr_body>`. Pre-existing findings shouldn't vanish with this PR: offer one follow-up
issue listing them (`gh issue create`), and file it only on the user's yes. Review comments that arrive on the
PR later are handled by `/land-it:respond`. Merging is the user's call: the hook asks the user for anything above
low tier, and Claude never approves PRs. End with a short summary: tier, check results, findings
fixed / won't fix / pre-existing, and what a human must look at.
