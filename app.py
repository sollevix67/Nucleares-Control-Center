#!/usr/bin/env python3
"""NUCLEARES Control Center — dashboard, alarms and game autopilot.

Runs on the Python standard library only (Windows 11 / Ubuntu 24.04).
This controls the Nucleares *game* through its local webserver.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


APP_VERSION = "0.2.1"
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
USER_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
STATIC_DIR = BUNDLE_ROOT / "static"
DATA_DIR = USER_ROOT / "data"
CONFIG_PATH = USER_ROOT / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "game_url": "http://127.0.0.1:8785/",
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8790,
    "poll_seconds": 2.0,
    "control_seconds": 5.0,
    "history_seconds": 3600,
    "autopilot": {
        "auto_start": False,
        "target_core_temp": 330.0,
        "grid_follow": True,
        "grid_buffer_mw": 10.0,
        "train_power_cap_kw": 100000.0,
        "target_boron_ppm": None,
        "boron_deadband_ppm": 5.0,
        "boron_max_output_pct": 20.0,
        "boron_gain_pct_per_ppm": 0.5,
        "areas": {
            "reactor": True,
            "grid": True,
            "secondary": True,
            "condenser": True,
            "retention": True,
            "pressurizer": True,
            "primary_makeup": True,
            "chemistry": True,
        },
    },
    "thresholds": {
        "core_temp_warning": 355.0,
        "core_temp_critical": 390.0,
        "core_temp_scram": 410.0,
        "core_pressure_warning_ratio": 0.92,
        "core_integrity_critical": 25.0,
        "condenser_low": 35.0,
        "condenser_high": 70.0,
        "vacuum_low": 50.0,
        "primary_level_low": 75.0,
        "retention_high": 75.0,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
        return deep_merge({}, DEFAULT_CONFIG)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        return deep_merge(DEFAULT_CONFIG, saved)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Configuration illisible: {path}: {exc}") from exc


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    try:
        number = float(text.replace(",", "."))
        return int(number) if number.is_integer() else number
    except ValueError:
        return text


class GameClient:
    """Small, dependency-free client for the current Nucleares webserver."""

    SPECIAL = {
        "WEBSERVER_BATCH_GET", "WEBSERVER_LIST_VARIABLES", "WEBSERVER_LIST_VARIABLES_JSON",
        "WEBSERVER_VIEW_VARIABLES", "VALVE_PANEL_JSON", "RESISTOR_BANKS_JSON",
        "INSTALLED_LOOPS_JSON", "INVENTORY_HTML", "MAINTENANCE_REPORT_HTML",
        "WEATHER_FORECAST_JSON",
    }

    def __init__(self, base_url: str, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.readable: list[str] = []
        self.writable: set[str] = set()

    def _request(self, method: str, params: dict[str, Any]) -> bytes:
        query = urllib.parse.urlencode(params)
        url = self.base_url + "?" + query
        request = urllib.request.Request(url, method=method)
        if method == "POST":
            request.data = b""
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status != HTTPStatus.OK:
                raise RuntimeError(f"HTTP {response.status} depuis Nucleares")
            return response.read()

    def query_text(self, variable: str, value: str | None = None) -> str:
        params: dict[str, Any] = {"variable": variable}
        if value is not None:
            params["value"] = value
        return self._request("GET", params).decode("utf-8", errors="replace").strip()

    def query_json(self, variable: str, value: str | None = None) -> Any:
        text = self.query_text(variable, value)
        return json.loads(text)

    def discover(self) -> tuple[list[str], set[str]]:
        text = self.query_text("WEBSERVER_LIST_VARIABLES")
        readable: list[str] = []
        writable: set[str] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            method, values = line.split(":", 1)
            names = [v.strip().upper() for v in values.split(",") if v.strip()]
            names = [v for v in names if v not in self.SPECIAL]
            if method.strip().upper() == "GET":
                readable.extend(names)
            elif method.strip().upper() == "POST":
                writable.update(names)
        self.readable = list(dict.fromkeys(readable))
        self.writable = writable
        return self.readable, self.writable

    def batch_get(self, variables: list[str], chunk_size: int = 90) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for offset in range(0, len(variables), chunk_size):
            chunk = variables[offset:offset + chunk_size]
            data = self.query_json("WEBSERVER_BATCH_GET", ",".join(chunk))
            if isinstance(data, dict) and isinstance(data.get("values"), dict):
                data = data["values"]
            if not isinstance(data, dict):
                raise RuntimeError("Réponse groupée invalide du jeu")
            result.update({str(k).upper(): normalize_value(v) for k, v in data.items()})
        return result

    def set_value(self, variable: str, value: Any) -> None:
        variable = variable.upper()
        if self.writable and variable not in self.writable:
            raise ValueError(f"Commande non exposée par cette version du jeu: {variable}")
        if isinstance(value, bool):
            value = str(value).lower()
        self._request("POST", {"variable": variable, "value": value})

    def valve_command(self, command: str, valve_name: str) -> None:
        self.set_value(command, valve_name)

    def valves(self) -> dict[str, Any]:
        data = self.query_json("VALVE_PANEL_JSON")
        if isinstance(data, list):
            return {str(v.get("Name", v.get("name", ""))): v for v in data if isinstance(v, dict)}
        if isinstance(data, dict):
            raw = data.get("valves", data)
            if isinstance(raw, list):
                return {str(v.get("Name", v.get("name", ""))): v for v in raw if isinstance(v, dict)}
            if isinstance(raw, dict):
                return raw
        return {}


@dataclass
class Alarm:
    alarm_id: str
    severity: str
    title: str
    detail: str
    since: str = field(default_factory=utc_now)
    acknowledged: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Action:
    timestamp: str
    area: str
    command: str
    value: Any
    reason: str
    ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class PID:
    def __init__(self, kp: float, ki: float, kd: float, minimum: float, maximum: float):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.minimum, self.maximum = minimum, maximum
        self.integral = 0.0
        self.previous: float | None = None

    def step(self, error: float, dt: float) -> float:
        self.integral = max(-100000.0, min(100000.0, self.integral + error * dt))
        derivative = 0.0 if self.previous is None else (error - self.previous) / max(dt, 0.1)
        self.previous = error
        return max(self.minimum, min(self.maximum, self.kp * error + self.ki * self.integral + self.kd * derivative))

    def reset(self) -> None:
        self.integral = 0.0
        self.previous = None


class HistoryStore:
    def __init__(self, db_path: Path):
        DATA_DIR.mkdir(exist_ok=True)
        self.db_path = db_path
        self.lock = threading.Lock()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS samples (
                    ts REAL NOT NULL,
                    variable TEXT NOT NULL,
                    value REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_samples ON samples(variable, ts);
                CREATE TABLE IF NOT EXISTS events (
                    ts TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5)

    def record_samples(self, state: dict[str, Any], names: list[str]) -> None:
        rows = []
        now = time.time()
        for name in names:
            value = state.get(name)
            if isinstance(value, bool):
                rows.append((now, name, int(value)))
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                rows.append((now, name, float(value)))
        if not rows:
            return
        with self.lock, self._connect() as db:
            db.executemany("INSERT INTO samples(ts, variable, value) VALUES (?, ?, ?)", rows)

    def record_event(self, kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT INTO events(ts, kind, message, payload) VALUES (?, ?, ?, ?)",
                (utc_now(), kind, message, json.dumps(payload or {}, ensure_ascii=False)),
            )

    def history(self, variables: list[str], since: float) -> dict[str, list[list[float]]]:
        output = {v: [] for v in variables}
        if not variables:
            return output
        marks = ",".join("?" for _ in variables)
        sql = f"SELECT ts, variable, value FROM samples WHERE ts >= ? AND variable IN ({marks}) ORDER BY ts"
        with self.lock, self._connect() as db:
            for ts, variable, value in db.execute(sql, [since, *variables]):
                output.setdefault(variable, []).append([ts, value])
        return output

    def cleanup(self, before: float) -> None:
        with self.lock, self._connect() as db:
            db.execute("DELETE FROM samples WHERE ts < ?", (before,))


class ControlCenter:
    KEY_HISTORY = [
        "CORE_TEMP", "CORE_PRESSURE", "CORE_INTEGRITY", "CORE_STATE_CRITICALITY",
        "ROD_BANK_POS_0_ACTUAL", "RODS_POS_ACTUAL", "POWER_DEMAND_MW",
        "GENERATOR_0_KW", "GENERATOR_1_KW", "GENERATOR_2_KW",
        "CONDENSER_VOLUME", "CONDENSER_VAPOR_VOLUME", "CONDENSER_VACUUM",
        "COOLANT_CORE_PRIMARY_LOOP_LEVEL", "CORE_PRIMARY_CIRCUIT_COOLING_TANK_VOLUME",
        "VACUUM_RETENTION_TANK_VOLUME",
        "CHEM_BORON_PPM", "CHEM_BORON_DOSAGE_ACTUAL", "CHEM_BORON_FILTER_ACTUAL",
        "CORE_IODINE_GENERATION", "CORE_IODINE_CUMULATIVE",
        "CORE_XENON_GENERATION", "CORE_XENON_CUMULATIVE",
    ]

    PRESSURIZER_VALVE = "Valvula_Pressurizer_Spray"
    PRESSURIZER_MAX = 176717.0
    RETENTION_MAX = 40000.0
    CHEM_DOSAGE_COMMAND = "CHEM_BORON_DOSAGE_ORDERED_RATE"
    CHEM_FILTER_COMMAND = "CHEM_BORON_FILTER_ORDERED_SPEED"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.client = GameClient(config["game_url"])
        self.store = HistoryStore(DATA_DIR / "telemetry.sqlite3")
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.connected = False
        self.last_error: str | None = None
        self.last_update: str | None = None
        self.state: dict[str, Any] = {}
        self.derived: dict[str, Any] = {}
        self.readable: list[str] = []
        self.writable: set[str] = set()
        self.alarms: dict[str, Alarm] = {}
        self.actions: deque[Action] = deque(maxlen=250)
        self.autopilot_enabled = bool(config["autopilot"].get("auto_start", False))
        self.autopilot_cycle = 0
        self.last_write: dict[str, tuple[Any, float]] = {}
        self.retention_draining = False
        self.pressurizer_spraying = False
        self.feedwater_on = False
        self.condenser_fill_on = False
        self.rod_integral = 0.0
        self.dynamic_temp_setpoint = float(config["autopilot"]["target_core_temp"])
        configured_boron = config["autopilot"].get("target_boron_ppm")
        self.dynamic_boron_target: float | None = None if configured_boron is None else float(configured_boron)
        self.train_pid = {i: PID(0.00002, 0.000002, 0.0, -0.3, 0.2) for i in range(3)}
        self.secondary_pid = {i: PID(0.0005, 0.00005, 0.001, -2.0, 2.0) for i in range(3)}
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        self.threads = [
            threading.Thread(target=self._poll_loop, name="telemetry", daemon=True),
            threading.Thread(target=self._control_loop, name="autopilot", daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def stop(self) -> None:
        self.autopilot_enabled = False
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=2)

    def _poll_loop(self) -> None:
        last_saved = 0.0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if not self.readable:
                    self.readable, self.writable = self.client.discover()
                    self.store.record_event("connexion", f"{len(self.readable)} mesures et {len(self.writable)} commandes détectées")
                state = self.client.batch_get(self.readable)
                with self.lock:
                    self.state = state
                    self.derived = self._derive(state)
                    self.connected = True
                    self.last_error = None
                    self.last_update = utc_now()
                    self._evaluate_alarms(state)
                if time.monotonic() - last_saved >= 5.0:
                    self.store.record_samples(state, self.KEY_HISTORY)
                    last_saved = time.monotonic()
            except Exception as exc:
                with self.lock:
                    self.connected = False
                    self.last_error = str(exc)
                    self._set_alarm("connection", "critical", "Connexion au jeu perdue", str(exc), True)
                self.readable = []
                self.writable = set()
            wait = max(0.1, float(self.config["poll_seconds"]) - (time.monotonic() - started))
            self.stop_event.wait(wait)

    def _control_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            if self.autopilot_enabled and self.connected:
                try:
                    with self.lock:
                        state = dict(self.state)
                    self._autopilot_step(state, float(self.config["control_seconds"]))
                    self.autopilot_cycle += 1
                except Exception as exc:
                    self._log_action("système", "AUTOPILOT", "erreur", str(exc), False)
                    self._set_alarm("autopilot_error", "critical", "Erreur du pilote automatique", str(exc), True)
            wait = max(0.2, float(self.config["control_seconds"]) - (time.monotonic() - started))
            self.stop_event.wait(wait)

    def _derive(self, s: dict[str, Any]) -> dict[str, Any]:
        generated = sum(as_number(s.get(f"GENERATOR_{i}_KW")) for i in range(3))
        demand_kw = as_number(s.get("POWER_DEMAND_MW")) * 1000.0
        liquid = as_number(s.get("CONDENSER_VOLUME"))
        vapor = as_number(s.get("CONDENSER_VAPOR_VOLUME"))
        condenser_fill = liquid / (liquid + vapor) * 100.0 if liquid + vapor > 0 else None
        pressurizer = as_number(s.get("CORE_PRIMARY_CIRCUIT_COOLING_TANK_VOLUME")) / self.PRESSURIZER_MAX * 100.0
        retention = as_number(s.get("VACUUM_RETENTION_TANK_VOLUME")) / self.RETENTION_MAX * 100.0
        return {
            "generated_kw": round(generated, 2),
            "demand_kw": round(demand_kw, 2),
            "power_balance_kw": round(generated - demand_kw, 2),
            "condenser_fill_pct": None if condenser_fill is None else round(condenser_fill, 2),
            "pressurizer_pct": round(pressurizer, 2),
            "retention_pct": round(retention, 2),
        }

    def _set_alarm(self, alarm_id: str, severity: str, title: str, detail: str, active: bool) -> None:
        existing = self.alarms.get(alarm_id)
        if active:
            if not existing:
                self.alarms[alarm_id] = Alarm(alarm_id, severity, title, detail)
                self.store.record_event("alarme", title, {"severity": severity, "detail": detail})
            else:
                existing.severity, existing.title, existing.detail = severity, title, detail
        elif existing:
            self.store.record_event("retour_normal", existing.title)
            del self.alarms[alarm_id]

    def _evaluate_alarms(self, s: dict[str, Any]) -> None:
        t = self.config["thresholds"]
        temp = as_number(s.get("CORE_TEMP"))
        pressure = as_number(s.get("CORE_PRESSURE"))
        pressure_max = as_number(s.get("CORE_PRESSURE_MAX"))
        integrity = as_number(s.get("CORE_INTEGRITY"), 100.0)
        imminent = bool(s.get("CORE_IMMINENT_FUSION", False))
        cond = self.derived.get("condenser_fill_pct")
        vacuum = as_number(s.get("CONDENSER_VACUUM"), 100.0)
        primary = as_number(s.get("COOLANT_CORE_PRIMARY_LOOP_LEVEL"), 100.0)

        self._set_alarm("connection", "critical", "Connexion au jeu perdue", "", False)
        self._set_alarm("core_temp", "critical" if temp >= t["core_temp_critical"] else "warning",
                        "Température cœur élevée", f"{temp:.1f} °C", temp >= t["core_temp_warning"])
        self._set_alarm("fusion", "critical", "Fusion imminente", "SCRAM requis", imminent)
        self._set_alarm("integrity", "critical", "Intégrité du cœur critique", f"{integrity:.1f} %",
                        integrity > 0 and integrity <= t["core_integrity_critical"])
        self._set_alarm("pressure", "critical", "Pression cœur élevée", f"{pressure:.1f} / {pressure_max:.1f}",
                        pressure_max > 0 and pressure >= pressure_max * t["core_pressure_warning_ratio"])
        self._set_alarm("condenser_low", "warning", "Niveau condenseur bas", f"{cond:.1f} %" if cond is not None else "indisponible",
                        cond is not None and cond < t["condenser_low"])
        self._set_alarm("condenser_high", "warning", "Niveau condenseur élevé", f"{cond:.1f} %" if cond is not None else "indisponible",
                        cond is not None and cond > t["condenser_high"])
        self._set_alarm("vacuum", "warning", "Vide condenseur insuffisant", f"{vacuum:.1f} %",
                        "CONDENSER_VACUUM" in s and vacuum < t["vacuum_low"])
        self._set_alarm("primary_level", "critical", "Niveau circuit primaire bas", f"{primary:.1f} %",
                        "COOLANT_CORE_PRIMARY_LOOP_LEVEL" in s and primary < t["primary_level_low"])

        chemistry = self._chemistry_info(s)
        for name, value in s.items():
            if name.startswith("CHEMICAL_") and not chemistry["installed"]:
                continue
            if name.endswith("_DRY_STATUS"):
                self._set_alarm("dry_" + name, "critical", "Pompe sans fluide", name, as_number(value) == 1)
            elif name.endswith("_OVERLOAD_STATUS"):
                self._set_alarm("overload_" + name, "warning", "Pompe en surcharge", name, as_number(value) == 1)

        chemistry_requested = self.autopilot_enabled and bool(self.config["autopilot"]["areas"].get("chemistry"))
        self._set_alarm("chemistry_connection", "warning", "Module chimique indisponible", "", False)
        self._set_alarm(
            "chemistry_fault", "critical", "Défaut du module chimique",
            chemistry.get("fault_detail", ""), chemistry["installed"] and chemistry["fault"],
        )
        self._set_alarm(
            "chemistry_commands", "warning", "Commandes chimiques non exposées",
            "Le webserveur ne permet pas le dosage et la filtration.",
            chemistry_requested and chemistry["installed"] and not chemistry["commands_exposed"],
        )

    def _write(self, area: str, variable: str, value: Any, reason: str, cooldown: float = 2.0) -> bool:
        now = time.monotonic()
        previous = self.last_write.get(variable)
        if previous and previous[0] == value and now - previous[1] < cooldown:
            return False
        try:
            self.client.set_value(variable, value)
            self.last_write[variable] = (value, now)
            self._log_action(area, variable, value, reason, True)
            return True
        except (ValueError, urllib.error.URLError, RuntimeError) as exc:
            self._log_action(area, variable, value, f"{reason} — {exc}", False)
            return False

    def _valve(self, area: str, command: str, name: str, reason: str) -> bool:
        try:
            self.client.valve_command(command, name)
            self._log_action(area, command, name, reason, True)
            return True
        except Exception as exc:
            self._log_action(area, command, name, f"{reason} — {exc}", False)
            return False

    def _log_action(self, area: str, command: str, value: Any, reason: str, ok: bool) -> None:
        action = Action(utc_now(), area, command, value, reason, ok)
        with self.lock:
            self.actions.appendleft(action)
        self.store.record_event("commande" if ok else "erreur_commande", f"{command}={value}", action.as_dict())

    def _first_available(self, *names: str) -> str | None:
        for name in names:
            if name in self.writable:
                return name
        return None

    def _chemistry_info(self, s: dict[str, Any]) -> dict[str, Any]:
        signal_names = {
            "CHEM_TRUCK_IN_ZONE", "CHEM_TRUCK_CONNECTED", "CHEM_BORON_PPM",
            "CHEMICAL_DOSING_PUMP_STATUS", "CHEMICAL_FILTER_PUMP_STATUS",
        }
        signals_present = any(name in s for name in signal_names)
        commands_exposed = {self.CHEM_DOSAGE_COMMAND, self.CHEM_FILTER_COMMAND}.issubset(self.writable)
        raw_ppm = s.get("CHEM_BORON_PPM")
        ppm = None if raw_ppm is None else as_number(raw_ppm)
        dosing_status = int(as_number(s.get("CHEMICAL_DOSING_PUMP_STATUS"), 4))
        filter_status = int(as_number(s.get("CHEMICAL_FILTER_PUMP_STATUS"), 4))
        both_not_installed = dosing_status == 4 and filter_status == 4
        installed = signals_present and ppm is not None and not both_not_installed
        in_zone = bool(s.get("CHEM_TRUCK_IN_ZONE", False))
        truck_connected = bool(s.get("CHEM_TRUCK_CONNECTED", False))

        faults: list[str] = []
        for label, status in (("dosage", dosing_status), ("filtration", filter_status)):
            if installed and status == 3:
                faults.append(f"pompe de {label} à maintenir")
            elif installed and status == 4:
                faults.append(f"pompe de {label} non installée")
            elif installed and status == 5:
                faults.append(f"énergie insuffisante pour la pompe de {label}")
        for label, prefix in (("dosage", "CHEMICAL_DOSING_PUMP"), ("filtration", "CHEMICAL_FILTER_PUMP")):
            if as_number(s.get(prefix + "_DRY_STATUS"), 4) == 1:
                faults.append(f"pompe de {label} sans fluide")
            if as_number(s.get(prefix + "_OVERLOAD_STATUS"), 4) == 1:
                faults.append(f"pompe de {label} en surcharge")

        if not signals_present and not commands_exposed:
            status, message = "unavailable", "Variables chimiques absentes du webserveur"
        elif not installed:
            status, message = "not_installed", "Module chimique non installé dans cette partie"
        elif faults:
            status, message = "fault", "; ".join(faults)
        elif not commands_exposed:
            status, message = "read_only", "Mesures disponibles, commandes POST absentes"
        else:
            status = "ready"
            message = "Dosage et filtration disponibles"
            if not truck_connected:
                message += " — réservoir local, camion non requis"

        return {
            "status": status,
            "message": message,
            "available": signals_present or commands_exposed,
            "installed": installed,
            "connected": truck_connected,
            "truck_in_zone": in_zone,
            "truck_connected": truck_connected,
            "ready": status == "ready",
            "commands_exposed": commands_exposed,
            "fault": bool(faults),
            "fault_detail": "; ".join(faults),
            "ppm": ppm,
            "target_ppm": self.dynamic_boron_target,
            "dosage_actual": None if s.get("CHEM_BORON_DOSAGE_ACTUAL") is None else as_number(s.get("CHEM_BORON_DOSAGE_ACTUAL")),
            "filter_actual": None if s.get("CHEM_BORON_FILTER_ACTUAL") is None else as_number(s.get("CHEM_BORON_FILTER_ACTUAL")),
        }

    def emergency_scram(self, reason: str = "Commande opérateur") -> None:
        command = self._first_available("CORE_SCRAM_BUTTON", "SCRAM_BUTTON", "RODS_POS_ORDERED")
        if not command:
            raise RuntimeError("La version du jeu n’expose aucune commande SCRAM")
        value: Any = 100.0 if command == "RODS_POS_ORDERED" else True
        self._write("sécurité", command, value, reason, cooldown=0)
        for i in range(3):
            pump = f"COOLANT_CORE_CIRCULATION_PUMP_{i}_ORDERED_SPEED"
            if pump in self.writable:
                self._write("sécurité", pump, 90.0, "Refroidissement après SCRAM", cooldown=0)

    def _autopilot_step(self, s: dict[str, Any], dt: float) -> None:
        areas = self.config["autopilot"]["areas"]
        temp = as_number(s.get("CORE_TEMP"))
        thresholds = self.config["thresholds"]
        if bool(s.get("CORE_IMMINENT_FUSION")) or temp >= thresholds["core_temp_scram"]:
            self.emergency_scram("Protection automatique température/fusion")
            return

        if areas.get("reactor"):
            self._control_reactor(s)
        if areas.get("grid"):
            self._control_grid(s, dt, bool(areas.get("secondary")))
        if areas.get("condenser"):
            self._control_condenser(s)
        if areas.get("retention"):
            self._control_retention(s)
        if areas.get("pressurizer"):
            self._control_pressurizer(s)
        if areas.get("primary_makeup"):
            self._control_primary_makeup(s)
        if areas.get("chemistry"):
            self._control_chemistry(s)

    def _control_reactor(self, s: dict[str, Any]) -> None:
        actual_name = "ROD_BANK_POS_0_ACTUAL" if "ROD_BANK_POS_0_ACTUAL" in s else "RODS_POS_ACTUAL"
        ordered_name = self._first_available("ROD_BANK_POS_0_ORDERED", "RODS_POS_ORDERED")
        if not ordered_name or actual_name not in s:
            return
        setpoint = self.dynamic_temp_setpoint
        error = as_number(s.get("CORE_TEMP")) - setpoint
        criticality = as_number(s.get("CORE_STATE_CRITICALITY"))
        self.rod_integral = max(-3.0, min(3.0, self.rod_integral + 0.002 * error))
        raw_delta = 0.04 * error + criticality + self.rod_integral
        magnitude = abs(error)
        max_step = 0.1 if magnitude <= 3 else 0.4 if magnitude <= 8 else 0.8 if magnitude <= 15 else 1.2
        target = max(0.0, min(100.0, as_number(s.get(actual_name)) + max(-max_step, min(max_step, raw_delta))))
        self._write("réacteur", ordered_name, round(target, 2), f"Régulation cœur {setpoint:.0f} °C")
        for i in range(3):
            pump = f"COOLANT_CORE_CIRCULATION_PUMP_{i}_ORDERED_SPEED"
            if pump in self.writable:
                self._write("réacteur", pump, 65.0, "Débit primaire nominal", cooldown=30)

    def _control_grid(self, s: dict[str, Any], dt: float, secondary: bool) -> None:
        installed = [i for i in range(3) if f"GENERATOR_{i}_KW" in s and f"MSCV_{i}_OPENING_ORDERED" in self.writable]
        if not installed:
            return
        demand = as_number(s.get("POWER_DEMAND_MW")) * 1000.0
        buffer_kw = float(self.config["autopilot"]["grid_buffer_mw"]) * 1000.0
        cap = float(self.config["autopilot"]["train_power_cap_kw"])
        target_each = min(cap, (demand + buffer_kw) / len(installed))
        total_power = sum(as_number(s.get(f"GENERATOR_{i}_KW")) for i in installed)
        total_error = demand + buffer_kw - total_power
        if self.config["autopilot"].get("grid_follow", True):
            step = max(-0.5, min(0.5, total_error * 0.00002))
            self.dynamic_temp_setpoint = max(306.0, min(375.0, self.dynamic_temp_setpoint + step))
        for i in installed:
            power = as_number(s.get(f"GENERATOR_{i}_KW"))
            steam = as_number(s.get(f"STEAM_GEN_{i}_OUTLET"))
            actual = as_number(s.get(f"MSCV_{i}_OPENING_ACTUAL"))
            error = target_each - power
            delta = 0.0 if target_each and abs(error) < target_each * 0.03 else self.train_pid[i].step(error, dt)
            new_mscv = max(0.5, min(100.0, actual + delta))
            if delta > 0 and steam > 0:
                new_mscv = min(new_mscv, max(steam / 8.0, 1.0))
            self._write("production", f"MSCV_{i}_OPENING_ORDERED", round(new_mscv, 2),
                        f"Suivi réseau, cible {target_each / 1000:.1f} MW")
            bypass = f"STEAM_TURBINE_{i}_BYPASS_ORDERED"
            if bypass in self.writable and as_number(s.get(f"STEAM_TURBINE_{i}_BYPASS_ACTUAL")) > 1.0:
                self._write("production", bypass, 0.0, "Bypass fermé en régime normal")
            if secondary:
                pump = f"COOLANT_SEC_CIRCULATION_PUMP_{i}_ORDERED_SPEED"
                level = as_number(s.get(f"COOLANT_SEC_{i}_LIQUID_VOLUME"))
                if pump in self.writable and level > 0:
                    correction = self.secondary_pid[i].step(25000.0 - level, dt)
                    speed = max(5.0, min(100.0, steam / 2.0 + correction))
                    self._write("secondaire", pump, round(speed, 2), "Niveau générateur vapeur")

    def _control_condenser(self, s: dict[str, Any]) -> None:
        fill = self.derived.get("condenser_fill_pct")
        if "FREIGHT_PUMP_CONDENSER_ACTIVE" in s:
            self.condenser_fill_on = bool(s["FREIGHT_PUMP_CONDENSER_ACTIVE"])
        if fill is not None:
            if fill < 45.0 and not self.condenser_fill_on:
                if self._write("condenseur", "FREIGHT_PUMP_CONDENSER_SWITCH", True, "Remplissage sous 45 %"):
                    self.condenser_fill_on = True
            elif fill >= 60.0 and self.condenser_fill_on:
                if self._write("condenseur", "FREIGHT_PUMP_CONDENSER_SWITCH", False, "Remplissage atteint 60 %"):
                    self.condenser_fill_on = False
        if "CONDENSER_VACUUM_PUMP_START_STOP" in self.writable and not bool(s.get("CONDENSER_VACUUM_PUMP_ACTIVE")) and not self.retention_draining:
            self._write("condenseur", "CONDENSER_VACUUM_PUMP_START_STOP", True, "Maintien du vide")
        if "CONDENSER_CIRCULATION_PUMP_SWITCH" in self.writable and not bool(s.get("CONDENSER_CIRCULATION_PUMP_ACTIVE")):
            self._write("condenseur", "CONDENSER_CIRCULATION_PUMP_SWITCH", True, "Circulation condenseur")
        if "CONDENSER_CIRCULATION_PUMP_ORDERED_SPEED" in self.writable:
            self._write("condenseur", "CONDENSER_CIRCULATION_PUMP_ORDERED_SPEED", 25.0, "Vitesse anti-surrefroidissement", cooldown=30)

    def _control_retention(self, s: dict[str, Any]) -> None:
        pct = self.derived.get("retention_pct", 0.0)
        command = "STEAM_EJECTOR_CONDENSER_RETURN_VALVE"
        if command not in self.writable:
            return
        if pct > 75.0 and not self.retention_draining:
            self.retention_draining = True
            if "CONDENSER_VACUUM_PUMP_START_STOP" in self.writable:
                self._write("rétention", "CONDENSER_VACUUM_PUMP_START_STOP", False, "Vidange rétention")
            self._write("rétention", command, 25.0, "Vidange au-dessus de 75 %")
        elif pct <= 50.0 and self.retention_draining:
            self._write("rétention", command, 0.0, "Vidange terminée à 50 %")
            self.retention_draining = False

    def _control_pressurizer(self, s: dict[str, Any]) -> None:
        pct = self.derived.get("pressurizer_pct", 60.0)
        if pct < 50.0 and not self.pressurizer_spraying and "VALVE_OPEN" in self.writable:
            if self._valve("pressuriseur", "VALVE_OPEN", self.PRESSURIZER_VALVE, "Niveau inférieur à 50 %"):
                self.pressurizer_spraying = True
        elif pct >= 60.0 and self.pressurizer_spraying and "VALVE_CLOSE" in self.writable:
            if self._valve("pressuriseur", "VALVE_CLOSE", self.PRESSURIZER_VALVE, "Niveau revenu à 60 %"):
                self.pressurizer_spraying = False

    def _control_primary_makeup(self, s: dict[str, Any]) -> None:
        level = as_number(s.get("COOLANT_CORE_PRIMARY_LOOP_LEVEL"), 100.0)
        if "FREIGHT_PUMP_FEEDWATER_ACTIVE" in s:
            self.feedwater_on = bool(s["FREIGHT_PUMP_FEEDWATER_ACTIVE"])
        command = "FREIGHT_PUMP_FEEDWATER_SWITCH"
        if command not in self.writable:
            return
        if level < 80.0 and not self.feedwater_on:
            if self._write("primaire", command, True, "Appoint primaire sous 80 %"):
                self.feedwater_on = True
        elif level >= 90.0 and self.feedwater_on:
            if self._write("primaire", command, False, "Appoint primaire atteint 90 %"):
                self.feedwater_on = False

    def _stop_chemistry(self, s: dict[str, Any], reason: str) -> None:
        dosage = max(
            as_number(s.get("CHEM_BORON_DOSAGE_ORDERED")),
            as_number(s.get("CHEM_BORON_DOSAGE_ACTUAL")),
            as_number(self.last_write.get(self.CHEM_DOSAGE_COMMAND, (0, 0))[0]),
        )
        filtering = max(
            as_number(s.get("CHEM_BORON_FILTER_ORDERED")),
            as_number(s.get("CHEM_BORON_FILTER_ACTUAL")),
            as_number(self.last_write.get(self.CHEM_FILTER_COMMAND, (0, 0))[0]),
        )
        if dosage > 0 and self.CHEM_DOSAGE_COMMAND in self.writable:
            self._write("chimie", self.CHEM_DOSAGE_COMMAND, 0.0, reason, cooldown=0)
        if filtering > 0 and self.CHEM_FILTER_COMMAND in self.writable:
            self._write("chimie", self.CHEM_FILTER_COMMAND, 0.0, reason, cooldown=0)

    def _control_chemistry(self, s: dict[str, Any]) -> None:
        chemistry = self._chemistry_info(s)
        if not chemistry["installed"]:
            return
        if not chemistry["ready"]:
            self._stop_chemistry(s, chemistry["message"])
            return

        ppm = chemistry["ppm"]
        if ppm is None:
            return
        if self.dynamic_boron_target is None:
            self.dynamic_boron_target = round(float(ppm), 2)
            self.store.record_event(
                "chimie", "Consigne de bore capturée",
                {"target_boron_ppm": self.dynamic_boron_target},
            )

        target = float(self.dynamic_boron_target)
        settings = self.config["autopilot"]
        deadband = float(settings["boron_deadband_ppm"])
        maximum = float(settings["boron_max_output_pct"])
        gain = float(settings.get("boron_gain_pct_per_ppm", 0.5))
        error = target - float(ppm)

        if error > deadband:
            output = round(min(maximum, max(1.0, (error - deadband) * gain)), 2)
            filtering = max(
                as_number(s.get("CHEM_BORON_FILTER_ORDERED")),
                as_number(s.get("CHEM_BORON_FILTER_ACTUAL")),
                as_number(self.last_write.get(self.CHEM_FILTER_COMMAND, (0, 0))[0]),
            )
            if filtering > 0 and not self._write("chimie", self.CHEM_FILTER_COMMAND, 0.0, "Filtration arrêtée avant dosage", cooldown=0):
                return
            self._write(
                "chimie", self.CHEM_DOSAGE_COMMAND, output,
                f"Bore {ppm:.1f} ppm sous la consigne {target:.1f} ppm", cooldown=10,
            )
        elif error < -deadband:
            output = round(min(maximum, max(1.0, (-error - deadband) * gain)), 2)
            dosage = max(
                as_number(s.get("CHEM_BORON_DOSAGE_ORDERED")),
                as_number(s.get("CHEM_BORON_DOSAGE_ACTUAL")),
                as_number(self.last_write.get(self.CHEM_DOSAGE_COMMAND, (0, 0))[0]),
            )
            if dosage > 0 and not self._write("chimie", self.CHEM_DOSAGE_COMMAND, 0.0, "Dosage arrêté avant filtration", cooldown=0):
                return
            self._write(
                "chimie", self.CHEM_FILTER_COMMAND, output,
                f"Bore {ppm:.1f} ppm au-dessus de la consigne {target:.1f} ppm", cooldown=10,
            )
        else:
            self._stop_chemistry(s, f"Bore stabilisé à {ppm:.1f} ppm")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "version": APP_VERSION,
                "connected": self.connected,
                "last_error": self.last_error,
                "last_update": self.last_update,
                "capabilities": {"readable": len(self.readable), "writable": len(self.writable)},
                "state": dict(self.state),
                "derived": dict(self.derived),
                "alarms": [a.as_dict() for a in self.alarms.values()],
                "autopilot": {
                    "enabled": self.autopilot_enabled,
                    "cycle": self.autopilot_cycle,
                    "areas": dict(self.config["autopilot"]["areas"]),
                    "target_core_temp": round(self.dynamic_temp_setpoint, 2),
                    "configured_core_temp": self.config["autopilot"]["target_core_temp"],
                    "grid_buffer_mw": self.config["autopilot"]["grid_buffer_mw"],
                    "target_boron_ppm": self.dynamic_boron_target,
                    "configured_boron_ppm": self.config["autopilot"].get("target_boron_ppm"),
                },
                "chemistry": self._chemistry_info(self.state),
                "actions": [a.as_dict() for a in list(self.actions)[:50]],
            }

    def acknowledge(self, alarm_id: str) -> bool:
        with self.lock:
            alarm = self.alarms.get(alarm_id)
            if not alarm:
                return False
            alarm.acknowledged = True
            return True

    def set_autopilot(self, enabled: bool) -> None:
        state: dict[str, Any] = {}
        with self.lock:
            self.autopilot_enabled = enabled
            if not enabled:
                state = dict(self.state)
                self.retention_draining = False
                self.pressurizer_spraying = False
                self.rod_integral = 0.0
                for pid in [*self.train_pid.values(), *self.secondary_pid.values()]:
                    pid.reset()
                self.dynamic_temp_setpoint = float(self.config["autopilot"]["target_core_temp"])
                if self.config["autopilot"].get("target_boron_ppm") is None:
                    self.dynamic_boron_target = None
        if not enabled:
            self._stop_chemistry(state, "Arrêt du pilote automatique")
        self.store.record_event("autopilot", "Pilote automatique activé" if enabled else "Pilote automatique arrêté")

    def update_config(self, updates: dict[str, Any]) -> None:
        allowed_top = {"game_url", "poll_seconds", "control_seconds", "autopilot", "thresholds"}
        sanitized = {key: value for key, value in updates.items() if key in allowed_top}
        stop_chemistry = False
        url_changed = False
        state: dict[str, Any] = {}
        with self.lock:
            old_url = self.config["game_url"]
            old_chemistry = bool(self.config["autopilot"]["areas"].get("chemistry"))
            candidate = deep_merge(self.config, sanitized)
            if not 0.5 <= float(candidate["poll_seconds"]) <= 30:
                raise ValueError("poll_seconds doit être compris entre 0,5 et 30")
            if not 1 <= float(candidate["control_seconds"]) <= 60:
                raise ValueError("control_seconds doit être compris entre 1 et 60")
            if not 250 <= float(candidate["autopilot"]["target_core_temp"]) <= 390:
                raise ValueError("La température cible doit être comprise entre 250 et 390 °C")
            target_boron = candidate["autopilot"].get("target_boron_ppm")
            if target_boron is not None and not 0 <= float(target_boron) <= 10000:
                raise ValueError("La consigne de bore doit être comprise entre 0 et 10 000 ppm")
            if not 0.1 <= float(candidate["autopilot"]["boron_deadband_ppm"]) <= 1000:
                raise ValueError("La bande morte du bore doit être comprise entre 0,1 et 1 000 ppm")
            if not 1 <= float(candidate["autopilot"]["boron_max_output_pct"]) <= 100:
                raise ValueError("La puissance chimique maximale doit être comprise entre 1 et 100 %")
            self.config = candidate
            stop_chemistry = self.autopilot_enabled and old_chemistry and not bool(candidate["autopilot"]["areas"].get("chemistry"))
            if stop_chemistry:
                state = dict(self.state)
            if not self.autopilot_enabled:
                self.dynamic_temp_setpoint = float(candidate["autopilot"]["target_core_temp"])
            self.dynamic_boron_target = None if target_boron is None else float(target_boron)
            save_config(self.config)
            url_changed = self.config["game_url"] != old_url
        if stop_chemistry:
            self._stop_chemistry(state, "Zone chimique désactivée")
        if url_changed:
            with self.lock:
                self.client = GameClient(self.config["game_url"])
                self.readable, self.writable = [], set()


class DashboardHandler(BaseHTTPRequestHandler):
    center: ControlCenter
    server_version = "NuclearesControlCenter/" + APP_VERSION

    MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("NUCLEARES_HTTP_LOG"):
            super().log_message(fmt, *args)

    def _json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length > 1024 * 1024:
            raise ValueError("Requête trop volumineuse")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Objet JSON attendu")
        return data

    def _static(self, path: str) -> None:
        filename = "index.html" if path in ("", "/") else path.lstrip("/")
        resolved = (STATIC_DIR / filename).resolve()
        if STATIC_DIR.resolve() not in resolved.parents and resolved != STATIC_DIR.resolve():
            self.send_error(404)
            return
        if not resolved.is_file():
            self.send_error(404)
            return
        content = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.MIME.get(resolved.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/state":
                self._json(self.center.snapshot())
            elif parsed.path == "/api/config":
                self._json(self.center.config)
            elif parsed.path == "/api/variables":
                query = urllib.parse.parse_qs(parsed.query)
                search = query.get("q", [""])[0].upper()
                state = self.center.snapshot()["state"]
                items = [{"name": k, "value": v, "writable": k in self.center.writable} for k, v in sorted(state.items()) if search in k]
                self._json({"variables": items, "writable": sorted(self.center.writable)})
            elif parsed.path == "/api/history":
                query = urllib.parse.parse_qs(parsed.query)
                variables = [v.upper() for v in query.get("variables", ["CORE_TEMP,GENERATOR_0_KW"])[0].split(",") if v]
                seconds = min(86400, max(60, int(query.get("seconds", ["1800"])[0])))
                self._json(self.center.store.history(variables[:12], time.time() - seconds))
            elif parsed.path == "/health":
                self._json({"ok": True, "game_connected": self.center.connected})
            elif parsed.path.startswith("/api/"):
                self._json({"error": "Route inconnue"}, 404)
            else:
                self._static(parsed.path)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = self._body()
            if parsed.path == "/api/autopilot":
                self.center.set_autopilot(bool(body.get("enabled")))
                self._json({"ok": True, "enabled": self.center.autopilot_enabled})
            elif parsed.path == "/api/scram":
                self.center.emergency_scram("SCRAM demandé depuis le tableau de bord")
                self._json({"ok": True})
            elif parsed.path == "/api/ack":
                self._json({"ok": self.center.acknowledge(str(body.get("alarm_id", "")))})
            elif parsed.path == "/api/config":
                self.center.update_config(body)
                self._json({"ok": True, "config": self.center.config})
            elif parsed.path == "/api/command":
                variable = str(body.get("variable", "")).upper()
                if not variable:
                    raise ValueError("variable requise")
                self.center._write("manuel", variable, body.get("value"), "Commande tableau de bord", cooldown=0)
                self._json({"ok": True})
            else:
                self._json({"error": "Route inconnue"}, 404)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


def run() -> int:
    parser = argparse.ArgumentParser(description="Tableau de bord et pilote automatique pour le jeu Nucleares")
    parser.add_argument("--no-browser", action="store_true", help="Ne pas ouvrir le navigateur automatiquement")
    parser.add_argument("--game-url", help="Adresse du webserveur Nucleares, ex. http://127.0.0.1:8785/")
    parser.add_argument("--port", type=int, help="Port du tableau de bord")
    parser.add_argument("--host", help="Adresse d’écoute du tableau de bord (défaut: configuration)")
    args = parser.parse_args()

    config = load_config()
    if args.game_url:
        config["game_url"] = args.game_url
    if args.port:
        config["dashboard_port"] = args.port
    if args.host:
        config["dashboard_host"] = args.host

    center = ControlCenter(config)
    DashboardHandler.center = center
    address = (str(config["dashboard_host"]), int(config["dashboard_port"]))
    server = ThreadingHTTPServer(address, DashboardHandler)
    center.start()
    url = f"http://127.0.0.1:{address[1]}/"
    print(f"NUCLEARES Control Center {APP_VERSION}")
    print(f"Tableau de bord : {url}")
    print(f"Serveur du jeu  : {config['game_url']}")
    print("Ctrl+C pour arrêter.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nArrêt en cours…")
    finally:
        server.shutdown()
        center.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception as exc:
        print(f"Erreur fatale: {exc}", file=sys.stderr)
        if os.environ.get("NUCLEARES_DEBUG"):
            traceback.print_exc()
        raise SystemExit(1)
