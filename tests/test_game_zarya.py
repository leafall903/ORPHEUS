"""Command-driven Zarya damage regressions; these do not accept client presentation."""

import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import bots, combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes

ZARYA = 0x02E0000000000068


class ZaryaBeamTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Enemy"))
        self.a, self.b = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[ZARYA])
        self.a.mover.position[:] = [0, 1, 0]
        self.b.mover.position[:] = [0, 1, 5]

    def fire(self, frames=51, buttons=1):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, buttons)})

    def health(self, owner=None):
        owner = self.b if owner is None else owner
        target = next(t for t in self.fight._targets() if t.owner is owner)
        return self.fight.vitals_of(target).total

    def test_primary_integrates_authored_75_dps_and_never_crits(self):
        self.fire()
        self.assertAlmostEqual(self.health(), 140)
        self.assertTrue(self.a.client.messages(combat.HIT))
        self.assertTrue(all(not (m["+0x78"]["+0x8"] & combat.CRITICAL)
                            for m in self.a.client.messages(combat.HIT)))

    def test_live_energy_expression_changes_damage_to_170_dps(self):
        self.a.body_script.component.entity_var(87).base = 100
        self.fire()
        self.assertAlmostEqual(self.health(), 64)

    def test_repeated_command_consumption_does_not_damage_twice(self):
        self.fire()
        before = self.health()
        command = Command(self.run.frame, yaw=0, pitch=0, buttons=1, extra=0)
        self.fight.command(self.a, command)
        self.fight.command(self.a, command)
        self.assertEqual(self.health(), before)

    def test_release_stops_damage(self):
        self.fire()
        self.fire(2, 0)
        before = self.health()
        self.fire(30, 0)
        self.assertEqual(self.health(), before)

    def test_allies_are_not_damaged(self):
        self.b.team = self.a.team
        self.fire()
        self.assertEqual(self.health(), 200)

    def test_out_of_range_target_is_not_damaged(self):
        self.b.mover.position[2] = 17
        self.fire()
        self.assertEqual(self.health(), 200)

    def test_enemy_rifle_consumes_zarya_shields_before_health(self):
        self.b.mover.position[2] = -5
        before = self.health(self.a)
        for _ in range(12):
            self.run.step({self.b: (0, 0, 1)})
        vitals = self.fight.vitals[self.a.body]
        self.assertLess(vitals.total, before)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
        self.assertLess(vitals.now[combat.SHIELDS_POOL], 200)

    def test_close_melee_deals_30_once(self):
        self.b.mover.position[2] = 1.5
        self.fire(1, 0x800)
        self.fire(40, 0)
        self.assertEqual(self.health(), 170)

    def test_secondary_projectile_deals_actual_damage(self):
        self.fire(1, 2)
        self.fire(90, 0)
        self.assertLess(self.health(), 200)

    def test_dead_zarya_cannot_continue_beam_damage(self):
        self.fire()
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 500, False, None, self.run.frame)
        before = self.health()
        self.fire(30)
        self.assertEqual(self.health(), before)

    def test_static_geometry_blocks_damage(self):
        wall = World([(-10, 0, 2, 10, 0, 2, 10, 10, 2),
                      (-10, 0, 2, 10, 10, 2, -10, 10, 2)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.fire()
        self.assertEqual(self.health(), 200)

    def test_body_retirement_discards_beam_clock(self):
        self.fire()
        old = self.a.body
        self.assertIn(old, self.fight.beam_seen)
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotIn(old, self.fight.beam_seen)

    def test_beam_damages_real_bot_actor_and_retains_manual_corpse(self):
        bot_hero, data = next(iter(bots.table()["heroes"].items()))
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": bot_hero, "team": 1, "position": [0, 1, 3],
            "rotation": [0, 0, 0, 1], "graphs": [],
        }, data, {})
        bot.retain_corpse = True
        self.run.match.bots.append(bot)
        self.fire()
        self.assertAlmostEqual(self.health(bot), bot.health - 60)
        self.assertEqual(self.health(), 200)
        self.fire(180)
        self.assertTrue(bot.dead)
        self.assertFalse(any(who is bot for _, who in self.fight.respawns))


if __name__ == "__main__":
    unittest.main()
