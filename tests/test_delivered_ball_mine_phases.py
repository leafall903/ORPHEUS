"""Original Minefield phase and no-command world deadline checks."""
import math
import unittest
from dataclasses import replace
from unittest.mock import patch
import test_delivered_ball_mine_factory as mine_fixtures
from test_delivered_wrecking_ball_fireball import relocate
from test_delivered_ball_mine_contacts import phase_instance, REAPER
from test_game_combat import Run, KANEZAKA_DM, aim
from ow174.game import ball, barriers, combat, stats, world
from ow174.game.commands import Command
from ow174.game.content import heroes, SOLDIER
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
MOIRA = 0x02E00000000001A2
ZENYATTA = 0x02E0000000000020

class PhaseFixture(unittest.TestCase):
    def protection_instance(self, player):
        index, state = combat.DAMAGE_IMMUNITY_STATES[player.hero.guid]
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == index)
        return instance, instance.state(state)

    def cast_transcendence(self, run, zen):
        script = zen.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertGreater(cost, 0)
        script.set_ult_charge(cost)
        run.step({zen: (-32768, 0, 32)})
        _, immunity = self.protection_instance(zen)
        self.assertTrue(immunity.active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        return immunity

class MinePhaseTests(unittest.TestCase):
    setUpClass = classmethod(mine_fixtures.MineTests.setUpClass.__func__)
    scenario = mine_fixtures.MineTests.scenario
    deploy = mine_fixtures.MineTests.deploy
    isolated = mine_fixtures.MineTests.isolated
    target = mine_fixtures.MineTests.target

    def test_armed_mine_triggers_on_phase_world_expiry_without_recipient_commands(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                self.check_mine_at_phase_deadline(hero)

    def check_mine_at_phase_deadline(self, hero):
        run, player, enemy = self.scenario()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[hero]))
        x, y, z = player.mover.position
        relocate(run, enemy, (x + 100, y, z))
        mine = self.isolated(run, player)
        run.step({enemy: (0, 0, 8)})
        script = enemy.body_script
        frozen = script.frame, script.component.now
        timer = phase_instance(enemy).state(3)
        deadline = (timer.start + timer.ms) // script.frame_ms
        x, y, z = mine.position
        relocate(run, enemy, (x, y, z + 0.5))
        match = run.match
        fight = match.combat
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        vitals = fight.vitals_of(self.target(run, enemy))
        before = vitals.total
        self.assertTrue(mine.armed)
        self.assertFalse(mine.triggered)
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertEqual((script.frame, script.component.now), frozen)
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, before - 130)
        self.assertGreater(enemy.mover.velocity[1], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_armed_mine_excludes_fade_and_wraith_even_when_another_body_triggers(self):
        for soldier_triggers in (False, True):
            with self.subTest(soldier_triggers=soldier_triggers):
                self.check_phased_mine_recipients(soldier_triggers)

    def test_mine_damage_and_impulse_during_cryo_and_transcendence(self):
        for hero, cancel in ((barriers.MEI, False), (barriers.MEI, True), (ZENYATTA, False)):
            with self.subTest(hero=hex(hero), cancel=cancel):
                run, player, enemy = self.scenario()
                run.match.switch_hero(enemy, heroes()[hero])
                x, y, z = player.mover.position
                relocate(run, enemy, (x + 100, y, z))
                mine = self.isolated(run, player)
                self.assertTrue(mine.body_script.primary().states[28].active)
                phase = PhaseFixture()
                if hero == barriers.MEI:
                    run.step({enemy: (-32768, 0, 8)})
                    _, immunity = phase.protection_instance(enemy)
                    self.assertTrue(immunity.active)
                    if cancel:
                        for _ in range(20):
                            run.step({})
                        run.step({enemy: (-32768, 0, 2)})
                        self.assertFalse(immunity.active)
                else:
                    immunity = phase.cast_transcendence(run, enemy)

                fight = run.match.combat
                vitals = fight.vitals_of(self.target(run, enemy))
                before = vitals.total
                x, y, z = mine.position
                relocate(run, enemy, (x, y, z + 0.5))
                run.step({})
                self.assertTrue(mine.triggered)
                self.assertEqual(immunity.active, not cancel)
                self.assertEqual(vitals.total, before - (130 if cancel else 0))
                if hero == barriers.MEI and not cancel:
                    self.assertEqual(tuple(enemy.mover.velocity), (0, 0, 0))
                    self.assertFalse(enemy.mover.airborne)
                else:
                    self.assertGreater(enemy.mover.velocity[1], 4)
                    self.assertGreater(enemy.mover.velocity[2], 4)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_cryo_protection_expires_for_minefield_without_mei_commands(self):
        run, player, mei = self.scenario()
        run.match.switch_hero(mei, heroes()[barriers.MEI])
        x, y, z = player.mover.position
        relocate(run, mei, (x + 100, y, z))
        mine = self.isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        run.step({mei: (-32768, 0, 8)})
        _, immunity = PhaseFixture().protection_instance(mei)
        source = mei.body_script
        frozen = source.frame, source.component.now
        deadline = (immunity.start + 4000) // source.frame_ms
        match = run.match
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        self.assertFalse(mine.triggered)
        x, y, z = mine.position
        relocate(run, mei, (x, y, z + 0.5))
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(immunity.active)
        self.assertTrue(mine.triggered)
        self.assertEqual(match.combat.vitals_of(self.target(run, mei)).total, 120)
        self.assertGreater(mei.mover.velocity[1], 4)
        self.assertGreater(mei.mover.velocity[2], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
    def check_phased_mine_recipients(self, soldier_triggers):
        run = Run(KANEZAKA_DM, ('Ball', 'Moira', 'Reaper', 'Soldier'))
        player, moira, reaper, soldier = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, moira.team, reaper.team, soldier.team = 0, 1, 1, 1
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for actor, hero in ((player, ball.HERO), (moira, MOIRA), (reaper, REAPER)):
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
        x, y, z = player.mover.position
        for offset, actor in zip((100, 110, 120), (moira, reaper, soldier), strict=True):
            relocate(run, actor, (x + offset, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        primary = mine.body_script.primary()
        self.assertTrue(mine.armed)
        run.step({moira: (0, 0, 8), reaper: (0, 0, 8)})
        fade = phase_instance(moira).states[4]
        wraith = phase_instance(reaper).states[5]
        self.assertTrue(fade.active)
        self.assertTrue(wraith.active)

        x, y, z = mine.position
        relocate(run, moira, (x + 1, y, z))
        relocate(run, reaper, (x - 1, y, z))
        fight = match.combat
        moira_hp, reaper_hp, soldier_hp = (
            fight.vitals_of(self.target(run, actor)) for actor in (moira, reaper, soldier)
        )
        for _ in range(10):
            run.step({})
            self.assertTrue(fade.active)
            self.assertTrue(wraith.active)
            self.assertFalse(mine.triggered)
        if soldier_triggers:
            relocate(run, soldier, (x, y, z + 1))
            run.step({})
        else:
            for _ in range(50):
                run.step({})
                if not fade.active:
                    break
                self.assertFalse(mine.triggered)
            self.assertFalse(fade.active)

        self.assertTrue(mine.triggered)
        self.assertEqual(fade.active, soldier_triggers)
        self.assertTrue(wraith.active)
        hit = soldier if soldier_triggers else moira
        self.assertEqual(primary.vars[375].value(), (expr.Entity(hit.body),))
        self.assertEqual(moira_hp.total, 200 if soldier_triggers else 70)
        self.assertEqual(reaper_hp.total, 250)
        self.assertEqual(soldier_hp.total, 70 if soldier_triggers else 200)
        self.assertGreater(hit.mover.velocity[1], 4)
        self.assertFalse(reaper.mover.airborne)
        if soldier_triggers:
            self.assertFalse(moira.mover.airborne)
        for _ in range(200):
            run.step({})
            if not wraith.active:
                break
        self.assertFalse(fade.active)
        self.assertFalse(wraith.active)
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertEqual(reaper_hp.total, 250)
        self.assertEqual(moira_hp.total, 200 if soldier_triggers else 70)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
