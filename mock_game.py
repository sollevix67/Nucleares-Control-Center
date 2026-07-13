#!/usr/bin/env python3
"""Small Nucleares webserver simulator for UI and autopilot testing."""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


READABLE = [
    "GAME_VERSION", "GAME_SIM_SPEED", "CORE_TEMP", "CORE_TEMP_MAX", "CORE_PRESSURE",
    "CORE_PRESSURE_MAX", "CORE_INTEGRITY", "CORE_STATE", "CORE_STATE_CRITICALITY",
    "CORE_IMMINENT_FUSION", "ROD_BANK_POS_0_ACTUAL", "POWER_DEMAND_MW",
    "CONDENSER_VOLUME", "CONDENSER_VAPOR_VOLUME", "CONDENSER_VACUUM",
    "CONDENSER_VACUUM_PUMP_ACTIVE", "CONDENSER_CIRCULATION_PUMP_ACTIVE",
    "FREIGHT_PUMP_CONDENSER_ACTIVE", "COOLANT_CORE_PRIMARY_LOOP_LEVEL",
    "PRESSURIZER_FILL_LEVEL", "CORE_PRIMARY_CIRCUIT_COOLING_TANK_VOLUME",
    "CORE_POOL_COOLANT_TANK_VOLUME", "CORE_EXTERNAL_COOLANT_RESERVOIR_VOLUME",
    "FREIGHT_PUMP_FEEDWATER_ACTIVE", "VACUUM_RETENTION_TANK_VOLUME",
    "STEAM_EJECTOR_CONDENSER_RETURN_VALVE_ACTUAL", "STEAM_EJECTOR_CONDENSER_RETURN_VALVE_ORDERED",
    "CORE_IODINE_GENERATION", "CORE_IODINE_CUMULATIVE",
    "CORE_XENON_GENERATION", "CORE_XENON_CUMULATIVE",
    "POWER_FROM_TURBINE_KW", "POWER_FROM_EXTERNAL_KW", "EMERGENCY_GENERATOR_POWER_OUTPUT_KW",
    "EMERGENCY_BATTERIES_POWER_OUTPUT_KW", "RES_DIVERT_SURPLUS_FROM_MW",
    "RES_EFFECTIVELY_DERIVED_ENERGY_MW", "RES_ABSORPTION_CAPACITY_MW",
    "RESISTOR_BANK_01_SWITCH", "RESISTOR_BANK_02_SWITCH", "RESISTOR_BANK_03_SWITCH",
    "RESISTOR_BANK_04_SWITCH", "RESISTOR_BANKS_MAIN_SWITCH",
    "EMERGENCY_GENERATOR_1_MODE", "EMERGENCY_GENERATOR_1_STATUS",
    "EMERGENCY_GENERATOR_1_PRESSURIZER", "EMERGENCY_GENERATOR_1_FUEL",
    "EMERGENCY_GENERATOR_1_MAINTENANCE_NEEDED", "EMERGENCY_GENERATOR_2_MODE",
    "EMERGENCY_GENERATOR_2_STATUS", "EMERGENCY_GENERATOR_2_PRESSURIZER",
    "EMERGENCY_GENERATOR_2_FUEL", "EMERGENCY_GENERATOR_2_MAINTENANCE_NEEDED",
]
for i in range(3):
    READABLE += [
        f"GENERATOR_{i}_KW", f"GENERATOR_{i}_V", f"GENERATOR_{i}_A",
        f"GENERATOR_{i}_HERTZ", f"GENERATOR_{i}_BREAKER",
        f"STEAM_TURBINE_{i}_RPM", f"STEAM_TURBINE_{i}_INSTALLED",
        f"STEAM_GEN_{i}_STATUS", f"STEAM_GEN_{i}_OUTLET",
        f"MSCV_{i}_OPENING_ACTUAL", f"STEAM_TURBINE_{i}_BYPASS_ACTUAL",
        f"COOLANT_CORE_CIRCULATION_PUMP_{i}_STATUS",
        f"COOLANT_SEC_CIRCULATION_PUMP_{i}_STATUS",
        f"COOLANT_CORE_CIRCULATION_PUMP_{i}_ORDERED_SPEED",
        f"COOLANT_SEC_CIRCULATION_PUMP_{i}_ORDERED_SPEED",
        f"COOLANT_SEC_{i}_LIQUID_VOLUME",
        f"COOLANT_CORE_CIRCULATION_PUMP_{i}_DRY_STATUS",
        f"COOLANT_CORE_CIRCULATION_PUMP_{i}_OVERLOAD_STATUS",
    ]

WRITABLE = {
    "CORE_SCRAM_BUTTON", "ROD_BANK_POS_0_ORDERED", "FREIGHT_PUMP_CONDENSER_SWITCH",
    "CONDENSER_VACUUM_PUMP_START_STOP", "CONDENSER_CIRCULATION_PUMP_SWITCH",
    "CONDENSER_CIRCULATION_PUMP_ORDERED_SPEED", "STEAM_EJECTOR_CONDENSER_RETURN_VALVE",
    "FREIGHT_PUMP_FEEDWATER_SWITCH", "VALVE_OPEN", "VALVE_CLOSE", "VALVE_OFF",
    "RESISTOR_BANK_01_SWITCH", "RESISTOR_BANK_02_SWITCH", "RESISTOR_BANK_03_SWITCH",
    "RESISTOR_BANK_04_SWITCH", "RESISTOR_BANKS_MAIN_SWITCH",
}
CHEM_READABLE = [
    "CHEM_TRUCK_IN_ZONE", "CHEM_TRUCK_CONNECTED", "CHEM_BORON_DOSAGE_ORDERED",
    "CHEM_BORON_DOSAGE_ACTUAL", "CHEM_BORON_FILTER_ORDERED", "CHEM_BORON_FILTER_ACTUAL",
    "CHEM_BORON_PPM", "CHEMICAL_DOSING_PUMP_STATUS", "CHEMICAL_DOSING_PUMP_DRY_STATUS",
    "CHEMICAL_DOSING_PUMP_OVERLOAD_STATUS", "CHEMICAL_FILTER_PUMP_STATUS",
    "CHEMICAL_FILTER_PUMP_DRY_STATUS", "CHEMICAL_FILTER_PUMP_OVERLOAD_STATUS",
]
CHEM_WRITABLE = {"CHEM_BORON_DOSAGE_ORDERED_RATE", "CHEM_BORON_FILTER_ORDERED_SPEED"}
TEXT_CODE_VARIABLES = {
    "CORE_STATE", "COOLANT_CORE_STATE", "RODS_STATUS", "CONDENSER_VACUUM_PUMP_MODE",
    "EMERGENCY_GENERATOR_1_MODE", "EMERGENCY_GENERATOR_1_STATUS", "EMERGENCY_GENERATOR_1_PRESSURIZER",
    "EMERGENCY_GENERATOR_2_MODE", "EMERGENCY_GENERATOR_2_STATUS", "EMERGENCY_GENERATOR_2_PRESSURIZER",
}
for i in range(3):
    WRITABLE |= {
        f"MSCV_{i}_OPENING_ORDERED", f"STEAM_TURBINE_{i}_BYPASS_ORDERED",
        f"COOLANT_CORE_CIRCULATION_PUMP_{i}_ORDERED_SPEED",
        f"COOLANT_SEC_CIRCULATION_PUMP_{i}_ORDERED_SPEED",
    }


class Plant:
    def __init__(self, chemistry_enabled: bool = False) -> None:
        self.lock = threading.RLock()
        self.running = True
        self.readable = list(READABLE)
        self.writable = set(WRITABLE)
        self.values: dict[str, Any] = {
            "GAME_VERSION": "2.2.25.213", "GAME_SIM_SPEED": 1, "CORE_TEMP": 329.0,
            "CORE_TEMP_MAX": 450.0, "CORE_PRESSURE": 151.0, "CORE_PRESSURE_MAX": 180.0,
            "CORE_INTEGRITY": 100.0, "CORE_STATE": "OPERATIVO", "CORE_STATE_CRITICALITY": 0.01,
            "CORE_IMMINENT_FUSION": False, "ROD_BANK_POS_0_ACTUAL": 69.0, "POWER_DEMAND_MW": 120.0,
            "CONDENSER_VOLUME": 55000.0, "CONDENSER_VAPOR_VOLUME": 45000.0, "CONDENSER_VACUUM": 1.0,
            "CONDENSER_VACUUM_PUMP_ACTIVE": True, "CONDENSER_CIRCULATION_PUMP_ACTIVE": True,
            "FREIGHT_PUMP_CONDENSER_ACTIVE": False, "COOLANT_CORE_PRIMARY_LOOP_LEVEL": 88.0,
            "PRESSURIZER_FILL_LEVEL": 60.0, "CORE_PRIMARY_CIRCUIT_COOLING_TANK_VOLUME": 106030.0,
            "CORE_POOL_COOLANT_TANK_VOLUME": 80000.0, "CORE_EXTERNAL_COOLANT_RESERVOIR_VOLUME": 150000.0,
            "FREIGHT_PUMP_FEEDWATER_ACTIVE": False, "VACUUM_RETENTION_TANK_VOLUME": 22000.0,
            "STEAM_EJECTOR_CONDENSER_RETURN_VALVE_ACTUAL": 0.0,
            "STEAM_EJECTOR_CONDENSER_RETURN_VALVE_ORDERED": 0.0,
            "CORE_IODINE_GENERATION": 0.8, "CORE_IODINE_CUMULATIVE": 12.0,
            "CORE_XENON_GENERATION": 0.6, "CORE_XENON_CUMULATIVE": 8.0,
            "POWER_FROM_TURBINE_KW": 126000.0, "POWER_FROM_EXTERNAL_KW": 0.0,
            "EMERGENCY_GENERATOR_POWER_OUTPUT_KW": 0.0, "EMERGENCY_BATTERIES_POWER_OUTPUT_KW": 0.0,
            "RES_DIVERT_SURPLUS_FROM_MW": 6.0, "RES_EFFECTIVELY_DERIVED_ENERGY_MW": 6.0,
            "RES_ABSORPTION_CAPACITY_MW": 25.0, "RESISTOR_BANK_01_SWITCH": True,
            "RESISTOR_BANK_02_SWITCH": False, "RESISTOR_BANK_03_SWITCH": False,
            "RESISTOR_BANK_04_SWITCH": False, "RESISTOR_BANKS_MAIN_SWITCH": True,
            "EMERGENCY_GENERATOR_1_MODE": "AUTOMÁTICO", "EMERGENCY_GENERATOR_1_STATUS": "APAGADO",
            "EMERGENCY_GENERATOR_1_PRESSURIZER": "PRESURIZADO", "EMERGENCY_GENERATOR_1_FUEL": 486.0,
            "EMERGENCY_GENERATOR_1_MAINTENANCE_NEEDED": False,
            "EMERGENCY_GENERATOR_2_MODE": "AUTOMÁTICO", "EMERGENCY_GENERATOR_2_STATUS": "EN ESPERA",
            "EMERGENCY_GENERATOR_2_PRESSURIZER": "NO PRESURIZADO", "EMERGENCY_GENERATOR_2_FUEL": 420.0,
            "EMERGENCY_GENERATOR_2_MAINTENANCE_NEEDED": False,
        }
        if chemistry_enabled:
            self.readable.extend(CHEM_READABLE)
            self.writable.update(CHEM_WRITABLE)
            self.values.update({
                "CHEM_TRUCK_IN_ZONE": True, "CHEM_TRUCK_CONNECTED": True,
                "CHEM_BORON_DOSAGE_ORDERED": 0.0, "CHEM_BORON_DOSAGE_ACTUAL": 0.0,
                "CHEM_BORON_FILTER_ORDERED": 0.0, "CHEM_BORON_FILTER_ACTUAL": 0.0,
                "CHEM_BORON_PPM": 1000.0,
                "CHEMICAL_DOSING_PUMP_STATUS": 0, "CHEMICAL_DOSING_PUMP_DRY_STATUS": 4,
                "CHEMICAL_DOSING_PUMP_OVERLOAD_STATUS": 4,
                "CHEMICAL_FILTER_PUMP_STATUS": 0, "CHEMICAL_FILTER_PUMP_DRY_STATUS": 4,
                "CHEMICAL_FILTER_PUMP_OVERLOAD_STATUS": 4,
            })
        self.commands: list[tuple[str, Any]] = []
        for i in range(3):
            self.values.update({
                f"GENERATOR_{i}_KW": 43000.0 - i * 1000, f"GENERATOR_{i}_V": 13800.0,
                f"GENERATOR_{i}_A": 3100.0 - i * 50, f"GENERATOR_{i}_HERTZ": 50.0,
                f"GENERATOR_{i}_BREAKER": False, f"STEAM_TURBINE_{i}_RPM": 3000.0,
                f"STEAM_TURBINE_{i}_INSTALLED": True,
                f"STEAM_GEN_{i}_STATUS": 2, f"STEAM_GEN_{i}_OUTLET": 80.0,
                f"MSCV_{i}_OPENING_ACTUAL": 8.0,
                f"STEAM_TURBINE_{i}_BYPASS_ACTUAL": 0.0,
                f"COOLANT_CORE_CIRCULATION_PUMP_{i}_STATUS": 2,
                f"COOLANT_SEC_CIRCULATION_PUMP_{i}_STATUS": 2,
                f"COOLANT_CORE_CIRCULATION_PUMP_{i}_ORDERED_SPEED": 65.0,
                f"COOLANT_SEC_CIRCULATION_PUMP_{i}_ORDERED_SPEED": 40.0,
                f"COOLANT_SEC_{i}_LIQUID_VOLUME": 25000.0,
                f"COOLANT_CORE_CIRCULATION_PUMP_{i}_DRY_STATUS": 4,
                f"COOLANT_CORE_CIRCULATION_PUMP_{i}_OVERLOAD_STATUS": 4,
            })
        self.thread = threading.Thread(target=self._simulate, daemon=True)
        self.thread.start()

    def set(self, variable: str, value: Any) -> None:
        value = self._coerce(value)
        with self.lock:
            self.commands.append((variable, value))
            if variable == "CORE_SCRAM_BUTTON" and value:
                self.values["ROD_BANK_POS_0_ACTUAL"] = 100.0
            elif variable == "ROD_BANK_POS_0_ORDERED":
                self.values["ROD_BANK_POS_0_ACTUAL"] = float(value)
            elif variable.startswith("MSCV_") and variable.endswith("_OPENING_ORDERED"):
                self.values[variable.replace("ORDERED", "ACTUAL")] = float(value)
            elif variable.startswith("STEAM_TURBINE_") and variable.endswith("_BYPASS_ORDERED"):
                self.values[variable.replace("ORDERED", "ACTUAL")] = float(value)
            elif variable == "FREIGHT_PUMP_CONDENSER_SWITCH":
                self.values["FREIGHT_PUMP_CONDENSER_ACTIVE"] = bool(value)
            elif variable == "CONDENSER_VACUUM_PUMP_START_STOP":
                self.values["CONDENSER_VACUUM_PUMP_ACTIVE"] = bool(value)
            elif variable == "CONDENSER_CIRCULATION_PUMP_SWITCH":
                self.values["CONDENSER_CIRCULATION_PUMP_ACTIVE"] = bool(value)
            elif variable == "STEAM_EJECTOR_CONDENSER_RETURN_VALVE":
                self.values["STEAM_EJECTOR_CONDENSER_RETURN_VALVE_ORDERED"] = float(value)
                self.values["STEAM_EJECTOR_CONDENSER_RETURN_VALVE_ACTUAL"] = float(value)
            elif variable == "FREIGHT_PUMP_FEEDWATER_SWITCH":
                self.values["FREIGHT_PUMP_FEEDWATER_ACTIVE"] = bool(value)
            elif variable == "CHEM_BORON_DOSAGE_ORDERED_RATE":
                rate = max(0.0, min(100.0, float(value)))
                self.values["CHEM_BORON_DOSAGE_ORDERED"] = rate
                self.values["CHEM_BORON_DOSAGE_ACTUAL"] = rate
                self.values["CHEMICAL_DOSING_PUMP_STATUS"] = 2 if rate > 0 else 0
            elif variable == "CHEM_BORON_FILTER_ORDERED_SPEED":
                speed = max(0.0, min(100.0, float(value)))
                self.values["CHEM_BORON_FILTER_ORDERED"] = speed
                self.values["CHEM_BORON_FILTER_ACTUAL"] = speed
                self.values["CHEMICAL_FILTER_PUMP_STATUS"] = 2 if speed > 0 else 0
            elif variable in self.values:
                self.values[variable] = value

    @staticmethod
    def _coerce(value: Any) -> Any:
        text = str(value).strip().lower()
        if text in ("true", "false"):
            return text == "true"
        try:
            number = float(text)
            return int(number) if number.is_integer() else number
        except ValueError:
            return value

    def _simulate(self) -> None:
        phase = 0.0
        while self.running:
            with self.lock:
                phase += 0.04
                rods = float(self.values["ROD_BANK_POS_0_ACTUAL"])
                target_temp = 330 + (70 - rods) * 1.8
                self.values["CORE_TEMP"] += (target_temp - self.values["CORE_TEMP"]) * 0.035
                self.values["CORE_STATE_CRITICALITY"] = (70 - rods) / 100 + math.sin(phase) * 0.004
                for i in range(3):
                    opening = float(self.values[f"MSCV_{i}_OPENING_ACTUAL"])
                    target_power = opening * 5500
                    self.values[f"GENERATOR_{i}_KW"] += (target_power - self.values[f"GENERATOR_{i}_KW"]) * 0.08
                generated_kw = sum(float(self.values[f"GENERATOR_{i}_KW"]) for i in range(3))
                self.values["POWER_FROM_TURBINE_KW"] = generated_kw
                surplus_mw = max(0.0, generated_kw / 1000.0 - float(self.values["POWER_DEMAND_MW"]))
                active_banks = sum(bool(self.values[f"RESISTOR_BANK_0{i}_SWITCH"]) for i in range(1, 5))
                capacity_mw = 25.0 * active_banks if self.values["RESISTOR_BANKS_MAIN_SWITCH"] else 0.0
                self.values["RES_DIVERT_SURPLUS_FROM_MW"] = surplus_mw
                self.values["RES_ABSORPTION_CAPACITY_MW"] = capacity_mw
                self.values["RES_EFFECTIVELY_DERIVED_ENERGY_MW"] = min(surplus_mw, capacity_mw)
                if self.values["FREIGHT_PUMP_CONDENSER_ACTIVE"]:
                    self.values["CONDENSER_VOLUME"] = min(70000, self.values["CONDENSER_VOLUME"] + 120)
                    self.values["CONDENSER_VAPOR_VOLUME"] = max(30000, self.values["CONDENSER_VAPOR_VOLUME"] - 120)
                if self.values["FREIGHT_PUMP_FEEDWATER_ACTIVE"]:
                    self.values["COOLANT_CORE_PRIMARY_LOOP_LEVEL"] = min(100, self.values["COOLANT_CORE_PRIMARY_LOOP_LEVEL"] + .08)
                retention_valve = float(self.values["STEAM_EJECTOR_CONDENSER_RETURN_VALVE_ACTUAL"])
                if retention_valve > 0:
                    self.values["VACUUM_RETENTION_TANK_VOLUME"] = max(
                        0, self.values["VACUUM_RETENTION_TANK_VOLUME"] - retention_valve * 8
                    )
                elif self.values["CONDENSER_VACUUM_PUMP_ACTIVE"]:
                    self.values["VACUUM_RETENTION_TANK_VOLUME"] = min(
                        40000, self.values["VACUUM_RETENTION_TANK_VOLUME"] + 4
                    )
                if "CHEM_BORON_PPM" in self.values:
                    dosage = float(self.values["CHEM_BORON_DOSAGE_ACTUAL"])
                    filtering = float(self.values["CHEM_BORON_FILTER_ACTUAL"])
                    self.values["CHEM_BORON_PPM"] = max(0.0, self.values["CHEM_BORON_PPM"] + (dosage - filtering) * .002)
            time.sleep(0.2)

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=1)


class MockHandler(BaseHTTPRequestHandler):
    plant: Plant

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        variable = query.get("variable", [""])[0].upper()
        if variable == "WEBSERVER_LIST_VARIABLES":
            self._send(f"GET:{','.join(self.plant.readable)}\nPOST:{','.join(sorted(self.plant.writable))}", "text/plain")
        elif variable == "WEBSERVER_BATCH_GET":
            names = query.get("value", [""])[0].split(",")
            with self.plant.lock:
                values = {
                    name: 0 if name in TEXT_CODE_VARIABLES and self.plant.values.get(name) is not None else self.plant.values.get(name)
                    for name in names
                }
            self._send(json.dumps({"values": values, "errors": {}}), "application/json")
        elif variable == "VALVE_PANEL_JSON":
            self._send(json.dumps({"valves": [{"Name": "Valvula_Pressurizer_Spray", "Value": 0, "IsOpened": False, "IsClosed": True, "Actuator": "OFF"}]}), "application/json")
        elif variable in self.plant.values:
            self._send(str(self.plant.values[variable]), "text/plain")
        else:
            self._send("NOT FOUND", "text/plain")

    def do_POST(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        variable = query.get("variable", [""])[0].upper()
        value = query.get("value", [""])[0]
        if variable not in self.plant.writable:
            self._send("NOT FOUND", "text/plain", 404)
            return
        self.plant.set(variable, value)
        self._send("OK", "text/plain")

    def _send(self, text: str, content_type: str, status: int = 200) -> None:
        raw = text.encode()
        self.send_response(status); self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)


def run(port: int = 8785, chemistry_enabled: bool = False) -> None:
    plant = Plant(chemistry_enabled=chemistry_enabled); MockHandler.plant = plant; server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    print(f"Simulateur Nucleares sur http://127.0.0.1:{port}/ — Ctrl+C pour arrêter")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown(); server.server_close(); plant.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--port", type=int, default=8785)
    parser.add_argument("--chemistry", action="store_true", help="Activer le module chimique simulé")
    args = parser.parse_args(); run(args.port, args.chemistry)
