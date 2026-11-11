"""Actual Orisa Halt against the Torbjorn collision mover."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import (
    combat,
    fortify,
    hero_torbjorn,
    molten_core,
    npc,
    npc_control,
    orisa_halt,
    phase_defenses,
    stats,
)
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_ORISA_HALT", "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs authored Halt and combat inputs")
class TorbjornHaltTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, orisa, other = fixture.arena("Orisa", 7)
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        fixture.place(orisa, (x, y, z))
        fixture.place(torb, (x, y, z + 7))
        fixture.advance(run, torb, orisa, 20)
        return fixture, run, torb, orisa

    @staticmethod
    def cast(run, orisa, *, detonate=True):
        for frame in range(26):
            run.step({orisa: (0, 0, 2 if frame == 0 or (detonate and frame == 25) else 0)})
        orb, = run.match.combat.halt.orbs.values()
        return orb

    def test_actual_second_press_pulls_at_authored_speed_without_damage_and_rejects_retry(self):
        fixture, run, torb, orisa = self.arena()
        fight = run.match.combat
        initial = tuple(torb.mover.position)
        orb = self.cast(run, orisa)
        self.assertEqual(orb.requested, fight.tick)
        source = next(
            i for i in orisa.body_script.component.instances.values() if i.graph.index == orisa_halt.CAST
        )
        self.assertIn(orisa_halt.ORB_HANDLE_VAR, source.vars)
        self.assertNotIn(orisa_halt.ORB_HANDLE_VAR, dict(orisa.body_script.replicated_vars(source)))
        centre = orb.position
        for _ in range(24):
            run.step({})
        self.assertEqual(orb.position, centre)
        self.assertEqual(tuple(torb.mover.position), initial)
        run.step({})
        pull = fight.halt.pulls[torb.body]
        destination, speed = pull.instance.state(2).move_request()
        self.assertEqual(speed, 20)
        self.assertGreater(destination.z, initial[2])
        self.assertLess(destination.z, centre[2])
        for _ in range(25):
            run.step({})
        self.assertAlmostEqual(torb.mover.position[2], destination.z, delta=.002)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertFalse(fight.halt.orbs)
        self.assertGreater(source.state(2).remaining(), 5)
        run.step({orisa: (0, 0, 2)})
        for _ in range(15):
            run.step({})
        self.assertEqual(source.state(4).counter, 1)
        self.assertFalse(fight.halt.orbs)
        for _ in range(520):
            if source.state(2).remaining() <= 0:
                break
            run.step({})
        replacement = self.cast(run, orisa, detonate=False)
        self.assertNotEqual(replacement.entity, orb.entity)
        self.assertIsNone(replacement.requested)

    def test_fortify_rejects_or_releases_halt_without_releasing_torbjorn(self):
        for during_pull in (False, True):
            with self.subTest(during_pull=during_pull):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, caster, orisa = fixture.arena("Orisa", 7)
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(orisa, caster.hero))
                x, y, z = torb.mover.position
                fixture.place(caster, (x, y, z))
                fixture.place(torb, (x + 1.5, y, z + 7))
                fixture.place(orisa, (x - 1.5, y, z + 7))
                fixture.advance(run, torb, orisa, 35)
                hp = fixture.health(run, orisa)
                torb_hp = fixture.health(run, torb)
                start = tuple(orisa.mover.position)
                torb_start = tuple(torb.mover.position)
                if not during_pull:
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNotNone(fortify.state_for(orisa))
                orb = self.cast(run, caster)
                for _ in range(30):
                    if orb.pulled:
                        break
                    run.step({})
                self.assertTrue(orb.pulled)
                torb_pull = fight.halt.pulls[torb.body]
                if during_pull:
                    pull = fight.halt.pulls[orisa.body]
                    instance = pull.instance
                    for _ in range(40):
                        run.step({})
                        if math.dist(orisa.mover.position, start) > .05:
                            break
                    self.assertGreater(math.dist(orisa.mover.position, start), .05)
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNotNone(fortify.state_for(orisa))
                    self.assertNotIn(instance.id, pull.component.instances)
                else:
                    self.assertEqual(tuple(orisa.mover.position), start)
                self.assertNotIn(orisa.body, fight.halt.pulls)
                self.assertIs(fight.halt.pulls[torb.body], torb_pull)
                self.assertEqual(hp.total, 450)
                self.assertEqual(torb_hp.total, 250)
                released = tuple(orisa.mover.position)

                for _ in range(40):
                    if math.dist(torb.mover.position, torb_start) > .05:
                        break
                    run.step({})
                self.assertGreater(math.dist(torb.mover.position, torb_start), .05)
                self.assertIs(fight.halt.pulls[torb.body], torb_pull)
                fixture.step(run, torb, orisa, fire=1, defense=1)
                for _ in range(25):
                    if hp.total < 450 and torb_hp.total < 250:
                        break
                    fixture.step(run, torb, orisa)
                rx, _, rz = released
                self.assertEqual((orisa.mover.position[0], orisa.mover.position[2]), (rx, rz))
                self.assertGreater(math.dist(torb.mover.position, torb_start), .05)
                vx, _, vz = orisa.mover.velocity
                self.assertEqual((vx, vz), (0, 0))
                self.assertAlmostEqual(450 - hp.total, 37, places=5)
                self.assertEqual(250 - torb_hp.total, 6)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 37, places=5)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 6)


    def test_quick_melee_uses_aim_and_fortify_while_halt_is_moving_torbjorn(self):
        for case, damage in (("normal", 25), ("fortified", 13), ("away", 0)):
            with self.subTest(case=case):
                fixture, run, torb, orisa = self.arena()
                fight = run.match.combat
                hp = fixture.health(run, orisa)
                before = hp.total
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(0)
                charge = torb.body_script.component.entity_var(342)
                rifle = next(i for i in torb.body_script.component.instances.values()
                             if i.graph.index == 0x1683)
                orb = self.cast(run, orisa)
                for _ in range(40):
                    run.step({})
                    pull = fight.halt.pulls.get(torb.body)
                    if pull is not None and pull.instance.state(2).move_request() is not None:
                        break
                self.assertTrue(orb.pulled)
                pull = fight.halt.pulls[torb.body]
                self.assertIsNotNone(pull.instance.state(2).move_request())
                x, y, z = torb.mover.position
                fixture.place(orisa, (x + 1.5, y, z))
                if case == "fortified":
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNotNone(fortify.state_for(orisa))
                start = tuple(torb.mover.position)
                yaw, pitch = fixture.body_aim(run, torb, orisa)
                run.step({torb: (-16384 if case == "away" else yaw, pitch, 0x800)})
                self.assertIs(fight.halt.pulls[torb.body], pull)
                self.assertIsNotNone(pull.instance.state(2).move_request())
                self.assertGreater(torb.mover.position[2], start[2])
                self.assertAlmostEqual(hp.total, before - damage, places=5)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), damage, places=5)
                self.assertAlmostEqual(charge.value(), damage, places=5)
                self.assertEqual(rifle.vars[53].value(), 18)
                fixture.advance(run, torb, orisa, 65)
                self.assertNotIn(torb.body, fight.halt.pulls)
                self.assertAlmostEqual(hp.total, before - damage, places=5)
                self.assertEqual(fixture.health(run, torb).total, 250)

    def test_overload_cooldown_recovers_during_active_halt_without_stopping_pull(self):
        fixture, run, torb, orisa = self.arena()
        fight = run.match.combat
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        run.step({torb: (0, 0, 16)})
        first_source = hp.over_armour.source
        self.assertEqual(hp.over_armour.amount, 100)
        for _ in range(1000):
            if hero_torbjorn.overload_cooldown_remaining(torb) <= .856:
                break
            run.step({})
        self.assertIsNone(hp.over_armour.source)

        orb = self.cast(run, orisa)
        for _ in range(40):
            run.step({})
            pull = fight.halt.pulls.get(torb.body)
            if pull is not None and pull.instance.state(2).move_request() is not None:
                break
        self.assertTrue(orb.pulled)
        pull = fight.halt.pulls[torb.body]
        movement = pull.instance.state(2)
        self.assertIsNotNone(movement.move_request())
        self.assertGreater(hero_torbjorn.overload_cooldown_remaining(torb), combat.TICK)

        run.step({torb: (0, 0, 16)})
        self.assertIsNone(hp.over_armour.source)
        for _ in range(12):
            start = tuple(torb.mover.position)
            remaining = hero_torbjorn.overload_cooldown_remaining(torb)
            run.step({torb: (0, 0, 16)})
            self.assertGreater(torb.mover.position[2], start[2])
            self.assertIs(fight.halt.pulls[torb.body], pull)
            if hp.over_armour.source is not None:
                break
        self.assertLessEqual(remaining, combat.TICK + combat.EPSILON)
        self.assertEqual(hp.over_armour.source, (first_source[0], first_source[1] + 1))
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(movement.move_request()[1], 20)
        self.assertEqual(hp.now, base)

        fixture.step(run, torb, orisa, defense=1)
        for _ in range(25):
            if hp.over_armour.amount < 100:
                break
            run.step({})
        self.assertIs(fight.halt.pulls[torb.body], pull)
        self.assertEqual(hp.over_armour.amount, 94)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 6)
        self.assertEqual(hp.now, base)

    def test_halt_post_pull_slow_keeps_rivet_and_overload_then_restores_run_speed(self):
        for overload in (False, True):
            with self.subTest(overload=overload):
                fixture, run, torb, orisa = self.arena()
                fight = run.match.combat
                hp = fixture.health(run, torb)
                if overload:
                    run.step({torb: (0, 0, 16)})
                    self.assertEqual(hp.over_armour.amount, 100)
                armour_source = hp.over_armour.source
                self.cast(run, orisa)
                pull = wait = None
                for _ in range(100):
                    run.step({})
                    pull = fight.halt.pulls.get(torb.body)
                    wait = pull.instance.states.get(3) if pull is not None else None
                    if wait is not None and wait.active:
                        break
                self.assertIsNotNone(wait)
                self.assertTrue(wait.active)
                self.assertEqual(wait.ms, 650)
                self.assertIsNone(pull.instance.state(2).move_request())
                slow = torb.body_script.component.entity_var(23186)
                self.assertAlmostEqual(slow.value(), .65)

                x, y, z = torb.mover.position
                fixture.place(orisa, (x, y, z + 8))
                for _ in range(26):
                    self.forward_step(run, torb)
                fast = 7.15 if overload else 5.5
                self.assertTrue(wait.active)
                # Authored speed modifiers add to the same movement scalar.
                slowed = 3.575 if overload else 1.925
                self.assertAlmostEqual(torb.mover.velocity[2], slowed, delta=1 / 1024)
                orisa_hp = fixture.health(run, orisa)
                _, pitch = fixture.body_aim(run, torb, orisa)
                self.forward_step(run, torb, pitch=pitch, fire=1)
                for _ in range(12):
                    if orisa_hp.total < 450:
                        break
                    self.forward_step(run, torb)
                self.assertTrue(wait.active)
                self.assertEqual(450 - orisa_hp.total, 65)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 65)

                for _ in range(40):
                    if not wait.active:
                        break
                    self.forward_step(run, torb)
                self.assertFalse(wait.active)
                self.assertEqual(wait.ended_at - wait.start, 650)
                self.assertFalse(slow.value())
                self.assertIsNone(pull.instance)
                self.forward_step(run, torb)
                self.assertNotIn(torb.body, fight.halt.pulls)
                for _ in range(30):
                    self.forward_step(run, torb)
                self.assertAlmostEqual(torb.mover.velocity[2], fast, delta=1 / 1024)
                self.assertEqual(hp.total, 350 if overload else 250)
                self.assertEqual(hp.over_armour.source, armour_source)

    @staticmethod
    def forward_step(run, actor, *, pitch=0, fire=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        for player in run.players:
            if player is actor:
                command = Command(run.frame, pitch=pitch, forward=127, buttons=fire)
            else:
                command = Command(run.frame)
            player.take_commands([command])


    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Talon NPC data")
    def test_actual_halt_moves_talon_npcs_and_preserves_heavy_immunity(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                fixture, run, torb, caster, bot = self.talon_arena(definition)
                fight = run.match.combat
                hp = fight.vitals[bot.entity]
                before = hp.total
                start = bot.position
                orb = self.cast(run, caster)
                for _ in range(30):
                    if orb.pulled:
                        break
                    run.step({})
                self.assertTrue(orb.pulled)
                self.assertNotIn(torb.body, fight.halt.pulls)
                immune = definition.name == "Heavy Assault"
                if immune:
                    flag = bot.body_script.component.entity_var(6601, create=False)
                    self.assertTrue(flag is not None and flag.value())
                    self.assertNotIn(bot.entity, fight.halt.pulls)
                else:
                    pull = fight.halt.pulls[bot.entity]
                    self.assertEqual(pull.instance.graph.index, orisa_halt.PULL)
                    for _ in range(23):
                        run.step({})
                        self.assertEqual(bot.position, start)
                    for _ in range(75):
                        run.step({})
                        wait = pull.instance.states.get(3)
                        if wait is not None and wait.active:
                            break
                    self.assertTrue(wait is not None and wait.active)
                    self.assertEqual(wait.ms, 650)
                    self.assertGreater(bot.position[2], start[2] + .5)
                for _ in range(55):
                    run.step({})
                self.assertNotIn(bot.entity, fight.halt.pulls)
                self.assertEqual(bot.position == start, immune)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(hp.total, before)

                run.step({torb: (*fixture.body_aim(run, torb, bot), 1)})
                for _ in range(25):
                    if hp.total < before:
                        break
                    run.step({})
                self.assertEqual(before - hp.total, 70)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)


    def talon_arena(self, definition):
        fixture, run, torb, caster = self.arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        caster.team = torb.team
        x, y, z = caster.mover.position
        bot = npc.spawn(match, definition, (x, y, z + 5), team=1 - torb.team)
        self.assertIsNotNone(bot.body_script)
        for viewer in run.players:
            viewer.client.bot_stream = lambda *args: None
        fixture.place(torb, (x - 4, y, z + 7))
        return fixture, run, torb, caster, bot

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Talon NPC data")
    def test_torbjorn_lethal_rivet_clears_active_halt_on_all_susceptible_talon_npcs(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        susceptible = [definition for definition in catalog.values() if definition.name != "Heavy Assault"]
        self.assertEqual(len(susceptible), 4)
        for definition in susceptible:
            with self.subTest(npc=definition.name):
                fixture, run, torb, caster, bot = self.talon_arena(definition)
                fight = run.match.combat
                hp = fight.vitals[bot.entity]
                hp.damage(hp.total - 1, armour_reduction=False)
                fight._sync_body_health(bot, hp)
                orb = self.cast(run, caster)
                for _ in range(70):
                    run.step({})
                    if bot.velocity != (0, 0, 0):
                        break
                self.assertTrue(orb.pulled)
                self.assertNotEqual(bot.velocity, (0, 0, 0))
                pull = fight.halt.pulls[bot.entity]
                self.assertIsNotNone(pull.instance.state(2).move_request())

                x, y, z = bot.position
                fixture.place(torb, (x - 1.25, y, z))
                run.step({torb: (*fixture.body_aim(run, torb, bot), 1)})
                self.assertFalse(hp.dead)
                self.assertIsNotNone(pull.instance.state(2).move_request())
                for _ in range(12):
                    if hp.dead:
                        break
                    self.assertIsNotNone(pull.instance.state(2).move_request())
                    run.step({})
                self.assertTrue(hp.dead)
                self.assertTrue(bot.dead)
                self.assertEqual(hp.death_tick, run.frame)
                self.assertEqual(bot.body_script.death_frame, run.frame)
                self.assertEqual(hp.killer, torb.body)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 1)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 1)
                self.assertNotIn(bot.entity, fight.halt.pulls)
                self.assertIsNone(pull.instance)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertFalse(any(actor is bot for _, actor in fight.respawns))
                corpse = bot.position
                for _ in range(50):
                    run.step({})
                    self.assertEqual(bot.position, corpse)
                self.assertNotIn(bot.entity, fight.halt.pulls)


    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Talon NPC data")
    def test_removing_pulled_trooper_keeps_another_talon_pull_live(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        trooper = next(definition for definition in catalog.values() if definition.name == "Trooper")
        _, run, _, caster, bot = self.talon_arena(trooper)
        match = run.match
        fight = match.combat
        x, y, z = bot.position
        other = npc.spawn(match, trooper, (x + 1.5, y, z), team=bot.team)
        hp = fight.vitals[other.entity]
        health = hp.total
        self.cast(run, caster)
        for _ in range(70):
            run.step({})
            if bot.velocity != (0, 0, 0) and other.velocity != (0, 0, 0):
                break
        first = fight.halt.pulls[bot.entity]
        second = fight.halt.pulls[other.entity]
        self.assertIsNotNone(first.instance.state(2).move_request())
        self.assertIsNotNone(second.instance.state(2).move_request())
        self.assertNotEqual(bot.velocity, (0, 0, 0))
        self.assertNotEqual(other.velocity, (0, 0, 0))

        self.assertEqual(npc.remove_spawned(match, entities={bot.entity}), (bot.entity,))
        self.assertNotIn(bot, match.bots)
        self.assertNotIn(bot.entity, fight.halt.pulls)
        self.assertIsNone(first.instance)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertIs(fight.halt.pulls[other.entity], second)
        before = other.position
        run.step({})
        self.assertGreater(math.dist(other.position, before), .05)
        self.assertIs(fight.halt.pulls[other.entity], second)
        for _ in range(90):
            run.step({})
        self.assertNotIn(other.entity, fight.halt.pulls)
        self.assertIsNone(second.instance)
        self.assertEqual(other.velocity, (0, 0, 0))
        self.assertEqual(hp.total, health)


    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Talon NPC data")
    def test_halt_talon_recipients_return_fire_against_overload(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        expected = {"Trooper": 15, "Enforcer": 25, "Sniper": 95, "Assassin": 20, "Heavy Assault": 2.5}
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                fixture, run, torb, caster, bot = self.talon_arena(definition)
                match = run.match
                fight = match.combat
                hp = fixture.health(run, torb)
                bot_hp = fight.vitals[bot.entity]
                initial = bot.position
                x, y, z = initial
                distance = 1.5 if definition.name == "Assassin" else 5
                fixture.place(torb, (x - distance, y, z + 1))
                run.step({torb: (*fixture.body_aim(run, torb, bot), 16)})
                self.assertEqual(hp.over_armour.amount, 100)
                armour_source = hp.over_armour.source
                # Sniper and Enforcer need longer weapon windups than one pull.
                early = {"Sniper": 64, "Enforcer": 0}.get(definition.name)
                if early is not None:
                    self.assertEqual(npc_control.start(match, torb), bot.entity)
                    for _ in range(early):
                        run.step({})
                orb = self.cast(run, caster)
                for _ in range(35):
                    if orb.pulled:
                        break
                    run.step({})
                self.assertTrue(orb.pulled)
                immune = definition.name == "Heavy Assault"
                pull = fight.halt.pulls.get(bot.entity)
                self.assertEqual(pull is None, immune)
                self.assertFalse(fight.is_stunned(bot))
                if early is None:
                    if not immune:
                        for _ in range(40):
                            if bot.velocity != (0, 0, 0):
                                break
                            run.step({})
                        self.assertIsNotNone(pull.instance.state(2).move_request())
                        self.assertNotEqual(bot.velocity, (0, 0, 0))
                    run.step({torb: (*fixture.body_aim(run, torb, bot), 0)})
                    self.assertEqual(npc_control.start(match, torb), bot.entity)
                self.assertEqual(hp.over_armour.amount, 100)
                for _ in range(65):
                    if hp.over_armour.amount < 100:
                        break
                    run.step({})
                self.assertEqual(100 - hp.over_armour.amount, expected[definition.name])
                self.assertEqual(hp.over_armour.source, armour_source)
                self.assertEqual(hp.now[combat.ARMOUR_POOL], 50)
                self.assertEqual(hp.now[combat.HEALTH_POOL], 200)
                self.assertIs(fight.halt.pulls.get(bot.entity), pull)
                if not immune:
                    self.assertIsNotNone(pull.instance)
                self.assertEqual(bot.position == initial, immune)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
                if definition.name == "Assassin":
                    self.assertEqual(fight.pins[bot.entity], torb.body)
                npc_control.stop(match)
                self.assertNotIn(bot.entity, fight.pins)

                for _ in range(85):
                    if bot.entity not in fight.halt.pulls:
                        break
                    run.step({})
                self.assertNotIn(bot.entity, fight.halt.pulls)
                before = bot_hp.total
                run.step({torb: (*fixture.body_aim(run, torb, bot), 1)})
                for _ in range(25):
                    if bot_hp.total < before:
                        break
                    run.step({})
                self.assertEqual(before - bot_hp.total, 70)


    def test_halt_source_death_or_switch_retires_orb_but_keeps_acquired_slow(self):
        for acquired in (False, True):
            for death in (False, True):
                with self.subTest(acquired=acquired, death=death):
                    fixture, run, torb, caster = self.arena()
                    match = run.match
                    fight = match.combat
                    hp = fixture.health(run, torb)
                    caster_hp = fixture.health(run, caster)
                    caster_body = caster.body
                    script = caster.body_script
                    orb = self.cast(run, caster, detonate=acquired)
                    pull = wait = None
                    if acquired:
                        for _ in range(100):
                            run.step({})
                            pull = fight.halt.pulls.get(torb.body)
                            wait = pull.instance.states.get(3) if pull is not None else None
                            if wait is not None and wait.active:
                                break
                        self.assertIsNotNone(wait)
                        self.assertTrue(wait.active)
                        self.assertEqual(wait.ms, 650)
                    else:
                        self.assertIsNone(orb.requested)
                        self.assertIs(fight.halt.orbs[orb.entity], orb)
                    start = tuple(torb.mover.position)
                    if death:
                        caster_hp.damage(caster_hp.total - 1, armour_reduction=False)
                        fight._sync_body_health(caster, caster_hp)
                        run.step({torb: (*fixture.body_aim(run, torb, caster), 1)})
                        for _ in range(22):
                            if caster_hp.dead:
                                break
                            run.step({})
                        self.assertTrue(caster_hp.dead)
                        self.assertEqual(caster_hp.killer, torb.body)
                        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 1)
                        self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                        self.assertTrue(script.component.owner_dead)
                        run.step({caster: (0, 0, 2)})
                    else:
                        self.assertTrue(match.switch_hero(caster, heroes()[SOLDIER]))
                        self.assertNotEqual(caster.body, caster_body)
                        self.assertTrue(script.retired)
                    self.assertNotIn(orb.entity, fight.halt.orbs)
                    self.assertEqual(hp.total, 250)

                    if acquired:
                        self.assertIs(fight.halt.pulls[torb.body], pull)
                        self.assertTrue(wait.active)
                        slow = torb.body_script.component.entity_var(23186)
                        self.assertAlmostEqual(slow.value(), .65)
                        for _ in range(50):
                            if not wait.active:
                                break
                            self.forward_step(run, torb)
                        self.assertFalse(wait.active)
                        self.assertEqual(wait.ended_at - wait.start, 650)
                        self.assertFalse(slow.value())
                        self.assertIsNone(pull.instance)
                        for _ in range(30):
                            self.forward_step(run, torb)
                        self.assertAlmostEqual(torb.mover.velocity[2], 5.5, delta=1 / 1024)
                    else:
                        for _ in range(180):
                            run.step({})
                        self.assertEqual(tuple(torb.mover.position), start)
                    self.assertFalse(fight.halt.pulls)
                    self.assertFalse(fight.halt.orbs)
                    self.assertEqual(hp.total, 250)


    def test_halt_recipient_death_or_reselect_releases_old_mover_and_rivet_recovers(self):
        for death in (False, True):
            with self.subTest(death=death):
                fixture, run, torb, caster = self.arena()
                match = run.match
                fight = match.combat
                x, y, z = caster.mover.position
                fixture.place(torb, (x, y, z + 4.5))
                hp = fixture.health(run, torb)
                if death:
                    hp.damage(hp.total - 1, armour_reduction=False)
                    fight._sync_body_health(torb, hp)
                body, script = torb.body, torb.body_script
                start = tuple(torb.mover.position)
                orb = self.cast(run, caster)
                for _ in range(65):
                    run.step({})
                    if math.dist(torb.mover.position, start) > .15:
                        break
                self.assertTrue(orb.pulled)
                pull = fight.halt.pulls[body]
                self.assertIsNotNone(pull.instance.state(2).move_request())
                self.assertGreater(math.dist(torb.mover.position, start), .15)
                if death:
                    run.step({caster: (*fixture.body_aim(run, caster, torb), 1)})
                    for _ in range(10):
                        if hp.dead:
                            break
                        self.assertIsNotNone(pull.instance.state(2).move_request())
                        run.step({})
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, caster.body)
                    self.assertTrue(script.component.owner_dead)
                    self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 1)
                    corpse = tuple(torb.mover.position)
                    self.assertNotIn(body, fight.halt.pulls)
                    self.assertFalse(torb.mover.driven)
                    self.assertEqual(torb.mover.velocity, (0, 0, 0))
                    for _ in range(660):
                        if torb.body != body:
                            break
                        run.step({})
                        if torb.body == body:
                            self.assertEqual(tuple(torb.mover.position), corpse)
                else:
                    self.assertTrue(match.switch_hero(torb, torb.hero))
                self.assertNotEqual(torb.body, body)
                self.assertTrue(script.retired)
                self.assertIsNone(pull.instance)
                self.assertNotIn(body, fight.halt.pulls)
                self.assertNotIn(torb.body, fight.halt.pulls)
                self.assertEqual(torb.mover.velocity, (0, 0, 0))
                self.assertEqual(fixture.health(run, torb).total, 250)

                fixture.place(torb, (x, y, z + 6))
                fixture.advance(run, torb, caster, 35)
                caster_hp = fixture.health(run, caster)
                run.step({torb: (*fixture.body_aim(run, torb, caster), 1)})
                for _ in range(25):
                    if caster_hp.total < 450:
                        break
                    run.step({})
                self.assertEqual(caster_hp.total, 385)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 65)
                self.assertNotIn(torb.body, fight.halt.pulls)


    def test_second_halt_replaces_slow_and_owns_its_own_movement_and_expiry(self):
        fixture, run, torb, first_caster = self.arena()
        match = run.match
        fight = match.combat
        second_caster = run.players[2]
        self.assertTrue(match.switch_hero(second_caster, first_caster.hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        first_caster.team = second_caster.team = 1 - torb.team
        x, y, z = first_caster.mover.position
        fixture.place(second_caster, (x + 2, y, z))
        fixture.advance(run, torb, first_caster, 35)
        torb_hp = fixture.health(run, torb)
        second_hp = fixture.health(run, second_caster)

        first_orb = self.cast(run, first_caster)
        first, first_wait = self.linger(run, torb)
        first_instance = first.instance
        self.assertEqual(first_instance.vars[40].value(), expr.Entity(first_orb.entity))
        self.assertEqual(first_instance.vars[41].value(), expr.Entity(first_caster.entity))
        slow = torb.body_script.component.entity_var(23186)
        self.assertAlmostEqual(slow.value(), .65)
        position = tuple(torb.mover.position)
        first_deadline = first_wait.start + first_wait.ms

        second_orb = self.cast(run, second_caster)
        self.assertTrue(first_wait.active)
        run.step({})
        self.assertTrue(second_orb.pulled)
        second = fight.halt.pulls[torb.body]
        self.assertIsNot(second, first)
        self.assertIsNone(first.instance)
        self.assertFalse(first_wait.active)
        self.assertFalse(slow.value())
        self.assertLess(first_wait.ended_at, first_deadline)
        self.assertEqual(second.instance.vars[40].value(), expr.Entity(second_orb.entity))
        self.assertEqual(second.instance.vars[41].value(), expr.Entity(second_caster.entity))

        attached, second_wait = self.linger(run, torb)
        self.assertIs(attached, second)
        self.assertGreater(torb.mover.position[0], position[0] + .25)
        self.assertGreater(second_wait.start, first_deadline)
        self.assertAlmostEqual(slow.value(), .65)
        self.assertEqual(torb_hp.total, 250)
        fixture.step(run, torb, second_caster, fire=1, defense=1)
        for _ in range(25):
            if torb_hp.total < 250 and second_hp.total < 450:
                break
            run.step({})
        self.assertEqual(torb_hp.total, 244)
        self.assertEqual(second_hp.total, 385)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 65)
        self.assertEqual(second_caster.stats.value(stats.HERO_DAMAGE), 6)
        self.assertTrue(second_wait.active)

        for _ in range(70):
            self.forward_step(run, torb)
        self.assertEqual(second_wait.ended_at - second_wait.start, 650)
        self.assertIsNone(second.instance)
        self.assertFalse(slow.value())
        self.assertNotIn(torb.body, fight.halt.pulls)
        self.assertAlmostEqual(torb.mover.velocity[2], 5.5, delta=1 / 1024)

    def linger(self, run, target):
        halt = run.match.combat.halt
        for _ in range(95):
            run.step({})
            pull = halt.pulls.get(target.body)
            wait = pull.instance.states.get(3) if pull is not None else None
            if wait is not None and wait.active:
                return pull, wait
        self.fail("Halt never reached the authored post-movement wait")


    def test_phase_skills_deny_halt_and_end_active_pull_without_releasing_torbjorn(self):
        for name in ("Reaper", "Moira", "Mei"):
            for during_pull in (False, True):
                with self.subTest(hero=name, during_pull=during_pull):
                    fixture = defenses.TorbjornDefenseTests()
                    run, torb, defender, caster = fixture.arena(name)
                    match = run.match
                    fight = match.combat
                    self.assertTrue(match.switch_hero(caster, heroes()[orisa_halt.HERO]))
                    x, y, z = torb.mover.position
                    fixture.place(caster, (x, y, z))
                    fixture.place(torb, (x + 1.25, y, z + 5))
                    fixture.place(defender, (x - 1.25, y, z + 5))
                    fixture.advance(run, torb, defender, 35)
                    start = tuple(defender.mover.position)
                    torb_start = tuple(torb.mover.position)
                    hp = fixture.health(run, defender)
                    before = hp.total
                    if not during_pull:
                        run.step({defender: (0, 0, 8)})
                        self.assertTrue(phase_defenses.phase(defender)[0])

                    orb = self.cast(run, caster)
                    for _ in range(35):
                        if orb.pulled:
                            break
                        run.step({})
                    self.assertTrue(orb.pulled)
                    torb_pull = fight.halt.pulls[torb.body]
                    if during_pull:
                        pull = fight.halt.pulls[defender.body]
                        for _ in range(40):
                            if math.dist(defender.mover.position, start) > .15:
                                break
                            run.step({})
                        self.assertIsNotNone(pull.instance.state(2).move_request())
                        self.assertGreater(math.dist(defender.mover.position, start), .15)
                        run.step({defender: (0, 0, 8)})
                        self.assertIsNone(pull.instance)
                    else:
                        self.assertEqual(tuple(defender.mover.position), start)
                    self.assertEqual(phase_defenses.phase(defender), (True, name != "Mei"))
                    self.assertNotIn(defender.body, fight.halt.pulls)
                    self.assertIs(fight.halt.pulls[torb.body], torb_pull)
                    dx, _, dz = defender.mover.position

                    run.step({torb: (*fixture.body_aim(run, torb, defender), 1)})
                    self.assertTrue(any(rocket.shooter is torb for rocket in fight.rockets))
                    for _ in range(12):
                        run.step({})
                    self.assertTrue(phase_defenses.phase(defender)[0])
                    self.assertEqual(hp.total, before)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual((defender.mover.position[0], defender.mover.position[2]), (dx, dz))
                    for _ in range(40):
                        if math.dist(torb.mover.position, torb_start) > .15:
                            break
                        run.step({})
                    self.assertGreater(math.dist(torb.mover.position, torb_start), .15)

                    for _ in range(295):
                        if (not phase_defenses.phase(defender)[0] and torb.body not in fight.halt.pulls
                                and not fight.rockets):
                            break
                        run.step({})
                    self.assertFalse(phase_defenses.phase(defender)[0])
                    self.assertFalse(fight.halt.pulls)
                    fixture.advance(run, torb, defender, 35)
                    run.step({torb: (*fixture.body_aim(run, torb, defender), 1)})
                    for _ in range(30):
                        if hp.total < before:
                            break
                        run.step({})
                    self.assertEqual(before - hp.total, 70)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)


    def test_halt_hero_replacement_cannot_bypass_cooldown(self):
        for flying in (False, True):
            for reselect in (False, True):
                with self.subTest(flying=flying, reselect=reselect):
                    fixture, run, torb, caster = self.arena()
                    match = run.match
                    halt = match.combat.halt
                    old_body = caster.body
                    hero = caster.hero
                    script = caster.body_script
                    orb = self.cast(run, caster, detonate=not flying)
                    if flying:
                        self.assertIsNone(orb.requested)
                        expected = 6.0
                    else:
                        for _ in range(50):
                            if orb.entity not in halt.orbs:
                                break
                            run.step({})
                        self.assertNotIn(orb.entity, halt.orbs)
                        source = next(i for i in script.component.instances.values()
                                      if i.graph.index == orisa_halt.CAST)
                        expected = source.state(2).remaining()
                        self.assertGreater(expected, 5.9)

                    away = 0
                    if not reselect:
                        self.assertTrue(match.switch_hero(caster, heroes()[SOLDIER]))
                        self.assertFalse(halt.orbs)
                        away = 40
                        for _ in range(away):
                            run.step({})
                    self.assertTrue(match.switch_hero(caster, hero))
                    self.assertNotEqual(caster.body, old_body)
                    self.assertTrue(script.retired)
                    self.assertFalse(halt.orbs)
                    instance = next(i for i in caster.body_script.component.instances.values()
                                    if i.graph.index == orisa_halt.CAST)
                    ability = instance.state(2)
                    self.assertAlmostEqual(ability.remaining(), expected - .016 * away, places=4)
                    x, y, z = torb.mover.position
                    fixture.place(caster, (x, y, z - 6))
                    for _ in range(5):
                        run.step({caster: (0, 0, 2)})
                        run.step({})
                        self.assertFalse(halt.orbs)
                        self.assertGreater(ability.remaining(), 0)
                    for _ in range(400):
                        if ability.remaining() <= 0:
                            break
                        run.step({})
                    self.assertEqual(ability.remaining(), 0)
                    self.assertFalse(halt.orbs)
                    fresh = self.cast(run, caster)
                    self.assertNotEqual(fresh.entity, orb.entity)
                    self.assertEqual(fresh.body, caster.body)
                    self.assertIsNotNone(fresh.requested)
                    run.step({})
                    self.assertIn(torb.body, halt.pulls)


    def test_graviton_keeps_pull_until_expiry_then_active_halt_takes_over(self):
        fixture, run, torb, orisa = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        caster = run.players[2]
        zarya = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(caster, zarya))
        caster.body_script.ult_seconds = 0
        orisa.team = caster.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(caster, (x, y, z - 5))
        fixture.place(orisa, (x + 10, y, z))
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        fixture.step(run, torb, caster, fire=16, defense=32)
        for _ in range(45):
            if fight.gravitons.fields:
                break
            run.step({})
        field, = fight.gravitons.fields
        self.assertEqual(hp.over_armour.amount, 97.5)
        armour_source = hp.over_armour.source
        while run.frame < field.end_tick - 56:
            run.step({})
        self.assertIn(torb.body, fight.gravitons.pulled)
        fixture.place(orisa, (x, y, field.point[2] - 5))
        orb = self.cast(run, orisa)
        self.assertGreater(orb.position[2], torb.mover.position[2] + 1)
        move = None
        for _ in range(27):
            run.step({})
            pull = fight.halt.pulls.get(torb.body)
            move = pull.instance.state(2).move_request() if pull is not None else None
            if move is not None:
                break
        self.assertIsNotNone(move)
        destination, speed = move
        self.assertEqual(speed, 20)
        self.assertGreater(destination.z, torb.mover.position[2] + 1)
        self.assertGreater(pull.until, field.end_tick)
        self.assertLess(run.frame, field.end_tick)
        self.assertIn(torb.body, fight.gravitons.pulled)
        held_z = torb.mover.position[2]
        held_armor = hp.over_armour.amount
        held_tick = run.frame
        while run.frame < field.end_tick - 1:
            run.step({})
            self.assertIn(torb.body, fight.gravitons.pulled)
            self.assertIs(fight.halt.pulls[torb.body], pull)
        self.assertAlmostEqual(torb.mover.position[2], held_z, delta=.02)
        self.assertAlmostEqual(hp.over_armour.amount, held_armor - .08 * (run.frame - held_tick))
        run.step({})
        self.assertEqual(run.frame, field.end_tick)
        self.assertFalse(fight.gravitons.fields)
        self.assertNotIn(torb.body, fight.gravitons.pulled)
        self.assertIs(fight.halt.pulls[torb.body], pull)
        self.assertGreater(torb.mover.position[2], held_z + .2)
        self.assertAlmostEqual(hp.over_armour.amount, 77.5)
        self.assertEqual(hp.over_armour.source, armour_source)
        while run.frame < pull.until:
            run.step({})
            if hp.over_armour.source is not None:
                self.assertEqual(hp.over_armour.source, armour_source)
                self.assertAlmostEqual(hp.over_armour.amount, 77.5)
        self.assertNotIn(torb.body, fight.halt.pulls)
        self.assertAlmostEqual(torb.mover.position[2], destination.z, delta=.01)
        self.assertIsNone(hp.over_armour.source)
        self.assertEqual(hp.now, base)
        self.assertAlmostEqual(caster.stats.value(stats.HERO_DAMAGE), 22.5)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_halt_crosses_landed_molten_boundary_in_both_directions(self):
        for entering in (True, False):
            with self.subTest(entering=entering):
                fixture, run, torb, orisa = self.arena()
                fight = run.match.combat
                caster = run.players[2]
                run.match.switch_hero(caster, torb.hero)
                x, y, z = torb.mover.position
                fixture.place(caster, (x + 10, y, z))
                caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
                run.step({caster: (0, 4000, 32)})
                for _ in range(45):
                    run.step({})
                run.step({caster: (0, 4000, 1)})
                for _ in range(100):
                    if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                        break
                    run.step({})
                pool, = fight.molten_pools.values()
                self.assertEqual(pool.phase, 1)
                self.assertIs(pool.owner, caster)

                x, y, z = pool.position
                target = next(t for t in fight._targets() if t.owner is torb)
                edge = pool.config["radius"] + target.radius
                start_z = z - edge - 0.1 if entering else z + edge - 0.1
                fixture.place(torb, (x, y, start_z))
                fixture.place(orisa, (x, y, start_z - 7))
                fixture.place(caster, (x + 30, y, z))
                key = caster.entity, torb.body
                hp = fixture.health(run, torb)
                orb = self.cast(run, orisa)
                self.assertEqual(key in fight.molten_burns, not entering)
                self.assertEqual(hp.total == 250, entering)
                self.assertIsNotNone(orb.requested)
                request = None
                for _ in range(55):
                    run.step({})
                    pull = fight.halt.pulls.get(torb.body)
                    request = pull.instance.state(2).move_request() if pull is not None else None
                    if request is not None:
                        break
                self.assertIsNotNone(request, (orb.position, torb.mover.position))
                destination, _ = request
                self.assertGreater(destination.z, start_z + 0.1, (orb.position, destination))
                before = hp.total
                for _ in range(16):
                    run.step({})
                self.assertGreater(torb.mover.position[2], start_z + 0.1)
                self.assertEqual(key in fight.molten_burns, entering)
                if entering:
                    self.assertLess(hp.total, before)
                after = hp.total
                for _ in range(8):
                    run.step({})
                if entering:
                    self.assertLess(hp.total, after)
                else:
                    self.assertEqual(hp.total, after)
                self.assertFalse(hp.dead)

    def test_halt_allows_molten_activation_and_an_active_cast_to_fire(self):
        for already_active in (False, True):
            with self.subTest(already_active=already_active):
                fixture, run, torb, orisa = self.arena()
                fight = run.match.combat
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(10000)
                charge = torb.body_script.component.entity_var(342)
                if already_active:
                    run.step({torb: (0, 4000, 32)})
                    self.assertEqual(charge.value(), 0)
                    fixture.advance(run, torb, orisa, 45)
                orb = self.cast(run, orisa)
                for _ in range(40):
                    run.step({})
                    pull = fight.halt.pulls.get(torb.body)
                    if pull is not None and pull.instance.state(2).move_request() is not None:
                        break
                self.assertTrue(orb.pulled)
                pull = fight.halt.pulls[torb.body]
                self.assertIsNotNone(pull.instance.state(2).move_request())
                start = tuple(torb.mover.position)
                if not already_active:
                    self.assertEqual(charge.value(), 10000)
                    run.step({torb: (16384, 4000, 32)})
                    self.assertEqual(charge.value(), 0)
                    self.assertIs(fight.halt.pulls[torb.body], pull)
                    self.assertGreater(torb.mover.position[2], start[2])
                    fixture.advance(run, torb, orisa, 45)
                    self.assertFalse(fight.rockets or fight.molten_pools)

                run.step({torb: (16384, 4000, 1)})
                globule, = fight.rockets
                self.assertEqual(globule.spawn, molten_core.POOL)
                self.assertIs(globule.shooter, torb)
                if already_active:
                    self.assertIs(fight.halt.pulls[torb.body], pull)
                    self.assertIsNotNone(pull.instance.state(2).move_request())
                    self.assertGreater(torb.mover.position[2], start[2])
                for _ in range(100):
                    run.step({})
                    if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                        break
                pool, = fight.molten_pools.values()
                self.assertEqual(pool.phase, 1)
                self.assertIs(pool.owner, torb)
                self.assertEqual(fixture.health(run, torb).total, 250)
                fixture.place(orisa, pool.position)
                enemy_hp = fixture.health(run, orisa)
                before = enemy_hp.total
                run.step({})
                self.assertAlmostEqual(before - enemy_hp.total, 4)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 4)
                self.assertIn((torb.entity, orisa.body), fight.molten_burns)
                self.assertEqual(charge.value(), 0)
                for _ in range(65):
                    if torb.body not in fight.halt.pulls:
                        break
                    run.step({})
                self.assertNotIn(torb.body, fight.halt.pulls)
                self.assertIn((torb.entity, orisa.body), fight.molten_burns)
                self.assertEqual(list(fight.molten_pools.values()), [pool])

    def test_turret_input_during_halt_launches_from_the_displaced_pose(self):
        fixture, run, torb, orisa = self.arena()
        fight = run.match.combat
        orb = self.cast(run, orisa)
        for _ in range(40):
            run.step({})
            pull = fight.halt.pulls.get(torb.body)
            if pull is not None and pull.instance.state(2).move_request() is not None:
                break
        self.assertTrue(orb.pulled)
        pull = fight.halt.pulls[torb.body]
        self.assertIsNotNone(pull.instance.state(2).move_request())
        before = tuple(torb.mover.position)
        direction = (16384, 4000)
        run.step({torb: (*direction, 8)})
        self.assertGreater(torb.mover.position[2], before[2])
        self.assertIs(fight.halt.pulls[torb.body], pull)
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 0)

        for _ in range(30):
            if fight.rockets:
                break
            run.step({torb: (*direction, 0)})
        throw, = fight.rockets
        self.assertEqual(throw.spawn, hero_torbjorn.TURRET)
        self.assertIs(throw.shooter, torb)
        self.assertEqual(throw.position, fight._eye(torb))
        self.assertGreater(throw.position[2], before[2])
        self.assertIs(fight.halt.pulls[torb.body], pull)
        self.assertFalse(fight.turrets)
        launch = throw.position

        run.step({torb: (*direction, 8)})
        for _ in range(100):
            if fight.turrets:
                break
            self.assertEqual(fight.rockets, [throw])
            run.step({})
        turret, = fight.turrets.values()
        self.assertIs(turret.owner, torb)
        self.assertGreater(turret.actor.position[0], launch[0])
        self.assertAlmostEqual(turret.actor.position[1], before[1])
        self.assertAlmostEqual(turret.actor.position[2], launch[2], delta=.01)
        self.assertFalse(fight.rockets)
        enemy_hp = fixture.health(run, orisa)
        health_before = enemy_hp.total
        for _ in range(240):
            if turret.shots:
                break
            run.step({})
        self.assertNotIn(torb.body, fight.halt.pulls)
        self.assertEqual(list(fight.turrets.values()), [turret])
        self.assertEqual(turret.target, orisa.body)
        self.assertEqual(turret.shots, 1)
        self.assertEqual(enemy_hp.total, health_before - 9)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 9)
        self.assertEqual(fixture.health(run, torb).total, 250)

    def test_deployed_turret_keeps_position_and_fires_during_owner_halt(self):
        fixture, run, torb, orisa = self.arena()
        fight = run.match.combat
        position = tuple(orisa.mover.position)
        x, y, z = torb.mover.position
        fixture.place(orisa, (x + 100, y, z + 100))
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, orisa, 225)
        turret = fight.turrets[torb.entity]
        turret_position = tuple(turret.actor.position)
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(turret.shots, 0)

        fixture.place(orisa, position)
        initial_z = torb.mover.position[2]
        self.cast(run, orisa)
        for _ in range(45):
            run.step({})
            if torb.body in fight.halt.pulls:
                break
        pull = fight.halt.pulls[torb.body]
        hp = fixture.health(run, orisa)
        before = hp.total
        shots = turret.shots
        while fight.tick < pull.until - 1:
            run.step({})
            self.assertIs(fight.turrets[torb.entity], turret)
            self.assertEqual(tuple(turret.actor.position), turret_position)
        self.assertGreater(torb.mover.position[2], initial_z + .15)
        self.assertGreater(turret.shots, shots)
        self.assertLess(hp.total, before)
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(fixture.health(run, torb).total, 250)
        run.step({})
        self.assertNotIn(torb.body, fight.halt.pulls)
        self.assertIs(fight.turrets[torb.entity], turret)

    def test_halt_pulls_hammer_out_of_repair_range_and_return_restores_contact(self):
        fixture, run, torb, orisa = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        distant = (x + 100, y, z + 100)
        fixture.place(orisa, distant)
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, orisa, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        x, y, z = turret.actor.position
        fixture.place(orisa, (x, y, z + 6))
        weapon_aim = aim(fight._eye(orisa), (x, y + .6, z))
        for _ in range(65):
            run.step({orisa: (*weapon_aim, 1)})
            if hp.total <= 195:
                break
        self.assertLessEqual(hp.total, 195)
        before = hp.total
        fixture.place(orisa, distant)
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, orisa, 45)

        hammer = next(i for i in torb.body_script.component.instances.values() if i.graph.index == 0x168C)
        track = hammer.graph.node(33)
        offset = hammer.evaluate(track.field("m_05BE62DD"))
        reach = hammer.evaluate(track.field("m_radius"))
        target = next(t for t in fight._targets() if t.owner is turret.actor)
        lower, upper = target.segment()
        height = max(lower[1] - y - offset.y, y + offset.y - upper[1], 0)
        edge = math.sqrt((reach + target.radius) ** 2 - height ** 2)
        start = (x - offset.x, y, z - offset.z + edge - .1)
        fixture.place(torb, start)
        fixture.place(orisa, (start[0], y, start[2] - 7))
        self.cast(run, orisa)
        for _ in range(85):
            run.step({})
        self.assertGreater(torb.mover.position[2], start[2] + .1)
        self.assertEqual(hp.total, before)
        fixture.place(orisa, distant)

        for expected in (before, before + 50):
            direction = aim(fight._eye(torb), (x, y + .4, z))
            run.step({torb: (*direction, 1)})
            for _ in range(55):
                run.step({torb: (*direction, 0)})
            self.assertEqual(hp.total, expected)
            if expected == before:
                fixture.place(torb, start)

    def test_wall_team_and_range_reject_pull(self):
        for obstacle in ("wall", "team", "range"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, orisa = self.arena()
                x, y, z = torb.mover.position
                if obstacle == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    torb.team = orisa.team
                elif obstacle == "range":
                    fixture.place(torb, (x + 20, y, z))
                else:
                    fixture.place(torb, (x + 2.5, y, z))
                    run.match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x + 1.5, z - 4, x + 1.5, z + 10, y0=y, y1=y + 4)
                    )
                initial = tuple(torb.mover.position)
                orb = self.cast(run, orisa)
                self.assertIsNotNone(orb.requested)
                for _ in range(60):
                    run.step({})
                self.assertFalse(run.match.combat.halt.pulls)
                self.assertEqual(tuple(torb.mover.position), initial)
                self.assertEqual(fixture.health(run, torb).total, 250)

    def test_world_clock_retires_unposed_pull_without_advancing_body_vm(self):
        _, run, torb, orisa = self.arena()
        self.cast(run, orisa)
        run.step({})
        fight = run.match.combat
        pull = fight.halt.pulls[torb.body]
        instance = pull.instance
        frame = torb.body_script.frame
        run.frame = pull.until
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(torb.body, fight.halt.pulls)
        self.assertTrue(instance.stopped)
        self.assertEqual(torb.body_script.frame, frame)

    def test_deflect_reassigns_halt_and_only_automatic_impact_pulls_genjis_enemy(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, genji, orisa = fixture.arena("Genji", 5)
                run.match.switch_hero(orisa, heroes()[orisa_halt.HERO])
                x, y, z = torb.mover.position
                fixture.place(orisa, (x, y, z))
                fixture.place(genji, (x, y, z + 5))
                fixture.place(torb, (x + 1.5, y, z - 1))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                orisa.team, genji.team = 0, 1
                torb.team = genji.team if allied else orisa.team
                impact_z = z - (6.17 if allied else 6)
                run.match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall(x - 4, impact_z, x + 4, impact_z, y0=y, y1=y + 4)
                )
                for _ in range(35):
                    run.step({genji: (-32768, 0, 0)})
                before = tuple(torb.mover.position)
                fight = run.match.combat
                original = None
                for frame in range(26):
                    run.step({genji: (-32768, 0, 16 if frame == 0 else 0),
                              orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                    if original is None and fight.halt.orbs:
                        original, = fight.halt.orbs
                orb, = fight.halt.orbs.values()
                self.assertIsNotNone(original)
                self.assertNotIn(original, fight.halt.orbs)
                self.assertIs(orb.owner, genji)
                self.assertEqual((orb.body, orb.team), (genji.body, genji.team))
                self.assertIsNone(orb.requested)
                self.assertLess(orb.direction[2], 0)
                for _ in range(100):
                    run.step({})
                    if orb.pulled:
                        break
                self.assertTrue(orb.pulled, (orb.position, orb.direction, orb.remaining))
                self.assertAlmostEqual(orb.position[2], impact_z, delta=.002)
                self.assertEqual(torb.body in fight.halt.pulls, not allied)
                self.assertNotIn(genji.body, fight.halt.pulls)
                for _ in range(55):
                    run.step({})
                if allied:
                    self.assertEqual(tuple(torb.mover.position), before)
                else:
                    self.assertLess(torb.mover.position[2], before[2] - 1)
                self.assertEqual(fixture.health(run, torb).total, 250)
