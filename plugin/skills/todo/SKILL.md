---
name: todo
description: Save a todo to the private vault inbox. Use for /me:todo, "remind me to…", "I need to…", or a dictated task. Offers (never creates on its own) a Jira ticket when the task is clearly team work.
---

# Quick todo

1. Take the task from `$ARGUMENTS`. If it's empty, ask for it in one short line.
2. Phrase each task as an action ("Email Ravi the draft", not "Ravi draft"). Keep names, dates and ticket keys exactly as given. Several tasks get one call each.
3. Save each one: `me todo "<task>"`.
4. Reply in one line with the saved task(s).
5. If a task is clearly team work for Jira (mentions a ticket, a bug in a work repo, or a teammate's request), offer one line: "Want this as a Jira ticket too?" Create it only after the user says yes, and confirm the project and summary first.
