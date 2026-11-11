"""Command-driven Winston barrier against Torbjorn's combat actions."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import hero_torbjorn, npc, npc_control, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE")
                     and os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "needs prepared hero and projectile data")
class TorbjornWinstonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self, distance=8):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, winston, other = fixture.arena("Winston", distance)
        return fixture, run, torb, winston, other

    def cast(self, fixture, run, torb, winston):
        run.step({winston: (0, 0, 16)})
        fixture.advance(run, torb, winston, 12)
        actors = run.match.combat.world_bubbles.actors
        self.assertEqual(len(actors), 1)
        actor = next(iter(actors.values()))
        self.assertEqual(actor.health, 700)
        return actor

    def test_transcendence_healing_respects_winston_shell_team_and_break(self):
        from itertools import product

        for allied, torb_inside in product((False, True), repeat=2):
            with self.subTest(allied=allied, torb_inside=torb_inside):
                self.exercise_transcendence_across_shell(allied=allied, torb_inside=torb_inside)

    def exercise_transcendence_across_shell(self, *, allied, torb_inside):
        from ow174.game import phase_defenses

        fixture, run, torb, winston, zen = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zen.team = torb.team
        winston.team = torb.team if allied else 1 - torb.team
        self.assertTrue(match.switch_hero(zen, heroes()[phase_defenses.ZENYATTA]))
        x, y, z = torb.mover.position
        fixture.place(winston, (x, y, z))
        fixture.place(torb, (x, y, z + 8))
        fixture.place(zen, (x + 20, y, z))
        fixture.advance(run, torb, winston, 35)
        shell = self.cast(fixture, run, torb, winston)
        sx, sy, sz = shell.point
        inside, outside = (sx, sy, sz), (sx, sy, sz + 8)
        fixture.place(torb, inside if torb_inside else outside)
        fixture.place(zen, outside if torb_inside else inside)
        fixture.place(winston, (sx + 40, sy, sz))
        hp = fixture.health(run, torb)
        hp.damage(60, armour_reduction=False)
        fight._sync_body_health(torb, hp)

        component = zen.body_script.component
        zen.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({zen: (0, 0, 32)})
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
        run.step({})
        self.assertEqual(shell.health, 700)
        if allied:
            self.assertAlmostEqual(hp.total, 194.8)
            self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)
            return
        self.assertEqual(hp.total, 190)
        self.assertEqual(zen.stats.value(stats.HEALING), 0)
        fight.world_bubbles.damage(shell, 699)
        run.step({torb: (*fixture.body_aim(run, torb, zen), 1)})
        for _ in range(16):
            if shell.entity not in fight.world_bubbles.actors:
                break
            self.assertEqual(hp.total, 190)
            run.step({})
        self.assertNotIn(shell.entity, fight.world_bubbles.actors)
        self.assertEqual(shell.health, 0)
        self.assertAlmostEqual(hp.total, 194.8)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_create_host_and_primary_secondary(self):
        fixture, run, torb, winston, _ = self.arena()
        actor = self.cast(fixture, run, torb, winston)
        source = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFA)
        self.assertEqual(source.vars[38].value(), expr.Entity(actor.entity))
        self.assertNotIn(38, dict(winston.body_script.replicated_vars(source)))
        hosted = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
        self.assertEqual(expr.to_float(hosted.vars[587].value()), 700)
        self.assertEqual(expr.to_float(hosted.vars[567].value()), 9)
        self.assertTrue(hosted.states[0].active and hosted.states[11].active)
        hp = fixture.health(run, winston)
        before = hp.total
        for button, remaining in ((1, 630), (2, 505)):
            fixture.step(run, torb, winston, fire=button)
            fixture.advance(run, torb, winston, 40)
            self.assertEqual(actor.health, remaining)
            self.assertEqual(hp.total, before)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_npc_ranged_fire_breaks_allied_shell_but_assassin_pin_crosses_it(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        ranged = {"Trooper": (20, 15), "Sniper": (100, 95), "Heavy Assault": (5, 2.5), "Enforcer": (50, 25)}
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                fixture, run, torb, winston, other = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                winston.team = torb.team
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                assassin = definition.name == "Assassin"
                fixture.place(winston, (x, y, z - (4.2 if assassin else 2)))
                shell = self.cast(fixture, run, torb, winston)
                distance = 2 if assassin else 5
                self.assertLess(abs(z - shell.point[2]), shell.radius)
                self.assertGreater(distance + z - shell.point[2], shell.radius)
                bot = npc.spawn(match, definition, (x, y, z + distance), team=1 - torb.team)
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                target = next(target for target in fight._targets() if target.owner is bot)
                direction = aim(fight._eye(torb), (x, y + target.height * .5, z + distance))
                while run.frame < bot.records_from:
                    run.step({})
                run.step({torb: (*direction, 16 if assassin else 0)})
                hp = fixture.health(run, torb)
                initial_health = 350 if assassin else 250
                self.assertEqual(hp.total, initial_health)
                base = dict(hp.now)
                self.assertEqual(npc_control.start(match, torb), bot.entity)
                for _ in range(170):
                    run.step({})
                    if hp.total < initial_health or shell.health < 700:
                        break
                if assassin:
                    self.assertEqual(shell.health, 700)
                    self.assertEqual(fight.pins.get(bot.entity), torb.body)
                    self.assertEqual(hp.over_armour.amount, 80)
                    npc_control.stop(match)
                    self.assertFalse(fight.is_stunned(torb))
                    fixture.advance(run, torb, winston, 20)
                    self.assertEqual(hp.over_armour.amount, 80)
                    self.assertEqual(shell.health, 700)
                else:
                    shield_damage, body_damage = ranged[definition.name]
                    self.assertEqual(shell.health, 700 - shield_damage)
                    self.assertEqual(hp.total, 250)
                    run.step({torb: (*direction, 16)})
                    self.assertEqual(hp.total, 350)
                    fight.world_bubbles.damage(shell, shell.health - 1)
                    for _ in range(150):
                        run.step({})
                        if shell.entity not in fight.world_bubbles.actors:
                            break
                    self.assertEqual(shell.health, 0)
                    self.assertNotIn(shell.entity, fight.world_bubbles.actors)
                    self.assertEqual(hp.over_armour.amount, 100)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                        for _ in range(150):
                            run.step({})
                            if hp.total < 350:
                                break
                    if definition.name == "Enforcer":
                        self.assertFalse(any(call.args[0] is bot for call in shots.call_args_list))
                        body_damage -= 1  # the first pellet was consumed at the shell
                    self.assertEqual(hp.over_armour.amount, 100 - body_damage)
                self.assertEqual(hp.now, base)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_talon_fire_crosses_allied_shell_then_projected_barrier_then_overload(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        ranged = {"Trooper": (20, 15), "Sniper": (100, 95), "Heavy Assault": (5, 2.5), "Enforcer": (50, 23)}
        for definition in catalog.values():
            if definition.name in ranged:
                with self.subTest(npc=definition.name):
                    self.check_layered_talon_fire(definition, *ranged[definition.name])

    def check_layered_talon_fire(self, definition, nominal, body_damage):
        fixture, run, torb, winston, zarya = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        winston.team = zarya.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(winston, (x, y, z - 2))
        fixture.place(zarya, (x - 5, y, z))
        shell = self.cast(fixture, run, torb, winston)
        bot = npc.spawn(match, definition, (x, y, z + 5), team=1 - torb.team)
        for viewer in run.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        direction = fixture.body_aim(run, torb, bot)
        run.step({torb: (*direction, 0)})
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        for _ in range(170):
            run.step({})
            if shell.health <= 700 - nominal:
                break
        self.assertEqual(shell.health, 700 - nominal)
        self.assertEqual(hp.total, 250)

        def protect():
            run.step({torb: (*direction, 16), zarya: (*fixture.body_aim(run, zarya, torb), 16)})
            self.assertEqual(hp.total, 350)
            bubble = fight.bubbles.for_target(torb)
            self.assertIsNotNone(bubble)
            self.assertIs(bubble.caster, zarya)
            self.assertEqual(bubble.health, 200)
            bubble.health = 1
            return bubble, hp.over_armour.source

        # The Sniper cadence exceeds the projected barrier lifetime across two shots.
        if definition.name != "Sniper":
            bubble, source = protect()
        fight.world_bubbles.damage(shell, shell.health - 1)
        for _ in range(150):
            run.step({})
            if not shell.health:
                break
        self.assertEqual(shell.health, 0)
        self.assertNotIn(shell.entity, fight.world_bubbles.actors)
        if definition.name == "Sniper":
            self.assertEqual(hp.total, 250)
            # Let the next windup advance before applying the two shorter effects.
            fixture.advance(run, torb, winston, 55)
            bubble, source = protect()
        else:
            self.assertEqual(bubble.health, 1)
            self.assertEqual(hp.over_armour.amount, 100)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            for _ in range(150):
                run.step({})
                if not bubble.health:
                    break
            self.assertEqual(bubble.health, 0)
            self.assertIsNone(fight.bubbles.for_target(torb))
            self.assertEqual(hp.over_armour.amount, 100)
            for _ in range(150):
                run.step({})
                if hp.total < 350:
                    break
        if definition.name == "Enforcer":
            self.assertFalse(any(call.args[0] is bot for call in shots.call_args_list))
        self.assertEqual(hp.over_armour.amount, 100 - body_damage)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)

    def test_particle_beam_breaks_winston_shell_and_retargets_overload_without_release(self):
        fixture, run, torb, winston, zarya = self.arena()
        match = run.match
        fight = match.combat
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        winston.team = torb.team
        zarya.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(winston, (x, y, z - 2))
        fixture.place(zarya, (x, y, z + 5))
        shell = self.cast(fixture, run, torb, winston)
        self.assertLess(abs(z - shell.point[2]), shell.radius)
        self.assertGreater(z + 5 - shell.point[2], shell.radius)
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        winston_hp = fixture.health(run, winston)
        winston_before = winston_hp.total
        run.step({torb: (0, 0, 16)})
        source = hp.over_armour.source
        direction = fixture.body_aim(run, zarya, torb)
        held = {zarya: (*direction, 1)}

        for _ in range(8):
            run.step(held)
        self.assertAlmostEqual(700 - shell.health, 9.525)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0)
        fight.world_bubbles.damage(shell, shell.health - 1)
        run.step(held)
        self.assertEqual(shell.health, 0)
        self.assertNotIn(shell.entity, fight.world_bubbles.actors)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0)

        run.step(held)
        self.assertAlmostEqual(hp.over_armour.amount, 99.2125)
        for _ in range(7):
            run.step(held)
        self.assertAlmostEqual(hp.over_armour.amount, 93.3325)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 6.6675)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        self.assertEqual(winston_hp.total, winston_before)

    def test_inside_primary_quick_melee_and_hammer_reach_body(self):
        fixture, run, torb, winston, _ = self.arena(distance=1.8)
        actor = self.cast(fixture, run, torb, winston)
        hp = fixture.health(run, winston)
        before = hp.total
        fixture.step(run, torb, winston, fire=1)
        fixture.advance(run, torb, winston, 40)
        self.assertEqual(hp.total, before - 65)
        before = hp.total
        fixture.step(run, torb, winston, fire=0x800)
        fixture.advance(run, torb, winston, 40)
        self.assertEqual(hp.total, before - 25)
        before = hp.total
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, winston, 45)
        fixture.step(run, torb, winston, fire=1)
        fixture.advance(run, torb, winston, 45)
        self.assertEqual(hp.total, before - 50)
        self.assertEqual(actor.health, 700)

    def test_quick_melee_and_hammer_reach_body_across_shell(self):
        for hammer, button, damage in ((False, 0x800, 25), (True, 1, 50)):
            with self.subTest(hammer=hammer):
                fixture, run, torb, winston, _ = self.arena()
                actor = self.cast(fixture, run, torb, winston)
                x, _, z = actor.point
                y = winston.mover.position[1]
                fixture.place(torb, (x, y, z - actor.radius - .5))
                fixture.place(winston, (x, y, z - actor.radius + 1))
                fixture.advance(run, torb, winston, 10)
                if hammer:
                    run.frame += 1
                    torb.take_commands([Command(run.frame, action=2)])
                    fixture.advance(run, torb, winston, 45)
                hp = fixture.health(run, winston)
                before = hp.total
                match = run.match
                clear = match.collision_world
                wall_z = z - actor.radius + .15
                match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + wall(x - 4, wall_z, x + 4, wall_z, y0=y, y1=y + 5)
                )
                fixture.step(run, torb, winston, fire=button)
                fixture.advance(run, torb, winston, 65)
                self.assertEqual(hp.total, before)
                self.assertEqual(actor.health, 700)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
                match.collision_world = clear
                fixture.step(run, torb, winston, fire=button)
                fixture.advance(run, torb, winston, 40)
                self.assertEqual(before - hp.total, damage)
                self.assertEqual(actor.health, 700)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), damage)

    def test_winston_melee_exits_shell_and_spends_overload_after_wall_removal(self):
        fixture, run, torb, winston, _ = self.arena()
        match = run.match
        actor = self.cast(fixture, run, torb, winston)
        x, _, z = actor.point
        y = winston.mover.position[1]
        fixture.place(torb, (x, y, z - actor.radius - .5))
        fixture.place(winston, (x, y, z - actor.radius + 1))
        fixture.advance(run, torb, winston, 10)
        fixture.step(run, torb, winston, fire=16)
        hp = fixture.health(run, torb)
        self.assertEqual(hp.over_armour.amount, 100)
        base = dict(hp.now)
        clear = match.collision_world
        wall_z = z - actor.radius + .15
        match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - 4, wall_z, x + 4, wall_z, y0=y, y1=y + 5)
        )
        fixture.step(run, torb, winston, defense=0x800)
        fixture.advance(run, torb, winston, 65)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(winston.stats.value(stats.HERO_DAMAGE), 0)
        match.collision_world = clear
        fixture.step(run, torb, winston, defense=0x800)
        fixture.advance(run, torb, winston, 35)
        self.assertEqual(hp.over_armour.amount, 75)
        self.assertEqual(hp.now, base)
        self.assertEqual(winston.stats.value(stats.HERO_DAMAGE), 25)
        self.assertEqual(actor.health, 700)

    def test_friendly_torbjorn_shot_exits_shell_and_hits_enemy(self):
        fixture, run, torb, winston, enemy = self.arena(distance=2)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        winston.team = torb.team
        enemy.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(enemy, (x, y, z + 9))
        actor = self.cast(fixture, run, torb, winston)
        hp = fixture.health(run, enemy)
        before = hp.total
        fixture.step(run, torb, enemy, fire=1)
        fixture.advance(run, torb, enemy, 30)
        self.assertEqual(hp.total, before - 70)
        self.assertEqual(actor.health, 700)

    def test_tesla_primary_tracks_overload_through_own_shell_but_stops_at_world_cover(self):
        fixture, run, torb, winston, ally = self.arena(distance=4)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        torb.team = 1 - winston.team
        ally.team = winston.team
        actor = self.cast(fixture, run, torb, winston)
        x, y, z = winston.mover.position
        fixture.place(torb, (x, y, z - 13))
        fixture.place(winston, (x, y, z - 7))
        fixture.place(ally, (x + 1, y, z - 9))
        fixture.advance(run, torb, winston, 4)
        friendly_hp = fixture.health(run, ally)
        friendly_before = friendly_hp.total
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        fixture.step(run, torb, winston, fire=16)
        self.assertEqual(hp.over_armour.amount, 100)
        instance = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFE)
        clear = match.collision_world
        match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - 4, z - 10, x + 4, z - 10, y0=y, y1=y + 5)
        )
        fixture.advance(run, torb, winston, 25, defense=1)
        self.assertTrue(instance.states[13].active)
        self.assertEqual(instance.vars[468].value(), ())
        self.assertEqual(hp.over_armour.amount, 100)
        match.collision_world = clear
        fixture.advance(run, torb, winston, 25, defense=1)
        self.assertEqual(instance.vars[468].value(), (expr.Entity(torb.body),))
        self.assertAlmostEqual(hp.over_armour.amount, 83.2, places=5)
        self.assertEqual(hp.now, base)
        self.assertAlmostEqual(winston.stats.value(stats.HERO_DAMAGE), 16.8, places=4)
        fixture.advance(run, torb, winston, 8)
        self.assertFalse(instance.states[13].active)
        self.assertAlmostEqual(hp.over_armour.amount, 83.2, places=5)
        fixture.place(torb, (x, y, z - 17))
        fixture.advance(run, torb, winston, 8, defense=1)
        self.assertTrue(instance.states[13].active)
        self.assertEqual(instance.vars[468].value(), ())
        self.assertAlmostEqual(hp.over_armour.amount, 83.2, places=5)
        self.assertEqual(friendly_hp.total, friendly_before)
        self.assertEqual(actor.health, 700)

    def test_projected_barrier_absorbs_tesla_break_frame_then_overload_takes_damage(self):
        fixture, run, torb, winston, zarya = self.arena(distance=4)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya.team = torb.team = 1 - winston.team
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        x, y, z = torb.mover.position
        fixture.place(zarya, (x - 5, y, z))
        fixture.advance(run, torb, winston, 20)
        fixture.step(run, torb, winston, fire=16)
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        self.assertEqual(hp.over_armour.amount, 100)
        run.step({zarya: (*fixture.body_aim(run, zarya, torb), 16)})
        bubbles = match.combat.bubbles
        bubble = bubbles.for_target(torb)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        fixture.advance(run, torb, winston, 25, defense=1)
        self.assertAlmostEqual(bubble.health, 176)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertGreater(zarya.body_script.component.entity_var(87).value(), 0)
        bubble.health = .5  # isolate the break frame before natural expiry
        fixture.step(run, torb, winston, defense=1)
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(bubbles.for_target(torb))
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(winston.stats.value(stats.HERO_DAMAGE), 0)
        fixture.step(run, torb, winston, defense=1)
        self.assertAlmostEqual(hp.over_armour.amount, 99.328)
        self.assertEqual(hp.now, base)
        self.assertAlmostEqual(winston.stats.value(stats.HERO_DAMAGE), .672, places=5)

    def test_wall_stops_rivet_before_shell_and_body(self):
        fixture, run, torb, winston, _ = self.arena()
        actor = self.cast(fixture, run, torb, winston)
        x, y, z = torb.mover.position
        run.match.collision_world = World(
            box(x-20, x+20, y-1, y, z-20, z+20)
            + wall(x-10, z+1.5, x+10, z+1.5, y0=y, y1=y+6)
        )
        hp = fixture.health(run, winston)
        before = hp.total
        fixture.step(run, torb, winston, fire=1)
        fixture.advance(run, torb, winston, 30)
        self.assertEqual(hp.total, before)
        self.assertEqual(actor.health, 700)

    def test_actual_tesla_destroys_full_health_thrown_turret_and_starts_cooldown(self):
        fixture, run, torb, winston, other = self.arena(distance=30)
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        fixture.place(winston, (x + 100, y, z))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, winston, 240)
        turret, = fight.turrets.values()
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        x, y, z = turret.actor.position
        floor = torb.mover.position[1]
        fixture.place(torb, (x + 25, floor, z))
        fixture.place(winston, (x, floor, z + 6))
        winston_hp = fixture.health(run, winston)
        before = winston_hp.total
        clear = run.match.collision_world
        run.match.collision_world = World(
            box(x - 32, x + 32, floor - 1, floor, z - 32, z + 32)
            + wall(x - 3, z + 3, x + 3, z + 3, y0=floor, y1=floor + 5)
        )
        for _ in range(12):
            yaw, pitch = aim(fight._eye(winston), (x, y + .5, z))
            run.step({winston: (yaw, pitch, 1)})
        self.assertEqual(hp.total, 250)
        self.assertEqual(winston.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(winston_hp.total, before)
        self.assertEqual(turret.shots, 0)
        run.match.collision_world = clear
        for _ in range(290):
            yaw, pitch = aim(fight._eye(winston), (x, y + .5, z))
            run.step({winston: (yaw, pitch, 1)})
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, winston.body)
        self.assertTrue(turret.actor.dead)
        self.assertAlmostEqual(winston.stats.value(stats.HERO_DAMAGE), 250, places=3)
        self.assertEqual(winston.stats.value(stats.ELIMINATIONS), 1)
        self.assertGreater(turret.shots, 0)
        self.assertLess(winston_hp.total, before)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), before - winston_hp.total)
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9.9)
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, winston, 20)
        self.assertNotIn(torb.entity, fight.turrets)
        for _ in range(630):
            if hero_torbjorn.turret_cooldown_remaining(torb) <= 0:
                break
            run.step({})
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, winston, 100)
        self.assertIsNot(fight.turrets[torb.entity], turret)

    def test_break_consumes_rivet_and_only_later_shot_reaches_body(self):
        fixture, run, torb, winston, _ = self.arena()
        actor = self.cast(fixture, run, torb, winston)
        hp = fixture.health(run, winston)
        before = hp.total
        for shot in range(10):
            fixture.step(run, torb, winston, fire=1)
            fixture.advance(run, torb, winston, 38)
            self.assertEqual(actor.health, 700 - (shot + 1) * 70)
            self.assertEqual(hp.total, before)
        self.assertNotIn(actor.entity, run.match.combat.world_bubbles.actors)
        fixture.step(run, torb, winston, fire=1)
        fixture.advance(run, torb, winston, 30)
        self.assertEqual(hp.total, before - 65)

    def test_tesla_held_retargets_body_and_turret_independently(self):
        fixture, run, torb, winston, other = self.arena(distance=30)
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        fixture.place(winston, (x + 100, y, z))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, winston, 240)
        turret, = fight.turrets.values()
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertEqual(turret_hp.total, 250)
        x, y, z = turret.actor.position
        floor = torb.mover.position[1]
        fixture.place(winston, (x, floor, z + 6))
        fixture.place(torb, (x + 2, floor, z))
        run.step({torb: (0, 0, 16)})
        torb_hp = fixture.health(run, torb)
        self.assertEqual(torb_hp.over_armour.amount, 100)
        yaw, pitch = aim(fight._eye(winston), (x, y + .5, z))
        instance = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFE)
        for _ in range(10):
            run.step({winston: (yaw, pitch, 1)})
        turret_entity = expr.Entity(turret.actor.entity)
        self.assertEqual(set(instance.vars[468].value()), {expr.Entity(torb.body), turret_entity})
        self.assertAlmostEqual(torb_hp.over_armour.amount, 93.28)
        self.assertAlmostEqual(turret_hp.total, 240.4)
        activation = instance.states[13].counter
        fixture.place(torb, (x + 2, floor, z - 4))
        for _ in range(10):
            run.step({winston: (yaw, pitch, 1)})
        self.assertEqual(instance.vars[468].value(), (turret_entity,))
        self.assertAlmostEqual(torb_hp.over_armour.amount, 93.28)
        self.assertAlmostEqual(turret_hp.total, 230.8)
        away = yaw - 32768 if yaw >= 0 else yaw + 32768
        run.step({winston: (away, pitch, 1)})
        self.assertEqual(instance.vars[468].value(), ())
        self.assertAlmostEqual(turret_hp.total, 230.8)
        fixture.place(torb, (x + 2, floor, z))
        run.step({winston: (yaw, pitch, 1)})
        self.assertEqual(instance.states[13].counter, activation)
        self.assertAlmostEqual(torb_hp.over_armour.amount, 92.608)
        self.assertAlmostEqual(turret_hp.total, 229.84)
        self.assertAlmostEqual(winston.stats.value(stats.HERO_DAMAGE), 27.552, places=4)

    def test_expiry_rejects_early_recast_and_recovers(self):
        fixture, run, torb, winston, _ = self.arena()
        actor = self.cast(fixture, run, torb, winston)
        vm = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFA)
        hosted = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
        timer = hosted.states[11]
        centre = actor.point
        x, y, z = winston.mover.position
        fixture.place(winston, (x + 12, y, z))
        fixture.advance(run, torb, winston, 4)
        self.assertEqual(actor.point, centre)
        fixture.advance(run, torb, winston, 560)
        self.assertTrue(timer.reached)
        actors = run.match.combat.world_bubbles.actors
        self.assertFalse(actors)
        self.assertGreater(vm.vars[61].value(), 0)
        run.step({winston: (0, 0, 16)})
        fixture.advance(run, torb, winston, 15)
        self.assertFalse(actors)
        for _ in range(400):
            if vm.vars[61].value() <= 0:
                break
            run.step({})
        self.assertLessEqual(vm.vars[61].value(), 0)
        new = self.cast(fixture, run, torb, winston)
        self.assertNotEqual(new.entity, actor.entity)
        run.match.switch_hero(winston, heroes()[0x02E0000000000042])
        self.assertFalse(actors)

    def test_tesla_empty_magazine_and_refill_gate_damage_while_primary_is_held(self):
        fixture, run, torb, winston, _ = self.arena(distance=4)
        hp = fixture.health(run, torb)
        instance = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFE)
        ammo = instance.vars[53]
        self.assertEqual(ammo.value(), 100)
        fixture.advance(run, torb, winston, 260, defense=1, defender_aim=(0, 0))
        self.assertEqual(hp.total, 250)
        fixture.advance(run, torb, winston, 52, defense=1)
        damage = instance.states[13]
        activation = damage.counter
        self.assertTrue(damage.active)
        self.assertGreater(ammo.value(), 0)
        self.assertLess(ammo.value(), 1)
        self.assertAlmostEqual(hp.total, 215.056)
        fixture.step(run, torb, winston, defense=1)
        self.assertEqual(ammo.value(), 0)
        self.assertFalse(damage.active)
        empty_hp = hp.total
        fixture.advance(run, torb, winston, 78, defense=1)
        self.assertEqual(ammo.value(), 0)
        fixture.step(run, torb, winston, defense=1)
        self.assertEqual(ammo.value(), 100)
        fixture.advance(run, torb, winston, 26, defense=1)
        self.assertEqual(hp.total, empty_hp)
        self.assertFalse(damage.active)
        fixture.step(run, torb, winston, defense=1)
        self.assertTrue(damage.active)
        self.assertGreater(damage.counter, activation)
        self.assertLess(ammo.value(), 100)
        self.assertAlmostEqual(empty_hp - hp.total, .672)
        self.assertAlmostEqual(winston.stats.value(stats.HERO_DAMAGE), 35.616, places=4)

    def test_tesla_kill_during_overload_clears_state_and_respawn_restores_pools(self):
        fixture, run, torb, winston, _ = self.arena(distance=4)
        old_body = torb.body
        hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 250)
        fixture.advance(run, torb, winston, 250, defense=1)
        self.assertAlmostEqual(hp.total, 31.4285714286)
        fixture.step(run, torb, winston, fire=16, defense=1)
        self.assertAlmostEqual(hp.over_armour.amount, 99.328)
        fixture.advance(run, torb, winston, 62, defense=1)
        self.assertAlmostEqual(hp.over_armour.amount, 58.336)
        empty_hp = hp.total
        fixture.advance(run, torb, winston, 105, defense=1)
        self.assertEqual(hp.total, empty_hp)
        for _ in range(130):
            self.assertIsNotNone(hp.over_armour.source)
            fixture.step(run, torb, winston, defense=1)
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, winston.body)
        self.assertEqual(hp.total, 0)
        self.assertIsNone(hp.over_armour.source)
        self.assertTrue(torb.body_script.component.owner_dead)
        self.assertEqual(winston.stats.value(stats.ELIMINATIONS), 1)
        self.assertAlmostEqual(winston.stats.value(stats.HERO_DAMAGE), 350, places=3)
        for _ in range(40):
            run.step({torb: (0, 0, 16)})
            self.assertEqual(hp.total, 0)
        for _ in range(750):
            if torb.body != old_body:
                break
            run.step({})
        self.assertNotEqual(torb.body, old_body)
        fresh = fixture.health(run, torb)
        self.assertIsNot(fresh, hp)
        self.assertEqual(fresh.total, 250)
        self.assertIsNone(fresh.over_armour.source)
        fixture.advance(run, torb, winston, 30)
        fixture.step(run, torb, winston, fire=16)
        self.assertEqual(fresh.total, 350)
        self.assertEqual(fresh.over_armour.amount, 100)

    def test_rivet_kill_interrupts_held_tesla_and_fresh_winston_body_can_fire(self):
        fixture, run, torb, winston, _ = self.arena(distance=4)
        old_body = winston.body
        hp = fixture.health(run, winston)
        torb_hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 500)
        for _ in range(2):
            fixture.step(run, torb, winston, fire=1)
            fixture.advance(run, torb, winston, 42)
        fixture.step(run, torb, winston, fire=16, defense=1)
        instance = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0xFE)
        for _ in range(6):
            fixture.step(run, torb, winston, fire=1, defense=1)
            fixture.advance(run, torb, winston, 42, defense=1)
            if hp.total < 70:
                break
        self.assertFalse(hp.dead)
        self.assertTrue(instance.states[13].active)
        self.assertLess(hp.total, 70)
        fixture.step(run, torb, winston, fire=1, defense=1)
        for _ in range(12):
            credit = winston.stats.value(stats.HERO_DAMAGE)
            fixture.step(run, torb, winston, defense=1)
            if hp.dead:
                self.assertEqual(winston.stats.value(stats.HERO_DAMAGE), credit)
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, torb.body)
        self.assertTrue(winston.body_script.component.owner_dead)
        self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 500, places=3)
        before = torb_hp.total
        credit = winston.stats.value(stats.HERO_DAMAGE)
        fixture.advance(run, torb, winston, 24, defense=1)
        self.assertEqual(torb_hp.total, before)
        self.assertEqual(winston.stats.value(stats.HERO_DAMAGE), credit)
        for _ in range(750):
            if winston.body != old_body:
                break
            run.step({})
        self.assertNotEqual(winston.body, old_body)
        self.assertEqual(fixture.health(run, winston).total, 500)
        x, y, z = torb.mover.position
        fixture.place(winston, (x, y, z + 4))
        fixture.advance(run, torb, winston, 30)
        before = torb_hp.total
        fixture.advance(run, torb, winston, 10, defense=1)
        self.assertAlmostEqual(before - torb_hp.total, 9.6)

    def test_globule_contact_consumes_globule_without_remote_pool(self):
        fixture, run, torb, winston, _ = self.arena()
        torb.body_script.set_ult_charge(10000)
        fixture.step(run, torb, winston, fire=32)
        fixture.advance(run, torb, winston, 45)
        actor = self.cast(fixture, run, torb, winston)
        hp = fixture.health(run, winston)
        before = hp.total
        fixture.step(run, torb, winston, fire=1)
        fixture.advance(run, torb, winston, 28)
        self.assertEqual(actor.health, 675)
        self.assertEqual(hp.total, before)
        self.assertFalse(run.match.combat.molten_pools)

    def test_airborne_shots_use_their_position_when_winston_shell_spawns(self):
        cases = (
            ("rivet", 1, 1, 0, 70, 65),
            ("secondary", 2, 10, 1, 125, 75),
            ("globule", 1, 1, -2, 25, 20),
        )
        spawn_frame = 3
        for weapon, button, count, inside_fire_frame, shield_damage, armored_damage in cases:
            for inside in (False, True):
                with self.subTest(weapon=weapon, inside=inside):
                    fixture, run, torb, winston, _ = self.arena()
                    fight = run.match.combat
                    actors = fight.world_bubbles.actors
                    if weapon == "globule":
                        torb.body_script.set_ult_charge(10000)
                        fixture.step(run, torb, winston, fire=32)
                        fixture.advance(run, torb, winston, 45)
                    hp = fixture.health(run, winston)
                    before = hp.total
                    charge = torb.body_script.component.entity_var(342)
                    fire_frame = inside_fire_frame if inside else inside_fire_frame + 1
                    volley = ()
                    for frame in range(min(0, fire_frame), spawn_frame + 1):
                        fire = button if frame == fire_frame else 0
                        run.step({winston: (0, 0, 16 if frame == 0 else 0),
                                  torb: (*fixture.body_aim(run, torb, winston), fire)})
                        self.assertEqual(bool(actors), frame == spawn_frame)
                        if frame == fire_frame:
                            volley = tuple(fight.rockets)
                            self.assertEqual(len(volley), count)
                            self.assertTrue(all(shot.shooter is torb for shot in volley))
                            self.assertTrue(all(shot.distance == 0 for shot in volley))

                    actor, = actors.values()
                    self.assertEqual(actor.born_tick, run.frame)
                    self.assertEqual(actor.health, 700)
                    self.assertEqual(tuple(fight.rockets), volley)
                    self.assertTrue(all((math.dist(shot.position, actor.point) < actor.radius) == inside
                                        for shot in volley))
                    self.assertEqual(hp.total, before)
                    fixture.advance(run, torb, winston, 25)

                    self.assertIs(actors[actor.entity], actor)
                    self.assertEqual(actor.health, 700 if inside else 700 - shield_damage)
                    direct = armored_damage if inside else 0
                    self.assertEqual(hp.total, before - direct)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(charge.value(), 0 if weapon == "globule" else direct)
                    self.assertEqual((torb.entity, winston.body) in fight.torbjorn_marks,
                                     weapon == "rivet" and inside)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)

    def test_airborne_shots_cross_winston_shell_on_natural_expiry_frame(self):
        cases = (
            ("rivet", 1, 1, 3, 70, 65),
            ("secondary", 2, 10, 2, 125, 75),
            ("globule", 1, 1, 5, 25, 20),
        )
        for weapon, button, count, travel_ticks, shield_damage, armored_damage in cases:
            for crosses_on_expiry in (False, True):
                with self.subTest(weapon=weapon, crosses_on_expiry=crosses_on_expiry):
                    fixture, run, torb, winston, _ = self.arena()
                    fight = run.match.combat
                    actors = fight.world_bubbles.actors
                    actor = self.cast(fixture, run, torb, winston)
                    hosted = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
                    timer = hosted.states[11]
                    self.assertEqual(expr.to_float(hosted.vars[567].value()), 9)
                    expiry = actor.born_tick + 563
                    crossing = expiry if crosses_on_expiry else expiry - 1
                    launch = crossing - travel_ticks
                    if weapon == "globule":
                        fixture.advance(run, torb, winston, launch - 47 - run.frame)
                        torb.body_script.set_ult_charge(10000)
                        fixture.step(run, torb, winston, fire=32)
                        fixture.advance(run, torb, winston, 45)
                    else:
                        fixture.advance(run, torb, winston, launch - 1 - run.frame)
                    self.assertEqual(run.frame, launch - 1)
                    hp = fixture.health(run, winston)
                    before = hp.total
                    charge = torb.body_script.component.entity_var(342)

                    fixture.step(run, torb, winston, fire=button)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is torb and shot.distance == 0 for shot in volley))
                    fixture.advance(run, torb, winston, travel_ticks - 1)
                    self.assertEqual(run.frame, crossing - 1)
                    self.assertIs(actors[actor.entity], actor)
                    self.assertFalse(timer.reached)
                    self.assertEqual(actor.health, 700)
                    self.assertEqual(tuple(fight.rockets), volley)
                    self.assertTrue(all(math.dist(shot.position, actor.point) > actor.radius
                                        for shot in volley))
                    fixture.step(run, torb, winston)
                    self.assertEqual(run.frame, crossing)
                    self.assertEqual(hp.total, before)
                    if crosses_on_expiry:
                        self.assertTrue(timer.reached)
                        self.assertNotIn(actor.entity, actors)
                        self.assertEqual(tuple(fight.rockets), volley)
                        self.assertTrue(all(math.dist(shot.position, actor.point) < actor.radius
                                            for shot in volley))
                    else:
                        self.assertIs(actors[actor.entity], actor)
                        self.assertFalse(timer.reached)
                        self.assertFalse(fight.rockets)
                        fixture.step(run, torb, winston)
                        self.assertEqual(run.frame, expiry)
                        self.assertTrue(timer.reached)
                        self.assertNotIn(actor.entity, actors)

                    fixture.advance(run, torb, winston, 20)
                    self.assertEqual(actor.health, 700 if crosses_on_expiry else 700 - shield_damage)
                    direct = armored_damage if crosses_on_expiry else 0
                    self.assertEqual(hp.total, before - direct)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(charge.value(), 0 if weapon == "globule" else direct)
                    self.assertEqual((torb.entity, winston.body) in fight.torbjorn_marks,
                                     weapon == "rivet" and crosses_on_expiry)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)

    def test_turret_rays_debit_fixed_pool(self):
        fixture, run, torb, winston, other = self.arena(distance=30)
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        fight = run.match.combat
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, winston, 100)
        turret, = fight.turrets.values()
        x, y, z = turret.actor.position
        fixture.place(winston, (x, y, z + 8))
        self.assertEqual(turret.shots, 0)
        hp = fixture.health(run, winston)
        before = hp.total
        actor = self.cast(fixture, run, torb, winston)
        for _ in range(600):
            run.step({})
            if actor.entity not in fight.world_bubbles.actors:
                break
            self.assertEqual(hp.total, before)
            self.assertEqual(actor.health, 700 - turret.shots * 14)
            self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        self.assertNotIn(actor.entity, fight.world_bubbles.actors)
        self.assertGreater(turret.shots, 1)
        self.assertGreater(actor.health, 0)
        for _ in range(30):
            if hp.total < before:
                break
            run.step({})
        self.assertEqual(hp.total, before - 9)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 9)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 9)

    def test_outer_flashbang_debits_shell_once_and_spares_both_allies(self):
        fixture, run, torb, winston, caster = self.arena(distance=7)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        winston.team = torb.team
        caster.team = 1 - torb.team
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        x, y, z = torb.mover.position
        fixture.place(caster, (x, y, z))
        fixture.place(torb, (x, y, z + 3.5))
        fixture.advance(run, torb, winston, 35)
        actor = self.cast(fixture, run, torb, winston)
        yaw, pitch = fixture.body_aim(run, caster, torb)
        run.step({caster: (yaw, pitch, 16)})
        fight = run.match.combat
        for _ in range(40):
            run.step({})
            self.assertFalse(fight.is_stunned(torb) or fight.is_stunned(winston))
        self.assertEqual(actor.health, 675)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertEqual(fixture.health(run, winston).total, 500)

    def test_live_actor_component_and_capacity_are_passive_snapshot_state(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest
        from ow174.game.world_bubbles import WINSTON

        case = ServerSnapshotTests()
        session, peers, players = case.scene()
        winston = players[0]
        session.apply("switch_hero", {"account": winston.account_lo, "hero": WINSTON})
        peers[0].commands(winston.body_script.frame + 1, 25, 0)
        peers[0].commands(winston.body_script.frame + 1, 1, 16)
        peers[0].commands(winston.body_script.frame + 1, 12, 0)
        actor, = winston.match.combat.world_bubbles.actors.values()
        digest = snapshot_digest(case.capture(session))
        self.assertEqual(digest, snapshot_digest(case.capture(session)))
        actor.health -= 1
        self.assertNotEqual(digest, snapshot_digest(case.capture(session)))

    def test_landed_molten_inside_shell_burns_winston_without_debiting_shell(self):
        fixture, run, torb, winston, _ = self.arena(distance=15)
        fight = run.match.combat
        torb.body_script.set_ult_charge(10000)
        run.step({torb: (0, 4000, 32)})
        fixture.advance(run, torb, winston, 45)
        run.step({torb: (0, 4000, 1)})
        for _ in range(40):
            run.step({})
            if fight.molten_pools:
                break
        pool, = fight.molten_pools.values()
        fixture.place(winston, pool.position)
        hp = fixture.health(run, winston)
        before = hp.total
        actor = self.cast(fixture, run, torb, winston)
        key = torb.entity, winston.body
        for _ in range(80):
            fixture.step(run, torb, winston)
            if key in fight.molten_burns:
                break
        self.assertIn(key, fight.molten_burns, (pool.position, winston.mover.position, pool.targets))
        self.assertLess(hp.total, before)
        self.assertEqual(actor.health, 700)

    def test_enemy_rivet_from_inside_hits_exit_surface_before_body(self):
        fixture, run, torb, winston, _ = self.arena(distance=2)
        actor = self.cast(fixture, run, torb, winston)
        x, y, z = winston.mover.position
        fixture.place(winston, (x, y, z + 7))
        fixture.advance(run, torb, winston, 3)
        hp = fixture.health(run, winston)
        before = hp.total
        fixture.step(run, torb, winston, fire=1)
        fixture.advance(run, torb, winston, 35)
        self.assertEqual(actor.health, 630)
        self.assertEqual(hp.total, before)
