import time
import threading
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import tkinter as tk
import yaml
import logging

from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
from pymodbus.device import ModbusDeviceIdentification

# --- Logging Setup ---
logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.INFO)

# --- Constant Vars ---
config = yaml.safe_load(open("config.yaml"))

# --- Load Config (Now MODBus) ---
# The purpose of this function is to fetch the modbus state into more easlity digestible variables.
def fetch_modbus_config():
    global THRESHOLD_1, THRESHOLD_2, THRESHOLD_3, CLOSE_LEVEL, reduction_rates

    # Fetch thresholds and close_level from holding registers
    THRESHOLD_1 = context[0x00].getValues(3, 4, count=1)[0]  # Holding register 5
    THRESHOLD_2 = context[0x00].getValues(3, 5, count=1)[0]  # Holding register 6
    THRESHOLD_3 = context[0x00].getValues(3, 6, count=1)[0]  # Holding register 7
    CLOSE_LEVEL = context[0x00].getValues(3, 0, count=1)[0]  # Holding register 1

    reduction_rates = {
        "door_1": context[0x00].getValues(3, 1, count=1)[0],  # Holding register 2
        "door_2": context[0x00].getValues(3, 2, count=1)[0],  # Holding register 3
        "door_3": context[0x00].getValues(3, 3, count=1)[0],  # Holding register 4
    }

# --- Shared Context ---
context = None  # Will be initialized in start_server()

# --- Datastore Setup (LOADING MODBUS VALUES)---
# This function initializes all of the modbus configuration from the YAML. 
def build_datastore(config):
    dev = config["device"]
    co = [0] * dev["setup"]["co size"]
    di = [0] * dev["setup"]["di size"]
    hr = [0] * dev["setup"]["hr size"]
    ir = [0] * dev["setup"]["ir size"]

    for item in dev.get("coils", []):
        co[item["addr"]] = item["value"]
    for item in dev.get("discrete_inputs", []):
        di[item["addr"]] = item["value"]
    for item in dev.get("holding_registers", []):
        hr[item["addr"]] = item["value"]
    for item in dev.get("input_registers", []):
        ir[item["addr"]] = item["value"]

    return ModbusSlaveContext(
        co=ModbusSequentialDataBlock(0, co),
        di=ModbusSequentialDataBlock(0, di),
        hr=ModbusSequentialDataBlock(0, hr),
        ir=ModbusSequentialDataBlock(0, ir),
    )

# --- Start MODBus Server ---
# This function starts the modbus server and logs the state of the values. 
# You can comment out the logging if you want.
def start_server():
    global context
    datastore = build_datastore(config)
    context = ModbusServerContext(slaves=datastore, single=True)

    # Logging
    original_get = context[0x00].getValues
    original_set = context[0x00].setValues

    def logging_getValues(fc, address, count=1):
        values = original_get(fc, address, count)
        log.info(f"[READ] fc={fc} address={address} count={count} -> values={values}")
        return values

    def logging_setValues(fc, address, values):
        log.info(f"[WRITE] fc={fc} address={address} values={values}")
        original_set(fc, address, values)

    context[0x00].getValues = logging_getValues
    context[0x00].setValues = logging_setValues

    print("\n" + "-" * 50 + "\n")

    identity = ModbusDeviceIdentification()
    for key, value in config["server"]["identity"].items():
        setattr(identity, key, value)

    host = config["server"]["host"]
    port = config["server"]["port"]
    log.info(f"Starting MODBUS Server on {host}:{port}")
    StartTcpServer(context, identity=identity, address=(host, port))

# --- State Tracking for Logic ---
water_levels, cumulative_release, door_1_status, door_2_status, door_3_status = [], [], [], [], []
cumulative_water_released = 0

# Maintain previous door state globally or within a class/module
previous_d1_state = [0]  # Using a list so it’s mutable in nested scopes

# This is all of the door logic. First door and override status is fetched from modbus and then all of the logic is executed to determine door state.
def control_doors(water_level):
    # Manual override states
    override_d1 = context[0x00].getValues(1, 3, count=1)[0] # Coil 3: override enable for door_1
    override_d2 = context[0x00].getValues(1, 4, count=1)[0] # Coil 4
    override_d3 = context[0x00].getValues(1, 5, count=1)[0] # Coil 5

    # Manual positions
    manual_d1 = context[0x00].getValues(1, 0, count=1)[0]
    manual_d2 = context[0x00].getValues(1, 1, count=1)[0]
    manual_d3 = context[0x00].getValues(1, 2, count=1)[0]

    # --- Hysteresis logic for door 1 ---
    if water_level > THRESHOLD_1:
        auto_d1 = 1  # Open the gate
    elif water_level < CLOSE_LEVEL:
        auto_d1 = 0  # Close the gate
    else:
        auto_d1 = previous_d1_state[0]  # Maintain current state

    auto_d2 = int(water_level > THRESHOLD_2)
    auto_d3 = int(water_level > THRESHOLD_3)

    # Apply manual override
    d1 = manual_d1 if override_d1 else auto_d1
    d2 = manual_d2 if override_d2 else auto_d2
    d3 = manual_d3 if override_d3 else auto_d3

    # Update previous state
    previous_d1_state[0] = d1

    # Store status
    door_1_status.append(d1)
    door_2_status.append(d2)
    door_3_status.append(d3)

    # Update coils and discrete inputs
    context[0x00].setValues(1, 0, [d1])
    context[0x00].setValues(2, 0, [1 if any([d1, d2, d3]) else 0])

    return d1, d2, d3

# This calculates how much water should be released.
# NOTE: Released water is relative to total water to simulate water pressure from gravity.
def reduce_water_level(water_level, d1, d2, d3, surge_rate):
    global cumulative_water_released
    reduction = 0
    if d1: reduction += water_level * (reduction_rates["door_1"] / 100)
    if d2: reduction += water_level * (reduction_rates["door_2"] / 100)
    if d3: reduction += water_level * (reduction_rates["door_3"] / 100)

    cumulative_water_released += reduction
    return min(100, max(0, water_level - reduction + surge_rate))

# --- Graph Updater ---
# This executes all of the logic to execute the graphs.
def update_graphs(canvas, axes):
    global cumulative_water_released
    while context is None:
        time.sleep(0.5)

    while True:
        try:
            # Fetch from MODBUS
            water_level = context[0x00].getValues(4, 0, count=1)[0]  # Input register 0
            surge_pct = context[0x00].getValues(4, 1, count=1)[0]    # Input register 1

            # Fetch dynamic configuration
            fetch_modbus_config()

            # Control & update
            d1, d2, d3 = control_doors(water_level)
            water_level = reduce_water_level(water_level, d1, d2, d3, surge_pct)
            context[0x00].setValues(4, 0, [int(water_level)])

            # Track state
            water_levels.append(water_level)
            cumulative_release.append(cumulative_water_released)

            # Plot
            axes[0].clear()
            axes[0].plot(water_levels, label="Water Level")
            axes[0].axhline(y=CLOSE_LEVEL, color="grey", linestyle="--", label="Close Level")
            axes[0].axhline(y=THRESHOLD_1, color="green", linestyle="--", label="Threshold 1")
            axes[0].axhline(y=THRESHOLD_2, color="orange", linestyle="--", label="Threshold 2")
            axes[0].axhline(y=THRESHOLD_3, color="red", linestyle="--", label="Threshold 3")
            axes[0].set_ylim(0, 100)
            axes[0].legend(loc="upper left")

            axes[1].clear()
            axes[1].plot(door_1_status, label="Door 1")
            axes[1].plot(door_2_status, label="Door 2")
            axes[1].plot(door_3_status, label="Door 3")
            axes[1].legend()

            axes[2].clear()
            axes[2].plot(cumulative_release, label="Cumulative Released", color="purple")
            axes[2].legend()

            canvas.draw()
            time.sleep(1)

        except Exception as e:
            log.warning(f"Graph update error: {e}")
            time.sleep(1)

# --- GUI ---
# This initialize the GUI that pops up.
def launch_gui():
    root = tk.Tk()
    root.title("Dam Modbus Simulation")
    root.geometry("1000x800")

    graph_frame = tk.Frame(root)
    graph_frame.pack(fill=tk.BOTH, expand=True)

    fig, axes = plt.subplots(3, 1, figsize=(8, 12))
    fig.tight_layout(pad=5.0)
    canvas = FigureCanvasTkAgg(fig, master=graph_frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    threading.Thread(target=update_graphs, args=(canvas, axes), daemon=True).start()
    root.mainloop()

# --- Run ---
if __name__ == "__main__":
    threading.Thread(target=start_server, daemon=True).start()
    launch_gui()