"""Actual Nano Boost and Tracer damage in a team mode."""

import unittest
from dataclasses import replace

from test_game_combat import PRACTICE_RANGE, Run, aim
from test_game_mover import box

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import find_hero


class TracerAnaNanoTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(replace(PRACTICE_RANGE, team_sizes=(6, 6)), ("Ana", "Tracer", "Enemy"))
        match = self.run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.fight = match.combat
        self.ana, self.player, self.enemy = self.run.players
        for actor, name in ((self.ana, "Ana"), (self.player, "Tracer")):
            self.assertTrue(match.switch_hero(actor, find_hero(name)))
        self.ana.team = self.player.team = 0
        self.enemy.team = 1
        x, y, z = self.ana.mover.position
        self.player.mover.position[:] = (x, y, z + 6)
        self.enemy.mover.position[:] = (x + 3, y, z + 6)
        for actor in self.run.players:
            actor.mover.body = None
        self.script = self.ana.body_script
        self.script.ult_seconds = 0
        self.cost = self.script.component.entity_var(338).value()
        for _ in range(25):
            self.run.step({})
        self.health = self.vitals(self.player)

    def vitals(self, actor):
        target = next(t for t in self.fight._targets() if t.owner is actor)
        return self.fight.vitals_of(target)

    def press(self, charge):
        self.script.set_ult_charge(charge)
        x, y, z = self.player.mover.position
        angles = aim(self.fight._eye(self.ana), (x, y + 0.9, z))
        self.run.step({self.ana: (*angles, 32)})

    def cast(self):
        self.press(self.cost)
        self.assertEqual(self.health.nano_until, 0)
        for _ in range(35):
            self.run.step({})
            if self.health.nano_until:
                break
        self.assertEqual(self.script.component.entity_var(342).value(), 0)
        self.assertEqual(self.health.nano_until - self.fight.tick, 500)

    def fire(self, shooter, victim):
        x, y, z = victim.mover.position
        angles = aim(self.fight._eye(shooter), (x, y + 0.9, z))
        vitals = self.vitals(victim)
        before = vitals.total
        self.run.step({shooter: (*angles, 1)})
        return before - vitals.total

    def test_actual_nano_heals_and_modifies_both_damage_directions_until_expiry(self):
        self.health.damage(75)
        self.cast()
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.fire(self.player, self.enemy), 18)
        self.assertEqual(self.fire(self.enemy, self.player), 10)
        while self.fight.tick + 1 < self.health.nano_until:
            self.run.step({})
        self.assertEqual(self.fire(self.player, self.enemy), 12)
        self.assertEqual(self.fire(self.enemy, self.player), 20)

    def test_actual_nano_boosts_quick_melee_once(self):
        self.cast()
        x, y, z = self.player.mover.position
        self.enemy.mover.position[:] = (x, y, z + 1.5)
        self.enemy.mover.body = None
        health = self.vitals(self.enemy)
        before = health.total
        self.run.step({self.player: (0, 0, 0x800)})
        for _ in range(40):
            self.run.step({})
        self.assertEqual(before - health.total, 45)

    def test_pulse_released_under_nano_keeps_damage_after_nano_expires(self):
        self.cast()
        x, y, z = self.player.mover.position
        self.enemy.mover.position[:] = (x, y, z + 3)
        self.enemy.mover.body = None
        enemy_health = self.vitals(self.enemy)
        enemy_health.most[combat.HEALTH_POOL] = enemy_health.now[combat.HEALTH_POOL] = 1000
        while self.fight.tick < self.health.nano_until - 40:
            self.run.step({})
        script = self.player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.player: (0, 0, 32)})
        for _ in range(35):
            self.run.step({})
            if enemy_health.total < 1000:
                break
        self.assertEqual(enemy_health.total, 992.5)
        self.assertGreater(self.health.nano_until, self.fight.tick)
        for _ in range(75):
            self.run.step({})
            if not self.fight.pulse_bombs.bombs:
                break
        self.assertEqual(enemy_health.total, 467.5)
        self.assertFalse(self.fight.pulse_bombs.bombs)

    def test_actual_deflect_rebases_boosted_pulse_to_unboosted_genji(self):
        self.cast()
        self.assertTrue(self.run.match.switch_hero(self.enemy, find_hero("Genji")))
        x, y, z = self.player.mover.position
        self.enemy.mover.position[:] = (x, y, z + 3)
        self.enemy.mover.body = None
        reflected = aim(self.fight._eye(self.enemy), (x, y + 1, z))
        script = self.player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.player: (0, 0, 32), self.enemy: (*reflected, 16)})
        bomb = None
        for _ in range(45):
            self.run.step({self.enemy: (*reflected, 16)})
            if self.fight.pulse_bombs.bombs:
                bomb, = self.fight.pulse_bombs.bombs
                if bomb.shooter is self.enemy:
                    break
        self.assertIsNotNone(bomb)
        self.assertIs(bomb.shooter, self.enemy)
        self.assertEqual((bomb.direct.amount, bomb.blast.amount), (5, 350))
        for _ in range(25):
            self.run.step({self.enemy: (*reflected, 0)})
            if bomb.stuck:
                break
        self.assertEqual(bomb.attached, self.player.body)
        self.assertEqual(self.health.total, 147.5)

    def test_actual_nano_rejects_range_and_cover_without_spending_charge(self):
        for gate in ("range", "cover"):
            with self.subTest(gate=gate):
                self.setUp()
                x, y, z = self.ana.mover.position
                if gate == "range":
                    self.player.mover.position[:] = (x, y, z + 41)
                    self.player.mover.body = None
                else:
                    floor = [(-1000, y, -1000, -1000, y, 1000, 1000, y, 1000),
                             (-1000, y, -1000, 1000, y, 1000, 1000, y, -1000)]
                    self.run.match.collision_world = World(
                        floor + box(x - 3, x + 3, y, y + 4, z + 3, z + 3.2),
                    )
                self.health.damage(75)
                self.press(self.cost)
                for _ in range(25):
                    self.run.step({})
                self.assertEqual(self.script.component.entity_var(342).value(), self.cost)
                self.assertEqual(self.health.total, 75)
                self.assertEqual(self.health.nano_until, 0)

    def test_released_nano_survives_caster_death_but_not_target_replacement(self):
        self.cast()
        old_body = self.player.body
        deadline = self.health.nano_until
        x, y, z = self.ana.mover.position
        self.enemy.mover.position[:] = (x + 2, y, z)
        self.enemy.mover.body = None
        ana_health = self.vitals(self.ana)
        ana_health.damage(199)
        self.fire(self.enemy, self.ana)
        self.assertTrue(ana_health.dead)
        self.assertEqual(self.health.nano_until, deadline)
        self.assertEqual(self.fire(self.player, self.enemy), 18)
        self.assertTrue(self.run.match.switch_hero(self.player, find_hero("Soldier: 76")))
        self.assertNotEqual(self.player.body, old_body)
        self.assertEqual(self.vitals(self.player).nano_until, 0)

    def test_actual_enemy_fire_kills_ana_before_nano_release(self):
        self.health.damage(75)
        x, y, z = self.ana.mover.position
        self.enemy.mover.position[:] = (x + 2, y, z)
        self.enemy.mover.body = None
        ana_health = self.vitals(self.ana)
        ana_health.damage(199)
        self.press(self.cost)
        self.assertEqual(self.script.component.entity_var(342).value(), 0)
        self.assertEqual(self.health.nano_until, 0)
        self.fire(self.enemy, self.ana)
        self.assertTrue(ana_health.dead)
        for _ in range(35):
            self.run.step({})
        self.assertEqual(self.health.total, 75)
        self.assertEqual(self.health.nano_until, 0)

    def test_actual_target_team_and_charge_gate_rejects_then_allows_cast(self):
        self.health.damage(75)
        self.press(self.cost - 1)
        for _ in range(20):
            self.run.step({})
        self.assertEqual(self.health.total, 75)
        self.assertEqual(self.script.component.entity_var(342).value(), self.cost - 1)
        self.player.team = self.enemy.team
        self.press(self.cost)
        for _ in range(20):
            self.run.step({})
        self.assertEqual(self.health.total, 75)
        self.assertEqual(self.script.component.entity_var(342).value(), self.cost)
        self.player.team = self.ana.team
        self.cast()
        self.assertEqual(self.health.total, 150)


if __name__ == "__main__":
    unittest.main()
