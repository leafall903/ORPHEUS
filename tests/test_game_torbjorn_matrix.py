"""Command-driven Matrix on the explicit 104319 possessed-mech fixture."""

import json
import os
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim, in_world
from test_game_mover import box

from ow174.game import (
    combat,
    hero_torbjorn,
    molten_core,
    mover,
    npc,
    npc_control,
    orisa_halt,
    pools,
    projectile_defenses,
    stats,
    torbjorn_mark,
)
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import graph


@unittest.skipUnless(
    os.environ.get("ORPHEUS_DVA_MECH_DATA")
    and os.environ.get("ORPHEUS_DVA_MECH_MOVERS")
    and os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE")
    and os.environ.get("ORPHEUS_PROJECTILE_TAGS"),
    "needs mech, hero graphs and projectile tags",
)
class TorbjornMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)
        supplement = json.loads(Path(os.environ["ORPHEUS_DVA_MECH_DATA"]).read_text(encoding="utf-8"))
        if supplement["build"] != 104319 or supplement["missing"]:
            raise ValueError("expected complete 104319 mech supplement")
        base = graph._data()
        known = {row["index"] for row in base["graphs"]}
        bodies = dict(base["bodies"])
        del bodies["0x040000000000040A"]
        bodies["0x0400000000000407"] = supplement["body"]
        data = dict(base, bodies=bodies, graphs=base["graphs"] + [
            row for row in supplement["graphs"] if row["index"] not in known
        ])
        override = patch.object(graph, "_data", return_value=data)
        cls.addClassCleanup(cls.clear_cache)
        override.start()
        cls.addClassCleanup(override.stop)
        cls.clear_cache()
        movers = json.loads(Path(os.environ["ORPHEUS_DVA_MECH_MOVERS"]).read_text(encoding="utf-8"))
        mech = movers["0x0400000000000407"]
        names = {field.name for field in fields(mover.MoverData)}
        table = dict(mover._movers())
        table[projectile_defenses.MATRIX_BODY] = mover.MoverData(**{
            key: value for key, value in mech.items() if key in names
        })
        override = patch.object(mover, "_mover_table", table)
        override.start()
        cls.addClassCleanup(override.stop)

    @staticmethod
    def clear_cache():
        graph._records.cache_clear()
        graph.graph.cache_clear()
        pools.hero_pools.cache_clear()

    def arena(self, distance=3):
        fixture = defenses.TorbjornDefenseTests()
        hero = heroes()[projectile_defenses.MATRIX_HERO]
        run, owner, dva, _ = fixture.arena(hero.name, distance)
        position = list(dva.mover.position)
        run.match.switch_hero(dva, replace(hero, body=projectile_defenses.MATRIX_BODY))
        fixture.place(dva, position)
        fixture.advance(run, owner, dva, 70)
        instances = {
            instance.graph.index: instance for instance in dva.body_script.component.instances.values()
        }
        # Supply the already possessed body. Normal hero selection uses the pilot.
        instances[0x0276].state(30).end(False)
        return fixture, run, owner, dva, instances[0x02CC]

    def test_matrix_allows_front_hammer_and_quick_melee(self):
        for hammer, button, damage in ((False, 0x800, 25), (True, 1, 50)):
            with self.subTest(hammer=hammer):
                fixture, run, owner, dva, matrix = self.arena(distance=1.5)
                if hammer:
                    run.frame += 1
                    owner.take_commands([Command(run.frame, action=2)])
                    fixture.advance(run, owner, dva, 45)
                hp = fixture.health(run, dva)
                before = hp.total
                fixture.step(run, owner, dva, defense=2)
                self.assertTrue(matrix.state(3).active)
                fixture.step(run, owner, dva, fire=button, defense=2)
                fixture.advance(run, owner, dva, 35, defense=2)
                self.assertTrue(matrix.state(3).active)
                self.assertEqual(before - hp.total, damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), damage)

    def test_matrix_deletes_actual_rivet_and_secondary_and_release_restores_hits(self):
        for button in (1, 2):
            with self.subTest(button=button):
                fixture, run, owner, dva, matrix = self.arena()
                hp = fixture.health(run, dva)
                before = hp.total
                self.assertEqual(matrix.vars[87].value(), 100)
                fixture.step(run, owner, dva, defense=2)
                self.assertTrue(matrix.state(3).active)
                fixture.step(run, owner, dva, fire=button, defense=2)
                fixture.advance(run, owner, dva, 20, defense=2)
                self.assertLess(matrix.vars[87].value(), 100)
                self.assertEqual(hp.total, before)
                self.assertEqual(fixture.health(run, owner).total, 250)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(run.match.combat.rockets, [])
                fixture.advance(run, owner, dva, 65)
                self.assertFalse(matrix.state(3).active)
                fixture.step(run, owner, dva, fire=button)
                fixture.advance(run, owner, dva, 20)
                self.assertLess(hp.total, before)

    def test_matrix_opened_after_launch_intercepts_only_the_frontal_volley(self):
        for button, rounds, count, damage in ((1, 1, 1, 65), (2, 2, 10, 75)):
            energy = {}
            for frontal in (False, True):
                with self.subTest(button=button, frontal=frontal):
                    fixture, run, owner, dva, matrix = self.arena(distance=4)
                    fight = run.match.combat
                    hp = fixture.health(run, dva)
                    before = hp.total
                    rifle = next(i for i in owner.body_script.component.instances.values()
                                 if i.graph.index == 0x1683)
                    charge = owner.body_script.component.entity_var(342)
                    self.assertFalse(matrix.state(3).active)
                    self.assertEqual(matrix.vars[87].value(), 100)
                    fixture.step(run, owner, dva, fire=button)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is owner and shot.distance == 0 for shot in volley))
                    direction = fixture.body_aim(run, dva, owner) if frontal else (0, 0)
                    with patch.object(fight, "_landed", wraps=fight._landed) as landed:
                        fixture.step(run, owner, dva, defense=2, defender_aim=direction)
                        self.assertTrue(matrix.state(3).active)
                        self.assertEqual(tuple(fight.rockets), volley)
                        fixture.advance(run, owner, dva, 20, defense=2, defender_aim=direction)
                    intercepted = sum(call.kwargs.get("intercepted", False) for call in landed.call_args_list)
                    self.assertEqual(intercepted, count if frontal else 0)
                    direct = 0 if frontal else damage
                    self.assertFalse(fight.rockets)
                    self.assertEqual(hp.total, before - direct)
                    self.assertEqual(charge.value(), direct)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(rifle.vars[53].value(), 18 - rounds)
                    self.assertEqual((owner.entity, dva.body) in fight.torbjorn_marks,
                                     button == 1 and not frontal)
                    energy[frontal] = matrix.vars[87].value()
                    self.assertLess(energy[frontal], 100)
                    if frontal:
                        fixture.advance(run, owner, dva, 65)
                        self.assertFalse(matrix.state(3).active)
                        fixture.step(run, owner, dva, fire=button)
                        fixture.advance(run, owner, dva, 20)
                        self.assertEqual(hp.total, before - damage)
                        self.assertEqual(charge.value(), damage)
                        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                        self.assertEqual(rifle.vars[53].value(), 18 - 2 * rounds)
            self.assertAlmostEqual(energy[True], energy[False], places=4)

    def test_secondary_volley_splits_at_matrix_edge_then_breaks_projected_barrier(self):
        fixture, run, owner, dva, matrix = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        protected = run.players[2]
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        zarya = match.add_player(8, "Zarya", zarya_hero.guid, 1 - owner.team, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        dva.team = protected.team = zarya.team = 1 - owner.team
        x, y, z = owner.mover.position
        fixture.place(dva, (x + 1.30, y, z + 3.8))
        fixture.place(protected, (x, y, z + 4))
        fixture.place(zarya, (x - 4, y, z + 4))
        fixture.advance(run, owner, protected, 35)
        held = {dva: (-32768, 0, 2)}
        run.step({**held, zarya: (*fixture.body_aim(run, zarya, protected), 16)})
        self.assertTrue(matrix.state(3).active)
        bubble = fight.bubbles.for_target(protected)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 5
        hp = fixture.health(run, protected)
        mech_hp = fixture.health(run, dva)
        mech_before = mech_hp.total
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0)
        gate = projectile_defenses.projectile_gate
        caught = []

        def record(*args, **kwargs):
            hit = gate(*args, **kwargs)
            if hit is not None:
                self.assertIs(hit[1].owner, dva)
                self.assertEqual(hit[2], 2)
                caught.append(hit)
            return hit

        with (
            patch.object(projectile_defenses, "projectile_gate", side_effect=record),
            patch.object(fight, "_shield_damage", wraps=fight._shield_damage) as shield_hits,
            patch.object(fight, "_damage", wraps=fight._damage) as body_hits,
        ):
            run.step({**held, owner: (*fixture.body_aim(run, owner, protected), 2)})
            self.assertEqual(len(fight.rockets), 10)
            for _ in range(16):
                run.step(held)
        self.assertEqual(len(caught), 4)
        self.assertEqual([call.args[1:] for call in shield_hits.call_args_list], [(bubble, 12.5)])
        self.assertIsNone(fight.bubbles.for_target(protected))
        self.assertEqual([call.args[2] for call in body_hits.call_args_list], [12.5] * 5)
        self.assertTrue(all(call.args[1].owner is protected for call in body_hits.call_args_list))
        damage = 62.5
        self.assertEqual(hp.total, 200 - damage)
        self.assertEqual(mech_hp.total, mech_before)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), damage)
        self.assertFalse(fight.rockets or fight.torbjorn_marks)
        self.assertTrue(matrix.state(3).active)

    def test_matrix_intercepts_last_round_secondary_without_changing_reload_timing(self):
        exposed = self.last_round_secondary_reload(matrix_enabled=False)
        intercepted = self.last_round_secondary_reload(matrix_enabled=True)
        self.assertEqual(intercepted, exposed)

    def last_round_secondary_reload(self, *, matrix_enabled):
        fixture, run, torb, dva, matrix = self.arena()
        fight = run.match.combat
        hp = fixture.health(run, dva)
        weapon = next(instance for instance in torb.body_script.component.instances.values()
                      if instance.graph.index == 0x1683)
        ammo = weapon.vars[53]
        for _ in range(630):
            run.step({torb: (16384, 0, 1)})
        fixture.advance(run, torb, dva, 50)
        self.assertEqual(ammo.value(), 1)
        before = hp.total
        defense = 2 if matrix_enabled else 0
        fixture.step(run, torb, dva, defense=defense)
        self.assertEqual(matrix.state(3).active, matrix_enabled)
        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            fixture.step(run, torb, dva, fire=2, defense=defense)
            empty = run.frame
            self.assertEqual(ammo.value(), 0)
            fixture.advance(run, torb, dva, 20, fire=2, defense=defense)
            self.assertEqual(ammo.value(), 0)
            self.assertEqual(sum(call.kwargs.get("intercepted", False) for call in landed.call_args_list),
                             10 if matrix_enabled else 0)
        self.assertEqual(hp.total, before if matrix_enabled else before - 75)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0 if matrix_enabled else 75)
        self.assertFalse(fight.rockets)

        reload = weapon.states[8]
        for _ in range(80):
            if reload.active:
                break
            fixture.step(run, torb, dva, fire=2)
            self.assertEqual(ammo.value(), 0)
        self.assertTrue(reload.active)
        started = run.frame - empty
        self.assertAlmostEqual(weapon.vars[581].value(), 2)
        for _ in range(90):
            if ammo.value() != 0:
                break
            fixture.step(run, torb, dva, fire=2)
        self.assertTrue(reload.active)
        self.assertEqual(ammo.value(), 18)
        refilled = run.frame - empty
        before = hp.total
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            for _ in range(90):
                if ammo.value() != 18:
                    break
                fixture.step(run, torb, dva, fire=2)
            resumed = run.frame - empty
            self.assertEqual(ammo.value(), 16)
            self.assertFalse(reload.active)
            self.assertFalse(matrix.state(3).active)
            launches = [call for call in shots.call_args_list if call.args[2].instance is weapon]
            self.assertEqual(len(launches), 1)
            self.assertEqual(launches[0].args[2].index, 13)
        fixture.advance(run, torb, dva, 20)
        self.assertEqual(hp.total, before - 75)
        return started, refilled, resumed

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_matrix_exhaustion_exposes_overload_to_continued_talon_fire(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        cases = (("Trooper", 15), ("Sniper", 95), ("Heavy Assault", 2.5), ("Enforcer", 25))
        for name, damage in cases:
            with self.subTest(npc=name):
                definition = next(row for row in catalog.values() if row.name == name)
                fixture, run, torb, dva, matrix = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                dva.team = torb.team
                x, y, z = torb.mover.position
                fixture.place(run.players[2], (x + 100, y, z + 100))
                fixture.place(dva, (x - 1.25, y, z + 1.25))
                bot = npc.spawn(match, definition, (x, y, z + 5), team=1 - torb.team)
                for viewer in run.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                run.step({torb: (*fixture.body_aim(run, torb, bot), 0)})
                self.assertEqual(npc_control.start(match, torb), bot.entity)
                if name == "Sniper":
                    fixture.advance(run, torb, dva, 50)
                torb_hp = fixture.health(run, torb)
                dva_hp = fixture.health(run, dva)
                bot_hp = fight.vitals[bot.entity]
                dva_before, bot_before = dva_hp.total, bot_hp.total
                base = dict(torb_hp.now)
                self.assertEqual(torb_hp.total, 250)
                direction = fixture.body_aim(run, dva, bot)
                held = {dva: (*direction, 2)}
                run.step({**held, torb: (0, 0, 16)})
                self.assertTrue(matrix.state(3).active)
                self.assertEqual(torb_hp.total, 350)
                source = torb_hp.over_armour.source
                gate = projectile_defenses.projectile_gate
                intercepted = []

                def record(*args, gate=gate, dva=dva, intercepted=intercepted, **kwargs):
                    result = gate(*args, **kwargs)
                    if result is not None:
                        self.assertIs(result[1].owner, dva)
                        self.assertEqual(result[2], 2)
                        intercepted.append(result[0])
                    return result

                with patch.object(projectile_defenses, "projectile_gate", side_effect=record):
                    for _ in range(180):
                        run.step(held)
                        self.assertEqual(torb_hp.total, 350)
                        self.assertEqual(dva_hp.total, dva_before)
                        if not matrix.state(3).active:
                            break
                self.assertFalse(matrix.state(3).active)
                self.assertLessEqual(matrix.vars[87].value(), 1)
                self.assertGreaterEqual(len(intercepted), 25 if name == "Enforcer" else 1)
                for _ in range(150):
                    if torb_hp.total < 350:
                        break
                    run.step(held)
                self.assertTrue(bot.attack_input)
                self.assertEqual(torb_hp.over_armour.source, source)
                self.assertEqual(torb_hp.over_armour.amount, 100 - damage)
                self.assertEqual(torb_hp.now, base)
                self.assertEqual(dva_hp.total, dva_before)
                self.assertEqual(bot_hp.total, bot_before)
                self.assertEqual(dva.stats.value(stats.HERO_DAMAGE), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_matrix_then_projected_barrier_protect_overload_from_continued_talon_fire(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for name, intercepts, body_damage in (("Trooper", 1, 15), ("Heavy Assault", 1, 2.5),
                                              ("Enforcer", 25, 25)):
            with self.subTest(npc=name):
                definition = next(row for row in catalog.values() if row.name == name)
                self.check_matrix_and_projected_barrier(definition, intercepts, body_damage)

    def check_matrix_and_projected_barrier(self, definition, intercepts, body_damage):
        fixture, run, torb, dva, matrix = self.arena()
        match = run.match
        fight = match.combat
        zarya = run.players[2]
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        dva.team = zarya.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(zarya, (x + 5, y, z))
        fixture.place(dva, (x - 1.25, y, z + 1.25))
        bot = npc.spawn(match, definition, (x, y, z + 5), team=1 - torb.team)
        for viewer in run.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        direction = fixture.body_aim(run, torb, bot)
        run.step({torb: (*direction, 0)})
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        hp = fixture.health(run, torb)
        mech_hp = fixture.health(run, dva)
        mech_before = mech_hp.total
        base = dict(hp.now)
        held = {dva: (*fixture.body_aim(run, dva, bot), 2)}
        run.step({**held, torb: (*direction, 16), zarya: (*fixture.body_aim(run, zarya, torb), 16)})
        self.assertEqual(hp.total, 350)
        self.assertTrue(matrix.state(3).active)
        bubble = fight.bubbles.for_target(torb)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        source = hp.over_armour.source
        gate = projectile_defenses.projectile_gate
        caught = []

        def record(*args, **kwargs):
            hit = gate(*args, **kwargs)
            if hit is not None:
                self.assertIs(hit[1].owner, dva)
                self.assertEqual(hit[2], 2)
                caught.append(hit)
            return hit

        with patch.object(projectile_defenses, "projectile_gate", side_effect=record):
            for _ in range(100):
                if len(caught) >= intercepts:
                    break
                run.step(held)
                self.assertEqual(bubble.health, 200)
                self.assertEqual(hp.total, 350)
        self.assertEqual(len(caught), intercepts)
        self.assertTrue(matrix.state(3).active)
        energy = zarya.body_script.component.entity_var(87)
        self.assertEqual(energy.value() or 0, 0)
        breaks = definition.name != "Enforcer"
        if breaks:
            bubble.health = 1
        for _ in range(150):
            run.step({})
            if fight.bubbles.for_target(torb) is None:
                break
            self.assertEqual(hp.total, 350)
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(bubble.health, 0 if breaks else 200)
        self.assertIsNone(fight.bubbles.for_target(torb))
        self.assertEqual(hp.total, 350)
        self.assertAlmostEqual(energy.value() or 0, .2 if breaks else 0)
        for _ in range(100):
            run.step({})
            if hp.total < 350:
                break
        self.assertTrue(bot.attack_input)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.over_armour.amount, 100 - body_damage)
        self.assertEqual(hp.now, base)
        self.assertEqual(mech_hp.total, mech_before)

    def test_particle_beam_passes_matrix_but_hits_the_nearer_mech_body(self):
        fixture, run, torb, dva, matrix = self.arena()
        match = run.match
        fight = match.combat
        zarya = run.players[2]
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        dva.team = torb.team
        zarya.team = 1 - torb.team
        x, y, z = torb.mover.position
        offset = (x - 1.25, y, z + 1.25)
        fixture.place(dva, offset)
        fixture.place(zarya, (x, y, z + 5))
        hp = fixture.health(run, torb)
        mech_hp = fixture.health(run, dva)
        mech_start = mech_hp.total
        base = dict(hp.now)
        run.step({torb: (0, 0, 16), dva: (*fixture.body_aim(run, dva, zarya), 2)})
        source = hp.over_armour.source
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertTrue(matrix.state(3).active)
        direction = fixture.body_aim(run, zarya, torb)
        gate = projectile_defenses.projectile_gate(
            fight._eye(zarya), combat.aim(*direction), 5, fight._enemies(zarya),
            0x0400000000002A66, collision=match.collision_world,
        )
        self.assertIsNotNone(gate)
        self.assertIs(gate[1].owner, dva)
        self.assertEqual(gate[2], 2)

        def fire(frames):
            for _ in range(frames):
                run.step({zarya: (*direction, 1), dva: (*fixture.body_aim(run, dva, zarya), 2)})
                self.assertTrue(matrix.state(3).active)

        fire(25)
        self.assertAlmostEqual(hp.over_armour.amount, 79.0525)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 20.9475)
        self.assertEqual(mech_hp.total, mech_start)

        fixture.place(dva, (x, y, z + 1.25))
        torb_before = hp.over_armour.amount
        fire(2)
        self.assertEqual(hp.over_armour.amount, torb_before)
        mech_before = mech_hp.total
        fire(8)
        self.assertAlmostEqual(mech_before - mech_hp.total, 6.72)
        self.assertEqual(hp.over_armour.amount, torb_before)
        fixture.place(dva, offset)
        fire(2)
        self.assertAlmostEqual(mech_hp.total, mech_before - 6.72)
        torb_before = hp.over_armour.amount
        fire(8)
        self.assertAlmostEqual(torb_before - hp.over_armour.amount, 6.72)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        damage = 350 - hp.total + mech_start - mech_hp.total
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), damage)

    def test_matrix_deletes_ana_rifle_without_wound_and_release_restores_hits(self):
        ana_hero = next(hero for hero in heroes().values() if hero.name == "Ana")
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                fixture, run, torb, dva, matrix = self.arena()
                match = run.match
                fight = match.combat
                ana = run.players[2]
                match.game_map = replace(match.game_map, free_for_all=False)
                torb.team = dva.team = 1 - ana.team
                self.assertTrue(match.switch_hero(ana, ana_hero))
                x, y, z = torb.mover.position
                fixture.place(ana, (x, y, z))
                fixture.place(torb, (x, y, z + 7))
                torb_hp = fixture.health(run, torb)
                dva_hp = fixture.health(run, dva)
                dva_before = dva_hp.total
                scope = 2 if scoped else 0
                rifle_aim = fixture.body_aim(run, ana, torb)
                matrix_aim = fixture.body_aim(run, dva, ana)
                run.step({torb: (0, 0, 16), ana: (*rifle_aim, scope)})
                for _ in range(40):
                    run.step({ana: (*rifle_aim, scope)})
                held = {ana: (*rifle_aim, scope), dva: (*matrix_aim, 2)}
                run.step(held)
                self.assertTrue(matrix.state(3).active)
                rifle = next(instance for instance in ana.body_script.component.instances.values()
                             if instance.graph.index == 0x0B94)
                rounds = rifle.vars[53].value()
                run.step({**held, ana: (*rifle_aim, scope | 1)})
                for _ in range(32):
                    run.step(held)
                    self.assertFalse(fight.rifle_wounds.active)
                self.assertEqual(rifle.vars[53].value(), rounds - 1)
                self.assertFalse(fight.rockets)
                self.assertEqual(dva_hp.total, dva_before)
                self.assertEqual(torb_hp.total, 350)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertLess(matrix.vars[87].value(), 100)

                fixture.place(dva, (x + 8, y, z + 3))
                for _ in range(45):
                    run.step({ana: (*rifle_aim, scope)})
                self.assertFalse(matrix.state(3).active)
                run.step({ana: (*rifle_aim, scope | 1)})
                for _ in range(55):
                    run.step({ana: (*rifle_aim, scope)})
                self.assertAlmostEqual(torb_hp.over_armour.amount, 30)
                self.assertEqual(dva_hp.total, dva_before)
                self.assertFalse(fight.rifle_wounds.active)
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 70, places=4)

    def mark_arena(self):
        fixture, run, owner, dva, matrix = self.arena()
        match = run.match
        fight = match.combat
        near = next(player for player in match.players if player not in (owner, dva))
        x, y, z = owner.mover.position
        fixture.place(near, (x + 7.5, y, z))
        turret = hero_torbjorn.deploy_turret(fight, owner, (x + 6, y, z))
        fixture.advance(run, owner, dva, 190)
        self.assertEqual(turret.target, near.body)
        return fixture, run, owner, dva, near, turret, matrix

    def test_matrix_consumes_rivet_without_mark_and_release_restores_turret_priority(self):
        fixture, run, owner, dva, near, turret, matrix = self.mark_arena()
        fight = run.match.combat
        hp = fixture.health(run, dva)
        before = hp.total
        self.assertEqual(turret.target, near.body)
        fixture.step(run, owner, dva, defense=2)
        self.assertTrue(matrix.state(3).active)
        fixture.step(run, owner, dva, fire=1, defense=2)
        fixture.advance(run, owner, dva, 20, defense=2)
        self.assertLess(matrix.vars[87].value(), 100)
        self.assertEqual(hp.total, before)
        self.assertFalse(fight.torbjorn_marks)
        self.assertEqual(turret.target, near.body)
        fixture.advance(run, owner, dva, 65)
        self.assertFalse(matrix.state(3).active)
        self.assertFalse(fight.torbjorn_marks)
        self.assertEqual(turret.target, near.body)
        fixture.step(run, owner, dva, fire=1)
        fixture.advance(run, owner, dva, 20)
        self.assertLess(hp.total, before)
        self.assertTrue(torbjorn_mark.active(fight.torbjorn_marks[owner.entity, dva.body]))
        self.assertEqual(turret.target, dva.body)

    def test_matrix_consumes_marked_turret_shots_without_refreshing_mark(self):
        fixture, run, owner, dva, near, turret, matrix = self.mark_arena()
        fight = run.match.combat
        hp = fixture.health(run, dva)
        fixture.step(run, owner, dva, fire=1)
        key = owner.entity, dva.body
        for _ in range(20):
            if key in fight.torbjorn_marks and turret.target == dva.body:
                break
            run.step({})
        mark = fight.torbjorn_marks[key]
        expires = mark.expires
        x, y, z = turret.actor.position
        direction = aim(fight._eye(dva), (x, y + turret.actor.combat_geometry[2], z))
        run.step({dva: (*direction, 2)})
        self.assertTrue(matrix.state(3).active)
        before, shots = hp.total, turret.shots
        for _ in range(20):
            run.step({dva: (*direction, 2)})
            if turret.shots > shots:
                break
        self.assertGreater(turret.shots, shots)
        self.assertEqual(hp.total, before)
        self.assertIs(fight.torbjorn_marks[key], mark)
        self.assertEqual(mark.expires, expires)
        self.assertEqual(turret.target, dva.body)
        for _ in range(60):
            if key not in fight.torbjorn_marks:
                break
            run.step({dva: (*direction, 2)})
        self.assertNotIn(key, fight.torbjorn_marks)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(hp.total, before)
        self.assertEqual(turret.target, near.body)

    def test_quick_melee_still_hits_during_matrix(self):
        fixture, run, owner, dva, matrix = self.arena(distance=1.5)
        hp = fixture.health(run, dva)
        before = hp.total
        fixture.step(run, owner, dva, defense=2)
        self.assertTrue(matrix.state(3).active)
        fixture.step(run, owner, dva, defense=2, fire=0x800)
        fixture.advance(run, owner, dva, 20, defense=2)
        self.assertEqual(hp.total, before - 25)

    def test_matrix_deletes_globule_without_spawning_a_pool(self):
        fixture, run, owner, dva, matrix = self.arena()
        owner.body_script.set_ult_charge(10000)
        fixture.step(run, owner, dva, fire=32)
        fixture.advance(run, owner, dva, 45)
        hp = fixture.health(run, dva)
        before = hp.total
        fixture.step(run, owner, dva, defense=2)
        fixture.step(run, owner, dva, fire=1, defense=2)
        fixture.advance(run, owner, dva, 20, defense=2)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(hp.total, before)
        self.assertEqual(run.match.combat.molten_pools, {})
        self.assertEqual(run.match.combat.rockets, [])
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_matrix_opened_during_globule_flight_intercepts_only_from_front(self):
        for frontal in (True, False):
            with self.subTest(frontal=frontal):
                fixture, run, owner, dva, matrix = self.arena(distance=4)
                fight = run.match.combat
                hp = fixture.health(run, dva)
                before = hp.total
                charge = owner.body_script.component.entity_var(342)
                owner.body_script.set_ult_charge(10000)
                fixture.step(run, owner, dva, fire=32)
                fixture.advance(run, owner, dva, 45)

                self.assertFalse(matrix.state(3).active)
                fixture.step(run, owner, dva, fire=1)
                shot, = fight.rockets
                self.assertIs(shot.shooter, owner)
                self.assertEqual(shot.spawn, molten_core.POOL)
                self.assertEqual(shot.distance, 0)
                direction = fixture.body_aim(run, dva, owner) if frontal else (0, 0)
                with patch.object(fight, "_landed", wraps=fight._landed) as landed:
                    fixture.step(run, owner, dva, defense=2, defender_aim=direction)
                    self.assertTrue(matrix.state(3).active)
                    self.assertIn(shot, fight.rockets)
                    fixture.advance(run, owner, dva, 20, defense=2, defender_aim=direction)

                self.assertTrue(matrix.state(3).active)
                self.assertEqual(landed.call_count, 1)
                self.assertIs(landed.call_args.args[0], shot)
                self.assertEqual(landed.call_args.kwargs.get("intercepted", False), frontal)
                self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)
                direct_damage = 0 if frontal else 20
                self.assertEqual(hp.total, before - direct_damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), direct_damage)
                self.assertEqual(charge.value(), 0)

                if frontal:
                    fixture.advance(run, owner, dva, 40)
                    self.assertFalse(matrix.state(3).active)
                    fixture.step(run, owner, dva, fire=1)
                    fresh, = fight.rockets
                    self.assertIsNot(fresh, shot)
                    fixture.advance(run, owner, dva, 20)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)
                    self.assertEqual(hp.total, before - 20)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 20)
                    self.assertEqual(charge.value(), 0)

    def test_mech_death_before_matrix_contact_preserves_the_airborne_globule(self):
        for lethal, pitch in ((False, -1000), (True, -1000), (False, -2000), (True, -2000)):
            with self.subTest(lethal=lethal, pitch=pitch):
                self.exercise_globule_after_mech_death(lethal=lethal, pitch=pitch)

    def exercise_globule_after_mech_death(self, *, lethal, pitch):
        fixture, run, owner, dva, matrix = self.arena(distance=16)
        match = run.match
        fight = match.combat
        rear = run.players[2]
        self.assertTrue(match.switch_hero(rear, heroes()[hero_torbjorn.HERO]))
        x, y, z = dva.mover.position
        fixture.place(rear, (x, y, z + 3))
        mech_hp = fixture.health(run, dva)
        rear_hp = fixture.health(run, rear)
        owner.body_script.set_ult_charge(10000)
        fixture.step(run, owner, dva, fire=32)
        fixture.advance(run, owner, dva, 45)
        if lethal:
            mech_hp.damage(mech_hp.total - 1)
            fight._sync_body_health(dva, mech_hp)

        direction = (-32768, 0)
        run.step({owner: (0, pitch, 1), dva: (*direction, 2)})
        shot, = (r for r in fight.rockets if r.spawn == molten_core.POOL)
        self.assertIs(shot.shooter, owner)
        self.assertEqual(shot.distance, 0)
        self.assertTrue(matrix.state(3).active)
        if lethal:
            run.step({rear: (*fixture.body_aim(run, rear, dva), 1), dva: (*direction, 2)})
            for _ in range(12):
                if mech_hp.dead:
                    break
                run.step({dva: (*direction, 2)})
            self.assertTrue(mech_hp.dead)
            self.assertEqual(mech_hp.killer, rear.body)
            self.assertFalse(matrix.state(3).active)
            self.assertIn(shot, fight.rockets)
            self.assertFalse(fight.molten_pools)

        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            for _ in range(50):
                if shot not in fight.rockets:
                    break
                run.step({dva: (*direction, 2)})
        self.assertNotIn(shot, fight.rockets)
        call, = (call for call in landed.call_args_list if call.args[0] is shot)
        self.assertEqual(call.kwargs.get("intercepted", False), not lethal)
        if not lethal:
            self.assertTrue(matrix.state(3).active)
            self.assertEqual(mech_hp.total, 600)
            self.assertEqual(rear_hp.total, 250)
            self.assertFalse(fight.molten_pools or fight.molten_burns)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        elif pitch == -2000:
            self.assertEqual(rear_hp.total, 230)
            self.assertFalse(fight.molten_pools or fight.molten_burns)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 20)
        else:
            self.assertEqual(rear_hp.total, 250)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
            pool, = fight.molten_pools.values()
            self.assertIs(pool.owner, owner)
            for _ in range(45):
                if pool.phase == 1:
                    break
                run.step({})
            self.assertEqual(pool.phase, 1)
            fixture.place(rear, pool.position)
            run.step({})
            self.assertAlmostEqual(rear_hp.total, 243)
            self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 7)
            self.assertIn((owner.entity, rear.body), fight.molten_burns)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_matrix_intercepts_ceiling_rebound_and_release_restores_direct_hit(self):
        fixture, run, owner, dva, matrix = self.arena(distance=6.5)
        fight = run.match.combat
        x, y, z = owner.mover.position
        run.match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + box(x - 8, x + 8, y + 2.5, y + 3, z - 4, z + 12)
        )
        owner.body_script.set_ult_charge(10000)
        run.step({owner: (0, -7000, 32)})
        fixture.advance(run, owner, dva, 45)
        hp = fixture.health(run, dva)
        before = hp.total
        run.step({owner: (0, -7000, 1)})
        flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
        for _ in range(20):
            if flight.segment_start:
                break
            run.step({})
        self.assertIn(flight, fight.rockets)
        self.assertGreater(flight.segment_start, 0)
        self.assertFalse(matrix.state(3).active)
        for _ in range(20):
            if flight not in fight.rockets:
                break
            run.step({dva: (-32768, 0, 2)})
        self.assertNotIn(flight, fight.rockets)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(hp.total, before)
        self.assertFalse(fight.molten_pools or fight.molten_burns)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        fixture.advance(run, owner, dva, 40)
        self.assertFalse(matrix.state(3).active)
        run.step({owner: (0, -7000, 1)})
        fixture.advance(run, owner, dva, 30)
        self.assertEqual(hp.total, before - 20)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 20)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.molten_pools or fight.molten_burns)

    def test_matrix_deletes_new_globule_while_landed_pool_keeps_burning(self):
        fixture, run, owner, dva, matrix = self.arena()
        fight = run.match.combat
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        run.step({owner: (16384, 4000, 32)})
        fixture.advance(run, owner, dva, 45)
        run.step({owner: (16384, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        fixture.place(dva, pool.position)
        fixture.step(run, owner, dva, defense=2)
        self.assertTrue(matrix.state(3).active)
        burn = fight.molten_burns[owner.entity, dva.body]
        hp = fixture.health(run, dva)
        before = hp.total
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            fixture.step(run, owner, dva, fire=1, defense=2)
            fixture.advance(run, owner, dva, 20, defense=2)
        launches = [call for call in shoot.call_args_list
                    if call.args[0] is owner and call.args[2].instance.graph.index == 0x16DA]
        self.assertEqual(len(launches), 1)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(fight.rockets, [])
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertIs(fight.molten_burns[owner.entity, dva.body], burn)
        config = pool.config
        burn_damage = (config["base_dps"] + config["armour_bonus_dps"]) * combat.TICK * 21
        self.assertAlmostEqual(before - hp.total, burn_damage, places=4)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_matrix_eats_turret_fire_without_reflection(self):
        fixture, run, owner, dva, matrix = self.arena(distance=100)
        x, y, z = owner.mover.position
        fixture.place(run.players[2], (x + 100, y, z + 100))
        fight = run.match.combat
        run.step({owner: (0, 4000, 8)})
        fixture.advance(run, owner, dva, 225)
        turret = fight.turrets[owner.entity]
        self.assertEqual(turret.shots, 0)
        hp = fixture.health(run, dva)
        before = hp.total
        look = (-32768, 0, 2)
        run.step({dva: look})
        self.assertTrue(matrix.state(3).active)
        x, y, z = turret.actor.position
        fixture.place(dva, (x, y, z + 7))
        for _ in range(300):
            run.step({dva: look})
            if not matrix.state(3).active:
                break
            self.assertEqual(hp.total, before)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
            self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), 1)
        self.assertGreaterEqual(turret.shots, 2)
        for _ in range(30):
            if hp.total < before:
                break
            run.step({dva: look})
        self.assertEqual(hp.total, before - 9)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 9)
        self.assertEqual(fight.vitals[turret.actor.entity].total, 250)
        self.assertEqual(fixture.health(run, owner).total, 250)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 9)

    def test_excluded_turret_throw_crosses_matrix_and_deploys_for_torbjorn(self):
        fixture, run, owner, dva, matrix = self.arena()
        x, y, z = owner.mover.position
        fixture.place(dva, (x + .95, y, z + 3))
        look = (-32768, 0, 2)
        run.step({dva: look})
        self.assertTrue(matrix.state(3).active)
        run.step({owner: (0, 4000, 8), dva: look})
        fight = run.match.combat
        crossed = False
        for _ in range(100):
            for rocket in fight.rockets:
                if rocket.definition != 0x0400000000002931:
                    continue
                self.assertFalse(projectile_defenses.eligible(rocket.definition))
                # Query the throw's current position with eligible rivet tags
                # to observe the volume without changing the live projectile.
                crossed |= projectile_defenses.projectile_gate(
                    rocket.position, rocket.direction, 0.0, fight._enemies(owner),
                    0x0400000000002A66, collision=run.match.collision_world,
                ) is not None
            run.step({owner: (0, 4000, 0), dva: look})
            if owner.entity in fight.turrets:
                break
        self.assertTrue(crossed)
        self.assertIs(fight.turrets[owner.entity].owner, owner)
        self.assertNotIn(dva.entity, fight.turrets)

    def test_rear_and_friendly_matrix_do_not_intercept(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                fixture, run, owner, dva, matrix = self.arena()
                if friendly:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    dva.team = owner.team
                    target = run.players[2]
                    target.team = 1 - owner.team
                    x, y, z = owner.mover.position
                    fixture.place(target, (x, y, z + 5))
                else:
                    target = dva
                aim = {"defender_aim": (-32768 if friendly else 0, 0)}
                hp = fixture.health(run, target)
                before = hp.total
                fixture.step(run, owner, dva, defense=2, **aim)
                self.assertTrue(matrix.state(3).active)
                fixture.step(run, owner, dva, defense=2, fire=1, **aim)
                fixture.advance(run, owner, dva, 20, defense=2, **aim)
                self.assertLess(hp.total, before)

    def test_matrix_exhaustion_denies_immediate_restart_then_recovers(self):
        fixture, run, owner, dva, matrix = self.arena()
        fixture.step(run, owner, dva, defense=2)
        self.assertTrue(matrix.state(3).active)
        for _ in range(180):
            fixture.step(run, owner, dva, defense=2)
            if not matrix.state(3).active:
                break
        self.assertFalse(matrix.state(3).active)
        self.assertLessEqual(matrix.vars[87].value(), 1)
        fixture.step(run, owner, dva)
        fixture.step(run, owner, dva, defense=2)
        self.assertFalse(matrix.state(3).active)
        fixture.advance(run, owner, dva, 180)
        self.assertGreater(matrix.vars[87].value(), 1)
        fixture.step(run, owner, dva, defense=2)
        self.assertTrue(matrix.state(3).active)

    def test_matrix_cannot_consume_a_shot_through_cover(self):
        fixture, run, owner, dva, matrix = self.arena(distance=5)
        x, y, z = owner.mover.position
        wall = z + 2
        floor = [
            (x-30, y, z-30, x-30, y, z+30, x+30, y, z+30),
            (x-30, y, z-30, x+30, y, z+30, x+30, y, z-30),
        ]
        run.match.collision_world = World([*floor,
            (x-3, y, wall, x+3, y, wall, x+3, y+4, wall),
            (x-3, y, wall, x+3, y+4, wall, x-3, y+4, wall),
        ], name="matrix-cover")
        fixture.step(run, owner, dva, defense=2)
        self.assertTrue(matrix.state(3).active)
        fixture.step(run, owner, dva, fire=1, defense=2)
        # Matrix would consume this projectile at distance zero without
        # the defender-to-contact LOS check. It instead flies to the wall.
        self.assertEqual(len(run.match.combat.rockets), 1)
        before = fixture.health(run, dva).total
        fight = run.match.combat
        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            fixture.advance(run, owner, dva, 20, defense=2)
        landed.assert_called_once()
        self.assertFalse(landed.call_args.kwargs.get("intercepted", False))
        self.assertEqual(fight.rockets, [])
        self.assertEqual(fixture.health(run, dva).total, before)

    def test_matrix_consumes_actual_snowball_before_storm_landing(self):
        from ow174.game import mei_blizzard, mei_frost

        fixture, run, mei, dva, matrix = self.arena(distance=5)
        position = tuple(mei.mover.position)
        run.match.switch_hero(mei, heroes()[mei_frost.HERO])
        fixture.place(mei, position)
        mei.body_script.ult_seconds = 0
        fixture.advance(run, mei, dva, 30)
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        seen = False
        fight = run.match.combat
        for frame in range(70):
            fixture.step(run, mei, dva, fire=32 if frame == 0 else 0, defense=2)
            seen |= any(rocket.definition == mei_blizzard.DEFINITION for rocket in fight.rockets)
        self.assertTrue(seen)
        self.assertLess(matrix.vars[87].value(), 100)
        self.assertEqual(mei.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(any(rocket.definition == mei_blizzard.DEFINITION for rocket in fight.rockets))
        self.assertFalse(fight.blizzard.areas)

    def test_matrix_consumes_fusion_driver_and_release_restores_overload_hit(self):
        fixture, run, torb, dva, matrix = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        orisa = run.players[2]
        hero = next(hero for hero in heroes().values() if hero.name == "Orisa")
        self.assertTrue(match.switch_hero(orisa, hero))
        orisa.body_script.ult_seconds = 0
        orisa.team = 1 - torb.team
        dva.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(dva, (x + 1, y, z + 3))
        fixture.place(orisa, (x, y, z + 8))
        held = {dva: (0, 0, 2)}
        for _ in range(10):
            run.step(held)
        self.assertTrue(matrix.state(3).active)
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        mech = fixture.health(run, dva)
        mech_health = mech.total
        rifle = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0BD4)
        charge = orisa.body_script.component.entity_var(342)
        direction = fixture.body_aim(run, orisa, torb)
        run.step({**held, torb: (0, 0, 16), orisa: (*direction, 1)})
        grant = hp.over_armour.source
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, orisa)
        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            for _ in range(20):
                run.step(held)
        landed.assert_called_once_with(rocket, intercepted=True)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(rifle.vars[53].value(), 149)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(mech.total, mech_health)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(8):
            run.step({})
        self.assertFalse(matrix.state(3).active)
        run.step({orisa: (*direction, 1)})
        fixture.advance(run, torb, orisa, 20)
        self.assertEqual(rifle.vars[53].value(), 148)
        self.assertEqual(hp.over_armour.source, grant)
        self.assertEqual(hp.over_armour.amount, 94)
        self.assertEqual(hp.now, base)
        self.assertEqual(mech.total, mech_health)
        self.assertEqual(charge.value(), 6)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 6)

    def test_matrix_eats_graviton_before_it_can_reach_overload(self):
        fixture, run, torb, dva, matrix = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya = run.players[2]
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, hero))
        zarya.body_script.ult_seconds = 0
        zarya.team = 1 - torb.team
        dva.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(dva, (x + 1, y, z + 3))
        fixture.place(zarya, (x, y, z + 8))
        held = {dva: (0, 0, 2)}
        for _ in range(10):
            run.step(held)
        self.assertTrue(matrix.state(3).active)
        hp = fixture.health(run, torb)
        mech = fixture.health(run, dva)
        mech_health = mech.total
        cast = fixture.body_aim(run, zarya, torb)
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        run.step({**held, torb: (0, 0, 16), zarya: (*cast, 32)})
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, zarya)
        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            for _ in range(45):
                run.step(held)
        landed.assert_called_once_with(rocket, intercepted=True)
        self.assertTrue(matrix.state(3).active)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.gravitons.fields)
        self.assertFalse(fight.gravitons.pulled)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(mech.total, mech_health)
        self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(zarya.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "needs authored Halt graphs")
    def test_matrix_halt_front_deletes_orb_rear_leaves_torbjorn_pull(self):
        for front in (True, False):
            with self.subTest(front=front):
                fixture, run, torb, dva, matrix = self.arena(distance=14)
                orisa = next(player for player in run.match.players if player not in (torb, dva))
                run.match.switch_hero(orisa, heroes()[orisa_halt.HERO])
                x, y, z = dva.mover.position
                z -= 14
                fixture.place(orisa, (x, y, z))
                fixture.place(torb, (x, y, z + 6))
                yaw = -32768 if front else 0
                for _ in range(30):
                    run.step({dva: (yaw, 0, 0)})
                before = tuple(torb.mover.position)
                fight = run.match.combat
                orb = None
                for frame in range(26):
                    run.step({dva: (yaw, 0, 2), orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                    if fight.halt.orbs:
                        orb, = fight.halt.orbs.values()
                self.assertIsNotNone(orb)
                self.assertTrue(matrix.state(3).active)
                if front:
                    self.assertFalse(fight.halt.orbs)
                    self.assertIsNone(orb.requested)
                else:
                    self.assertIsNotNone(orb.requested)
                for _ in range(25):
                    run.step({})
                self.assertEqual(orb.pulled, not front)
                self.assertEqual(fixture.health(run, torb).total, 250)
                if front:
                    self.assertEqual(tuple(torb.mover.position), before)
                    self.assertNotIn(torb.body, fight.halt.pulls)
                else:
                    pull = fight.halt.pulls[torb.body]
                    destination, speed = pull.instance.state(2).move_request()
                    self.assertEqual(speed, 20)
                    self.assertGreater(destination.z, before[2] + .25)
                    for _ in range(20):
                        run.step({})
                    self.assertAlmostEqual(torb.mover.position[2], destination.z, delta=.002)
