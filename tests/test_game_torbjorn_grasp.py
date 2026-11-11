"""Torbjorn attacks settle through actual Sigma absorption/expiry requests."""

import math
import os
import struct
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import (
    combat,
    npc,
    npc_control,
    orisa_halt,
    projectile_defenses,
    sigma_flux,
    sigma_grasp,
    stats,
)
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE")
                     and os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "needs explicit graphs/tags")
class TorbjornGraspSettlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        defenses.TorbjornDefenseTests.tearDownClass()

    def absorbed(self, button=1):
        case = defenses.TorbjornDefenseTests()
        run, owner, sigma, _ = case.arena("Sigma")
        case.cast(run, owner, sigma)
        case.step(run, owner, sigma, fire=button)
        case.advance(run, owner, sigma, 20)
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertEqual(instance.vars[21312].value(), 70 if button == 1 else 125)
        hp = case.health(run, sigma)
        self.assertEqual(hp.total, 400)
        return case, run, owner, sigma, hp, instance

    def granted(self, button=1):
        case, run, owner, sigma, hp, instance = self.absorbed(button)
        case.advance(run, owner, sigma, 140)
        self.assertEqual(hp.total, 442 if button == 1 else 475)
        return case, run, owner, sigma, hp, instance

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "needs authored Halt graphs")
    def test_grasp_eats_halt_without_shield_credit_and_still_settles_real_rivet(self):
        for mode in ("front", "front_and_rivet", "rear"):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, orisa = fixture.arena("Sigma", 7)
                run.match.switch_hero(orisa, heroes()[orisa_halt.HERO])
                x, y, z = torb.mover.position
                fixture.place(orisa, (x, y, z))
                fixture.place(torb, (x, y, z + 6))
                fixture.place(sigma, (x, y, z + 9))
                front = mode != "rear"
                yaw = -32768 if front else 0
                for _ in range(35):
                    run.step({sigma: (yaw, 0, 0)})
                fight = run.match.combat
                instance = next(i for i in sigma.body_script.component.instances.values()
                                if i.graph.index == sigma_grasp.GRASP)
                before = tuple(torb.mover.position)
                orb = None
                for frame in range(26):
                    run.step({sigma: (yaw, 0, 8 if frame == 0 else 0),
                              orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                    if fight.halt.orbs:
                        orb, = fight.halt.orbs.values()
                self.assertIsNotNone(orb)
                self.assertTrue(instance.state(1).active)
                if front:
                    self.assertFalse(fight.halt.orbs)
                    self.assertIsNone(orb.requested)
                    self.assertEqual(instance.vars[6584].value(), 0)
                else:
                    self.assertIsNotNone(orb.requested)
                if mode == "front_and_rivet":
                    run.step({torb: (*fixture.body_aim(run, torb, sigma), 1), sigma: (yaw, 0, 0)})
                for _ in range(170):
                    run.step({sigma: (yaw, 0, 0)})
                self.assertEqual(fixture.health(run, torb).total, 250)
                if front:
                    self.assertFalse(orb.pulled)
                    self.assertEqual(tuple(torb.mover.position), before)
                else:
                    self.assertTrue(orb.pulled)
                    self.assertGreater(torb.mover.position[2], before[2] + .25)
                self.assertEqual(fixture.health(run, sigma).total, 442 if mode == "front_and_rivet" else 400)

    def test_primary_and_full_secondary_convert_at_authored_exit_into_a_distinct_pool(self):
        for button, bonus in ((1, 42), (2, 75)):
            with self.subTest(button=button):
                _, run, owner, sigma, hp, _ = self.granted(button)
                pool = hp.temporary_shields[sigma_grasp.SHIELD_TAG]
                self.assertEqual(pool.now, bonus)
                self.assertEqual(hp.now[combat.SHIELDS_POOL], 100)
                self.assertEqual(hp.now[combat.HEALTH_POOL], 300)
                self.assertEqual(hp.maximum, 400 + bonus)
                self.assertEqual(sigma.body_script.component.entity_var(1732).value(), 400 + bonus)
                self.assertEqual(sigma.body_script.component.entity_var(1737).value(), 400 + bonus)
                record = hp.component(hp.ids)[51]
                base = struct.unpack("<IffI4B", record[combat.SHIELDS_POOL * combat.POOLS])
                extra = struct.unpack("<IffI4B", record[combat.SHIELDS_POOL * combat.POOLS + 1])
                self.assertNotEqual(base[0], extra[0])
                self.assertEqual(extra[1:4], (bonus, bonus, combat.SHIELDS_POOL))
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertIn(sigma.body, run.match.combat.grasp_shields)

    def test_secondary_splits_at_grasp_edge_before_projected_barrier_and_body(self):
        fixture = defenses.TorbjornDefenseTests()
        run, owner, sigma, protected = fixture.arena("Sigma")
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        zarya = match.add_player(8, "Zarya", zarya_hero.guid, 1 - owner.team, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        sigma.team = protected.team = zarya.team = 1 - owner.team
        x, y, z = owner.mover.position
        fixture.place(sigma, (x + 1.05, y, z + 3.8))
        fixture.place(protected, (x, y, z + 4))
        fixture.place(zarya, (x - 4, y, z + 4))
        fixture.advance(run, owner, protected, 35)
        run.step({sigma: (-32768, 0, 8), zarya: (*fixture.body_aim(run, zarya, protected), 16)})
        pose = {sigma: (-32768, 0, 0)}
        for _ in range(2):
            run.step(pose)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.state(1).active)
        bubble = fight.bubbles.for_target(protected)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 5
        hp = fixture.health(run, protected)
        sigma_hp = fixture.health(run, sigma)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0)
        gate = projectile_defenses.projectile_gate
        caught = []

        def record(*args, **kwargs):
            hit = gate(*args, **kwargs)
            if hit is not None:
                self.assertIs(hit[1].owner, sigma)
                caught.append(hit)
            return hit

        with (
            patch.object(projectile_defenses, "projectile_gate", side_effect=record),
            patch.object(fight, "_shield_damage", wraps=fight._shield_damage) as shield_hits,
            patch.object(fight, "_damage", wraps=fight._damage) as body_hits,
        ):
            run.step({owner: (*fixture.body_aim(run, owner, protected), 2), sigma: (-32768, 0, 0)})
            self.assertEqual(len(fight.rockets), 10)
            for _ in range(16):
                run.step(pose)
        self.assertEqual(len(caught), 4)
        self.assertEqual(grasp.vars[21312].value(), 50)
        self.assertEqual([call.args[1:] for call in shield_hits.call_args_list], [(bubble, 12.5)])
        self.assertIsNone(fight.bubbles.for_target(protected))
        self.assertEqual([call.args[2] for call in body_hits.call_args_list], [12.5] * 5)
        self.assertTrue(all(call.args[1].owner is protected for call in body_hits.call_args_list))
        self.assertEqual(hp.total, 137.5)
        self.assertEqual(sigma_hp.total, 400)
        self.assertNotIn(sigma_grasp.SHIELD_TAG, sigma_hp.temporary_shields)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 62.5)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 62.5)
        self.assertFalse(fight.rockets or fight.torbjorn_marks)
        for _ in range(145):
            if not grasp.state(1).active:
                break
            run.step({})
        self.assertFalse(grasp.state(1).active)
        self.assertAlmostEqual(sigma_hp.temporary_shields[sigma_grasp.SHIELD_TAG].now, 30, places=5)
        self.assertAlmostEqual(sigma_hp.total, 430, places=5)

    def test_actual_hypersphere_impact_and_airburst_spend_overload(self):
        for mode, loss, hits in (("direct", 90, 4), ("airburst", 50, 2), ("wall", 0, 0), ("ally", 0, 0)):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 6)
                match = run.match
                fight = match.combat
                x, y, z = sigma.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                fixture.place(torb, (x, y, z + 6) if mode == "direct" else (x + 1, y, z + 22))
                if mode == "wall":
                    match.collision_world = World(
                        box(x - 35, x + 35, y - 1, y, z - 35, z + 35)
                        + wall(x + .25, z + 18, x + .25, z + 26, y0=y, y1=y + 4)
                    )
                if mode == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    torb.team = sigma.team
                run.step({torb: (0, 0, 16)})
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                self.assertEqual(hp.over_armour.amount, 100)
                direction = fixture.body_aim(run, sigma, torb) if mode == "direct" else (0, 0)
                run.step({sigma: (*direction, 1)})
                launches = set(fight.rockets)
                for _ in range(60):
                    run.step({sigma: (*direction, 0)})
                    launches.update(fight.rockets)
                self.assertEqual(len(launches), 2)
                self.assertFalse(fight.rockets)
                self.assertEqual(hp.over_armour.amount, 100 - loss)
                self.assertEqual(hp.now, base)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), loss)
                self.assertEqual(len(sigma.client.messages(combat.HIT)), hits)
                fixture.advance(run, torb, sigma, 30)
                self.assertEqual(hp.over_armour.amount, 100 - loss)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), loss)

    def test_hyperspheres_and_full_health_turret_respect_teams_range_and_cover(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, sigma, other = fixture.arena("Sigma", 100)
        match = run.match
        fight = match.combat
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sigma, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        sigma_hp = fixture.health(run, sigma)
        self.assertEqual(hp.total, 250)
        self.assertEqual(turret.shots, 0)

        x, y, z = turret.actor.position
        fixture.place(torb, (x - 12, y, z))
        match.game_map = replace(match.game_map, free_for_all=False)
        floor = box(x - 55, x + 55, y - 1, y, z - 55, z + 55)
        cover = wall(x - 6, z + 3, x + 6, z + 3, y0=y, y1=y + 4)
        hostile = 1 - turret.actor.team

        def volley():
            direction = aim(fight._eye(sigma), (x, y + .6, z))
            launches = set()
            for frame in range(100):
                run.step({sigma: (*direction, 1 if frame == 0 else 0)})
                launches.update(fight.rockets)
                if hp.dead:
                    break
            self.assertEqual(len(launches), 1 if hp.dead else 2)

        for mode, team, distance in (("ally", turret.actor.team, 6), ("range", hostile, 45),
                                     ("wall", hostile, 6)):
            with self.subTest(mode=mode):
                sigma.team = team
                fixture.place(sigma, (x, y, z + distance))
                match.collision_world = World(floor + cover if mode == "wall" else floor)
                volley()
                self.assertFalse(fight.rockets)
                self.assertEqual(hp.total, 250)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(turret.shots, 0)
                self.assertEqual(sigma_hp.total, 400)

        match.collision_world = World(floor)
        for remaining in (140, 30, 0):
            volley()
            self.assertEqual(hp.total, remaining)
        hits = sigma.client.messages(combat.HIT)
        self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == turret.actor.entity for hit in hits))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [25, 30] * 4 + [25, 5])
        self.assertEqual(hits[-1]["+0x78"]["+0x8"], combat.KILLING)
        self.assertEqual(hp.killer, sigma.body)
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 250)
        self.assertEqual(sigma.stats.value(stats.ELIMINATIONS), 1)
        self.assertGreater(turret.shots, 0)
        self.assertGreater(sigma_hp.total, 0)
        self.assertEqual(sigma_hp.total, 400 - 14 * turret.shots)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 14 * turret.shots)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertNotIn(torb.entity, fight.turrets)
        ability = next(i for i in torb.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).state(13)
        self.assertGreater(ability.remaining(), 9.9)
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sigma, 15)
        self.assertNotIn(torb.entity, fight.turrets)
        fixture.place(sigma, (x + 45, y, z + 45))
        for _ in range(650):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(fight.rockets)
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sigma, 225)
        replacement = fight.turrets[torb.entity]
        self.assertNotEqual(replacement.actor.entity, turret.actor.entity)
        self.assertAlmostEqual(fight.vitals[replacement.actor.entity].total, 250)
        self.assertEqual(replacement.shots, 0)
        x, y, z = replacement.actor.position
        fixture.place(sigma, (x, y, z + 6))
        before = sigma_hp.total
        for _ in range(30):
            run.step({})
            if replacement.shots:
                break
        self.assertEqual(replacement.shots, 1)
        self.assertEqual(sigma_hp.total, before - 14)

    def test_hyperspheres_rebound_off_wall_and_hit_overload_behind_sigma(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, sigma, other = fixture.arena("Sigma")
        match = run.match
        fight = match.combat
        x, y, z = sigma.mover.position
        fixture.place(torb, (x, y, z - 2))
        fixture.place(other, (x + 100, y, z + 100))
        match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + wall(x - 4, z + 6, x + 4, z + 6, y0=y, y1=y + 4)
        )
        run.step({torb: (0, 0, 16)})
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        self.assertEqual(hp.total, 350)
        returned = set()
        direction = aim(fight._eye(sigma), (x, y + 1.8, z + 6))
        for frame in range(65):
            run.step({sigma: (*direction, 1 if frame == 0 else 0)})
            for rocket in fight.rockets:
                if rocket.direction[2] < 0:
                    self.assertLess(rocket.position[2], z + 5.95)
                    self.assertAlmostEqual(rocket.speed, 35, delta=.15)
                    self.assertEqual(rocket.bounces, 99)
                    self.assertLess(rocket.left, .44)
                    returned.add(rocket)
        self.assertEqual(len(returned), 2)
        self.assertFalse(fight.rockets)
        self.assertEqual(hp.over_armour.amount, 10)
        self.assertEqual(hp.now, base)
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 90)
        self.assertEqual(len(sigma.client.messages(combat.HIT)), 4)

    def test_hypersphere_radius_reaches_torbjorn_outside_centre_ray(self):
        for offset, damage in ((.60, 90), (.85, 0)):
            with self.subTest(offset=offset):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma")
                fight = run.match.combat
                x, y, z = sigma.mover.position
                fixture.place(torb, (x + offset, y, z + 6))
                fixture.place(other, (x + 100, y, z + 100))
                run.step({torb: (0, 0, 16)})
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                target = next(target for target in fight._targets() if target.owner is torb)
                self.assertGreater(offset, target.radius)
                direction = aim(fight._eye(sigma), (x, y + target.height * .5, z + 6))
                for frame in range(60):
                    run.step({sigma: (*direction, 1 if frame == 0 else 0)})
                self.assertFalse(fight.rockets)
                self.assertEqual(hp.over_armour.amount, 100 - damage)
                self.assertEqual(hp.now, base)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(len(sigma.client.messages(combat.HIT)), 4 if damage else 0)

    def test_deflected_hyperspheres_airburst_with_genjis_team_and_credit(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, genji, sigma = fixture.arena("Genji", 6)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        torb.team = sigma.team = 1 - genji.team
        self.assertTrue(match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        x, y, z = torb.mover.position
        fixture.place(sigma, (x, y, z))
        fixture.place(torb, (x + 5.15, y, z - 10.7))
        fixture.advance(run, sigma, genji, 35)
        redirect = aim(fight._eye(genji), (x + 4, y + 1.65, z - 10))
        launch = fixture.body_aim(run, sigma, genji)
        run.step({torb: (0, 0, 16), genji: (*redirect, 16)})
        hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 350)
        base = dict(hp.now)

        reflected = set()
        for frame in range(68):
            run.step({sigma: (*launch, 1 if frame == 2 else 0), genji: (*redirect, 0)})
            reflected.update(rocket for rocket in fight.rockets if rocket.shooter is genji)
        self.assertEqual(len(reflected), 2)
        self.assertTrue(all(rocket.airburst and rocket.team == genji.team for rocket in reflected))
        self.assertFalse(fight.rockets)
        self.assertEqual(hp.over_armour.amount, 50)
        self.assertEqual(hp.now, base)
        self.assertEqual(fixture.health(run, sigma).total, 400)
        self.assertEqual(fixture.health(run, genji).total, 200)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 2)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 50)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 50)
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)

    def test_flux_ground_confirmation_rejects_invalid_aim_then_confirms_or_expires(self):
        for confirm in (True, False):
            with self.subTest(confirm=confirm):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 8)
                match = run.match
                fight = match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                floor = box(x - 55, x + 55, y - 1, y, z - 55, z + 55)
                match.collision_world = World(floor)
                component = sigma.body_script.component
                flux = next(i for i in component.instances.values() if i.graph.index == sigma_flux.CAST)
                ability = flux.node_state(flux.graph.node(3))
                ground = flux.node_state(flux.graph.node(23))
                good = aim(fight._eye(sigma), (x, y, z))

                cost = component.entity_var(338).value()
                sigma.body_script.set_ult_charge(cost - 1)
                run.step({sigma: (*good, 32)})
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertEqual(component.entity_var(342).value(), cost - 1)
                run.step({})
                sigma.body_script.set_ult_charge(cost)
                run.step({sigma: (*good, 32), torb: (0, 0, 16)})
                self.assertTrue(ability.flags & ability.RUNNING)
                self.assertEqual(component.entity_var(342).value(), 0)
                hp = fixture.health(run, torb)
                self.assertEqual(hp.total, 350)
                for _ in range(50):
                    if ground.active:
                        break
                    run.step({sigma: (*good, 0)})
                self.assertTrue(ground.active)

                behind_wall = wall(x - 3, z + 4, x + 3, z + 4, y0=y, y1=y + 4)
                far = aim(fight._eye(sigma), (x, y, z - 37))
                invalid_aims = (((good[0], -4096), floor), (far, floor), (good, floor + behind_wall))
                for direction, geometry in invalid_aims:
                    match.collision_world = World(geometry)
                    run.step({sigma: (*direction, 1)})
                    self.assertEqual(flux.vars[59].value(), 1)
                    self.assertTrue(ground.active)
                    self.assertNotIn(2478, flux.vars)
                    self.assertTrue(flux.vars[6732].value())
                    self.assertFalse(fight.rockets)
                    run.step({sigma: (*direction, 0)})

                match.collision_world = World(floor)
                if confirm:
                    run.step({sigma: (*good, 1)})
                    self.assertEqual(flux.vars[59].value(), 2)
                    self.assertFalse(ground.active)
                    self.assertTrue(flux.node_state(flux.graph.node(31)).active)
                    point = flux.vars[2478].value()
                    for actual, expected in zip((point.x, point.y, point.z), (x, y, z), strict=True):
                        self.assertAlmostEqual(actual, expected, delta=.02)
                    run.step({sigma: (*good, 0)})
                    run.step({sigma: (0, 0, 1)})
                    self.assertEqual(flux.vars[2478].value(), point)
                else:
                    for _ in range(360):
                        if not ability.flags & ability.RUNNING:
                            break
                        run.step({sigma: (*good, 0)})
                    self.assertFalse(ability.flags & ability.RUNNING)
                    self.assertFalse(ground.active)
                    self.assertNotIn(2478, flux.vars)
                self.assertIsNone(component.fault)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)

    def test_flux_warning_selects_enemies_and_settles_lift_and_landing(self):
        modes = ("inside", "outside", "cover", "ally", "lethal", "slam_lethal",
                 "caster_death", "caster_switch")
        for mode in modes:
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 8)
                match = run.match
                fight = match.combat
                x, y, z = torb.mover.position
                match.game_map = replace(match.game_map, free_for_all=False)
                sigma.team = 1 - torb.team
                other.team = torb.team
                fixture.place(other, (x + 100, y, z + 100))
                floor = box(x - 45, x + 45, y - 1, y, z - 45, z + 45)
                cover = wall(x + 2, z - 4, x + 2, z + 4, y0=y, y1=y + 4)
                match.collision_world = World(floor + cover if mode == "cover" else floor)
                component = sigma.body_script.component
                flux = next(i for i in component.instances.values() if i.graph.index == sigma_flux.CAST)
                recipient = torb.body_script.component
                hp = fixture.health(run, torb)
                if mode in ("lethal", "slam_lethal"):
                    remaining = 1 if mode == "lethal" else 100
                    hp.damage(hp.total - remaining, armour_reduction=False)
                    fight._sync_body_health(torb, hp)
                    self.assertEqual(hp.total, remaining)
                if mode == "inside":
                    fixture.place(torb, (x + 18, y, z))
                sigma.body_script.set_ult_charge(component.entity_var(338).value())
                direction = aim(fight._eye(sigma), (x, y, z))
                use_overload = mode not in ("lethal", "slam_lethal")
                run.step({sigma: (*direction, 32), torb: (0, 0, 16 if use_overload else 0)})
                base = dict(hp.now)
                for _ in range(45):
                    run.step({sigma: (*direction, 0)})
                run.step({sigma: (*direction, 1)})
                self.assertEqual(flux.vars[59].value(), 2)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)
                if mode == "ally":
                    torb.team = sigma.team
                # Move after confirmation so the warning's end determines membership.
                fixture.place(torb, (x + (15.2 if mode == "outside" else 14.8), y, z))
                if mode == "inside":
                    fixture.place(other, (x - 6, y, z))
                other_hp = fixture.health(run, other)
                other_before = other_hp.total
                warning = flux.node_state(flux.graph.node(31))
                for _ in range(80):
                    if not warning.active:
                        break
                    run.step({})
                self.assertFalse(warning.active)
                for _ in range(2):
                    run.step({})
                effect = [i for i in recipient.instances.values() if i.graph.index == sigma_flux.EFFECT]
                self.assertIsNone(recipient.fault)
                self.assertIsNone(component.fault)
                if mode == "inside":
                    hit, = effect
                    self.assertEqual(hit.source_entity(), sigma.body)
                    self.assertEqual(flux.vars[59].value(), 3)
                    self.assertEqual(hp.over_armour.amount, 55)
                    self.assertEqual(hp.now, base)
                    self.assertEqual(other_hp.total, other_before - 50)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 95)
                    apex = torb.mover.position[1]
                    for _ in range(200):
                        self.assertEqual(hp.total, 305)
                        run.step({})
                        apex = max(apex, torb.mover.position[1])
                        if hp.total < 305:
                            break
                    self.assertAlmostEqual(apex - y, 7, delta=.01)
                    self.assertTrue(torb.mover.landed)
                    self.assertAlmostEqual(torb.mover.position[1], y, delta=.01)
                    self.assertEqual(hp.total, 185)
                    self.assertEqual(hp.over_armour.amount, 0)
                    self.assertEqual(hp.now[combat.HEALTH_POOL], base[combat.HEALTH_POOL] - 15)
                    self.assertEqual(hp.now[combat.ARMOUR_POOL], 0)
                    for _ in range(400):
                        if hit.id not in recipient.instances:
                            break
                        run.step({})
                    self.assertNotIn(hit.id, recipient.instances)
                    self.assertFalse(torb.mover.driven)
                    self.assertEqual(other_hp.total, other_before - 150)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 315)
                elif mode == "lethal":
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, sigma.body)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 1)
                    self.assertEqual(sigma.stats.value(stats.ELIMINATIONS), 1)
                elif mode == "slam_lethal":
                    self.assertEqual(hp.total, 50)
                    for _ in range(200):
                        run.step({})
                        if hp.dead:
                            break
                    self.assertTrue(hp.dead)
                    self.assertTrue(torb.mover.landed)
                    self.assertEqual(hp.killer, sigma.body)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 100)
                    self.assertEqual(sigma.stats.value(stats.ELIMINATIONS), 1)
                elif mode in ("caster_death", "caster_switch"):
                    hit, = effect
                    caster_died = mode == "caster_death"
                    self.assertEqual(hp.total, 305)
                    for _ in range(12):
                        run.step({})
                    self.assertTrue(torb.mover.airborne)
                    if caster_died:
                        sigma_hp = fixture.health(run, sigma)
                        sigma_hp.damage(sigma_hp.total - 1, armour_reduction=False)
                        fight._sync_body_health(sigma, sigma_hp)
                        for _ in range(35):
                            fixture.step(run, torb, sigma, fire=1)
                            if sigma_hp.dead:
                                break
                        self.assertTrue(sigma_hp.dead)
                        self.assertEqual(sigma_hp.killer, torb.body)
                    else:
                        self.assertTrue(match.switch_hero(sigma, heroes()[SOLDIER]))
                        run.step({})
                        self.assertNotIn(hit.id, recipient.instances)
                    for _ in range(300):
                        if hit.id not in recipient.instances and not torb.mover.airborne:
                            break
                        run.step({})
                    self.assertNotIn(hit.id, recipient.instances)
                    self.assertFalse(torb.mover.airborne)
                    self.assertEqual(hp.total, 185 if caster_died else 305)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 165 if caster_died else 45)
                else:
                    self.assertFalse(effect)
                    self.assertEqual(flux.vars[59].value(), 4)
                    self.assertEqual(hp.over_armour.amount, 100)
                    self.assertEqual(hp.now, base)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)

    def test_graviton_pulls_horizontally_without_changing_flux_lift_or_landing(self):
        baseline_landing, baseline_path = self.flux_vertical_path_during_graviton(False)
        landing, path = self.flux_vertical_path_during_graviton(True)
        self.assertEqual(landing, baseline_landing)
        self.assertEqual(path, baseline_path)

    def flux_vertical_path_during_graviton(self, graviton):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, sigma, zarya = fixture.arena("Sigma", 8)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, hero))
        zarya.body_script.ult_seconds = 0
        sigma.team = zarya.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(zarya, (x + 4, y, z - 5))
        fixture.advance(run, torb, sigma, 35)
        hp = fixture.health(run, torb)
        component = sigma.body_script.component
        recipient = torb.body_script.component
        flux = next(i for i in component.instances.values() if i.graph.index == sigma_flux.CAST)
        sigma.body_script.set_ult_charge(component.entity_var(338).value())
        direction = aim(fight._eye(sigma), (x, y, z))
        run.step({torb: (0, 0, 16), sigma: (*direction, 32)})
        for _ in range(45):
            run.step({sigma: (*direction, 0)})
        commands = {sigma: (*direction, 1)}
        if graviton:
            zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
            commands[zarya] = (*aim(fight._eye(zarya), (x + 4, y, z)), 32)
        run.step(commands)
        self.assertEqual(flux.vars[59].value(), 2)
        confirmed = run.frame
        trace = []
        pulled_in_air = False
        field_seen = False
        for _ in range(290):
            before = torb.mover.position[0]
            run.step({})
            mx, my, _ = torb.mover.position
            field_seen |= bool(fight.gravitons.fields)
            if torb.mover.airborne and torb.body in fight.gravitons.pulled and mx > before:
                pulled_in_air = True
            trace.append((my, torb.mover.airborne))
            sigma_damage = sigma.stats.value(stats.HERO_DAMAGE)
            if sigma_damage > 45:
                self.assertTrue(torb.mover.landed)
                self.assertAlmostEqual(my, y, delta=.01)
                break
        self.assertEqual(sigma_damage, 165)
        self.assertAlmostEqual(max(height for height, _ in trace) - y, 7, delta=.01)
        self.assertEqual(pulled_in_air, graviton)
        self.assertEqual(field_seen, graviton)
        zarya_damage = zarya.stats.value(stats.HERO_DAMAGE)
        if graviton:
            self.assertGreater(zarya_damage, 0)
            self.assertLess(zarya_damage, 20)
        self.assertAlmostEqual(hp.total, 350 - 165 - zarya_damage)
        landing = run.frame - confirmed
        for _ in range(400):
            if not fight.gravitons.fields and not any(
                    i.graph.index == sigma_flux.EFFECT for i in recipient.instances.values()):
                break
            run.step({})
        self.assertFalse(fight.gravitons.fields)
        self.assertFalse(fight.gravitons.pulled)
        self.assertFalse(torb.mover.driven)
        self.assertFalse(any(i.graph.index == sigma_flux.EFFECT for i in recipient.instances.values()))
        final_graviton_damage = zarya.stats.value(stats.HERO_DAMAGE)
        if graviton:
            self.assertGreater(final_graviton_damage, zarya_damage)
            self.assertLess(final_graviton_damage, 20)
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 165)
        self.assertAlmostEqual(hp.total, 350 - 165 - final_graviton_damage)
        return landing, trace

    def test_flux_warning_cancels_on_lethal_hits_flashbang_or_body_switch(self):
        def confirm(run, torb, sigma):
            component = sigma.body_script.component
            sigma.body_script.set_ult_charge(component.entity_var(338).value())
            direction = aim(run.match.combat._eye(sigma), tuple(torb.mover.position))
            run.step({sigma: (*direction, 32), torb: (0, 0, 16)})
            for _ in range(45):
                run.step({sigma: (*direction, 0)})
            run.step({sigma: (*direction, 1)})
            flux = next(i for i in component.instances.values() if i.graph.index == sigma_flux.CAST)
            self.assertEqual(component.entity_var(342).value(), 0)
            self.assertEqual(flux.vars[59].value(), 2)
            return flux.node_state(flux.graph.node(31))

        for mode in ("rivet", "turret", "flashbang", "switch"):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 100 if mode == "turret" else 8)
                match = run.match
                fight = match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                if mode == "turret":
                    run.step({torb: (0, 4000, 8)})
                    fixture.advance(run, torb, sigma, 225)
                    turret = fight.turrets[torb.entity]
                    self.assertEqual(turret.shots, 0)
                    x, y, z = turret.actor.position
                    fixture.place(torb, (x - 3, y, z))
                    fixture.place(sigma, (x, y, z + 8))

                hp = fixture.health(run, torb)
                base = dict(hp.now)
                old_body, old_script = sigma.body, sigma.body_script
                warning = confirm(run, torb, sigma)
                self.assertTrue(warning.active)
                confirmed = run.frame
                if mode == "switch":
                    self.assertTrue(match.switch_hero(sigma, heroes()[SOLDIER]))
                    self.assertNotEqual(sigma.body, old_body)
                    self.assertTrue(old_script.retired)
                elif mode == "flashbang":
                    self.assertTrue(match.switch_hero(other, heroes()[0x02E0000000000042]))
                    sx, sy, sz = sigma.mover.position
                    fixture.place(other, (sx + 3, sy, sz))
                    direction = fixture.body_aim(run, other, sigma)
                    run.step({other: (*direction, 16)})
                    for _ in range(35):
                        if fight.is_stunned(sigma):
                            break
                        run.step({})
                    self.assertTrue(fight.is_stunned(sigma))
                    self.assertFalse(warning.active)
                else:
                    sigma_hp = fixture.health(run, sigma)
                    sigma_hp.damage(sigma_hp.total - 1, armour_reduction=False)
                    fight._sync_body_health(sigma, sigma_hp)
                    credit = torb.stats.value(stats.HERO_DAMAGE)
                    if mode == "rivet":
                        fixture.advance(run, torb, sigma, 45)
                        self.assertTrue(warning.active)
                        fixture.step(run, torb, sigma, fire=1)
                    else:
                        shots = turret.shots
                    for _ in range(35):
                        if sigma_hp.dead:
                            break
                        run.step({})
                    self.assertTrue(sigma_hp.dead)
                    self.assertEqual(sigma_hp.killer, torb.body)
                    self.assertFalse(warning.active)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, 1)
                    self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                    if mode == "turret":
                        self.assertEqual(turret.shots, shots + 1)
                    else:
                        self.assertGreater(sigma_hp.death_tick - confirmed, 45)
                        self.assertLess(sigma_hp.death_tick - confirmed, 63)

                for _ in range(95):
                    run.step({sigma: (0, 0, 32 | 1)} if mode in ("rivet", "turret") else {})
                    self.assertFalse(torb.mover.airborne)
                    self.assertFalse(any(i.graph.index == sigma_flux.EFFECT
                                         for i in torb.body_script.component.instances.values()))
                if mode != "switch":
                    self.assertFalse(warning.active)
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(hp.now, base)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)

                if mode == "rivet":
                    for _ in range(700):
                        if sigma.body != old_body:
                            break
                        run.step({})
                    self.assertNotEqual(sigma.body, old_body)
                    self.assertTrue(old_script.retired)
                    fixture.place(sigma, (x, y, z + 8))
                    fixture.advance(run, torb, sigma, 20)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)
                    fresh = confirm(run, torb, sigma)
                    self.assertTrue(fresh.active)
                    for _ in range(80):
                        if sigma.stats.value(stats.HERO_DAMAGE):
                            break
                        run.step({})
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 45)
                    self.assertEqual(hp.over_armour.amount, 0)
                    self.assertEqual(hp.now[combat.ARMOUR_POOL], base[combat.ARMOUR_POOL] - 45)
                    effect, = [i for i in torb.body_script.component.instances.values()
                               if i.graph.index == sigma_flux.EFFECT]
                    self.assertEqual(effect.source_entity(), sigma.body)

    def test_turret_and_molten_kill_sigma_during_flux_without_cancelling_slam(self):
        for deploy_turret in (True, False):
            with self.subTest(deploy_turret=deploy_turret):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 100)
                fight = run.match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                if deploy_turret:
                    run.step({torb: (0, 4000, 8)})
                    fixture.advance(run, torb, sigma, 225)
                    turret = fight.turrets[torb.entity]
                    x, y, z = turret.actor.position
                    fixture.place(torb, (x - 3, y, z))
                else:
                    torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
                    run.step({torb: (0, 4000, 32)})
                    fixture.advance(run, torb, sigma, 45)
                    run.step({torb: (0, 4000, 1)})
                    for _ in range(100):
                        if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                            break
                        run.step({})
                    pool, = fight.molten_pools.values()
                    self.assertEqual(pool.phase, 1)
                    x, y, z = pool.position
                    fixture.place(torb, (x, y, z))

                fixture.place(sigma, (x, y, z + 8))
                sigma.body_script.set_ult_charge(sigma.body_script.component.entity_var(338).value())
                direction = aim(fight._eye(sigma), tuple(torb.mover.position))
                run.step({sigma: (*direction, 32)})
                for _ in range(45):
                    run.step({sigma: (*direction, 0)})
                run.step({sigma: (*direction, 1)})
                hp = fixture.health(run, torb)
                for _ in range(90):
                    if torb.mover.airborne:
                        break
                    run.step({})
                self.assertTrue(torb.mover.airborne)
                self.assertEqual(hp.total, 205)
                effect, = [i for i in torb.body_script.component.instances.values()
                           if i.graph.index == sigma_flux.EFFECT]
                source = sigma.body
                self.assertEqual(effect.source_entity(), source)

                sigma_hp = fixture.health(run, sigma)
                before = sigma_hp.total
                credit = torb.stats.value(stats.HERO_DAMAGE)
                if deploy_turret:
                    shots = turret.shots
                else:
                    fixture.place(sigma, pool.position)
                for _ in range(30):
                    if sigma_hp.total < before:
                        break
                    run.step({})
                tick_damage = 14 if deploy_turret else 2.56
                self.assertAlmostEqual(before - sigma_hp.total, tick_damage)
                self.assertTrue(torb.mover.airborne)
                if deploy_turret:
                    self.assertEqual(turret.shots, shots + 1)
                else:
                    self.assertIn((torb.entity, source), fight.molten_burns)

                sigma_hp.damage(sigma_hp.total - 1, armour_reduction=False)
                fight._sync_body_health(sigma, sigma_hp)
                for _ in range(30):
                    if sigma_hp.dead:
                        break
                    run.step({})
                self.assertTrue(sigma_hp.dead)
                self.assertTrue(torb.mover.airborne)
                self.assertEqual(sigma_hp.killer, torb.body)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, tick_damage + 1)
                self.assertEqual(hp.total, 205)

                if not deploy_turret:
                    for _ in range(20):
                        if torb.mover.position[1] > y + 2:
                            break
                        run.step({})
                    self.assertGreater(torb.mover.position[1], y + 2)
                    run.step({torb: (16384, 4000, 1)})
                    for _ in range(50):
                        if len(fight.molten_pools) == 2:
                            break
                        run.step({})
                    self.assertEqual(len(fight.molten_pools), 2)
                    self.assertTrue(torb.mover.airborne)

                component = torb.body_script.component
                for _ in range(200):
                    if effect.id not in component.instances:
                        break
                    run.step({})
                self.assertNotIn(effect.id, component.instances)
                self.assertFalse(torb.mover.airborne)
                self.assertEqual(hp.total, 85)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 165)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, tick_damage + 1)
                if deploy_turret:
                    self.assertIs(fight.turrets[torb.entity], turret)
                    self.assertEqual(turret.shots, shots + 2)
                else:
                    self.assertIn(pool, fight.molten_pools.values())
                    self.assertNotIn((torb.entity, source), fight.molten_burns)

    def test_flux_checks_projected_barrier_independently_at_lift_and_landing(self):
        for mode in ("early", "early_break", "late", "break", "enemy"):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, zarya = fixture.arena("Sigma", 8)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                torb.team = 1 - sigma.team
                zarya.team = sigma.team if mode == "enemy" else torb.team
                self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                x, y, z = torb.mover.position
                fixture.place(zarya, (x + 16, y, z))
                fixture.advance(run, torb, sigma, 25)
                sigma.body_script.set_ult_charge(sigma.body_script.component.entity_var(338).value())
                direction = aim(fight._eye(sigma), (x, y, z))
                run.step({sigma: (*direction, 32), torb: (0, 0, 16)})
                fixture.advance(run, torb, sigma, 45)
                run.step({sigma: (*direction, 1)})
                hp = fixture.health(run, torb)
                self.assertEqual(hp.total, 350)
                early = mode in ("early", "early_break")
                if early:
                    fixture.advance(run, torb, sigma, 50)
                    run.step({zarya: (*fixture.body_aim(run, zarya, torb), 16)})
                    bubble = fight.bubbles.for_target(torb)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                    if mode == "early_break":
                        bubble.health = 20
                for _ in range(90):
                    if torb.mover.airborne:
                        break
                    run.step({})
                self.assertTrue(torb.mover.airborne)
                self.assertEqual(hp.total, 350 if early else 305)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0 if early else 45)
                energy_var = zarya.body_script.component.entity_var(87)
                if early:
                    self.assertEqual(bubble.health, 150 if mode == "early" else 0)
                    self.assertEqual(energy_var.value(), 10 if mode == "early" else 4)
                else:
                    for _ in range(60):
                        run.step({})
                    self.assertGreater(torb.mover.position[1], y + 6.9)
                    run.step({zarya: (*fixture.body_aim(run, zarya, torb), 16)})
                    bubble = fight.bubbles.for_target(torb)
                    if mode == "enemy":
                        self.assertIsNone(bubble)
                    else:
                        self.assertIsNotNone(bubble)
                        self.assertIs(bubble.caster, zarya)
                        self.assertEqual(bubble.health, 200)
                        if mode == "break":
                            bubble.health = 20

                for _ in range(170):
                    run.step({})
                    if torb.mover.landed:
                        break
                self.assertTrue(torb.mover.landed)
                capacity, remaining, damage, energy = {
                    "early": (150, 230, 120, 10),
                    "early_break": (0, 230, 120, 4),
                    "late": (75, 305, 45, 25),
                    "break": (0, 305, 45, 4),
                    "enemy": (None, 185, 165, 0),
                }[mode]
                self.assertEqual(hp.total, remaining)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), damage)
                if bubble is not None:
                    self.assertEqual(bubble.health, capacity)
                self.assertIs(fight.bubbles.for_target(torb), bubble if mode == "late" else None)
                if early:
                    self.assertGreater(energy_var.value(), 0)
                    self.assertLess(energy_var.value(), energy)
                else:
                    self.assertAlmostEqual(energy_var.value() or 0, energy)

    def test_flux_lift_changes_hammer_repair_reach_at_contact(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, sigma, other = fixture.arena("Sigma", 100)
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sigma, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        x, y, z = turret.actor.position
        fixture.place(torb, (x - 12, y, z))
        fixture.place(sigma, (x, y, z + 8))
        direction = aim(fight._eye(sigma), (x, y + .6, z))
        run.step({sigma: (*direction, 1)})
        for _ in range(65):
            run.step({sigma: (*direction, 0)})
        self.assertEqual(hp.total, 140)
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 110)

        fixture.place(torb, (x - 1.25, y, z))
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, sigma, 45)
        sigma.body_script.set_ult_charge(sigma.body_script.component.entity_var(338).value())
        direction = aim(fight._eye(sigma), tuple(torb.mover.position))
        run.step({sigma: (*direction, 32)})
        fixture.advance(run, torb, sigma, 45)
        run.step({sigma: (*direction, 1)})
        fixture.advance(run, torb, sigma, 47)

        # Start the swing on the floor and sample its actual contact after lift begins.
        self.assertFalse(torb.mover.airborne)
        run.step({torb: (-32768, 0, 1)})
        for _ in range(35):
            if hp.total > 140:
                break
            run.step({})
        self.assertTrue(torb.mover.airborne)
        self.assertGreater(torb.mover.position[1], y)
        self.assertEqual(hp.total, 190)
        first = fight.hammer_contacts[torb.body]
        self.assertEqual(first.repaired, {turret.actor.entity})

        # A fresh swing at the top uses the raised repair origin.
        fixture.advance(run, torb, sigma, 55)
        self.assertGreater(torb.mover.position[1], y + 6.9)
        run.step({torb: (0, 8192, 1)})
        sampled = False
        for _ in range(35):
            run.step({})
            self.assertTrue(torb.mover.airborne)
            self.assertEqual(hp.total, 190)
            contact = fight.hammer_contacts.get(torb.body)
            now = torb.body_script.component.now
            if contact is not None and contact.begin <= now < contact.end:
                self.assertNotEqual(contact.counter, first.counter)
                self.assertFalse(contact.repaired)
                sampled = True
        self.assertTrue(sampled)

        for _ in range(100):
            run.step({})
            if torb.mover.landed:
                break
        self.assertTrue(torb.mover.landed)
        self.assertEqual(fixture.health(run, torb).total, 85)
        self.assertEqual(hp.total, 190)
        fixture.advance(run, torb, sigma, 25)
        run.step({torb: (-32768, 0, 1)})
        fixture.advance(run, torb, sigma, 35)
        self.assertEqual(hp.total, 240)
        self.assertIs(fight.turrets[torb.entity], turret)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 14 * turret.shots)

    def test_flux_slam_uses_overload_mounted_before_the_landing_frame(self):
        landing_offset = None
        for activate_in_air in (True, False):
            with self.subTest(activate_in_air=activate_in_air):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 8)
                fight = run.match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                hp = fixture.health(run, torb)
                sigma.body_script.set_ult_charge(sigma.body_script.component.entity_var(338).value())
                direction = aim(fight._eye(sigma), (x, y, z))
                run.step({sigma: (*direction, 32)})
                fixture.advance(run, torb, sigma, 45)
                run.step({sigma: (*direction, 1)})
                confirmed = run.frame
                for _ in range(100):
                    if torb.mover.airborne:
                        break
                    run.step({})
                self.assertTrue(torb.mover.airborne)
                self.assertEqual(hp.total, 205)
                self.assertEqual(hp.now[combat.ARMOUR_POOL], 5)
                self.assertIsNone(hp.over_armour.source)

                if activate_in_air:
                    for _ in range(60):
                        run.step({})
                    self.assertGreater(torb.mover.position[1], y + 6.9)
                    run.step({torb: (0, 0, 16)})
                    self.assertTrue(torb.mover.airborne)
                    self.assertEqual(hp.over_armour.amount, 100)
                    for _ in range(100):
                        run.step({})
                        if torb.mover.landed:
                            break
                    landing_offset = run.frame - confirmed
                else:
                    self.assertIsNotNone(landing_offset)
                    while run.frame < confirmed + landing_offset - 1:
                        run.step({})
                    self.assertTrue(torb.mover.airborne)
                    self.assertEqual(hp.total, 205)
                    run.step({torb: (0, 0, 16)})
                self.assertTrue(torb.mover.landed)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 165)
                hits = sigma.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [45, 120])
                self.assertEqual(hp.total, 185)
                self.assertEqual(hp.now[combat.ARMOUR_POOL], 0)
                self.assertEqual(hp.now[combat.HEALTH_POOL], 185 if activate_in_air else 85)
                self.assertEqual(hp.over_armour.amount, 0 if activate_in_air else 100)
                source = hp.over_armour.source
                self.assertIsNotNone(source)

                for _ in range(16):
                    run.step({})
                    run.step({torb: (0, 0, 16)})
                    self.assertEqual(hp.over_armour.source, source)
                    self.assertEqual(hp.over_armour.amount, 0 if activate_in_air else 100)
                if activate_in_air:
                    ability = next(i for i in torb.body_script.component.instances.values()
                                   if i.graph.index == 0x1721).state(19)
                    for _ in range(350):
                        if hp.over_armour.source is None:
                            break
                        run.step({})
                    self.assertIsNone(hp.over_armour.source)
                    self.assertGreater(ability.remaining(), 0)
                    run.step({torb: (0, 0, 16)})
                    fixture.advance(run, torb, sigma, 20)
                    self.assertIsNone(hp.over_armour.source)
                    self.assertEqual(hp.total, 185)
                    for _ in range(650):
                        if ability.remaining() <= 0:
                            break
                        run.step({})
                    self.assertEqual(ability.remaining(), 0)
                    run.step({torb: (0, 0, 16)})
                    self.assertEqual(hp.over_armour.source, (source[0], source[1] + 1))
                    self.assertEqual(hp.over_armour.amount, 100)

    def test_flux_recipient_death_and_body_replacement_retire_the_lift(self):
        for mode in ("death", "reselect", "switch"):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 8)
                match = run.match
                fight = match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                old_body, old_script = torb.body, torb.body_script
                hp = fixture.health(run, torb)
                sigma.body_script.set_ult_charge(sigma.body_script.component.entity_var(338).value())
                direction = aim(fight._eye(sigma), (x, y, z))
                run.step({sigma: (*direction, 32)})
                fixture.advance(run, torb, sigma, 45)
                run.step({sigma: (*direction, 1)})
                for _ in range(100):
                    if torb.mover.position[1] > y + 2:
                        break
                    run.step({})
                self.assertTrue(torb.mover.airborne)
                self.assertTrue(torb.mover.driven)
                self.assertGreater(torb.mover.position[1], y + 2)
                self.assertEqual(hp.total, 205)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 45)
                effect, = [i for i in old_script.component.instances.values()
                           if i.graph.index == sigma_flux.EFFECT]

                if mode == "death":
                    hp.damage(hp.total - 1, armour_reduction=False)
                    fight._sync_body_health(torb, hp)
                    fixture.place(other, (x + 6, y, z + 3))
                    direction = fixture.body_aim(run, other, torb)
                    run.step({other: (*direction, 1)})
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, other.body)
                    self.assertEqual(other.stats.value(stats.HERO_DAMAGE), 1)
                    self.assertEqual(other.stats.value(stats.ELIMINATIONS), 1)
                    self.assertNotIn(effect.id, old_script.component.instances)
                    self.assertFalse(torb.mover.driven)
                    self.assertEqual(torb.mover.velocity, (0, 0, 0))
                    corpse = tuple(torb.mover.position)
                    for _ in range(45):
                        run.step({torb: (0, 0, 1 | 16)})
                        self.assertEqual(tuple(torb.mover.position), corpse)
                        self.assertIsNone(hp.over_armour.source)
                    for _ in range(660):
                        if torb.body != old_body:
                            break
                        run.step({})
                else:
                    hero = torb.hero if mode == "reselect" else heroes()[SOLDIER]
                    self.assertTrue(match.switch_hero(torb, hero))

                self.assertNotEqual(torb.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertNotIn(effect.id, old_script.component.instances)
                new_hp = fixture.health(run, torb)
                full = 200 if mode == "switch" else 250
                for _ in range(200):
                    run.step({})
                    self.assertEqual(new_hp.total, full)
                    self.assertFalse(torb.mover.driven)
                    self.assertFalse(any(i.graph.index == sigma_flux.EFFECT
                                         for i in torb.body_script.component.instances.values()))
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 45)
                self.assertEqual(sigma.stats.value(stats.ELIMINATIONS), 0)
                if mode != "switch":
                    run.step({torb: (0, 0, 16)})
                    self.assertEqual(new_hp.over_armour.amount, 100)

    def test_flux_lift_respects_static_ceiling_and_slam_requires_ground(self):
        for mode in ("ceiling", "platform", "ledge"):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 8)
                match = run.match
                fight = match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                floor = box(x - 25, x + (2 if mode == "ledge" else 25), y - 1, y, z - 25, z + 25)
                base_y = y
                if mode == "ceiling":
                    floor += box(x - 2, x + 2, y + 4, y + 4.25, z - 2, z + 2)
                elif mode == "platform":
                    base_y = y + 2
                    floor += box(x - 2, x + 2, y, base_y, z - 2, z + 2)
                    floor += box(x - 2, x + 2, y, base_y, z + 6, z + 10)
                    fixture.place(torb, (x, base_y, z))
                    fixture.place(sigma, (x, base_y, z + 8))
                match.collision_world = World(floor)
                fixture.advance(run, torb, sigma, 12)
                hp = fixture.health(run, torb)
                sigma.body_script.set_ult_charge(sigma.body_script.component.entity_var(338).value())
                direction = aim(fight._eye(sigma), (x, base_y, z))
                run.step({sigma: (*direction, 32)})
                fixture.advance(run, torb, sigma, 45)
                run.step({sigma: (*direction, 1)})
                flux = next(i for i in sigma.body_script.component.instances.values()
                            if i.graph.index == sigma_flux.CAST)
                self.assertEqual(flux.vars[59].value(), 2)
                self.assertAlmostEqual(flux.vars[2478].value().y, base_y, delta=.01)
                if mode == "ledge":
                    fixture.advance(run, torb, sigma, 58)
                    fixture.place(torb, (x + 3, y, z))

                component = torb.body_script.component
                for _ in range(75):
                    if hp.total < 250:
                        break
                    run.step({})
                self.assertEqual(hp.total, 205)
                effect, = [i for i in component.instances.values() if i.graph.index == sigma_flux.EFFECT]
                apex = torb.mover.position[1]
                hit_on_landing = False
                for _ in range(400):
                    previous = hp.total
                    run.step({})
                    apex = max(apex, torb.mover.position[1])
                    if hp.total < previous:
                        self.assertTrue(torb.mover.landed)
                        self.assertAlmostEqual(torb.mover.position[1], base_y, delta=.01)
                        hit_on_landing = True
                    if effect.id not in component.instances:
                        break
                self.assertNotIn(effect.id, component.instances)
                self.assertFalse(torb.mover.driven)
                if mode == "ledge":
                    self.assertFalse(hit_on_landing)
                    self.assertTrue(torb.mover.airborne)
                    self.assertLess(torb.mover.position[1], y - 5)
                    self.assertEqual(hp.total, 205)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 45)
                else:
                    if mode == "ceiling":
                        self.assertGreater(apex - base_y, 1)
                        self.assertLess(apex - base_y, 4)
                    else:
                        self.assertAlmostEqual(apex - base_y, 7, delta=.01)
                    self.assertTrue(hit_on_landing)
                    self.assertEqual(hp.total, 85)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 165)

    def test_accretion_direct_hit_stuns_overload_but_wall_splash_does_not(self):
        for direct, loss in ((True, 60), (False, 35)):
            with self.subTest(direct=direct):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma")
                match = run.match
                fight = match.combat
                x, y, z = sigma.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                fixture.place(torb, (x if direct else x + 1.2, y, z + 6))
                if not direct:
                    match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x - 4, z + 6.3, x + 4, z + 6.3, y0=y, y1=y + 4)
                    )
                run.step({torb: (0, 0, 16)})
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                self.assertEqual(hp.total, 350)
                source = hp.over_armour.source
                direction = aim(fight._eye(sigma), fight._eye(torb)) if direct else (0, 0)
                run.step({sigma: (*direction, 16)})
                rock = None
                for _ in range(100):
                    if hp.over_armour.amount < 100:
                        break
                    run.step({sigma: (*direction, 0)})
                    rock = next(iter(fight.rockets), rock)
                self.assertIsNotNone(rock)
                self.assertEqual(hp.over_armour.amount, 100 - loss)
                self.assertEqual(hp.over_armour.source, source)
                self.assertEqual(hp.now, base)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), loss)
                self.assertEqual(len(sigma.client.messages(combat.HIT)), 2 if direct else 1)

                if direct:
                    end = fight.stuns[torb.body]
                    self.assertEqual(end - fight.tick, 50)
                    while run.frame < end - 1:
                        fixture.step(run, torb, sigma, fire=1 | 2 | 8 | 16)
                        self.assertTrue(fight.is_stunned(torb))
                        self.assertFalse(torb.body_script.component.held)
                    self.assertFalse(fight.turrets)
                else:
                    self.assertNotIn(torb.body, fight.stuns)
                fixture.step(run, torb, sigma, fire=1)
                self.assertFalse(fight.is_stunned(torb))
                fixture.advance(run, torb, sigma, 30)
                self.assertEqual(fixture.health(run, sigma).total, 330)

    def test_accretion_knocks_down_or_destroys_a_deployed_turret(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 100)
                fight = run.match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                run.step({torb: (0, 4000, 8)})
                fixture.advance(run, torb, sigma, 225)
                turret = fight.turrets[torb.entity]
                hp = fight.vitals[turret.actor.entity]
                self.assertEqual(hp.total, 250)
                self.assertEqual(turret.shots, 0)
                starting_health = 50 if lethal else 250
                if lethal:
                    hp.damage(hp.total - starting_health)
                    fight._sync_body_health(turret.actor, hp)

                x, y, z = turret.actor.position
                fixture.place(torb, (x - 12, y, z))
                fixture.place(sigma, (x, y, z + 6))
                direction = aim(fight._eye(sigma), (x, y + .6, z))
                run.step({sigma: (*direction, 16)})
                for _ in range(100):
                    run.step({sigma: (*direction, 0)})
                    if hp.total < starting_health:
                        break
                self.assertEqual(fixture.health(run, torb).total, 250)
                hits = sigma.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [30, 20 if lethal else 40])
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 50 if lethal else 70)

                if lethal:
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, sigma.body)
                    self.assertNotIn(torb.entity, fight.turrets)
                    self.assertNotIn(turret.actor.entity, fight.stuns)
                    ability = next(i for i in torb.body_script.component.instances.values()
                                   if i.graph.index == 0x16C5).state(13)
                    self.assertGreater(ability.remaining(), 9.9)
                    run.step({torb: (0, 4000, 8)})
                    fixture.advance(run, torb, sigma, 15)
                    self.assertNotIn(torb.entity, fight.turrets)
                    for _ in range(650):
                        if ability.remaining() <= 0:
                            break
                        run.step({})
                    self.assertEqual(ability.remaining(), 0)
                    run.step({torb: (0, 4000, 8)})
                    fixture.advance(run, torb, sigma, 225)
                    replacement = fight.turrets[torb.entity]
                    self.assertNotEqual(replacement.actor.entity, turret.actor.entity)
                    self.assertAlmostEqual(fight.vitals[replacement.actor.entity].total, 250)
                else:
                    self.assertEqual(hp.total, 180)
                    end = fight.stuns[turret.actor.entity]
                    self.assertEqual(end - fight.tick, 50)
                    self.assertFalse(fight.is_stunned(torb))
                    shots = turret.shots
                    sigma_hp = fixture.health(run, sigma)
                    before = sigma_hp.total
                    while run.frame < end - 1:
                        run.step({})
                        self.assertTrue(fight.is_stunned(turret.actor))
                        self.assertEqual(turret.shots, shots)
                        self.assertEqual(sigma_hp.total, before)
                    run.step({})
                    self.assertFalse(fight.is_stunned(turret.actor))
                    self.assertEqual(turret.shots, shots + 1)
                    self.assertEqual(sigma_hp.total, before - 14)

    def test_projected_barrier_intercepts_airborne_accretion_even_when_it_breaks(self):
        zarya_hero = heroes()[0x02E0000000000068]
        for mode in ("intact", "break", "wrong_team"):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, zarya = fixture.arena("Sigma")
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                torb.team = 1 - sigma.team
                zarya.team = sigma.team if mode == "wrong_team" else torb.team
                self.assertTrue(match.switch_hero(zarya, zarya_hero))
                x, y, z = sigma.mover.position
                fixture.place(torb, (x, y, z + 8))
                fixture.place(zarya, (x + 5, y, z + 8))
                fixture.advance(run, torb, sigma, 35)
                rock_aim = aim(fight._eye(sigma), fight._eye(torb))
                bubble_aim = fixture.body_aim(run, zarya, torb)
                run.step({torb: (0, 0, 16), sigma: (*rock_aim, 16)})
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                self.assertEqual(hp.total, 350)
                for _ in range(65):
                    if fight.rockets:
                        break
                    run.step({sigma: (*rock_aim, 0)})
                rock, = fight.rockets
                self.assertIs(rock.shooter, sigma)
                self.assertIsNotNone(rock.accretion_source)
                self.assertIsNone(fight.bubbles.for_target(torb))
                run.step({zarya: (*bubble_aim, 16)})
                self.assertIn(rock, fight.rockets)
                bubble = fight.bubbles.for_target(torb)
                if mode == "wrong_team":
                    self.assertIsNone(bubble)
                else:
                    self.assertIs(bubble.caster, zarya)
                    self.assertEqual(bubble.health, 200)
                    if mode == "break":
                        bubble.health = 50

                for _ in range(25):
                    if rock not in fight.rockets:
                        break
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(hp.now, base)
                energy = zarya.body_script.component.entity_var(87).value() or 0
                if mode == "wrong_team":
                    self.assertEqual(hp.over_armour.amount, 40)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 60)
                    self.assertEqual(fight.stuns[torb.body] - fight.tick, 50)
                    self.assertEqual(energy, 0)
                else:
                    self.assertEqual(hp.over_armour.amount, 100)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertFalse(fight.is_stunned(torb))
                    self.assertEqual(bubble.health, 0 if mode == "break" else 130)
                    self.assertIs(fight.bubbles.for_target(torb), None if mode == "break" else bubble)
                    self.assertAlmostEqual(energy, 10 if mode == "break" else 14)

                if mode == "break":
                    fixture.advance(run, torb, sigma, 40)
                    fixture.step(run, torb, sigma, defense=1, defender_aim=rock_aim)
                    fixture.advance(run, torb, sigma, 65, defender_aim=rock_aim)
                    self.assertEqual(hp.over_armour.amount, 10)
                    self.assertEqual(hp.now, base)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 90)

    def test_winston_shell_stops_accretion_before_overload_even_on_break(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, winston = fixture.arena("Sigma")
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                torb.team = winston.team = 1 - sigma.team
                self.assertTrue(match.switch_hero(winston, heroes()[0x02E0000000000009]))
                x, y, z = sigma.mover.position
                fixture.place(torb, (x, y, z + 10))
                fixture.place(winston, (x + 2, y, z + 10))
                fixture.advance(run, torb, sigma, 35)
                run.step({torb: (0, 0, 16), winston: (0, 0, 16)})
                fixture.advance(run, torb, sigma, 12)
                shell, = fight.world_bubbles.actors.values()
                self.assertEqual(shell.health, 700)
                if breaking:
                    fight.world_bubbles.damage(shell, 650)
                    self.assertEqual(shell.health, 50)
                torb_hp = fixture.health(run, torb)
                winston_hp = fixture.health(run, winston)
                before = winston_hp.total
                direction = aim(fight._eye(sigma), fight._eye(torb))
                run.step({sigma: (*direction, 16)})
                for _ in range(65):
                    if fight.rockets:
                        break
                    run.step({sigma: (*direction, 0)})
                rock, = fight.rockets
                self.assertIsNotNone(rock.accretion_source)
                for _ in range(30):
                    if rock not in fight.rockets:
                        break
                    run.step({})
                self.assertNotIn(rock, fight.rockets)

                self.assertEqual(shell.health, 0 if breaking else 630)
                self.assertEqual(shell.entity in fight.world_bubbles.actors, not breaking)
                self.assertEqual(torb_hp.total, 350)
                self.assertEqual(winston_hp.total, before)
                self.assertFalse(fight.is_stunned(torb) or fight.is_stunned(winston))
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)

    def test_accretion_lifecycle_on_sigma_death_and_body_replacement(self):
        for mode in ("windup_death", "airborne_death", "body_switch"):
            with self.subTest(mode=mode):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, other = fixture.arena("Sigma", 12)
                match = run.match
                fight = match.combat
                x, y, z = torb.mover.position
                fixture.place(other, (x + 100, y, z + 100))
                run.step({torb: (0, 0, 16)})
                fixture.advance(run, torb, sigma, 55)
                hp = fixture.health(run, torb)
                sigma_hp = fixture.health(run, sigma)
                if mode != "body_switch":
                    sigma_hp.damage(sigma_hp.total - 20, armour_reduction=False)
                    self.assertEqual(sigma_hp.total, 20)
                base = dict(hp.now)
                self.assertEqual(hp.over_armour.amount, 100)
                old_body, old_script = sigma.body, sigma.body_script
                sigma_position = tuple(sigma.mover.position)
                direction = aim(fight._eye(sigma), fight._eye(torb))
                run.step({sigma: (*direction, 16)})
                self.assertFalse(fight.rockets)
                cast = next(instance for instance in sigma.body_script.component.instances.values()
                            if instance.graph.index == 0x1BBB)
                self.assertTrue(cast.state(0).flags & nodes.Ability.RUNNING)

                if mode != "windup_death":
                    for _ in range(65):
                        if fight.rockets:
                            break
                        run.step({sigma: (*direction, 0)})
                    rock, = fight.rockets
                    self.assertEqual(rock.accretion_source[0], old_body)

                if mode == "body_switch":
                    self.assertTrue(match.switch_hero(sigma, heroes()[SOLDIER]))
                    self.assertNotEqual(sigma.body, old_body)
                    self.assertNotIn(rock, fight.rockets)
                else:
                    fixture.step(run, torb, sigma, fire=1, defender_aim=direction)
                    for _ in range(20):
                        if sigma_hp.dead:
                            break
                        run.step({sigma: (*direction, 0)})
                        if mode == "windup_death":
                            self.assertFalse(any(shot.accretion_source is not None for shot in fight.rockets))
                    self.assertTrue(sigma_hp.dead)
                    self.assertEqual(sigma_hp.killer, torb.body)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 20)
                    self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)

                if mode == "airborne_death":
                    self.assertIn(rock, fight.rockets)
                    for _ in range(45):
                        if rock not in fight.rockets:
                            break
                        run.step({})
                    self.assertNotIn(rock, fight.rockets)
                    self.assertEqual(hp.over_armour.amount, 40)
                    self.assertEqual(hp.now, base)
                    self.assertEqual(fight.stuns[torb.body] - fight.tick, 50)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 60)
                else:
                    for _ in range(65):
                        run.step({})
                        self.assertFalse(fight.rockets)
                    self.assertEqual(hp.over_armour.amount, 100)
                    self.assertEqual(hp.now, base)
                    self.assertFalse(fight.is_stunned(torb))
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)

                if mode == "body_switch":
                    continue
                damage = sigma.stats.value(stats.HERO_DAMAGE)
                for _ in range(750):
                    if sigma.body != old_body:
                        break
                    run.step({sigma: (*direction, 16)})
                    self.assertFalse(fight.rockets)
                self.assertNotEqual(sigma.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertFalse(fight.is_dead(sigma))
                self.assertFalse(fight.is_stunned(torb))
                self.assertEqual(hp.total, 250)
                fixture.place(sigma, sigma_position)
                fixture.advance(run, torb, sigma, 35)
                direction = aim(fight._eye(sigma), fight._eye(torb))
                run.step({sigma: (*direction, 16)})
                for _ in range(100):
                    if sigma.stats.value(stats.HERO_DAMAGE) > damage:
                        break
                    run.step({sigma: (*direction, 0)})
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE) - damage, 60)
                self.assertEqual(hp.total, 190)
                self.assertEqual(fight.stuns[torb.body] - fight.tick, 50)

    def test_accretion_damage_wakes_but_cannot_replace_guaranteed_sleep(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, sigma, ana = fixture.arena("Sigma")
        match = run.match
        fight = match.combat
        hero = next(hero for hero in heroes().values() if hero.name == "Ana")
        self.assertTrue(match.switch_hero(ana, hero))
        x, y, z = torb.mover.position
        fixture.place(ana, (x + 3, y, z))
        fixture.advance(run, torb, sigma, 35)
        rock_aim = aim(fight._eye(sigma), fight._eye(torb))
        dart_aim = aim(fight._eye(ana), (x, y + 1, z))
        run.step({torb: (0, 0, 16), sigma: (*rock_aim, 16)})
        hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 350)
        for _ in range(22):
            run.step({sigma: (*rock_aim, 0)})
        run.step({sigma: (*rock_aim, 0), ana: (*dart_aim, 8)})
        for _ in range(40):
            if torb.body in fight.sleep:
                break
            run.step({sigma: (*rock_aim, 0), ana: (*dart_aim, 0)})
        deadline, earliest = fight.sleep[torb.body]
        self.assertGreater(deadline, earliest)
        self.assertEqual(hp.over_armour.amount, 97.5)

        for _ in range(20):
            if sigma.stats.value(stats.HERO_DAMAGE) > 0:
                break
            run.step({sigma: (*rock_aim, 0)})
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 60)
        self.assertEqual(hp.over_armour.amount, 37.5)
        self.assertGreater(earliest - fight.tick, 50)
        self.assertNotIn(torb.body, fight.stuns)
        self.assertEqual(fight.sleep[torb.body], (earliest, earliest))
        while run.frame < earliest - 1:
            fixture.step(run, torb, sigma, fire=1)
            self.assertTrue(fight.is_stunned(torb))
        fixture.step(run, torb, sigma, fire=1)
        self.assertFalse(fight.is_stunned(torb))
        fixture.advance(run, torb, sigma, 30)
        self.assertEqual(fixture.health(run, sigma).total, 330)

    def test_grasp_entered_after_launch_uses_facing_and_settles_temporary_shields(self):
        fixture = defenses.TorbjornDefenseTests()
        for button, count, rounds, raw, grant in ((1, 1, 1, 70, 42), (2, 10, 2, 125, 75)):
            for frontal in (False, True):
                with self.subTest(button=button, frontal=frontal):
                    run, owner, sigma, _ = fixture.arena("Sigma", 4)
                    fight = run.match.combat
                    hp = fixture.health(run, sigma)
                    charge = owner.body_script.component.entity_var(342)
                    rifle = next(i for i in owner.body_script.component.instances.values()
                                 if i.graph.index == 0x1683)
                    fixture.step(run, owner, sigma, fire=button)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is owner and shot.distance == 0 for shot in volley))
                    direction = fixture.body_aim(run, sigma, owner) if frontal else (0, 0)
                    fixture.step(run, owner, sigma, defense=8, defender_aim=direction)
                    self.assertEqual(tuple(fight.rockets), volley)
                    self.assertEqual(hp.total, 400)
                    fixture.advance(run, owner, sigma, 24, defender_aim=direction)
                    instance = next(i for i in sigma.body_script.component.instances.values()
                                    if i.graph.index == sigma_grasp.GRASP)
                    direct = 0 if frontal else raw
                    self.assertEqual(instance.vars[21312].value(), raw if frontal else 0)
                    self.assertEqual(hp.total, 400 - direct)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(charge.value(), direct)
                    self.assertEqual(rifle.vars[53].value(), 18 - rounds)
                    self.assertEqual((owner.entity, sigma.body) in fight.torbjorn_marks,
                                     bool(direct and button == 1))
                    self.assertNotIn(sigma_grasp.SHIELD_TAG, hp.temporary_shields)
                    for _ in range(150):
                        if sigma_grasp.SHIELD_TAG in hp.temporary_shields:
                            break
                        fixture.step(run, owner, sigma, defender_aim=direction)
                    if not frontal:
                        self.assertNotIn(sigma_grasp.SHIELD_TAG, hp.temporary_shields)
                        self.assertEqual(hp.total, 400 - direct)
                        continue

                    bonus = hp.temporary_shields[sigma_grasp.SHIELD_TAG]
                    self.assertAlmostEqual(bonus.now, grant, places=5)
                    self.assertEqual(charge.value(), 0)
                    fixture.step(run, owner, sigma, fire=1)
                    fixture.advance(run, owner, sigma, 20)
                    self.assertAlmostEqual(bonus.now, max(0, grant - 70), places=5)
                    self.assertAlmostEqual(hp.now[combat.SHIELDS_POOL], 100 - max(0, 70 - grant), places=5)
                    self.assertEqual(hp.now[combat.HEALTH_POOL], 300)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70)
                    self.assertEqual(charge.value(), 70)
                    self.assertIn((owner.entity, sigma.body), fight.torbjorn_marks)
                    self.assertEqual(rifle.vars[53].value(), 17 - rounds)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_grasp_absorbs_talon_ranged_fire_before_overload_and_grants_shields(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        cases = (("Trooper", 20, 15), ("Sniper", 100, 95), ("Heavy Assault", 5, 2.5), ("Enforcer", 50, 25))
        for name, nominal, direct in cases:
            with self.subTest(npc=name):
                definition = next(row for row in catalog.values() if row.name == name)
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, bot = fixture.ranged_talon_at_overload("Sigma", definition)
                fight = run.match.combat
                torb_hp = fixture.health(run, torb)
                base = dict(torb_hp.now)
                source = torb_hp.over_armour.source
                self.assertIsNotNone(source)
                sigma_hp = fixture.health(run, sigma)
                bot_hp = fight.vitals[bot.entity]
                bot_before = bot_hp.total
                direction = fixture.body_aim(run, sigma, bot)
                fixture.cast(run, torb, sigma, defender_aim=direction)
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRASP)
                for _ in range(120):
                    if grasp.vars[21312].value() >= nominal or sigma_hp.total < 400 or torb_hp.total < 350:
                        break
                    run.step({sigma: (*direction, 0)})
                self.assertEqual(grasp.vars[21312].value(), nominal)
                self.assertEqual(torb_hp.total, 350)
                self.assertEqual(sigma_hp.total, 400)
                self.assertEqual(bot_hp.total, bot_before)
                self.assertNotIn(sigma_grasp.SHIELD_TAG, sigma_hp.temporary_shields)

                x, y, z = torb.mover.position
                fixture.place(sigma, (x + 8, y, z))
                for _ in range(150):
                    if torb_hp.total < 350:
                        break
                    run.step({})
                npc_control.stop(run.match)
                self.assertEqual(torb_hp.over_armour.source, source)
                self.assertEqual(torb_hp.over_armour.amount, 100 - direct)
                self.assertEqual(torb_hp.now, base)
                self.assertEqual(grasp.vars[21312].value(), nominal)
                for _ in range(150):
                    if sigma_grasp.SHIELD_TAG in sigma_hp.temporary_shields:
                        break
                    run.step({})
                bonus = sigma_hp.temporary_shields[sigma_grasp.SHIELD_TAG]
                self.assertAlmostEqual(bonus.now, nominal * .6, places=5)
                self.assertAlmostEqual(sigma_hp.total, 400 + bonus.now, places=5)
                self.assertIn(sigma.body, fight.grasp_shields)
                self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(sigma.body_script.component.entity_var(342).value(), 0)

    def test_grasp_absorbs_front_ana_rifle_then_torbjorn_spends_the_grant(self):
        ana_hero = next(hero for hero in heroes().values() if hero.name == "Ana")
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                fixture = defenses.TorbjornDefenseTests()
                run, torb, sigma, ana = fixture.arena("Sigma", 7)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                ana.team = torb.team = 1 - sigma.team
                self.assertTrue(match.switch_hero(ana, ana_hero))
                x, y, z = torb.mover.position
                fixture.place(ana, (x + 1.8, y, z))
                sigma_aim = fixture.body_aim(run, sigma, torb)
                scope = 2 if scoped else 0
                fixture.advance(run, ana, sigma, 40, fire=scope, defender_aim=sigma_aim)
                fixture.cast(run, ana, sigma, fire=scope, defender_aim=sigma_aim)
                grasp = next(instance for instance in sigma.body_script.component.instances.values()
                             if instance.graph.index == sigma_grasp.GRASP)
                hp = fixture.health(run, sigma)
                fixture.step(run, ana, sigma, fire=scope | 1, defender_aim=sigma_aim)
                fixture.advance(run, ana, sigma, 48, fire=scope, defender_aim=sigma_aim)
                self.assertEqual(grasp.vars[21312].value(), 70)
                self.assertEqual(hp.total, 400)
                self.assertFalse(fight.rifle_wounds.active or fight.rockets)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

                fixture.place(ana, (x + 1.8, y, z + 14))
                fixture.step(run, ana, sigma, fire=scope | 1, defender_aim=sigma_aim)
                for _ in range(28):
                    if fight.rifle_wounds.active:
                        break
                    fixture.step(run, ana, sigma, fire=scope, defender_aim=sigma_aim)
                self.assertTrue(grasp.state(1).active)
                wound, = fight.rifle_wounds.active
                self.assertIs(wound.source, ana)
                self.assertEqual(wound.target.entity, sigma.body)
                self.assertEqual(grasp.vars[21312].value(), 70)

                fixture.advance(run, ana, sigma, 110, fire=scope, defender_aim=sigma_aim)
                self.assertFalse(grasp.state(1).active)
                self.assertFalse(fight.rifle_wounds.active)
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 70, places=4)
                self.assertAlmostEqual(hp.now[combat.SHIELDS_POOL], 30)
                self.assertEqual(hp.now[combat.HEALTH_POOL], 300)
                bonus = hp.temporary_shields[sigma_grasp.SHIELD_TAG]
                self.assertEqual(bonus.now, 42)
                fixture.step(run, torb, sigma, fire=1)
                fixture.advance(run, torb, sigma, 20)
                self.assertEqual(bonus.now, 0)
                self.assertAlmostEqual(hp.now[combat.SHIELDS_POOL], 2)
                self.assertEqual(hp.now[combat.HEALTH_POOL], 300)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 70)

    def test_absorbed_globule_grants_shields_then_landed_molten_spends_them(self):
        fixture = defenses.TorbjornDefenseTests()
        run, owner, sigma, _ = fixture.arena("Sigma", 8)
        fight = run.match.combat
        hp = fixture.health(run, sigma)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        fixture.step(run, owner, sigma, fire=32)
        fixture.advance(run, owner, sigma, 45)
        fixture.cast(run, owner, sigma)
        fixture.step(run, owner, sigma, fire=1)
        fixture.advance(run, owner, sigma, 20)
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertEqual(instance.vars[21312].value(), 25)
        self.assertEqual(hp.total, 400)
        self.assertFalse(fight.molten_pools or fight.molten_burns)
        for _ in range(150):
            if sigma_grasp.SHIELD_TAG in hp.temporary_shields:
                break
            fixture.step(run, owner, sigma)
        bonus = hp.temporary_shields[sigma_grasp.SHIELD_TAG]
        self.assertAlmostEqual(bonus.now, 15, places=5)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

        run.step({owner: (16384, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, owner)
        fixture.place(sigma, pool.position)
        run.step({})
        self.assertIn((owner.entity, sigma.body), fight.molten_burns)
        damage = pool.config["base_dps"] * combat.TICK
        self.assertAlmostEqual(bonus.now, 15 - damage, places=5)
        self.assertEqual(hp.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(hp.now[combat.HEALTH_POOL], 300)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), damage)

    def test_thrown_turret_fire_grants_grasp_shields_then_spends_them(self):
        fixture = defenses.TorbjornDefenseTests()
        run, owner, sigma, other = fixture.arena("Sigma", 100)
        fight = run.match.combat
        x, y, z = owner.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        run.step({owner: (0, 4000, 8)})
        fixture.advance(run, owner, sigma, 225)
        turret = fight.turrets[owner.entity]
        self.assertEqual(turret.shots, 0)
        hp = fixture.health(run, sigma)
        self.assertEqual(hp.total, 400)
        flat = (-32768, 0)
        distant = tuple(sigma.mover.position)
        fixture.cast(run, owner, sigma, defender_aim=flat)
        x, y, z = turret.actor.position
        near = (x, y, z + 7)
        fixture.place(sigma, near)
        for _ in range(30):
            fixture.step(run, owner, sigma, defender_aim=flat)
            if turret.shots == 2:
                break
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertEqual(turret.shots, 2)
        self.assertEqual(instance.vars[21312].value(), 28)
        self.assertEqual(hp.total, 400)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        fixture.place(sigma, distant)
        for _ in range(150):
            if sigma_grasp.SHIELD_TAG in hp.temporary_shields:
                break
            fixture.step(run, owner, sigma, defender_aim=flat)
        bonus = hp.temporary_shields[sigma_grasp.SHIELD_TAG]
        self.assertAlmostEqual(bonus.now, 16.8, places=5)
        self.assertEqual(turret.shots, 2)

        fixture.place(sigma, near)
        for _ in range(30):
            fixture.step(run, owner, sigma, defender_aim=flat)
            if turret.shots == 3:
                break
        self.assertEqual(turret.shots, 3)
        self.assertAlmostEqual(bonus.now, 2.8, places=5)
        self.assertEqual(hp.now[combat.SHIELDS_POOL], 100)
        self.assertEqual(hp.now[combat.HEALTH_POOL], 300)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)

    def test_real_rivet_spends_bonus_before_base_and_healing_cannot_refill_bonus(self):
        case, run, owner, sigma, hp, _ = self.granted()
        case.step(run, owner, sigma, fire=1)
        case.advance(run, owner, sigma, 20)
        self.assertEqual(hp.total, 372)
        self.assertEqual(hp.temporary_shields[sigma_grasp.SHIELD_TAG].now, 0)
        self.assertEqual(hp.now[combat.SHIELDS_POOL], 72)
        run.match.combat.heal_area(sigma, run.match.combat._eye(sigma), 10, 1000)
        self.assertEqual(hp.total, 400)
        self.assertEqual(hp.temporary_shields[sigma_grasp.SHIELD_TAG].now, 0)

    def test_authored_grace_then_five_per_second_decay_preserves_base_and_has_no_damage_credit(self):
        _, run, owner, sigma, hp, _ = self.granted()
        fight = run.match.combat
        decay = fight.grasp_shields[sigma.body]
        self.assertEqual(decay.rate, 5)
        start = math.ceil(decay.starts / combat.TICK)
        run.frame = start - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(hp.total, 442)
        damage = owner.stats.value(stats.HERO_DAMAGE)
        run.frame = start + 125
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(hp.temporary_shields[sigma_grasp.SHIELD_TAG].now, 32, delta=.081)
        run.frame += 1000
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(hp.total, 400)
        self.assertEqual(hp.maximum, 400)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
        self.assertNotIn(sigma.body, fight.grasp_shields)
        cleared = hp.component(hp.ids)[51][combat.SHIELDS_POOL * combat.POOLS + 1]
        self.assertEqual(struct.unpack("<IffI4B", cleared)[1:3], (0, 0))

    def test_six_actual_full_secondary_volleys_reach_the_four_hundred_bonus_cap(self):
        run = Run(KANEZAKA_DM, tuple(["Sigma"] + [f"Torb{i}" for i in range(6)]))
        sigma, *sources = run.players
        run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = sigma.mover.position
        for index, owner in enumerate(sources):
            run.match.switch_hero(owner, heroes()[0x02E0000000000006])
            owner.mover.position = [x + (index - 2.5) * 1.2, y, z + 6]
            owner.mover.body = None
        for player in run.players:
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        for _ in range(35):
            run.step({})
        run.step({sigma: (0, 0, 8)})
        commands = {}
        for owner in sources:
            yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1.2, z))
            commands[owner] = (yaw, pitch, 2)
        run.step(commands)
        for _ in range(165):
            run.step({})
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertEqual(instance.vars[21312].value(), 750)
        hp = defenses.TorbjornDefenseTests.health(run, sigma)
        self.assertEqual(hp.temporary_shields[sigma_grasp.SHIELD_TAG].now, 400)
        self.assertEqual(hp.total, 800)
        self.assertTrue(all(owner.body_script.component.entity_var(342).value() == 0 for owner in sources))

    def test_death_during_grasp_denies_delayed_grant_and_death_after_grant_clears_decay(self):
        for after_grant in (False, True):
            with self.subTest(after_grant=after_grant):
                case, run, owner, sigma, hp, _ = self.granted() if after_grant else self.absorbed()
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is sigma)
                fight._damage(owner, target, 10000, False, None, run.frame)
                case.advance(run, owner, sigma, 150)
                self.assertTrue(hp.dead)
                self.assertEqual(hp.total, 0)
                self.assertNotIn(sigma.body, fight.grasp_shields)
                self.assertTrue(all(pool.now == 0 for pool in hp.temporary_shields.values()))

    def test_body_replacement_and_repeated_authored_request_do_not_leak_or_duplicate(self):
        _, run, _, sigma, hp, instance = self.granted()
        fight = run.match.combat
        self.assertTrue(sigma_grasp.grant(fight, sigma.body, instance, instance.graph.node(34)))
        self.assertEqual(hp.total, 442)
        body = sigma.body
        run.match.switch_hero(sigma, heroes()[SOLDIER])
        self.assertNotIn(body, fight.grasp_shields)
        self.assertNotIn(body, fight.grasp_grants)
        self.assertNotIn(body, fight.vitals)

    def test_cooldown_rejects_early_recast_then_new_activation_gets_fresh_accounting(self):
        case, run, owner, sigma, hp, instance = self.granted()
        ability = instance.state(0)
        first = instance.state(1).counter
        self.assertGreater(ability.remaining(), 0)
        case.step(run, owner, sigma, defense=8)
        case.advance(run, owner, sigma, 5)
        self.assertEqual(instance.state(1).counter, first)
        self.assertEqual(hp.total, 442)
        case.advance(run, owner, sigma, math.ceil(ability.remaining() / combat.TICK) + 2)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(hp.total, 400)
        case.cast(run, owner, sigma)
        self.assertEqual(instance.state(1).counter, first + 1)
        case.step(run, owner, sigma, fire=1)
        case.advance(run, owner, sigma, 20)
        self.assertEqual(instance.vars[21312].value(), 70)
        case.advance(run, owner, sigma, 140)
        self.assertEqual(hp.total, 442)

    def test_bonus_pool_and_decay_are_passive_server_snapshot_state(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        owner = players[0]
        fight = owner.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        hp = fight.vitals_of(target)
        hp.grant_temporary_shields(sigma_grasp.SHIELD_TAG, 42, 400)
        fight.grasp_shields[owner.body] = sigma_grasp.ShieldDecay(3, 0, 5)
        fight.grasp_grants[owner.body] = (7, 1)
        baseline = snapshot_digest(case.capture(session))
        self.assertEqual(baseline, snapshot_digest(case.capture(session)))
        hp.temporary_shields[sigma_grasp.SHIELD_TAG].now -= 1
        self.assertNotEqual(baseline, snapshot_digest(case.capture(session)))
        hp.temporary_shields[sigma_grasp.SHIELD_TAG].now += 1
        fight.grasp_shields[owner.body].starts += 1
        self.assertNotEqual(baseline, snapshot_digest(case.capture(session)))
