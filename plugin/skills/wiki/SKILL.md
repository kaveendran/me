---
name: wiki
description: Answer questions about one of the user's code repositories from its OpenWiki docs, or refresh them. Use for /me:wiki, "how does <repo> handle…", "where is X implemented in <repo>", or "update the wiki for <repo>".
---

# Repo wiki

- **Answering:** find the repo path (in `me.toml` [codewiki] repos, or ask), read `<repo>/openwiki/quickstart.md`, then follow its routing map to the relevant pages. Answer with page and source-file references. If the wiki doesn't cover something, say so and read the source directly.
- **Refreshing all listed repos:** `me wiki` updates every repo whose HEAD moved since its last wiki run.
- **A repo with no wiki yet:** offer to initialise it with the `openwiki` skill. It writes `openwiki/` plus an AGENTS.md/CLAUDE.md block into that repo, so confirm first.
