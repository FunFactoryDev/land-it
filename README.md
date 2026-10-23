# land-it

**You land it, you own it.** A Claude Code plugin that brings the review pipeline from
[How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/)
into any repository: machines do most of the reading, humans spend attention where the risk is,
and nothing reaches a PR without evidence.

Claude Code already ships the reviewers (`/code-review`, `/security-review`, `/simplify`, `/verify`).
land-it is the part around them: the gates that make them run at the right moment, the repo rules that
tune them, and the evidence that the change you open as a PR is the change that passed.

## What it does

| Practice from the article | land-it |
|---|---|
| Run tests, build and lint, and show the output instead of saying "done" | **Stop hook**: after Claude edits code, it can't end the turn on red checks; it gets the failing output back |
| Formatter and linter hooks | **PostToolUse hook**: per-file `on_edit` commands after every edit; a failure goes straight back to Claude |
| Review in a fresh context; bug hunt, simplify, end-to-end check | **`/land-it:preflight`**: checks → `/simplify` → `/code-review` → `/security-review` → `/verify` |
| Every finding gets an answer; Pre-existing bugs aren't blamed on the author | **Findings ledger**: each finding is fixed, won't fix (reason required) or pre-existing (set from `git blame` against the base); the stamp refuses while one is open |
| The change sets the risk tier, not the author; review depth matches risk | **Risk tiers** from paths (CI and gate config always high, docs low), raised by Claude when the content is riskier, never lowered; low/medium/high pick the review effort and whether a person must read |
| Round cap, diff-only re-review, no repeated findings | **Review rounds**: round 2+ reviews only the range since the last round's snapshot; findings already recorded aren't posted again; past `max_rounds` preflight stops and asks to rethink the approach |
| An agent's approval never counts as a person's | Claude's `gh pr review --approve` is denied; `gh pr merge` asks you unless HEAD is stamped low-tier |
| `REVIEW.md` written by code owners | **`/land-it:init`** writes one for the repo and imports it from CLAUDE.md, so the local `/code-review` follows it too (it doesn't read REVIEW.md on its own) |
| PR template: Testing, Risk, How to revert | init installs it; preflight fills it from real command output |
| An approval covers one specific commit | **PreToolUse hook**: Claude's `gh pr create` is allowed only if HEAD's tree is exactly the tree preflight stamped |
| AI review and security review on every PR, every thread resolved, stale approvals dismissed | `init --ci --protect`: GitHub workflow plus branch protection (asks before touching GitHub) |

## Install

```
/plugin marketplace add FunFactoryDev/land-it
/plugin install land-it@land-it-marketplace --scope project
/land-it:init
```

`--scope project` records the plugin in `.claude/settings.json`, so everyone who clones the repo gets it.
The hooks stay silent in repositories without `.claude/land-it.json`, so a user-scope install is safe too.

Requirements: git, Python 3.9+, a recent Claude Code (tested on 2.1.300).

## Daily use

1. Work as usual. On every edit `on_edit` formats or lints the file; when Claude ends a turn after edits, the
   `stop_checks` run, and red output goes back to Claude instead of a "done".
2. Before the PR: `/land-it:preflight` (add `commit` to let it commit the stamped tree).
3. `gh pr create --body-file <path preflight printed>`. If anything changed after the stamp, the hook says so.

## Configuration: `.claude/land-it.json`

```json
{
  "checks": { "lint": "npm run lint", "typecheck": "npx tsc --noEmit", "test": "npm test", "build": "npm run build" },
  "on_edit": { "*.{ts,tsx,js,jsx,css,json}": "npx prettier --write {file}" },
  "stop_checks": ["lint", "typecheck", "test"],
  "risk": { "high": ["src/auth/**", "src/billing/**", "migrations/**"], "low": ["src/generated/**"] },
  "max_rounds": 3
}
```

| Key | Meaning |
|---|---|
| `checks` | Ordered name → command, run from the repo root; stops at the first failure. `preflight` runs all of them |
| `on_edit` | Glob → command run after Claude edits a matching file; `{file}` is the shell-quoted path |
| `stop_checks` | Which checks the Stop hook runs (default: all). Leave slow suites out |
| `stop_gate` | `false` turns the Stop hook off |
| `risk` | `high` / `medium` / `low` globs. Unlisted paths are medium; `*.md`, `docs/**`, `LICENSE*` default to low; `CLAUDE.md`, `REVIEW.md`, `.claude/**` to medium; CI workflows, CODEOWNERS and this file are always high |
| `max_rounds` | Review rounds before preflight stops and asks to rethink the approach (default 3) |
| `base` | The branch PRs target, if not `origin/HEAD`, `main` or `master` |

Globs follow gitignore rules: `*` stays inside a path segment, `**` crosses segments, `{a,b}` alternates,
a pattern without `/` matches at any depth, and a directory pattern covers everything under it.

## CLI

The plugin puts `land-it` on Claude's PATH; the skills call it. You can call it too
(`python3 <plugin>/bin/land-it …`):

| Command | Does |
|---|---|
| `land-it check` | Run every check, print the output; on success mark the current tree green |
| `land-it risk` | Tier of the change against the merge base, per file (committed, uncommitted and untracked) |
| `land-it round [--reset]` | Start a review round: number, cap, and the snapshot range for a diff-only re-review |
| `land-it findings [add \| resolve ID --status S --reason R]` | The branch's findings ledger; `add` takes JSON on stdin and tags pre-existing lines |
| `land-it stamp [--tier T]` | Bind the green tree and its tier to a stamp; refuses on open findings or a tier below the path floor |
| `land-it status` | Is the worktree green? Does the stamp match the worktree, and HEAD? |

State lives in the git dir (`.git/land-it/`), per worktree, never committed.

## Limitations

- The gates cover Claude's tool calls. You can still run `gh pr create` yourself; that's deliberate.
- The Stop hook tracks files Claude edits with Edit/Write. Files changed only through Bash (`sed -i`) don't mark the session dirty.
- No state lock on Windows (no `fcntl`): parallel hooks may drop a dirty mark.

## License

MIT
