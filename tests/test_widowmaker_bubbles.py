"""Actual Widow shots against authored Winston00FA deployments."""

import unittest

from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import bubbles, combat
from ow174.game.collision import World
from ow174.game.content import heroes


class WidowmakerBubbleTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fight = self.run.match.combat
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[bubbles.WINSTON]))
        self.b.mover.position[:] = (0, 0, 8)
        self.b.mover.body = None

    def cast(self):
        self.press(16, attacker=self.b, defender=self.a)
        self.assertNotIn(self.b.body, self.fight.bubbles.actors)
        self.press(0, 4, attacker=self.b, defender=self.a)
        return self.fight.bubbles.actors[self.b.body]

    def test_actual_scoped_damage_spends_ammo_and_only_hits_barrier(self):
        shield = self.cast()
        self.assertEqual(shield.health, 700)
        self.assertAlmostEqual(shield.expires - shield.start, 9)
        before = self.health(self.b)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.press(SCOPE, 100)
        self.press(FIRE | SCOPE)
        self.assertEqual(weapon.vars[53].value(), ammo - 5)
        self.assertEqual(shield.health, 580)
        self.assertEqual(self.health(self.b), before)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_breaking_primary_does_not_spill_and_next_shot_reaches_body(self):
        shield = self.cast()
        shield.health = 5
        before = self.health(self.b)
        self.press(FIRE)
        self.assertEqual(shield.health, 0)
        self.assertEqual(self.health(self.b), before)
        self.press(0, 15)
        self.press(FIRE)
        self.assertLess(self.health(self.b), before)

    def test_wall_before_shield_protects_shield_and_body(self):
        shield = self.cast()
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-5, 0, 2, 5, 0, 2, 5, 3, 2),
            (-5, 0, 2, 5, 3, 2, -5, 3, 2),
        ])
        before = self.health(self.b)
        self.press(FIRE)
        self.assertEqual(shield.health, 700)
        self.assertEqual(self.health(self.b), before)

    def test_world_only_expiry_and_inside_rays(self):
        shield = self.cast()
        self.a.mover.position[:] = (0, 0, 6)
        self.a.mover.body = None
        self.press(0, 4)
        before = self.health(self.b)
        self.press(FIRE)
        self.assertLess(self.health(self.b), before)
        self.assertEqual(shield.health, 700)
        owner_frame = self.b.body_script.frame
        self.run.frame += 600
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertNotIn(self.b.body, self.fight.bubbles.actors)
        self.assertEqual(self.b.body_script.frame, owner_frame)

    def test_actual_melee_contacts_shell_out_of_body_range(self):
        shield = self.cast()
        self.a.mover.position[:] = (0, 0, 2)
        self.a.mover.body = None
        self.press(0, 4)
        before = self.health(self.b)
        self.press(MELEE)
        self.press(0, 25)
        self.assertEqual(shield.health, 670)
        self.assertEqual(self.health(self.b), before)

    def test_enemy_shield_stops_ballistic_mine_and_friendly_shield_allows_it(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                self.setUp()
                shield = self.cast()
                if friendly:
                    self.b.team = self.a.team
                self.press(16)
                self.press(0, 60)
                self.assertEqual(self.fight.venom.flights, [])
                self.assertEqual(len(self.fight.venom.mines), int(friendly))
                self.assertEqual(self.fight.venom.poison, [])
                self.assertEqual(shield.health, 700)
