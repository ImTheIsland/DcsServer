"""
DCS Lookup — CustomTkinter client.

Connects to the FastAPI server and lets users look up DCS codes
by typing a product description.

Run:
    python dcs_lookup/client/dcs_ui.py [--server http://HOST:PORT]
    python -m dcs_lookup.client.dcs_ui [--server http://HOST:PORT]

Server URL resolution order:
    1. --server command-line argument
    2. server_url in config.json next to this script (deployed location)
    3. server_url in config.json at the project root (dev mode)
    4. Default: http://localhost:8000
"""
import json
import sys
import threading
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import customtkinter as ctk
import requests

# --- Config ----------------------------------------------------------------
_DEFAULT_SERVER_URL = "http://localhost:8000"


def _resolve_server_url(cli_override: str | None = None) -> str:
    if cli_override:
        return cli_override.rstrip("/")

    if getattr(sys, "frozen", False):
        # Running as a PyInstaller bundle — config.json lives next to the .exe
        search_dirs = [Path(sys.executable).parent]
    else:
        # Running as a script — next to the script first, then project root (dev)
        search_dirs = [
            Path(__file__).resolve().parent,
            Path(__file__).resolve().parents[2],
        ]

    for directory in search_dirs:
        try:
            with open(directory / "config.json") as f:
                url = json.load(f).get("server_url")
                if url:
                    return url.rstrip("/")
        except (FileNotFoundError, ValueError, KeyError):
            continue

    return _DEFAULT_SERVER_URL


# Resolved at import time with no CLI override; __main__ may reinitialise.
_SERVER_URL = _resolve_server_url()


def _resolve_icon_path() -> str | None:
    if getattr(sys, "frozen", False):
        path = Path(sys.executable).parent / "DCS.ico"
    else:
        path = Path(__file__).resolve().parent / "DCS.ico"
    return str(path) if path.exists() else None


_ICON_PATH = _resolve_icon_path()

MAX_RESULTS = 8

# --- Appearance ------------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_FONT_TITLE  = ("Segoe UI", 18, "bold")
_FONT_LABEL  = ("Segoe UI", 12)
_FONT_DCS    = ("Segoe UI Mono", 12, "bold")
_FONT_DESC   = ("Segoe UI", 11)
_FONT_SCORE  = ("Segoe UI", 10)
_FONT_STATUS = ("Segoe UI", 11)

_ROW_COLORS     = ["gray20", "gray17"]
_DCS_COLOR      = "white"
_DCS_COLOR_SEL  = "#3ecf6e"


# ---------------------------------------------------------------------------

_SCOPE_ACTIVE_COLOR  = "white"
_SCOPE_HOVER_COLOR   = "#3ecf6e"
_SCOPE_INACTIVE_COLOR = "gray45"
_MAX_KW_DISPLAY = 20


class KeywordEditDialog(ctk.CTkToplevel):
    """
    Edit dialog for user_keywords, scoped to D / D+C / D+C+S.

    The DCS code is shown as three clickable segments in the header.
    Clicking D narrows the scope to D only; clicking C widens to D+C;
    the full D+C+S scope is the default.  Inactive (greyed) segments
    can be clicked to restore full scope down to that segment.

    Keywords are loaded from GET /user-keywords and displayed with ×
    delete buttons (DELETE /user-keywords/{id}).  New keywords are added
    via POST /user-keywords at the current scope.  The Save button
    commits all pending additions; changing scope without saving discards
    them.
    """

    def __init__(self, parent, dcs_code: str, d: str, c: str | None, s: str | None,
                 type_val: str | None, desc_val: str | None):
        super().__init__(parent)
        self.withdraw()
        self.title(f"Keywords — {dcs_code}")
        w, h = 400, 500
        px = parent.winfo_x() + (parent.winfo_width() - w) // 2
        py = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"{w}x{h}+{px}+{py}")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._dcs_code = dcs_code
        self._d = d
        self._c = c
        self._s = s
        self._type_val = type_val or ""
        self._desc_val = desc_val or ""

        # active scope: "d" | "dc" | "dcs"
        self._scope: str = "dcs" if s else ("dc" if c else "d")

        # keywords loaded from server: list of {"id": int, "keyword": str}
        self._loaded: list[dict] = []
        # pending additions (not yet saved): list of str
        self._pending: list[str] = []

        self._build_ui()
        self.deiconify()
        self._reload_keywords()

    # -------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- Header ---
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))

        # DCS scope selector row
        scope_row = ctk.CTkFrame(header, fg_color="transparent")
        scope_row.pack(anchor="w")

        self._lbl_d = ctk.CTkLabel(
            scope_row, text=self._d, font=_FONT_TITLE,
            text_color=_SCOPE_ACTIVE_COLOR, cursor="hand2",
        )
        self._lbl_d.pack(side="left")
        self._lbl_d.bind("<Enter>",  lambda e: self._on_seg_enter("d"))
        self._lbl_d.bind("<Leave>",  lambda e: self._on_seg_leave("d"))
        self._lbl_d.bind("<Button-1>", lambda e: self._on_seg_click("d"))

        if self._c:
            self._lbl_c = ctk.CTkLabel(
                scope_row, text=self._c, font=_FONT_TITLE,
                text_color=_SCOPE_ACTIVE_COLOR, cursor="hand2",
            )
            self._lbl_c.pack(side="left")
            self._lbl_c.bind("<Enter>",  lambda e: self._on_seg_enter("c"))
            self._lbl_c.bind("<Leave>",  lambda e: self._on_seg_leave("c"))
            self._lbl_c.bind("<Button-1>", lambda e: self._on_seg_click("c"))
        else:
            self._lbl_c = None

        if self._s:
            self._lbl_s = ctk.CTkLabel(
                scope_row, text=self._s, font=_FONT_TITLE,
                text_color=_SCOPE_ACTIVE_COLOR, cursor="hand2",
            )
            self._lbl_s.pack(side="left")
            self._lbl_s.bind("<Enter>",  lambda e: self._on_seg_enter("s"))
            self._lbl_s.bind("<Leave>",  lambda e: self._on_seg_leave("s"))
            self._lbl_s.bind("<Button-1>", lambda e: self._on_seg_click("s"))
        else:
            self._lbl_s = None

        # Type — Description subtitle
        type_desc = " — ".join(filter(None, [self._type_val, self._desc_val]))
        if type_desc:
            ctk.CTkLabel(
                header, text=type_desc, font=_FONT_DESC,
                text_color="gray", anchor="w",
            ).pack(anchor="w", pady=(2, 0))

        # --- Keyword list ---
        self._list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=4)
        self._list_frame.grid_columnconfigure(0, weight=1)

        # --- Add row ---
        add_frame = ctk.CTkFrame(self, fg_color="transparent")
        add_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 4))
        add_frame.grid_columnconfigure(0, weight=1)

        self._new_kw_entry = ctk.CTkEntry(
            add_frame, placeholder_text="Add keyword…", height=32, font=_FONT_LABEL
        )
        self._new_kw_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._new_kw_entry.bind("<Return>", self._on_add)

        ctk.CTkButton(
            add_frame, text="Add", width=60, height=32, command=self._on_add
        ).grid(row=0, column=1)

        # --- Footer ---
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=20, pady=(8, 16))
        footer.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            footer, text="Close", width=80,
            fg_color="gray35", hover_color="gray25",
            command=self.destroy,
        ).grid(row=0, column=0, sticky="w")

        self._status_label = ctk.CTkLabel(
            footer, text="", font=_FONT_STATUS, text_color="gray"
        )
        self._status_label.grid(row=0, column=1, padx=8)

        ctk.CTkButton(
            footer, text="Save", width=80, command=self._on_save
        ).grid(row=0, column=2, sticky="e")

        self._apply_scope_colors()

    # -------------------------------------------------------------------
    # Scope interaction
    # -------------------------------------------------------------------

    def _on_seg_enter(self, seg: str):
        """Hover: turn segment green — active or inactive, it's always clickable."""
        if seg == "d":
            self._lbl_d.configure(text_color=_SCOPE_HOVER_COLOR)
        elif seg == "c" and self._lbl_c:
            self._lbl_c.configure(text_color=_SCOPE_HOVER_COLOR)
        elif seg == "s" and self._lbl_s:
            self._lbl_s.configure(text_color=_SCOPE_HOVER_COLOR)

    def _on_seg_leave(self, seg: str):
        """Un-hover: restore the segment's current scope color."""
        if seg == "d":
            self._lbl_d.configure(text_color=_SCOPE_ACTIVE_COLOR)
        elif seg == "c" and self._lbl_c:
            color = _SCOPE_ACTIVE_COLOR if self._scope in ("dc", "dcs") else _SCOPE_INACTIVE_COLOR
            self._lbl_c.configure(text_color=color)
        elif seg == "s" and self._lbl_s:
            color = _SCOPE_ACTIVE_COLOR if self._scope == "dcs" else _SCOPE_INACTIVE_COLOR
            self._lbl_s.configure(text_color=color)

    def _on_seg_click(self, seg: str):
        """
        D click → scope = "d"  (C and S grey out)
        C click → scope = "dc" (S greys out; if scope was "d", expands to dc)
        S click → scope = "dcs" (restores full scope)
        """
        new_scope = {"d": "d", "c": "dc", "s": "dcs"}[seg]
        if new_scope == self._scope:
            return
        self._pending = []
        self._scope = new_scope
        self._apply_scope_colors()
        self._reload_keywords()

    def _apply_scope_colors(self):
        """Set label colours to reflect the active scope."""
        # D is always active (it's always part of any scope)
        self._lbl_d.configure(text_color=_SCOPE_ACTIVE_COLOR)

        if self._lbl_c:
            color = _SCOPE_ACTIVE_COLOR if self._scope in ("dc", "dcs") else _SCOPE_INACTIVE_COLOR
            self._lbl_c.configure(text_color=color)

        if self._lbl_s:
            color = _SCOPE_ACTIVE_COLOR if self._scope == "dcs" else _SCOPE_INACTIVE_COLOR
            self._lbl_s.configure(text_color=color)

    # -------------------------------------------------------------------
    # Data loading
    # -------------------------------------------------------------------

    def _reload_keywords(self):
        self._status_label.configure(text="Loading…", text_color="gray")
        threading.Thread(target=self._fetch_keywords, daemon=True).start()

    def _fetch_keywords(self):
        try:
            params: dict = {"d": self._d}
            if self._scope in ("dc", "dcs") and self._c:
                params["c"] = self._c
            if self._scope == "dcs" and self._s:
                params["s"] = self._s
            resp = requests.get(f"{_SERVER_URL}/user-keywords", params=params, timeout=5)
            resp.raise_for_status()
            rows = resp.json()  # list of {id, keyword, d, c, s, created_at}
            self.after(0, self._set_loaded, rows)
        except Exception as e:
            msg = f"Load error: {e}"
            self.after(0, lambda m=msg: self._status_label.configure(text=m, text_color="tomato"))

    def _set_loaded(self, rows: list[dict]):
        self._loaded = rows
        self._pending = []
        self._refresh_list()

    # -------------------------------------------------------------------
    # List rendering
    # -------------------------------------------------------------------

    def _refresh_list(self):
        for widget in self._list_frame.winfo_children():
            widget.destroy()

        display = self._loaded[:_MAX_KW_DISPLAY]
        overflow = len(self._loaded) - _MAX_KW_DISPLAY

        row_idx = 0

        # Saved keywords
        for item in display:
            self._render_saved_row(row_idx, item)
            row_idx += 1

        # Overflow notice
        if overflow > 0:
            ctk.CTkLabel(
                self._list_frame,
                text=f"… and {overflow} more keyword(s) not shown",
                font=_FONT_SCORE, text_color="gray",
            ).grid(row=row_idx, column=0, sticky="w", padx=10, pady=(2, 4))
            row_idx += 1

        # Pending (unsaved) additions
        for kw in self._pending:
            self._render_pending_row(row_idx, kw)
            row_idx += 1

        total = len(self._loaded) + len(self._pending)
        self._status_label.configure(
            text=f"{total} keyword(s)", text_color="gray"
        )

    def _render_saved_row(self, idx: int, item: dict):
        row = ctk.CTkFrame(
            self._list_frame, fg_color=_ROW_COLORS[idx % 2], corner_radius=4
        )
        row.grid(row=idx, column=0, sticky="ew", pady=1)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row, text=item["keyword"], font=_FONT_DESC, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=(10, 4), pady=5)

        ctk.CTkButton(
            row, text="×", width=26, height=26,
            fg_color="transparent", text_color="gray",
            hover_color="gray30",
            command=lambda i=item["id"]: self._on_delete(i),
        ).grid(row=0, column=1, padx=(4, 6), pady=4)

    def _render_pending_row(self, idx: int, kw: str):
        row = ctk.CTkFrame(
            self._list_frame, fg_color="gray25", corner_radius=4
        )
        row.grid(row=idx, column=0, sticky="ew", pady=1)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row, text=kw, font=_FONT_DESC, anchor="w", text_color="#aaaaaa"
        ).grid(row=0, column=0, sticky="w", padx=(10, 4), pady=5)

        ctk.CTkButton(
            row, text="×", width=26, height=26,
            fg_color="transparent", text_color="gray",
            hover_color="gray30",
            command=lambda k=kw: self._on_remove_pending(k),
        ).grid(row=0, column=1, padx=(4, 6), pady=4)

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    def _on_add(self, _event=None):
        kw = self._new_kw_entry.get().strip().lower()
        self._new_kw_entry.delete(0, "end")
        if not kw:
            return
        # Dedup against both loaded and pending
        existing_kws = {item["keyword"] for item in self._loaded} | set(self._pending)
        if kw in existing_kws:
            return
        self._pending.append(kw)
        self._refresh_list()

    def _on_remove_pending(self, keyword: str):
        self._pending = [k for k in self._pending if k != keyword]
        self._refresh_list()

    def _on_delete(self, kw_id: int):
        threading.Thread(target=self._delete_keyword, args=(kw_id,), daemon=True).start()

    def _delete_keyword(self, kw_id: int):
        try:
            resp = requests.delete(f"{_SERVER_URL}/user-keywords/{kw_id}", timeout=5)
            resp.raise_for_status()
            # Refresh from server after delete
            self.after(0, self._reload_keywords)
        except Exception as e:
            msg = f"Delete error: {e}"
            self.after(0, lambda m=msg: self._status_label.configure(text=m, text_color="tomato"))

    def _on_save(self):
        if not self._pending:
            self.destroy()
            return
        threading.Thread(target=self._save_pending, daemon=True).start()

    def _save_pending(self):
        c_val = self._c if self._scope in ("dc", "dcs") else None
        s_val = self._s if self._scope == "dcs" else None

        errors = []
        for kw in self._pending:
            body: dict = {"keyword": kw, "d": self._d}
            if c_val:
                body["c"] = c_val
            if s_val:
                body["s"] = s_val
            try:
                resp = requests.post(f"{_SERVER_URL}/user-keywords", json=body, timeout=5)
                resp.raise_for_status()
            except Exception as e:
                errors.append(str(e))

        if errors:
            msg = f"Save error: {errors[0]}"
            self.after(0, lambda m=msg: self._status_label.configure(text=m, text_color="tomato"))
        else:
            self.after(0, self.destroy)


# ---------------------------------------------------------------------------

class dcs_ui(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("DCS Lookup")
        self.geometry("500x500")
        self.resizable(False, False)
        if _ICON_PATH:
            self.after(0, lambda: self.iconbitmap(_ICON_PATH))
        self._selected_dcs: str | None = None
        self._selected_result: dict | None = None
        self._selected_label: ctk.CTkLabel | None = None
        self._last_search: tuple[str, str, str] = ("", "", "")
        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Title row — label left, Edit button right
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 8))
        title_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            title_frame, text="DCS Lookup", font=_FONT_TITLE, anchor="w"
        ).grid(row=0, column=0, sticky="w")

        self._edit_btn = ctk.CTkButton(
            title_frame,
            text="Edit User Keywords",
            width=60,
            height=28,
            command=self._on_edit,
        )
        self._edit_btn.grid(row=0, column=1, sticky="e")
        self._edit_btn.grid_remove()  # hidden until a row is selected

        # Input frame — description + vendor + person on one row
        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=1, column=0, sticky="ew", padx=20)
        input_frame.grid_columnconfigure(0, weight=1)

        row_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
        row_frame.grid(row=0, column=0, sticky="ew")
        row_frame.grid_columnconfigure(0, weight=1)

        self._item_entry = ctk.CTkEntry(
            row_frame,
            placeholder_text="Product description...",
            height=36,
            font=_FONT_LABEL,
        )
        self._item_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._item_entry.bind("<Return>", self._on_search)
        self._item_entry.bind("<FocusOut>", self._on_search)
        self.after(100, self._item_entry.focus)

        # Vendor code — 6 chars max, forced uppercase
        self._vendor_var = ctk.StringVar()
        self._vendor_var.trace_add("write", self._enforce_vendor_format)

        self._vendor_entry = ctk.CTkEntry(
            row_frame,
            textvariable=self._vendor_var,
            placeholder_text="Vendor",
            height=36,
            width=80,
            font=_FONT_DCS,
            justify="center",
        )
        self._vendor_entry.grid(row=0, column=1, sticky="e", padx=(0, 6))
        self._vendor_entry.bind("<Return>", self._on_search)
        self._vendor_entry.bind("<FocusOut>", self._on_search)

        # Person type — 1 char max, forced uppercase
        self._person_var = ctk.StringVar()
        self._person_var.trace_add("write", self._enforce_person_format)

        self._person_entry = ctk.CTkEntry(
            row_frame,
            textvariable=self._person_var,
            placeholder_text="P",
            height=36,
            width=36,
            font=_FONT_DCS,
            justify="center",
        )
        self._person_entry.grid(row=0, column=2, sticky="e")
        self._person_entry.bind("<Return>", self._on_search)
        self._person_entry.bind("<FocusOut>", self._on_search)

        # Status label
        self._status_var = ctk.StringVar()
        ctk.CTkLabel(
            self,
            textvariable=self._status_var,
            font=_FONT_STATUS,
            text_color="gray",
        ).grid(row=2, column=0, pady=(6, 2))

        # Results frame
        self._results_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._results_frame.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 16))
        self._results_frame.grid_columnconfigure(0, weight=1)

    # -----------------------------------------------------------------------

    def _enforce_vendor_format(self, *_):
        val = self._vendor_var.get()
        corrected = val.upper()[:6]
        if corrected != val:
            self._vendor_var.set(corrected)

    def _enforce_person_format(self, *_):
        val = self._person_var.get()
        corrected = val.upper()[:1]
        if corrected != val:
            self._person_var.set(corrected)

    # -----------------------------------------------------------------------

    def _on_search(self, _event=None):
        item_name = self._item_entry.get().strip()
        if not item_name:
            return
        vendor_code = self._vendor_var.get().strip() or None
        person = self._person_var.get().strip() or None

        current = (item_name, vendor_code or "", person or "")
        if current == self._last_search:
            return
        self._last_search = current

        self._status_var.set("Searching…")
        self._clear_results()
        threading.Thread(
            target=self._fetch, args=(item_name, vendor_code, person), daemon=True
        ).start()

    def _fetch(self, item_name: str, vendor_code: str | None, person: str | None):
        try:
            params = {"item_name": item_name}
            if vendor_code:
                params["vendor_code"] = vendor_code
            if person:
                params["person"] = person
            resp = requests.get(
                f"{_SERVER_URL}/dcs/possible", params=params, timeout=5
            )
            resp.raise_for_status()
            results = resp.json()[:MAX_RESULTS]
            self.after(0, self._display_results, results)
        except requests.ConnectionError:
            self.after(0, self._status_var.set, f"Cannot connect to {_SERVER_URL}")
        except Exception as e:
            self.after(0, self._status_var.set, f"Error: {e}")

    # -----------------------------------------------------------------------

    def _on_row_click(self, dcs_code: str, result: dict, dcs_label: ctk.CTkLabel, _event=None):
        # Reset previous selection
        if self._selected_label is not None:
            self._selected_label.configure(text_color=_DCS_COLOR)

        # Highlight new selection
        dcs_label.configure(text_color=_DCS_COLOR_SEL)
        self._selected_label = dcs_label
        self._selected_dcs = dcs_code
        self._selected_result = result

        # Copy to clipboard
        self.clipboard_clear()
        self.clipboard_append(dcs_code)

        # Show Edit button
        self._edit_btn.grid()

    def _on_edit(self):
        if self._selected_dcs and self._selected_result:
            r = self._selected_result
            KeywordEditDialog(
                self,
                self._selected_dcs,
                r.get("d", self._selected_dcs[:3]),
                r.get("c"),
                r.get("s"),
                r.get("type"),
                r.get("description"),
            )

    # -----------------------------------------------------------------------

    def _clear_results(self):
        self._selected_dcs = None
        self._selected_label = None
        self._last_search = ("", "", "")
        self._edit_btn.grid_remove()
        for widget in self._results_frame.winfo_children():
            widget.destroy()

    def _display_results(self, results: list):
        self._clear_results()
        if not results:
            self._status_var.set("No results found.")
            return

        self._status_var.set(f"{len(results)} result(s)")

        for i, r in enumerate(results):
            dcs_code = r.get("dcs", "")
            row = ctk.CTkFrame(
                self._results_frame, fg_color=_ROW_COLORS[i % 2], corner_radius=4
            )
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)

            # DCS code label — turns green on selection
            dcs_label = ctk.CTkLabel(
                row,
                text=dcs_code,
                font=_FONT_DCS,
                text_color=_DCS_COLOR,
                width=90,
                anchor="w",
            )
            dcs_label.grid(row=0, column=0, padx=(10, 6), pady=6, sticky="w")

            # Type — Description
            type_val = r.get("type") or ""
            desc_val = r.get("description") or ""
            desc_text = " — ".join(filter(None, [type_val, desc_val])) or "—"
            ctk.CTkLabel(
                row,
                text=desc_text,
                font=_FONT_DESC,
                anchor="w",
                wraplength=260,
            ).grid(row=0, column=1, padx=4, pady=6, sticky="w")

            # Right side: vendor match star + score
            right = ctk.CTkFrame(row, fg_color="transparent")
            right.grid(row=0, column=2, padx=(4, 10), pady=6, sticky="e")

            if r.get("vendor_match"):
                ctk.CTkLabel(
                    right, text="★", font=_FONT_LABEL, text_color="#4a9eff"
                ).pack(side="left")

            score = r.get("combined_score")
            if score is not None:
                ctk.CTkLabel(
                    right,
                    text=f"{score:.0f}",
                    font=_FONT_SCORE,
                    text_color="gray",
                    width=28,
                    anchor="e",
                ).pack(side="left")

            # Bind click on row and all children
            handler = lambda e, code=dcs_code, res=r, lbl=dcs_label: self._on_row_click(code, res, lbl, e)
            for widget in [row, dcs_label] + list(row.winfo_children()):
                widget.bind("<Button-1>", handler)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DCS Lookup UI")
    parser.add_argument(
        "--server",
        metavar="URL",
        help="Server base URL, e.g. http://192.168.1.10:8000",
    )
    args = parser.parse_args()

    # Reinitialise with the CLI override (if any) before the window opens.
    _SERVER_URL = _resolve_server_url(args.server)

    dcs_ui().mainloop()
