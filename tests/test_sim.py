import unittest

from simulator.sim import DamState, dam_step


def make_state(**kwargs):
    # The DamState defaults are all 0, which would make every threshold test pass
    # trivially, so every case sets the thresholds explicitly.
    return DamState(close_level=40, threshold_1=60, threshold_2=75, threshold_3=85, **kwargs)


class SimulationTests(unittest.TestCase):
    def test_door_1_opens_above_threshold(self):
        sim = make_state(water_level=70)
        dam_step(sim)
        self.assertEqual(sim.doors, (1, 0, 0))

    def test_door_1_holds_then_closes_below_close_level(self):
        sim = make_state(water_level=50)
        sim.previous_d1_state[0] = 1
        dam_step(sim)
        self.assertEqual(sim.doors[0], 1)

        sim.water_level = 30
        dam_step(sim)
        self.assertEqual(sim.doors[0], 0)

    def test_doors_2_and_3_follow_their_thresholds(self):
        sim = make_state(water_level=80)
        dam_step(sim)
        self.assertEqual(sim.doors, (1, 1, 0))

        sim.water_level = 90
        dam_step(sim)
        self.assertEqual(sim.doors, (1, 1, 1))

    def test_override_forces_the_manual_position(self):
        sim = make_state(water_level=90, overrides=(1, 0, 1), manual=(0, 0, 0))
        dam_step(sim)
        self.assertEqual(sim.doors, (0, 1, 0))

    def test_open_doors_reduce_the_level_and_accumulate_release(self):
        sim = make_state(water_level=100, reduction_rates={"door_1": 1, "door_2": 3, "door_3": 6})
        dam_step(sim)
        self.assertEqual(sim.doors, (1, 1, 1))
        self.assertAlmostEqual(sim.cumulative_water_released, 10.0)
        self.assertAlmostEqual(sim.water_level, 90.0)

    def test_level_stays_clamped_with_surge(self):
        sim = make_state(water_level=95, surge_rate=20, reduction_rates={"door_1": 1, "door_2": 1, "door_3": 1})
        for _ in range(50):
            dam_step(sim)
            self.assertTrue(0 <= sim.water_level <= 100)

        sim = make_state(
            water_level=50,
            overrides=(1, 1, 1),
            manual=(1, 1, 1),
            reduction_rates={"door_1": 50, "door_2": 50, "door_3": 50},
        )
        dam_step(sim)
        self.assertEqual(sim.water_level, 0)


if __name__ == "__main__":
    unittest.main()
