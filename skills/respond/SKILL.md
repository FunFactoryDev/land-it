---
name: respond
description: Work through every unresolved review thread on a pull request - from Code Review, the CI review workflow, Copilot, or people. Each thread goes into the findings ledger, gets fixed or a reasoned won't-fix, and, once the user confirms, a reply that resolves it. Use when a PR has review comments to address or the user asks to handle PR feedback.
argument-hint: "[PR number]"
allowed-tools: Bash(land-it *), Bash(git status*), Bash(git diff*), Bash(git log*), Bash(git blame*), Bash(gh pr view*), Bash(gh pr diff*)
---

# land-it respond

Talk to the user in their language. If `land-it` isn't on PATH, call
`python3 ${CLAUDE_PLUGIN_ROOT}/bin/land-it` instead. Make sure the PR's branch is checked out
(`gh pr view <PR> --json headRefName`); if it isn't, ask before switching.

The article's rule: **every finding gets an answer**, fixed or resolved with a reason. A silent thread is an unanswered finding.

## 1. Collect

`land-it threads [PR]` lists the unresolved threads: `path`, `line`, `outdated` (the code under it changed since),
`bot` (an automated reviewer) and the first comment. `land-it findings` shows what this branch already recorded.

## 2. Record

Add each thread to the ledger, with its thread id, so the stamp can't happen while one is unanswered:
`land-it findings add` with JSON on stdin, e.g.
`[{"file": "src/a.py", "line": 42, "summary": "…", "severity": "important", "thread": "PRRT_…"}]`.
A thread that repeats a recorded finding takes that finding's answer; don't record it twice.

## 3. Answer each one

- **Outdated**: read the current code first; often it's already fixed.
- **Real bug, introduced by this PR**: fix it (every instance, if it's a class of bug), then
  `land-it findings resolve F… --status fixed`.
- **Not a bug, or intended**: `--status wontfix --reason "…"` with evidence (a test, a doc, the code path).
- **Pre-existing** (the ledger tagged it from blame): not this PR's job; say so and offer a follow-up issue.
- **A person's question** (`bot: false`) may need an answer rather than a code change. Answer it.

## 4. Verify

`land-it check` after the fixes and show the output. If the code changed, run `/land-it:preflight` again: it
starts a new round that reviews only what changed and stamps the new tree.

## 5. Reply, after the user says yes

Draft every reply in one list and show it. Replies are posted under the user's GitHub account, so post only on
their yes; the hook asks again for each one. Then, per thread:

`land-it threads reply <thread id> --body "<reply>" [--resolve]`

- Fixed: `Fixed in <short sha>: <what changed>.` and `--resolve`.
- Won't fix: `Won't fix: <reason>.`; resolve a **bot's** thread, but leave a **person's** thread open for them to close.
- Pre-existing: `Pre-existing (from <short sha>, before this PR); tracked in #<issue>.` and `--resolve`.

Pushing the fix commits is part of answering; ask before `git push` if the user hasn't said to push.

## 6. Report

Threads: N fixed, N won't fix, N pre-existing, N left open for a person, and the check output.
