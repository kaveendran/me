---
name: ask
description: Answer questions about the user's own life and work from memory. Use for /me:ask, "what did I promise…", "when did we decide…", "what's going on with <person/project>", or "remind me what…". Searches the OpenWiki memory, the vault notes, then Jira and Calendar.
---

# Ask memory

1. Look in the memory wiki first: `~/Documents/me/vault/openwiki/` (start at `quickstart.md`, then `people/`, `projects/`, `commitments.md`, `decisions.md`). `me memory search <words>` finds pages quickly.
2. Check the cited source notes before answering. Memory pages cite note lines, and a fact matters more than a summary.
3. If memory has nothing, grep the private vault (`Daily/`, `Meetings/`, `Inbox/`) and the public notes vault. For work items, use Jira (`searchJiraIssuesUsingJql`, cloudId in `me.toml`) or Calendar (`search_events`).
4. Answer in 1–4 sentences and cite the notes you used as `[[Note name]]`. Say plainly when you don't know. Never invent commitments or decisions.
5. Never quote diary text verbatim unless the user asks for that specific entry.
