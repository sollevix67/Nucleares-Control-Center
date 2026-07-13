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

    def test_batch_get_refreshes_text_localization_codes(self):
        with MockServer() as mock:
            client = app.GameClient(mock.url)
            values = client.batch_get([
                "CORE_STATE", "EMERGENCY_GENERATOR_1_STATUS",
                "EMERGENCY_GENERATOR_1_MODE", "EMERGENCY_GENERATOR_1_PRESSURIZER",
            ])
            self.assertEqual(values["CORE_STATE"], "OPERATIVO")
            self.assertEqual(values["EMERGENCY_GENERATOR_1_STATUS"], "APAGADO")
            self.assertEqual(values["EMERGENCY_GENERATOR_1_MODE"], "AUTOMÁTICO")
            self.assertEqual(values["EMERGENCY_GENERATOR_1_PRESSURIZER"], "PRESURIZADO")

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

    def test_emergency_generators_default_to_automatic_detection(self):
        overrides = app.DEFAULT_CONFIG["equipment_overrides"]["emergency_generators"]
        self.assertEqual(overrides, {"1": "auto", "2": "auto"})
        self.assertEqual(app.DEFAULT_CONFIG["autopilot"]["grid_buffer_mw"], 0.0)

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
                reservoirs = {item["id"]: item for item in center.derived["reservoirs"]}
                self.assertEqual(reservoirs["core_pool_tank"]["capacity"], 100000.0)
                self.assertEqual(reservoirs["core_pool_tank"]["percent"], 80.0)
                self.assertEqual(reservoirs["external_coolant"]["capacity"], 200000.0)
                self.assertEqual(reservoirs["external_coolant"]["percent"], 75.0)
                self.assertEqual(len(center.derived["generators"]["main"]), 3)
                self.assertEqual(center.derived["generators"]["main"][0]["status"], "COUPLÉ")
                self.assertEqual(len(center.derived["generators"]["emergency"]), 2)
                emergency = center.derived["generators"]["emergency"]
                self.assertTrue(emergency[0]["installed"])
                self.assertEqual(emergency[0]["installation_status"], "INSTALLÉ")
                low_fuel_state = dict(state)
                low_fuel_state["EMERGENCY_GENERATOR_1_FUEL"] = 4.0
                self.assertTrue(center._derive(low_fuel_state)["generators"]["emergency"][0]["installed"])
                self.assertEqual(emergency[0]["status"], "ARRÊTÉ")
                self.assertEqual(emergency[0]["mode"], "AUTOMATIQUE")
                self.assertEqual(emergency[0]["fuel"], 486.0)
                self.assertEqual(emergency[0]["fuel_unit"], "L")
                self.assertEqual(emergency[0]["pressurizer"], "PRESSURISÉ")
                self.assertTrue(emergency[1]["installed"])
                self.assertEqual(emergency[1]["status"], "EN ATTENTE")
                self.assertEqual(emergency[1]["installation_source"], "DÉTECTION AUTO")
                electrical = center.derived["electrical"]
                self.assertEqual(len(electrical["transformers"]), 3)
                self.assertTrue(electrical["transformers"][0]["available"])
                self.assertEqual(electrical["transformers"][0]["telemetry"], "INDIRECTE")
                self.assertTrue(electrical["resistors"]["available"])
                self.assertTrue(electrical["resistors"]["main_on"])
                self.assertEqual(len(electrical["resistors"]["banks"]), 4)
                self.assertEqual(electrical["resistors"]["active_banks"], 1)
                self.assertEqual(center.derived["chemical_reservoirs"], [])
            finally:
                app.DATA_DIR = old_data

    def test_emergency_generators_are_shown_when_not_installed(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                with mock.plant.lock:
                    mock.plant.values.update({
                        "EMERGENCY_GENERATOR_1_STATUS": "No instalada (sin comprar)",
                        "EMERGENCY_GENERATOR_1_MODE": "No instalado",
                        "EMERGENCY_GENERATOR_1_PRESSURIZER": "Sin instalar",
                        "EMERGENCY_GENERATOR_1_FUEL": 0,
                    })
                    for name in list(mock.plant.values):
                        if name.startswith("EMERGENCY_GENERATOR_2_"):
                            mock.plant.values[name] = None
                state = center.client.batch_get(center.readable)
                emergency = center._derive(state)["generators"]["emergency"]
                self.assertEqual(len(emergency), 2)
                self.assertFalse(emergency[0]["installed"])
                self.assertFalse(emergency[1]["installed"])
                self.assertEqual(emergency[0]["status"], "NON INSTALLÉ")
                self.assertIsNone(emergency[0]["fuel"])
            finally:
                app.DATA_DIR = old_data

    def test_spanish_generator_statuses_are_translated(self):
        self.assertEqual(app.game_text_fr("Modo automático"), "AUTOMATIQUE")
        self.assertEqual(app.game_text_fr("Sin combustible"), "SANS CARBURANT")
        self.assertEqual(app.game_text_fr("Despresurizado"), "DÉPRESSURISÉ")
        self.assertEqual(app.game_text_fr("Requiere mantenimiento"), "MAINTENANCE REQUISE")
        self.assertEqual(app.game_text_fr("NOREACTIVO"), "NON RÉACTIF")
        self.assertEqual(app.game_text_fr("No reactivo"), "NON RÉACTIF")
        self.assertEqual(app.game_text_fr("NO_REACTIVO"), "NON RÉACTIF")
        self.assertEqual(app.game_text_fr("REACTIVO"), "RÉACTIF")
        self.assertEqual(app.game_text_fr("OPERACIONAL"), "OPÉRATIONNEL")
        self.assertEqual(app.game_text_fr("CIRCULANDO"), "EN CIRCULATION")

    def test_nonreactive_core_state_is_translated(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                state = dict(mock.plant.values); state["CORE_STATE"] = "NOREACTIVO"
                self.assertEqual(center._derive(state)["core_state"], "NON RÉACTIF")
            finally:
                app.DATA_DIR = old_data

    def test_reactive_core_state_is_translated(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                state = dict(mock.plant.values); state["CORE_STATE"] = "REACTIVO"
                self.assertEqual(center._derive(state)["core_state"], "RÉACTIF")
            finally:
                app.DATA_DIR = old_data

    def test_emergency_generator_manual_override_has_priority(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                config = self.config(mock.url)
                center = app.ControlCenter(config)
                state = dict(mock.plant.values)
                automatic = center._derive(state)["generators"]["emergency"][1]
                self.assertTrue(automatic["installed"])
                config["equipment_overrides"]["emergency_generators"]["2"] = "not_installed"
                forced = center._derive(state)["generators"]["emergency"][1]
                self.assertFalse(forced["installed"])
                self.assertEqual(forced["installation_source"], "RÉGLAGE MANUEL")
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

                for _ in range(3):
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
                center.mscv_ordered.clear()
                center.bypass_ordered.clear()
                state.update({
                    "POWER_DEMAND_MW": 40.0,
                    "GENERATOR_0_KW": 90000.0,
                    "MSCV_0_OPENING_ACTUAL": 12.0,
                })
                for _ in range(2):
                    center._control_grid(state, 5.0, secondary=False)
                commands = dict(mock.plant.commands)
                self.assertLess(commands["MSCV_0_OPENING_ORDERED"], state["MSCV_0_OPENING_ACTUAL"])
                self.assertEqual(commands["STEAM_TURBINE_0_BYPASS_ORDERED"], 10)
            finally:
                app.DATA_DIR = old_data

    def test_mscv_order_accumulates_past_integer_actual_value(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                state = center.client.batch_get(center.readable)
                state.update({
                    "POWER_DEMAND_MW": 10.0,
                    "GENERATOR_2_KW": 40756.46,
                    "MSCV_2_OPENING_ACTUAL": 23.0,
                    "STEAM_TURBINE_2_INSTALLED": True,
                    "STEAM_GEN_2_STATUS": 2,
                })
                for index in (0, 1):
                    state[f"STEAM_TURBINE_{index}_INSTALLED"] = False
                    state[f"STEAM_GEN_{index}_STATUS"] = 4
                center.derived = {"poisons": {"guard_active": False}}
                with mock.plant.lock:
                    mock.plant.commands.clear()

                for _ in range(5):
                    center._control_grid(state, 5.0, secondary=False)

                orders = [
                    value for name, value in mock.plant.commands
                    if name == "MSCV_2_OPENING_ORDERED"
                ]
                self.assertEqual(orders, [23, 22])
                self.assertTrue(all(isinstance(value, int) for value in orders))
                self.assertEqual(center.mscv_ordered[2], 21.5)
                bypass_orders = [
                    value for name, value in mock.plant.commands
                    if name == "STEAM_TURBINE_2_BYPASS_ORDERED"
                ]
                self.assertEqual(bypass_orders, [5, 10])
                self.assertTrue(all(isinstance(value, int) for value in bypass_orders))
            finally:
                app.DATA_DIR = old_data

    def test_command_values_are_integer_except_control_rods(self):
        with MockServer(chemistry_enabled=True) as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                with mock.plant.lock:
                    mock.plant.commands.clear()
                center._write("test", "MSCV_0_OPENING_ORDERED", 12.7, "test", cooldown=0)
                center._write("test", "COOLANT_SEC_CIRCULATION_PUMP_0_ORDERED_SPEED", 47.55, "test", cooldown=0)
                center._write("test", center.CHEM_DOSAGE_COMMAND, 2.4, "test", cooldown=0)
                center._write("test", "ROD_BANK_POS_0_ORDERED", 77.56, "test", cooldown=0)
                commands = dict(mock.plant.commands)
                self.assertEqual(commands["MSCV_0_OPENING_ORDERED"], 13)
                self.assertEqual(commands["COOLANT_SEC_CIRCULATION_PUMP_0_ORDERED_SPEED"], 48)
                self.assertEqual(commands[center.CHEM_DOSAGE_COMMAND], 2)
                self.assertEqual(commands["ROD_BANK_POS_0_ORDERED"], 77.6)
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

    def test_xenon_tracking_raises_alarm_and_enables_guard(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                state = dict(mock.plant.values)
                center._update_poison_tracking(state, now=100.0)
                state["CORE_XENON_CUMULATIVE"] = 12.4
                state["CORE_IODINE_CUMULATIVE"] = 13.2
                center._update_poison_tracking(state, now=160.0)
                center.derived = center._derive(state)
                poisons = center.derived["poisons"]
                self.assertTrue(poisons["available"])
                self.assertTrue(poisons["guard_active"])
                self.assertAlmostEqual(poisons["xenon"]["ratio"], 1.55)
                self.assertGreater(poisons["xenon"]["trend_per_min"], 0)
                center._evaluate_alarms(state)
                self.assertIn("xenon_high", center.alarms)
                self.assertEqual(center.alarms["xenon_high"].severity, "critical")
            finally:
                app.DATA_DIR = old_data

    def test_xenon_guard_limits_power_target_ramp_without_fun_commands(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center.readable, center.writable = center.client.discover()
                state = center.client.batch_get(center.readable)
                state.update({
                    "POWER_DEMAND_MW": 120.0,
                    "GENERATOR_0_KW": 40000.0,
                    "STEAM_TURBINE_0_INSTALLED": True,
                    "STEAM_GEN_0_STATUS": 2,
                })
                for index in (1, 2):
                    state[f"STEAM_TURBINE_{index}_INSTALLED"] = False
                    state[f"STEAM_GEN_{index}_STATUS"] = 4
                center.derived = {"poisons": {"guard_active": True}}
                center._control_grid(state, 5.0, secondary=False)
                self.assertAlmostEqual(center.filtered_grid_target_kw, 40833.3333, places=3)
                self.assertTrue(mock.plant.commands)
                self.assertFalse(any(name.startswith("FUN_") for name, _ in mock.plant.commands))
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

    def test_global_alarm_acknowledgement(self):
        with MockServer() as mock, tempfile.TemporaryDirectory() as temp:
            old_data = app.DATA_DIR; app.DATA_DIR = Path(temp)
            try:
                center = app.ControlCenter(self.config(mock.url))
                center._set_alarm("one", "warning", "Alarme 1", "test", True)
                center._set_alarm("two", "critical", "Alarme 2", "test", True)
                self.assertTrue(center.acknowledge("one"))
                self.assertEqual(center.acknowledge_all(), 1)
                self.assertTrue(all(alarm.acknowledged for alarm in center.alarms.values()))
                self.assertEqual(center.acknowledge_all(), 0)
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
                self.assertIn("pool-capacity", html); self.assertIn("external-capacity", html)
                self.assertIn('id="pool-capacity" type="number" min="1" max="10000000" step="1"', html)
                self.assertIn('id="external-capacity" type="number" min="1" max="10000000" step="1"', html)
                self.assertIn('id="xenon-rise-guard" type="number" min="0.01" max="100" step="0.01"', html)
                self.assertIn('id="xenon-power-ramp" type="number" min="0.1" max="100" step="0.1"', html)
                self.assertIn('data-supervision-zone="reactor"', html)
                self.assertIn('data-supervision-zone="chemistry"', html)
                self.assertIn('id="poison-chart"', html)
                self.assertIn('id="xenon-power-ramp"', html)
                self.assertIn('id="ack-all"', html)
                self.assertIn('id="alarm-sound-test"', html)
                self.assertIn('id="emergency-generator-2-installation"', html)
                self.assertIn('id="transformer-list"', html)
                self.assertIn('id="resistor-banks"', html)
                javascript = urllib.request.urlopen(base + "/app.js").read().decode("utf-8")
                self.assertIn("d.vacuum_pct", javascript)
                self.assertIn('generator.fuel_unit || "L"', javascript)
                self.assertIn("generator.installation_status", javascript)
                self.assertIn("switchSupervisionZone", javascript)
                self.assertIn("renderPoisons", javascript)
                self.assertIn("acknowledgeAll", javascript)
                self.assertIn("alarm-needs-ack", javascript)
                self.assertIn("unlockAlarmAudio", javascript)
                self.assertIn("pendingAlarmSeverity", javascript)
                self.assertIn("window.AudioContext || window.webkitAudioContext", javascript)
                self.assertIn("playAlarmTone", javascript)
                self.assertIn('NOREACTIVO:"NON RÉACTIF"', javascript)
                self.assertIn('REACTIVO:"RÉACTIF"', javascript)
                self.assertIn('OPERACIONAL:"OPÉRATIONNEL"', javascript)
                self.assertIn('CIRCULANDO:"EN CIRCULATION"', javascript)
                self.assertIn("renderElectrical", javascript)
                self.assertIn("BYPASS ${fmt", javascript)
                request = urllib.request.Request(base + "/api/autopilot", data=b'{"enabled":true}',
                                                 headers={"Content-Type": "application/json"}, method="POST")
                response = json.load(urllib.request.urlopen(request))
                self.assertTrue(response["enabled"])
                center._set_alarm("global_test", "warning", "Test global", "test", True)
                request = urllib.request.Request(base + "/api/ack-all", data=b'{}',
                                                 headers={"Content-Type": "application/json"}, method="POST")
                response = json.load(urllib.request.urlopen(request))
                self.assertEqual(response["acknowledged"], 1)
                snapshot = json.load(urllib.request.urlopen(base + "/api/state"))
                self.assertEqual(snapshot["capabilities"]["readable"], len(center.readable))
                self.assertGreater(snapshot["capabilities"]["writable"], 10)
                self.assertGreaterEqual(len(snapshot["derived"]["reservoirs"]), 5)
                self.assertEqual(len(snapshot["derived"]["generators"]["main"]), 3)
                self.assertTrue(next(a for a in snapshot["alarms"] if a["alarm_id"] == "global_test")["acknowledged"])
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
