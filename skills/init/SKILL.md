---
name: init
description: Set up land-it in the current repository - .claude/land-it.json (checks, on-edit formatters), REVIEW.md, the @REVIEW.md import in CLAUDE.md, a PR template with Testing/Risk/How to revert, and optionally a GitHub review workflow and branch protection. Use when the user asks to set up land-it, a pre-PR pipeline, or review rules for a project.
argument-hint: "[--ci] [--sweep] [--protect]"
allowed-tools: Bash(land-it *), Bash(git status*), Bash(git rev-parse*)
---

# land-it init

Talk to the user in their language. Templates live in `${CLAUDE_PLUGIN_ROOT}/templates/`.
If `land-it` isn't on PATH, call `python3 ${CLAUDE_PLUGIN_ROOT}/bin/land-it` instead.

The goal is a repo where the agent has **runnable checks**, the reviewer has **repo-specific rules**, and
every PR carries **Testing, Risk and How to revert**. Don't overwrite an existing file without showing a diff and asking.

## 1. Preconditions

`git rev-parse --show-toplevel`. Not a git repo → offer `git init` and stop until the user answers.

## 2. `.claude/land-it.json`

Read the project's manifests (package.json scripts and lockfile, pyproject.toml, Cargo.toml, go.mod,
Package.swift / *.xcodeproj, Makefile, existing CI workflows) and write the config. Shape: `templates/land-it.json`.

- `checks`: ordered name → shell command, cheapest first (lint, typecheck, test, build). Use the commands
  the project already has (its scripts, its Makefile targets, its CI steps); don't invent tooling.
  Use the package manager the lockfile names (pnpm/yarn/bun/npm).
- `on_edit`: glob → command run on every file Claude edits; `{file}` is the path. Only fast, per-file tools
  (formatter, `ruff check --fix {file}`, `gofmt -w {file}`). Leave it out if the project has no formatter.
- `stop_checks`: the subset the Stop hook runs when Claude ends a turn after edits. Drop slow suites
  (e2e, full builds) here; `preflight` always runs every check.
- `stop_gate: false` turns the Stop hook off for this repo.
- `risk`: globs per tier. Put the repo's real dangerous areas under `high` (auth, billing, payments,
  migrations, installers, release scripts, anything an incident would start in) and generated code or
  fixtures under `low`. Unlisted paths are medium; `*.md` and `docs/**` are low; CI workflows and this
  config are always high. Start strict: it's easier to loosen a tier with evidence than to tighten one after an incident.
- `max_rounds` (default 3): review rounds before preflight stops and asks to rethink the approach.
- `review_effort`: `/code-review` effort per tier (default low/medium/high). Raise `high` to `xhigh` or `max`
  only for a repo where one missed bug costs more than a long, expensive review.
- `base`: the branch PRs target, when it isn't `origin/HEAD`, `main` or `master`. Without a base land-it refuses
  to rate risk or stamp, rather than guess.
- `required_stages`: review stages the stamp requires per tier (default low `code`; medium `code`, `security`;
  high `code`, `security`, `verify`). Loosen only with a reason the user agrees to.
- Checks must not modify files: a check that formats or generates code changes the tree under itself and won't
  count as green. Put formatters in `on_edit`, or use their check mode (`prettier --check`, `ruff format --check`).
- `circuit_breaker` (on by default: `{"revert_rate": 0.05, "days": 14, "min_commits": 20}`): while reverts on
  the base branch are above the rate, every change's floor is one tier higher. `false` turns it off.

- A check may be `{"run": "...", "paths": ["web/**"]}`: it runs only when the change touches those paths.
  Use it in monorepos so a change in one package doesn't wait on every package's suite.

land-it runs these commands only after the user trusts them, so a config edit can't turn into command
execution. Show the user every command you wrote and ask them to type `/land-it:trust`. You can't do it for
them, and `land-it trust` from your shell is refused by design. After they have, run `land-it check` and
show the output. A check that fails because the tool is missing or the command is wrong: fix the config (they'll
need to trust it again). A check that fails on real problems in the code: keep it and tell the user; the repo starts red.

## 3. REVIEW.md

Copy `templates/REVIEW.md` to the repo root and make it this repo's: for each real top-level area, one line on
what failure looks like there; real generated/vendored paths under Skip; 2–5 invariants under Always check
that you can see in the code or docs. Delete example lines that don't apply. Under 60 lines.

## 4. CLAUDE.md

`/code-review` follows CLAUDE.md but doesn't read REVIEW.md, so CLAUDE.md must import it. Add the line
`@REVIEW.md` (once). If there is no CLAUDE.md, write a short one: stack, how to run each check (same
commands as the config), conventions, non-goals, links to deeper docs. Short beats complete.

## 5. PR template

Copy `templates/pull_request_template.md` to `.github/pull_request_template.md` unless one exists; if one
exists, add the missing Testing / Risk / How to revert sections to it.

## 6. Optional, ask first (they touch GitHub or cost money)

- **`--ci` / "AI review on every PR"**: copy `templates/workflows/land-it.yml` to `.github/workflows/`, with
  `LAND_IT_REF` set to `v<version>` from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`, so CI runs the land-it
  release the user has. Each push gets a `land-it/review` commit status: the new commits are reviewed by
  `/land-it:ci-review`, and `land-it ci-verdict` sets success / failure / error from what the review recorded.
  Tell the user which secrets it needs and that the Claude GitHub App must be installed (`/install-github-app`).
  If the repo has no CI running its checks, also write a plain CI workflow that runs the same commands.
- **`--sweep` / "nightly bug sweep"**: copy `templates/workflows/land-it-sweep.yml` to `.github/workflows/`
  (same `LAND_IT_REF`). Each
  night it reviews one `risk.high` area of existing code and files new bugs as one issue labeled `land-it-sweep`.
  Same secrets and app as `--ci`. Tell the user what a run costs: one code review of one area per night.
- **`--protect` / branch protection**: follow `branch-protection.md` next to this file. Ask whether the user
  is solo or a team, fill in OWNER/REPO and check names, show the exact command, run it only on a yes.

## 7. Report

List what was created or changed, the `land-it check` result, and the next step:
"make a change, then run `/land-it:preflight` before opening the PR".
