# Changelog

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
