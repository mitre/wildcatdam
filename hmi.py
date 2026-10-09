#################################################################
##### Developed by University of Arizona INSURE Group 1 2025 ####
# Dalia Castro
# Perla Gutierrez
# Samuel Moreno
# Ben Trout
#################################################################

import argparse
import logging
import queue
import sys
import threading
import time

import matplotlib

if "--web" in sys.argv:
    matplotlib.use("WebAgg")

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
from pymodbus.client import ModbusTcpClient

# --- Logging Setup ---
logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.INFO)

# --- Constant Vars ---
POLL_INTERVAL = 1

# --- Shared State ---
lock = threading.Lock()
cmd_q = queue.Queue()

water_levels, cumulative_release = [], []
door_1_status, door_2_status, door_3_status = [], [], []
cumulative_water_released = 0
close_level, threshold_1, threshold_2, threshold_3 = 0, 0, 0, 0
door_overrides = [0, 0, 0]
connected = False


# --- Modbus Poller ---
def modbus_poller(host, port):
    """Poll the dam over Modbus, update shared state, and drain queued coil writes."""
    global cumulative_water_released, close_level
    global threshold_1, threshold_2, threshold_3, connected

    client = ModbusTcpClient(host=host, port=port)
    while True:
        try:
            if not client.connected:
                client.connect()

            # Drain button writes before reading back the new state
            while not cmd_q.empty():
                addr, value = cmd_q.get()
                client.write_coil(addr, value, slave=1)  # Coils 0-2 position, 3-5 override
                log.info(f"[WRITE] coil={addr} value={value}")

            wl = client.read_input_registers(0, count=1)  # Input register 0
            if wl.isError():
                raise ConnectionError(str(wl))
            water_level = wl.registers[0]

            cl = client.read_holding_registers(0, count=1).registers[0]  # Holding register 0
            th = client.read_holding_registers(4, count=3).registers  # Holding registers 4-6
            rr = client.read_holding_registers(1, count=3).registers  # Holding registers 1-3
            coils = client.read_coils(0, count=6).bits  # Coils 0-2 positions, 3-5 overrides

            d1, d2, d3 = int(coils[0]), int(coils[1]), int(coils[2])

            reduction = 0
            if d1:
                reduction += water_level * (rr[0] / 100)
            if d2:
                reduction += water_level * (rr[1] / 100)
            if d3:
                reduction += water_level * (rr[2] / 100)

            with lock:
                close_level = cl
                threshold_1, threshold_2, threshold_3 = th[0], th[1], th[2]
                cumulative_water_released += reduction
                water_levels.append(water_level)
                door_1_status.append(d1)
                door_2_status.append(d2)
                door_3_status.append(d3)
                cumulative_release.append(cumulative_water_released)
                door_overrides[0] = int(coils[3])
                door_overrides[1] = int(coils[4])
                door_overrides[2] = int(coils[5])
                if not connected:
                    log.info(f"Connected to Modbus {host}:{port}, water level={water_level}")
                connected = True

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            with lock:
                connected = False
            log.warning(f"Modbus poll error: {e}")
            time.sleep(2)


# --- Coil Writes (button control path) ---
def toggle_override(door):
    """Flip the manual override enable for a door (coils 3-5)."""
    with lock:
        current = door_overrides[door]
    cmd_q.put((3 + door, not current))


def toggle_position(door):
    """Flip the manual position for a door (coils 0-2)."""
    status = (door_1_status, door_2_status, door_3_status)[door]
    with lock:
        current = status[-1] if status else 0
    cmd_q.put((door, not current))


# --- Graph Updater ---
def update_graphs(frame, axes):
    """Redraw the graphs from the latest Modbus reads."""
    with lock:
        wl = list(water_levels)
        d1 = list(door_1_status)
        d2 = list(door_2_status)
        d3 = list(door_3_status)
        cr = list(cumulative_release)
        cs = close_level
        t1, t2, t3 = threshold_1, threshold_2, threshold_3

    axes[0].clear()
    axes[0].plot(wl, label="Water Level")
    axes[0].axhline(y=cs, color="grey", linestyle="--", label="Close Level")
    axes[0].axhline(y=t1, color="green", linestyle="--", label="Threshold 1")
    axes[0].axhline(y=t2, color="orange", linestyle="--", label="Threshold 2")
    axes[0].axhline(y=t3, color="red", linestyle="--", label="Threshold 3")
    axes[0].set_ylim(0, 100)
    axes[0].legend(loc="upper left")

    axes[1].clear()
    axes[1].plot(d1, label="Door 1")
    axes[1].plot(d2, label="Door 2")
    axes[1].plot(d3, label="Door 3")
    axes[1].legend()

    axes[2].clear()
    axes[2].plot(cr, label="Cumulative Released", color="purple")
    axes[2].legend()


def set_btn_color(btn, on):
    color = "#d5f5e3" if on else "#f2f3f4"
    btn.color = color
    btn.ax.set_facecolor(color)


# --- HMI ---
def _webagg_fill_window():
    # Size the WebAgg figure to the browser window instead of a fixed canvas
    # that scrolls. mpl.js already re-renders the figure when its canvas
    # container resizes, so we just size that container to the window.
    import io
    from matplotlib.backends import backend_webagg_core as wac

    js = """
(function(){
  function fit(){
    var c=document.querySelector('.mpl-canvas');if(!c)return false;
    var cd=c.parentElement;
    var de=document.documentElement;de.style.height='100%';de.style.overflow='hidden';
    var b=document.body.style;b.margin='0';b.height='100%';b.overflow='hidden';
    var figs=document.getElementById('figures');if(figs)figs.style.margin='0';
    var root=document.getElementById('figure-div');if(root){root.style.display='block';root.style.width='100%';}
    if(cd.parentElement){cd.parentElement.style.display='block';cd.parentElement.style.width='100%';}
    cd.style.width=Math.max(200,window.innerWidth-2)+'px';
    cd.style.height=Math.max(150,window.innerHeight-84)+'px';
    return true;
  }
  window.addEventListener('resize',fit);
  var done=0,n=0,iv=setInterval(function(){
    var c=document.querySelector('.mpl-canvas');n++;
    if(c&&c.width>0){fit();if(++done>=3)clearInterval(iv);}
    if(n>200)clearInterval(iv);
  },150);
})();
"""
    orig = wac.FigureManagerWebAgg.get_javascript.__func__

    def patched(cls, stream=None):
        buf = io.StringIO()
        orig(cls, buf)
        out = buf.getvalue() + js
        if stream is None:
            return out
        stream.write(out)

    wac.FigureManagerWebAgg.get_javascript = classmethod(patched)


def main():
    parser = argparse.ArgumentParser(description="Wildcat Dam Modbus web HMI")
    parser.add_argument("--modbus-host", default="127.0.0.1", help="Modbus server host")
    parser.add_argument("--modbus-port", type=int, default=5020, help="Modbus server port")
    parser.add_argument("--web", action="store_true", help="Serve the HMI in a browser (WebAgg)")
    parser.add_argument("--web-port", type=int, default=8090, help="Web HMI port (default 8090)")
    args = parser.parse_args()

    if args.web:
        plt.rcParams["webagg.address"] = "0.0.0.0"
        plt.rcParams["webagg.port"] = args.web_port
        plt.rcParams["webagg.open_in_browser"] = False
        _webagg_fill_window()

    threading.Thread(target=modbus_poller, args=(args.modbus_host, args.modbus_port), daemon=True).start()

    fig = plt.figure(figsize=(9, 11))
    fig.suptitle("Dam Modbus HMI")
    gs = fig.add_gridspec(5, 3, height_ratios=[3, 3, 3, 0.5, 0.5], hspace=0.6, wspace=0.3)
    axes = [fig.add_subplot(gs[0, :]), fig.add_subplot(gs[1, :]), fig.add_subplot(gs[2, :])]

    override_btns, position_btns = [], []
    for i in range(3):
        b_o = Button(fig.add_subplot(gs[3, i]), f"Door {i + 1} Override")
        b_p = Button(fig.add_subplot(gs[4, i]), f"Door {i + 1} Open/Close")
        b_o.on_clicked(lambda e, d=i: toggle_override(d))
        b_p.on_clicked(lambda e, d=i: toggle_position(d))
        override_btns.append(b_o)
        position_btns.append(b_p)

    def update(frame):
        update_graphs(frame, axes)
        with lock:
            ov = list(door_overrides)
            pos = [s[-1] if s else 0 for s in (door_1_status, door_2_status, door_3_status)]
        for i in range(3):
            override_btns[i].label.set_text(f"Door {i + 1} Manual" if ov[i] else f"Door {i + 1} Auto")
            position_btns[i].label.set_text(f"Door {i + 1} Open" if pos[i] else f"Door {i + 1} Closed")
            set_btn_color(override_btns[i], ov[i])
            set_btn_color(position_btns[i], pos[i])
        return []

    anim = FuncAnimation(fig, update, interval=1000, cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    main()
