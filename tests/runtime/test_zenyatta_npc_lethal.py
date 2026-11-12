"""Actual five-NPC lethal attacks against a live Zenyatta with both orbs."""
import os
import unittest

import test_zenyatta_npcs as scenes
from test_game_combat import aim

from ow174.game import combat, npc_control
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes
from ow174.game.mover import mover_data


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA"), "requires all five actual NPC bodies")
class NpcLethalZenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        scenes.ZenyattaNpcTests.tearDownClass()

    def test_each_actual_npc_kills_zen_and_retires_harmony_discord_and_charge(self):
        for key, definition in scenes.ZenyattaNpcTests.catalog.items():
            with self.subTest(npc=definition.name):
                self.kill_zen(key, definition)

    def kill_zen(self, key, definition):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, bot_health = fixture.arena(definition, names=("Zen", "Ally"))
        ally = run.players[1]
        x, y, z = zen.mover.position
        ally.team = zen.team
        ally.mover.position = [x+3, y, z+2]
        ally.mover.body = None
        if key == "4":
            bot.position = (x, y, z+2)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is zen)
        ally_target = next(t for t in fight._targets() if t.owner is ally)
        vitals = fight.vitals_of(own)
        ally_health = fight.vitals_of(ally_target)
        ally_health.damage(100)
        vitals.damage(vitals.total-1)
        ally_aim = aim(fight._eye(zen), (x+3, y+1.5, z+2))
        run.step({zen: (*ally_aim, 0x10)})
        run.step({zen: (0, 0, 0x08)})
        run.step({})
        owner = fight.zenyatta.owners[zen.body]
        self.assertEqual(owner.harmony.target, ally.body)
        self.assertEqual(owner.discord.target, bot.entity)
        bx, by, bz = bot.position
        angles = aim(fight._eye(zen), (bx, by+mover_data(bot.body).stand_height*.5, bz))
        for _ in range(60):
            run.step({zen: (*angles, SECONDARY)})
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertGreater(weapon.vars[1392].value(), 0)
        original_health = bot_health.total
        self.assertEqual(npc_control.start(run.match, zen), bot.entity)
        for _ in range(145):
            run.step({zen: (*angles, SECONDARY)})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(bot_health.total, original_health)
        self.assertNotIn(zen.body, fight.zenyatta.owners)
        self.assertNotIn(zen.body, fight.pins.values())
        heals = ally_health.total
        for _ in range(16):
            run.step({zen: (*angles, SECONDARY)})
        self.assertEqual(weapon.vars[1392].value(), 0)
        self.assertEqual(ally_health.total, heals)
        self.assertEqual(bot_health.total, original_health)
        notices = zen.client.messages(combat.DEATH_NOTICE)
        self.assertTrue(any(row["+0x7C"]["+0x0"] == bot.entity for row in notices))

    def test_actual_allied_dart_interrupts_each_manual_npc_then_attack_resumes(self):
        for key, definition in scenes.ZenyattaNpcTests.catalog.items():
            with self.subTest(npc=definition.name):
                self.sleep_npc(key, definition)

    def sleep_npc(self, key, definition):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(definition, names=("Zen", "Ana"))
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zen.team
        x, y, z = zen.mover.position
        ana.mover.position = [x+3, y, z+1]
        ana.mover.body = None
        if key == "4":
            bot.position = (x, y, z+2)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is zen)
        health = fight.vitals_of(own)
        bx, by, bz = bot.position
        aim_point = (bx, by+mover_data(bot.body).stand_height*.5, bz)
        zen_aim = aim(fight._eye(zen), aim_point)
        run.step({zen: (*zen_aim, 0)})
        self.assertEqual(npc_control.start(run.match, zen), bot.entity)
        for _ in range(400):
            run.step({})
            if health.total < 200:
                break
        self.assertLess(health.total, 200)
        dart_aim = aim(fight._eye(ana), aim_point)
        run.step({ana: (*dart_aim, 8)})
        for _ in range(70):
            run.step({ana: (*dart_aim, 0)})
            if bot.entity in fight.sleeps:
                break
        self.assertIn(bot.entity, fight.sleeps)
        start, end, waking = fight.sleeps[bot.entity]
        self.assertFalse(waking)
        self.assertEqual(end-start, 313)
        for _ in range(4):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertNotIn(zen.body, fight.pins.values())
        while run.frame < end-1:
            run.step({})
            self.assertFalse(bot.attack_input)
        before = health.total
        for _ in range(400):
            run.step({})
            if health.total < before:
                break
        self.assertNotIn(bot.entity, fight.sleeps)
        self.assertLess(health.total, before)
