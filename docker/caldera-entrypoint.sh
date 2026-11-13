#!/bin/sh
set -e

# FACT_SOURCE is a glob: every fact source it matches that has FACT_TRAIT gets the sim's IP.
python - <<'PY'
import glob, os, socket, yaml

host = os.environ["SIM_HOST"]
pattern = os.environ["FACT_SOURCE"]
trait = os.environ["FACT_TRAIT"]

try:
    ip = socket.gethostbyname(host)
except OSError as exc:
    print(f"[caldera] couldn't resolve {host} ({exc})")
    raise SystemExit(1)

updated = 0
for path in sorted(glob.glob(pattern)):
    with open(path) as f:
        source = yaml.safe_load(f)
    facts = [fact for fact in source["facts"] if fact["trait"] == trait]
    if not facts:
        continue
    for fact in facts:
        fact["value"] = ip
    with open(path, "w") as f:
        yaml.safe_dump(source, f, sort_keys=False)
    updated += 1
if not updated:
    print(f"[caldera] no {trait} fact in {pattern}")
    raise SystemExit(1)
print(f"[caldera] {trait} set to {ip} ({host}) in {updated} fact source(s)")
PY

# Without -E the environment defaults to "local" and ensure_local_config()
# generates conf/local.yml with random secrets on first boot.
exec python server.py -E default
