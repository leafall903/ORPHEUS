"""Actual Widow commands against selected Genji Deflect reconstruction."""

import unittest

from test_game_combat import aim
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import combat, deflection
from ow174.game.collision import World
from ow174.game.content import heroes

GENJI = 0x02E0000000000029


class _DeflectMatch(_WidowmakerMatch):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[GENJI]))
        self.place(4)
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        self.step(60)
        self.initial_widow_health = self.health(self.a)

    def place(self, distance):
        self.b.mover.position[:] = (0, 0, distance)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)

    def step(self, count=1, *, widow=0, genji=0, rear=False, height=1):
        for _ in range(count):
            direction = aim(self.run.match.combat._eye(self.a),
                            (0, self.b.mover.position[1] + height, self.b.mover.position[2]))
            self.run.step({self.a: (*direction, widow), self.b: (0 if rear else -32768, 0, genji)})

    def start(self, **kwargs):
        self.step(genji=16, **kwargs)
        self.step(4, **kwargs)
        self.assertTrue(self.deflect().state(10).active)

    def deflect(self):
        return next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x3A3)



class WidowmakerDeflectTests(_DeflectMatch, unittest.TestCase):
    def test_primary_returns_damage_and_credit_without_widow_charge(self):
        self.start()
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.health(self.a), self.initial_widow_health - 13)
        self.assertEqual(ammo - weapon.vars[53].value(), 1)
        self.assertEqual(self.a.body_script.component.vars[342].value(), 0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        self.assertEqual(self.b.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], 13)

    def test_fully_scoped_body_return_spends_original_five_ammo(self):
        self.step(100, widow=SCOPE)
        self.start(widow=SCOPE)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.step(widow=SCOPE | FIRE)
        self.assertEqual(self.health(self.a), self.initial_widow_health - 120)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(ammo - weapon.vars[53].value(), 5)

    def test_reflected_scoped_head_kill_belongs_to_genji(self):
        self.step(100, widow=SCOPE, height=combat.EYE)
        self.start(widow=SCOPE, height=combat.EYE)
        self.step(widow=SCOPE | FIRE, height=combat.EYE)
        fight = self.run.match.combat
        self.assertTrue(fight.is_dead(self.a))
        self.assertEqual(fight.vitals[self.a.body].killer, self.b.body)
        self.assertEqual(self.health(self.b), 200)

    def test_rear_bullet_and_rear_melee_damage_genji(self):
        self.place(1.5)
        self.start(rear=True)
        self.step(widow=FIRE, rear=True)
        self.step(15, rear=True)
        self.step(widow=MELEE, rear=True)
        self.step(30, rear=True)
        self.assertEqual(self.health(self.b), 157)
        self.assertEqual(self.health(self.a), self.initial_widow_health)

    def test_front_melee_is_blocked_without_reflection(self):
        self.place(1.5)
        self.start()
        self.step(widow=MELEE)
        self.step(30)
        self.assertEqual(self.health(self.a), self.initial_widow_health)
        self.assertEqual(self.health(self.b), 200)

    def test_expiry_and_cooldown_repress_do_not_protect(self):
        self.start()
        self.step(140)
        self.assertFalse(self.deflect().state(10).active)
        counter = self.deflect().state(10).counter
        self.step(genji=16)
        self.assertFalse(self.deflect().state(10).active)
        self.assertEqual(self.deflect().state(10).counter, counter)
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.b), 187)

    def test_wall_stops_bullet_before_reflection(self):
        self.start()
        self.run.match.collision_world = World([
            (-10, -10, 2, 10, -10, 2, 10, 10, 2),
            (-10, -10, 2, 10, 10, 2, -10, 10, 2),
        ])
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.a), self.initial_widow_health)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.b.client.messages(combat.HIT), [])

    def test_allied_genji_cannot_return_friendly_fire(self):
        self.b.team = self.a.team
        self.start()
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.a), self.initial_widow_health)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.b.client.messages(combat.HIT), [])

    def test_return_path_uses_remaining_range(self):
        self.place(60)
        self.start()
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.a), self.initial_widow_health)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        self.assertEqual(self.b.client.messages(combat.HIT), [])

    def test_established_poison_is_not_blocked_by_deflect(self):
        self.place(1.5)
        self.step(widow=16)
        self.step(110)
        self.assertTrue(self.run.match.combat.venom.poison)
        self.start()
        self.step(400)
        self.assertAlmostEqual(self.health(self.b), 125)
        self.assertEqual(self.health(self.a), self.initial_widow_health)

    def test_reflected_flight_survives_original_body_retirement(self):
        if not self.run.match.combat.projectile_tags.admitted(0x04000000000000AF):
            self.skipTest("requires explicit projectile tag input")
        self.start()
        self.step(widow=16)
        mines = self.run.match.combat.venom
        for _ in range(60):
            self.step()
            owned = [flight for flight in mines.flights if flight.owner is self.b]
            if owned:
                break
        self.assertEqual(len(owned), 1)
        flight = owned[0]
        remaining = flight.remaining
        self.step()
        self.assertAlmostEqual(remaining - flight.remaining, combat.TICK)
        self.run.match.switch_hero(self.a, heroes()[0x02E000000000006E])
        self.assertIn(flight, mines.flights)
        self.assertEqual(flight.body, self.b.body)

    def test_reflected_mine_deploys_and_poisons_original_shooter(self):
        if not self.run.match.combat.projectile_tags.admitted(0x04000000000000AF):
            self.skipTest("requires explicit projectile tag input")
        self.start()
        self.step(widow=16)
        self.step(25)
        mines = self.run.match.combat.venom
        owned = [item for item in (*mines.flights, *mines.mines) if item.owner is self.b]
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0].body, self.b.body)
        mine_weapon = next(
            i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x56
        )
        counter = mine_weapon.state(0).counter
        self.step(widow=16)
        self.step(15)
        self.assertEqual(mine_weapon.state(0).counter, counter)
        self.step(500)
        self.assertEqual(self.health(self.b), 200)
        self.assertAlmostEqual(self.health(self.a), self.initial_widow_health - 75)
        self.assertEqual(sum(h["+0x78"]["+0x4"] for h in self.b.client.messages(combat.HIT)), 75)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
