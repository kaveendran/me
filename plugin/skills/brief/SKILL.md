---
name: brief
description: Show today's brief (meetings, focus windows, proposed Top 3, reviews, PRs, Jira, risks), building it if it doesn't exist yet. Use for /me:brief, "what's my day look like", "brief me", or "what's on today". To actually plan the day, use the plan skill instead.
---

# Brief

1. Run `me today`. If it says there's no brief yet, tell the user "gathering your day…", run `me brief` (about a minute), then run `me today` again.
2. Give the brief in your own words, short: the headline, the shape of the day, the proposed Top 3, and anything urgent (a failing CI run, a review someone is waiting on, a meeting about to start). Don't paste the raw note.
3. Offer one next step: "Want to plan it properly? (`/me:plan`)"
