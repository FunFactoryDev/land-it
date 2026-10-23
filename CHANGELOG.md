# Changelog

## 0.1.0

- `/land-it:init`: `.claude/land-it.json`, REVIEW.md, `@REVIEW.md` import in CLAUDE.md, PR template
  (Testing / Risk / How to revert); optional GitHub review + security workflow and branch protection.
- `/land-it:preflight`: checks with shown output → simplify → code review → security review → behavior
  check → stamp → PR body.
- Hooks: `on_edit` per-file commands (PostToolUse), checks before Claude ends a turn after edits (Stop),
  `gh pr create` allowed only for the stamped tree (PreToolUse).
- `land-it` CLI: `check`, `stamp`, `status`.
