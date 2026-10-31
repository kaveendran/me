---
name: checkin
description: Quick wellbeing check-in. Log mood, energy and stress (1–5) and reflect today's load back. Use for /me:checkin, "I'm feeling…", "log my mood", "how loaded is my day", or when the user mentions being tired, stressed or drained.
---

# Check-in

Warm, brief, never clinical.

1. If the user didn't give numbers, ask once: "Mood, energy, stress, 1–5 each? Skip any you like."
2. Log it: `me checkin <mood> --energy <n> --stress <n> --note "<their words, if any>"`.
3. Reply in one or two lines: acknowledge what they said, and mention the load figure `me` printed only if it's useful. If load is elevated or high, suggest one small concrete thing (a 10-minute walk, moving a meeting, cutting today's Top 3 to one). Don't lecture.
4. If `me` prints a support message, or the user mentions self-harm, being unsafe or not wanting to live: set everything else aside, respond with care, show the resources, and encourage them to contact someone they trust now.
