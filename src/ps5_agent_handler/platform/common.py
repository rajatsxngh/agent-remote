"""What Claude's screen looks like, shared by the Windows and Mac versions.

These are labels and names from Claude's own interface (its accessibility tree),
so both operating systems look for the same things.
"""

from __future__ import annotations

from ..keys import Chord

# Where Claude's own controls live. Searching only here keeps session names in
# the sidebar (say, a session called "Deny the refund") from ever matching.
MAIN_PANE = "Primary pane"
POPUPS_ID = "portal-root"
CONVERSATION = "Chat messages"
SIDEBAR = "Sidebar"

# What a session row's hidden status line says (for screen readers), and what it
# means. The Code tab's own words come first; the others are what Cowork rows say.
SESSION_STATUS_TEXT = {
    "Awaiting input": "attention",
    "Awaiting answer": "attention",
    "Running": "running",
    "Using the browser": "running",
    "Unread response": "unread",
    "Error": "error",
    "Needs attention": "attention",
    "Unread": "unread",
}
IGNORED_STATUS_TEXT = {"Idle", "Done"}

# Buttons that mean Claude is asking your permission for something.
APPROVAL_LABELS = ("Allow once", "Allow for this session", "Always allow", "Deny")
# The box at the end of a reply where Claude asks what to do next, with answer buttons.
NEXT_STEP_BOX = "Suggested next actions"
NO_LABELS = ("Not now", "No", "Skip", "Later", "Cancel")
DISMISS_LABELS = ("Dismiss recap", "Dismiss suggestion", "Dismiss")
# Claude's question card (a question with answers, Skip and Submit) has this class,
# and each answer on it this one.
QUESTION_CARD_CLASS = "epitaxy-approval-card"
ANSWER_CLASS = "epitaxy-answer-option"

# Claude's own shortcuts for its menus (from the Code tab's keyboard shortcuts list),
# written the Windows way; the Mac version turns Ctrl into Cmd.
MENU_SHORTCUTS = {"effort": Chord(("ctrl", "shift"), "e"), "model": Chord(("ctrl", "shift"), "i")}
# Model families in Claude's model menu (to skip its other entries, like "More models").
MODEL_WORDS = ("opus", "sonnet", "haiku", "fable")
