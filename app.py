import cv2
import numpy as np
import subprocess
import time
import io
import re
import json
import os
import sys
import math
import random
import argparse
import shutil
from PIL import Image
import pytesseract

# Suppress terminal windows that flash when spawning subprocesses on Windows.
# This is critical when running as a compiled .exe — every ADB call (tap,
# screenshot, swipe) would otherwise pop open and immediately close a black
# cmd window visible to the user.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Force UTF-8 encoding for Windows consoles to avoid UnicodeEncodeError.
# line_buffering=True is required here: without it, this replacement
# stream defaults to BLOCK buffering (not line buffering), which silently
# overrides PYTHONUNBUFFERED=1 set by the launching GUI — print() calls
# queue up in an internal buffer and only reach the GUI's log pipe in
# multi-KB chunks whenever that buffer happens to fill, instead of after
# each line. That's what makes the GUI's System Logs panel look like it's
# missing lines or showing them late/out of order.
if sys.platform == "win32":
    try:
        if getattr(sys, "stdout", None) is not None and hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass
    try:
        if getattr(sys, "stderr", None) is not None and hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)
    except Exception:
        pass

# ── Paths ─────────────────────────────────────────────────────

# Dynamic base directory — works on any machine, any folder name
# When frozen by PyInstaller, data files (including templates/) are extracted
# to the temp _MEI* folder (sys._MEIPASS). sys.argv[0] always points to the
# actual .exe, so we use that as the runtime root for user-facing files.
if getattr(sys, "frozen", False):
    BASE_DIR  = os.path.dirname(os.path.abspath(sys.argv[0]))
    TEMPLATES = os.path.join(sys._MEIPASS, "templates")
else:
    BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
    TEMPLATES = os.path.join(BASE_DIR, "templates")

DEBUG_DIR = BASE_DIR   # debug images saved next to the exe / script
THRESHOLD = 0.8

# Suppress OpenCV's native C-level logger (the "[ WARN:0@...] global
# loadsave.cpp..." lines). Those writes bypass Python's sys.stderr/stdout
# entirely — they hit the raw OS file descriptor straight from the C++
# layer — so they can appear out of sequence relative to Python's own
# print() output in the GUI's log feed. Silencing them here means a
# missing/corrupt template surfaces ONLY through our own explicit
# "[!] Template missing" print() below, which IS routed through the
# line-buffered stream above and will show up correctly and in order.
try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
except Exception:
    pass

# ── ADB resolution ───────────────────────────────────────────
def _resolve_adb():
    if sys.platform != "win32":
        return "adb"

    # Persistent location so it survives across runs regardless of
    # where the user placed/moved the exe.
    dest_dir = os.path.join(os.environ.get("LOCALAPPDATA", BASE_DIR), "KrakenPrime", "adb")
    dest_adb = os.path.join(dest_dir, "adb.exe")

    if os.path.isfile(dest_adb):
        return dest_adb

    # First run (or frozen exe): copy bundled adb out of _MEIPASS.
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
        print(f"  [!] Could not extract bundled adb.exe: {e}")

    # Last resort: local folder next to the exe, then system PATH.
    local_adb = os.path.join(BASE_DIR, "adb.exe")
    if os.path.isfile(local_adb):
        return local_adb
    return "adb"

ADB = _resolve_adb()

if ADB == "adb" and shutil.which("adb") is None:
    print()
    print("  [!] ADB not found. Please re-run setup.bat, or make sure")
    print("      adb.exe is in the same folder as KrakenPrime.exe.")
    print()
    input("  Press ENTER to exit...")
    sys.exit(1)

DEVICE    = "127.0.0.1:5555"

# ── Tesseract path — auto-detected, no hardcoding ────────────
def _find_tesseract():
    import shutil
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (
                r"SOFTWARE\Tesseract-OCR",
                r"SOFTWARE\WOW6432Node\Tesseract-OCR",
            ):
                try:
                    key = winreg.OpenKey(root, subkey)
                    install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                    winreg.CloseKey(key)
                    candidate = os.path.join(install_dir, "tesseract.exe")
                    if os.path.isfile(candidate):
                        print(f"  [tesseract] Found via registry: {candidate}")
                        return candidate
                except Exception:
                    pass
    except Exception:
        pass
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
        os.path.join(os.environ.get("APPDATA", ""), "Tesseract-OCR", "tesseract.exe"),
        os.path.join(BASE_DIR, "tesseract", "tesseract.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            print(f"  [tesseract] Found at: {path}")
            return path
    found_in_path = shutil.which("tesseract")
    if found_in_path:
        print(f"  [tesseract] Found in PATH: {found_in_path}")
        return found_in_path
    print()
    print("  [!] Tesseract OCR not found on this machine.")
    print("  [!] Please install it from:")
    print("      https://github.com/UB-Mannheim/tesseract/wiki")
    print("  [!] Use the default install path when prompted.")
    print()
    input("  Press ENTER to exit...")
    sys.exit(1)

pytesseract.pytesseract.tesseract_cmd = _find_tesseract()

DEVICE_W  = 1600
DEVICE_H  = 900

CONFIG_FILE        = "config.json"
TROOP_SLOTS_FILE   = "troop_slots.json"
DEPLOY_POINTS_FILE = "deploy_points.json"
RAGE_POINTS_FILE   = "rage_points.json"
DEPLOY_PRESET_FILES = {
    "preset1": "deploy_preset_1.json",
    "preset2": "deploy_preset_2.json",
    "preset3": "deploy_preset_3.json",
    "preset4": "deploy_preset_4.json",
}
RAGE_PRESET_FILES = {
    "preset1": "rage_preset_1.json",
    "preset2": "rage_preset_2.json",
    "preset3": "rage_preset_3.json",
    "preset4": "rage_preset_4.json",
}

AIR_DEF_TEMPLATE_DIR = os.path.join(TEMPLATES, "air_defense")
AIR_DEF_THRESHOLD    = 0.75
AD_DEDUP_RADIUS      = 50

SWIPE_ANGLE    = 45
SWIPE_DISTANCE = 300
SWIPE_DURATION = 300

MAX_SKIPS         = 20
TROOP_WAIT        = 5
MIN_GOLD          = 200_000
GOLD_ROI          = (65, 163, 377, 205)
RAGE_DEPLOY_DELAY = 10   # default seconds to wait before dropping rage

ATTACK_BTN_TEMPLATES     = ["attack_btn.png","attack_btn_map.png", "attack_btn_5star.png", "attack_btn_5star_v1.png","attack_btn_5star_v2.png", "attack_btn_5star_v3.png", "attack_btn_5star_v4.png", "attack_btn_w_check.png", "attack_btn_5star_v6.png"]
END_BATTLE_BUTTONS       = ["return_home_btn.png", "claim_reward_btn.png"]
FAST_FORWARD_BTN         = "fast_forward.png"
SETUP_END_BATTLE_BUTTONS = ["end_battle_btn.png", "return_home_btn.png", "claim_reward_btn.png"]
POST_BATTLE_DISMISS      = ["okay_btn.png", "close_btn.png", "okay_btn_v2.png", "yes_btn.png"]
POPUP_DISMISS_TEMPLATES  = ["okay_btn.png"]
CHEST_TEMPLATE = "blacksmith_chest.png"
CHEST_CONTINUE = "continue_btn.png"
CHEST_SKIP     = "skip_btn.png"
CHEST_SKIP_YES = "yes_btn.png"

# Runtime state — populated from config / setup
CFG         = {}
TROOP_SLOTS = []
DEPLOY_PTS  = []
RAGE_PTS    = []   # where to drop rage spells on the map
DEPLOY_PRESETS = []
DEPLOY_PRESET_MODE = "sequence"
DEPLOY_PRESET_ORDER = []

o   = "\033[38;5;208m"
r   = "\033[0m"
g   = "\033[38;5;82m"
b   = "\033[38;5;39m"
RED = "\033[38;5;196m"

# ─────────────────────────────────────────────────────────────
# BANNER
# ─────────────────────────────────────────────────────────────



# ─────────────────────────────────────────────────────────────
# ADB HELPERS
# ─────────────────────────────────────────────────────────────

def reconnect_adb():
    print(f"  {o}[~] Reconnecting ADB...{r}")
    subprocess.run([ADB, "disconnect", DEVICE], capture_output=True, creationflags=_NO_WINDOW)
    time.sleep(1)
    result = subprocess.run([ADB, "connect", DEVICE], capture_output=True, creationflags=_NO_WINDOW)
    print(f"  {o}[~] ADB: {result.stdout.decode().strip()}{r}")
    time.sleep(2)

def restart_game():
    print(f"  {RED}[!] Critical error or timeout — Restarting Clash of Clans...{r}")
    # Force stop the app
    subprocess.run([ADB, "-s", DEVICE, "shell", "am", "force-stop", "com.supercell.clashofclans"], creationflags=_NO_WINDOW)
    time.sleep(2)
    # Start the app
    subprocess.run([ADB, "-s", DEVICE, "shell", "monkey", "-p", "com.supercell.clashofclans", "-c", "android.intent.category.LAUNCHER", "1"], creationflags=_NO_WINDOW)
    print(f"  {o}[~] Waiting 30s for game to load...{r}")
    time.sleep(30)
    # Clear any startup popups
    step_dismiss_popups()

def screenshot_cv(retries=5, delay=2):
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=10, creationflags=_NO_WINDOW
            )
            if not result.stdout or len(result.stdout) < 1000:
                raise ValueError("Empty or corrupt screenshot data")
            # Decode directly with cv2 (skips PIL round-trip + LANCZOS resize overhead)
            buf = np.frombuffer(result.stdout, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("cv2.imdecode returned None")
            if img.shape[1] != DEVICE_W or img.shape[0] != DEVICE_H:
                img = cv2.resize(img, (DEVICE_W, DEVICE_H), interpolation=cv2.INTER_LINEAR)
            return img
        except Exception as e:
            print(f"  [!] Screenshot failed (attempt {attempt}/{retries}): {e}")
            reconnect_adb()
            time.sleep(delay)
    raise RuntimeError("Failed to take screenshot after multiple retries.")

def tap(x, y, delay=1):
    subprocess.run([ADB, "-s", DEVICE, "shell", "input", "tap", str(x), str(y)], creationflags=_NO_WINDOW)
    time.sleep(delay)

def _adb_swipe(x1, y1, x2, y2, duration_ms=300):
    subprocess.run(
        [ADB, "-s", DEVICE, "shell", "input", "touchscreen",
         "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        capture_output=True, creationflags=_NO_WINDOW
    )

def center_screen():
    cx  = DEVICE_W // 2
    cy  = DEVICE_H // 2
    rad = math.radians(SWIPE_ANGLE)
    dx  = int(math.cos(rad) * SWIPE_DISTANCE)
    dy  = int(math.sin(rad) * SWIPE_DISTANCE)
    print(f"  [cam] Centering screen (45 diagonal swipe)...")
    _adb_swipe(cx, cy, cx + dx, cy - dy, SWIPE_DURATION)
    time.sleep(0.6)
    print(f"  [cam] Done.")

# ─────────────────────────────────────────────────────────────
# TEMPLATE MATCHING
# ─────────────────────────────────────────────────────────────

_template_cache: dict = {}

def _load_template(template_name):
    """Load and cache a template image. Returns None if missing."""
    if template_name not in _template_cache:
        path = os.path.join(TEMPLATES, template_name)
        tmpl = cv2.imread(path)
        if tmpl is None:
            print(f"  [!] Template missing or unreadable: {path}")
        _template_cache[template_name] = tmpl
    return _template_cache[template_name]

def find(screen, template_name, threshold=THRESHOLD):
    template = _load_template(template_name)
    if template is None:
        return None
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= threshold:
        h, w = template.shape[:2]
        return (max_loc[0] + w // 2, max_loc[1] + h // 2)
    return None

def wait_for(template_name, timeout=30, interval=2, label=None):
    label = label or template_name
    print(f"  Waiting for [{label}]...")
    start = time.time()
    while time.time() - start < timeout:
        screen = screenshot_cv()
        pos = find(screen, template_name)
        if pos:
            print(f"  Found [{label}] at {pos}")
            return screen, pos
        time.sleep(interval)
    print(f"  [!] Timed out waiting for [{label}]")
    return None, None

# ─────────────────────────────────────────────────────────────
# CONFIG  —  load / save / wizard
# ─────────────────────────────────────────────────────────────

def load_config():
    global CFG
    if not os.path.exists(CONFIG_FILE):
        return False
    try:
        with open(CONFIG_FILE) as f:
            CFG = json.load(f)
        return True
    except Exception as e:
        print(f"  [!] Could not read {CONFIG_FILE}: {e}")
        return False

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(CFG, f, indent=2)
    print(f"  {g}[OK] Config saved -> {CONFIG_FILE}{r}")

def _consume_auto_input(prompt, default=""):
    if sys.stdin.isatty():
        return input(prompt).strip()

    if not hasattr(sys, "_auto_input_queue"):
        sys._auto_input_queue = []
        try:
            while True:
                line = sys.stdin.readline()
                if line == "":
                    break
                sys._auto_input_queue.append(line.rstrip("\n"))
        except Exception:
            pass

    if sys._auto_input_queue:
        return sys._auto_input_queue.pop(0)

    if default is not None:
        return str(default)
    raise EOFError("No more automatic responses available")


def _ask_int(prompt, lo=0, hi=999):
    while True:
        try:
            raw = _consume_auto_input(prompt, "")
            if raw == "":
                raise ValueError
            val = int(raw)
            if lo <= val <= hi:
                return val
            print(f"      Please enter a number between {lo} and {hi}.")
        except ValueError:
            print("      Invalid input — please enter a whole number.")
        except EOFError:
            print("      No more input available — using default value 0.")
            return 0


def _ask_yes_no(prompt):
    while True:
        try:
            ans = _consume_auto_input(prompt, "y").strip().lower()
            if ans in ("y", "yes"):
                return True
            if ans in ("n", "no"):
                return False
            print("      Please answer y or n.")
        except EOFError:
            print("      No more input available — assuming yes.")
            return True

def run_config_wizard():
    global CFG
    print(f"\n{o}  ╔══════════════════════════════════════════╗")
    print(f"  ║        FIRST-TIME SETUP WIZARD           ║")
    print(f"  ╚══════════════════════════════════════════╝{r}\n")
    print("  Answer a few questions to configure the bot.\n")

    print(f"  {b}── Troops ──────────────────────────────────{r}")
    print("  How many DIFFERENT troop types are in your bar?")
    print("  (e.g. if you only bring Dragons -> answer 1)")
    num_troop_slots = _ask_int("  Number of troop slot icons in the bar: ", 1, 10)
    print()
    print("  How many troops total do you deploy per attack?")
    print("  (e.g. 10 Dragons -> answer 10)")
    num_troops_total = _ask_int("  Total number of troops to deploy: ", 1, 200)

    print(f"\n  {b}── Heroes ──────────────────────────────────{r}")
    num_heroes = _ask_int("  How many heroes will you deploy? (0 if none): ", 0, 6)

    print(f"\n  {b}── Lightning Spells ────────────────────────{r}")
    num_spells = _ask_int("  How many lightning spells do you have? (0 if none): ", 0, 20)
    spells_per_ad = 0
    if num_spells > 0:
        spells_per_ad = _ask_int("  How many spells to drop per air defense? (e.g. 4): ", 1, num_spells)

    print(f"\n  {b}── Rage Spells ─────────────────────────────{r}")
    has_rage   = _ask_yes_no("  Do you have rage spells? (y/n): ")
    num_rage   = 0
    rage_delay = RAGE_DEPLOY_DELAY
    if has_rage:
        num_rage   = _ask_int("  How many rage spells do you have?: ", 1, 10)
        rage_delay = _ask_int("  Seconds to wait after troops deploy before dropping rage? (e.g. 10): ", 0, 60)

    print(f"\n  {b}── Loot filter ─────────────────────────────{r}")
    min_gold = _ask_int("  Minimum gold to attack (e.g. 200000): ", 0, 10_000_000)

    CFG = {
        "num_troop_slots":  num_troop_slots,
        "num_troops_total": num_troops_total,
        "num_heroes":       num_heroes,
        "num_spells":       num_spells,
        "spells_per_ad":    spells_per_ad,
        "has_rage":         has_rage,
        "num_rage":         num_rage,
        "rage_delay":       rage_delay,
        "min_gold":         min_gold,
        "deploy_preset_mode": "sequence",
        "deploy_preset_order": "preset1,preset2,preset3",
        "setup_complete":   False,
    }
    save_config()

    print(f"\n  {g}Configuration saved!{r}")
    print(f"    Troop slot icons : {num_troop_slots}")
    print(f"    Troops to deploy : {num_troops_total}")
    print(f"    Heroes           : {num_heroes}")
    print(f"    Lightning spells : {num_spells}  ({spells_per_ad} per AD)")
    print(f"    Rage spells      : {num_rage if has_rage else 'None'}  (drop after {rage_delay}s)")
    print(f"    Min gold         : {min_gold:,}")

# ─────────────────────────────────────────────────────────────
# SLOT INDEX HELPERS
# ─────────────────────────────────────────────────────────────
#
#   Bar pin order during setup:
#   [0 .. num_troop_slots-1]              troop icons
#   [num_troop_slots .. +num_heroes-1]    hero icons
#   [+1 if lightning spells > 0]          lightning spell slot
#   [+1 if has_rage]                      rage spell slot
#

def _spell_slot_index():
    if not TROOP_SLOTS or CFG.get("num_spells", 0) == 0:
        return None
    idx = CFG.get("num_troop_slots", 1) + CFG.get("num_heroes", 0)
    if idx >= len(TROOP_SLOTS):
        print(f"  [AD] Expected lightning slot at index {idx} but only {len(TROOP_SLOTS)} slot(s) pinned.")
        return None
    return idx


def _load_points_from_file(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return [(p["x"], p["y"]) for p in data.get("points", data.get("slots", []))]
    except Exception as e:
        print(f"  [!] Could not read {path}: {e}")
        return None


def load_deploy_presets():
    global DEPLOY_PRESETS, DEPLOY_PRESET_MODE, DEPLOY_PRESET_ORDER, DEPLOY_PTS, RAGE_PTS
    DEPLOY_PRESETS = []
    DEPLOY_PRESET_MODE = str(CFG.get("deploy_preset_mode", "sequence")).lower()

    # ── Preset Mode disabled -> normal deployment from deploy_points.json + rage_points.json ──
    if not CFG.get("deploy_preset_enabled", False):
        DEPLOY_PRESET_ORDER = []
        ok = load_deploy_points()
        if CFG.get("has_rage", False):
            load_rage_points()
        return ok

    raw_order = CFG.get("deploy_preset_order", "preset1,preset2,preset3")
    DEPLOY_PRESET_ORDER = [p.strip() for p in str(raw_order).split(",") if p.strip()]
    if not DEPLOY_PRESET_ORDER:
        DEPLOY_PRESET_ORDER = ["preset1"]

    for preset_id in DEPLOY_PRESET_ORDER:
        if preset_id not in DEPLOY_PRESET_FILES:
            continue
        path = DEPLOY_PRESET_FILES[preset_id]
        points = _load_points_from_file(path)
        rage_path = RAGE_PRESET_FILES.get(preset_id)
        rage_points = _load_points_from_file(rage_path) if rage_path else None
        if points:
            DEPLOY_PRESETS.append({
                "id": preset_id, "name": preset_id,
                "points": points, "rage_points": rage_points or [],
            })
            print(f"  [deploy] Loaded preset {preset_id} from {path}"
                  + (f" (+{len(rage_points)} rage pt(s))" if rage_points else ""))
            continue
        if preset_id == "preset1":
            fallback_points = _load_points_from_file(DEPLOY_POINTS_FILE)
            if fallback_points:
                fallback_rage = _load_points_from_file(RAGE_POINTS_FILE) or []
                DEPLOY_PRESETS.append({
                    "id": preset_id, "name": preset_id,
                    "points": fallback_points, "rage_points": fallback_rage,
                })
                print(f"  [deploy] Loaded fallback preset {preset_id} from {DEPLOY_POINTS_FILE}")

    if DEPLOY_PRESETS:
        print(f"  [deploy] Loaded {len(DEPLOY_PRESETS)} deploy preset(s) ({DEPLOY_PRESET_MODE})")
        return True

    if load_deploy_points():
        if CFG.get("has_rage", False):
            load_rage_points()
        DEPLOY_PRESETS = [{"id": "preset1", "name": "preset1", "points": DEPLOY_PTS, "rage_points": RAGE_PTS}]
        DEPLOY_PRESET_ORDER = ["preset1"]
        return True
    return False


def get_deploy_points_for_round(round_num):
    global DEPLOY_PTS, RAGE_PTS
    if DEPLOY_PRESETS:
        if DEPLOY_PRESET_MODE == "random":
            preset = random.choice(DEPLOY_PRESETS)
        else:
            preset = DEPLOY_PRESETS[(round_num - 1) % len(DEPLOY_PRESETS)]
        DEPLOY_PTS = preset["points"]
        RAGE_PTS = preset.get("rage_points") or []
        print(f"  [deploy] Using preset {preset['id']} for raid {round_num}")
        return DEPLOY_PTS
    return DEPLOY_PTS

def _rage_slot_index():
    if not TROOP_SLOTS or not CFG.get("has_rage", False):
        return None
    idx = (CFG.get("num_troop_slots", 1)
           + CFG.get("num_heroes", 0)
           + (1 if CFG.get("num_spells", 0) > 0 else 0))
    if idx >= len(TROOP_SLOTS):
        print(f"  [rage] Expected rage slot at index {idx} but only {len(TROOP_SLOTS)} slot(s) pinned.")
        return None
    return idx

# ─────────────────────────────────────────────────────────────
# TROOP SLOTS  —  load / save
# ─────────────────────────────────────────────────────────────

def load_troop_slots():
    global TROOP_SLOTS
    if not os.path.exists(TROOP_SLOTS_FILE):
        return False
    try:
        with open(TROOP_SLOTS_FILE) as f:
            data = json.load(f)
        TROOP_SLOTS = [(p["x"], p["y"]) for p in data.get("slots", [])]
        print(f"  [slots] Loaded {len(TROOP_SLOTS)} troop slot(s) from {TROOP_SLOTS_FILE}")
        return True
    except Exception as e:
        print(f"  [!] Could not read {TROOP_SLOTS_FILE}: {e}")
        return False

def save_troop_slots(points_dev):
    data = {"slots": [{"x": x, "y": y} for x, y in points_dev]}
    with open(TROOP_SLOTS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  {g}[OK] Troop slots saved -> {TROOP_SLOTS_FILE}{r}")

# ─────────────────────────────────────────────────────────────
# DEPLOY POINTS  —  load / save
# ─────────────────────────────────────────────────────────────

def load_deploy_points():
    global DEPLOY_PTS
    if not os.path.exists(DEPLOY_POINTS_FILE):
        return False
    try:
        with open(DEPLOY_POINTS_FILE) as f:
            data = json.load(f)
        DEPLOY_PTS = [(p["x"], p["y"]) for p in data.get("points", [])]
        print(f"  [deploy] Loaded {len(DEPLOY_PTS)} deploy point(s) from {DEPLOY_POINTS_FILE}")
        return True
    except Exception as e:
        print(f"  [!] Could not read {DEPLOY_POINTS_FILE}: {e}")
        return False

# ─────────────────────────────────────────────────────────────
# RAGE POINTS  —  load / save
# ─────────────────────────────────────────────────────────────

def load_rage_points():
    global RAGE_PTS
    if not os.path.exists(RAGE_POINTS_FILE):
        return False
    try:
        with open(RAGE_POINTS_FILE) as f:
            data = json.load(f)
        RAGE_PTS = [(p["x"], p["y"]) for p in data.get("points", [])]
        print(f"  [rage] Loaded {len(RAGE_PTS)} rage point(s) from {RAGE_POINTS_FILE}")
        return True
    except Exception as e:
        print(f"  [!] Could not read {RAGE_POINTS_FILE}: {e}")
        return False

# ─────────────────────────────────────────────────────────────
# DEPLOY OVERLAY  — launch as subprocess, wait for json output
# ─────────────────────────────────────────────────────────────

def _python_exe() -> str:
    """Return the real python.exe even when running inside a PyInstaller .exe."""
    if not getattr(sys, "frozen", False):
        return sys.executable

    exe_dir = os.path.dirname(sys.executable)
    candidate = os.path.join(exe_dir, "python.exe")
    if os.path.isfile(candidate):
        return candidate

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

    for path in [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python311", "python.exe"),
        r"C:\Python311\python.exe",
        r"C:\Program Files\Python311\python.exe",
    ]:
        if os.path.isfile(path):
            return path

    import shutil
    found = shutil.which("python")
    return found if found else "python"


def _script_path(filename: str) -> str:
    """Resolve a bundled script path for both frozen and dev modes."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)


def launch_overlay(output_file, title_hint=""):
    env = os.environ.copy()
    env["OVERLAY_OUTPUT"] = output_file
    env["OVERLAY_TITLE"]  = title_hint
    if os.path.exists(output_file):
        os.remove(output_file)
    print(f"\n  {b}[overlay] Launching visual planner — pin your points then click Save & Close.{r}")
    proc = subprocess.Popen(
        [_python_exe(), _script_path("deploy_overlay.py")],
        env=env,
        creationflags=_NO_WINDOW
    )
    proc.wait()
    if os.path.exists(output_file):
        print(f"  {g}[overlay] Points saved to {output_file}{r}")
        return True
    else:
        print(f"  {RED}[overlay] No file saved — overlay closed without saving.{r}")
        return False

# ─────────────────────────────────────────────────────────────
# SETUP BATTLE  — one-time guided battle to pin coords
# ─────────────────────────────────────────────────────────────

def run_setup_battle():
    print(f"\n{o}  ╔══════════════════════════════════════════╗")
    print(f"  ║          SETUP BATTLE (one-time)         ║")
    print(f"  ╚══════════════════════════════════════════╝{r}")
    print("  We will enter a battle so you can pin coordinates.")
    print("  NO troops will be deployed automatically yet.\n")

    # Check if running from GUI (no interactive terminal)
    if not sys.stdin.isatty():
        print("  [AUTO] Running from GUI — auto-proceeding...")
        time.sleep(2)
    else:
        input("  Press ENTER when you are on the HOME VILLAGE screen... ")

    step_dismiss_popups()

    print("\n  [S1] Tapping Attack button...")
    print("  [AUTO] This may take a few moments. Please wait...")
    time.sleep(3)
    if not step_open_attack():
        print(f"  {RED}[!] Could not find Attack button. Aborting setup.{r}")
        return False

    print("  [S2] Tapping Find a Match...")
    if not step_find_match():
        print(f"  {RED}[!] Could not find Find a Match. Aborting setup.{r}")
        return False

    print("  [S3] Confirming attack (army screen)...")
    if not step_confirm_attack():
        print(f"  {RED}[!] Could not confirm attack. Aborting setup.{r}")
        return False

    print("\n  [S4] Waiting for battle screen to load...")
    print("  [AUTO] The visual planner will open automatically...")
    time.sleep(8)

    # ── Pin troop bar slots ───────────────────────────────────
    has_rage      = CFG.get("has_rage", False)
    num_lightning = CFG.get("num_spells", 0)
    expected = (f"{CFG['num_troop_slots']} troop slot(s) + "
                f"{CFG['num_heroes']} hero(es)"
                + (f" + 1 lightning slot" if num_lightning > 0 else "")
                + (f" + 1 rage slot"      if has_rage          else ""))

    print(f"\n  {b}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{r}")
    print(f"  {b}STEP: Pin your TROOP BAR slots{r}")
    print(f"  {b}Order: troops -> heroes -> lightning -> rage{r}")
    print(f"  {b}Total expected: {expected}{r}")
    print(f"  {b}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{r}\n")

    print("  [AUTO] Opening visual planner for troop slot pinning...")
    saved = launch_overlay(TROOP_SLOTS_FILE, title_hint="Pin TROOP BAR SLOTS (troops > heroes > lightning > rage)")
    if not saved:
        print(f"  {RED}[!] Troop slots not saved. Aborting setup.{r}")
        _end_battle_now()
        return False
    print("  [OK] Troop slots saved!")

    with open(TROOP_SLOTS_FILE) as f:
        raw = json.load(f)
    if "points" in raw and "slots" not in raw:
        raw["slots"] = raw.pop("points")
        with open(TROOP_SLOTS_FILE, "w") as f:
            json.dump(raw, f, indent=2)
    load_troop_slots()

    # ── Center screen ─────────────────────────────────────────
    print("\n  [S5] Centering/zooming battle screen (45 swipe)...")
    center_screen()
    time.sleep(1.5)

    # ── Pin deploy positions ──────────────────────────────────
    print(f"\n  {b}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{r}")
    print(f"  {b}STEP: Pin where you want TROOPS DEPLOYED{r}")
    print(f"  {b}Click on the map where troops should drop.{r}")
    print(f"  {b}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{r}\n")

    print("  [AUTO] Opening visual planner for deployment positions...")
    saved = launch_overlay(DEPLOY_POINTS_FILE, title_hint="Pin DEPLOY POSITIONS on the map")
    if not saved:
        print(f"  {RED}[!] Deploy points not saved. Aborting setup.{r}")
        _end_battle_now()
        return False
    load_deploy_points()
    print("  [OK] Deploy points saved!")

    # ── Pin rage positions (only if rage configured) ──────────
    if has_rage:
        print(f"\n  {b}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{r}")
        print(f"  {b}STEP: Pin where you want RAGE SPELLS dropped{r}")
        print(f"  {b}Pin {CFG['num_rage']} point(s) — one per rage spell{r}")
        print(f"  {b}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{r}\n")

        print("  [AUTO] Opening visual planner for rage spell positions...")
        saved = launch_overlay(RAGE_POINTS_FILE, title_hint="Pin RAGE SPELL positions on the map")
        if not saved:
            print(f"  {RED}[!] Rage points not saved. Aborting setup.{r}")
            _end_battle_now()
            return False
        load_rage_points()
        print("  [OK] Rage points saved!")

    # ── End setup battle ──────────────────────────────────────
    print("\n  [S6] Ending setup battle and returning home...")
    _end_battle_now()

    CFG["setup_complete"] = True
    save_config()

    print(f"\n  {g}╔══════════════════════════════════════════╗{r}")
    print(f"  {g}║       SETUP COMPLETE - READY TO FARM!    ║{r}")
    print(f"  {g}╚══════════════════════════════════════════╝{r}")
    print(f"    Troop slots : {len(TROOP_SLOTS)}")
    print(f"    Deploy pts  : {len(DEPLOY_PTS)}")
    if has_rage:
        print(f"    Rage pts    : {len(RAGE_PTS)}")
    print(f"\n  {g}click START BOT in the GUI!{r}")
    return True

def _end_battle_now():
    print("  [end] Looking for End Battle button (setup exit)...")
    deadline = time.time() + 30
    while time.time() < deadline:
        screen = screenshot_cv()
        for btn in SETUP_END_BATTLE_BUTTONS:
            pos = find(screen, btn)
            if pos:
                print(f"  [end] Tapping [{btn}]...")
                tap(*pos, delay=3)
                time.sleep(1)
                screen2 = screenshot_cv()
                for confirm in ["okay_btn.png", "yes_btn.png"]:
                    cp = find(screen2, confirm)
                    if cp:
                        tap(*cp, delay=2)
                step_dismiss_post_battle()
                return True
        time.sleep(2)
    print("  [!] Could not find End Battle button — please tap it manually.")
    if sys.stdin.isatty():
        input("  Press ENTER once you are back on the home village screen... ")
    else:
        print("  [AUTO] Continuing without waiting because input is not interactive.")
    return False

# ─────────────────────────────────────────────────────────────
# POPUPS / CHEST
# ─────────────────────────────────────────────────────────────

def step_open_chest(screen):
    pos = find(screen, CHEST_TEMPLATE)
    if not pos:
        return False
    print("  [chest] Blacksmith chest — opening...")
    tap(*pos, delay=1.5)
    for i in range(6):
        screen2 = screenshot_cv()
        skip_pos = find(screen2, CHEST_SKIP)
        if skip_pos:
            tap(*skip_pos, delay=1.0)
            screen3 = screenshot_cv()
            yes_pos = find(screen3, CHEST_SKIP_YES)
            tap(*(yes_pos if yes_pos else (800, 450)), delay=2.0)
            return True
        cont_pos = find(screen2, CHEST_CONTINUE)
        if cont_pos:
            tap(*cont_pos, delay=2.0)
            return True
        tap(800, 450, delay=1.0)
    return True

def step_dismiss_popups():
    dismissed = 0
    deadline = time.time() + 10
    while time.time() < deadline:
        screen = screenshot_cv()
        if step_open_chest(screen):
            dismissed += 1
            continue
        found = False
        for tmpl in POPUP_DISMISS_TEMPLATES:
            pos = find(screen, tmpl)
            if pos:
                print(f"  [popup] Dismissing [{tmpl}]...")
                tap(*pos, delay=1.5)
                dismissed += 1
                found = True
                break
        if not found:
            break
    if dismissed:
        print(f"  [popup] Dismissed {dismissed} popup(s).")


def step_dismiss_post_battle():
    clear_count = 0
    deadline = time.time() + 45
    while time.time() < deadline:
        screen = screenshot_cv()
        if step_open_chest(screen):
            clear_count = 0
            continue
        dismissed = False
        for tmpl in POST_BATTLE_DISMISS:
            pos = find(screen, tmpl)
            if pos:
                print(f"  [post] Tapping [{tmpl}]...")
                tap(*pos, delay=1.5)
                clear_count = 0
                dismissed = True
                break
        if not dismissed:
            clear_count += 1
            if clear_count >= 3:
                print("  [post] All popups cleared!")
                return
            time.sleep(1.5)

# ─────────────────────────────────────────────────────────────
# FARM STEPS
# ─────────────────────────────────────────────────────────────

def step_open_attack():
    print("[1] Pressing Attack button...")
    start = time.time()
    while time.time() - start < 30:
        screen = screenshot_cv()
        for tmpl in ATTACK_BTN_TEMPLATES:
            pos = find(screen, tmpl)
            if pos:
                print(f"  Found attack button at {pos}")
                tap(*pos, delay=2)
                return True
        time.sleep(1)   # was 2 — tighter poll since screenshot itself takes ~0.5s
    print("  [!] Could not find Attack button.")
    return False

def step_find_match():
    print("[2] Pressing Find a Match...")
    screen, pos = wait_for("find_match_btn.png", timeout=15, label="Find a Match")
    if pos:
        tap(*pos, delay=3)
        return True
    return False

def step_confirm_attack():
    print("[3] Pressing Attack! (confirmation)...")
    screen, pos = wait_for("attack_confirm_btn.png", timeout=15, label="Attack! confirm")
    if pos:
        tap(*pos, delay=4)
        return True
    return False

def read_gold(screen):
    x1, y1, x2, y2 = GOLD_ROI
    roi    = screen[y1:y2, x1:x2]
    gray   = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, thr = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    kernel = np.ones((2, 2), np.uint8)
    thr    = cv2.morphologyEx(thr, cv2.MORPH_OPEN, kernel)
    scaled = cv2.resize(thr, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(os.path.join(DEBUG_DIR, "debug_gold_roi.png"), scaled)
    cfg = '--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789'
    raw = pytesseract.image_to_string(scaled, config=cfg)
    digits = re.sub(r'\s+', '', raw.strip())
    try:
        return int(digits)
    except ValueError:
        print(f"  [OCR] Could not parse gold: {repr(raw.strip())}")
        return 0

def step_scout_and_skip():
    print("[4] Scouting base...")
    min_gold = CFG.get("min_gold", MIN_GOLD)
    skipped  = 0
    while skipped < MAX_SKIPS:
        screen   = screenshot_cv()
        next_btn = find(screen, "next_btn.png")
        if not next_btn:
            time.sleep(1)   # was 2; next button usually appears quickly
            continue
        gold = read_gold(screen)
        print(f"  Gold: {gold:,}  (need {min_gold:,})")
        if gold >= min_gold:
            print(f"  {g}Good base! (skipped {skipped}){r}")
            center_screen()
            return True
        print(f"  Loot too low — skipping... ({skipped + 1}/{MAX_SKIPS})")
        tap(*next_btn, delay=2)   # was 3; matchmaking usually resolves in ~2s
        skipped += 1
    print("  [!] Max skips reached — attacking anyway.")
    center_screen()
    return True

# ── Air defense detection ─────────────────────────────────────

_ad_templates: list | None = None  # cached list of (filename, np.ndarray)

def _load_ad_templates():
    global _ad_templates
    if _ad_templates is not None:
        return _ad_templates
    _ad_templates = []
    if not os.path.exists(AIR_DEF_TEMPLATE_DIR):
        return _ad_templates
    ref_files = [f for f in os.listdir(AIR_DEF_TEMPLATE_DIR)
                 if f.lower().endswith((".png", ".jpg"))]
    for ref_file in ref_files:
        tmpl = cv2.imread(os.path.join(AIR_DEF_TEMPLATE_DIR, ref_file), cv2.IMREAD_COLOR)
        if tmpl is not None:
            _ad_templates.append((ref_file, tmpl))
    return _ad_templates

def find_air_defenses(screen):
    templates = _load_ad_templates()
    if not templates:
        return []
    print(f"  [AD] Scanning with {len(templates)} template(s)...")
    PLAY_X1, PLAY_Y1, PLAY_X2, PLAY_Y2 = 60, 20, 1550, 740
    playfield = screen[PLAY_Y1:PLAY_Y2, PLAY_X1:PLAY_X2]
    found = []
    for ref_file, template in templates:
        for scale in [0.8, 0.9, 1.0, 1.1, 1.2]:
            h, w = template.shape[:2]
            new_w, new_h = int(w * scale), int(h * scale)
            if new_w < 10 or new_h < 10:
                continue
            resized = cv2.resize(template, (new_w, new_h))
            result  = cv2.matchTemplate(playfield, resized, cv2.TM_CCOEFF_NORMED)
            locs    = np.where(result >= AIR_DEF_THRESHOLD)
            for pt in zip(*locs[::-1]):
                cx = pt[0] + new_w // 2 + PLAY_X1
                cy = pt[1] + new_h // 2 + PLAY_Y1
                if all(abs(cx - d["pos"][0]) > AD_DEDUP_RADIUS or
                       abs(cy - d["pos"][1]) > AD_DEDUP_RADIUS for d in found):
                    found.append({"pos": (cx, cy)})
                    print(f"  [AD] [{ref_file}] scale={scale} at ({cx},{cy})")
    debug = screen.copy()
    for d in found:
        cx, cy = d["pos"]
        cv2.circle(debug, (cx, cy), 25, (0, 0, 255), 3)
        cv2.putText(debug, "AD", (cx - 15, cy - 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    cv2.imwrite(os.path.join(DEBUG_DIR, "debug_ad_positions.png"), debug)
    if found:
        print(f"  [AD] {len(found)} air defense(s) found.")
    else:
        print("  [AD] None found.")
    return found

def step_lightning_air_defenses(screen):
    num_spells = CFG.get("num_spells", 0)
    per_ad     = CFG.get("spells_per_ad", 4)
    spell_slot = _spell_slot_index()

    if num_spells == 0:
        print("  [AD] No spells configured - skipping lightning phase.")
        return
    if spell_slot is None:
        print("  [AD] No spell slot pinned - skipping lightning phase.")
        return

    print(f"[5a] Lightning phase - {num_spells} spell(s) total, {per_ad} per AD, slot index {spell_slot}...")
    air_defs = find_air_defenses(screen)
    if not air_defs:
        print("  [AD] No air defenses detected — skipping.")
        return

    spells_left = num_spells
    for i, ad in enumerate(air_defs):
        if spells_left <= 0:
            break
        x, y    = ad["pos"]
        to_drop = min(per_ad, spells_left)
        print(f"  [AD] Target {i+1} at ({x},{y}) — dropping {to_drop} spell(s)")
        _select_slot(spell_slot)
        time.sleep(2.5)
        for n in range(to_drop):
            print(f"  [AD]    Spell {n+1}/{to_drop} ({spells_left} left)")
            tap(x, y, delay=2.0)
            spells_left -= 1
            if spells_left <= 0:
                break
        time.sleep(4.0)
    print("  [AD] Lightning phase complete.")

def _select_slot(slot_index):
    if slot_index >= len(TROOP_SLOTS):
        print(f"  [!] slot_index {slot_index} out of range")
        return
    x, y = TROOP_SLOTS[slot_index]
    print(f"  Selecting slot {slot_index} at ({x},{y})")
    tap(x, y, delay=0.4)

def step_deploy_troops(round_num):
    """
    Slot order:
      0 .. num_troop_slots-1           troop icons  (multi-tap across points)
      num_troop_slots .. +num_heroes-1 hero icons   (single tap each)
      lightning slot                   SKIPPED (used in step 5a)
      rage slot                        SKIPPED (used in step 5c)
    """
    print("[5b] Deploying troops...")
    center_screen()

    num_troop_slots  = CFG.get("num_troop_slots", 1)
    num_troops_total = CFG.get("num_troops_total", 10)
    num_heroes       = CFG.get("num_heroes", 0)

    troop_slot_range = range(0, num_troop_slots)
    hero_slot_range  = range(num_troop_slots, num_troop_slots + num_heroes)

    pts = get_deploy_points_for_round(round_num)
    if not pts:
        pts = _fallback_line()
        print("  [!] No deploy points found - using fallback diagonal line.")

    # Deploy troops
    for slot in troop_slot_range:
        _select_slot(slot)
        time.sleep(0.8)
        taps_needed = num_troops_total // num_troop_slots
        if slot == 0:
            taps_needed += num_troops_total % num_troop_slots
        print(f"  Slot {slot} (troop) -> {taps_needed} tap(s) across {len(pts)} point(s)")
        taps_done = 0
        while taps_done < taps_needed:
            for x, y in pts:
                if taps_done >= taps_needed:
                    break
                tap(x, y, delay=0.15)
                taps_done += 1
        time.sleep(0.3)

    # Deploy heroes — single tap each
    for slot in hero_slot_range:
        _select_slot(slot)
        time.sleep(0.5)
        x, y = pts[0]
        print(f"  Slot {slot} (hero) -> single tap at ({x},{y})")
        tap(x, y, delay=0.5)
        time.sleep(0.3)

    print("  Troops deployed!")

# ── Rage spell deployment ─────────────────────────────────────

def step_deploy_rage():
    """
    Step 5c — wait for troops to walk into position, then drop
    rage spells one by one at the pinned rage positions.
    """
    if not CFG.get("has_rage", False):
        return

    num_rage   = CFG.get("num_rage", 0)
    rage_delay = CFG.get("rage_delay", RAGE_DEPLOY_DELAY)
    rage_slot  = _rage_slot_index()

    if num_rage == 0:
        print("  [rage] No rage spells configured - skipping.")
        return
    if rage_slot is None:
        print("  [rage] No rage slot pinned - skipping.")
        return
    if not RAGE_PTS:
        print("  [rage] No rage positions pinned - skipping.")
        return

    print(f"[5c] Rage phase - waiting {rage_delay}s for troops to move into position...")
    time.sleep(rage_delay)

    print(f"  [rage] Dropping {num_rage} rage spell(s) one by one...")
    for i in range(num_rage):
        pt_index = i % len(RAGE_PTS)   # cycle through points if fewer than spells
        x, y = RAGE_PTS[pt_index]
        print(f"  [rage] Rage {i+1}/{num_rage} -> slot {rage_slot}, dropping at ({x},{y})")
        _select_slot(rage_slot)
        time.sleep(1.5)
        tap(x, y, delay=2.0)
        print(f"  [rage] Rage {i+1} dropped.")

    print("  [rage] All rage spells deployed!")

def _fallback_line():
    start, end, steps = (847, 42), (1066, 161), 13
    x1, y1 = start
    x2, y2 = end
    return [(int(x1 + i / steps * (x2 - x1)),
             int(y1 + i / steps * (y2 - y1))) for i in range(steps + 1)]

def step_wait_battle_end():
    print("[6] Waiting for battle to end...")
    start = time.time()
    while time.time() - start < 600:
        screen = screenshot_cv()

        # Check for end-battle buttons
        for btn in END_BATTLE_BUTTONS:
            pos = find(screen, btn)
            if pos:
                print(f"  Found [{btn}] — tapping...")
                tap(*pos, delay=3)
                print("[6b] Clearing post-battle popups...")
                step_dismiss_post_battle()
                return True

        # Fast-forward button — tap to speed up; reuse same screenshot
        ff_pos = find(screen, FAST_FORWARD_BTN)
        if ff_pos:
            print("  [ff] Fast-forward button found — tapping...")
            tap(*ff_pos, delay=1)

        time.sleep(2)   # was 3 — slightly tighter poll
    print("  [!] Battle timed out.")
    return False

def step_retrain_troops():
    #print("[7] Retraining troops (Quick Train)...")
    # 1. Tap the Train Troops button (bottom left)
    # 2. Tap the Quick Train tab
    # 3. Tap the Train button for the first slot
    # 4. Close the training window
    
    # We'll use templates for these buttons.
    # Note: These coordinates/templates are based on standard layouts.
    """
    # 1. Open Train Menu
    pos = find(screenshot_cv(), "train_btn.png")
    if not pos:
        # Fallback to coordinate if template fails
        tap(50, 800, delay=2) 
    else:
        tap(*pos, delay=2)
    
    # 2. Go to Quick Train tab
    pos = find(screenshot_cv(), "quick_train_tab.png")
    if pos:
        tap(*pos, delay=1.5)
    
    # 3. Tap Train button (assuming slot 1)
    pos = find(screenshot_cv(), "train_now_btn.png")
    if pos:
        tap(*pos, delay=1.5)
    
    # 4. Close menu
    pos = find(screenshot_cv(), "close_btn.png")
    if pos:
        tap(*pos, delay=1.5)
    else:
        tap(1550, 50, delay=1.5) # Fallback top-right X
    """
    print(f"  [7] Troops queued. Waiting {TROOP_WAIT}s for retraining...")
    time.sleep(TROOP_WAIT)

# ─────────────────────────────────────────────────────────────
# FARM LOOP
# ─────────────────────────────────────────────────────────────

def run_farm_loop():
    print(f"\n{o}  ╔══════════════════════════════════════════╗")
    print(f"  ║           FARMING LOOP STARTED           ║")
    print(f"  ╚══════════════════════════════════════════╝{r}\n")

    round_num = 0
    while True:
        round_num += 1
        print(f"\n{o}{'=' * 45}")
        print(f"  RAID {round_num}")
        print(f"{'=' * 45}{r}")

        step_dismiss_popups()

        if not step_open_attack():
            print("  Attack button not found — might be stuck. Restarting game...")
            restart_game()
            continue

        if not step_find_match():
            print("  Find Match button not found. Returning home...")
            tap(1550, 50, delay=2) # Close menu
            continue

        if not step_confirm_attack():
            print("  Confirm Attack button not found. Returning home...")
            tap(1550, 50, delay=2) # Close menu
            continue

        if not step_scout_and_skip():
            time.sleep(10)
            continue

        # 5a - Lightning spells on air defenses
        time.sleep(1.5)
        scout = screenshot_cv()
        cv2.imwrite(os.path.join(DEBUG_DIR, "debug_battle_screen.png"), scout)
        step_lightning_air_defenses(scout)

        # 5b - Deploy dragons + heroes
        step_deploy_troops(round_num)

        # 5c - Rage spells after delay
        step_deploy_rage()

        # 6 - Wait for battle end
        step_wait_battle_end()

        # 7 - Retrain and wait
        step_retrain_troops()

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="COC Farming Bot — THE KRAKEN")
    parser.add_argument("--reconfigure", action="store_true",
                        help="Wipe settings and re-run the setup wizard")
    parser.add_argument("--setup-only", action="store_true",
                        help="Run setup wizard then exit (no farming)")
    parser.add_argument("--farm-only", action="store_true",
                        help="Skip setup, go straight to farming loop")
    args = parser.parse_args()

    print(f"{o}  Make sure LDPlayer is open and COC is on the main village screen.{r}\n")

    if args.reconfigure:
        for f in [CONFIG_FILE, TROOP_SLOTS_FILE, DEPLOY_POINTS_FILE, RAGE_POINTS_FILE]:
            if os.path.exists(f):
                os.remove(f)
                print(f"  [reset] Deleted {f}")
        print()

    config_exists = load_config()
    if not config_exists or args.reconfigure:
        if not args.farm_only: # Only run wizard if not in farm-only mode (which is used by GUI)
            run_config_wizard()
        else:
            print(f"  {o}[!] Config missing! Please save configuration in the GUI first.{r}")
            sys.exit(1)

    slots_ok  = load_troop_slots()
    deploy_ok = load_deploy_presets()
    if CFG.get("has_rage", False):
        rage_ok = bool(RAGE_PTS) or any(p.get("rage_points") for p in DEPLOY_PRESETS)
    else:
        rage_ok = True

    setup_done = CFG.get("setup_complete", False) and slots_ok and deploy_ok and rage_ok

    if not setup_done and not args.farm_only:
        print(f"\n  {b}Setup coordinates not found — starting guided setup battle.{r}")
        print(f"  {b}Starting in 5 seconds...{r}\n")
        time.sleep(5)
        ok = run_setup_battle()
        if not ok:
            print(f"\n  {RED}Setup failed. Please fix the issue and run again.{r}")
            sys.exit(1)
    elif args.farm_only and not setup_done:
        print(f"  {RED}[!] --farm-only used but setup is incomplete.{r}")
        print(f"  {RED}    Run without --farm-only first to complete setup.{r}")
        sys.exit(1)
    else:
        print(f"  {g}[OK] Setup already complete — loaded saved coordinates.{r}")

    if args.setup_only:
        print(f"\n  {g}--setup-only flag set — exiting after setup.{r}")
        sys.exit(0)

    print(f"\n  {g}Starting farm loop in 5 seconds...{r}\n")
    time.sleep(5)
    run_farm_loop()


if __name__ == "__main__":
    main()