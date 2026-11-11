"""Actual Mercy attacks against an Assassin pinning a teammate."""

import unittest
from unittest.mock import Mock

import test_game_mercy_npc_geometry as geometry
from test_game_mercy_blaster_critical import equip, shot
from test_game_combat import Run, aim
from test_game_mercy_staff import SCENE
from test_game_mover import FLOOR, wall

from ow174.game import assassin, npc, npc_control, stats
from ow174.game.collision import World
from ow174.game.content import heroes

MERCY = 0x02E0000000000004


class MercyAssassinRescueTests(unittest.TestCase):
    def encounter(self, *, blaster=False, attacker_hp=None):
        geometry_case = geometry.MercyNpcGeometryTests()
        geometry_case.setUp()
        self.addCleanup(geometry_case.doCleanups)
        fixture = geometry_case.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.hero == assassin.HERO)
        run = Run(SCENE, ("Mercy", "Victim"))
        mercy, victim = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(mercy, heroes()[MERCY])
        mercy.team = victim.team = 0
        mercy.mover.position[:] = (1.5, 1, 2.5)
        victim.mover.position[:] = (0, 1, 0)
        for player in run.players:
            player.mover.body = None
            player.client.bot_stream = Mock(return_value=None)
            player.client.queue_bot_death = Mock()
            player.client.queue_bot_death_update = Mock()
        bot = npc.spawn(run.match, definition, (0, 1, 2.5), team=1)
        if blaster:
            equip(run, mercy)
        if attacker_hp is not None:
            target = next(t for t in fight._targets() if t.owner is bot)
            before = fight.vitals_of(target).total
            fight._damage(mercy, target, before - attacker_hp, False, None, run.frame)
        angle = aim(fight._eye(victim), (0, 2, 2.5))
        run.step({victim: (*angle, 0)})
        self.assertEqual(npc_control.start(run.match, victim), bot.entity)
        for _ in range(180):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), victim.body)
        targets = {t.owner.entity: t for t in fight._targets()}
        victim_health = fight.vitals_of(targets[victim.entity])
        attacker_health = fight.vitals_of(targets[bot.entity])
        return run, mercy, victim, bot, victim_health, attacker_health

    @staticmethod
    def melee(run, mercy, bot):
        x, y, z = bot.position
        yaw, pitch = aim(run.match.combat._eye(mercy), (x, y + 0.9, z))
        run.step({mercy: (yaw, pitch, 0x800)})

    def test_actual_melee_releases_teammate_and_recovery_prevents_immediate_repin(self):
        run, mercy, victim, bot, health, attacker_health = self.encounter()
        fight = run.match.combat
        self.assertEqual(health.total, 175)
        before = attacker_health.total
        self.melee(run, mercy, bot)
        for _ in range(30):
            run.step({})
            if not fight.is_stunned(victim):
                break
        self.assertFalse(fight.is_stunned(victim))
        self.assertEqual(attacker_health.total, before - 30)
        for _ in range(10):
            run.step({})
            self.assertFalse(fight.is_stunned(victim))
            self.assertEqual(health.total, 175)
        for _ in range(220):
            run.step({})
            if fight.is_stunned(victim):
                break
        self.assertTrue(fight.is_stunned(victim))
        self.assertEqual(health.total, 150)

    def test_blaster_hit_preserves_pin_until_a_lethal_followup_releases_it(self):
        run, mercy, victim, bot, health, attacker = self.encounter(
            blaster=True, attacker_hp=35)
        fight = run.match.combat
        self.assertEqual(health.total, 175)
        self.assertEqual(attacker.total, 35)
        eliminations = mercy.stats.value(stats.ELIMINATIONS)
        shot(run, mercy, bot.position, 0.8, frames=30)
        self.assertEqual(attacker.total, 15)
        self.assertEqual(fight.pins.get(bot.entity), victim.body)
        self.assertTrue(fight.is_stunned(victim))

        shot(run, mercy, bot.position, 0.8, frames=25)
        self.assertTrue(attacker.dead)
        self.assertTrue(bot.dead)
        self.assertEqual(attacker.total, 0)
        self.assertEqual(mercy.stats.value(stats.ELIMINATIONS), eliminations + 1)
        victim.client.queue_bot_death.assert_called_once()
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(victim))
        self.assertGreater(health.total, 0)
        after = health.total
        for _ in range(40):
            run.step({})
        self.assertEqual(health.total, after)
        self.assertFalse(fight.is_stunned(victim))

    def test_wall_rejects_melee_rescue_without_blocking_the_pin(self):
        run, mercy, victim, bot, health, attacker_health = self.encounter()
        run.match.collision_world = World(FLOOR + wall(0.75, 1.75, 0.75, 3.25))
        before = attacker_health.total
        self.melee(run, mercy, bot)
        for _ in range(28):
            run.step({})
        self.assertTrue(run.match.combat.is_stunned(victim))
        self.assertEqual(attacker_health.total, before)
        self.assertEqual(health.total, 175)
