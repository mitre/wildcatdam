"""Dam process model: door logic and water release, independent of Modbus."""

from dataclasses import dataclass, field


@dataclass
class DamState:
    water_level: float = 0
    surge_rate: int = 0
    close_level: int = 0
    threshold_1: int = 0
    threshold_2: int = 0
    threshold_3: int = 0
    reduction_rates: dict = field(default_factory=lambda: {"door_1": 0, "door_2": 0, "door_3": 0})
    overrides: tuple = (0, 0, 0)
    manual: tuple = (0, 0, 0)
    doors: tuple = (0, 0, 0)
    # Maintain previous door state globally or within a class/module
    previous_d1_state: list = field(default_factory=lambda: [0])  # Using a list so it’s mutable in nested scopes
    cumulative_water_released: float = 0


def dam_step(sim):
    """
    Advance the dam by one tick: decide the door states, then release water.

    Args:
        sim (DamState): Current dam state.
    """
    water_level = sim.water_level
    surge_rate = sim.surge_rate
    CLOSE_LEVEL = sim.close_level
    THRESHOLD_1 = sim.threshold_1
    THRESHOLD_2 = sim.threshold_2
    THRESHOLD_3 = sim.threshold_3
    override_d1, override_d2, override_d3 = sim.overrides
    manual_d1, manual_d2, manual_d3 = sim.manual
    previous_d1_state = sim.previous_d1_state

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

    sim.doors = (d1, d2, d3)
    sim.water_level = reduce_water_level(sim, water_level, d1, d2, d3, surge_rate)


def reduce_water_level(sim, water_level, d1, d2, d3, surge_rate):
    """
    Calculate the amount of water to be released based on door states and surge rate.
    NOTE: Released water is relative to total water to simulate water pressure from gravity.

    Args:
        water_level (int): Current water level.
        d1 (int): State of door 1.
        d2 (int): State of door 2.
        d3 (int): State of door 3.
        surge_rate (int): Surge rate percentage.

    Returns:
        int: Updated water level after reduction.
    """
    reduction = 0
    if d1:
        reduction += water_level * (sim.reduction_rates["door_1"] / 100)
    if d2:
        reduction += water_level * (sim.reduction_rates["door_2"] / 100)
    if d3:
        reduction += water_level * (sim.reduction_rates["door_3"] / 100)

    sim.cumulative_water_released += reduction
    return min(100, max(0, water_level - reduction + surge_rate))
