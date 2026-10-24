# Changelog

## 0.8.0

CI that proves each pushed commit was reviewed (audit findings R11, R12), and a circuit breaker that observes first.

**Upgrading:** rerun `/land-it:init --ci` (and `--sweep`) to replace the workflow files, then make `land-it/review`
the required check instead of the old `review` / `security` jobs. The circuit breaker now only reports by default;
add `"circuit_breaker": {"mode": "enforce"}` to keep 0.5–0.7 behavior.

- **Per-commit review.** The `land-it` workflow reviews only the commits each push added (`before..after`; the whole
  PR when it opens or after a force-push) with the new `/land-it:ci-review` skill, which records findings and stages
  and changes nothing. Before, a re-run on a new push could skip the review because a comment already existed.
- **A status that means the analysis finished.** `land-it ci-verdict` sets the `land-it/review` commit status from
  the recorded review, by the same policy as the local stamp: `success` only when the tier's review stages completed
  and no Important finding is open, `failure` with open Important findings, `error` when the review didn't complete.
  A failed or skipped review can no longer show as a green check. Open findings are posted inline once (fingerprinted),
  ones outside the diff in one summary comment. Claude's shell can't run `ci-verdict`.
- **Pinned and bounded workflows.** Actions pinned to commit SHAs; land-it itself runs from the release tag in
  `LAND_IT_REF` via `--plugin-dir`; `timeout-minutes`; `concurrency` cancels a superseded PR run. The separate
  security action (API key only, `@main`, could pass without analyzing) is replaced by the bundled `/security-review`
  in the same run. `LAND_IT_BASE` gives the PR's base SHA to land-it.
- **Branch protection:** read the current rules, merge, show the diff, then replace, since a `PUT` drops what it
  leaves out. The solo profile says what it doesn't cover.
- **Circuit breaker** defaults to `"mode": "observe"` and compares the exact revert rate with the threshold.
- `stats --github` counts people's inline review comments.

## 0.7.0

The stamp now means what the README says. Each item closes a finding from the independent audit, with its
reproduction as a regression test.

**Upgrading:** preflight records review stages with `land-it stage`; a stamp from a flow that didn't record them is
refused. Formatters that ran as `checks` now fail the check (they change the tree): move them to `on_edit` or use
their check mode.

- **Review stages.** `land-it stage code|security|verify|simplify --status completed|skipped|not_run [--reason]`
  records what actually ran in the current round. The stamp requires, per tier (`required_stages`): low `code`;
  medium `code`, `security`; high `code`, `security`, `verify` (only `verify` may be `not_run`, with a reason).
  `round` reports `reviewed: true` only when the round's code review completed, so a started round no longer
  passes for a finished one. The stamp records the base, the reviewed tree and the stages, and says
  `changed_after_review` when fixes followed the review.
- **Fresh checks.** A failing `check` clears the tree's green mark and any stamp of it. Checks that change the
  tree under themselves (formatters, codegen, an edit meanwhile) don't count as green.
- **No base, no guess.** When the base branch can't be found, `risk` and `stamp` refuse with a hint to set `base`,
  instead of reading an uncomputable diff as an empty, low-risk one.
- **Renames and Unicode.** Changed files come from `git diff --name-status -z -M`: a rename counts both paths
  (moving a file out of a high-risk area is a high-risk change), and non-ASCII names match their globs.
- **Pre-existing is a decision, not a blame result.** `findings add` keeps every finding open and adds a hint,
  `introduced: before | this-change | unknown`; `pre-existing` and `wontfix` both need a reason.
- **Behavior checks run things.** The preflight fallback for an unavailable reviewer covers code and security
  review only; a behavior check that can't run is recorded as `verify --status not_run` with a reason.
- **Evals you can trust.** `eval run` exits 2 when a case failed to run. The judge must exit 0 and return
  non-negative counts no higher than the recorded bugs. Runs compare only on the same corpus (a content
  fingerprint, so an edited case starts a new baseline) and the same scorer (judge command and prompt included);
  unmeasured noise counts as unknown, not zero. Raw reviewer output per case goes to `.git/land-it/eval-runs/`.
  The judge's accuracy against human labels isn't measured, and the docs say so.
- Metrics: a real median for even samples. An invalid `.claude/land-it.json` is a clear error, not a traceback.
- README: a "What the stamp proves, and what it doesn't" section.

## 0.6.2

Security fixes from an independent audit of 0.6.0; each reproduction is now a regression test.

- **Self-trust closed.** `land-it hook …` refuses to run outside Claude Code's hooks (they export `CLAUDE_PLUGIN_ROOT`;
  Bash commands don't), and the policy hook denies Claude's `land-it hook` and writes to the trust store. Before,
  Claude could pipe a fake `UserPromptExpansion` payload into `land-it hook expand` through the pre-approved
  `Bash(land-it *)` and trust its own config commands.
- **No shell injection through file names.** `on_edit` hands the path over in `$LAND_IT_FILE`; `{file}` becomes
  `"$LAND_IT_FILE"`. Before, `"{file}"` inside the user's own quotes let a name like `$(cmd).py` run.
- **Eval overlay can't write outside the checkout.** Today's guidance replaces a symlinked file in the old commit
  instead of writing through it, and a symlinked directory makes the case fail as unsafe.
- **Eval ids and line ranges are validated**; `--id ../x` no longer writes outside `.land-it/evals/`.
- **The policy hook fails closed** (`onFailure: "block"`): a crash or timeout blocks the command instead of allowing it.
- `gh -R owner/repo pr …` and `--repo=` forms go through the same PR gates.
- README: what trust does and doesn't cover, and that the Bash gates mirror GitHub enforcement rather than replace it.
- `RECOMMENDATIONS.ru.md`, an internal audit accidentally committed in 0.6.1, is no longer tracked.

## 0.6.1

land-it reviewed its own code with `/code-review` (fresh context, medium effort): 10 findings, 9 fixed, 1 won't fix.

- PreToolUse checks every deny before any ask. Before, `land-it threads reply … && gh pr review --approve` stopped
  at the reply's prompt, and approving that prompt let the agent's approval through.
- The thread-write gate matches `reply` / `resolve` in any argument order (`land-it threads --body x reply T`).
- `land-it threads resolve ID`; if `--resolve` fails after the reply posted, the error says so and points to
  `resolve` instead of inviting a second identical reply. A null GraphQL payload is a clean error, not a traceback.
- Outdated threads keep their location (`originalLine` when GitHub nulls `line`). A PR can be given as N, #N or a URL.
- `sweep-target`: a glob's static prefix is used only if it exists; otherwise the common parent of what it matches
  (a slash-less `billing` → `src/billing`).
- `eval scan` skips reapplies (a revert of a revert) and shares one revert detector with the circuit breaker.
- `github_stats` uses the shared `gh` runner.
- Won't fix: gating direct `gh api` / `gh pr comment` writes. The gate exists because land-it's skills pre-approve
  `Bash(land-it *)`; direct `gh` writes aren't pre-approved, so Claude Code's own permission prompt covers them.

## 0.6.0

- `/land-it:respond` and `land-it threads [PR | reply ID --body B --resolve]`: every unresolved review thread on a PR
  (Code Review, the CI workflow, Copilot, people) goes into the findings ledger with its thread id, gets fixed or a
  reasoned won't-fix, and is answered and resolved through GitHub GraphQL after the user confirms. Replies always
  trigger a permission prompt, since they post under the user's account; a person's won't-fix thread is left for them to close.
- `/land-it:sweep`, `land-it sweep-target` and `templates/workflows/land-it-sweep.yml` (`init --sweep`): the article's
  nightly review agents. One `risk.high` area per night by rotation (or the top-level source directories), new
  bugs filed as one `land-it-sweep` issue, ones already filed skipped.
- `land-it eval scan`: reverts on the base branch whose reverted commit isn't an eval case yet.
- preflight offers a follow-up issue for pre-existing findings instead of letting them vanish with the PR.

## 0.5.0

- `/land-it:stats` and `land-it stats [--days N] [--github]`: the article's numbers for the repo. One-round passes,
  mean rounds and the share that hit the cap, finding outcomes (fixed / won't fix / pre-existing), tier mix and
  preflight minutes from a per-stamp log; the revert rate among first-parent commits on the base branch; with
  `--github`, approvals and human comments per PR (median, p95), merged without approval, hours to merge, revert PRs.
  Bots are told apart by GraphQL `__typename`, because `gh pr list` shows the Claude app as plain `claude`.
- Circuit breaker, the gap the article names: on by default (`revert_rate` 0.05 over 14 days, at least 20 commits).
  While tripped, every change's floor rises one tier; `risk` and preflight report it, the stamp enforces it.
- `land-it eval run --judge MODEL` (or `--judge-cmd`): an LLM judge decides whether each recorded defect is reported
  and counts distinct findings; runs with different scorers aren't compared.

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
