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
from pymodbus.client import ModbusTcpClient

from simulator import SIM

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


# --- Modbus Poller ---
def modbus_poller(host, port, state, lock):
    """Poll the dam over Modbus, update shared state, and drain queued coil writes."""
    client = ModbusTcpClient(host=host, port=port)
    failing = False
    while True:
        try:
            if not client.connected:
                client.connect()

            # Drain button writes before reading back the new state
            while not cmd_q.empty():
                addr, value = cmd_q.get()
                result = client.write_coil(addr, value)  # Coils 0-2 position, 3-5 override
                text = "ON" if value else "OFF"
                if result.isError():
                    logger.warning("PLC rejected coil %s = %s: %s", addr, text, result)
                else:
                    logger.info("Wrote coil %s = %s", addr, text)

            water_level, _, released = checked(client.read_input_registers(0, count=3)).registers  # Input registers 0-2
            hr = checked(client.read_holding_registers(0, count=7)).registers  # Holding registers 0-6
            coils = checked(client.read_coils(0, count=6)).bits  # Coils 0-2 positions, 3-5 overrides
            doors = checked(client.read_discrete_inputs(1, count=3)).bits  # Discrete inputs 1-3 door states

            d1, d2, d3 = int(doors[0]), int(doors[1]), int(doors[2])

            with lock:
                state.close_level = hr[0]
                state.threshold_1, state.threshold_2, state.threshold_3 = hr[4], hr[5], hr[6]
                if state.last_released is not None:
                    state.cumulative_water_released += (released - state.last_released) % 65536
                state.last_released = released
                state.water_levels.append(water_level)
                state.door_1_status.append(d1)
                state.door_2_status.append(d2)
                state.door_3_status.append(d3)
                state.cumulative_release.append(state.cumulative_water_released)
                state.door_overrides = [int(coils[3]), int(coils[4]), int(coils[5])]
                state.door_positions = [int(coils[0]), int(coils[1]), int(coils[2])]
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
    """Flip the manual override enable for a door (coils 3-5)."""
    with lock:
        current = state.door_overrides[door]
    cmd_q.put((3 + door, not current))


def toggle_position(state, door):
    """Flip the manual position for a door (coils 0-2)."""
    with lock:
        current = state.door_positions[door]
    cmd_q.put((door, not current))


def main(argv=None):
    port = SIM["protocols"]["modbus"]["port"]
    parser = argparse.ArgumentParser(
        prog="python -m simulator.hmi",
        description="Wildcat Dam HMI: polls the PLC over Modbus/TCP.",
    )
    parser.add_argument("--host", default="127.0.0.1", metavar="HOST", help="PLC to poll (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=port, metavar="PORT", help=f"PLC port (default: {port})")
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
    threading.Thread(target=modbus_poller, args=(args.host, args.port, state, lock), daemon=True).start()
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
