---
name: review
description: Run or discuss the weekly review (wins, slips, KR progress, wellbeing patterns, next week's experiment). Use for /me:review, "weekly review", "how did my week go", or Friday afternoons.
---

# Weekly review

1. If this week's review note doesn't exist yet (`me vault read reviews`), run `me review` (about a minute).
2. Read it back briefly: the summary, the top 2 wins, the top slip, and the experiment.
3. Ask: "Does that experiment feel right for next week, or shall we pick another?" If they change it, update the note with `me vault section reviews experiment` (new text on stdin).
4. If key results moved, offer to update the Goals & OKRs sheet. That needs the Google Sheets connector; if it's off, say so once.
