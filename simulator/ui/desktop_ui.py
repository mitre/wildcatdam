"""Desktop and web (WebAgg) HMI window for the dam."""

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button

from simulator import SIM

TITLE = f"{SIM['protocols']['modbus']['name']} - {SIM['name']}"


# --- Graph Updater ---
def update_graphs(frame, axes, state, lock):
    """Redraw the graphs from the latest Modbus reads."""
    with lock:
        wl = list(state.water_levels)
        d1 = list(state.door_1_status)
        d2 = list(state.door_2_status)
        d3 = list(state.door_3_status)
        cr = list(state.cumulative_release)
        cs = state.close_level
        t1, t2, t3 = state.threshold_1, state.threshold_2, state.threshold_3

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
  function title(){
    var h=document.querySelector('.ui-dialog-title');
    if(h&&h.textContent&&document.title!==h.textContent)document.title=h.textContent;
  }
  new MutationObserver(title).observe(document.body,{childList:true,subtree:true,characterData:true});
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


def start_plot(state, lock, toggle_override, toggle_position):
    fig = plt.figure(TITLE, figsize=(9, 11))
    fig.suptitle("Wildcat Dam HMI")
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
        update_graphs(frame, axes, state, lock)
        with lock:
            ov = list(state.door_overrides)
            pos = list(state.door_positions)
        for i in range(3):
            override_btns[i].label.set_text(f"Door {i + 1} Manual" if ov[i] else f"Door {i + 1} Auto")
            position_btns[i].label.set_text(f"Door {i + 1} Open" if pos[i] else f"Door {i + 1} Closed")
            set_btn_color(override_btns[i], ov[i])
            set_btn_color(position_btns[i], pos[i])
        return []

    anim = FuncAnimation(fig, update, interval=1000, cache_frame_data=False)
    plt.show()
