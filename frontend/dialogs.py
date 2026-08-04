"""Small modal dialogs used by App.py.

Kept in its own module (rather than folded into App.py) so the main window
file stays focused on the table/toolbar/statusbar it already owned, per the
project's modularization goal. Dialogs read fonts off `parent` (a live
BotVault instance) rather than constructing their own CTkFont objects --
CTkFont can't be built before a CTk() root exists, so it can't safely live
as a module-level singleton shared between modules.
"""

import os

import customtkinter as ctk

from theme import COL

NEW_STRATEGY_SENTINEL = "+ New Strategy..."


class AddBotDialog(ctk.CTkToplevel):
    """Collects a bot name + strategy after the caller has already picked a
    file. `.result` is set just before the window closes:
      - None                                            -> cancelled
      - {"bot_name": str, "strategy_id": int}             -> existing strategy
      - {"bot_name": str, "new_strategy": {...}}           -> create + use a
                                                             brand new strategy
    """

    def __init__(self, parent, file_path: str, strategies: list[dict]):
        # Builds the modal's widgets, then grabs input focus at the end.
        super().__init__(parent)
        self.result = None
        self._strategies = strategies
        self._file_path = file_path

        self.title("Add Bot")
        self.configure(fg_color=COL["bg"])
        self.resizable(False, False)
        self.transient(parent)

        f_mono = parent.f_mono
        f_small = parent.f_small
        pad = {"padx": 14, "pady": (10, 0)}

        ctk.CTkLabel(self, text="Add Bot", font=parent.f_title,
                     text_color=COL["green"]).pack(anchor="w", padx=14, pady=(14, 4))

        # --- file (read-only) ---
        ctk.CTkLabel(self, text="File:", font=f_small,
                     text_color=COL["muted"]).pack(anchor="w", **pad)
        file_entry = ctk.CTkEntry(self, font=f_mono, width=320,
                                   fg_color=COL["panel"], border_color=COL["border"],
                                   text_color=COL["muted2"])
        file_entry.insert(0, os.path.basename(file_path))
        file_entry.configure(state="disabled")
        file_entry.pack(anchor="w", padx=14, pady=(2, 0))

        # --- bot name ---
        ctk.CTkLabel(self, text="Bot name:", font=f_small,
                     text_color=COL["muted"]).pack(anchor="w", **pad)
        self.name_entry = ctk.CTkEntry(self, font=f_mono, width=320,
                                        fg_color=COL["panel"], border_color=COL["border"],
                                        text_color=COL["text"])
        default_name = os.path.splitext(os.path.basename(file_path))[0]
        self.name_entry.insert(0, default_name)
        self.name_entry.pack(anchor="w", padx=14, pady=(2, 0))

        # --- strategy ---
        ctk.CTkLabel(self, text="Strategy:", font=f_small,
                     text_color=COL["muted"]).pack(anchor="w", **pad)
        values = [s["strategy_name"] for s in strategies] + [NEW_STRATEGY_SENTINEL]
        self.strategy_menu = ctk.CTkOptionMenu(
            self, values=values, font=f_mono, width=320, height=28,
            fg_color=COL["panel"], button_color=COL["border"],
            button_hover_color=COL["hover"], text_color=COL["text"],
            dropdown_fg_color=COL["panel"], dropdown_hover_color=COL["hover"],
            dropdown_text_color=COL["text"],
            command=self._on_strategy_change)
        # Default-select the first real strategy so the common path never
        # needs the dropdown touched at all (keeps the upload flow short).
        self.strategy_menu.set(values[0])
        self.strategy_menu.pack(anchor="w", padx=14, pady=(2, 0))

        # --- inline "new strategy" sub-form, hidden unless selected ---
        self._new_strategy_frame = ctk.CTkFrame(self, fg_color=COL["bg"])
        self._ns_name = ctk.CTkEntry(
            self._new_strategy_frame, placeholder_text="strategy name",
            font=f_mono, width=320, fg_color=COL["panel"],
            border_color=COL["border"], text_color=COL["text"])
        self._ns_name.pack(anchor="w", pady=(4, 4))
        self._ns_market = ctk.CTkEntry(
            self._new_strategy_frame, placeholder_text="market type (optional)",
            font=f_mono, width=320, fg_color=COL["panel"],
            border_color=COL["border"], text_color=COL["text"])
        self._ns_market.pack(anchor="w", pady=(0, 4))
        self._ns_desc = ctk.CTkEntry(
            self._new_strategy_frame, placeholder_text="description (optional)",
            font=f_mono, width=320, fg_color=COL["panel"],
            border_color=COL["border"], text_color=COL["text"])
        self._ns_desc.pack(anchor="w")
        self._new_strategy_frame.pack(anchor="w", padx=14, pady=(0, 0))
        self._new_strategy_frame.pack_forget()  # hidden until sentinel chosen
        if values[0] == NEW_STRATEGY_SENTINEL:
            # No strategies exist yet (shouldn't normally happen -- the repo
            # seeds defaults on first run) -- reveal the sub-form up front
            # since .set() above doesn't fire the `command` callback.
            self._on_strategy_change(values[0])

        # --- inline error slot ---
        self._error_label = ctk.CTkLabel(self, text="", font=f_small,
                                          text_color=COL["red"])
        self._error_label.pack(anchor="w", padx=14, pady=(6, 0))

        # --- buttons ---
        btns = ctk.CTkFrame(self, fg_color=COL["bg"])
        btns.pack(fill="x", padx=14, pady=14)
        ctk.CTkButton(btns, text="Cancel", font=f_mono, width=90, height=30,
                      fg_color=COL["panel"], border_width=1, border_color=COL["border"],
                      hover_color=COL["hover"], text_color=COL["text"],
                      command=self._on_cancel).pack(side="right")
        ctk.CTkButton(btns, text="Add", font=f_mono, width=90, height=30,
                      fg_color=COL["green_dk"], hover_color=COL["green"],
                      text_color="#0B0E0C", command=self._on_add).pack(
            side="right", padx=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Return>", lambda e: self._on_add())

        # Modal setup must happen in this order, after the window has real
        # content and geometry, or grab_set() / centering use stale sizes.
        self.update_idletasks()
        self._center_over(parent)
        self.grab_set()
        self.focus_force()
        self.name_entry.focus_set()
        self.name_entry.select_range(0, "end")

    def _center_over(self, parent):
        # Requires update_idletasks() on both windows first, or
        # winfo_width()/winfo_height() return stale 1x1 placeholder sizes
        # instead of the real, just-built layout.
        parent.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _on_strategy_change(self, value: str):
        # Fired by the CTkOptionMenu's `command` whenever the user actually
        # picks something. Reveal/hide the inline new-strategy fields to
        # match, without needing a separate "edit mode" toggle.
        if value == NEW_STRATEGY_SENTINEL:
            self._new_strategy_frame.pack(anchor="w", padx=14, pady=(0, 0))
        else:
            self._new_strategy_frame.pack_forget()
        self.update_idletasks()

    def _show_error(self, message: str):
        # Inline label, not a blocking messagebox -- so a validation retry
        # doesn't cost the user an extra "OK" click every time.
        self._error_label.configure(text=message)

    def _on_cancel(self):
        # Bound to both the Cancel button and the titlebar close button
        # (WM_DELETE_WINDOW) -- either way .result stays None so the caller
        # in App.add_bot() knows to treat this as "nothing happened".
        self.result = None
        self.grab_release()
        self.destroy()

    def _on_add(self):
        # Validate bot name first regardless of which strategy path is taken.
        bot_name = self.name_entry.get().strip()
        if not bot_name:
            self._show_error("Bot name cannot be blank.")
            self.name_entry.focus_set()
            return

        chosen = self.strategy_menu.get()
        if chosen == NEW_STRATEGY_SENTINEL:
            # Creating a brand new strategy inline -- validate its name here
            # (blank + case-insensitive duplicate check against the already-
            # loaded list) so the common failure cases don't need a round
            # trip through the repository's own UNIQUE-constraint check.
            new_name = self._ns_name.get().strip()
            if not new_name:
                self._show_error("New strategy name cannot be blank.")
                self._ns_name.focus_set()
                return
            existing_names = {s["strategy_name"].lower() for s in self._strategies}
            if new_name.lower() in existing_names:
                self._show_error(f"Strategy '{new_name}' already exists.")
                self._ns_name.focus_set()
                return
            self.result = {
                "bot_name": bot_name,
                "new_strategy": {
                    "strategy_name": new_name,
                    "market_type": self._ns_market.get().strip(),
                    "strategy_description": self._ns_desc.get().strip(),
                },
            }
        else:
            # Existing strategy: look up its id from the list we were
            # constructed with (already fetched once, no need to re-query).
            strategy_id = next(
                s["strategy_id"] for s in self._strategies if s["strategy_name"] == chosen
            )
            self.result = {"bot_name": bot_name, "strategy_id": strategy_id}

        self.grab_release()
        self.destroy()
