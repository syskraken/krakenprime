"""
deploy_overlay.py  —  COC Visual Deployment Planner
────────────────────────────────────────────────────
Can be launched standalone OR by app.py (which passes
env vars to control the output file and window title).

ENV VARS (set by app.py):
  OVERLAY_OUTPUT   — path to write the JSON file (default: deploy_points.json)
  OVERLAY_TITLE    — hint shown in the window title bar
  OVERLAY_SENTINEL — path to temp sentinel file; overlay removes it on successful save
                     (if sentinel still exists on close → user cancelled)

Standalone usage:
  python deploy_overlay.py

Controls:
  Left-click      → place a numbered dot
  Right-click     → remove the last dot
  Refresh         → grab a fresh screenshot
  Clear All       → remove all dots
  Save & Close    → write the JSON file and close
"""

# Then use ADB_PATH in your subprocess calls

import tkinter as tk
from tkinter import messagebox
import subprocess, time, io, json, os, sys
import numpy as np
from PIL import Image, ImageTk, ImageDraw, ImageFont

# Suppress terminal flicker on Windows when spawning ADB via subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── Brand theme (matches gui_app.py) ────────────────────────────
ACCENT       = "#df7d59"   # primary brand color
ACCENT_HOVER = "#c5663f"   # darker, for hover states
ACCENT_DARK  = "#7a4530"   # dark filled variant (toolbar buttons)

# ── Config ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

def _resolve_adb():
    if sys.platform != "win32":
        return "adb"
    dest_dir = os.path.join(os.environ.get("LOCALAPPDATA", BASE_DIR), "KrakenPrime", "adb")
    dest_adb = os.path.join(dest_dir, "adb.exe")
    if os.path.isfile(dest_adb):
        return dest_adb
    src_dir = getattr(sys, "_MEIPASS", BASE_DIR)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        for fname in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
            src = os.path.join(src_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dest_dir, fname))
        if os.path.isfile(dest_adb):
            return dest_adb
    except Exception as e:
        print(f"[overlay] Could not extract bundled adb.exe: {e}")
    local_adb = os.path.join(BASE_DIR, "adb.exe")
    if os.path.isfile(local_adb):
        return local_adb
    return "adb"

ADB = _resolve_adb()
DEVICE       = "127.0.0.1:5555"
# These describe the LDPlayer / device resolution — the coordinate
# space that ADB taps are ultimately sent in. They stay fixed
# regardless of the size of the window on screen.
DEVICE_W     = 1600
DEVICE_H     = 900
SCREENSHOT_W = 1600
SCREENSHOT_H = 900

# How much of the available screen we're willing to use (leaving room
# for the toolbar, status bar, and window chrome/taskbar), and a
# sensible floor so the canvas never gets too small to use.
MIN_DISPLAY_W = 480
MAX_SCREEN_FRACTION_W = 0.90
MAX_SCREEN_FRACTION_H = 0.80
CHROME_RESERVE_H = 110   # approx height used by toolbar + status labels

# Read env vars from app.py (or use defaults for standalone)
POINTS_FILE   = os.environ.get("OVERLAY_OUTPUT", "deploy_points.json")
TITLE_HINT    = os.environ.get("OVERLAY_TITLE",  "COC Deployment Planner")
SENTINEL_FILE = os.environ.get("OVERLAY_SENTINEL", None)  # Signals cancel if still exists on close

# ── Coordinate helpers ──────────────────────────────────────────
# These take the *current* on-screen display size explicitly, since
# the window/canvas can now be resized (or start smaller to fit
# smaller screens/laptops) instead of always being a fixed 1600x900
# canvas.

def display_to_device(dx, dy, display_w, display_h):
    devx = int(dx / display_w * DEVICE_W)
    devy = int(dy / display_h * DEVICE_H)
    return devx, devy

def device_to_display(devx, devy, display_w, display_h):
    dx = int(devx / DEVICE_W * display_w)
    dy = int(devy / DEVICE_H * display_h)
    return dx, dy

# ── ADB screenshot ────────────────────────────────────────────

def grab_screenshot():
    try:
        result = subprocess.run(
            [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=12, creationflags=_NO_WINDOW
        )
        if not result.stdout or len(result.stdout) < 1000:
            return None
        img = Image.open(io.BytesIO(result.stdout))
        img = img.resize((SCREENSHOT_W, SCREENSHOT_H), Image.LANCZOS)
        return img
    except Exception as e:
        print(f"[overlay] Screenshot error: {e}")
        return None

# ── Dot drawing ───────────────────────────────────────────────

DOT_RADIUS = 10
DOT_COLORS = ["#FF4444", "#FF8C00", "#FFD700", "#44FF88",
              "#00BFFF", "#BF7FFF", "#FF69B4", "#FFFFFF"]

def draw_dots_on_image(pil_img, points_display):
    img  = pil_img.copy()
    draw = ImageDraw.Draw(img)
    for i, (dx, dy) in enumerate(points_display):
        color = DOT_COLORS[i % len(DOT_COLORS)]
        r = DOT_RADIUS
        draw.ellipse([dx-r-2, dy-r-2, dx+r+2, dy+r+2], fill="white", outline="white")
        draw.ellipse([dx-r,   dy-r,   dx+r,   dy+r  ], fill=color,   outline="black")
        label = str(i + 1)
        try:
            font = ImageFont.truetype("arial.ttf", 11)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.text((dx - tw//2, dy - th//2), label, fill="black", font=font)
    return img

# ── Main overlay app ──────────────────────────────────────────

class DeployOverlay:
    def __init__(self, root):
        self.root = root
        self.root.title(f"⚔  {TITLE_HINT}")
        # Allow the window to be resized so it can be fit to (or
        # adjusted on) any screen — no longer locked to a fixed
        # 1600x900 size.
        self.root.resizable(True, True)
        self.root.configure(bg="#1a1a2e")
        self._set_window_icon()

        self.base_image  = None
        self.tk_image    = None
        self.points_dev  = []   # (devx, devy) device space — source of truth

        # Compute an initial canvas size that fits comfortably on
        # this screen, preserving the device's aspect ratio.
        self.display_w, self.display_h = self._compute_fit_size()
        self._resize_job = None

        self._build_ui()
        self._try_load_existing_points()
        self.refresh_screenshot()

    def _compute_fit_size(self):
        """Pick an on-screen canvas size that fits the current screen
        while preserving the device's aspect ratio."""
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
        except Exception:
            screen_w, screen_h = DEVICE_W, DEVICE_H

        max_w = max(MIN_DISPLAY_W, int(screen_w * MAX_SCREEN_FRACTION_W))
        max_h = max(1, int(screen_h * MAX_SCREEN_FRACTION_H) - CHROME_RESERVE_H)

        aspect = DEVICE_W / DEVICE_H
        # Fit within (max_w, max_h) without ever upscaling past native res.
        scale = min(max_w / DEVICE_W, max_h / DEVICE_H, 1.0)
        w = max(MIN_DISPLAY_W, int(DEVICE_W * scale))
        h = int(w / aspect)
        return w, h

    def _set_window_icon(self):
        """Match gui_app.py branding by using the same icon.ico, if present."""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(base, "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

    # ── UI ────────────────────────────────────────────────────

    def _build_ui(self):
        bar = tk.Frame(self.root, bg="#16213e", pady=6, padx=8)
        bar.pack(fill="x")

        tk.Label(bar, text=f"⚔  {TITLE_HINT}",
                 font=("Consolas", 12, "bold"),
                 fg=ACCENT, bg="#16213e").pack(side="left")

        btn = {"bg": ACCENT_DARK, "fg": "#e0e0e0",
               "activebackground": ACCENT, "activeforeground": "white",
               "relief": "flat", "padx": 12, "pady": 4,
               "font": ("Consolas", 10, "bold"), "cursor": "hand2"}

        tk.Button(bar, text="⟳  Refresh",
                  command=self.refresh_screenshot, **btn).pack(side="right", padx=4)
        tk.Button(bar, text="✕  Clear All",
                  command=self.clear_points, **btn).pack(side="right", padx=4)
        tk.Button(bar, text="↩  Undo",
                  command=self.undo_point, **btn).pack(side="right", padx=4)
        tk.Button(bar, text="💾  Save & Close",
                  command=self.save_and_close,
                  bg=ACCENT, fg="white", activebackground=ACCENT_HOVER,
                  relief="flat", padx=12, pady=4,
                  font=("Consolas", 10, "bold"), cursor="hand2").pack(side="right", padx=4)

        self.canvas = tk.Canvas(self.root,
                                width=self.display_w, height=self.display_h,
                                bg="#0d0d1a", cursor="crosshair",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.on_left_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        # Keep the screenshot fitted to the window as the user
        # resizes it (e.g. maximizing, or dragging to fit their screen).
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.status_var = tk.StringVar(value="Connecting to LDPlayer…")
        tk.Label(self.root, textvariable=self.status_var,
                 font=("Consolas", 9), fg="#888",
                 bg="#1a1a2e", anchor="w", padx=8, pady=4).pack(fill="x")

        tk.Label(self.root,
                 text=("Left-click → place dot    Right-click / Undo → remove last    "
                       "Save & Close → write " + os.path.basename(POINTS_FILE)),
                 font=("Consolas", 8), fg="#555", bg="#1a1a2e").pack(pady=(0, 6))

    # ── Screenshot ────────────────────────────────────────────

    def refresh_screenshot(self):
        self.status_var.set("Grabbing screenshot from LDPlayer…")
        self.root.update()
        img = grab_screenshot()
        if img is None:
            self.status_var.set("⚠  Could not connect — is LDPlayer running?")
            img = Image.new("RGB", (SCREENSHOT_W, SCREENSHOT_H), (20, 20, 40))
        self.base_image = img
        self._redraw()
        self.status_var.set(
            f"Screenshot loaded  |  {len(self.points_dev)} point(s)  "
            f"|  saving to: {POINTS_FILE}"
        )

    def _on_canvas_resize(self, event):
        # Debounce: only redraw a short moment after resizing stops,
        # so dragging the window edge doesn't trigger a redraw storm.
        new_w, new_h = event.width, event.height
        if new_w < 10 or new_h < 10:
            return
        self.display_w, self.display_h = new_w, new_h
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(80, self._redraw)

    def _redraw(self):
        self._resize_job = None
        if self.base_image is None:
            return
        # Points are stored in device space; convert to the *current*
        # display size each time we draw, so resizing the window
        # always keeps dots aligned with the screenshot.
        points_disp = [
            device_to_display(devx, devy, self.display_w, self.display_h)
            for devx, devy in self.points_dev
        ]
        composite = draw_dots_on_image(
            self.base_image.resize((self.display_w, self.display_h), Image.LANCZOS),
            points_disp
        )
        self.tk_image = ImageTk.PhotoImage(composite)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)

    # ── Click handlers ────────────────────────────────────────

    def on_left_click(self, event):
        devx, devy = display_to_device(event.x, event.y, self.display_w, self.display_h)
        self.points_dev.append((devx, devy))
        self._redraw()
        self.status_var.set(
            f"Point {len(self.points_dev)} → device ({devx},{devy})  |  "
            f"Total: {len(self.points_dev)}"
        )

    def on_right_click(self, event):
        self.undo_point()

    def undo_point(self):
        if self.points_dev:
            removed = self.points_dev.pop()
            self._redraw()
            self.status_var.set(
                f"Removed {removed}  |  Total: {len(self.points_dev)}"
            )

    def clear_points(self):
        self.points_dev.clear()
        self._redraw()
        self.status_var.set("All points cleared.")

    # ── Save / load ───────────────────────────────────────────

    def save_and_close(self):
        if not self.points_dev:
            messagebox.showwarning("No points",
                                   "Place at least one dot before saving.")
            return
        # Always save with "points" key so app.py can load either file
        data = {
            "device_resolution": [DEVICE_W, DEVICE_H],
            "points": [{"x": x, "y": y} for x, y in self.points_dev]
        }
        with open(POINTS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        
        # ✅ Remove sentinel to signal successful save
        if SENTINEL_FILE and os.path.exists(SENTINEL_FILE):
            try:
                os.remove(SENTINEL_FILE)
            except OSError:
                pass
        
        messagebox.showinfo(
            "Saved",
            f"✔  {len(self.points_dev)} point(s) saved to:\n{POINTS_FILE}\n\n"
            "You can close this window."
        )
        self.root.destroy()

    def _try_load_existing_points(self):
        if not os.path.exists(POINTS_FILE):
            return
        try:
            with open(POINTS_FILE) as f:
                data = json.load(f)
            # Support both "points" and "slots" keys
            raw_pts = data.get("points", data.get("slots", []))
            for pt in raw_pts:
                devx, devy = pt["x"], pt["y"]
                self.points_dev.append((devx, devy))
        except Exception as e:
            print(f"[overlay] Could not load existing points: {e}")


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app  = DeployOverlay(root)
    root.mainloop()