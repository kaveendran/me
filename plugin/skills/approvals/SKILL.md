---
name: approvals
description: Review the agents' approval queue (drafted PR reviews, Jira comments and transitions, calendar declines, promotions to the public vault) and approve or reject each one. Use for /me:approvals, "what's waiting for me", "any approvals", or "what did the agents propose".
---

# Approvals

1. `me proposals list`. If it's empty, say so and stop.
2. Present them grouped by agent, one line each. For anything outward-facing (a PR review, Jira comment, calendar decline), show the full text with `me proposals show <id>` so the user sees exactly what would be sent.
3. Let the user decide per item, or say "approve all safe" (`me proposals approve-safe` covers only reversible housekeeping).
4. Apply each decision with `me proposals approve <id>` or `me proposals reject <id>`, and report the result in one line. If the user wants a draft edited, reject it and do the edited action directly after they confirm the new text.
