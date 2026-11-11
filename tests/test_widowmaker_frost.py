"""Actual Mei spray and Widow movement, aim and ability input."""

import unittest

from test_game_combat import SOLDIER, aim, in_world
from test_widowmaker_barriers import ZARYA
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import combat, mei_frost
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


class WidowmakerFrostTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[mei_frost.HERO]))
        self.b.mover.position[:] = (0, 0, 3)
        self.b.mover.body = None
        self.fight = self.run.match.combat

    def spray(self, frames):
        direction = aim(self.fight._eye(self.b), (0, 0.9, 0))
        for n in range(frames):
            self.run.step({self.b: (*direction, FIRE if frames > 21 or n == 0 else 0)})

    def frost_digest(self):
        graph = _Graph({"combat": self.fight}, {})
        root = graph.value(self.fight.frost)
        return snapshot_digest({
            "schema": SCHEMA, "scope": SCOPE, "restorable": False,
            "roots": {"frost": root}, "nodes": graph.finish(), "assets": {},
        })

    def test_actual_single_spray_changes_speed_then_world_gap_retires_slow(self):
        baseline = self.frost_digest()
        self.spray(21)
        self.assertNotEqual(self.frost_digest(), baseline)
        self.assertEqual(self.health(self.a), 172.25)
        sample = self.fight.frost.sample(self.a, self.run.frame)
        self.assertAlmostEqual(sample[0], 0.2)
        self.run.frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.a.take_commands([Command(self.run.frame, forward=127)])
        self.assertAlmostEqual(self.a.mover.values.run_forward, self.a.mover.data.run_forward * .8, places=5)
        owner_frame = self.a.body_script.frame
        self.run.frame += 70
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(self.a.body_script.frame, owner_frame)
        self.assertIsNone(self.fight.frost.sample(self.a, self.run.frame))
        self.assertEqual(self.a.body_script.component.entity_var(2427).value(), 0.0)
        self.assertEqual(self.frost_digest(), baseline)

    def test_actual_freeze_locks_actions_and_thaw_does_not_replay_blocked_commands(self):
        self.spray(110)
        sample = self.fight.frost.sample(self.a, self.run.frame)
        self.assertEqual(sample[2] - sample[1], 1300)
        self.assertTrue(self.fight.is_stunned(self.a))
        position = tuple(self.a.mover.position)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        for _ in range(15):
            self.run.frame += 1
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
            self.a.take_commands([Command(self.run.frame, yaw=17000, pitch=5000,
                                          buttons=FIRE | 8 | 16 | 32 | 0x40, forward=127)])
        self.assertEqual(tuple(self.a.mover.position), position)
        self.assertEqual((self.a.mover.yaw, self.a.mover.pitch), sample[3:])
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(self.a.mover.last_command.buttons, 0)
        self.assertEqual(self.a.mover.last_command.forward, 0)
        self.assertIsNone(self.a.body_script.infra_window())
        self.assertFalse(self.fight.venom.flights)
        self.run.frame = (sample[2] + 15) // 16
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertFalse(self.fight.is_stunned(self.a))
        before = self.health(self.b)
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        self.assertLess(self.health(self.b), before)
        self.assertFalse(self.fight.venom.flights)
        self.assertIsNone(self.a.body_script.infra_window())

    def test_one_actual_piercing_spray_damages_and_slows_both_enemy_bodies_once(self):
        other = self.run.match.add_player(3, "Widow teammate", SOLDIER, self.a.team, False)
        in_world(self.run.match, other)
        self.assertTrue(self.run.match.switch_hero(other, heroes()[SOLDIER]))
        other.mover.position[:] = (0, 0, -1.5)
        other.mover.body = None
        self.run.players.append(other)
        self.spray(21)
        self.assertEqual(self.health(self.a), 172.25)
        self.assertEqual(self.health(other), 197.25)
        for target in (self.a, other):
            self.assertAlmostEqual(self.fight.frost.sample(target, self.run.frame)[0], 0.2)

    def test_actual_projected_protection_blocks_spray_and_frost(self):
        ally = self.run.match.add_player(3, "Zarya ally", ZARYA, self.a.team, False)
        in_world(self.run.match, ally)
        self.assertTrue(self.run.match.switch_hero(ally, heroes()[ZARYA]))
        ally.mover.position[:] = (-2, 0, 0)
        ally.mover.body = None
        self.run.players.append(ally)
        for _ in range(8):
            self.run.step({})
        direction = aim(self.fight._eye(ally), self.fight._eye(self.a))
        self.run.step({ally: (*direction, 16)})
        self.spray(21)
        self.assertEqual(self.health(self.a), 175)
        self.assertIsNone(self.fight.frost.sample(self.a, self.run.frame))
        barrier = self.fight.zarya_barriers.projected[ally.body]
        self.assertAlmostEqual(barrier.remaining, 197.25)
