import copy
import unittest

from pymodbus.pdu.bit_message import WriteMultipleCoilsRequest
from pymodbus.pdu.register_message import WriteSingleRegisterRequest

from simulator import SIM
from simulator.config import POINTS, load_addresses, load_config
from simulator.hmi import State, cmd_q, read_points, toggle_override, toggle_position, write_queued_coils
from simulator.plc import (
    COILS,
    DISCRETE_INPUTS,
    HOLDING_REGISTERS,
    INPUT_REGISTERS,
    Points,
    build_datastore,
    log_request,
    tick,
)
from simulator.sim import DamState


def moved(config, table, name, addr):
    """Return a copy of the config with one point at a new address."""
    config = copy.deepcopy(config)
    for item in config["device"][table]:
        if item["name"] == name:
            item["addr"] = addr
    return config


class PLCTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.device = build_datastore(self.config)
        self.points = Points(self.device, load_addresses(self.config))

    def test_config_defaults_match_the_run_script(self):
        modbus = SIM["protocols"]["modbus"]
        self.assertEqual(self.config["server"]["host"], modbus["bind"])
        self.assertEqual(self.config["server"]["port"], modbus["port"])

    def test_datastore_uses_protocol_addresses(self):
        self.assertEqual(self.device.getValues(COILS, 0, count=6), [False] * 6)
        self.assertEqual(self.device.getValues(HOLDING_REGISTERS, 0, count=7), [40, 1, 3, 6, 60, 75, 85])
        self.assertEqual(self.device.getValues(INPUT_REGISTERS, 0, count=3), [10, 3, 0])

    def test_default_addresses_are_unchanged(self):
        # The fact sources and scenario docs use these addresses.
        addresses = load_addresses(self.config)
        for table, names in POINTS.items():
            self.assertEqual(list(addresses[table].values()), list(range(len(names))), table)

    def test_tick_publishes_door_states_and_keeps_manual_coils(self):
        self.device.setValues(INPUT_REGISTERS, 0, [90])
        self.device.setValues(COILS, 0, [False, False, True, False, False, True])
        sim = DamState()
        tick(self.points, sim)

        self.assertEqual(sim.doors, (1, 1, 1))
        self.assertEqual(self.device.getValues(DISCRETE_INPUTS, 0, count=4), [True, True, True, True])
        self.assertEqual(self.device.getValues(COILS, 0, count=6), [False, False, True, False, False, True])
        self.assertEqual(self.device.getValues(INPUT_REGISTERS, 2)[0], int(sim.cumulative_water_released))

    def test_override_closes_a_door_the_logic_would_open(self):
        self.device.setValues(INPUT_REGISTERS, 0, [90])
        self.device.setValues(COILS, 3, [True])
        tick(self.points, DamState())
        self.assertEqual(self.device.getValues(DISCRETE_INPUTS, 0, count=4), [True, False, True, True])

    def test_a_moved_point_is_used_at_its_new_address(self):
        config = moved(self.config, "holding_registers", "thresholds.threshold_1", 9)
        config = moved(config, "coils", "door_1.override_enable", 8)
        device = build_datastore(config)
        points = Points(device, load_addresses(config))
        self.assertEqual(device.getValues(HOLDING_REGISTERS, 9)[0], 60)

        device.setValues(HOLDING_REGISTERS, 9, [20])  # door 1 now opens at 20
        device.setValues(INPUT_REGISTERS, 0, [30])
        sim = DamState()
        tick(points, sim)
        self.assertEqual(sim.threshold_1, 20)
        self.assertEqual(sim.doors, (1, 0, 0))

        device.setValues(COILS, 8, [True])  # override door 1 shut from its new coil
        tick(points, sim)
        self.assertEqual(sim.doors, (0, 0, 0))

    def test_the_plc_publishes_at_moved_addresses(self):
        config = self.config
        for table, name, addr in (
            ("discrete_inputs", "any_door_open", 9), ("discrete_inputs", "door_1.state", 8),
            ("discrete_inputs", "door_2.state", 7), ("discrete_inputs", "door_3.state", 6),
            ("input_registers", "water_level", 9), ("input_registers", "water_released", 8),
        ):
            config = moved(config, table, name, addr)
        device = build_datastore(config)
        points = Points(device, load_addresses(config))
        device.setValues(INPUT_REGISTERS, 9, [90])  # all three doors open at 90
        sim = DamState()
        tick(points, sim)

        self.assertEqual(sim.doors, (1, 1, 1))
        self.assertEqual(device.getValues(DISCRETE_INPUTS, 6, count=4), [True, True, True, True])
        self.assertEqual(device.getValues(DISCRETE_INPUTS, 0, count=4), [False] * 4)
        self.assertEqual(device.getValues(INPUT_REGISTERS, 8)[0], int(sim.cumulative_water_released))
        self.assertEqual(device.getValues(INPUT_REGISTERS, 9)[0], int(sim.water_level))
        self.assertEqual(device.getValues(INPUT_REGISTERS, 0)[0], 0)

    def test_bad_addresses_are_rejected(self):
        def with_entry(table, entry):
            config = copy.deepcopy(self.config)
            config["device"][table].append(entry)
            return config

        missing = copy.deepcopy(self.config)
        missing["device"]["discrete_inputs"].pop()
        hr_wide = moved(self.config, "holding_registers", "close_level", 200)
        hr_wide["device"]["setup"]["hr size"] = 300
        cases = {
            "no address for door_3.state": missing,
            "door_1.position is listed twice": with_entry("coils", {"name": "door_1.position", "addr": 9, "value": 0}),
            "door_2.position shares address 0": moved(self.config, "coils", "door_2.position", 0),
            "unnamed entry at 0 shares address 0": with_entry("coils", {"addr": 0, "value": 1}),
            "surge_rate address 10 is outside 0-9": moved(self.config, "input_registers", "surge_rate", 10),
            "surge_rate address -1 is outside 0-9": moved(self.config, "input_registers", "surge_rate", -1),
            "unnamed entry at -2 address -2 is outside": with_entry("coils", {"addr": -2, "value": 1}),
            "surge_rate address must be a whole number": moved(self.config, "input_registers", "surge_rate", 1.0),
            "close_level address must be a whole number": moved(self.config, "holding_registers", "close_level", True),
            "unknown point door_1.postion": with_entry("coils", {"name": "door_1.postion", "addr": 9, "value": 0}),
            "points span 200 addresses": hr_wide,
        }
        for message, config in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message.replace(".", r"\.")):
                load_addresses(config)

    def test_a_config_without_point_names_is_rejected(self):
        config = copy.deepcopy(self.config)
        for table in POINTS:
            for item in config["device"][table]:
                del item["name"]
        with self.assertRaisesRegex(ValueError, "no address for door_1.position"):
            load_addresses(config)

    def test_client_writes_are_logged(self):
        with self.assertLogs("simulator.plc", "INFO") as logs:
            log_request(self.device, False, WriteMultipleCoilsRequest(address=3, bits=[True, True, False]))
            log_request(self.device, False, WriteSingleRegisterRequest(address=5, registers=[5]))
            log_request(self.device, False, WriteSingleRegisterRequest(address=200, registers=[7]))
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            [
                "Client wrote coils 3-5 = ON,ON,OFF",
                "Client wrote holding register 5 = 5",
                "Rejected client write to holding register 200 = 7: address out of range",
            ],
        )


class FakeClient:
    """Answers reads from a PLC datastore the way a Modbus client would."""

    def __init__(self, device):
        self.device = device

    def _read(self, fc, attr, start, count):
        values = self.device.getValues(fc, start, count=count)
        return type("Response", (), {"isError": lambda self: False, attr: values})()

    def read_coils(self, start, count):
        return self._read(COILS, "bits", start, count)

    def read_discrete_inputs(self, start, count):
        return self._read(DISCRETE_INPUTS, "bits", start, count)

    def read_holding_registers(self, start, count):
        return self._read(HOLDING_REGISTERS, "registers", start, count)

    def read_input_registers(self, start, count):
        return self._read(INPUT_REGISTERS, "registers", start, count)

    def write_coil(self, addr, value):
        self.device.setValues(COILS, addr, [value])
        return type("Response", (), {"isError": lambda self: False})()


class HMITests(unittest.TestCase):
    def test_hmi_reads_points_at_their_configured_addresses(self):
        # Every input register moves off 0, so the read starts at 5.
        config = moved(load_config(), "input_registers", "water_level", 7)
        config = moved(config, "input_registers", "surge_rate", 5)
        config = moved(config, "input_registers", "water_released", 9)
        config = moved(config, "discrete_inputs", "door_3.state", 6)
        addresses = load_addresses(config)
        device = build_datastore(config)
        device.setValues(INPUT_REGISTERS, 5, [4, 0, 55, 0, 1234])
        device.setValues(DISCRETE_INPUTS, 6, [True])

        point = read_points(FakeClient(device), addresses)
        self.assertEqual((point["surge_rate"], point["water_level"], point["water_released"]), (4, 55, 1234))
        self.assertTrue(point["door_3.state"])
        self.assertEqual(point["close_level"], 40)
        self.assertEqual(set(point), {name for names in POINTS.values() for name in names})

    def test_hmi_buttons_write_the_configured_coils(self):
        config = moved(load_config(), "coils", "door_2.override_enable", 9)
        config = moved(config, "coils", "door_3.position", 8)
        addresses = load_addresses(config)
        device = build_datastore(config)
        client = FakeClient(device)
        state = State()

        toggle_override(state, 1)
        toggle_position(state, 2)
        with self.assertLogs("simulator.hmi", "INFO"):
            write_queued_coils(client, addresses)

        self.assertEqual(device.getValues(COILS, 0, count=10), [False] * 8 + [True, True])
        self.assertEqual(cmd_q.qsize(), 0)


if __name__ == "__main__":
    unittest.main()
