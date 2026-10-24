# Branch protection for land-it

What each setting buys, in the article's terms:

| Setting | Rule it enforces |
|---|---|
| `required_status_checks` with `land-it/review` | each pushed commit was reviewed: the status is success only when the tier's review stages completed and no Important finding is open |
| `required_status_checks.strict` | CI must pass on the latest base (a poor man's merge queue) |
| `required_conversation_resolution` | every finding is answered: fixed, or resolved with a reason |
| `dismiss_stale_reviews` | an approval covers one commit; new commits need a new one |
| `require_code_owner_reviews` + CODEOWNERS | high-risk paths always get a person |
| approvals count | how many humans; an agent's approval never counts |

Branch protection on a **private** repo needs GitHub Pro, Team or Enterprise; public repos get it free.

**Read before you write.** `PUT .../protection` replaces the whole rule set: required checks you leave out are
dropped, and `restrictions: null` removes push restrictions. So:

1. `gh api repos/OWNER/REPO/branches/main/protection` (404 means there is none yet).
2. Build the new body from the current one: keep its required checks, add `land-it/review` and the repo's own CI
   job names; keep review rules and restrictions the user has, change only what land-it needs.
3. Show the user the old and new rules side by side, and run the `PUT` only on their yes.

The templates below are for a branch with no protection yet; replace `OWNER/REPO`, `main` and the extra check
names (`contexts`) with the repo's real ones.

## Solo (you are the only human)

GitHub won't let you approve your own PR, so require 0 approvals and let the rest do the work. Be clear with the user about what that
leaves out: nobody independent reviews, and with `enforce_admins: false` an admin can still push past the rules.

```bash
gh api -X PUT repos/OWNER/REPO/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": { "strict": true, "contexts": ["land-it/review"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0, "dismiss_stale_reviews": true },
  "required_conversation_resolution": true,
  "restrictions": null
}
EOF
```

## Team

```bash
gh api -X PUT repos/OWNER/REPO/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": { "strict": true, "contexts": ["land-it/review"] },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "required_conversation_resolution": true,
  "restrictions": null
}
EOF
```

With `.github/CODEOWNERS` listing the paths that always need a person, e.g.:

```
.github/workflows/  @owner
src/billing/        @owner
src/auth/           @owner
```
