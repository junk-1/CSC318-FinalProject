#import libraries for front end

import datetime  # used only for the live clock
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox  # native pop-ups + file picker

import customtkinter as ctk  # modern themed wrapper over tkinter

# App.py now lives in frontend/, but backend/ is a sibling of frontend/ at
# the project root, not of this file — put the project root on sys.path so
# `import backend...` below still resolves regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.exceptions import BotVaultError
from backend.repository import open_repository
from detail_dialog import BotDetailDialog
from dialogs import AddBotDialog
from theme import COL, COLUMNS, STATUS_COLOR, TAG_OPTIONS


# The whole app is one class that subclasses ctk.CTk (the main window).
class BotVault(ctk.CTk):
    def __init__(self):
        # Build the window, open the backend, then assemble the UI top-down.
        super().__init__()                    # build the underlying Tk window
        ctk.set_appearance_mode("dark")       # dark base theme for all widgets

        # --- window basics ---
        self.title("BotVault")                # text in the OS title bar
        self.geometry("1000x600")             # initial width x height in pixels
        self.minsize(880, 480)                # smallest the user can shrink it
        self.configure(fg_color=COL["bg"])    # window background colour

        # monospace fonts (Consolas ships on Windows; falls back elsewhere).
        # Monospace keeps columns vertically aligned — core to the terminal look.
        self.f_title = ctk.CTkFont("Consolas", 15, "bold")
        self.f_mono  = ctk.CTkFont("Consolas", 12)
        self.f_small = ctk.CTkFont("Consolas", 11)

        # Refs to the row widgets currently in the table, so _reload() can
        # destroy them before drawing the new set.
        self._row_widgets = []
        # bot_id -> row frame, rebuilt on every _reload(). Lets a single
        # click toggle a border in place instead of forcing a full reload
        # (see _on_row_click).
        self._row_by_bot_id = {}

        # The currently selected row (single click), used by the Delete
        # hotkey. None means nothing is selected.
        self._selected_bot_id = None

        # Click-to-sort state for the column headers (see _on_sort_header_click).
        self._sort_key = "name"
        self._sort_dir = "asc"

        # SQLite + LMDB backend. Opens/creates the DB on first run.
        self.repo = open_repository()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Assemble the screen top-to-bottom. Order matters: each section is
        # pack()ed in turn, so the first call sits at the top and the last
        # fills the remaining space.
        self._build_topbar()      # green title bar + clock
        self._build_toolbar()     # search + filter + add button
        self._build_table()       # column headers + scrollable rows
        self._build_statusbar()   # hotkey hint bar at the very bottom

        # Hotkeys advertised by the status bar. Bound on the root so they
        # work anywhere, but each handler bails out if a text-entry widget
        # currently has focus (see _typing_target_focused).
        self.bind("<KeyPress-plus>", self._hotkey_add)
        self.bind("<KeyPress-slash>", self._hotkey_search)
        self.bind("<Delete>", self._hotkey_delete)

        self._tick_clock()        # start the 1-second clock loop
        self._reload()            # pull data and paint the table once

    def _on_close(self):
        # Close the DB connection + LMDB env cleanly before the window dies.
        self.repo.close()
        self.destroy()

    # ---- hotkeys -----------------------------------------------------

    def _typing_target_focused(self) -> bool:
        # CustomTkinter's focus_get() returns the *internal* plain-Tk widget
        # (tk.Entry/tk.Text), not the CTkEntry wrapper -- isinstance against
        # ctk.CTkEntry would never match here.
        w = self.focus_get()
        return isinstance(w, (tk.Entry, tk.Text))

    def _hotkey_add(self, _event):
        # "+" -- opens the same Add Bot flow as the toolbar button.
        if self._typing_target_focused():
            return
        self.add_bot()

    def _hotkey_search(self, _event):
        # "/" -- jumps focus to the search box, like most terminal UIs.
        if self._typing_target_focused():
            return  # let "/" type normally into whichever entry has focus
        self.search.focus_set()
        self.search.select_range(0, "end")
        return "break"

    def _hotkey_delete(self, _event):
        # Delete -- removes whichever row is currently selected, if any.
        if self._typing_target_focused():
            return
        if self._selected_bot_id is not None:
            self._confirm_and_delete(self._selected_bot_id)

    def _confirm_and_delete(self, bot_id: int):
        # Blocking Yes/No guard in front of delete_bot() -- deletion also
        # removes LMDB blobs, so it's not undoable.
        if messagebox.askyesno("Delete bot", "Remove this bot and all its versions?"):
            self.delete_bot(bot_id)

    # ======================================================================
    # BACKEND HOOKS  —  wired to backend.repository.BotRepository.
    # The GUI only ever calls these; it never talks to the DB directly.
    # ======================================================================

    def load_bots(self, query: str = "", status: str = "all",
                   sort_key: str = "name", sort_dir: str = "asc") -> list[dict]:
        """Return the bots to show in the table, already filtered + sorted."""
        return self.repo.search_bots(query=query, status=status,
                                      sort_key=sort_key, sort_dir=sort_dir)

    def add_bot(self):
        """Called by the 'Add bot' button (and the '+' hotkey)."""
        path = filedialog.askopenfilename(
            title="Select bot strategy file",
            filetypes=[("Bot files", "*.py *.cs"), ("All files", "*.*")],
        )
        if not path:                          # user hit Cancel
            return

        dlg = AddBotDialog(self, path, self.repo.list_strategies())
        self.wait_window(dlg)
        if dlg.result is None:                # user cancelled the modal
            return

        try:
            # dlg.result tells us whether the user picked an existing
            # strategy or typed a brand new one inline (see dialogs.py) --
            # a new one has to be created first to get its strategy_id.
            if "new_strategy" in dlg.result:
                strategy = self.repo.create_strategy(**dlg.result["new_strategy"])
                strategy_id = strategy["strategy_id"]
            else:
                strategy_id = dlg.result["strategy_id"]
            self.repo.create_bot(path, dlg.result["bot_name"], strategy_id)
        except BotVaultError as e:
            messagebox.showerror("Add bot failed", str(e))
        self._reload()

    def delete_bot(self, bot_id: int):
        # Called after the confirmation dialog has already been accepted.
        try:
            self.repo.delete_bot(bot_id)
        except BotVaultError as e:
            messagebox.showerror("Delete failed", str(e))
        # Clear selection if the deleted bot was the selected one, so the
        # Delete hotkey doesn't try to act on an id that no longer exists.
        if self._selected_bot_id == bot_id:
            self._selected_bot_id = None
        self._reload()

    def set_status(self, bot_id: int, status: str):
        """Persist a new status tag chosen from the dropdown."""
        try:
            self.repo.set_status(bot_id, status)
        except BotVaultError as e:
            messagebox.showerror("Update failed", str(e))

    def set_notes(self, bot_id: int, notes: str):
        """Persist the free-text note for a bot."""
        try:
            self.repo.set_notes(bot_id, notes)
        except BotVaultError as e:
            messagebox.showerror("Update failed", str(e))

    def export_vault(self):
        """Called by the 'Export Vault' toolbar button."""
        path = filedialog.asksaveasfilename(
            title="Export Vault", defaultextension=".zip",
            filetypes=[("Zip archive", "*.zip")],
            initialfile=f"botvault_export_{datetime.date.today():%Y%m%d}.zip",
        )
        if not path:
            return
        try:
            self.repo.export_vault(path)
            messagebox.showinfo("Export complete", f"Vault exported to:\n{path}")
        except BotVaultError as e:
            messagebox.showerror("Export failed", str(e))

    def _on_row_click(self, bot: dict):
        """Single click: select the row (Delete hotkey acts on it).

        Deliberately does NOT call _reload() -- destroying/recreating the row
        widgets mid-click breaks Tk's double-click detection, since the 2nd
        click of a double-click would land on a brand-new widget instance
        instead of the one that saw the 1st click. Instead, just toggle the
        border on the two affected row frames directly.
        """
        prev_id = self._selected_bot_id
        self._selected_bot_id = bot["bot_id"]

        prev_row = self._row_by_bot_id.get(prev_id)
        if prev_row is not None:
            prev_row.configure(border_width=0)

        new_row = self._row_by_bot_id.get(bot["bot_id"])
        if new_row is not None:
            new_row.configure(border_width=1, border_color=COL["green"])

    def _on_row_double_click(self, bot: dict):
        """Double click: open the version-history / backtest detail popup."""
        dlg = BotDetailDialog(self, self.repo, bot)
        self.wait_window(dlg)
        if dlg.changed:
            self._reload()

    # ---- tag / notes event handlers -------------------------------------

    def _on_status_change(self, bot: dict, new_status: str):
        # Fired when the user picks a new tag from a row's dropdown.
        self.set_status(bot["bot_id"], new_status)   # persist the choice
        self._reload()   # repaint so colours + any active status filter update

    def _on_notes_save(self, bot: dict, entry: "ctk.CTkEntry"):
        # Fired on Enter or when the notes box loses focus. We persist the
        # text but do NOT reload — reloading would steal focus mid-typing.
        self.set_notes(bot["bot_id"], entry.get())

    # ======================================================================
    # UI construction  (heavily commented)
    # ======================================================================

    def _build_topbar(self):
        # A CTkFrame is a rectangular container. corner_radius=0 keeps it a
        # sharp-edged bar; height=34 fixes its height in pixels.
        bar = ctk.CTkFrame(self, fg_color=COL["panel"], corner_radius=0, height=34)
        # pack(fill="x") stretches it full-width and stacks it at the top.
        bar.pack(fill="x")
        # A frame normally shrinks to fit its children; turning off propagation
        # forces it to keep the fixed height we set above.
        bar.pack_propagate(False)

        # App name, left-aligned. padx=(14,10) = 14px left gap, 10px right.
        ctk.CTkLabel(bar, text="BOTVAULT//DESK", font=self.f_title,
                     text_color=COL["green"]).pack(side="left", padx=(14, 10))
        # Small subtitle right next to it.
        ctk.CTkLabel(bar, text="v14 · SQLite+LMDB", font=self.f_small,
                     text_color=COL["muted"]).pack(side="left")
        # Clock pinned to the far right; text is filled in by _tick_clock().
        self.clock = ctk.CTkLabel(bar, text="", font=self.f_small,
                                  text_color=COL["muted"])
        self.clock.pack(side="right", padx=14)

        # 1px hairline divider under the title bar (used to sit under the
        # metric strip, which has been removed).
        ctk.CTkFrame(self, fg_color=COL["border"], height=1,
                     corner_radius=0).pack(fill="x")

    def _build_toolbar(self):
        # Row holding the search box, status filter, and add button.
        bar = ctk.CTkFrame(self, fg_color=COL["bg"], corner_radius=0)
        bar.pack(fill="x", padx=14, pady=(8, 4))

        # Search entry. Left-aligned, fixed width.
        self.search = ctk.CTkEntry(
            bar, placeholder_text="search library", font=self.f_mono,
            fg_color=COL["panel"], border_color=COL["border"],
            text_color=COL["text"], width=260, height=30)
        self.search.pack(side="left")
        # <KeyRelease> fires after every keystroke, so results filter live.
        self.search.bind("<KeyRelease>", lambda e: self._reload())

        # Dropdown to filter by status. Options are "all" + every tag. `command`
        # runs on each selection; it receives the chosen value (ignored — we
        # just reload, which re-reads this menu's current value).
        self.status_filter = ctk.CTkOptionMenu(
            bar, values=["all"] + TAG_OPTIONS,
            font=self.f_mono, width=150, height=30,
            fg_color=COL["panel"], button_color=COL["border"],
            button_hover_color=COL["hover"], text_color=COL["text"],
            command=lambda _: self._reload())
        self.status_filter.pack(side="left", padx=8)

        # Green action button, pinned right. `command` = the method to run on
        # click (no parentheses — we pass the function, not its result).
        ctk.CTkButton(
            bar, text="+ Add bot", font=self.f_mono, width=100, height=30,
            fg_color=COL["green_dk"], hover_color=COL["green"],
            text_color="#0B0E0C", command=self.add_bot).pack(side="right")

        # Recoverability NFR: back up the SQLite + LMDB data to a single
        # portable zip archive.
        ctk.CTkButton(
            bar, text="Export Vault", font=self.f_mono, width=110, height=30,
            fg_color=COL["panel"], border_width=1, border_color=COL["border"],
            hover_color=COL["hover"], text_color=COL["text"],
            command=self.export_vault).pack(side="right", padx=(0, 8))

    def _build_table(self):
        # --- header row (fixed, does not scroll) ---
        head = ctk.CTkFrame(self, fg_color=COL["panel"], corner_radius=0, height=26)
        head.pack(fill="x", padx=14)
        self._config_columns(head)            # give it the same column grid
        # One heading label per column, aligned like its data column.
        # Click-to-sort: clicking a header sorts by that column, clicking the
        # active one again flips direction. self._header_labels lets
        # _on_sort_header_click update just the arrow indicator in place.
        self._header_labels = {}
        for j, (title, key, _w, align) in enumerate(COLUMNS):
            lbl = ctk.CTkLabel(head, text=self._header_text(title, key), font=self.f_small,
                               text_color=COL["muted"], cursor="hand2",
                               anchor="e" if align == "e" else "w")
            lbl.grid(row=0, column=j, sticky="ew", padx=8, pady=4)
            lbl.bind("<Button-1>", lambda _e, k=key: self._on_sort_header_click(k))
            self._header_labels[key] = lbl

        # --- scrollable body (holds the bot rows) ---
        # CTkScrollableFrame adds a scrollbar automatically once the rows
        # overflow. expand=True lets it grow to fill leftover window space.
        self.body = ctk.CTkScrollableFrame(self, fg_color=COL["bg"],
                                           corner_radius=0)
        self.body.pack(fill="both", expand=True, padx=14)
        self._config_columns(self.body)

    def _build_statusbar(self):
        # Thin hint bar at the very bottom.
        bar = ctk.CTkFrame(self, fg_color=COL["panel"], corner_radius=0, height=24)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="[+] add   [/] search   [del] remove   "
                     "[dbl-click] details",
                     font=self.f_small, text_color=COL["muted"]).pack(
            side="left", padx=14)

    # ------- helpers ------------------------------------------------------

    def _config_columns(self, frame):
        # Applies the SAME column layout to any frame (header + each row), so
        # every row lines up under the headers. `uniform="tbl"` ties the
        # columns into one group so their widths stay proportional.
        for j, (_t, _k, w, _a) in enumerate(COLUMNS):
            frame.grid_columnconfigure(j, weight=w, uniform="tbl")

    def _header_text(self, title, key):
        # Only the active sort column gets an arrow suffix; every other
        # header just shows its plain title.
        if key != self._sort_key:
            return title
        return title + (" ▲" if self._sort_dir == "asc" else " ▼")

    def _on_sort_header_click(self, key):
        # Clicking the already-active column flips direction; clicking a
        # different column switches to it, defaulting to ascending.
        if self._sort_key == key:
            self._sort_dir = "desc" if self._sort_dir == "asc" else "asc"
        else:
            self._sort_key = key
            self._sort_dir = "asc"
        # Update every header's arrow in place (cheap, and avoids rebuilding
        # the header row just to change one label's text), then re-fetch and
        # repaint the body with the new sort applied.
        for title, k, _w, _a in COLUMNS:
            self._header_labels[k].configure(text=self._header_text(title, k))
        self._reload()

    def _tick_clock(self):
        # Update the clock text, then schedule this method again in 1000ms.
        # after() is tkinter's non-blocking timer (never use sleep() in a GUI).
        self.clock.configure(
            text=datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.after(1000, self._tick_clock)

    def _reload(self):
        # Called on startup and whenever the search/filter/tag changes.
        # Clear the old rows first (destroy frees the widgets).
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets.clear()
        self._row_by_bot_id.clear()

        # Fetch the current (filtered, sorted) bot list from the backend hook.
        bots = self.load_bots(self.search.get().strip(),
                              self.status_filter.get(),
                              self._sort_key, self._sort_dir)

        # Empty-state message if nothing matches / nothing loaded.
        if not bots:
            empty = ctk.CTkLabel(self.body, text="no bots loaded",
                                 font=self.f_mono, text_color=COL["muted"])
            empty.grid(row=0, column=0, columnspan=len(COLUMNS),
                       pady=24, sticky="ew")
            self._row_widgets.append(empty)
            return

        # Draw one row per bot.
        for i, bot in enumerate(bots):
            self._render_row(i, bot)

    def _render_row(self, i, bot):
        # Alternate background colour for readability (zebra striping).
        rbg = COL["row"] if i % 2 else COL["bg"]
        # Each row is its own frame spanning all columns of the body grid.
        # A selected row (set by a single click, cleared on delete/reload)
        # gets a green border -- composes cleanly with hover/zebra below
        # since those only ever touch fg_color, never border_width/colour.
        is_selected = bot["bot_id"] == self._selected_bot_id
        row = ctk.CTkFrame(
            self.body, fg_color=rbg, corner_radius=0,
            border_width=1 if is_selected else 0,
            border_color=COL["green"] if is_selected else rbg)
        row.grid(row=i, column=0, columnspan=len(COLUMNS), sticky="ew")
        self._config_columns(row)             # same column widths as header
        self._row_widgets.append(row)         # remember it so _reload can clear it
        self._row_by_bot_id[bot["bot_id"]] = row

        # `static_cells` collects only the plain-label cells; we bind row
        # hover/click to those. The interactive widgets (dropdown, notes box)
        # are deliberately left out so clicking them edits the value instead
        # of triggering the row-click, and typing isn't interrupted.
        static_cells = []

        for j, (_title, key, _w, align) in enumerate(COLUMNS):
            if key == "status":
                # --- STATUS: editable tag dropdown ---
                menu = ctk.CTkOptionMenu(
                    row, values=TAG_OPTIONS, font=self.f_mono,
                    width=150, height=26,
                    fg_color=COL["panel"], button_color=COL["border"],
                    button_hover_color=COL["hover"],
                    dropdown_fg_color=COL["panel"],
                    dropdown_hover_color=COL["hover"],
                    dropdown_text_color=COL["text"],
                    # text colour reflects the current tag (amber/green/grey)
                    text_color=STATUS_COLOR.get(bot["status"], COL["text"]),
                    # bind the current bot to the callback so we know which
                    # row changed (default-arg trick avoids the late-binding
                    # closure bug in loops).
                    command=lambda val, b=bot: self._on_status_change(b, val))
                menu.set(bot["status"])       # show the bot's current tag
                menu.grid(row=0, column=j, sticky="w", padx=8, pady=6)

            elif key == "notes":
                # --- NOTES: editable free-text box ---
                ent = ctk.CTkEntry(
                    row, placeholder_text="notes…", font=self.f_mono,
                    fg_color=COL["panel"], border_color=COL["border"],
                    text_color=COL["text"], height=26)
                if bot.get("notes"):
                    ent.insert(0, bot["notes"])   # pre-fill saved note
                # Save on Enter or when focus leaves the box.
                ent.bind("<Return>",
                         lambda e, b=bot, en=ent: self._on_notes_save(b, en))
                ent.bind("<FocusOut>",
                         lambda e, b=bot, en=ent: self._on_notes_save(b, en))
                ent.grid(row=0, column=j, sticky="ew", padx=8, pady=6)

            else:
                # --- everything else: plain read-only label ---
                text, colour = self._cell(bot, key)
                lbl = ctk.CTkLabel(row, text=text, font=self.f_mono,
                                   text_color=colour,
                                   anchor="e" if align == "e" else "w")
                lbl.grid(row=0, column=j, sticky="ew", padx=8, pady=6)
                static_cells.append(lbl)

        # --- mouse behaviour for the whole row ---
        # Highlight on hover, restore on leave, and treat a click on a plain
        # cell as selecting that bot. Bound on the row frame + the static
        # label cells only (see note on static_cells above).
        def enter(_):
            row.configure(fg_color=COL["hover"])
        def leave(_):
            row.configure(fg_color=rbg)
        def click(_):
            self._on_row_click(bot)
        def double_click(_):
            self._on_row_double_click(bot)
        for w in (row, *static_cells):
            w.bind("<Enter>", enter)          # mouse enters the widget
            w.bind("<Leave>", leave)          # mouse leaves the widget
            w.bind("<Button-1>", click)       # left mouse click (select)
            w.bind("<Double-Button-1>", double_click)  # open detail popup

    def _cell(self, bot, key):
        """
        Given a bot record and a column key, return (display_text, colour) for
        that one cell. Only handles the plain-label columns — STATUS and NOTES
        are interactive widgets built in _render_row().
        """
        if key == "name":
            # Just the name now; VERSION lives in its own column.
            return bot["name"], COL["text"]
        if key == "strategy":
            return bot["strategy"], COL["muted2"]
        if key == "version":
            # Shown as v4, v7, etc.
            return f"v{bot['version']}", COL["muted2"]
        # Fallback for any key without a dedicated branch above.
        return str(bot.get(key, "")), COL["text"]


if __name__ == "__main__":
    BotVault().mainloop()