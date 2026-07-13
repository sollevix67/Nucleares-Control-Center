import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
from mock_game import MockHandler, Plant


class MockServer:
    def __init__(self, chemistry_enabled=False):
        self.chemistry_enabled = chemistry_enabled

    def __enter__(self):
        self.plant = Plant(chemistry_enabled=self.chemistry_enabled)
        MockHandler.plant = self.plant
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        return self

    def __exit__(self, *args):
        self.server.shutdown(); self.server.server_close(); self.plant.close(); self.thread.join(timeout=1)


class GameClientTests(unittest.TestCase):
    def test_discovery_batch_and_write(self):
        with MockServer() as mock:
            client = app.GameClient(mock.url)
            readable, writable = client.discover()
            self.assertIn("CORE_TEMP", readable)
            self.assertIn("CORE_SCRAM_BUTTON", writable)
            values = client.batch_get(["CORE_TEMP", "GENERATOR_0_KW"])
            self.assertIsInstance(values["CORE_TEMP"], float)
            client.set_value("ROD_BANK_POS_0_ORDERED", 77.5)
            self.assertEqual(mock.plant.values["ROD_BANK_POS_0_ACTUAL"], 77.5)

    def test_unknown_write_is_refused(self):
        with MockServer() as mock:
            client = app.GameClient(mock.url); client.discover()
            with self.assertRaises(ValueError):
                client.set_value("NOT_A_GAME_COMMAND", 1)


class ControlTests(unittest.TestCase):
    def config(self, url):
        config = app.deep_merge({}, app.DEFAULT_CONFIG)
        config["game_url"] = url; config["poll_seconds"] = .2; config["control_seconds"] = .2
        config["autopilot"]["auto_start"] = False
        return config

    def test_alarm_and_derived_values(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                state = dict(mock.plant.values); state["CORE_TEMP"] = 395
                center.derived = center._derive(state); center._evaluate_alarms(state)
                self.assertIn("core_temp", center.alarms)
                self.assertAlmostEqual(center.derived["condenser_fill_pct"], 55.0)
                self.assertAlmostEqual(center.derived["vacuum_pct"], 100.0)
                self.assertEqual(center.derived["core_state"], "OPÉRATIONNEL")
                self.assertNotIn("vacuum", center.alarms)
                expected_power = sum(state[f"GENERATOR_{i}_KW"] for i in range(3))
                self.assertAlmostEqual(center.derived["generated_kw"], expected_power)
                reservoir_ids = {item["id"] for item in center.derived["reservoirs"]}
                self.assertIn("primary_cooling_tank", reservoir_ids)
                self.assertIn("external_coolant", reservoir_ids)
                self.assertEqual(len(center.derived["generators"]["main"]), 3)
                self.assertEqual(center.derived["generators"]["main"][0]["status"], "COUPLÉ")
                self.assertEqual(len(center.derived["generators"]["emergency"]), 2)
                emergency = center.derived["generators"]["emergency"]
                self.assertEqual(emergency[0]["status"], "ARRÊTÉ")
                self.assertEqual(emergency[0]["mode"], "AUTOMATIQUE")
                self.assertEqual(emergency[0]["fuel"], 486.0)
                self.assertEqual(emergency[0]["fuel_unit"], "L")
                self.assertEqual(emergency[0]["pressurizer"], "PRESSURISÉ")
                self.assertEqual(emergency[1]["status"], "EN ATTENTE")
                self.assertEqual(center.derived["chemical_reservoirs"], [])
            finally:
                app.DATA_DIR = old_data

    def test_autopilot_writes_controls(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                state = center.client.batch_get(center.readable); center.state = state; center.derived = center._derive(state)
                center._autopilot_step(state, 5.0)
                commands = {name for name, _ in mock.plant.commands}
                self.assertIn("ROD_BANK_POS_0_ORDERED", commands)
                self.assertIn("MSCV_0_OPENING_ORDERED", commands)
                self.assertIn("CONDENSER_CIRCULATION_PUMP_ORDERED_SPEED", commands)
                self.assertNotIn("CHEM_BORON_DOSAGE_ORDERED_RATE", commands)
                self.assertEqual(center._chemistry_info(state)["status"], "unavailable")
            finally:
                app.DATA_DIR = old_data

    def test_uninstalled_circuits_receive_no_commands(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                state = center.client.batch_get(center.readable)
                for index in (1, 2):
                    state[f"STEAM_TURBINE_{index}_INSTALLED"] = False
                    state[f"STEAM_GEN_{index}_STATUS"] = 4
                    state[f"COOLANT_CORE_CIRCULATION_PUMP_{index}_STATUS"] = 4
                    state[f"COOLANT_SEC_CIRCULATION_PUMP_{index}_STATUS"] = 4
                    state[f"STEAM_TURBINE_{index}_BYPASS_ACTUAL"] = 5.0
                state["STEAM_TURBINE_0_BYPASS_ACTUAL"] = 5.0
                center.state = state
                center.derived = center._derive(state)

                center._autopilot_step(state, 5.0)
                commands = {name for name, _ in mock.plant.commands}
                self.assertIn("MSCV_0_OPENING_ORDERED", commands)
                self.assertIn("STEAM_TURBINE_0_BYPASS_ORDERED", commands)
                self.assertIn("COOLANT_CORE_CIRCULATION_PUMP_0_ORDERED_SPEED", commands)
                self.assertIn("COOLANT_SEC_CIRCULATION_PUMP_0_ORDERED_SPEED", commands)
                for index in (1, 2):
                    self.assertNotIn(f"MSCV_{index}_OPENING_ORDERED", commands)
                    self.assertNotIn(f"STEAM_TURBINE_{index}_BYPASS_ORDERED", commands)
                    self.assertNotIn(f"COOLANT_CORE_CIRCULATION_PUMP_{index}_ORDERED_SPEED", commands)
                    self.assertNotIn(f"COOLANT_SEC_CIRCULATION_PUMP_{index}_ORDERED_SPEED", commands)

                with mock.plant.lock:
                    mock.plant.commands.clear()
                center.last_write.clear()
                center.emergency_scram("test circuits", state)
                scram_commands = {name for name, _ in mock.plant.commands}
                self.assertIn("COOLANT_CORE_CIRCULATION_PUMP_0_ORDERED_SPEED", scram_commands)
                self.assertNotIn("COOLANT_CORE_CIRCULATION_PUMP_1_ORDERED_SPEED", scram_commands)
                self.assertNotIn("COOLANT_CORE_CIRCULATION_PUMP_2_ORDERED_SPEED", scram_commands)
            finally:
                app.DATA_DIR = old_data

    def test_installed_train_tracks_demand_without_secondary_pump(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                state = center.client.batch_get(center.readable)
                state.update({
                    "POWER_DEMAND_MW": 80.0,
                    "GENERATOR_0_KW": 40000.0,
                    "MSCV_0_OPENING_ACTUAL": 8.0,
                    "STEAM_TURBINE_0_BYPASS_ACTUAL": 0.0,
                    "STEAM_TURBINE_0_INSTALLED": True,
                    "STEAM_GEN_0_STATUS": 2,
                    "COOLANT_SEC_CIRCULATION_PUMP_0_STATUS": 4,
                })
                for index in (1, 2):
                    state[f"STEAM_TURBINE_{index}_INSTALLED"] = False
                    state[f"STEAM_GEN_{index}_STATUS"] = 4

                center._control_grid(state, 5.0, secondary=True)
                commands = dict(mock.plant.commands)
                self.assertGreater(commands["MSCV_0_OPENING_ORDERED"], state["MSCV_0_OPENING_ACTUAL"])
                self.assertEqual(commands["STEAM_TURBINE_0_BYPASS_ORDERED"], 0.0)
                self.assertNotIn("COOLANT_SEC_CIRCULATION_PUMP_0_ORDERED_SPEED", commands)
                self.assertNotIn("MSCV_1_OPENING_ORDERED", commands)
                self.assertNotIn("MSCV_2_OPENING_ORDERED", commands)

                with mock.plant.lock:
                    mock.plant.commands.clear()
                center.last_write.clear()
                center.train_pid[0].reset()
                state.update({
                    "POWER_DEMAND_MW": 40.0,
                    "GENERATOR_0_KW": 90000.0,
                    "MSCV_0_OPENING_ACTUAL": 12.0,
                })
                center._control_grid(state, 5.0, secondary=False)
                commands = dict(mock.plant.commands)
                self.assertLess(commands["MSCV_0_OPENING_ORDERED"], state["MSCV_0_OPENING_ACTUAL"])
                self.assertEqual(commands["STEAM_TURBINE_0_BYPASS_ORDERED"], 0.0)
            finally:
                app.DATA_DIR = old_data

    def test_chemistry_captures_current_ppm(self):
        with MockServer(chemistry_enabled=True) as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                state = center.client.batch_get(center.readable)
                center._control_chemistry(state)
                self.assertEqual(center.dynamic_boron_target, 1000.0)
                self.assertEqual(center._chemistry_info(state)["status"], "ready")
                chemistry_commands = [name for name, _ in mock.plant.commands if name.startswith("CHEM_")]
                self.assertEqual(chemistry_commands, [])
            finally:
                app.DATA_DIR = old_data

    def test_chemistry_doses_and_filters(self):
        with MockServer(chemistry_enabled=True) as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                config = self.config(mock.url); config["autopilot"]["target_boron_ppm"] = 1000.0
                center = app.ControlCenter(config)
                center.readable, center.writable = center.client.discover()
                with mock.plant.lock:
                    mock.plant.values["CHEM_BORON_PPM"] = 950.0
                state = center.client.batch_get(center.readable)
                center._control_chemistry(state)
                commands = dict(mock.plant.commands)
                self.assertGreater(commands["CHEM_BORON_DOSAGE_ORDERED_RATE"], 0)

                with mock.plant.lock:
                    mock.plant.commands.clear()
                    mock.plant.values["CHEM_BORON_PPM"] = 1050.0
                center.last_write.clear()
                state = center.client.batch_get(center.readable)
                center._control_chemistry(state)
                commands = dict(mock.plant.commands)
                self.assertEqual(commands["CHEM_BORON_DOSAGE_ORDERED_RATE"], 0)
                self.assertGreater(commands["CHEM_BORON_FILTER_ORDERED_SPEED"], 0)
            finally:
                app.DATA_DIR = old_data

    def test_chemistry_uses_local_tank_without_truck(self):
        with MockServer(chemistry_enabled=True) as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                config = self.config(mock.url); config["autopilot"]["target_boron_ppm"] = 1000.0
                center = app.ControlCenter(config)
                center.readable, center.writable = center.client.discover()
                with mock.plant.lock:
                    mock.plant.values["CHEM_TRUCK_IN_ZONE"] = False
                    mock.plant.values["CHEM_TRUCK_CONNECTED"] = False
                    mock.plant.values["CHEM_BORON_PPM"] = 950.0
                state = center.client.batch_get(center.readable)
                center.autopilot_enabled = True
                center._evaluate_alarms(state)
                center._control_chemistry(state)
                self.assertEqual(center._chemistry_info(state)["status"], "ready")
                self.assertNotIn("chemistry_connection", center.alarms)
                commands = dict(mock.plant.commands)
                self.assertGreater(commands["CHEM_BORON_DOSAGE_ORDERED_RATE"], 0)
            finally:
                app.DATA_DIR = old_data

    def test_future_chemical_tank_level_is_discovered(self):
        with MockServer(chemistry_enabled=True) as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                state = dict(mock.plant.values)
                state["CHEM_BORIC_ACID_TANK_LEVEL"] = 49.0
                derived = center._derive(state)
                self.assertEqual(len(derived["chemical_reservoirs"]), 1)
                tank = derived["chemical_reservoirs"][0]
                self.assertEqual(tank["variable"], "CHEM_BORIC_ACID_TANK_LEVEL")
                self.assertEqual(tank["value"], 49.0)
                self.assertEqual(tank["unit"], "%")
            finally:
                app.DATA_DIR = old_data

    def test_chemistry_not_installed_is_silent(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                center.writable.update({center.CHEM_DOSAGE_COMMAND, center.CHEM_FILTER_COMMAND})
                state = center.client.batch_get(center.readable)
                state.update({
                    "CHEM_BORON_PPM": None,
                    "CHEMICAL_DOSING_PUMP_STATUS": 4,
                    "CHEMICAL_FILTER_PUMP_STATUS": 4,
                    "CHEM_TRUCK_IN_ZONE": False,
                    "CHEM_TRUCK_CONNECTED": False,
                })
                center.autopilot_enabled = True
                center._evaluate_alarms(state)
                center._control_chemistry(state)
                self.assertEqual(center._chemistry_info(state)["status"], "not_installed")
                self.assertNotIn("chemistry_connection", center.alarms)
                self.assertFalse(any(name.startswith("CHEM_") for name, _ in mock.plant.commands))
            finally:
                app.DATA_DIR = old_data

    def test_scram(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url)); center.readable, center.writable = center.client.discover()
                center.emergency_scram("test")
                self.assertEqual(mock.plant.values["ROD_BANK_POS_0_ACTUAL"], 100.0)
            finally:
                app.DATA_DIR = old_data

    def test_dashboard_api_end_to_end(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            center = None; dashboard = None; thread = None
            try:
                center = app.ControlCenter(self.config(mock.url)); center.start()
                deadline = time.time() + 3
                while not center.connected and time.time() < deadline:
                    time.sleep(.05)
                self.assertTrue(center.connected)
                app.DashboardHandler.center = center
                dashboard = ThreadingHTTPServer(("127.0.0.1", 0), app.DashboardHandler)
                thread = threading.Thread(target=dashboard.serve_forever, daemon=True); thread.start()
                base = f"http://127.0.0.1:{dashboard.server_address[1]}"
                health = json.load(urllib.request.urlopen(base + "/health"))
                self.assertTrue(health["ok"]); self.assertTrue(health["game_connected"])
                html = urllib.request.urlopen(base + "/").read().decode("utf-8")
                self.assertIn("NUCLEARES", html); self.assertIn("autopilot-toggle", html)
                self.assertIn("reservoir-list", html); self.assertIn("main-generator-list", html)
                javascript = urllib.request.urlopen(base + "/app.js").read().decode("utf-8")
                self.assertIn("d.vacuum_pct", javascript)
                self.assertIn('generator.fuel_unit || "L"', javascript)
                request = urllib.request.Request(base + "/api/autopilot", data=b'{"enabled":true}',
                                                 headers={"Content-Type": "application/json"}, method="POST")
                response = json.load(urllib.request.urlopen(request))
                self.assertTrue(response["enabled"])
                snapshot = json.load(urllib.request.urlopen(base + "/api/state"))
                self.assertEqual(snapshot["capabilities"]["readable"], len(center.readable))
                self.assertGreater(snapshot["capabilities"]["writable"], 10)
                self.assertGreaterEqual(len(snapshot["derived"]["reservoirs"]), 5)
                self.assertEqual(len(snapshot["derived"]["generators"]["main"]), 3)
                history = json.load(urllib.request.urlopen(base + "/api/history?variables=CORE_TEMP&seconds=60"))
                self.assertIn("CORE_TEMP", history)
            finally:
                if dashboard:
                    dashboard.shutdown(); dashboard.server_close()
                if thread:
                    thread.join(timeout=1)
                if center:
                    center.stop()
                app.DATA_DIR = old_data


if __name__ == "__main__":
    unittest.main()
