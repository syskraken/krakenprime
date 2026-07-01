import customtkinter as ctk
import threading
import sys
import os
import io
import json
import time
import re
import webbrowser
from PIL import Image, ImageTk, ImageDraw
import subprocess
from tkinter import messagebox, Canvas, Scrollbar

# Suppress terminal flicker on Windows — every subprocess call (ADB, overlay,
# app.py itself) uses this flag so no black cmd window ever flashes on screen.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── Frozen-exe subprocess helper ──────────────────────────────
# When PyInstaller compiles everything into KrakenPrime.exe, sys.executable
# points to KrakenPrime.exe — NOT to python.exe.  Calling
#   subprocess.Popen([sys.executable, "app.py"])
# re-launches the whole GUI exe instead of running the script.
#
# _python_exe()   → real python.exe path (for running .py scripts)
# _script_path(f) → absolute path to a bundled .py file (works frozen + dev)
#
def _python_exe() -> str:
    """Return the real python.exe, even when running as a PyInstaller .exe."""
    if not getattr(sys, "frozen", False):
        return sys.executable          # dev mode: already python.exe

    # Frozen: sys.executable is KrakenPrime.exe — find python.exe next to it
    # or walk common install paths.
    exe_dir = os.path.dirname(sys.executable)

    # 1. python.exe sitting next to the compiled exe (cleanest deploy)
    candidate = os.path.join(exe_dir, "python.exe")
    if os.path.isfile(candidate):
        return candidate

    # 2. Search registry for Python 3.11 install dir
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (
                r"SOFTWARE\Python\PythonCore\3.11\InstallPath",
                r"SOFTWARE\WOW6432Node\Python\PythonCore\3.11\InstallPath",
            ):
                try:
                    key = winreg.OpenKey(root, subkey)
                    install_dir, _ = winreg.QueryValueEx(key, "ExecutablePath")
                    winreg.CloseKey(key)
                    if os.path.isfile(install_dir):
                        return install_dir
                except Exception:
                    pass
    except Exception:
        pass

    # 3. Common fixed paths
    for path in [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python311", "python.exe"),
        r"C:\Python311\python.exe",
        r"C:\Program Files\Python311\python.exe",
    ]:
        if os.path.isfile(path):
            return path

    # 4. Last resort: whatever "python" resolves to in PATH
    import shutil
    found = shutil.which("python")
    return found if found else "python"


def _script_path(filename: str) -> str:
    """Return absolute path to a bundled .py script.

    When frozen, PyInstaller extracts data files to sys._MEIPASS (the temp
    folder).  In dev mode the scripts live next to gui_app.py.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)

# Set theme and appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Brand theme ──────────────────────────────────────────────
ACCENT       = "#df7d59"   # primary brand color
ACCENT_HOVER = "#c5663f"   # darker, for hover states
ACCENT_DARK  = "#7a4530"   # dark filled variant (secondary buttons)
ACCENT_SOFT  = "#e8a87c"   # lighter tint, for secondary accents/cards

class ProfessionalCoCBot(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("KRAKEN PRIME")
        self.geometry("1100x700")
        self.resizable(False, False)

        self.frozen = getattr(sys, "frozen", False)
        self.app_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if self.frozen else os.path.dirname(os.path.abspath(__file__))
        self.resource_dir = getattr(sys, "_MEIPASS", self.app_dir)
        os.chdir(self.app_dir)

        self._set_window_icon()

        # Configuration
        self.config_file = os.path.join(self.app_dir, "config.json")
        self.overlay_script = os.path.join(self.resource_dir, "deploy_overlay.py")
        self.config = self.load_initial_config()
        
        # State variables
        self.attack_count = 0
        self.start_time = time.time()
        self.bot_process = None
        self.stop_event = threading.Event()
        self.active_preset_id = None  # e.g. "preset1" once the bot logs which preset it's using

        # Layout setup
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._create_sidebar()
        self._create_main_content()
        
        # Initialize button states
        self._update_button_states()
        
        # Initialize with Dashboard
        self.select_frame_by_name("dashboard")

    def _set_window_icon(self):
        icon_path = os.path.join(self.resource_dir, "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
                return
            except Exception:
                pass

        # Fallback: use a bundled PNG if available
        png_icon = os.path.join(self.resource_dir, "icon.png")
        if os.path.exists(png_icon):
            try:
                img = Image.open(png_icon)
                self.iconphoto(True, ImageTk.PhotoImage(img))
            except Exception:
                pass

    def _load_icon_image(self, size=(28, 28)):
        """Loads icon.ico as a CTkImage for use in labels (replaces emoji branding)."""
        icon_path = os.path.join(self.resource_dir, "icon.png")
        if not os.path.exists(icon_path):
            return None
        try:
            img = Image.open(icon_path).convert("RGBA")
            img = img.resize(size, Image.LANCZOS)
            return ctk.CTkImage(light_image=img, dark_image=img, size=size)
        except Exception:
            return None

    def _create_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color="#1a1c1e")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        self.logo_icon_image = self._load_icon_image((26, 26))
        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="  KRAKEN PRIME",
                                     image=self.logo_icon_image, compound="left",
                                     font=ctk.CTkFont(size=22, weight="bold"), text_color=ACCENT, anchor="w")
        self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 8), sticky="w")

        self.sidebar_brand = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.sidebar_brand.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")
        ctk.CTkLabel(self.sidebar_brand, text="Clash of Clans Bot", font=ctk.CTkFont(size=10), anchor="w", height=14).pack(anchor="w", pady=0)
        discord_link = ctk.CTkLabel(self.sidebar_brand, text="Discord: https://discord.gg/jV7ymDtH7F", font=ctk.CTkFont(size=10), text_color="#8ab4f8", anchor="w", cursor="hand2", height=14)
        discord_link.pack(anchor="w", pady=0)
        discord_link.bind("<Button-1>", lambda e: self.open_url("https://discord.gg/jV7ymDtH7F"))
        
        github_link = ctk.CTkLabel(self.sidebar_brand, text="Github: https://github.com/syskraken", font=ctk.CTkFont(size=10), text_color="#8ab4f8", anchor="w", cursor="hand2", height=14)
        github_link.pack(anchor="w", pady=0)
        github_link.bind("<Button-1>", lambda e: self.open_url("https://github.com/syskraken"))

        # Navigation Buttons
        self.dashboard_button = self._create_nav_button("Dashboard", "dashboard", 2)
        self.config_button = self._create_nav_button("Configuration", "config", 3)
        self.preview_button = self._create_nav_button("Live Preview", "preview", 4)
        self.history_button = self._create_nav_button("Raid History", "history", 5)
        self.developer_button = self._create_nav_button("Donation", "developer", 6)

        # Control Section
        self.control_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.control_frame.grid(row=7, column=0, padx=20, pady=20, sticky="ew")

        # Glow border frame for START BOT button
        self.start_btn_glow_frame = ctk.CTkFrame(self.control_frame, fg_color=ACCENT, corner_radius=8)
        self.start_btn_glow_frame.pack(fill="x", pady=5, padx=0)

        self.start_btn = ctk.CTkButton(self.start_btn_glow_frame, text="START BOT", command=self.toggle_bot,
                                     fg_color=ACCENT, hover_color=ACCENT_HOVER, height=40, font=ctk.CTkFont(weight="bold"))
        self.start_btn.pack(fill="x", padx=2, pady=2)

        self.refresh_btn = ctk.CTkButton(self.control_frame, text="🔄 REFRESH", command=self.refresh_ui,
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER, height=35, font=ctk.CTkFont(weight="bold"))
        self.refresh_btn.pack(fill="x", pady=5)

        self.status_label = ctk.CTkLabel(self.sidebar_frame, text="● SYSTEM READY", text_color="#FFD700", 
                                       font=ctk.CTkFont(size=12, weight="bold"))
        self.status_label.grid(row=8, column=0, padx=20, pady=(0, 10))

        self.setup_warning = ctk.CTkLabel(self.sidebar_frame, text="⚠️ SETUP INCOMPLETE", text_color="#dc3545",
                                        font=ctk.CTkFont(size=10, weight="bold"))
        self.setup_warning.grid(row=9, column=0, padx=20, pady=(0, 20))
        
        self.setup_complete_label = ctk.CTkLabel(self.sidebar_frame, text="✅ SETUP COMPLETE - READY TO FARM!", text_color=ACCENT,
                                                font=ctk.CTkFont(size=10, weight="bold"))
        # The warning is managed by _update_button_states()
        
        # Glow effect tracking
        self.glow_active = False
        self.glow_direction = 1  # 1 for increasing, -1 for decreasing

    def _create_nav_button(self, text, name, row):
        btn = ctk.CTkButton(self.sidebar_frame, corner_radius=8, height=40, border_spacing=10, text=text,
                           fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                           anchor="w", command=lambda: self.select_frame_by_name(name))
        btn.grid(row=row, column=0, sticky="ew", padx=15, pady=5)
        return btn

    def _create_main_content(self):
        # Dashboard Frame
        self.dashboard_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.dashboard_frame.grid_columnconfigure((0, 1), weight=1)
        self.dashboard_frame.grid_rowconfigure(1, weight=1)
        
        # --- Stats Cards ---
        self.card_attacks = self._create_stat_card(self.dashboard_frame, "TOTAL RAIDS", "0", ACCENT, 0, 0)
        self.card_runtime = self._create_stat_card(self.dashboard_frame, "UPTIME", "00:00:00", ACCENT_SOFT, 0, 1)

        # --- System Logs (replaces Current Operation card) ---
        self.task_frame = ctk.CTkFrame(self.dashboard_frame)
        self.task_frame.grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 20), sticky="nsew")
        self.task_frame.grid_rowconfigure(1, weight=1)
        self.task_frame.grid_columnconfigure(0, weight=1)
        self.task_title = ctk.CTkLabel(self.task_frame, text="SYSTEM LOGS", font=ctk.CTkFont(size=12, weight="bold"))
        self.task_title.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")
        self.log_textbox = ctk.CTkTextbox(self.task_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_textbox.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="nsew")

        # Configuration Frame
        self.config_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.config_frame.grid_rowconfigure(0, weight=1)
        self.config_frame.grid_columnconfigure(0, weight=1)

        self.config_canvas = Canvas(self.config_frame, bg="#1a1c1e", highlightthickness=0)
        self.config_canvas.grid(row=0, column=0, sticky="nsew")

        self.config_scrollbar = Scrollbar(self.config_frame, orient="vertical", command=self.config_canvas.yview)
        self.config_scrollbar.grid(row=0, column=1, sticky="ns")

        self.config_canvas.configure(yscrollcommand=self.config_scrollbar.set)
        self.config_content = ctk.CTkFrame(self.config_canvas, fg_color="transparent")
        self.config_canvas.create_window((0, 0), window=self.config_content, anchor="nw")
        self.config_content.grid_columnconfigure(0, weight=1)
        self.config_content.grid_columnconfigure(1, weight=1)

        self.config_canvas.bind("<Configure>", lambda e: self.config_canvas.configure(scrollregion=self.config_canvas.bbox("all")))
        self.config_content.bind("<Configure>", lambda e: self.config_canvas.configure(scrollregion=self.config_canvas.bbox("all")))
        self.config_canvas.bind_all("<MouseWheel>", lambda e: self.config_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self._setup_config_tabs()

        # Preview Frame
        self.preview_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.preview_label = ctk.CTkLabel(self.preview_frame, text="Live Screen Preview", font=ctk.CTkFont(size=20, weight="bold"))
        self.preview_label.pack(pady=20)
        self.screen_canvas = ctk.CTkLabel(self.preview_frame, text="Click  📷 Refresh Screenshot  to grab a live view.", fg_color="#000", width=800, height=450)
        self.screen_canvas.pack(padx=20, pady=(0, 10))

        # Refresh screenshot button
        self.refresh_screenshot_btn = ctk.CTkButton(
            self.preview_frame,
            text="📷  Refresh Screenshot",
            command=self.refresh_preview_once,
            fg_color="#0f3460", hover_color="#e94560",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36
        )
        self.refresh_screenshot_btn.pack(pady=(0, 8))

        # --- Overlay legend / toggles ---
        self.overlay_vars = {
            "slots":  ctk.BooleanVar(value=True),
            "deploy": ctk.BooleanVar(value=True),
            "rage":   ctk.BooleanVar(value=True),
        }
        legend_frame = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        legend_frame.pack(padx=20, pady=(0, 4))

        self._add_overlay_toggle(legend_frame, "slots",  "Troop Slots",  "#00BFFF", 0)
        self._add_overlay_toggle(legend_frame, "deploy", "Deploy Points", "#39FF14", 1)
        self._add_overlay_toggle(legend_frame, "rage",   "Rage Points",  "#FF3B3B", 2)

        # Second row: shown only in Preset Mode, one swatch pair per active preset
        self.preset_legend_frame = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        self.preset_legend_frame.pack(padx=20, pady=(0, 10))

        # History Frame
        self.history_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.history_list = ctk.CTkTextbox(self.history_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.history_list.pack(fill="both", expand=True, padx=20, pady=20)

        # Developer / Donation Frame
        self.developer_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        dev_title = ctk.CTkLabel(self.developer_frame, text="Support the Developer",
                                  font=ctk.CTkFont(size=20, weight="bold"))
        dev_title.pack(anchor="w", padx=20, pady=(20, 5))

        dev_subtitle = ctk.CTkLabel(self.developer_frame,
                                     text="If Kraken Prime has been useful to you, consider supporting development.",
                                     font=ctk.CTkFont(size=12), text_color="gray70", anchor="w", justify="left")
        dev_subtitle.pack(anchor="w", padx=20, pady=(0, 20))

        donate_card = ctk.CTkFrame(self.developer_frame, corner_radius=10)
        donate_card.pack(fill="x", padx=20, pady=(0, 20))

        ctk.CTkLabel(donate_card, text="☕ Buy me a coffee", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(15, 5))
        coffee_link = ctk.CTkLabel(donate_card, text="https://ko-fi.com/franklinmarshall",
                     font=ctk.CTkFont(size=12), text_color="#8ab4f8", cursor="hand2", anchor="w")
        coffee_link.pack(anchor="w", padx=15, pady=(0, 5))
        coffee_link.bind("<Button-1>", lambda e: self.open_url("https://ko-fi.com/franklinmarshall"))

        
        ctk.CTkLabel(donate_card, text="Paypal", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(15, 5))
        coffee_link = ctk.CTkLabel(donate_card, text="https://www.paypal.com/paypalme/FranklinTripole?locale.x=en_US&country.x=PH",
                     font=ctk.CTkFont(size=12), text_color="#8ab4f8", cursor="hand2", anchor="w")
        coffee_link.pack(anchor="w", padx=15, pady=(0, 5))
        coffee_link.bind("<Button-1>", lambda e: self.open_url("https://www.paypal.com/paypalme/FranklinTripole?locale.x=en_US&country.x=PH"))


        ctk.CTkLabel(donate_card, text="Gcash", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 5))
        ctk.CTkLabel(donate_card, text="09271272799",
                     font=ctk.CTkFont(family="Consolas", size=12), text_color="#8ab4f8", anchor="w").pack(
            anchor="w", padx=15)
        ctk.CTkLabel(donate_card, text="Franklin Tripole",
                     font=ctk.CTkFont(family="Consolas", size=12), text_color="#8ab4f8", anchor="w").pack(
            anchor="w", padx=15, pady=(0, 5))

        ctk.CTkLabel(donate_card, text="Email: nzeus624@gmail.com", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 5))

    

        self.thanks_icon_image = self._load_icon_image((16, 16))
        thanks_row = ctk.CTkFrame(self.developer_frame, fg_color="transparent")
        thanks_row.pack(anchor="w", padx=20, pady=(0, 20))
        ctk.CTkLabel(thanks_row, text="", image=self.thanks_icon_image).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(thanks_row, text="Thanks for your support!",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray60").pack(side="left")

    def _create_stat_card(self, parent, title, value, color, row, col):
        card = ctk.CTkFrame(parent, height=120)
        card.grid(row=row, column=col, padx=15, pady=20, sticky="nsew")
        title_lbl = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=12, weight="bold"), text_color="gray70")
        title_lbl.pack(pady=(20, 0))
        val_lbl = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=32, weight="bold"), text_color=color)
        val_lbl.pack(pady=(0, 20))
        return val_lbl

    def _add_overlay_toggle(self, parent, key, label, color, col):
        chip = ctk.CTkFrame(parent, fg_color="transparent")
        chip.grid(row=0, column=col, padx=10)
        swatch = ctk.CTkLabel(chip, text="", width=14, height=14, fg_color=color, corner_radius=3)
        swatch.pack(side="left", padx=(0, 6))
        cb = ctk.CTkCheckBox(chip, text=label, variable=self.overlay_vars[key],
                              font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
                              command=self.refresh_preview_once)
        cb.pack(side="left")

    @staticmethod
    def _rgb_to_hex(rgb):
        return "#{:02X}{:02X}{:02X}".format(*rgb)

    def _add_swatch_label(self, parent, color_rgb, text, col):
        chip = ctk.CTkFrame(parent, fg_color="transparent")
        chip.grid(row=0, column=col, padx=6)
        swatch = ctk.CTkLabel(chip, text="", width=12, height=12,
                               fg_color=self._rgb_to_hex(color_rgb), corner_radius=3)
        swatch.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(chip, text=text, font=ctk.CTkFont(size=11), text_color="gray70").pack(side="left")

    def _update_preset_legend(self):
        """Rebuild the per-preset color key — only shown when Preset Mode is enabled."""
        for widget in self.preset_legend_frame.winfo_children():
            widget.destroy()

        if not bool(self.config.get("deploy_preset_enabled", False)):
            return

        if self.active_preset_id:
            preset_ids = [self.active_preset_id]
            suffix = " (in use)"
        else:
            raw_order = self.config.get("deploy_preset_order", "preset1,preset2,preset3")
            preset_ids = [p.strip() for p in str(raw_order).split(",") if p.strip()] or ["preset1"]
            suffix = ""

        col = 0
        for preset_id in preset_ids:
            try:
                slot_num = int(preset_id.replace("preset", ""))
            except ValueError:
                continue
            colors = self.PRESET_COLORS.get(slot_num, self.PRESET_COLORS[1])
            self._add_swatch_label(self.preset_legend_frame, colors["deploy"], f"Preset {slot_num} Deploy{suffix}", col)
            col += 1
            self._add_swatch_label(self.preset_legend_frame, colors["rage"], f"Preset {slot_num} Rage{suffix}", col)
            col += 1


    def _setup_config_tabs(self):
        # We'll use a dictionary to store entries for easy saving
        self.entries = {}
        parent = self.config_content

        troops_panel = ctk.CTkFrame(parent, fg_color="transparent")
        troops_panel.grid(row=0, column=0, padx=(20, 10), pady=(0, 20), sticky="nsew")
        troops_panel.grid_columnconfigure(0, weight=1)
        troops_panel.grid_columnconfigure(1, weight=1)

        spells_panel = ctk.CTkFrame(parent, fg_color="transparent")
        spells_panel.grid(row=0, column=1, padx=(10, 20), pady=(0, 20), sticky="nsew")
        spells_panel.grid_columnconfigure(0, weight=1)
        spells_panel.grid_columnconfigure(1, weight=1)

        self._add_panel(troops_panel, "Troops & Heroes", [
            ("num_troop_slots", "Troop Slot Icons"),
            ("num_troops_total", "Total Troops to Deploy"),
            ("num_heroes", "Number of Heroes"),
        ])

        self._add_panel(spells_panel, "Spells & Strategy", [
            ("num_spells", "Lightning Spells", True),
            ("spells_per_ad", "Spells per Air Defense", True),
            ("num_rage", "Number of Rage Spells"),
            ("rage_delay", "Rage Delay (sec)"),
        ])

        self._add_section_label(parent, "Loot & Targets", 6, 0, span=2)
        ctk.CTkLabel(parent, text="Minimum Gold Target").grid(row=7, column=0, padx=20, pady=10, sticky="w")
        min_gold_entry = ctk.CTkEntry(parent, width=220)
        min_gold_entry.grid(row=7, column=1, padx=(10, 20), pady=10, sticky="ew")
        min_gold_entry.insert(0, str(self.config.get("min_gold", "")))
        self.entries["min_gold"] = min_gold_entry

        self._add_section_label(parent, "Deployment Presets", 8, 0, span=2)
        preset_enabled_var = ctk.BooleanVar(value=bool(self.config.get("deploy_preset_enabled", False)))
        ctk.CTkLabel(parent, text="Enable preset mode").grid(row=9, column=0, padx=20, pady=10, sticky="w")
        preset_toggle = ctk.CTkSwitch(parent, text="", variable=preset_enabled_var, onvalue=True, offvalue=False)
        preset_toggle.grid(row=9, column=1, padx=(10, 20), pady=10, sticky="w")
        self.entries["deploy_preset_enabled"] = preset_toggle

        self.preset_config_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.preset_config_frame.grid(row=10, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="nsew")
        self.preset_config_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.preset_config_frame, text="Deployment mode:", anchor="w").grid(row=0, column=0, pady=(0, 4), sticky="w")
        preset_mode_var = ctk.StringVar(value=self.config.get("deploy_preset_mode", "sequence"))
        preset_mode_menu = ctk.CTkOptionMenu(self.preset_config_frame, variable=preset_mode_var, values=["sequence", "random"], width=220)
        preset_mode_menu.grid(row=1, column=0, pady=(0, 10), sticky="ew")
        self.entries["deploy_preset_mode"] = preset_mode_menu

        self.preset_list_frame = ctk.CTkFrame(self.preset_config_frame, fg_color="transparent")
        self.preset_list_frame.grid(row=2, column=0, pady=(10, 0), sticky="nsew")

        add_preset_btn = ctk.CTkButton(self.preset_config_frame, text="Add deployment preset", command=self.add_deployment_preset)
        add_preset_btn.grid(row=3, column=0, pady=(8, 4), sticky="ew")

        self._refresh_preset_list()
        self._toggle_preset_config(preset_enabled_var.get())

        preset_toggle.configure(command=lambda: self._toggle_preset_config(preset_enabled_var.get()))

        self._add_section_label(parent, "Advanced", 11, 0, span=2)
        self.run_setup_btn = ctk.CTkButton(parent, text="RUN FULL GUIDED SETUP", command=self.run_setup, fg_color="#6c757d")
        self.run_setup_btn.grid(row=12, column=0, columnspan=2, pady=(10, 5), sticky="ew", padx=20)
        self.edit_troop_slots_btn = ctk.CTkButton(parent, text="EDIT PIN TROOP BAR SLOTS", command=lambda: self.launch_manual_overlay("troop_slots.json", "Pin Troop Bar Slots"))
        self.edit_troop_slots_btn.grid(row=13, column=0, columnspan=2, pady=5, sticky="ew", padx=20)
        self.edit_deploy_btn = ctk.CTkButton(parent, text="EDIT DEPLOY POINTS", command=lambda: self.launch_manual_overlay("deploy_points.json", "Edit Deploy Points"))
        self.edit_deploy_btn.grid(row=14, column=0, columnspan=2, pady=5, sticky="ew", padx=20)
        self.edit_rage_btn = ctk.CTkButton(parent, text="EDIT RAGE POINTS", command=lambda: self.launch_manual_overlay("rage_points.json", "Edit Rage Points"))
        self.edit_rage_btn.grid(row=15, column=0, columnspan=2, pady=5, sticky="ew", padx=20)
        ctk.CTkButton(parent, text="SAVE ALL SETTINGS", command=self.save_config, fg_color=ACCENT).grid(row=16, column=0, columnspan=2, pady=(15, 5), sticky="ew", padx=20)

        separator = ctk.CTkFrame(parent, height=1, fg_color="gray30")
        separator.grid(row=17, column=0, columnspan=2, padx=20, pady=(10, 5), sticky="ew")

        ctk.CTkLabel(parent, text="Reconfigure", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#dc3545").grid(row=18, column=0, columnspan=2, padx=20, pady=(0, 4), sticky="w")

        ctk.CTkButton(
            parent, text="🗑 Reset Deployment Points",
            command=self.reset_all_config,
            fg_color="#7a1a1a", hover_color="#dc3545",
            font=ctk.CTkFont(weight="bold")
        ).grid(row=19, column=0, columnspan=2, pady=(0, 20), sticky="ew", padx=20)

    def _add_section_label(self, parent, text, row, col=0, span=2):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=14, weight="bold"), text_color=ACCENT).grid(
            row=row, column=col, columnspan=span, padx=20, pady=(4, 8), sticky="w"
        )

    def _toggle_preset_config(self, enabled):
        if hasattr(self, "preset_config_frame"):
            if enabled:
                self.preset_config_frame.grid()
            else:
                self.preset_config_frame.grid_remove()

    def _refresh_preset_list(self):
        for widget in self.preset_list_frame.winfo_children():
            widget.destroy()

        preset_slots = []
        for idx in range(1, 5):
            deploy_path = f"deploy_preset_{idx}.json"
            rage_path = f"rage_preset_{idx}.json"
            if os.path.exists(deploy_path):
                preset_slots.append((idx, deploy_path, rage_path))

        if not preset_slots:
            ctk.CTkLabel(self.preset_list_frame, text="No deployment presets saved yet.", text_color="gray70").pack(anchor="w", pady=4)
            return

        for idx, deploy_path, rage_path in preset_slots:
            has_rage = os.path.exists(rage_path)
            rage_note = "+ rage" if has_rage else "no rage pts"

            row = ctk.CTkFrame(self.preset_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            row.columnconfigure(0, weight=1)

            ctk.CTkLabel(row, text=f"Preset {idx}: {deploy_path} ({rage_note})", anchor="w").grid(
                row=0, column=0, sticky="w")

            ctk.CTkButton(
                row, text="✏ Edit", width=70, height=26,
                fg_color=ACCENT_DARK, hover_color=ACCENT,
                font=ctk.CTkFont(size=11),
                command=lambda i=idx: self._edit_preset(i),
            ).grid(row=0, column=1, padx=(8, 4))

            ctk.CTkButton(
                row, text="🗑 Del", width=70, height=26,
                fg_color="#7a1a1a", hover_color="#dc3545",
                font=ctk.CTkFont(size=11),
                command=lambda i=idx: self._delete_preset(i),
            ).grid(row=0, column=2)

    def _edit_preset(self, slot_num):
        deploy_path = f"deploy_preset_{slot_num}.json"
        rage_path   = f"rage_preset_{slot_num}.json"
        threading.Thread(
            target=self._run_preset_overlay_sequence,
            args=(slot_num, deploy_path, rage_path),
            daemon=True,
        ).start()

    def _delete_preset(self, slot_num):
        deploy_path = f"deploy_preset_{slot_num}.json"
        rage_path   = f"rage_preset_{slot_num}.json"

        confirmed = messagebox.askyesno(
            "Delete Preset",
            f"Delete Preset {slot_num}?\n\n"
            f"This will remove {deploy_path}"
            + (f" and {rage_path}" if os.path.exists(rage_path) else "")
            + ".",
        )
        if not confirmed:
            return

        for path in (deploy_path, rage_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                    self.log(f"[preset] Deleted {path}")
                except Exception as e:
                    self.log(f"[preset] Could not delete {path}: {e}")

        self._refresh_preset_list()

    def add_deployment_preset(self):
        next_slot = 1
        while next_slot <= 4:
            path = f"deploy_preset_{next_slot}.json"
            if not os.path.exists(path):
                break
            next_slot += 1

        if next_slot > 4:
            messagebox.showwarning("Preset limit reached", "You can only save up to 4 deployment presets.")
            return

        deploy_path = f"deploy_preset_{next_slot}.json"
        rage_path = f"rage_preset_{next_slot}.json"

        # Run both overlays back-to-back in a background thread so the GUI
        # stays responsive: pin deploy points first, then immediately pin
        # the matching rage points for that same preset.
        threading.Thread(
            target=self._run_preset_overlay_sequence,
            args=(next_slot, deploy_path, rage_path),
            daemon=True
        ).start()

    def _run_preset_overlay_sequence(self, slot_num, deploy_path, rage_path):
        has_rage = bool(self.config.get("has_rage", False))

        deploy_sentinel = deploy_path + ".editing"
        deploy_proc = self._launch_overlay_blocking(deploy_path, f"Edit Deploy Preset {slot_num}")
        deploy_proc.wait()

        # Check if user cancelled: if sentinel still exists, user cancelled the overlay
        user_cancelled = os.path.exists(deploy_sentinel)
        
        if user_cancelled:
            self.after(0, lambda: self.log(f"[preset] Deploy preset {slot_num} editing cancelled."))
            return
        
        # Overlay saved successfully (sentinel was removed)
        if not os.path.exists(deploy_path):
            self.after(0, lambda: self.log(f"[preset] Deploy preset {slot_num} closed without saving — skipping rage step."))
            return

        self.after(0, self._refresh_preset_list)

        if has_rage:
            self.after(0, lambda: self.log(f"[preset] Deploy preset {slot_num} saved. Opening rage point overlay..."))
            rage_sentinel = rage_path + ".editing"
            rage_proc = self._launch_overlay_blocking(rage_path, f"Edit Rage Preset {slot_num}")
            rage_proc.wait()
            
            # Check if user cancelled rage overlay
            rage_cancelled = os.path.exists(rage_sentinel)
            if rage_cancelled:
                self.after(0, lambda: self.log(f"[preset] Rage preset {slot_num} editing cancelled."))
            elif os.path.exists(rage_path):
                self.after(0, lambda: self.log(f"[preset] Rage preset {slot_num} saved."))
            else:
                self.after(0, lambda: self.log(f"[preset] Rage preset {slot_num} closed without saving — preset will run without rage."))

        self.after(0, self._refresh_preset_list)

    def _add_panel(self, parent, title, items):
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(size=14, weight="bold"), text_color=ACCENT).grid(
            row=0, column=0, columnspan=2, padx=0, pady=(0, 8), sticky="w"
        )
        for idx, item in enumerate(items):
            if isinstance(item, tuple) and len(item) == 2:
                key, label = item
                disabled = False
            else:
                key, label, disabled = item
            self._add_config_item(parent, key, label, idx + 1, disabled=disabled)

    def _add_config_item(self, parent, key, label, row, disabled=False):
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, padx=0, pady=10, sticky="w")
        entry = ctk.CTkEntry(parent, width=220)
        entry.grid(row=row, column=1, padx=(10, 0), pady=10, sticky="ew")
        entry.insert(0, str(self.config.get(key, "")))
        if disabled:
            entry.configure(state="disabled", fg_color="gray20")
        self.entries[key] = entry

    def _update_button_states(self):
        is_configured = self.config.get("setup_complete", False)
        
        # Check if all required fields are filled for the setup button
        has_troops = (self.config.get("num_troop_slots") and 
                      self.config.get("num_troops_total") and 
                      self.config.get("num_heroes"))
        has_spells = (self.config.get("num_rage") and 
                      self.config.get("rage_delay"))
        has_loot_target = self.config.get("min_gold")
        
        setup_ready = has_troops and has_spells and has_loot_target
        
        if is_configured:
            self.start_btn.configure(state="normal", fg_color=ACCENT)
            self.start_btn_glow_frame.configure(fg_color=ACCENT)
            self.edit_deploy_btn.configure(state="normal", fg_color=ACCENT)
            self.edit_rage_btn.configure(state="normal", fg_color=ACCENT)
            self.edit_troop_slots_btn.configure(state="normal", fg_color=ACCENT)
            self.setup_warning.grid_forget()
            self.setup_complete_label.grid(row=9, column=0, padx=20, pady=(0, 20))
            # Start glow effect
            self.glow_active = True
            self._glow_index = 0
            self._animate_glow()
        else:
            # We keep the start button clickable but it will show a message
            # This fulfills "unclickable" (functionally) and "message if they start"
            # However, for "edit" buttons, we make them strictly disabled as requested
            self.start_btn.configure(state="normal", fg_color="#6c757d")
            self.start_btn_glow_frame.configure(fg_color="#6c757d")
            self.edit_deploy_btn.configure(state="disabled", fg_color="gray30")
            self.edit_rage_btn.configure(state="disabled", fg_color="gray30")
            self.edit_troop_slots_btn.configure(state="disabled", fg_color="gray30")
            self.setup_warning.grid(row=9, column=0, padx=20, pady=(0, 20))
            self.setup_complete_label.grid_forget()
            # Stop glow effect
            self.glow_active = False
        
        # Disable "RUN FULL GUIDED SETUP" button if required fields are empty
        if setup_ready:
            self.run_setup_btn.configure(state="normal", fg_color="#6c757d")
        else:
            self.run_setup_btn.configure(state="disabled", fg_color="gray30")

    def select_frame_by_name(self, name):
        # Set button color for selected button
        self.dashboard_button.configure(fg_color=("gray75", "gray25") if name == "dashboard" else "transparent")
        self.config_button.configure(fg_color=("gray75", "gray25") if name == "config" else "transparent")
        self.preview_button.configure(fg_color=("gray75", "gray25") if name == "preview" else "transparent")
        self.history_button.configure(fg_color=("gray75", "gray25") if name == "history" else "transparent")
        self.developer_button.configure(fg_color=("gray75", "gray25") if name == "developer" else "transparent")

        # Show selected frame
        if name == "dashboard": self.dashboard_frame.grid(row=0, column=1, sticky="nsew")
        else: self.dashboard_frame.grid_forget()
        if name == "config": self.config_frame.grid(row=0, column=1, sticky="nsew")
        else: self.config_frame.grid_forget()
        if name == "preview":
            self.preview_frame.grid(row=0, column=1, sticky="nsew")
            self.refresh_preview_once()
        else: self.preview_frame.grid_forget()
        if name == "history": self.history_frame.grid(row=0, column=1, sticky="nsew")
        else: self.history_frame.grid_forget()
        if name == "developer": self.developer_frame.grid(row=0, column=1, sticky="nsew")
        else: self.developer_frame.grid_forget()

    def load_initial_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f: return json.load(f)
            except: pass
        return {}

    def save_config(self):
        new_config = {}
        for key, entry in self.entries.items():
            if hasattr(entry, "cget") and entry.cget("state") == "disabled":
                new_config[key] = self.config.get(key, 0)
                continue
            if isinstance(entry, ctk.CTkOptionMenu):
                new_config[key] = entry.get()
                continue
            if isinstance(entry, ctk.CTkSwitch):
                new_config[key] = bool(entry.get())
                continue
            try: new_config[key] = int(entry.get())
            except: new_config[key] = entry.get()
        
        # Automatically enable rage if count > 0
        new_config["has_rage"] = int(new_config.get("num_rage", 0)) > 0
        new_config["setup_complete"] = self.config.get("setup_complete", False)
        
        with open(self.config_file, "w") as f: json.dump(new_config, f, indent=2)
        self.config = new_config
        self._update_button_states()
        self.log("Configuration saved successfully.")

    def reset_all_config(self):
        if self.bot_process:
            messagebox.showwarning("Bot Running", "Stop the bot before resetting configuration.")
            return

        confirmed = messagebox.askyesno(
            "Reset All Config",
            "This will permanently delete config.json and all .json point files "
            "(troop slots, deploy points, rage points, and all presets).\n\n"
            "The bot will need a full guided setup before it can run again.\n\n"
            "Are you sure?",
        )
        if not confirmed:
            return

        core_files = [
            "config.json",
            "troop_slots.json",
            "deploy_points.json",
            "rage_points.json",
        ]
        preset_files = [
            f for f in os.listdir(".")
            if re.match(r"^(deploy|rage)_preset_\d+\.json$", f)
        ]

        deleted = []
        for fname in core_files + preset_files:
            if os.path.exists(fname):
                try:
                    os.remove(fname)
                    deleted.append(fname)
                except Exception as e:
                    self.log(f"[reset] Could not delete {fname}: {e}")

        # Reset in-memory state
        self.config = {}
        self.active_preset_id = None

        # Clear config entry widgets
        for key, widget in self.entries.items():
            if isinstance(widget, ctk.CTkEntry) and widget.cget("state") != "disabled":
                widget.delete(0, "end")

        self._update_button_states()
        self._refresh_preset_list()

        if deleted:
            self.log(f"[reset] Deleted: {', '.join(deleted)}")
        self.log("[reset] All configuration cleared. Run FULL GUIDED SETUP to reconfigure.")

    def refresh_ui(self):
        """Refresh and reload all UI elements and configuration."""
        self.config = self.load_initial_config()
        
        # Update all entry fields with current config values
        for key, widget in self.entries.items():
            if isinstance(widget, ctk.CTkEntry) and widget.cget("state") != "disabled":
                widget.delete(0, "end")
                widget.insert(0, str(self.config.get(key, "")))
            elif isinstance(widget, ctk.CTkOptionMenu):
                widget.set(self.config.get(key, "sequence"))
            elif isinstance(widget, ctk.CTkSwitch):
                widget.set(bool(self.config.get(key, False)))
        
        # Refresh preset list
        self._refresh_preset_list()
        
        # Update button states
        self._update_button_states()
        
        # Update preset legend in preview
        self._update_preset_legend()
        
        self.log("UI refreshed successfully.")

    def _animate_glow(self):
        """Animate a glowing border effect around the START BOT button."""
        if not self.glow_active:
            return
        
        # Warm glow pulse cycling around the brand accent color (no neon green)
        glow_colors = [
            "#df7d59",  # Base accent
            "#e2895f",
            "#e69566",
            "#e9a16c",
            "#ecad73",
            "#f0b97a",  # Brightest point
            "#ecad73",
            "#e9a16c",
            "#e69566",
            "#e2895f",
            "#df7d59",  # Back to base
        ]
        
        # Cycle through the glow colors
        if not hasattr(self, '_glow_index'):
            self._glow_index = 0
        
        color = glow_colors[self._glow_index % len(glow_colors)]
        self.start_btn_glow_frame.configure(fg_color=color)
        
        self._glow_index += 1
        
        # Schedule next animation frame (update every 80ms for smooth effect)
        self.after(80, self._animate_glow)

    def open_url(self, url):
        webbrowser.open_new_tab(url)

    def log(self, message):
        self.log_textbox.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_textbox.see("end")

    def _get_entry_value(self, key, fallback=0):
        entry = self.entries.get(key)
        if entry is None:
            return fallback
        try:
            raw = entry.get().strip()
            if raw == "":
                return fallback
            return int(raw)
        except ValueError:
            return raw

    def _build_setup_answers(self):
        num_troop_slots = self._get_entry_value("num_troop_slots", 1)
        num_troops_total = self._get_entry_value("num_troops_total", 10)
        num_heroes = self._get_entry_value("num_heroes", 0)
        num_spells = self._get_entry_value("num_spells", 0)
        spells_per_ad = self._get_entry_value("spells_per_ad", 0)
        num_rage = self._get_entry_value("num_rage", 0)
        rage_delay = self._get_entry_value("rage_delay", 10)
        min_gold = self._get_entry_value("min_gold", 200000)

        answers = [
            str(num_troop_slots),
            str(num_troops_total),
            str(num_heroes),
            str(num_spells),
        ]

        if num_spells > 0:
            answers.append(str(spells_per_ad))

        has_rage = num_rage > 0
        answers.append("y" if has_rage else "n")

        if has_rage:
            answers.extend([str(num_rage), str(rage_delay)])

        answers.append(str(min_gold))
        return answers

    def toggle_bot(self):
        if not self.config.get("setup_complete", False):
            messagebox.showwarning("Setup Required", "Configuration is empty! Please run the full guided setup before starting the bot.")
            self.log("Configuration -> Advanced -> RUN FULL GUIDED SETUP")
            return

        if self.bot_process:
            self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        self.start_btn.configure(text="STOP BOT", fg_color="#dc3545", hover_color="#c82333")
        self.status_label.configure(text="● SYSTEM ACTIVE", text_color=ACCENT)
        self.stop_event.clear()
        self.start_time = time.time()
        self.active_preset_id = None
        threading.Thread(target=self.bot_loop, daemon=True).start()
        threading.Thread(target=self.update_runtime, daemon=True).start()
        threading.Thread(target=self.refresh_preview, daemon=True).start()

    def stop_bot(self):
        self.stop_event.set()
        if self.bot_process: self.bot_process.terminate()
        self.bot_process = None
        self.start_btn.configure(text="START BOT", fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.status_label.configure(text="● SYSTEM READY", text_color="#FFD700")

    def update_runtime(self):
        while not self.stop_event.is_set():
            elapsed = int(time.time() - self.start_time)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self.after(0, lambda: self.card_runtime.configure(text=f"{h:02d}:{m:02d}:{s:02d}"))
            time.sleep(1)

    # Color mapping for each point category — kept in sync with the
    # legend swatches in _create_main_content (BGR-friendly hex -> RGB tuple).
    OVERLAY_STYLES = {
        "slots":  {"file": "troop_slots.json",  "key": "slots",  "color": (0, 191, 255), "label": "T"},
        "deploy": {"file": "deploy_points.json", "key": "points", "color": (57, 255, 20),  "label": "D"},
        "rage":   {"file": "rage_points.json",   "key": "points", "color": (255, 59, 59),  "label": "R"},
    }

    # Distinct colors per preset slot so overlapping presets stay readable.
    # Deploy points use the bright variant, rage points use the dim variant,
    # so within one preset you can still tell deploy vs rage apart.
    PRESET_COLORS = {
        1: {"deploy": (57, 255, 20),  "rage": (255, 59, 59)},
        2: {"deploy": (255, 191, 0),  "rage": (255, 0, 200)},
        3: {"deploy": (0, 255, 255),  "rage": (180, 80, 255)},
        4: {"deploy": (255, 140, 0),  "rage": (120, 255, 120)},
    }

    def _load_points(self, filename, key):
        """Read a {"<key>": [{"x":.., "y":..}, ...]} JSON file. Returns [] on any failure."""
        if not os.path.exists(filename):
            return []
        try:
            with open(filename) as f:
                data = json.load(f)
            return [(p["x"], p["y"]) for p in data.get(key, [])]
        except Exception:
            return []

    def _draw_marker(self, draw, x, y, color, label, radius):
        draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                     outline=color, width=max(2, radius // 3))
        draw.line([x - radius * 1.6, y, x + radius * 1.6, y], fill=color, width=1)
        draw.line([x, y - radius * 1.6, x, y + radius * 1.6], fill=color, width=1)
        draw.text((x + radius + 3, y - radius - 2), label, fill=color)

    def _draw_overlays(self, img):
        """Draw troop slot / deploy / rage points onto a PIL image (native resolution),
        scaling marker sizes to the image so it still looks right after resizing.

        Mirrors app.py's deployment logic: if Preset Mode is enabled, draws every
        configured deploy_preset_N.json + its paired rage_preset_N.json (numbered
        and color-coded per preset). If Preset Mode is disabled, draws the plain
        deploy_points.json + rage_points.json — whichever the bot will actually
        use for the next raid.
        """
        draw = ImageDraw.Draw(img)
        w, h = img.size
        radius = max(4, int(min(w, h) * 0.012))

        # Troop slots are always drawn from the same file regardless of preset mode.
        if self.overlay_vars["slots"].get():
            style = self.OVERLAY_STYLES["slots"]
            for (x, y) in self._load_points(style["file"], style["key"]):
                self._draw_marker(draw, x, y, style["color"], style["label"], radius)

        preset_enabled = bool(self.config.get("deploy_preset_enabled", False))

        if not preset_enabled:
            if self.overlay_vars["deploy"].get():
                style = self.OVERLAY_STYLES["deploy"]
                for (x, y) in self._load_points(style["file"], style["key"]):
                    self._draw_marker(draw, x, y, style["color"], style["label"], radius)
            if self.overlay_vars["rage"].get():
                style = self.OVERLAY_STYLES["rage"]
                for (x, y) in self._load_points(style["file"], style["key"]):
                    self._draw_marker(draw, x, y, style["color"], style["label"], radius)
            return img

        # Preset mode on. If the bot has told us which preset it's actually
        # running (via the "[deploy] Using preset presetN for raid" log line),
        # show only that one — otherwise (bot not started yet) fall back to
        # showing every configured preset so pins can still be verified.
        if self.active_preset_id:
            preset_ids = [self.active_preset_id]
        else:
            raw_order = self.config.get("deploy_preset_order", "preset1,preset2,preset3")
            preset_ids = [p.strip() for p in str(raw_order).split(",") if p.strip()] or ["preset1"]

        for preset_id in preset_ids:
            try:
                slot_num = int(preset_id.replace("preset", ""))
            except ValueError:
                continue
            colors = self.PRESET_COLORS.get(slot_num, self.PRESET_COLORS[1])

            if self.overlay_vars["deploy"].get():
                deploy_path = f"deploy_preset_{slot_num}.json"
                pts = self._load_points(deploy_path, "points")
                if not pts and slot_num == 1:
                    # Same fallback app.py uses when preset1 has no file yet.
                    pts = self._load_points("deploy_points.json", "points")
                for (x, y) in pts:
                    self._draw_marker(draw, x, y, colors["deploy"], f"D{slot_num}", radius)

            if self.overlay_vars["rage"].get():
                rage_path = f"rage_preset_{slot_num}.json"
                pts = self._load_points(rage_path, "points")
                if not pts and slot_num == 1:
                    pts = self._load_points("rage_points.json", "points")
                for (x, y) in pts:
                    self._draw_marker(draw, x, y, colors["rage"], f"R{slot_num}", radius)

        return img

    def refresh_preview_once(self):
        """Grab a fresh ADB screenshot and render it immediately."""
        self.after(0, self._update_preset_legend)
        self.after(0, lambda: self.screen_canvas.configure(text="📡  Grabbing screenshot...", image=""))
        if hasattr(self, "refresh_screenshot_btn"):
            self.after(0, lambda: self.refresh_screenshot_btn.configure(state="disabled", text="📡  Connecting..."))
        def _run():
            self._render_preview_frame()
            if hasattr(self, "refresh_screenshot_btn"):
                self.after(0, lambda: self.refresh_screenshot_btn.configure(state="normal", text="📷  Refresh Screenshot"))
        threading.Thread(target=_run, daemon=True).start()

    def _grab_adb_screenshot(self):
        """Take a live screenshot from LDPlayer via ADB. Returns a PIL Image or None."""
        try:
            # Find adb.exe — prefer local copy next to the exe, fall back to PATH
            adb = os.path.join(self.app_dir, "adb.exe") if os.path.exists(
                os.path.join(self.app_dir, "adb.exe")) else "adb"
            result = subprocess.run(
                [adb, "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
                capture_output=True, timeout=12, creationflags=_NO_WINDOW
            )
            if result.stdout and len(result.stdout) > 1000:
                return Image.open(io.BytesIO(result.stdout)).convert("RGB")
        except Exception as e:
            print(f"[preview] ADB screenshot failed: {e}")
        return None

    def _render_preview_frame(self):
        img = None

        # 1. Try a live ADB screenshot first
        img = self._grab_adb_screenshot()

        # 2. Fall back to the debug file saved by app.py (correct absolute path)
        if img is None:
            debug_path = os.path.join(self.app_dir, "debug_battle_screen.png")
            if os.path.exists(debug_path):
                try:
                    img = Image.open(debug_path).convert("RGB")
                except Exception:
                    pass

        if img is None:
            self.after(0, lambda: self.screen_canvas.configure(
                text="⚠  Could not connect to LDPlayer.\nMake sure ADB is enabled on port 5555.",
                image=""
            ))
            return

        try:
            img = self._draw_overlays(img)
            img = img.resize((720, 405), Image.LANCZOS)
            tk_img = ImageTk.PhotoImage(img)
            self.after(0, lambda i=tk_img: self.screen_canvas.configure(image=i, text=""))
        except Exception as e:
            print(f"[preview] Render error: {e}")

    def refresh_preview(self):
        while not self.stop_event.is_set():
            self._render_preview_frame()
            time.sleep(5)

    def bot_loop(self):
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        self.bot_process = subprocess.Popen(
            [_python_exe(), _script_path("app.py"), "--farm-only"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=_NO_WINDOW
        )

        for line in self.bot_process.stdout:
            if self.stop_event.is_set(): break
            clean_line = ansi_escape.sub('', line).strip()
            if not clean_line: continue
            self.after(0, lambda l=clean_line: self.log(l))

            # Parse metrics — "RAID 12" marks the start of each attempt,
            # so this counts every raid attempt (including ones that get
            # aborted early), unlike hooking a single mid-sequence step.
            raid_match = re.match(r"RAID\s+(\d+)", clean_line)
            if raid_match:
                self.attack_count = int(raid_match.group(1))
                self.after(0, lambda c=self.attack_count: self.card_attacks.configure(text=str(c)))
                self.after(0, lambda c=self.attack_count: self.history_list.insert("0.0", f"[{time.strftime('%H:%M')}] Raid #{c} started.\n"))

            # Track which deployment preset the bot is actually using right now,
            # so the Live Preview overlay shows that preset only — not all of them.
            preset_match = re.search(r"\[deploy\]\s+Using preset\s+(preset\d+)\s+for raid", clean_line)
            if preset_match:
                new_preset_id = preset_match.group(1)
                if new_preset_id != self.active_preset_id:
                    self.active_preset_id = new_preset_id
                    self.after(0, self.refresh_preview_once)

        self.after(0, self.stop_bot)

    def run_setup(self):
        self.log("Launching guided setup...")
        threading.Thread(target=self.run_setup_process, daemon=True).start()

    def run_setup_process(self):
        answers = self._build_setup_answers()
        self.log(f"Submitting setup answers: {', '.join(answers)}")

        process = subprocess.Popen(
            [_python_exe(), _script_path("app.py"), "--setup-only"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=_NO_WINDOW
        )

        if process.stdin:
            process.stdin.write("\n".join(answers) + "\n")
            process.stdin.flush()
            process.stdin.close()

        for line in process.stdout:
            self.after(0, lambda l=line.strip(): self.log(f"[SETUP] {l}"))

        process.wait()

        # Reload config after setup
        self.config = self.load_initial_config()
        self.after(0, self._update_button_states)
        if self.config.get("setup_complete", False):
            self.log("Setup complete! You can now start the bot.")
        else:
            self.log("Setup finished but config was not marked complete. Please verify the bot output.")

    def launch_manual_overlay(self, file_path, title):
        env = os.environ.copy()
        env["OVERLAY_OUTPUT"] = os.path.join(self.app_dir, file_path)
        env["OVERLAY_TITLE"] = title
        subprocess.Popen([_python_exe(), _script_path("deploy_overlay.py")], env=env, creationflags=_NO_WINDOW)

    def _launch_overlay_blocking(self, file_path, title):
        """Like launch_manual_overlay, but returns the Popen handle so a background
        thread can .wait() on it before chaining the next overlay. Never call this
        from the Tk main thread.
        
        Uses a temp sentinel file to detect if user cancelled:
        - If sentinel exists after overlay exits → user cancelled (don't treat as delete)
        - If sentinel removed → user saved (overlay wrote to file_path)
        """
        env = os.environ.copy()
        env["OVERLAY_OUTPUT"] = os.path.join(self.app_dir, file_path)
        env["OVERLAY_TITLE"] = title
        
        # Create a sentinel file to detect cancel (overlay removes it on successful save)
        sentinel_path = file_path + ".editing"
        try:
            with open(sentinel_path, 'w') as f:
                f.write("editing")
        except OSError:
            pass
        
        env["OVERLAY_SENTINEL"] = sentinel_path
        return subprocess.Popen([_python_exe(), _script_path("deploy_overlay.py")], env=env, creationflags=_NO_WINDOW)

if __name__ == "__main__":
    app = ProfessionalCoCBot()
    app.mainloop()