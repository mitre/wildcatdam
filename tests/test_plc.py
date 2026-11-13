import unittest

from pymodbus.pdu.bit_message import WriteMultipleCoilsRequest
from pymodbus.pdu.register_message import WriteSingleRegisterRequest

from simulator import SIM
from simulator.config import load_config
from simulator.plc import COILS, DISCRETE_INPUTS, HOLDING_REGISTERS, INPUT_REGISTERS, build_datastore, log_request, tick
from simulator.sim import DamState


class PLCTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.device = build_datastore(self.config)

    def test_config_defaults_match_the_run_script(self):
        modbus = SIM["protocols"]["modbus"]
        self.assertEqual(self.config["server"]["host"], modbus["bind"])
        self.assertEqual(self.config["server"]["port"], modbus["port"])

    def test_datastore_uses_protocol_addresses(self):
        self.assertEqual(self.device.getValues(COILS, 0, count=6), [False] * 6)
        self.assertEqual(self.device.getValues(HOLDING_REGISTERS, 0, count=7), [40, 1, 3, 6, 60, 75, 85])
        self.assertEqual(self.device.getValues(INPUT_REGISTERS, 0, count=3), [10, 3, 0])

    def test_tick_publishes_door_states_and_keeps_manual_coils(self):
        self.device.setValues(INPUT_REGISTERS, 0, [90])
        self.device.setValues(COILS, 0, [False, False, True, False, False, True])
        sim = DamState()
        tick(self.device, sim)

        self.assertEqual(sim.doors, (1, 1, 1))
        self.assertEqual(self.device.getValues(DISCRETE_INPUTS, 0, count=4), [True, True, True, True])
        self.assertEqual(self.device.getValues(COILS, 0, count=6), [False, False, True, False, False, True])
        self.assertEqual(self.device.getValues(INPUT_REGISTERS, 2)[0], int(sim.cumulative_water_released))

    def test_override_closes_a_door_the_logic_would_open(self):
        self.device.setValues(INPUT_REGISTERS, 0, [90])
        self.device.setValues(COILS, 3, [True])
        tick(self.device, DamState())
        self.assertEqual(self.device.getValues(DISCRETE_INPUTS, 0, count=4), [True, False, True, True])

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


if __name__ == "__main__":
    unittest.main()
