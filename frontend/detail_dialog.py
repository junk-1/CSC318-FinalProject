"""Bot detail popup: version history + backtest documents.

Opened on double-click of a bot row (single click is reserved for row
selection / the Delete hotkey in App.py). This is the "Manage Versions" /
"Store Performance Metrics" use case from the requirements doc, built as
the final milestone once the rest of the backend wiring was in place --
get_versions/add_version/get_code/create_backtest/list_backtests/
delete_backtest in backend/repository.py were written earlier specifically
to make this dialog a thin UI layer over already-tested repository calls.
"""

import os
from tkinter import filedialog, messagebox

import customtkinter as ctk

from backend.exceptions import BotVaultError
from theme import COL, STATUS_COLOR


def _short_hash(h: str) -> str:
    # Full sha256 hex digests are 64 characters -- way too wide for a table
    # column, so the version list only ever shows a truncated preview.
    return h[:10] + "…" if len(h) > 10 else h


class BotDetailDialog(ctk.CTkToplevel):
    """`.changed` is set True if anything mutated that could affect the main
    table (a new version was uploaded, changing the displayed head, or the
    bot was renamed/reassigned) -- App.py reloads the table when this is
    True after the dialog closes."""

    def __init__(self, parent, repo, bot: dict):
        # bot is the row dict from the main table (App._on_row_double_click).
        super().__init__(parent)
        self.repo = repo
        self.bot = bot                     # mutated in place as edits are saved (see _on_save_header)
        self.changed = False
        self._selected_version_id = None   # drives the border highlight + Export Code's target
        self._selected_backtest_id = None  # same, for the backtest list
        self._version_rows = []            # widget refs so _reload_versions can clear them
        self._backtest_rows = []

        # Read fonts off the parent window rather than constructing new
        # CTkFont objects -- same reasoning as AddBotDialog in dialogs.py.
        self.f_mono = parent.f_mono
        self.f_small = parent.f_small

        self.title(f"Bot Detail — {bot['name']}")
        self.configure(fg_color=COL["bg"])
        self.geometry("640x600")
        self.minsize(560, 400)
        self.transient(parent)

        # The footer is packed FIRST with side="bottom" so its space is
        # reserved and it's always visible, then the scrollable body is
        # packed to fill whatever remains above it. Without this, the
        # header + version list + backtest list can be taller than the
        # window (very likely once Windows display scaling is above
        # 100%, which is the common case) with no way to reach whatever
        # gets pushed below the bottom edge -- which is exactly what was
        # happening to the Add Backtest button.
        self._build_footer()

        self._scroll = ctk.CTkScrollableFrame(self, fg_color=COL["bg"], corner_radius=0)
        self._scroll.pack(fill="both", expand=True)

        # Layout first, then populate the two lists -- keeps widget
        # construction (_build_*) separate from data loading (_reload_*),
        # so a later refresh doesn't need to rebuild buttons. Both sections
        # are built inside self._scroll (not self) so their contents scroll
        # along with the header instead of having their own independent
        # inner scrollbar -- one scrollable region for the whole dialog,
        # not several nested ones fighting over mouse-wheel events.
        self._build_header(self._scroll)
        self._build_versions_section(self._scroll)
        self._build_backtests_section(self._scroll)

        self._reload_versions()
        self._reload_backtests()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Same modal-setup order as AddBotDialog: build content, measure it,
        # THEN grab/focus -- grab_set() on a not-yet-viewable window raises.
        self.update_idletasks()
        self._center_over(parent)
        self.grab_set()
        self.focus_force()

    # ---- layout --------------------------------------------------------

    def _build_header(self, parent):
        # Bot name + strategy are editable here (rather than in the main
        # table, which keeps BOT/STRATEGY read-only) -- this popup is
        # already the "display bot data" pop-up box from the requirements
        # doc, so it's the natural home for bot-level edits too.
        head = ctk.CTkFrame(parent, fg_color=COL["panel"], corner_radius=0)
        head.pack(fill="x")

        row1 = ctk.CTkFrame(head, fg_color=COL["panel"])
        row1.pack(fill="x", padx=14, pady=(10, 4))

        self._name_entry = ctk.CTkEntry(row1, font=self.f_mono, width=240,
                                         fg_color=COL["bg"], border_color=COL["border"],
                                         text_color=COL["green"])
        self._name_entry.insert(0, self.bot["name"])
        self._name_entry.pack(side="left")

        self._strategies = self.repo.list_strategies()
        strat_names = [s["strategy_name"] for s in self._strategies]
        self._strategy_menu = ctk.CTkOptionMenu(
            row1, values=strat_names or [self.bot["strategy"]], font=self.f_small,
            width=150, height=26, fg_color=COL["bg"], button_color=COL["border"],
            button_hover_color=COL["hover"], text_color=COL["text"],
            dropdown_fg_color=COL["panel"], dropdown_hover_color=COL["hover"],
            dropdown_text_color=COL["text"])
        if self.bot["strategy"] in strat_names:
            self._strategy_menu.set(self.bot["strategy"])
        self._strategy_menu.pack(side="left", padx=(8, 0))

        ctk.CTkButton(row1, text="Save", font=self.f_small, width=60, height=26,
                      fg_color=COL["green_dk"], hover_color=COL["green"],
                      text_color="#0B0E0C", command=self._on_save_header).pack(
            side="left", padx=(8, 0))

        self._header_error = ctk.CTkLabel(head, text="", font=self.f_small,
                                           text_color=COL["red"])
        self._header_error.pack(anchor="w", padx=14, pady=(0, 10))

    def _on_save_header(self):
        # Only call the repository for whichever field actually changed --
        # avoids a pointless UPDATE (and, for rename, needlessly re-checking
        # blankness) when the user only touched the other field.
        new_name = self._name_entry.get().strip()
        if not new_name:
            self._header_error.configure(text="Bot name cannot be blank.")
            return
        chosen = self._strategy_menu.get()
        strategy_id = next(
            (s["strategy_id"] for s in self._strategies if s["strategy_name"] == chosen),
            None,
        )
        try:
            if new_name != self.bot["name"]:
                self.repo.rename_bot(self.bot["bot_id"], new_name)
            if strategy_id is not None and chosen != self.bot["strategy"]:
                self.repo.set_strategy(self.bot["bot_id"], strategy_id)
        except BotVaultError as e:
            self._header_error.configure(text=str(e))
            return
        self._header_error.configure(text="")
        # Keep self.bot in sync so a second edit in the same dialog session
        # diffs against the latest saved values, not the stale ones this
        # dialog was opened with.
        self.bot["name"] = new_name
        self.bot["strategy"] = chosen
        self.title(f"Bot Detail — {new_name}")
        self.changed = True

    def _build_versions_section(self, parent):
        # Static section title + column header row (labels only -- the
        # actual data rows are drawn into versions_body by _reload_versions,
        # same "static header, rebuilt body" split App.py's main table uses).
        ctk.CTkLabel(parent, text="VERSION HISTORY", font=self.f_small,
                     text_color=COL["muted"]).pack(anchor="w", padx=14, pady=(10, 2))

        vhead = ctk.CTkFrame(parent, fg_color=COL["panel"], corner_radius=0, height=22)
        vhead.pack(fill="x", padx=14)
        vhead.pack_propagate(False)
        for text, w in (("VER", 1), ("STATUS", 3), ("DATE", 3), ("FILE", 3), ("HASH", 2)):
            ctk.CTkLabel(vhead, text=text, font=self.f_small, text_color=COL["muted"],
                         anchor="w").pack(side="left", padx=6)

        # A plain (non-scrolling) frame -- the outer self._scroll built in
        # __init__ is the ONLY scrollable region in this dialog now, so this
        # just grows to fit however many version rows there are.
        self.versions_body = ctk.CTkFrame(parent, fg_color=COL["bg"], corner_radius=0)
        self.versions_body.pack(fill="x", padx=14)

        vbtns = ctk.CTkFrame(parent, fg_color=COL["bg"])
        vbtns.pack(fill="x", padx=14, pady=(4, 0))
        ctk.CTkButton(vbtns, text="Upload New Version", font=self.f_mono,
                      width=160, height=26, fg_color=COL["green_dk"],
                      hover_color=COL["green"], text_color="#0B0E0C",
                      command=self._on_upload_version).pack(side="left")
        # Acts on whichever version row is currently selected (see
        # _reload_versions' select() closure) -- not necessarily the head.
        ctk.CTkButton(vbtns, text="Export Code", font=self.f_mono,
                      width=120, height=26, fg_color=COL["panel"],
                      border_width=1, border_color=COL["border"],
                      hover_color=COL["hover"], text_color=COL["text"],
                      command=self._on_export_code).pack(side="left", padx=(8, 0))

    def _build_backtests_section(self, parent):
        # Same header/body/buttons shape as the versions section above.
        ctk.CTkLabel(parent, text="BACKTESTS", font=self.f_small,
                     text_color=COL["muted"]).pack(anchor="w", padx=14, pady=(14, 2))

        bhead = ctk.CTkFrame(parent, fg_color=COL["panel"], corner_radius=0, height=22)
        bhead.pack(fill="x", padx=14)
        bhead.pack_propagate(False)
        for text in ("PERIOD", "FILE", "NOTE"):
            ctk.CTkLabel(bhead, text=text, font=self.f_small, text_color=COL["muted"],
                         anchor="w").pack(side="left", padx=6)

        # Plain (non-scrolling) frame, same reasoning as versions_body above.
        self.backtests_body = ctk.CTkFrame(parent, fg_color=COL["bg"], corner_radius=0)
        self.backtests_body.pack(fill="x", padx=14)

        # Inline "add backtest" form -- built once here but deliberately
        # never packed in this method, so it starts off invisible. It's
        # packed/unpacked on demand by _on_add_backtest_clicked /
        # _cancel_backtest_form / _on_save_backtest, instead of opening a
        # whole separate dialog for three fields.
        self._backtest_form = ctk.CTkFrame(parent, fg_color=COL["bg"])
        self._bt_start = ctk.CTkEntry(self._backtest_form, placeholder_text="start (YYYY-MM-DD)",
                                       font=self.f_mono, width=140, fg_color=COL["panel"],
                                       border_color=COL["border"], text_color=COL["text"])
        self._bt_end = ctk.CTkEntry(self._backtest_form, placeholder_text="end (YYYY-MM-DD)",
                                     font=self.f_mono, width=140, fg_color=COL["panel"],
                                     border_color=COL["border"], text_color=COL["text"])
        self._bt_note = ctk.CTkEntry(self._backtest_form, placeholder_text="note",
                                      font=self.f_mono, width=200, fg_color=COL["panel"],
                                      border_color=COL["border"], text_color=COL["text"])
        self._bt_start.pack(side="left")
        self._bt_end.pack(side="left", padx=(6, 0))
        self._bt_note.pack(side="left", padx=(6, 0))
        ctk.CTkButton(self._backtest_form, text="Save", font=self.f_mono,
                      width=70, height=26, fg_color=COL["green_dk"],
                      hover_color=COL["green"], text_color="#0B0E0C",
                      command=self._on_save_backtest).pack(side="left", padx=(6, 0))
        ctk.CTkButton(self._backtest_form, text="Cancel", font=self.f_mono,
                      width=70, height=26, fg_color=COL["panel"],
                      border_width=1, border_color=COL["border"],
                      hover_color=COL["hover"], text_color=COL["text"],
                      command=self._cancel_backtest_form).pack(side="left", padx=(6, 0))

        bbtns = ctk.CTkFrame(parent, fg_color=COL["bg"])
        bbtns.pack(fill="x", padx=14, pady=(4, 0))
        ctk.CTkButton(bbtns, text="Add Backtest", font=self.f_mono,
                      width=120, height=26, fg_color=COL["green_dk"],
                      hover_color=COL["green"], text_color="#0B0E0C",
                      command=self._on_add_backtest_clicked).pack(side="left")
        ctk.CTkButton(bbtns, text="Export", font=self.f_mono,
                      width=90, height=26, fg_color=COL["panel"],
                      border_width=1, border_color=COL["border"],
                      hover_color=COL["hover"], text_color=COL["text"],
                      command=self._on_export_backtest).pack(side="left", padx=(8, 0))
        ctk.CTkButton(bbtns, text="Delete", font=self.f_mono,
                      width=90, height=26, fg_color=COL["panel"],
                      border_width=1, border_color=COL["border"],
                      hover_color=COL["hover"], text_color=COL["red"],
                      command=self._on_delete_backtest).pack(side="left", padx=(8, 0))

    def _build_footer(self):
        # Its own bar, packed with side="bottom" directly on the Toplevel
        # (not inside self._scroll) -- Close stays pinned and visible no
        # matter how far the scrollable body above it is scrolled.
        bar = ctk.CTkFrame(self, fg_color=COL["bg"], corner_radius=0)
        bar.pack(side="bottom", fill="x")
        ctk.CTkButton(bar, text="Close", font=self.f_mono, width=90, height=28,
                      fg_color=COL["panel"], border_width=1, border_color=COL["border"],
                      hover_color=COL["hover"], text_color=COL["text"],
                      command=self._on_close).pack(anchor="e", padx=14, pady=14)

    def _center_over(self, parent):
        # Same approach as AddBotDialog's -- must run after update_idletasks()
        # on both windows or the width/height reads are stale placeholders.
        parent.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    # ---- versions --------------------------------------------------------

    def _reload_versions(self):
        # Full destroy-and-rebuild of the list, same pattern as App._reload:
        # simple and fast enough at this scale (a handful of versions per
        # bot), and this popup has no double-click/typing interactions on
        # these rows that a rebuild could disrupt (unlike App's main table).
        for w in self._version_rows:
            w.destroy()
        self._version_rows.clear()

        versions = self.repo.get_versions(self.bot["bot_id"])
        for v in versions:
            selected = v["version_id"] == self._selected_version_id
            row = ctk.CTkFrame(
                self.versions_body, fg_color=COL["row"], corner_radius=0,
                border_width=1 if selected else 0,
                border_color=COL["green"] if selected else COL["row"])
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=f"v{v['version_number']}", font=self.f_mono,
                         text_color=COL["muted2"], width=40, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=v["status_tag"], font=self.f_mono,
                         text_color=STATUS_COLOR.get(v["status_tag"], COL["text"]),
                         width=110, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=v["date_created"][:19], font=self.f_mono,
                         text_color=COL["muted2"], width=140, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=v["source_filename"], font=self.f_mono,
                         text_color=COL["text"], width=140, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=_short_hash(v["code_hash"]), font=self.f_mono,
                         text_color=COL["muted"], anchor="w").pack(side="left", padx=6)

            # Bind the click on the row AND every child label -- clicking
            # anywhere in the row (not just its background gaps) selects it.
            # Default-arg vid=... avoids the classic late-binding closure bug
            # (all rows would otherwise select whichever v the loop ended on).
            def select(_e, vid=v["version_id"]):
                self._selected_version_id = vid
                self._reload_versions()
            row.bind("<Button-1>", select)
            for child in row.winfo_children():
                child.bind("<Button-1>", select)

            self._version_rows.append(row)

    def _on_upload_version(self):
        # "Upload New Version" button -- picks a replacement file, then
        # delegates to the repository's dedup/versioning logic.
        path = filedialog.askopenfilename(
            title="Select updated bot strategy file",
            filetypes=[("Bot files", "*.py *.cs"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            # repo.add_version() applies the dedup rule itself (see
            # backend/repository.py) -- may be a genuine new version or a
            # silent no-op if the file is byte-identical to the current head.
            self.repo.add_version(self.bot["bot_id"], path)
        except BotVaultError as e:
            messagebox.showerror("Upload failed", str(e))
            return
        # A new head version changes what the main table's VERSION/STATUS
        # columns show for this bot, so App.py must reload after this
        # dialog closes.
        self.changed = True
        self._reload_versions()

    def _on_export_code(self):
        # "Export Code" button -- writes the selected version's blob back
        # out to a file the user picks, restoring its original filename.
        if self._selected_version_id is None:
            messagebox.showinfo("Export code", "Select a version first.")
            return
        try:
            # get_code() re-verifies the sha256 hash on every read -- an
            # IntegrityError here means the stored blob doesn't match what
            # was recorded at upload time.
            blob, source_filename = self.repo.get_code(self._selected_version_id)
        except BotVaultError as e:
            messagebox.showerror("Export failed", str(e))
            return
        # initialfile/defaultextension restore the original .py/.cs name so
        # the exported file is immediately usable, not a bare hash.
        dest = filedialog.asksaveasfilename(
            title="Export bot code", initialfile=source_filename,
            defaultextension=os.path.splitext(source_filename)[1])
        if not dest:
            return
        with open(dest, "wb") as f:
            f.write(blob)
        messagebox.showinfo("Export complete", f"Code exported to:\n{dest}")

    # ---- backtests ---------------------------------------------------

    def _reload_backtests(self):
        # Same destroy-and-rebuild + per-row click-to-select pattern as
        # _reload_versions above.
        for w in self._backtest_rows:
            w.destroy()
        self._backtest_rows.clear()

        backtests = self.repo.list_backtests(self.bot["bot_id"])
        for b in backtests:
            selected = b["backtest_id"] == self._selected_backtest_id
            row = ctk.CTkFrame(
                self.backtests_body, fg_color=COL["row"], corner_radius=0,
                border_width=1 if selected else 0,
                border_color=COL["green"] if selected else COL["row"])
            row.pack(fill="x", pady=1)
            period = f"{b['start_period'] or '?'} → {b['end_period'] or '?'}"
            ctk.CTkLabel(row, text=period, font=self.f_mono,
                         text_color=COL["muted2"], width=200, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=b.get("source_filename") or "—", font=self.f_mono,
                         text_color=COL["text"], width=140, anchor="w").pack(side="left", padx=6)
            ctk.CTkLabel(row, text=b["backtest_note"], font=self.f_mono,
                         text_color=COL["text"], anchor="w").pack(side="left", padx=6)

            def select(_e, bid=b["backtest_id"]):
                # Click-to-select a backtest row, same pattern as the
                # version list's row click handler above.
                self._selected_backtest_id = bid
                self._reload_backtests()
            row.bind("<Button-1>", select)
            for child in row.winfo_children():
                child.bind("<Button-1>", select)

            self._backtest_rows.append(row)

    def _on_add_backtest_clicked(self):
        # Pick the document FIRST, then reveal the metadata form -- if the
        # user cancels the file picker, nothing else happens (no empty form
        # left dangling open).
        path = filedialog.askopenfilename(title="Select backtest document")
        if not path:
            return
        self._pending_doc_path = path
        self._pending_doc_filename = os.path.basename(path)
        self._backtest_form.pack(fill="x", padx=14, pady=(4, 0))

    def _cancel_backtest_form(self):
        # Used both by the form's own Cancel button and by _on_save_backtest
        # on success, to reset all the pending state + hide the form again.
        self._pending_doc_path = None
        self._pending_doc_filename = None
        self._bt_start.delete(0, "end")
        self._bt_end.delete(0, "end")
        self._bt_note.delete(0, "end")
        self._backtest_form.pack_forget()

    def _on_save_backtest(self):
        # "Save" on the add-backtest form -- writes the doc + metadata row.
        path = getattr(self, "_pending_doc_path", None)
        if not path:
            # Defensive: Save shouldn't be reachable without a pending path,
            # but if it happens, just close the form instead of erroring.
            self._backtest_form.pack_forget()
            return
        with open(path, "rb") as f:
            doc_bytes = f.read()
        try:
            self.repo.create_backtest(
                self.bot["bot_id"], doc_bytes, self._pending_doc_filename,
                self._bt_start.get().strip(), self._bt_end.get().strip(),
                self._bt_note.get().strip())
        except BotVaultError as e:
            messagebox.showerror("Add backtest failed", str(e))
            return
        self._cancel_backtest_form()  # reset the form back to hidden
        self._reload_backtests()

    def _on_export_backtest(self):
        # "Export" button -- writes the selected backtest doc back to disk.
        if self._selected_backtest_id is None:
            messagebox.showinfo("Export backtest", "Select a backtest first.")
            return
        try:
            blob, source_filename = self.repo.get_backtest_doc(self._selected_backtest_id)
        except BotVaultError as e:
            messagebox.showerror("Export failed", str(e))
            return
        # Older rows created before source_filename existed (migrated DBs)
        # have an empty string here -- fall back to a generic name instead
        # of asksaveasfilename choking on a blank initialfile.
        initial = source_filename or "backtest_doc"
        dest = filedialog.asksaveasfilename(
            title="Export backtest document", initialfile=initial,
            defaultextension=os.path.splitext(initial)[1])
        if not dest:
            return
        with open(dest, "wb") as f:
            f.write(blob)
        messagebox.showinfo("Export complete", f"Backtest document exported to:\n{dest}")

    def _on_delete_backtest(self):
        # "Delete" button -- confirms, then removes the row + its LMDB blob.
        if self._selected_backtest_id is None:
            messagebox.showinfo("Delete backtest", "Select a backtest first.")
            return
        if not messagebox.askyesno("Delete backtest", "Remove this backtest document?"):
            return
        try:
            self.repo.delete_backtest(self._selected_backtest_id)
        except BotVaultError as e:
            messagebox.showerror("Delete failed", str(e))
            return
        self._selected_backtest_id = None
        self._reload_backtests()
        # Not marked self.changed -- backtests don't affect anything the
        # main table displays, so App.py has no reason to reload for this.

    # ---- close ---------------------------------------------------------

    def _on_close(self):
        # Bound to both the Close button and the titlebar X
        # (WM_DELETE_WINDOW) so wait_window() in App.py always unblocks.
        self.grab_release()
        self.destroy()
