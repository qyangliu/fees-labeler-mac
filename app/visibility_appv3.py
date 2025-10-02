import sys
import platform
import tkinter as tk
from tkinter import messagebox, filedialog
from PIL import Image, ImageTk
import os
import csv
import re
from glob import glob
import pandas as pd
import shutil
import datetime
from pathlib import Path

# ========== CONFIGURATION ==========
APP_NAME = "FEES-Labeler"

def resource_dir() -> Path:
    """
    Returns a directory suitable for bundled resources.
    For read/write user resources (like preload.csv), use:
      - macOS:  ~/Library/Application Support/FEES-Labeler
      - Windows: %APPDATA%\\FEES-Labeler
      - Linux:  ~/.local/share/FEES-Labeler
    """
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    elif platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base

# Default preload.csv in per-user app data; copied from bundle on first run if present
DEFAULT_PRELOAD = resource_dir() / "preload.csv"

def ensure_preload_csv() -> Path:
    """
    Ensure a preload.csv exists in the per-user resource dir.
    If not, try copying one bundled alongside the executable/script.
    Return the (possibly non-existent) target path either way.
    """
    if DEFAULT_PRELOAD.exists():
        return DEFAULT_PRELOAD
    try:
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        bundled = bundle_root / "preload.csv"
        if bundled.exists():
            shutil.copy2(bundled, DEFAULT_PRELOAD)
            return DEFAULT_PRELOAD
    except Exception:
        pass
    return DEFAULT_PRELOAD

PRELOAD_CSV = str(ensure_preload_csv())

IMAGE_SIZE = (600, 600)
STRUCTURE_COLUMNS = [
    "LPW_PPW", "B", "VT", "LC_LP", "RC_RP", "PCR",
    "LA_LAF", "RA_RAF", "IAS", "LSE", "LSAF_FVF", "AC_TVF_PC"
]
HOTKEYS = ['1','2','3','4','5','6','7','8','9','0','-','=']  # length must match STRUCTURE_COLUMNS
# ===================================

def ensure_dir(path: str):
    """Create parent directories for a file path if they don't exist."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

def parse_metadata(path: str):
    """Extract (video, swallow, frame) from a path like .../videoN/swallowM/frame_####.png"""
    normalized = path.replace("\\", "/")
    match = re.search(r"video(\d+)/swallow(\d+)/.*?frame_(\d+)\.(?:png|jpg|jpeg)$",
                      normalized, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return -1, -1, -1

# Load swallow-level defaults (tolerant if no preload present)
preload_df = None
if os.path.exists(PRELOAD_CSV):
    try:
        _raw_df = pd.read_csv(PRELOAD_CSV)
        _raw_df = _raw_df.rename(columns={
            "video ID": "video",
            "swallow number": "swallow",
            "LPW-PPW": "LPW_PPW",
            "V-T": "VT",
            "LC-LP": "LC_LP",
            "RC-RP": "RC_RP",
            "LA-LAF": "LA_LAF",
            "RA-RAF": "RA_RAF",
            "LSAF-FVF": "LSAF_FVF",
            "AC-TVF-PC": "AC_TVF_PC"
        })
        _raw_df["video"] = _raw_df["video"].astype(int)
        _raw_df["swallow"] = _raw_df["swallow"].astype(int)
        preload_df = _raw_df[["video", "swallow"] + STRUCTURE_COLUMNS].copy()
    except Exception:
        preload_df = None


class LabelApp:
    def __init__(self, root):
        self.root = root
        self.root.title("FEES-AI Frame Labeler (val+overridden + range)")

        # State that depends on the opened folder
        self.image_root = None
        self.image_paths = []
        self.output_csv = None

        self.index = 0
        self.total = 0
        self.checkbox_vars = {s: tk.IntVar() for s in STRUCTURE_COLUMNS}
        self.frame_states = {}  # (video, swallow, frame) -> checkbox list (1=checked/visible)
        self.checkbuttons = {}  # key to structure
        self.range_min = 0
        self.range_max = 0

        # --- preload CSV state (start with whatever was loaded globally) ---
        self.preload_csv = PRELOAD_CSV if os.path.exists(PRELOAD_CSV) else None
        self.preload_df = preload_df

        # Menu bar
        menubar = tk.Menu(root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open Folder...", command=self.open_folder)
        filemenu.add_command(label="Select Preload CSV...", command=self.select_preload_csv)  # preload picker
        filemenu.add_separator()
        filemenu.add_command(label="Save", command=self.persist_all)
        filemenu.add_command(label="Exit", command=root.quit)
        menubar.add_cascade(label="File", menu=filemenu)
        root.config(menu=menubar)

        # UI layout
        self.image_label = tk.Label(root)
        self.image_label.pack(side="left", padx=10, pady=10)

        right_panel = tk.Frame(root)
        right_panel.pack(side="right", fill="y", padx=10)

        tk.Label(
            right_panel,
            text="Hotkeys: ←/→ prev/next; 1–0 - = toggle. ✓=visible, ☐=not. _overridden shows deviation from swallow default.",
            font=("Helvetica", 12),
            wraplength=320,
            justify="left"
        ).pack(pady=(0,5))

        # checkboxes with hotkeys
        for key, s in zip(HOTKEYS, STRUCTURE_COLUMNS):
            cb = tk.Checkbutton(
                right_panel,
                text=f"[{key}] {s}",
                variable=self.checkbox_vars[s],
                font=("Helvetica", 14),
                anchor="w",
                padx=10
            )
            cb.pack(anchor="w")
            self.checkbuttons[key] = s

        # All/None
        ctrl_frame = tk.Frame(right_panel)
        ctrl_frame.pack(pady=5)
        tk.Button(ctrl_frame, text="All", command=self.check_all, width=6).grid(row=0, column=0, padx=5)
        tk.Button(ctrl_frame, text="None", command=self.uncheck_all, width=6).grid(row=0, column=1, padx=5)

        # Navigation
        nav_frame = tk.Frame(right_panel)
        nav_frame.pack(pady=8)
        tk.Button(nav_frame, text="← Previous", command=self.prev_image).grid(row=0, column=0, padx=5)
        tk.Button(nav_frame, text="Next →", command=self.save_and_next, bg="lightblue").grid(row=0, column=1, padx=5)

        # Skip-to and range
        skip_frame = tk.Frame(right_panel)
        skip_frame.pack(pady=5)
        tk.Label(skip_frame, text="Go to #").grid(row=0, column=0)
        self.skip_entry = tk.Entry(skip_frame, width=6)
        self.skip_entry.grid(row=0, column=1)
        tk.Button(skip_frame, text="Go", command=self.skip_to_index).grid(row=0, column=2)
        self.skip_entry.bind("<Return>", lambda e: (self.skip_to_index(), self.root.focus()))

        range_frame = tk.Frame(right_panel)
        range_frame.pack(pady=5)
        tk.Label(range_frame, text="Limit range:").grid(row=0, column=0, padx=2)
        self.range_start = tk.Entry(range_frame, width=4)
        self.range_start.grid(row=0, column=1, padx=2)
        tk.Label(range_frame, text="to").grid(row=0, column=2)
        self.range_end = tk.Entry(range_frame, width=4)
        self.range_end.grid(row=0, column=3, padx=2)
        tk.Button(range_frame, text="Apply", command=self.apply_range).grid(row=0, column=4, padx=5)
        tk.Button(range_frame, text="Clear", command=self.clear_range).grid(row=0, column=5, padx=2)

        self.status_label = tk.Label(right_panel, text="", font=("Helvetica", 12))
        self.status_label.pack(pady=10)

        # clicking outside skip entry clears its focus
        def clear_focus(event):
            if event.widget in (self.skip_entry, self.range_start, self.range_end):
                return
            try:
                self.skip_entry.selection_clear()
            except Exception:
                pass
            self.root.focus()
        root.bind_all("<Button-1>", clear_focus, add="+")

        # Key bindings
        root.bind("<Left>", lambda e: self.prev_image())
        root.bind("<Right>", lambda e: self.save_and_next())
        for key in HOTKEYS:
            root.bind(key, self._hotkey_toggle)
            root.bind(key.upper(), self._hotkey_toggle)

        # Prompt for preload if missing
        if self.preload_df is None:
            resp = messagebox.askyesno("Select preload CSV?",
                                       "No preload.csv was found. Do you want to select one now?")
            if resp:
                self.select_preload_csv()

        # Prompt for folder on startup
        self.open_folder(initial_prompt=True)

    # -------- Folder handling --------
    def open_folder(self, initial_prompt=False):
        title = "Select the dataset root folder (contains video*/swallow*/frame_*.png)" if initial_prompt else "Open Folder"
        folder = filedialog.askdirectory(title=title)
        if not folder:
            if initial_prompt:
                messagebox.showinfo("No folder selected", "No folder chosen. Exiting.")
                self.root.quit()
            return

        # Gather images
        paths = sorted(glob(os.path.join(folder, "video*", "swallow*", "*frame_*.[Pp][Nn][Gg]")))
        if len(paths) == 0:
            messagebox.showerror("No images found",
                                 "No images matching video*/swallow*/frame_*.png were found in this folder.")
            if initial_prompt:
                self.root.quit()
            return

        # Reset state
        self.image_root = folder
        self.image_paths = paths
        self.output_csv = os.path.join(self.image_root, "frame_labels.csv")
        self.index = 0
        self.total = len(self.image_paths)
        self.range_min = 0
        self.range_max = self.total - 1
        self.frame_states = {}

        # Prepare CSV (backup if exists, then load any existing labels)
        ensure_dir(self.output_csv)
        if os.path.exists(self.output_csv):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.output_csv + f".bak.{timestamp}"
            try:
                shutil.copy2(self.output_csv, backup_path)
                print(f"[INFO] backed up existing CSV to {backup_path}")
            except Exception as e:
                print(f"[WARN] backup failed: {e}")
            self._load_existing_csv(self.output_csv)

        # Show first image
        self.load_image()

    def _load_existing_csv(self, csv_path):
        try:
            with open(csv_path, newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    try:
                        video = int(row[0]);
                        swallow = int(row[1]);
                        frame = int(row[2])
                    except ValueError:
                        continue
                    key = (video, swallow, frame)

                    checkbox_state = []
                    if header:
                        # NEW schema: *_sev, *_vis
                        if any(f"{s}_vis" in header for s in STRUCTURE_COLUMNS):
                            for s in STRUCTURE_COLUMNS:
                                vis_idx = header.index(f"{s}_vis")
                                # compute base index for this structure
                                base = 3 + 2 * STRUCTURE_COLUMNS.index(s)
                                sev = int(row[base])  # not used here
                                vis = int(row[base + 1])  # *_vis
                                checkbox_state.append(1 if vis == 1 else 0)

                        # OLD schema: *_val (was checkbox), *_overridden (mismatch)
                        elif any(f"{s}_overridden" in header for s in STRUCTURE_COLUMNS):
                            ptr = 3
                            for _ in STRUCTURE_COLUMNS:
                                vis = int(row[ptr])  # *_val used to be visibility
                                checkbox_state.append(1 if vis == 1 else 0)
                                ptr += 2

                        # VERY OLD schema: one column per structure (visibility only)
                        else:
                            for i, _s in enumerate(STRUCTURE_COLUMNS):
                                try:
                                    vis = int(row[3 + i])
                                except Exception:
                                    vis = 0
                                checkbox_state.append(1 if vis == 1 else 0)
                    self.frame_states[key] = checkbox_state
        except Exception as e:
            messagebox.showwarning("CSV load warning", f"Could not load existing CSV:\n{e}")

    # -------- Defaults / range helpers --------
    def get_swallow_defaults(self, video, swallow):
        if isinstance(self.preload_df, pd.DataFrame):
            row = self.preload_df[(self.preload_df["video"] == video) & (self.preload_df["swallow"] == swallow)]
            if not row.empty:
                return row.iloc[0][STRUCTURE_COLUMNS].tolist()
        # No defaults found
        return [0] * len(STRUCTURE_COLUMNS)

    def in_range_index(self, idx):
        return self.range_min <= idx <= self.range_max

    def _load_preload_csv(self, path):
        try:
            raw_df = pd.read_csv(path)
            raw_df = raw_df.rename(columns={
                "video ID": "video",
                "swallow number": "swallow",
                "LPW-PPW": "LPW_PPW",
                "V-T": "VT",
                "LC-LP": "LC_LP",
                "RC-RP": "RC_RP",
                "LA-LAF": "LA_LAF",
                "RA-RAF": "RA_RAF",
                "LSAF-FVF": "LSAF_FVF",
                "AC-TVF-PC": "AC_TVF_PC"
            })
            raw_df["video"] = raw_df["video"].astype(int)
            raw_df["swallow"] = raw_df["swallow"].astype(int)
            self.preload_df = raw_df[["video", "swallow"] + STRUCTURE_COLUMNS].copy()
            self.preload_csv = path

            # Only refresh the status if images are already loaded
            if getattr(self, "total", 0) > 0 and 0 <= getattr(self, "index", 0) < self.total:
                v, s, f = parse_metadata(self.image_paths[self.index])
                try:
                    self._set_status(v, s, f)  # if present in file; ignore if not
                except AttributeError:
                    self.status_label.config(
                        text=f"{self.index + 1}/{self.total} — video {v}, swallow {s}, frame {f}\n"
                             f"Folder: {self.image_root}\nPreload: {os.path.basename(self.preload_csv)}"
                    )

        except Exception as e:
            messagebox.showerror("Preload CSV error", f"Failed to load preload CSV:\n{e}")

    def select_preload_csv(self):
        initdir = os.path.dirname(self.preload_csv) if self.preload_csv else None
        path = filedialog.askopenfilename(
            title="Select preload CSV",
            initialdir=initdir,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self._load_preload_csv(path)

    def apply_range(self):
        try:
            start = int(self.range_start.get()) - 1
            end = int(self.range_end.get()) - 1
            if not (0 <= start <= end < self.total):
                messagebox.showerror("Invalid", "Range out of bounds.")
                return
            self.range_min = start
            self.range_max = end
            if self.index < self.range_min:
                self.index = self.range_min
            if self.index > self.range_max:
                self.index = self.range_max
            self.load_image()
        except ValueError:
            messagebox.showerror("Invalid", "Enter integers for range.")

    def clear_range(self):
        self.range_min = 0
        self.range_max = self.total - 1
        self.range_start.delete(0, tk.END)
        self.range_end.delete(0, tk.END)
        self.load_image()

    # -------- Image + UI logic --------
    def load_image(self):
        if self.total == 0:
            return
        if self.index >= self.total:
            messagebox.showinfo("Done", "All images labeled.")
            self.root.quit()
            return

        # enforce range boundaries
        if not self.in_range_index(self.index):
            self.index = max(self.range_min, min(self.index, self.range_max))

        path = self.image_paths[self.index]
        video, swallow, frame = parse_metadata(path)

        img = Image.open(path).resize(IMAGE_SIZE)
        self.tk_img = ImageTk.PhotoImage(img)
        self.image_label.config(image=self.tk_img)

        self.status_label.config(
            text=f"{self.index+1}/{self.total} — video {video}, swallow {swallow}, frame {frame}\nFolder: {self.image_root}"
        )

        key = (video, swallow, frame)
        if key in self.frame_states:
            checkbox_state = self.frame_states[key]
        elif self.index > 0:
            prev_video, prev_swallow, prev_frame = parse_metadata(self.image_paths[self.index - 1])
            prev_key = (prev_video, prev_swallow, prev_frame)
            checkbox_state = self.frame_states.get(prev_key, [0]*len(STRUCTURE_COLUMNS))
        else:
            checkbox_state = [0]*len(STRUCTURE_COLUMNS)

        for i, s in enumerate(STRUCTURE_COLUMNS):
            self.checkbox_vars[s].set(checkbox_state[i])

    def _hotkey_toggle(self, event):
        focused = self.root.focus_get()
        if isinstance(focused, tk.Entry):
            return  # typing in an entry (goto or range) should not toggle boxes

        key = event.keysym
        mapping = {
            'minus': '-', 'equal': '=', '0': '0', '1': '1', '2': '2', '3': '3', '4': '4',
            '5': '5', '6': '6', '7': '7', '8': '8', '9': '9'
        }
        hot = mapping.get(key, None)
        if hot and hot in self.checkbuttons:
            struct = self.checkbuttons[hot]
            current = self.checkbox_vars[struct].get()
            self.checkbox_vars[struct].set(0 if current == 1 else 1)

    def get_current_values(self):
        path = self.image_paths[self.index]
        video, swallow, frame = parse_metadata(path)
        defaults = self.get_swallow_defaults(video, swallow)

        vis_vals = []
        overridden_flags = []
        checkbox_state = []

        for i, s in enumerate(STRUCTURE_COLUMNS):
            checked = self.checkbox_vars[s].get()
            vis = 1 if checked == 1 else 0
            checkbox_state.append(checked)
            vis_vals.append(vis)
            overridden = 1 if vis != defaults[i] else 0
            overridden_flags.append(overridden)

        self.frame_states[(video, swallow, frame)] = checkbox_state
        return video, swallow, frame, vis_vals, overridden_flags

    def persist_all(self):
        if not self.output_csv:
            return
        header = ["video", "swallow", "frame"]
        # write severity (from preload) + visibility (from checkbox)
        for s in STRUCTURE_COLUMNS:
            header += [f"{s}_sev", f"{s}_vis"]

        ensure_dir(self.output_csv)
        with open(self.output_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for (video, swallow, frame), checkbox_state in sorted(self.frame_states.items()):
                defaults = self.get_swallow_defaults(video, swallow)  # severities 0–4
                row = [video, swallow, frame]
                for idx, s in enumerate(STRUCTURE_COLUMNS):
                    sev = int(defaults[idx]) if pd.notna(defaults[idx]) else 0
                    vis = 1 if checkbox_state[idx] == 1 else 0
                    row.append(sev)  # *_sev
                    row.append(vis)  # *_vis
                writer.writerow(row)

    def save_and_next(self):
        if self.total == 0:
            return
        self.get_current_values()
        self.persist_all()
        next_idx = self.index + 1
        if next_idx > self.range_max:
            return
        self.index = next_idx
        self.load_image()

    def prev_image(self):
        if self.total == 0:
            return
        if self.index <= self.range_min:
            return
        self.get_current_values()
        self.persist_all()
        prev_idx = self.index - 1
        if prev_idx < self.range_min:
            return
        self.index = prev_idx
        self.load_image()

    def skip_to_index(self):
        try:
            val = int(self.skip_entry.get()) - 1
            if val < 0 or val >= self.total:
                messagebox.showerror("Invalid", "Out of bounds.")
                return
            if not self.in_range_index(val):
                messagebox.showerror("Invalid", f"Must be between {self.range_min+1} and {self.range_max+1}")
                return
            self.index = val
            self.load_image()
        except ValueError:
            messagebox.showerror("Invalid", "Enter a number.")

    def check_all(self):
        for var in self.checkbox_vars.values():
            var.set(1)

    def uncheck_all(self):
        for var in self.checkbox_vars.values():
            var.set(0)


if __name__ == "__main__":
    # Optional: improve retina scaling on macOS; harmless elsewhere if ignored
    if platform.system() == "Darwin":
        try:
            # This scaling value can be tuned; 2.0 is often right for retina
            # Comment out if it causes issues on specific setups.
            # Will raise if Tk not yet initialized on some versions.
            pass
        except Exception:
            pass

    root = tk.Tk()
    # Silence the legacy Tk warning on macOS if desired:
    os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
    app = LabelApp(root)
    root.mainloop()
