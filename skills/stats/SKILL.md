---
name: stats
description: Report this repo's review numbers the way the Claude Code team measures theirs - one-round passes, mean review rounds, finding outcomes, the revert rate on the base branch and the circuit breaker, and with --github human time per PR (approvals, human comments, hours to merge). Use when the user asks how review is going, whether land-it helps, or whether to loosen or tighten the tiers.
argument-hint: "[--days N] [--github]"
allowed-tools: Bash(land-it *)
---

# land-it stats

Talk to the user in their language. If `land-it` isn't on PATH, call
`python3 ${CLAUDE_PLUGIN_ROOT}/bin/land-it` instead.

Run `land-it stats --days 30` (add `--github` when the user wants human-time numbers; it needs `gh` logged in).
Then read the numbers against the article's goal: **minimum human review time with no rise in reverts or incidents.**
Report a short table, then what it means:

- **One-round passes** (`one_round_pct`) and **mean rounds**: the team moved from 65% to 78% one-round passes.
  Low or falling: findings aren't being fixed at the source. Look at what keeps recurring
  (`land-it findings` on recent branches) and add the rule to REVIEW.md, CLAUDE.md or a lint rule.
- **`wontfix_pct`**: the share of findings rejected, a noise proxy. High: the reviewer is noisy for this repo;
  tighten REVIEW.md (nit cap, skip paths) and confirm with `/land-it:eval run`.
- **`pre_existing_pct`**: old bugs the reviewer surfaces. Worth a sweep of those areas.
- **Revert rate** on the base branch and the **circuit breaker**: the guardrail. If it's tripped, every change's
  floor is one tier higher until the rate falls; say so plainly. If reverts rise, saved human time was only moved.
- **GitHub**: approvals per PR (median, p95), merged without approval, human comments per PR, hours to merge.
  Fewer approvals and comments with a flat revert rate is the goal. Fewer with rising reverts means the tiers are too loose.

Suggest one change at most, the one the numbers support best. Few data points (under ~10 changes): say the numbers
aren't meaningful yet instead of reading trends into them.
