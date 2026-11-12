"""Actual Mercy health changes using unchanged authored graphs and match input.

These headless controls are partial server evidence, not original-client or
five-enemy PvE acceptance.
"""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bodytrace, combat, movelog, stats
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import HUD_HEALTH, OWNER_HEALTH, OWNER_MAX_HEALTH
from ow174.game.script.regeneration import MERCY


class MercyCombatTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))
        self.run = Run(KANEZAKA_DM, ("Mercy", "Enemy"))
        self.mercy, self.enemy = self.run.players
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.match.switch_hero(self.mercy, heroes()[MERCY])
        self.run.step({})  # establish the match's world clock before damage/death deadlines
        self.fight = self.match.combat
        self.target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.vitals = self.fight.vitals_of(self.target)

    def step(self, count):
        for _ in range(count):
            self.run.step({})

    def damage(self, amount=50):
        self.fight._damage(self.enemy, self.target, amount, False, None, self.run.frame)

    def test_damage_waits_one_second_then_restores_actual_health_and_hud(self):
        self.damage()
        self.step(62)
        self.assertEqual(self.vitals.total, 150)
        self.step(63)
        self.assertAlmostEqual(self.vitals.total, 170.16, places=4)
        component = self.mercy.body_script.component
        for key in (OWNER_HEALTH, HUD_HEALTH):
            self.assertAlmostEqual(component.entity_var(key).value(), self.vitals.total, places=4)
        self.assertEqual(component.entity_var(OWNER_MAX_HEALTH).value(), 200)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)  # passive grants no team-healing credit

    def test_repeated_damage_restarts_the_authored_wait(self):
        self.damage()
        self.step(80)
        self.assertGreater(self.vitals.total, 150)
        self.damage(10)
        before = self.vitals.total
        self.step(62)
        self.assertEqual(self.vitals.total, before)
        self.step(3)
        self.assertGreater(self.vitals.total, before)

    def test_death_cannot_regenerate_or_cancel_normal_player_respawn(self):
        self.damage(250)
        self.step(140)
        self.assertTrue(self.vitals.dead)
        self.assertEqual(self.vitals.total, 0)
        self.assertTrue(self.mercy.body_script.component.owner_dead)
        self.assertTrue(any(who is self.mercy for _, who in self.fight.respawns))

    def test_capped_health_and_duplicate_commands_cannot_grant_extra_healing(self):
        self.damage(1)
        self.step(100)
        self.assertEqual(self.vitals.total, 200)
        self.damage(50)
        self.step(70)
        before = self.vitals.total
        self.mercy.take_commands([Command(self.run.frame)])
        self.assertEqual(self.vitals.total, before)
        self.assertEqual(self.mercy.body_script.self_healing, 0)

    def test_switching_hero_retires_the_passive(self):
        self.damage()
        self.step(80)
        self.match.switch_hero(self.mercy, heroes()[SOLDIER])
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        vitals = self.fight.vitals_of(target)
        self.fight._damage(self.enemy, target, 50, False, None, self.run.frame)
        self.step(150)
        self.assertEqual(vitals.total, 150)

    def test_damage_during_the_wait_postpones_healing(self):
        self.damage()
        self.step(40)
        self.damage(5)
        self.step(40)
        self.assertEqual(self.vitals.total, 145)
        self.step(25)
        self.assertGreater(self.vitals.total, 145)

    def test_retained_input_gap_counts_only_simulated_regeneration_frames(self):
        self.damage()
        self.run.frame += 125
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame)])
        self.assertAlmostEqual(self.vitals.total, 170.16, places=4)
        self.assertEqual(self.mercy.body_script.self_healing, 0)

    def test_training_npc_damage_restarts_self_healing_without_healing_enemies(self):
        npc_run = Run()
        mercy, = npc_run.players
        npc_run.match.switch_hero(mercy, heroes()[MERCY])
        npc_run.step({})
        fight = npc_run.match.combat
        attacker = next(b for b in npc_run.match.bots if b.team != mercy.team)
        victim = next(t for t in fight._targets() if t.owner is mercy)
        hostile = next(t for t in fight._targets() if t.owner is attacker)
        hostile_health = fight.vitals_of(hostile)
        hostile_health.damage(20)
        fight._damage(attacker, victim, 50, False, None, npc_run.frame)
        for _ in range(125):
            npc_run.step({})
        self.assertAlmostEqual(fight.vitals_of(victim).total, 170.16, places=4)
        self.assertEqual(hostile_health.total, attacker.health - 20)

    def test_blaster_deals_projectile_damage_to_enemy_player(self):
        self.step(35)
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame, action=2)])
        self.step(35)
        for _ in range(20):
            x, y, z = self.enemy.mover.position
            yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 0.8, z))
            self.run.step({self.mercy: (yaw, pitch, FIRE)})
        self.step(10)
        vitals = self.fight.vitals[self.enemy.body]
        self.assertLess(vitals.total, 200)
        self.assertEqual(self.mercy.stats.value(stats.HERO_DAMAGE), 200 - vitals.total)

    def test_quick_melee_deals_thirty_once(self):
        x, y, z = self.mercy.mover.position
        self.enemy.mover.position = [x, y, z + 1.5]
        yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z + 1.5))
        self.run.step({self.mercy: (yaw, pitch, 0x800)})
        self.step(40)
        self.assertEqual(self.fight.vitals[self.enemy.body].total, 170)
        self.assertEqual([h["+0x78"]["+0x4"] for h in self.mercy.client.messages(combat.HIT)], [30])
