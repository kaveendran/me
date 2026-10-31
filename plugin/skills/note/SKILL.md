---
name: note
description: Save a quick note to the private vault inbox. Use for /me:note, "note that…", "jot down…", or a dictated thought the user wants kept. Not for todos (use todo) or ideas (use idea).
---

# Quick note

Capture is instant; enrichment happens later. Do not tag, file, research or expand the note now. The nightly inbox librarian does that.

1. Take the note text from `$ARGUMENTS`. If it's empty, ask for the note in one short line.
2. Keep the user's words. Fix only obvious dictation slips. If the text holds several separate thoughts, save each one separately.
3. Save each one with Bash: `me note "<text>"`. Escape double quotes. For multi-line text use `me note - <<'EOF'` … `EOF`.
4. Reply in one line: what you saved and the file path `me` printed.
