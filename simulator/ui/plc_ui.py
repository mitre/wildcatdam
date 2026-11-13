"""Tk window with the PLC's own graphs (python -m simulator.plc --gui)."""

import tkinter as tk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


def draw_graphs(
    canvas,
    axes,
    water_levels,
    door_1_status,
    door_2_status,
    door_3_status,
    cumulative_release,
    CLOSE_LEVEL,
    THRESHOLD_1,
    THRESHOLD_2,
    THRESHOLD_3,
):
    """
    Update the graphs to reflect the current state of the simulation.

    Args:
        canvas (FigureCanvasTkAgg): Canvas for rendering graphs.
        axes (list): List of matplotlib axes for plotting.
    """
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


# --- GUI ---
def launch_gui(root, snapshot, stop):
    """
    Show the PLC's graphs and redraw them once a second until the window closes or stop is set.

    Args:
        root (tk.Tk): The window to draw in.
        snapshot (callable): Returns the draw_graphs arguments after canvas and axes.
        stop (threading.Event): Set to close the window.
    """
    root.title("Wildcat Dam PLC")
    root.geometry("1000x800")

    graph_frame = tk.Frame(root)
    graph_frame.pack(fill=tk.BOTH, expand=True)

    fig = Figure(figsize=(8, 12))
    axes = fig.subplots(3, 1)
    fig.tight_layout(pad=5.0)
    canvas = FigureCanvasTkAgg(fig, master=graph_frame)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def update():
        if stop.is_set():
            root.destroy()
            return
        draw_graphs(canvas, axes, *snapshot())
        root.after(1000, update)

    update()
    root.mainloop()
