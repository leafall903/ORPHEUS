"""Actual Mei Blizzard casts against Widowmaker in build 104319."""

import unittest

from test_game_combat import aim, in_world
from test_widowmaker_barriers import ZARYA
from test_widowmaker_combat import FIRE, _WidowmakerMatch

from ow174.game import mei_frost, server_snapshot
from ow174.game.collision import World
from ow174.game.content import heroes


class WidowmakerBlizzardTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[mei_frost.HERO]))
        self.a.mover.position[:] = (2, 0, 5)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat
        self.blizzard = self.fight.blizzard
        self.direction = aim(self.fight._eye(self.b), (0, 0, 5))

    def advance(self, milliseconds):
        while self.run.frame * 16 < milliseconds:
            self.run.step({})

    def cast(self, *, charge=True):
        if charge:
            script = self.b.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        for n in range(32):
            self.run.step({self.b: (*self.direction, 32 if n == 0 else 0)})

    def land(self):
        self.cast()
        self.assertEqual(len(self.blizzard.flights), 1)
        flight = self.blizzard.flights[0]
        self.assertEqual(flight.gravity, 9.800000190734863)
        for _ in range(150):
            self.run.step({})
            if self.blizzard.areas:
                break
        area, = self.blizzard.areas.values()
        self.assertAlmostEqual(area.position[1], 0)
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.blizzard.flights)
        return area

    def test_actual_cast_charge_damage_freeze_and_thaw(self):
        self.cast(charge=False)
        self.assertFalse(self.blizzard.flights)
        self.assertFalse(self.blizzard.areas)
        area = self.land()
        self.assertEqual(self.b.body_script.component.entity_var(342).value(), 0)
        self.advance(area.starts - 16)
        self.assertEqual(self.health(self.a), 175)
        self.advance(area.starts + 32)
        self.assertLess(self.health(self.a), 175)
        self.assertGreaterEqual(self.fight.frost.sample(self.a, self.run.frame)[0], 0.5)
        self.advance(area.starts + 2500)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.advance(area.starts + 2632)
        self.assertTrue(self.fight.is_stunned(self.a))
        sample = self.fight.frost.sample(self.a, self.run.frame)
        self.assertEqual(sample[2] - sample[1], 1300)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.press(FIRE, 4)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(self.fight.frost.sample(self.a, self.run.frame), sample)
        self.advance(sample[2] + 16)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.press(FIRE)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        self.advance(area.ends + 16)
        self.assertFalse(self.blizzard.areas)
        self.assertAlmostEqual(self.health(self.a), 90)
        self.advance(area.ends + 1100)
        self.assertIsNone(self.fight.frost.sample(self.a, self.run.frame))
        self.assertAlmostEqual(self.health(self.a), 90)

    def test_released_area_survives_caster_death_and_stores_exposure_clock(self):
        area = self.land()
        caster = self.fight.vitals_of(self.target(self.b))
        caster.damage(caster.total - 10)
        self.press(FIRE)
        self.assertTrue(caster.dead)
        self.assertIn(area.entity, self.blizzard.areas)
        self.advance(area.starts + 32)
        self.assertLess(self.health(self.a), 175)

        def stored_area():
            graph = server_snapshot._Graph(
                {'combat': self.fight, 'caster': self.b}, server_snapshot.WorldRegistry(),
            )
            root = graph.value(self.blizzard)
            return root, graph.finish()

        stored = stored_area()
        due = area.frost_due[self.a.body]
        area.frost_due[self.a.body] = due + 1
        self.assertNotEqual(stored_area(), stored)
        area.frost_due[self.a.body] = due
        self.advance(area.ends + 16)
        self.assertFalse(self.blizzard.areas)
        self.assertAlmostEqual(self.health(self.a), 90)

    def test_projected_barrier_blocks_area_damage_and_frost_until_expiry(self):
        ally = self.run.match.add_player(3, 'ShieldAlly', ZARYA, self.a.team, False)
        in_world(self.run.match, ally)
        self.assertTrue(self.run.match.switch_hero(ally, heroes()[ZARYA]))
        ally.mover.position[:] = (4, 0, 5)
        ally.mover.body = None
        self.run.players.append(ally)
        area = self.land()
        direction = aim(self.fight._eye(ally), self.fight._eye(self.a))
        self.run.step({ally: (*direction, 0x10)})
        self.advance(area.starts + 192)
        self.assertLess(self.fight.zarya_barriers.projected[ally.body].remaining, 200)
        self.assertEqual(self.health(self.a), 175)
        self.assertIsNone(self.fight.frost.sample(self.a, self.run.frame))
        self.advance(area.starts + 2200)
        self.assertLess(self.health(self.a), 175)
        frost = self.fight.frost.sample(self.a, self.run.frame)
        self.assertIsNotNone(frost)
        self.assertLess(frost[0], 0.7)

    def test_actual_cast_wall_ally_and_outside_radius(self):
        for mode in ('wall', 'ally', 'range'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == 'wall':
                    self.run.match.collision_world = World([
                        (-30, 0, -30, -30, 0, 30, 30, 0, 30),
                        (-30, 0, -30, 30, 0, 30, 30, 0, -30),
                        (1, 0, -2, 1, 0, 16, 1, 4, 16),
                        (1, 0, -2, 1, 4, 16, 1, 4, -2),
                    ])
                elif mode == 'ally':
                    self.a.team = self.b.team
                else:
                    self.a.mover.position[0] = 12
                area = self.land()
                self.advance(area.ends + 16)
                self.assertEqual(self.health(self.a), 175)
                self.assertIsNone(self.fight.frost.sample(self.a, self.run.frame))
