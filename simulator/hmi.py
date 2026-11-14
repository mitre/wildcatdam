"""Wildcat Dam HMI: polls the PLC over Modbus/TCP and plots the dam."""

import argparse
import logging
import queue
import signal
import sys
import threading
import time
from collections import deque

import matplotlib
import yaml
from pymodbus.client import ModbusTcpClient

from simulator import SIM
from simulator.config import DEFAULT_CONFIG, load_addresses, load_config

logger = logging.getLogger(__name__)

# --- Constant Vars ---
POLL_INTERVAL = 1
HISTORY = 240

# --- Shared State ---
lock = threading.Lock()
cmd_q = queue.Queue()


class State:
    def __init__(self):
        self.water_levels, self.cumulative_release = deque(maxlen=HISTORY), deque(maxlen=HISTORY)
        self.door_1_status, self.door_2_status, self.door_3_status = (
            deque(maxlen=HISTORY), deque(maxlen=HISTORY), deque(maxlen=HISTORY))
        self.cumulative_water_released = 0
        self.last_released = None
        self.close_level, self.threshold_1, self.threshold_2, self.threshold_3 = 0, 0, 0, 0
        self.door_overrides = [0, 0, 0]
        self.door_positions = [0, 0, 0]
        self.connected = False


def checked(response):
    """Return a Modbus response, raising if the PLC answered with an error."""
    if response.isError():
        raise ConnectionError(str(response))
    return response


def read_points(client, addresses):
    """
    Read every point the HMI shows, one request per table.

    Args:
        client (ModbusTcpClient): Connected client.
        addresses (dict): {table: {name: address}} from load_addresses().

    Returns:
        dict: {name: value} for every point.
    """
    reads = {
        "coils": lambda start, count: checked(client.read_coils(start, count=count)).bits,
        "discrete_inputs": lambda start, count: checked(client.read_discrete_inputs(start, count=count)).bits,
        "holding_registers": lambda start, count: checked(client.read_holding_registers(start, count=count)).registers,
        "input_registers": lambda start, count: checked(client.read_input_registers(start, count=count)).registers,
    }
    values = {}
    for table, names in addresses.items():
        start = min(names.values())
        block = reads[table](start, max(names.values()) - start + 1)
        values.update({name: block[addr - start] for name, addr in names.items()})
    return values


def write_queued_coils(client, addresses):
    """Write each queued (point name, value) button press to its coil."""
    while not cmd_q.empty():
        name, value = cmd_q.get()
        addr = addresses["coils"][name]
        result = client.write_coil(addr, value)
        text = "ON" if value else "OFF"
        if result.isError():
            logger.warning("PLC rejected coil %s (%s) = %s: %s", addr, name, text, result)
        else:
            logger.info("Wrote coil %s (%s) = %s", addr, name, text)


# --- Modbus Poller ---
def modbus_poller(host, port, state, lock, addresses):
    """Poll the dam over Modbus, update shared state, and drain queued coil writes."""
    client = ModbusTcpClient(host=host, port=port)
    failing = False
    while True:
        try:
            if not client.connected:
                client.connect()

            # Drain button writes before reading back the new state
            write_queued_coils(client, addresses)

            point = read_points(client, addresses)
            water_level, released = point["water_level"], point["water_released"]
            d1, d2, d3 = (int(point[f"door_{n}.state"]) for n in (1, 2, 3))

            with lock:
                state.close_level = point["close_level"]
                state.threshold_1, state.threshold_2, state.threshold_3 = (
                    point[f"thresholds.threshold_{n}"] for n in (1, 2, 3))
                if state.last_released is not None:
                    state.cumulative_water_released += (released - state.last_released) % 65536
                state.last_released = released
                state.water_levels.append(water_level)
                state.door_1_status.append(d1)
                state.door_2_status.append(d2)
                state.door_3_status.append(d3)
                state.cumulative_release.append(state.cumulative_water_released)
                state.door_overrides = [int(point[f"door_{n}.override_enable"]) for n in (1, 2, 3)]
                state.door_positions = [int(point[f"door_{n}.position"]) for n in (1, 2, 3)]
                if not state.connected:
                    logger.info("Connected to PLC at %s:%s, water level %s", host, port, water_level)
                state.connected = True
            failing = False

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            with lock:
                state.connected = False
                state.last_released = None
            if not failing:
                logger.warning("Cannot poll PLC at %s:%s: %s", host, port, e)
            failing = True
            client.close()
            time.sleep(2)


# --- Coil Writes (button control path) ---
def toggle_override(state, door):
    """Flip the manual override enable for a door (0-2)."""
    with lock:
        current = state.door_overrides[door]
    cmd_q.put((f"door_{door + 1}.override_enable", not current))


def toggle_position(state, door):
    """Flip the manual position for a door (0-2)."""
    with lock:
        current = state.door_positions[door]
    cmd_q.put((f"door_{door + 1}.position", not current))


def main(argv=None):
    port = SIM["protocols"]["modbus"]["port"]
    parser = argparse.ArgumentParser(
        prog="python -m simulator.hmi",
        description="Wildcat Dam HMI: polls the PLC over Modbus/TCP.",
    )
    parser.add_argument("--host", default="127.0.0.1", metavar="HOST", help="PLC to poll (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=port, metavar="PORT", help=f"PLC port (default: {port})")
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, metavar="PATH",
        help="configuration file with the PLC's point addresses (default: config.yaml in the repo root)",
    )
    parser.add_argument("--web", action="store_true", help="serve the HMI as a web page instead of a desktop window")
    parser.add_argument("--web-port", type=int, default=8090, metavar="PORT", help="web HMI port (default: 8090)")
    parser.add_argument("--debug", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    logging.getLogger("pymodbus").setLevel(logging.DEBUG if args.debug else logging.CRITICAL)
    logging.getLogger("tornado.access").setLevel(logging.WARNING)

    try:
        addresses = load_addresses(load_config(args.config))
    except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError) as e:
        logger.error("Cannot load %s: %s", args.config, e)
        return 1

    if args.web:
        matplotlib.use("WebAgg")
        matplotlib.rcParams["webagg.address"] = "0.0.0.0"
        matplotlib.rcParams["webagg.port"] = args.web_port
        matplotlib.rcParams["webagg.port_retries"] = 1
        matplotlib.rcParams["webagg.open_in_browser"] = False

    from simulator.ui.desktop_ui import _webagg_fill_window, start_plot

    if args.web:
        from matplotlib.backends.backend_webagg import WebAggApplication

        _webagg_fill_window()
        try:
            WebAggApplication.initialize()
        except (OSError, SystemExit) as e:
            logger.error("Cannot serve the web HMI on port %s: %s", args.web_port, e)
            return 1
    else:
        import matplotlib.pyplot as plt

        if plt.get_backend().lower() == "agg":
            logger.error("Cannot open the HMI window: no display or GUI toolkit. Use --web to serve the HMI as a web page.")
            return 1

    state = State()

    logger.info("Polling PLC at %s:%s", args.host, args.port)
    threading.Thread(target=modbus_poller, args=(args.host, args.port, state, lock, addresses), daemon=True).start()
    if args.web:
        logger.info("Web HMI at http://0.0.0.0:%s", args.web_port)
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    else:
        signal.signal(signal.SIGTERM, lambda *_: plt.close("all"))
    try:
        start_plot(state, lock, lambda d: toggle_override(state, d), lambda d: toggle_position(state, d))
    except KeyboardInterrupt:
        pass
    logger.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
