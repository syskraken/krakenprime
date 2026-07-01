import os
import json
import time
import uuid
import threading

import customtkinter as ctk

import sys
import logging


if getattr(sys, 'frozen', False):
    _bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    _cert_path = os.path.join(_bundle_dir, 'certifi', 'cacert.pem')
    if os.path.exists(_cert_path):
        os.environ['REQUESTS_CA_BUNDLE'] = _cert_path
        os.environ['SSL_CERT_FILE'] = _cert_path

if getattr(sys, 'frozen', False):
    _bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    _log_dir = os.path.dirname(sys.executable)
    logging.basicConfig(
        filename=os.path.join(_log_dir, 'telemetry_debug.log'),
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s %(message)s'
    )
    _cert_path = os.path.join(_bundle_dir, 'certifi', 'cacert.pem')
    logging.debug(f"looking for cert at: {_cert_path} — exists: {os.path.exists(_cert_path)}")
    if os.path.exists(_cert_path):
        os.environ['REQUESTS_CA_BUNDLE'] = _cert_path
        os.environ['SSL_CERT_FILE'] = _cert_path

SERVER_URL = "https://kraken.protectiva.site"  
PRIVACY_POLICY_URL = "https://kraken.protectiva.site/privacy" 
HEARTBEAT_INTERVAL_S = 25
REQUEST_TIMEOUT_S = 5
OPT_OUT_SENDS_HEARTBEAT = True

SETTINGS_FILENAME = "telemetry_settings.json"


class TelemetryClient:
    def __init__(self, root, app_dir):
        self.root = root
        self.settings_path = os.path.join(app_dir, SETTINGS_FILENAME)
        self.client_id = None
        self.region = None       # ISO code, or None if opted out
        self.opted_out = False
        self._stop_event = threading.Event()
        self._thread = None

    #Public API

    def start(self):
        settings = self._load_settings()
        if settings is None:
            # First run — ask once, then persist the answer.
            self._prompt_for_region(on_done=self._begin_heartbeats)
        else:
            self.client_id = settings["client_id"]
            self.region = settings.get("region")
            self.opted_out = settings.get("opted_out", False)
            self._begin_heartbeats()

    def stop(self):
        self._stop_event.set()
        if self.client_id and (not self.opted_out or OPT_OUT_SENDS_HEARTBEAT):
            self._post("/api/leave", {"client_id": self.client_id, "region": self.region})

    #Local settings

    def _load_settings(self):
        if not os.path.exists(self.settings_path):
            return None
        try:
            with open(self.settings_path) as f:
                return json.load(f)
        except Exception:
            return None

    def _save_settings(self):
        data = {"client_id": self.client_id, "region": self.region, "opted_out": self.opted_out}
        try:
            with open(self.settings_path, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # One-time region picker

    def _prompt_for_region(self, on_done):
        self.client_id = str(uuid.uuid4())
        regions = self._fetch_region_list()

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Select your Region")
        dialog.geometry("420x300")
        dialog.grab_set()
        dialog.resizable(False, False)

        ctk.CTkLabel(
            dialog, wraplength=380, justify="left",
            text=("Select your region"),
        ).pack(padx=16, pady=(16, 10))

        region_names = [name for _, name in regions]
        combo = ctk.CTkComboBox(dialog, values=region_names, width=280)
        if region_names:
            combo.set(region_names[0])
        combo.pack(pady=6)

        def _filter_regions(event=None):
            # Ignore navigation/selection keys so we don't refilter after a pick.
            if event is not None and event.keysym in (
                "Return", "Escape", "Up", "Down", "Tab"
            ):
                return
            query = combo.get().strip().lower()
            matches = [name for name in region_names if query in name.lower()]
            combo.configure(values=matches if matches else region_names)
            # Best-effort: reopen the dropdown so filtered results are visible
            # immediately. Falls back silently if this private hook changes.
            try:
                combo._open_dropdown_menu()
            except Exception:
                pass

        combo.bind("<KeyRelease>", _filter_regions)

        def _confirm():
            idx = region_names.index(combo.get()) if combo.get() in region_names else 0
            self.region = regions[idx][0] if regions else None
            self.opted_out = False
            self._save_settings()
            dialog.destroy()
            on_done()

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(pady=10)
        ctk.CTkButton(btn_row, text="Continue", command=_confirm).pack(side="left", padx=6)

    def _fetch_region_list(self):
        """Pull the canonical region list from the server so the dropdown
        always matches what the map/backend actually supports. Falls back
        to a short built-in list if the server can't be reached yet."""
        try:
            import requests
            resp = requests.get(f"{SERVER_URL}/api/regions", timeout=REQUEST_TIMEOUT_S)
            resp.raise_for_status()
            data = resp.json()
            return [(r["code"], r["name"]) for r in data]
        
        except Exception as e:
            logging.debug(f"region fetch failed: {type(e).__name__}: {e}")
            return [("US", "United States"), ("GB", "United Kingdom"), ("IN", "India"),
                    ("PH", "Philippines"), ("BR", "Brazil"), ("DE", "Germany")]

    #Heartbeat loop

    def _begin_heartbeats(self):
        if self.opted_out and not OPT_OUT_SENDS_HEARTBEAT:
            return
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def _heartbeat_loop(self):
        payload_region = None if self.opted_out else self.region
        while not self._stop_event.is_set():
            self._post("/api/heartbeat", {"client_id": self.client_id, "region": payload_region})
            self._stop_event.wait(HEARTBEAT_INTERVAL_S)

    def _post(self, path, payload):
        try:
            import requests
            requests.post(f"{SERVER_URL}{path}", json=payload, timeout=REQUEST_TIMEOUT_S)
        except Exception:
            pass   # never let a network hiccup interrupt the bot