"""Shared visual constants for the BotVault GUI.

Deliberately dependency-free (no customtkinter import, no CTkFont objects) so
it can be imported by App.py, dialogs.py, and detail_dialog.py alike without
needing a live Tk root to already exist. Font objects themselves are created
in App.py after the CTk() root is constructed, and passed down to dialogs.
"""

COL = {
    "bg":        "#0B0E0C", # window background
    "panel":     "#10160F", # bars / header strips
    "row":       "#0E140C", # zebra row
    "border":    "#1E2A1C", # hairlines
    "text":      "#C8D6C4", # primary text
    "muted":     "#5A6B56", # labels / dim text
    "muted2":    "#8A9A85", # secondary values
    "green":     "#97C459", # positive / live / accent
    "green_dk":  "#639922",
    "amber":     "#EF9F27", # in-development
    "red":       "#E24B4A", # negative
    "hover":     "#151D12", # row hover
}

# column headers, may add more
# STATUS is an editable dropdown and NOTES is an editable text box, so those
# two are handled specially in _render_row() instead of _cell().
COLUMNS = [
    ("BOT",      "name",     20, "w"),
    ("STRATEGY", "strategy", 11, "w"),
    ("STATUS",   "status",   16, "w"),
    ("VERSION",  "version",   7, "e"),
    ("NOTES",    "notes",    30, "w"),
]

# The tags the user can pick from the STATUS dropdown, each mapped to a
# colour. Must stay in lockstep with backend.config.STATUS_TAGS / the
# bot_version.status_tag CHECK constraint in schema.sql.
STATUS_COLOR = {
    "in development": COL["amber"],
    "finished":       COL["green"],
    "shelved":        COL["muted2"],
}
TAG_OPTIONS = list(STATUS_COLOR)   # the dropdown's option list, in this order
