"""Barrage's authored persistent world-velocity SET through actual input and mover phases."""

import unittest
from unittest.mock import patch

import test_pharah_combat as primary

from ow174.game import replay_values
from ow174.game.commands import CROUCH, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.physics_snapshot import snapshot_mover
from ow174.game.script import runtime


class PharahBarrageMovementTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter

    def step(self, run, player, *, buttons=0, forward=0, right=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        for owner in run.players:
            owner.take_commands([Command(run.frame, buttons=buttons if owner is player else 0,
                                         forward=forward if owner is player else 0,
                                         right=right if owner is player else 0)])

    def cast(self, run, player, enemy):
        enemy.mover.position[0] = 30
        enemy.mover.body = None
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        self.step(run, player, buttons=32)
        self.assertTrue(player.body_script.ultimate_running())

    def test_ground_channel_stops_existing_momentum_and_held_throttle_then_releases(self):
        run, player, enemy = self.encounter()
        for _ in range(20):
            self.step(run, player, forward=127)
        self.assertGreater(player.mover.velocity[2], 5)
        self.cast(run, player, enemy)
        start = tuple(player.mover.position)
        for _ in range(60):
            self.step(run, player, forward=127, right=127)
            self.assertEqual(player.mover.velocity, (0, 0, 0))
        self.assertEqual(tuple(player.mover.position), start)
        self.assertTrue(player.body_script.ultimate_running())
        for _ in range(180):
            self.step(run, player, forward=127)
        self.assertFalse(player.body_script.ultimate_running())
        self.assertGreater(player.mover.position[2], start[2] + 1)
        self.assertGreater(player.mover.velocity[2], 5)

    def test_air_channel_stops_jump_jet_and_gravity_then_falls_after_end(self):
        run, player, enemy = self.encounter()
        floor = player.mover.position[1]
        self.step(run, player, buttons=8)
        self.assertGreater(player.mover.velocity[1], 19)
        self.cast(run, player, enemy)
        start = tuple(player.mover.position)
        self.assertGreater(start[1], floor)
        for _ in range(90):
            self.step(run, player, forward=127)
            self.assertEqual(player.mover.velocity, (0, 0, 0))
        self.assertEqual(tuple(player.mover.position), start)
        for _ in range(150):
            self.step(run, player)
        self.assertFalse(player.body_script.ultimate_running())
        self.assertAlmostEqual(player.mover.position[1], floor, places=2)

    def test_hero_switch_replaces_the_channel_mover_without_sticky_velocity_set(self):
        run, player, enemy = self.encounter()
        self.cast(run, player, enemy)
        self.step(run, player, forward=127)
        self.assertEqual(player.mover.velocity, (0, 0, 0))
        previous = player.mover
        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
        self.assertIsNot(player.mover, previous)
        start = tuple(player.mover.position)
        for _ in range(30):
            self.step(run, player, forward=127)
        self.assertGreater(player.mover.position[2], start[2] + 1)
        self.assertGreater(player.mover.velocity[2], 5)

    def test_channel_rejects_other_attacks_and_flight_without_spending_their_resources(self):
        run, player, enemy = self.encounter()
        self.cast(run, player, enemy)
        instances = {i.graph.index: i for i in player.body_script.component.instances.values()}
        for _ in range(30):
            self.step(run, player, buttons=1 | 2 | 8 | 16 | 0x40 | 0x800 | CROUCH,
                      forward=127)
        self.assertTrue(player.body_script.ultimate_running())
        self.assertEqual(instances[0x23].vars[53].value(), 6)
        self.assertEqual(instances[0x25].vars[87].value(), 100)
        self.assertEqual(instances[0x27].vars[61].value(), 0)
        self.assertEqual(instances[0x7B].vars[61].value(), 0)
        self.assertFalse(any(s.counter for s in instances[0x43].states.values() if s.index == 4))
        self.assertFalse(player.mover.crouched)
        self.assertEqual(player.mover.velocity, (0, 0, 0))

    def test_active_vector_mod_is_passively_stored_and_changes_physics_snapshot(self):
        run, player, enemy = self.encounter()
        self.cast(run, player, enemy)
        self.step(run, player)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no graph evaluation")):
            first = snapshot_mover(player.mover)
            self.assertEqual(first, snapshot_mover(player.mover))
        nodes = replay_values.decode(first["state"])["nodes"]
        vectors = [node for node in nodes if node["class"] == "Mod"
                   and isinstance(dict(node["fields"])["value"], dict)]
        self.assertEqual(len(vectors), 1)
        value_node = nodes[dict(vectors[0]["fields"])["value"]["node"]]
        self.assertEqual(value_node, {"class": "tuple", "items": [0.0, 0.0, 0.0]})
        vector = next(mod for mod in player.mover.mods.values() if isinstance(mod.value, tuple))
        vector.value = (1.0, 0.0, 0.0)
        self.assertNotEqual(first, snapshot_mover(player.mover))


if __name__ == "__main__":
    unittest.main()
