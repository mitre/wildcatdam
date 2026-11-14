"""Wildcat Dam PLC: runs the dam model and serves it over Modbus/TCP."""

import argparse
import asyncio
import functools
import logging
import signal
import sys
import threading
from collections import deque

import yaml
from pymodbus import ModbusDeviceIdentification
from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
from pymodbus.server import ModbusTcpServer

from simulator.config import DEFAULT_CONFIG, load_addresses, load_config
from simulator.sim import DamState, dam_step

logger = logging.getLogger(__name__)

TICK_SECONDS = 1
HISTORY = 240

COILS, DISCRETE_INPUTS, HOLDING_REGISTERS, INPUT_REGISTERS = 1, 2, 3, 4
DOORS = ("door_1", "door_2", "door_3")
WRITES = {5: "coil", 15: "coils", 6: "holding register", 16: "holding registers"}


# --- Datastore Setup (LOADING MODBUS VALUES)---
def build_datastore(config):
    """
    Initialize the Modbus datastore from the configuration.

    The device context adds 1 to every address it is given, so each block
    starts one entry early and a value for address N is stored at index N + 1.

    Args:
        config (dict): Configuration loaded from config.yaml.

    Returns:
        ModbusDeviceContext: Device context with initialized data blocks.
    """
    dev = config["device"]
    blocks = {}
    for block, key in (("co", "coils"), ("di", "discrete_inputs"), ("hr", "holding_registers"), ("ir", "input_registers")):
        values = [0] * (dev["setup"][f"{block} size"] + 1)
        for item in dev.get(key, []):
            values[item["addr"] + 1] = item["value"]
        blocks[block] = ModbusSequentialDataBlock(0, values)
    return ModbusDeviceContext(**blocks)


def build_identity(config):
    identity = ModbusDeviceIdentification()
    for key, value in config["server"]["identity"].items():
        setattr(identity, key, value)
    return identity


class Points:
    """Reads and writes the PLC's datastore by point name, at the addresses from config.yaml."""

    TABLES = {
        "coils": COILS,
        "discrete_inputs": DISCRETE_INPUTS,
        "holding_registers": HOLDING_REGISTERS,
        "input_registers": INPUT_REGISTERS,
    }

    def __init__(self, device, addresses):
        self.device = device
        self.where = {
            name: (self.TABLES[table], addr) for table, names in addresses.items() for name, addr in names.items()
        }

    def get(self, *names):
        values = [self.device.getValues(*self.where[name], count=1)[0] for name in names]
        return values[0] if len(values) == 1 else values

    def set(self, name, value):
        self.device.setValues(*self.where[name], [value])


# --- Load Config (Now MODBUS) ---
def fetch_modbus_config(points, sim):
    """
    Load the close level, thresholds, and reduction rates from the holding registers.

    Args:
        points (Points): The PLC's datastore, by point name.
        sim (DamState): Simulation state to update.
    """
    sim.close_level = points.get("close_level")
    sim.threshold_1, sim.threshold_2, sim.threshold_3 = points.get(
        "thresholds.threshold_1", "thresholds.threshold_2", "thresholds.threshold_3"
    )
    sim.reduction_rates = {door: points.get(f"reduction_rates.{door}") for door in DOORS}


def control_doors(points, sim):
    """
    Run one step of the door logic from the override and manual-position coils.

    Publishes the door states and the released water total.

    Args:
        points (Points): The PLC's datastore, by point name.
        sim (DamState): Simulation state to update.
    """
    sim.manual = tuple(int(points.get(f"{door}.position")) for door in DOORS)
    sim.overrides = tuple(int(points.get(f"{door}.override_enable")) for door in DOORS)

    dam_step(sim)

    points.set("any_door_open", int(any(sim.doors)))
    for door, state in zip(DOORS, sim.doors):
        points.set(f"{door}.state", state)
    points.set("water_released", int(sim.cumulative_water_released) % 65536)


def tick(points, sim):
    """Advance the dam by one tick: read the inputs, run the model, publish the results."""
    sim.water_level, sim.surge_rate = points.get("water_level", "surge_rate")
    fetch_modbus_config(points, sim)
    control_doors(points, sim)
    points.set("water_level", int(sim.water_level))


class History:
    """Recent values for the PLC's own graphs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.water_levels = deque(maxlen=HISTORY)
        self.doors = [deque(maxlen=HISTORY) for _ in range(3)]
        self.cumulative_release = deque(maxlen=HISTORY)
        self.levels = (0, 0, 0, 0)

    def record(self, sim):
        with self.lock:
            self.water_levels.append(sim.water_level)
            for status, door in zip(self.doors, sim.doors):
                status.append(door)
            self.cumulative_release.append(sim.cumulative_water_released)
            self.levels = (sim.close_level, sim.threshold_1, sim.threshold_2, sim.threshold_3)

    def snapshot(self):
        with self.lock:
            return (
                list(self.water_levels),
                *(list(status) for status in self.doors),
                list(self.cumulative_release),
                *self.levels,
            )


def log_request(device, sending, pdu):
    """Log client writes at INFO and every other request at DEBUG."""
    if sending:
        return pdu
    kind = WRITES.get(pdu.function_code)
    if kind is None:
        logger.debug("Client request: function %s, address %s, count %s", pdu.function_code, pdu.address, pdu.count)
    else:
        values = ["ON" if bit else "OFF" for bit in pdu.bits] if pdu.function_code in (5, 15) else pdu.registers
        last = pdu.address + len(values) - 1
        where = f"{pdu.address}-{last}" if last > pdu.address else str(pdu.address)
        text = ",".join(str(value) for value in values)
        if isinstance(device.getValues(pdu.function_code, pdu.address, count=len(values)), list):
            logger.info("Client wrote %s %s = %s", kind, where, text)
        else:
            logger.info("Rejected client write to %s %s = %s: address out of range", kind, where, text)
    return pdu


def log_connection(connected):
    logger.debug("Client %s", "connected" if connected else "disconnected")


# --- Start MODBUS Server ---
async def serve(points, identity, host, port, sim, history=None, started=None):
    """
    Serve the datastore over Modbus/TCP and run the dam model once a second.

    Returns:
        int: 1 if the server cannot listen. Otherwise runs until cancelled.
    """
    server = ModbusTcpServer(
        ModbusServerContext(devices=points.device, single=True),
        identity=identity,
        address=(host, port),
        trace_pdu=functools.partial(log_request, points.device),
        trace_connect=log_connection,
    )
    try:
        await server.serve_forever(background=True)
    except RuntimeError:
        logger.error("Cannot listen on %s:%s", host, port)
        return 1
    logger.info("PLC listening on %s:%s", host, port)
    if started is not None:
        started.set()

    try:
        while True:
            try:
                tick(points, sim)
                if history is not None:
                    history.record(sim)
            except Exception as e:
                logger.warning("Simulation error: %s", e)
            await asyncio.sleep(TICK_SECONDS)
    finally:
        await server.shutdown()


def run_gui(points, identity, host, port, sim):
    """Serve from a background thread and show the PLC's graphs in a Tk window."""
    try:
        import tkinter

        from simulator.ui.plc_ui import launch_gui
    except ImportError as e:
        logger.error("Cannot open the PLC window: %s", e)
        return 1
    try:
        root = tkinter.Tk()
    except tkinter.TclError as e:
        logger.error("Cannot open the PLC window: %s", e)
        return 1

    history = History()
    started = threading.Event()
    stop = threading.Event()
    result = {}

    def run():
        result["code"] = asyncio.run(serve(points, identity, host, port, sim, history, started))
        started.set()

    threading.Thread(target=run, daemon=True).start()
    started.wait()
    if "code" in result:
        root.destroy()
        return result["code"]

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    launch_gui(root, history.snapshot, stop)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m simulator.plc",
        description="Wildcat Dam PLC: serves the dam model over Modbus/TCP.",
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, metavar="PATH",
        help="configuration file (default: config.yaml in the repo root)",
    )
    parser.add_argument(
        "--host", metavar="HOST", help="address to bind (default: server.host in the config, 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, metavar="PORT", help="Modbus port (default: server.port in the config, 5020)"
    )
    parser.add_argument(
        "--gui", action="store_true", help="also show the PLC's graphs in a desktop window (needs Tkinter)"
    )
    parser.add_argument("--debug", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    logging.getLogger("pymodbus").setLevel(logging.DEBUG if args.debug else logging.ERROR)

    try:
        config = load_config(args.config)
        host = args.host or config["server"]["host"]
        port = args.port or config["server"]["port"]
        addresses = load_addresses(config)  # first, so a bad address gets its own message
        points = Points(build_datastore(config), addresses)
        identity = build_identity(config)
    except (OSError, yaml.YAMLError, KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
        logger.error("Cannot load %s: %s", args.config, e)
        return 1

    sim = DamState()
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        if args.gui:
            code = run_gui(points, identity, host, port, sim)
        else:
            code = asyncio.run(serve(points, identity, host, port, sim))
    except KeyboardInterrupt:
        code = 0
    if code == 0:
        logger.info("Stopped")
    return code


if __name__ == "__main__":
    sys.exit(main())
