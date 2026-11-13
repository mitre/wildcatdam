"""Load the dam configuration from config.yaml."""

from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


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
