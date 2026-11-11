"""Actual Enforcer weapon vs Lucio with declared manual controller context.

This does not establish native controller allocation, pellet/spread fidelity,
autonomous AI or the raw graph's released-input ownership contract.
"""

import unittest
from unittest.mock import patch

import test_lucio_pve as pve
from test_game_combat import aim

from ow174.game import combat, npc, npc_context, npc_control
from ow174.game.commands import FIRE


@unittest.skipUnless(pve.CATALOG and pve.BUNDLE, 'requires actual five-type NPC resources')
class LucioEnforcerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = pve.LucioPveTests()
        cls.fixture.setUpClass()
        cls.definition = next(d for d in cls.fixture.definitions.values() if d.hero == npc_context.ENFORCER)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def scenario(self):
        helper, run, source, bot = self.fixture.scenario(self.definition, position=(0, 1, 3))
        source.body_script.ult_seconds = 0
        self.assertEqual(bot.body_script.component.world.controller_components, (0x4B,))
        return helper, run, source, bot

    def start(self, run, source, bot):
        source.mover.yaw = source.mover.pitch = 0
        self.assertEqual(npc_control.start(run.match, source), bot.entity)

    def first_damage(self, helper, run, source, before, limit=300):
        for _ in range(limit):
            run.step({})
            if helper.health(run, source) < before:
                return before-helper.health(run, source)
        self.fail('actual Enforcer graph produced no incoming damage')

    def health_query(self, bot):
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        return instance.evaluate({'$':'STU_9B7648C3','m_entity':{'$':'STU_B5A0CAF0'}})

    def test_initial_health_and_live_damage_healing_death_feed_the_authored_query(self):
        _, run, source, bot = self.scenario()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        self.assertEqual(self.health_query(bot), 350.)
        fight._damage(source, target, 35., False, None, run.frame)
        self.assertEqual(self.health_query(bot), 315.)
        target.team = bot.team = source.team
        fight.heal_area(source, bot.position, 5., 10.)
        self.assertEqual(self.health_query(bot), 325.)
        fight._damage(source, target, 9999., False, None, run.frame)
        self.assertEqual(self.health_query(bot), 0.)
        self.assertTrue(bot.dead)

    def test_actual_incoming_damage_manual_stop_and_reengagement(self):
        helper, run, source, bot = self.scenario()
        self.start(run, source, bot)
        self.assertEqual(self.first_damage(helper, run, source, 200.), 2.)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(100):
            run.step({})
        self.assertEqual(helper.health(run, source), 198.)
        self.start(run, source, bot)
        self.assertEqual(self.first_damage(helper, run, source, 198.), 2.)

    def test_wall_and_team_loss_release_manual_input_then_enemy_reacquires(self):
        for condition in ('wall', 'team'):
            with self.subTest(condition=condition):
                helper, run, source, bot = self.scenario()
                self.start(run, source, bot)
                clear_world = run.match.collision_world
                if condition == 'wall':
                    self.fixture.wall(run)
                else:
                    bot.team = source.team
                for _ in range(100):
                    run.step({})
                self.assertEqual(helper.health(run, source), 200.)
                button = bot.body_script.component.world.ai_weapon_button(0)
                self.assertNotIn(button, bot.body_script.component.held)
                if condition == 'wall':
                    run.match.collision_world = clear_world
                else:
                    bot.team = 1
                self.assertEqual(self.first_damage(helper, run, source, 200.), 2.)

    def test_actual_enforcer_damage_is_absorbed_by_sound_barrier_before_base_health(self):
        helper, run, source, bot = self.scenario()
        source.body_script.set_ult_charge(2940.)
        run.step({source:(0,0,32)})
        for _ in range(41):
            run.step({})
        self.assertEqual(helper.health(run, source), 950.)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
        self.start(run, source, bot)
        start = run.frame
        with patch.object(fight, '_damage', wraps=fight._damage) as damage:
            for _ in range(300):
                run.step({})
                hits = [call for call in damage.call_args_list if call.args[0] is bot]
                if hits:
                    break
        self.assertTrue(hits)
        self.assertEqual(hits[0].args[2], 2.)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200.)
        self.assertEqual(helper.health(run, source), 950.-(run.frame-start)*2.-2.)

    def test_actual_primary_kills_enforcer_and_retained_corpse_cannot_reengage(self):
        helper, run, source, bot = self.scenario()
        self.start(run, source, bot)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(450):
            yaw,pitch = aim(run.match.combat._eye(source), (0, 1.8, 3))
            run.step({source:(yaw,pitch,FIRE)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(self.health_query(bot), 0.)
        self.assertEqual(helper.health(run, bot), 0.)
        with self.assertRaises(ValueError):
            self.start(run, source, bot)
        for _ in range(100):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertEqual(helper.health(run, source), 200.)
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertNotIn(bot, run.match.bots)


if __name__ == '__main__':
    unittest.main()
