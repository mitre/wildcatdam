#!/bin/sh
set -e

python - <<'PY'
import os, socket, yaml

host = os.environ["SIM_HOST"]
path = os.environ["FACT_SOURCE"]
trait = os.environ["FACT_TRAIT"]

try:
    ip = socket.gethostbyname(host)
except OSError as exc:
    print(f"[caldera] couldn't resolve {host} ({exc})")
    raise SystemExit(1)

with open(path) as f:
    source = yaml.safe_load(f)
facts = [fact for fact in source["facts"] if fact["trait"] == trait]
if not facts:
    print(f"[caldera] no {trait} fact in {path}")
    raise SystemExit(1)
for fact in facts:
    fact["value"] = ip
with open(path, "w") as f:
    yaml.safe_dump(source, f, sort_keys=False)
print(f"[caldera] {trait} set to {ip} ({host})")
PY

# Without -E the environment defaults to "local" and ensure_local_config()
# generates conf/local.yml with random secrets on first boot.
exec python server.py -E default
