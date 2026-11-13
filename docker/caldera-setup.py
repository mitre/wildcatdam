"""Prepare a Caldera checkout.

usage: python caldera-setup.py <plugin> [<plugin> ...]
"""

import glob
import re
import sys

import yaml

PLUGINS = sys.argv[1:]

# app_svc.py builds the Jinja2 search path from plugin names only, omitting the
# core templates/ dir where login.html lives. Patch it so all routes resolve.
PATH = "app/service/app_svc.py"
ANCHOR = "templates.append('plugins/magma/dist')"
src = open(PATH).read()
if ANCHOR not in src:
    raise SystemExit(f"patch anchor not found in {PATH} - upstream may have changed")
if "templates.append('templates')" not in src:
    with open(PATH, "w") as f:
        f.write(src.replace(ANCHOR, ANCHOR + "\n        templates.append('templates')"))

# contact_tcp.py imports c_session from manx, which later manx releases removed.
# Stub it out so the server starts without TCP agent support.
with open("plugins/manx/app/c_session.py", "w") as f:
    f.write("class Session:\n    pass\n")

# Caldera only calls enable() on plugins listed in the config, and enable() is
# what sets data_dir so the plugin's abilities load. Drop atomic and training:
# only the OT plugins are needed, and atomic's enable() falls back to a live
# git clone of redcanaryco/atomic-red-team when its submodule data isn't present
# in the image, which can block server startup for several minutes.
PATH = "conf/default.yml"
with open(PATH) as f:
    config = yaml.safe_load(f)
config["plugins"] = [p for p in config["plugins"] if p not in ("atomic", "training")]
config["plugins"] += [p for p in PLUGINS if p not in config["plugins"]]
with open(PATH, "w") as f:
    yaml.safe_dump(config, f, default_flow_style=False)

# The OT abilities ship as repeatable, so the atomic planner hands the first
# step out again on every beacon and an adversary profile never reaches step
# two. Clear it for the plugins loaded here.
for plugin in PLUGINS:
    for path in glob.glob(f"plugins/{plugin}/data/abilities/*/*.yml"):
        src = open(path).read()
        patched = re.sub(r"(?mi)^(\s*repeatable:\s*)true\s*$", r"\1False", src)
        if patched != src:
            with open(path, "w") as f:
                f.write(patched)

# Agents beacon every 30-60s by default, which leaves a profile running for
# minutes.
PATH = "conf/agents.yml"
with open(PATH) as f:
    config = yaml.safe_load(f)
config["sleep_min"] = 1
config["sleep_max"] = 1
with open(PATH, "w") as f:
    yaml.safe_dump(config, f, default_flow_style=False)
