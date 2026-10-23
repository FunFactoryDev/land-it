# Branch protection for land-it

What each setting buys, in the article's terms:

| Setting | Rule it enforces |
|---|---|
| `required_status_checks.strict` | CI must pass on the latest base (a poor man's merge queue) |
| `required_conversation_resolution` | every finding is answered: fixed, or resolved with a reason |
| `dismiss_stale_reviews` | an approval covers one commit; new commits need a new one |
| `require_code_owner_reviews` + CODEOWNERS | high-risk paths always get a person |
| approvals count | how many humans; an agent's approval never counts |

Branch protection on a **private** repo needs GitHub Pro, Team or Enterprise; public repos get it free.
Replace `OWNER/REPO`, `main` and the check names (`contexts`) with the repo's real ones.
**Always show the command to the user and get a yes before running it.**

## Solo (you are the only human)

GitHub won't let you approve your own PR, so require 0 approvals and let the rest do the work:

```bash
gh api -X PUT repos/OWNER/REPO/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": { "strict": true, "contexts": ["review", "security"] },
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
  "required_status_checks": { "strict": true, "contexts": ["review", "security"] },
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
