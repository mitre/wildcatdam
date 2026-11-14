"""Load the dam configuration from config.yaml."""

from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

# Every point the PLC and HMI use, by table. config.yaml gives each one an address.
POINTS = {
    "coils": (
        "door_1.position", "door_2.position", "door_3.position",
        "door_1.override_enable", "door_2.override_enable", "door_3.override_enable",
    ),
    "discrete_inputs": ("any_door_open", "door_1.state", "door_2.state", "door_3.state"),
    "holding_registers": (
        "close_level",
        "reduction_rates.door_1", "reduction_rates.door_2", "reduction_rates.door_3",
        "thresholds.threshold_1", "thresholds.threshold_2", "thresholds.threshold_3",
    ),
    "input_registers": ("water_level", "surge_rate", "water_released"),
}
SIZES = {"coils": "co size", "discrete_inputs": "di size", "holding_registers": "hr size", "input_registers": "ir size"}
# Modbus caps one read at 2000 bits or 125 registers.
MAX_READ = {"coils": 2000, "discrete_inputs": 2000, "holding_registers": 125, "input_registers": 125}


def load_config(path=DEFAULT_CONFIG):
    """
    Read a YAML configuration file.

    Args:
        path (str | Path): Configuration file, config.yaml in the repo root by default.

    Returns:
        dict: The parsed configuration.
    """
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_addresses(config):
    """
    Map each point's name to its address from the configuration.

    Args:
        config (dict): Configuration loaded from config.yaml.

    Returns:
        dict: {table: {name: address}} for every table in POINTS.

    Raises:
        ValueError: An address is not a whole number, is outside the table's
            size, or is used twice; a point is missing or listed twice; or a
            table's points are spread wider than one Modbus read allows.
    """
    device = config["device"]
    addresses = {}
    for table, names in POINTS.items():
        size = device["setup"][SIZES[table]]
        found, used = {}, set()
        for item in device.get(table) or []:
            name, addr = item.get("name"), item["addr"]
            label = name or f"unnamed entry at {addr!r}"
            if type(addr) is not int:
                raise ValueError(f"{table}: {label} address must be a whole number")
            if not 0 <= addr < size:
                raise ValueError(f"{table}: {label} address {addr} is outside 0-{size - 1}")
            if addr in used:
                raise ValueError(f"{table}: {label} shares address {addr} with another entry")
            used.add(addr)
            if name is None:
                continue
            if name not in names:
                raise ValueError(f"{table}: unknown point {name}")
            if name in found:
                raise ValueError(f"{table}: {name} is listed twice")
            found[name] = addr
        missing = [name for name in names if name not in found]
        if missing:
            raise ValueError(f"{table}: no address for {', '.join(missing)}")
        span = max(found.values()) - min(found.values()) + 1
        if span > MAX_READ[table]:
            raise ValueError(f"{table}: points span {span} addresses; the HMI reads at most {MAX_READ[table]} at once")
        addresses[table] = {name: found[name] for name in names}
    return addresses
