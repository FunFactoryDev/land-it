# Changelog

## 0.4.0

**Upgrading:** in each repo, type `/land-it:trust` once. Until then land-it's hooks and `land-it check` won't run
the config's commands.

- Trust: `checks` and `on_edit` run only after the user approves them. A hash per repo is pinned in
  `~/.land-it/trust.json`; any command change needs approval again. Approval comes from the user typing
  `/land-it:trust` (a `UserPromptExpansion` hook, which never fires for skills the model invokes; the skill is
  user-only), or `land-it trust` at a terminal outside Claude Code. Claude's `land-it trust` is denied. This closes
  a path from "Claude may edit files" to "Claude may run commands" through the Stop hook.
- Turn snapshots: a `UserPromptSubmit` hook snapshots the tree, so the Stop gate sees every change made during the
  turn, including through Bash (`sed -i`), and doesn't blame Claude for your edits between turns.
- Path-scoped checks: `{"run": "...", "paths": ["web/**"]}` runs only when the change touches those paths.
- The repo now uses land-it on itself (`.claude/land-it.json`).
- README in English and Russian, with the article this plugin is based on, the trust model and measured token costs.

## 0.3.0

- `/land-it:eval` and `land-it eval add|run|list`: past incidents become reviewer test cases (bug cases with
  file, lines and summary; clean cases for noise). `run` reviews each case in a throwaway worktree under
  today's CLAUDE.md / REVIEW.md / config, scores recall and noise, appends to `.land-it/evals/history.jsonl`,
  and says whether to keep or revert the reviewer change. `--reviewer` scores any other reviewer command.
- `review_effort` config: `/code-review` effort per tier, default low / medium / high. `max` is opt-in: in a
  live run it fanned out to ~45 agents and 40+ minutes on a 6-line diff.
- preflight: explicit step order (simplify before review); a failed review skill falls back to a fresh-context
  subagent instead of Claude's own read, and the PR body says so.

## 0.2.0

- Risk tiers: `land-it risk` classifies the change from its paths (CI, CODEOWNERS and the land-it config
  always high; docs low); preflight may raise the tier from the content, the stamp refuses to lower it.
  The tier sets review effort, security review, behavior check and whether a person must read.
- Review rounds: `land-it round` snapshots the worktree; round 2+ reviews only the range since the previous
  snapshot; past `max_rounds` preflight stops and asks to rethink the approach.
- Findings ledger: `land-it findings add|resolve`; duplicates are dropped, `pre-existing` comes from
  `git blame` against the base, `wontfix` needs a reason, the stamp refuses while any finding is open.
- PreToolUse: Claude's `gh pr review --approve` is denied; `gh pr merge` asks the user unless HEAD is stamped low.
- Stop hook skips turns that only touched low-tier files.

## 0.1.0

- `/land-it:init`: `.claude/land-it.json`, REVIEW.md, `@REVIEW.md` import in CLAUDE.md, PR template
  (Testing / Risk / How to revert); optional GitHub review + security workflow and branch protection.
- `/land-it:preflight`: checks with shown output → simplify → code review → security review → behavior
  check → stamp → PR body.
- Hooks: `on_edit` per-file commands (PostToolUse), checks before Claude ends a turn after edits (Stop),
  `gh pr create` allowed only for the stamped tree (PreToolUse).
- `land-it` CLI: `check`, `stamp`, `status`.
