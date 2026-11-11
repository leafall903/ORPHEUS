"""Resurrection target rejection and actual NPC attacks against revived players.

Manual corpse retention,explicit lethal setup and flat collision are declared;
this does not restore NPC AI or native resurrectable-target services.
"""

import unittest
from unittest.mock import Mock

import test_game_mercy_npc_geometry as geometry

from test_game_combat import aim
import test_game_mercy_resurrect as resurrect_tests

from ow174.game import assassin, heavy_charge, npc, npc_control
from ow174.game.script import nodes


class MercyNpcResurrectTests(unittest.TestCase):
    def setUp(self):
        g = geometry.MercyNpcGeometryTests()
        g.setUp()
        self.addCleanup(g.doCleanups)
        self.f = g.npc_fixture()

    def reject(self, definition, team, dead):
        f = self.f
        run, player, bot, target = f.spawn(definition, team)
        fight = run.match.combat
        if dead:
            source = player
            if team == player.team:
                source = npc.spawn(run.match, definition, (20, 1, 20), team=1)
            fight._damage(source, target, 1e8, False, None, run.frame)
            self.assertTrue(bot.dead)
        body, health = bot.entity, fight.vitals_of(target).total
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xCA)
        f.tick(run, player, bot, 3, buttons=0)
        f.tick(run, player, bot, 1, buttons=0x10)
        f.tick(run, player, bot, 140, buttons=0)
        self.assertEqual(bot.entity, body)
        self.assertEqual(bot.dead, dead)
        self.assertEqual(fight.vitals_of(target).total, health)
        self.assertEqual(instance.find_var(0, 61).value(), 0)
        self.assertEqual(player.stats.value(0x086000000000026A), 0)
        self.assertFalse(any(isinstance(s, nodes.Ability) and s.flags & s.RUNNING
                             for s in instance.states.values()))
        self.assertFalse(player.body_script_failed)
        if dead:
            self.assertIn(bot, run.match.bots)
            self.assertFalse(any(who is bot for _, who in fight.respawns))

    def test_revived_player_rejects_actual_pounce_and_charge_until_immunity_expires(self):
        cases = (("Assassin", 2.5), ("Heavy Assault", 2.0), ("Heavy Assault", 0.7))
        for name, distance in cases:
            with self.subTest(npc=name, distance=distance):
                definition = next(d for d in self.f.catalog.values() if d.name == name)
                resurrection = resurrect_tests.ResurrectTests()
                resurrection.setUp()
                self.addCleanup(resurrection.doCleanups)
                resurrection.cast()
                run, ally, fight = resurrection.run, resurrection.ally, resurrection.fight
                resurrection.mercy.mover.position[:] = (0, 1, -8)
                resurrection.mercy.mover.body = None
                ally.client.bot_stream = Mock(return_value=None)
                health = fight.vitals_of(resurrection.target)
                body = ally.body
                charge = name == "Heavy Assault"
                close_impact = charge and distance < 1

                def attack():
                    bot = npc.spawn(run.match, definition, (distance, 1, 3), team=resurrection.enemy.team)
                    yaw, pitch = aim(fight._eye(ally), (distance, 2, 3))
                    # The revived subject cannot turn during the rising lock.
                    ally.mover.yaw, ally.mover.pitch = yaw, pitch
                    self.assertEqual(npc_control.start(run.match, ally, charge=charge), bot.entity)
                    return bot

                protected = attack()
                for _ in range(100):
                    run.step({})
                    self.assertFalse(fight.is_stunned(ally))
                self.assertTrue(fight.damage_immune(resurrection.target))
                self.assertEqual(health.total, 200)
                if close_impact:
                    instance = heavy_charge.charge(protected)
                    impact = instance.graph.node(heavy_charge.IMPACT_HEALTH)
                    self.assertGreater(instance.states[impact.state].counter, 0)
                    self.assertNotIn(protected.entity, fight.heavy_pins)
                elif charge:
                    self.assertLess(protected.position[0], distance - 0.5)
                    self.assertNotIn(protected.entity, fight.heavy_pins)
                else:
                    script = next(i for i in protected.body_script.component.instances.values()
                                  if i.graph.index == assassin.GRAPH)
                    self.assertGreater(script.states[script.graph.node(15).state].counter, 0)
                    self.assertNotIn(protected.entity, fight.pins)

                npc.remove_spawned(run.match)
                resurrection.step(45)
                self.assertFalse(fight.damage_immune(resurrection.target))
                self.assertEqual(ally.body, body)
                exposed = attack()
                for _ in range(100):
                    run.step({})
                    if fight.is_stunned(ally):
                        break
                self.assertTrue(fight.is_stunned(ally))
                if close_impact:
                    self.assertNotIn(exposed.entity, fight.heavy_pins)
                    self.assertEqual(health.total, 25)
                    self.assertEqual(fight.stuns[body] - run.frame, 125)
                elif charge:
                    self.assertIs(fight.heavy_pins[exposed.entity][0], ally)
                    self.assertEqual(health.total, 200)
                else:
                    self.assertEqual(fight.pins[exposed.entity], body)
                    self.assertEqual(health.total, 175)
                npc_control.stop(run.match)
                if close_impact:
                    resurrection.step(125)
                self.assertFalse(fight.is_stunned(ally))

    def test_all_five_living_friendly_npcs_reject_res_without_cooldown_or_credit(self):
        for definition in self.f.catalog.values():
            with self.subTest(npc=definition.name):
                self.reject(definition, 0, False)

    def test_all_five_friendly_npc_corpses_reject_res_and_retain_same_corpse(self):
        for definition in self.f.catalog.values():
            with self.subTest(npc=definition.name):
                self.reject(definition, 0, True)

    def test_all_five_hostile_npc_corpses_reject_res_without_cooldown_or_credit(self):
        for definition in self.f.catalog.values():
            with self.subTest(npc=definition.name):
                self.reject(definition, 1, True)
