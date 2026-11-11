"""Resource-backed Junkrat primary fire through real Match input and health paths.

These headless tests cover the reconstructed ricochet and projectile lifetime.
"""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, immunity, sigma_grasp
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, find_hero


class JunkratProjectileTests(unittest.TestCase):
    def setup_fight(self):
        run = Run(KANEZAKA_DM, ("Junkrat", "Enemy"))
        shooter, victim = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(shooter, find_hero("Junkrat"))
        x, y, z = shooter.mover.position
        victim.mover.position = [x, y, z + 6]
        for _ in range(30):
            run.step({})
        return run, shooter, victim

    def health(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def place(self, run, actor, position):
        actor.spawn = (position, actor.spawn[1])
        run.match.switch_hero(actor, actor.hero)

    def test_actual_primary_input_deals_direct_and_splash_once(self):
        run, shooter, victim = self.setup_fight()
        run.step({shooter: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        self.assertEqual((rocket.gravity, rocket.speed), (12.25, 25))
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, victim).total, 80)
        self.assertEqual(self.health(run, shooter).total, 200)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 2)
        # A replay of the input cannot spend another round or deal damage again.
        shooter.take_commands([Command(rocket.shot.frame, buttons=FIRE)])
        self.assertEqual(self.health(run, victim).total, 80)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 2)

    def test_zarya_barrier_break_absorbs_the_entire_grenade(self):
        run, shooter, zarya = self.setup_fight()
        zarya.spawn = (tuple(zarya.mover.position), zarya.spawn[1])
        run.match.switch_hero(zarya, find_hero("Zarya"))
        for _ in range(25):
            run.step({})
        run.step({zarya: (0, 0, 8)})
        fight = run.match.combat
        bubble = fight.barriers.sync(zarya)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.assertNotEqual(bubble.entity, zarya.body)
        for expected in (80, 0):
            run.step({shooter: (0, 0, FIRE)})
            for _ in range(45):
                run.step({})
            self.assertEqual(bubble.health, expected)
            self.assertEqual(self.health(run, zarya).total, 400)
            self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertIsNone(fight.barriers.sync(zarya))
        self.assertFalse(bubble.phase.active)
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, zarya).total, 280)

    def test_zarya_barrier_absorbs_quick_melee(self):
        run, shooter, zarya = self.setup_fight()
        run.match.switch_hero(zarya, find_hero("Zarya"))
        x, y, z = shooter.mover.position
        self.place(run, zarya, (x, y, z + 1.5))
        for _ in range(25):
            run.step({})
        run.step({zarya: (0, 0, 8)})
        bubble = run.match.combat.barriers.sync(zarya)
        self.assertIsNotNone(bubble)
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(15):
            run.step({})
        self.assertEqual(bubble.health, 170)
        self.assertEqual(self.health(run, zarya).total, 400)

    def test_genji_deflect_returns_junkrat_grenade_with_new_source(self):
        run, shooter, genji = self.setup_fight()
        x, y, z = shooter.mover.position
        run.match.switch_hero(genji, find_hero("Genji"))
        self.place(run, genji, (x, y, z + 4))
        run.step({genji: (-32768, 0, 16)})
        for _ in range(4):
            run.step({genji: (-32768, 0, 0)})
        run.step({genji: (-32768, 0, 0), shooter: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        self.assertTrue(rocket.direct.deflectable)
        lifetime = rocket.left
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
            if rocket.shooter is genji:
                break
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.source_entity, genji.body)
        self.assertLess(rocket.left, lifetime)
        self.assertIsNone(rocket.shot)
        self.assertEqual(self.health(run, shooter).total, 200)
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, shooter).total, 80)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 2)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_genji_redirects_soldier_hitscan_to_the_player_who_fired(self):
        run, soldier, genji = self.setup_fight()
        x, y, z = soldier.mover.position
        run.match.switch_hero(soldier, find_hero("Soldier"))
        run.match.switch_hero(genji, find_hero("Genji"))
        self.place(run, genji, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        outgoing = aim(fight._eye(soldier), (x, y + 1.0, z + 6))
        incoming = (-32768, 0)
        run.step({genji: (*incoming, 16)})
        for _ in range(4):
            run.step({genji: (*incoming, 0)})
        run.step({genji: (*incoming, 0), soldier: (*outgoing, FIRE)})
        for _ in range(12):
            run.step({genji: (*incoming, 0)})
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, soldier).total, 180)
        hit, = genji.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], soldier.body)
        self.assertEqual(hit["+0x78"]["+0x4"], 20)
        self.assertFalse(soldier.client.messages(combat.HIT))

    def test_reflected_soldier_hitscan_rechecks_cover_and_team_for_junkrat(self):
        for scenario in ("clear", "wall", "friendly"):
            with self.subTest(scenario=scenario):
                run, soldier, genji = self.setup_fight()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = soldier.mover.position
                match.switch_hero(soldier, find_hero("Soldier"))
                match.switch_hero(genji, find_hero("Genji"))
                self.place(run, genji, (x, y, z + 6))
                junkrat = match.add_player(3, "Junkrat", SOLDIER,
                                           genji.team if scenario == "friendly" else soldier.team, False)
                in_world(match, junkrat)
                match.switch_hero(junkrat, find_hero("Junkrat"))
                self.place(run, junkrat, (x - 4, y, z + 1.5))
                run.players.append(junkrat)
                for _ in range(25):
                    run.step({})
                if scenario == "wall":
                    match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x - 2, z - 1, x - 2, z + 7, y0=y, y1=y + 5)
                    )
                outgoing = aim(fight._eye(soldier), (x, y + 0.80, z + 6))
                reflected = (-24576, 0)
                run.step({genji: (*reflected, 16)})
                for _ in range(4):
                    run.step({genji: (*reflected, 0)})
                run.step({genji: (*reflected, 0), soldier: (*outgoing, FIRE)})
                for _ in range(12):
                    run.step({genji: (*reflected, 0)})
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertEqual(self.health(run, soldier).total, 200)
                self.assertEqual(self.health(run, junkrat).total, 180 if scenario == "clear" else 200)
                hits = genji.client.messages(combat.HIT)
                if scenario == "clear":
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], junkrat.body)
                else:
                    self.assertFalse(hits)

    def test_authored_genji_box_deflects_capsule_misses_only_when_unoccluded(self):
        for weapon in ("Junkrat", "Soldier"):
            for blocked in (False, True):
                with self.subTest(weapon=weapon, blocked=blocked):
                    run, shooter, genji = self.setup_fight()
                    match = run.match
                    fight = match.combat
                    x, y, z = shooter.mover.position
                    match.switch_hero(shooter, find_hero(weapon))
                    match.switch_hero(genji, find_hero("Genji"))
                    self.place(run, genji, (x + 0.70, y, z + 5))
                    for _ in range(25):
                        run.step({})
                    if blocked:
                        plane = z + 2.5
                        match.collision_world = World(
                            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                            + wall(x - 5, plane, x + 5, plane, y0=y, y1=y + 5)
                        )
                    facing = (-32768, 0)
                    run.step({genji: (*facing, 16)})
                    for _ in range(4):
                        run.step({genji: (*facing, 0)})
                    target = next(t for t in fight._targets() if t.owner is genji)
                    origin = fight._eye(shooter)
                    direction = combat.aim(0, 0)
                    initial = self.health(run, shooter).total
                    run.step({genji: (*facing, 0), shooter: (0, 0, FIRE)})
                    radius = fight.rockets[0].radius if weapon == "Junkrat" else 0
                    self.assertIsNone(combat.ray_capsule(origin, direction, *target.segment(),
                                                         target.radius + radius))
                    for _ in range(45):
                        run.step({genji: (*facing, 0)})
                    self.assertEqual(self.health(run, genji).total, 200)
                    hits = genji.client.messages(combat.HIT)
                    if blocked:
                        self.assertFalse(hits)
                    else:
                        self.assertLess(self.health(run, shooter).total, initial)
                        self.assertTrue(hits)
                        self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == shooter.body for hit in hits))

    def test_genji_uses_both_lateral_boxes_after_the_front_plane(self):
        for side in (-1, 1):
            with self.subTest(side=side):
                run, soldier, genji = self.setup_fight()
                match = run.match
                fight = match.combat
                x, y, z = soldier.mover.position
                match.switch_hero(soldier, find_hero("Soldier"))
                match.switch_hero(genji, find_hero("Genji"))
                self.place(run, genji, (x + side * 0.95, y, z - 0.15))
                for _ in range(25):
                    run.step({})
                facing = (-32768, 0)
                run.step({genji: (*facing, 16)})
                for _ in range(4):
                    run.step({genji: (*facing, 0)})
                origin = fight._eye(soldier)
                outgoing = aim(origin, (x + side, origin[1], z + 0.5))
                target = next(t for t in fight._targets() if t.owner is genji)
                self.assertGreater(origin[2], target.feet[2])
                self.assertIsNone(combat.ray_capsule(origin, combat.aim(*outgoing),
                                                     *target.segment(), target.radius))
                run.step({genji: (*facing, 0), soldier: (*outgoing, FIRE)})
                for _ in range(12):
                    run.step({genji: (*facing, 0)})
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertLess(self.health(run, soldier).total, 200)
                hit, = genji.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], soldier.body)

    def test_projected_barrier_precedes_genji_deflect_box(self):
        for weapon, damage in (("Junkrat", 120), ("Soldier", 20)):
            with self.subTest(weapon=weapon):
                run, shooter, genji = self.setup_fight()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = shooter.mover.position
                match.switch_hero(shooter, find_hero(weapon))
                match.switch_hero(genji, find_hero("Genji"))
                self.place(run, genji, (x + 0.70, y, z + 5))
                zarya = match.add_player(3, "Zarya", SOLDIER, genji.team, False)
                in_world(match, zarya)
                match.switch_hero(zarya, find_hero("Zarya"))
                self.place(run, zarya, (x + 5, y, z + 5))
                run.players.append(zarya)
                for _ in range(25):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is genji)
                a, b = target.segment()
                centre = tuple((lo + hi) * 0.5 for lo, hi in zip(a, b, strict=True))
                facing = (-32768, 0)
                run.step({genji: (*facing, 16), zarya: (*aim(fight._eye(zarya), centre), 16)})
                fight.barriers.sync(zarya)
                shield, = fight.barriers.live.values()
                self.assertEqual((shield.body, shield.health), (genji.body, 200))
                for _ in range(4):
                    run.step({genji: (*facing, 0)})
                run.step({genji: (*facing, 0), shooter: (0, 0, FIRE)})
                for _ in range(30):
                    run.step({genji: (*facing, 0)})
                self.assertEqual(shield.health, 200 - damage)
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertEqual(self.health(run, shooter).total, 200)
                self.assertFalse(genji.client.messages(combat.HIT))

    def test_rear_grenade_bypasses_active_deflect(self):
        run, shooter, genji = self.setup_fight()
        x, y, z = shooter.mover.position
        run.match.switch_hero(genji, find_hero("Genji"))
        self.place(run, genji, (x, y, z + 4))
        run.step({genji: (0, 0, 16)})
        for _ in range(4):
            run.step({})
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, genji).total, 80)
        self.assertEqual(self.health(run, shooter).total, 200)

    def test_grenade_passes_through_wraith_and_hits_after_recovery(self):
        run, shooter, reaper = self.setup_fight()
        x, y, z = shooter.mover.position
        run.match.switch_hero(reaper, find_hero("Reaper"))
        self.place(run, reaper, (x, y, z + 4))
        run.step({reaper: (0, 0, 8)})
        self.assertTrue(immunity.wraith_active(reaper, reaper.body))
        run.step({shooter: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        for _ in range(14):
            run.step({})
        self.assertGreater(rocket.position[2], reaper.mover.position[2])
        self.assertEqual(self.health(run, reaper).total, 250)
        for _ in range(220):
            if not immunity.wraith_active(reaper, reaper.body):
                break
            run.step({})
        self.assertFalse(immunity.wraith_active(reaper, reaper.body))
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, reaper).total, 130)

    def test_grenade_passes_through_authored_fade_then_hits_after_recovery(self):
        run, shooter, moira = self.setup_fight()
        x, y, z = shooter.mover.position
        run.match.switch_hero(moira, find_hero("Moira"))
        self.place(run, moira, (x, y, z + 4))
        before = self.health(run, moira).total
        run.step({moira: (0, 0, 8)})
        self.assertTrue(immunity.phased_out(moira, moira.body))
        instance = next(i for i in moira.body_script.component.instances.values() if i.graph.index == 0x0F7C)
        ability = instance.states[9]
        self.assertEqual(instance.evaluate(instance.graph.node(6).field("m_timeout")), .75)
        run.step({shooter: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        for _ in range(14):
            run.step({})
        self.assertGreater(rocket.position[2], moira.mover.position[2])
        self.assertEqual(self.health(run, moira).total, before)
        for _ in range(75):
            run.step({})
            if not immunity.phased_out(moira, moira.body) and ability.remaining() > 0:
                break
        self.assertFalse(immunity.phased_out(moira, moira.body))
        self.assertGreater(ability.remaining(), 0)
        run.step({moira: (0, 0, 8), shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertFalse(immunity.phased_out(moira, moira.body))
        self.assertEqual(self.health(run, moira).total, before - 120)
        for _ in range(420):
            if not ability.remaining():
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({moira: (0, 0, 8)})
        self.assertTrue(immunity.phased_out(moira, moira.body))

    def test_grenade_is_spent_on_cryo_without_damage_and_hits_after_cancel(self):
        run, shooter, mei = self.setup_fight()
        x, y, z = shooter.mover.position
        run.match.switch_hero(mei, find_hero("Mei"))
        self.place(run, mei, (x, y, z + 4))
        run.step({mei: (0, 0, 8)})
        self.assertTrue(immunity.cryo_active(mei, mei.body))
        before = self.health(run, mei).total

        run.step({shooter: (0, 0, FIRE)})
        first, = run.match.combat.rockets
        for _ in range(45):
            run.step({})
        self.assertTrue(immunity.cryo_active(mei, mei.body))
        self.assertNotIn(first, run.match.combat.rockets)
        self.assertEqual(self.health(run, mei).total, before)
        self.assertFalse(shooter.client.messages(combat.HIT))

        run.step({mei: (0, 0, 2)})
        self.assertFalse(immunity.cryo_active(mei, mei.body))
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, mei).total, before - 120)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 2)

    def test_fortify_scales_each_grenade_hit_before_armor(self):
        run, shooter, orisa = self.setup_fight()
        x, y, z = shooter.mover.position
        run.match.switch_hero(orisa, find_hero("Orisa"))
        self.place(run, orisa, (x, y, z + 4))
        before = self.health(run, orisa).total
        run.step({orisa: (0, 0, 8)})
        scale = immunity.damage_scale(orisa, orisa.body)
        self.assertLess(scale, 1)
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        expected = sum(max(amount * scale - 5, amount * scale / 2) for amount in (40, 80))
        self.assertAlmostEqual(before - self.health(run, orisa).total, expected)

    def test_sigma_grasp_only_absorbs_enemy_grenades_reaching_its_front(self):
        for case, facing, distance, dealt, absorbed in (
            ("front", -32768, 4, 0, 120),
            ("rear", 0, 4, 120, 0),
            ("wall", -32768, 8, 0, 0),
            ("ally", -32768, 4, 0, 0),
        ):
            with self.subTest(case=case):
                run, shooter, sigma = self.setup_fight()
                x, y, z = shooter.mover.position
                run.match.switch_hero(sigma, find_hero("Sigma"))
                self.place(run, sigma, (x, y, z + distance))
                if case == "wall":
                    self.install_wall(run, shooter)
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sigma.team = shooter.team
                before = self.health(run, sigma).total
                run.step({sigma: (facing, 0, 8)})
                run.step({sigma: (facing, 0, 0), shooter: (0, 0, FIRE)})
                rocket, = run.match.combat.rockets
                self.assertTrue(rocket.grasp_eligible)
                for _ in range(32):
                    run.step({sigma: (facing, 0, 0)})
                instances = sigma.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                self.assertEqual(instance.vars[21312].value(), absorbed)
                self.assertEqual(self.health(run, sigma).total, before - dealt)
                if absorbed:
                    self.assertEqual(instance.vars[6584].value(), 120)
                    self.assertNotIn(rocket, run.match.combat.rockets)
                    self.assertFalse(shooter.client.messages(combat.HIT))

    def test_sigma_absorbs_soldier_hitscan_in_front_without_a_body_hit(self):
        for scenario in ("clear", "wall", "friendly"):
            with self.subTest(scenario=scenario):
                run, soldier, sigma = self.setup_fight()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                if scenario == "friendly":
                    sigma.team = soldier.team
                x, y, z = soldier.mover.position
                match.switch_hero(soldier, find_hero("Soldier"))
                match.switch_hero(sigma, find_hero("Sigma"))
                self.place(run, sigma, (x + 0.75, y, z + 5))
                for _ in range(25):
                    run.step({})
                if scenario == "wall":
                    plane = z + 2
                    match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x - 5, plane, x + 5, plane, y0=y, y1=y + 5)
                    )
                origin = fight._eye(soldier)
                target = next(t for t in fight._targets() if t.owner is sigma)
                a, b = target.segment()
                self.assertIsNone(combat.ray_capsule(origin, combat.aim(0, 0), a, b, target.radius))
                run.step({sigma: (-32768, 0, 8)})
                instance = next(i for i in sigma.body_script.component.instances.values()
                                if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                run.step({sigma: (-32768, 0, 0), soldier: (0, 0, FIRE)})
                for _ in range(12):
                    run.step({sigma: (-32768, 0, 0)})
                absorbed = 20 if scenario == "clear" else 0
                self.assertEqual(instance.vars[21312].value(), absorbed)
                self.assertEqual(self.health(run, sigma).total, 400)
                self.assertFalse(soldier.client.messages(combat.HIT))
                if absorbed:
                    for _ in range(150):
                        run.step({sigma: (-32768, 0, 0)})
                    temporary = self.health(run, sigma).temporary_shields[sigma_grasp.SHIELD_TAG]
                    self.assertEqual(temporary.now, 12)

    def test_sigma_absorbs_a_reflected_soldier_shot_after_team_transfer(self):
        run, soldier, genji = self.setup_fight()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        x, y, z = soldier.mover.position
        match.switch_hero(soldier, find_hero("Soldier"))
        match.switch_hero(genji, find_hero("Genji"))
        self.place(run, genji, (x, y, z + 8))
        sigma = match.add_player(3, "Sigma", SOLDIER, soldier.team, False)
        in_world(match, sigma)
        match.switch_hero(sigma, find_hero("Sigma"))
        self.place(run, sigma, (x + 0.75, y, z + 3))
        run.players.append(sigma)
        for _ in range(25):
            run.step({})
        facing = (-32768, 0)
        run.step({genji: (*facing, 16), sigma: (0, 0, 8)})
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == 0x1D02)
        self.assertTrue(instance.states[1].active)
        for _ in range(4):
            run.step({genji: (*facing, 0)})
        outgoing = aim(fight._eye(soldier), (x, y + 1.0, z + 8))
        run.step({genji: (*facing, 0), soldier: (*outgoing, FIRE)})
        for _ in range(12):
            run.step({genji: (*facing, 0)})
        self.assertEqual(instance.vars[21312].value(), 20)
        self.assertEqual(self.health(run, soldier).total, 200)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, sigma).total, 400)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertFalse(soldier.client.messages(combat.HIT))

    def test_airborne_deployables_cross_active_grasp_and_still_park(self):
        for button, table in ((8, "mines"), (16, "traps")):
            with self.subTest(deployable=table):
                run, shooter, sigma = self.setup_fight()
                fight = run.match.combat
                x, y, z = shooter.mover.position
                run.match.switch_hero(sigma, find_hero("Sigma"))
                self.place(run, sigma, (x, y, z + 3.5))
                for _ in range(25):
                    run.step({sigma: (-32768, 0, 0)})
                run.step({sigma: (-32768, 0, 8)})
                instance = next(i for i in sigma.body_script.component.instances.values()
                                if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                run.step({sigma: (-32768, 0, 0), shooter: (0, 0, button)})
                deployables = getattr(fight.junkrat, table)
                for _ in range(35):
                    if deployables:
                        break
                    run.step({sigma: (-32768, 0, 0)})
                deployable, = deployables.values()
                self.assertFalse(deployable.parked)
                crossed = False
                for _ in range(65):
                    start = deployable.flight.position
                    run.step({sigma: (-32768, 0, 0)})
                    end = deployable.flight.position
                    length = math.dist(start, end)
                    if length:
                        direction = tuple((b - a) / length for a, b in zip(start, end, strict=True))
                        crossed |= sigma_grasp.grasp_intersection(fight, shooter, start, direction, length) is not None
                    if deployable.parked:
                        break
                self.assertTrue(crossed)
                self.assertTrue(deployable.parked)
                self.assertIn(deployable.entity, deployables)
                self.assertEqual(instance.vars[21312].value(), 0)

    def test_airborne_deployables_keep_owner_across_active_deflect(self):
        for button, table in ((8, "mines"), (16, "traps")):
            with self.subTest(deployable=table):
                run, shooter, genji = self.setup_fight()
                fight = run.match.combat
                x, y, z = shooter.mover.position
                run.match.switch_hero(genji, find_hero("Genji"))
                self.place(run, genji, (x, y, z + 3.5))
                for _ in range(25):
                    run.step({genji: (-32768, 0, 0)})
                run.step({genji: (-32768, 0, 16)})
                run.step({genji: (-32768, 0, 0), shooter: (0, 0, button)})
                deployables = getattr(fight.junkrat, table)
                for _ in range(35):
                    if deployables:
                        break
                    run.step({genji: (-32768, 0, 0)})
                deployable, = deployables.values()
                self.assertFalse(deployable.parked)
                body = next(target for target in fight._targets() if target.owner is genji)
                crossed = False
                for _ in range(65):
                    start = deployable.flight.position
                    run.step({genji: (-32768, 0, 0)})
                    end = deployable.flight.position
                    length = math.dist(start, end)
                    if length and fight._genji_deflects(body, deployable.flight.direction):
                        direction = tuple((b - a) / length for a, b in zip(start, end, strict=True))
                        a, b = body.segment()
                        contact = combat.ray_capsule(start, direction, a, b, body.radius)
                        crossed |= contact is not None and contact <= length
                    if deployable.parked:
                        break
                self.assertTrue(crossed)
                self.assertTrue(deployable.parked)
                self.assertIn(deployable.entity, deployables)
                self.assertIs(deployable.creator, shooter)
                self.assertIs(deployable.flight.shooter, shooter)
                self.assertEqual(deployable.source, shooter.body)

    def test_quick_melee_deflect_blocks_only_frontal_hits_without_reflecting_damage(self):
        for yaw, remaining in ((-32768, 200), (0, 170)):
            with self.subTest(yaw=yaw):
                run, shooter, genji = self.setup_fight()
                x, y, z = shooter.mover.position
                run.match.switch_hero(genji, find_hero("Genji"))
                self.place(run, genji, (x, y, z + 1.5))
                run.step({genji: (yaw, 0, 16)})
                for _ in range(4):
                    run.step({genji: (yaw, 0, 0)})
                run.step({genji: (yaw, 0, 0), shooter: (0, 0, 0x800)})
                for _ in range(15):
                    run.step({genji: (yaw, 0, 0)})
                self.assertEqual(self.health(run, genji).total, remaining)
                self.assertEqual(self.health(run, shooter).total, 200)

    def test_grasp_grants_shields_from_grenade_then_spends_or_decays_them(self):
        for spend in (True, False):
            with self.subTest(spend=spend):
                run, shooter, sigma = self.setup_fight()
                x, y, z = shooter.mover.position
                run.match.switch_hero(sigma, find_hero("Sigma"))
                self.place(run, sigma, (x, y, z + 4))
                run.step({sigma: (-32768, 0, 8)})
                run.step({sigma: (-32768, 0, 0), shooter: (0, 0, FIRE)})
                for _ in range(160):
                    run.step({sigma: (-32768, 0, 0)})
                fight = run.match.combat
                vitals = self.health(run, sigma)
                temporary = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
                self.assertEqual(temporary.now, 72)
                self.assertEqual(vitals.total, 472)
                self.assertEqual(vitals.maximum, 472)
                self.assertEqual(sigma.body_script.component.entity_var(1732).value(), 472)
                instance = next(iter(sigma.body_script.component.instances.values()))
                self.assertEqual(instance.evaluate({
                    "$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"},
                }), 472)
                if spend:
                    run.step({shooter: (0, 0, FIRE)})
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(temporary.now, 0)
                    self.assertEqual(vitals.now[combat.SHIELDS_POOL], 52)
                    self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
                else:
                    start = math.ceil(fight.grasp_shields[sigma.body].starts / combat.TICK)
                    run.match.update((start - 1) * combat.TICK, start - 1, frames=False)
                    self.assertEqual(temporary.now, 72)
                    run.match.update((start + 125) * combat.TICK, start + 125, frames=False)
                    self.assertAlmostEqual(temporary.now, 62, delta=.081)
                    self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)

    def test_gravity_survives_a_skipped_scheduler_tick(self):
        outcomes = []
        for advances in (range(1, 21), (20,)):
            run, shooter, victim = self.setup_fight()
            x, y, z = victim.mover.position
            self.place(run, victim, (x + 100, y, z))
            run.step({shooter: (0, 0, FIRE)})
            rocket, = run.match.combat.rockets
            self.assertEqual(rocket.last_tick, run.frame)
            initial = rocket.position
            for advance in advances:
                at = run.frame + advance
                run.match.update(at * combat.TICK, at, frames=False)
            duration = 20 * combat.TICK
            self.assertAlmostEqual(rocket.position[1], initial[1] - .5 * 12.25 * duration**2)
            self.assertAlmostEqual(rocket.position[2], initial[2] + 25 * duration)
            self.assertAlmostEqual(rocket.direction[1] * rocket.speed, -12.25 * duration)
            outcomes.append((rocket.position, rocket.direction, rocket.speed, rocket.flown, rocket.left))
        self.assertEqual(*outcomes)

    def test_ballistic_hit_clock_matches_contiguous_ticks(self):
        outcomes = []
        for advances in (range(1, 21), (20,)):
            run, shooter, victim = self.setup_fight()
            run.step({shooter: (0, 0, FIRE)})
            rocket, = run.match.combat.rockets
            self.assertEqual(rocket.last_tick, run.frame)
            for advance in advances:
                at = run.frame + advance
                run.match.update(at * combat.TICK, at, frames=False)
            hits = shooter.client.messages(combat.HIT)
            outcomes.append((self.health(run, victim).total, rocket.flown,
                             [hit["+0x84"] for hit in hits]))
        self.assertEqual(*outcomes)
        self.assertEqual(outcomes[0][0], 80)

    def test_frag_range_and_lifetime_are_independent_after_terrain_slowdown(self):
        for rebounds in (1, 2):
            with self.subTest(rebounds=rebounds):
                run, shooter, victim = self.setup_fight()
                x, y, z = shooter.mover.position
                self.place(run, victim, (x + 100, y, z))
                run.match.collision_world = World(
                    wall(x - 10, z + 4, x + 10, z + 4, y0=y, y1=y + 10)
                    + wall(x - 10, z - 4, x + 10, z - 4, y0=y, y1=y + 10)
                )
                run.step({shooter: (0, 0, FIRE)})
                fight = run.match.combat
                rocket, = fight.rockets
                self.assertEqual((rocket.left, rocket.max_distance), (10, 100))
                rocket.gravity = 0  # isolate the authored limits from the reconstructed gravity
                last = run.frame
                for offset in range(1, 90):
                    last = run.frame + offset
                    run.match.update(last * combat.TICK, last, frames=False)
                    if rocket.terrain_bounces == rebounds:
                        break
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(rocket.terrain_bounces, rebounds)
                self.assertAlmostEqual(rocket.speed, 25 * .5**rebounds)
                # Remove the fixture walls after the desired number of real contacts.
                run.match.collision_world = World([])
                after_old_cap = run.frame + math.ceil(4.1 / combat.TICK)
                run.match.update(after_old_cap * combat.TICK, after_old_cap, frames=False)
                self.assertIn(rocket, fight.rockets)
                self.assertGreater(rocket.flown, 4)
                with patch.object(fight, "_explode", wraps=fight._explode) as explode:
                    expiry = run.frame + math.ceil(10.1 / combat.TICK)
                    run.match.update(expiry * combat.TICK, expiry, frames=False)
                    run.match.update((expiry + 1) * combat.TICK, expiry + 1, frames=False)
                    explode.assert_called_once_with(rocket, rocket.position)
                self.assertNotIn(rocket, fight.rockets)
                if rebounds == 1:
                    self.assertAlmostEqual(rocket.distance_flown, 100)
                    self.assertLess(rocket.flown, 10)
                else:
                    self.assertAlmostEqual(rocket.flown, 10)
                    self.assertLess(rocket.distance_flown, 100)

    def test_frag_gravity_reaches_cumulative_range_before_launch_speed_estimate(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = shooter.mover.position
        self.place(run, victim, (x + 100, y, z))
        run.match.collision_world = World([])
        run.step({shooter: (0, 0, FIRE)})
        fight = run.match.combat
        rocket, = fight.rockets
        after_range = run.frame + math.ceil(4 / combat.TICK)
        with patch.object(fight, "_explode", wraps=fight._explode) as explode:
            run.match.update(after_range * combat.TICK, after_range, frames=False)
            explode.assert_called_once_with(rocket, rocket.position)
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.distance_flown, 100)
        self.assertLess(rocket.flown, 4)
        self.assertGreater(rocket.left, 6)

    def test_ricochet_at_range_endpoint_retires_before_the_fuse(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = victim.mover.position
        self.place(run, victim, (x + 100, y, z))
        run.step({shooter: (0, 0, FIRE)})
        fight = run.match.combat
        rocket, = fight.rockets
        rocket.max_distance = rocket.speed * combat.TICK / 2
        at = run.frame + 1
        with (
            patch.object(combat.contacts, "scene_sweep", return_value=(1.0, None, (0.0, 0.0, -1.0))),
            patch.object(fight, "_explode", wraps=fight._explode) as explode,
        ):
            run.match.update(at * combat.TICK, at, frames=False)
            self.assertNotIn(rocket, fight.rockets)
            explode.assert_called_once_with(rocket, rocket.position)
            run.match.update((at + 1) * combat.TICK, at + 1, frames=False)
            explode.assert_called_once()
        self.assertEqual(rocket.distance_flown, rocket.max_distance)
        self.assertGreater(rocket.left, 9)

    def test_ballistic_expiry_keeps_its_bound(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = victim.mover.position
        self.place(run, victim, (x + 100, y, z))
        run.step({shooter: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        self.assertEqual(rocket.last_tick, run.frame)
        rocket.left = .05
        at = run.frame + 20
        fight = run.match.combat
        with patch.object(fight, "_explode", wraps=fight._explode) as explode:
            run.match.update(at * combat.TICK, at, frames=False)
            run.match.update((at + 1) * combat.TICK, at + 1, frames=False)
            explode.assert_called_once_with(rocket, rocket.position)
        self.assertNotIn(rocket, run.match.combat.rockets)
        self.assertAlmostEqual(rocket.flown, .05)
        self.assertEqual(self.health(run, victim).total, 200)

    def test_ricochet_at_fuse_deadline_retires_in_the_same_tick(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = victim.mover.position
        self.place(run, victim, (x + 100, y, z))
        run.step({shooter: (0, 0, FIRE)})
        fight = run.match.combat
        rocket, = fight.rockets
        rocket.left = combat.TICK / 2
        # Report a contact in the sweep tolerance after the fuse endpoint.
        at = run.frame + 1
        with (
            patch.object(combat.contacts, "scene_sweep", return_value=(1.0, None, (0.0, 0.0, -1.0))),
            patch.object(fight, "_explode", wraps=fight._explode) as explode,
        ):
            run.match.update(at * combat.TICK, at, frames=False)
            self.assertNotIn(rocket, fight.rockets)
            explode.assert_called_once_with(rocket, rocket.position)
            run.match.update((at + 1) * combat.TICK, at + 1, frames=False)
            explode.assert_called_once()
        self.assertAlmostEqual(rocket.flown, combat.TICK / 2)

    def install_wall(self, run, shooter):
        x, y, z = shooter.mover.position
        z += 4
        run.match.collision_world = World([
            (x - 100, y, z - 100, x + 100, y, z - 100, x + 100, y, z + 100),
            (x - 100, y, z - 100, x + 100, y, z + 100, x - 100, y, z + 100),
            (x - 10, y, z, x + 10, y, z, x + 10, y + 10, z),
            (x - 10, y, z, x + 10, y + 10, z, x - 10, y + 10, z),
        ])

    def test_static_wall_blocks_actual_grenade_splash(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = shooter.mover.position
        self.place(run, victim, (x, y, z + 4.8))
        self.install_wall(run, shooter)
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, victim).total, 200)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_wall_ricochet_can_hit_a_body_behind_the_shooter(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = shooter.mover.position
        self.place(run, victim, (x, y, z - 1))
        self.install_wall(run, shooter)
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(20):
            run.step({})
        rocket, = run.match.combat.rockets
        self.assertLess(rocket.direction[2], 0)
        self.assertEqual(self.health(run, victim).total, 200)
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, victim).total, 80)
        self.assertNotIn(rocket, run.match.combat.rockets)

    def test_oblique_terrain_hit_uses_authored_normal_and_tangent_response(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = shooter.mover.position
        self.place(run, victim, (x + 100, y, z))
        self.install_wall(run, shooter)
        fight = run.match.combat
        run.step({shooter: (0, 0, FIRE)})
        rocket, = fight.rockets
        rocket.gravity = 0.0
        rocket.direction = (0.6, 0.0, 0.8)
        for _ in range(30):
            run.step({})
            if rocket.terrain_bounces == 1:
                break
        self.assertEqual(rocket.terrain_bounces, 1)
        self.assertAlmostEqual(rocket.direction[0] * rocket.speed, (15 - 4.8) * .75)
        self.assertAlmostEqual(rocket.direction[1] * rocket.speed, 0)
        self.assertAlmostEqual(rocket.direction[2] * rocket.speed, -10)

    def test_terrain_contact_threshold_retires_without_blast(self):
        for starting_count in (0, 99):
            with self.subTest(starting_count=starting_count):
                run, shooter, victim = self.setup_fight()
                x, y, z = shooter.mover.position
                self.place(run, victim, (x + 1.25, y, z - 3.3))
                run.match.collision_world = World(
                    wall(x - 10, z + 4, x + 10, z + 4, y0=y, y1=y + 10)
                    + wall(x - 10, z - 4, x + 10, z - 4, y0=y, y1=y + 10)
                )
                fight = run.match.combat
                run.step({shooter: (0, 0, FIRE)})
                rocket, = fight.rockets
                self.assertEqual((rocket.terrain_bounces, rocket.terrain_bounce_limit), (0, 100))
                rocket.gravity = 0.0
                rocket.terrain_bounces = starting_count
                with patch.object(fight, "_explode", wraps=fight._explode) as explode:
                    for expected in range(starting_count + 1, 4 if not starting_count else 102):
                        for _ in range(100):
                            run.step({})
                            if rocket.terrain_bounces == expected:
                                break
                        self.assertEqual(rocket.terrain_bounces, expected)
                        if expected <= 100:
                            self.assertIn(rocket, fight.rockets)
                            self.assertAlmostEqual(rocket.speed, 25 * .5**(expected - starting_count))
                            explode.assert_not_called()
                    if starting_count:
                        self.assertNotIn(rocket, fight.rockets)
                        self.assertGreater(rocket.left, 0)
                        self.assertLess(rocket.distance_flown, rocket.max_distance)
                        self.assertEqual(rocket.speed, 0)
                        run.step({})
                        explode.assert_not_called()
                self.assertEqual(self.health(run, victim).total, 200)

    def test_ricochet_catchup_matches_contiguous_world_updates(self):
        def fly(stride):
            run, shooter, victim = self.setup_fight()
            x, y, z = shooter.mover.position
            self.place(run, victim, (x, y, z - 1))
            self.install_wall(run, shooter)
            run.step({shooter: (0, 0, FIRE)})
            fight = run.match.combat
            rocket, = fight.rockets
            first = run.frame
            for tick in range(first + stride, first + 48 + 1, stride):
                run.match.update(tick * combat.TICK, tick, frames=False)
            return self.health(run, victim).total, rocket.flown, rocket.terrain_bounces

        continuous = fly(1)
        self.assertEqual(continuous[0], 80)
        self.assertEqual(fly(8), continuous)

    def test_enemy_primary_can_damage_junkrat(self):
        run, junkrat, enemy = self.setup_fight()
        direction = aim(run.match.combat._eye(enemy), run.match.combat._eye(junkrat))
        run.step({enemy: (*direction, FIRE)})
        self.assertEqual(self.health(run, junkrat).total, 160)

    def test_grenade_damage_and_retained_death_of_a_training_bot(self):
        run = Run()
        shooter, = run.players
        run.match.switch_hero(shooter, find_hero("Junkrat"))
        bot = next(bot for bot in run.match.bots if bot.team != shooter.team and not bot.walking())
        run.match.bots[:] = [bot]
        run.match._pending_bot_scripts.clear()
        bot.retain_corpse = True
        bot.body_script = None
        x, y, z = shooter.mover.position
        bot.position = [x, y, z + 6]
        for _ in range(30):
            run.step({})
        for _ in range(2):
            run.step({shooter: (0, 0, FIRE)})
            for _ in range(45):
                run.step({})
        self.assertTrue(bot.dead)
        self.assertEqual(self.health(run, bot).total, 0)
        for _ in range(math.ceil(combat.BOT_RESPAWN / combat.TICK) + 10):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))

    def test_primary_miss_and_full_magazine_auto_reload(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = victim.mover.position
        self.place(run, victim, (x + 100, y, z))
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x22B)
        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as fired:
            for _ in range(201):
                run.step({shooter: (0, 0, FIRE)})
            self.assertEqual((weapon.vars[53].value(), fired.call_count), (0, 5))
            for _ in range(40):
                run.step({shooter: (0, 0, FIRE)})
            self.assertEqual(fired.call_count, 5)  # empty/reloading cannot bypass the magazine
            for _ in range(100):
                run.step({})
            self.assertEqual(weapon.vars[53].value(), 5)
            run.step({shooter: (0, 0, FIRE)})
            self.assertEqual((weapon.vars[53].value(), fired.call_count), (4, 6))
        self.assertEqual(self.health(run, victim).total, 200)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_held_primary_crosses_two_reload_handoffs_without_stale_gate_or_ammo_loss(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = victim.mover.position
        self.place(run, victim, (x + 100, y, z))
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x22B)
        rows = []
        original = run.match.combat._shoot

        def shoot(*args, **kwargs):
            if args[0] is shooter:
                rows.append((run.frame, args[2].ammo0, weapon.vars[53].value()))
            return original(*args, **kwargs)

        with patch.object(run.match.combat, '_shoot', side_effect=shoot):
            for _ in range(1000):
                run.step({shooter: (0, 0, FIRE)})
                if len(rows) >= 15:
                    break
        self.assertEqual([row[1] for row in rows], [5, 4, 3, 2, 1] * 3)
        self.assertEqual([row[2] for row in rows], [4, 3, 2, 1, 0] * 3)
        normal_gaps = [rows[i][0] - rows[i - 1][0] for i in range(1, 15) if i not in (5, 10)]
        for index in (5, 10):
            self.assertGreater(rows[index][0] - rows[index - 1][0], max(normal_gaps))
        self.assertEqual(self.health(run, victim).total, 200)

    def test_primary_does_not_hit_a_teammate(self):
        run, shooter, ally = self.setup_fight()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = shooter.team
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, ally).total, 200)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_manual_reload_refills_spent_ammo_and_prevents_fire_during_reload(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = victim.mover.position
        self.place(run, victim, (x + 100, y, z))
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x22B)
        run.step({shooter: (0, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 4)
        for _ in range(50):
            run.step({})
        run.step({shooter: (0, 0, 0x400)})
        for _ in range(20):
            run.step({shooter: (0, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 4)
        for _ in range(150):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 5)

    def test_actual_melee_hit_and_cooldown(self):
        run, shooter, victim = self.setup_fight()
        x, y, z = shooter.mover.position
        self.place(run, victim, (x, y, z + 1.5))
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(15):
            run.step({})
        self.assertEqual(self.health(run, victim).total, 170)
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(15):
            run.step({})
        self.assertEqual(self.health(run, victim).total, 170)
        for _ in range(70):
            run.step({})
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(15):
            run.step({})
        self.assertEqual(self.health(run, victim).total, 140)

    def test_quick_melee_respects_wraith_and_cryo_then_hits_after_expiry(self):
        for name, active in (("Reaper", immunity.wraith_active), ("Mei", immunity.cryo_active)):
            with self.subTest(hero=name):
                run, shooter, victim = self.setup_fight()
                x, y, z = shooter.mover.position
                run.match.switch_hero(victim, find_hero(name))
                self.place(run, victim, (x, y, z + 1.5))
                for _ in range(25):
                    run.step({})
                run.step({victim: (0, 0, 8)})
                self.assertTrue(active(victim, victim.body))
                run.step({shooter: (0, 0, 0x800)})
                for _ in range(20):
                    run.step({})
                self.assertTrue(active(victim, victim.body))
                self.assertEqual(self.health(run, victim).total, 250)
                self.assertFalse(shooter.client.messages(combat.HIT))
                for _ in range(320):
                    if not active(victim, victim.body):
                        break
                    run.step({})
                self.assertFalse(active(victim, victim.body))
                self.assertEqual(self.health(run, victim).total, 250)
                run.step({shooter: (0, 0, 0x800)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(self.health(run, victim).total, 220)

    def test_quick_melee_deals_scaled_fortify_damage_and_bypasses_grasp(self):
        for name in ("Orisa", "Sigma"):
            with self.subTest(hero=name):
                run, shooter, victim = self.setup_fight()
                x, y, z = shooter.mover.position
                run.match.switch_hero(victim, find_hero(name))
                self.place(run, victim, (x, y, z + 1.5))
                for _ in range(25):
                    run.step({})
                health = self.health(run, victim)
                before = health.total
                run.step({victim: (-32768, 0, 8)})
                run.step({victim: (-32768, 0, 0), shooter: (0, 0, 0x800)})
                for _ in range(20):
                    run.step({victim: (-32768, 0, 0)})
                if name == "Orisa":
                    scale = immunity.damage_scale(victim, victim.body)
                    self.assertLess(scale, 1)
                    amount = 30 * scale
                    self.assertAlmostEqual(before - health.total, max(amount - 5, amount / 2))
                else:
                    instance = next(i for i in victim.body_script.component.instances.values()
                                    if i.graph.index == 0x1D02)
                    self.assertTrue(instance.states[1].active)
                    self.assertEqual(instance.vars[21312].value(), 0)
                    self.assertEqual(before - health.total, 30)

    def test_quick_melee_ignores_same_team_body(self):
        run, shooter, ally = self.setup_fight()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = shooter.team
        x, y, z = shooter.mover.position
        self.place(run, ally, (x, y, z + 1.5))
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, ally).total, 200)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_melee_miss_out_of_range_and_enemy_behind_wall(self):
        for barrier in (False, True):
            with self.subTest(barrier=barrier):
                run, shooter, victim = self.setup_fight()
                x, y, z = shooter.mover.position
                if barrier:
                    self.place(run, victim, (x, y, z + 1.5))
                    run.match.collision_world = World([
                        (x - 10, y, z + .7, x + 10, y, z + .7, x, y + 10, z + .7),
                    ])
                run.step({shooter: (0, 0, 0x800)})
                for _ in range(15):
                    run.step({})
                self.assertEqual(self.health(run, victim).total, 200)

    def test_dead_junkrat_cannot_use_primary_or_melee(self):
        run, junkrat, enemy = self.setup_fight()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is junkrat)
        fight._damage(enemy, target, 200, False, None, run.frame)
        self.assertTrue(fight.is_dead(junkrat))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
            for _ in range(60):
                run.step({junkrat: (0, 0, FIRE | 0x800)})
            # Total Mayhem must eventually be allowed to report its own death volleys.
            self.assertTrue(all(call.args[2].instance.graph.index == 0x22A for call in fired.call_args_list))
        self.assertEqual(self.health(run, enemy).total, 200)

    def test_cryo_freeze_takes_no_grenade_damage_until_expiry(self):
        run, shooter, mei = self.setup_fight()
        mei.spawn = (tuple(mei.mover.position), mei.spawn[1])
        run.match.switch_hero(mei, find_hero("Mei"))
        for _ in range(25):
            run.step({})
        run.step({mei: (0, 0, 8)})
        self.assertTrue(immunity.cryo_active(mei, mei.body))
        run.step({shooter: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        for _ in range(30):
            run.step({})
        self.assertNotIn(rocket, run.match.combat.rockets)
        self.assertEqual(self.health(run, mei).total, 250)
        self.assertFalse(shooter.client.messages(combat.HIT))
        for _ in range(250):
            run.step({})
        self.assertFalse(immunity.cryo_active(mei, mei.body))
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, mei).total, 130)

    def test_actual_soldier_kill_triggers_total_mayhem_against_personal_barrier(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run = Run(KANEZAKA_DM, ("Junkrat", "Soldier", "Zarya"))
                junkrat, soldier, zarya = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(junkrat, find_hero("Junkrat"))
                run.match.switch_hero(zarya, find_hero("Zarya"))
                x, y, z = junkrat.mover.position
                self.place(run, soldier, (x, y, z - 6))
                self.place(run, zarya, (x + 1.4, y, z))
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                fire_at = aim(fight._eye(soldier), fight._eye(junkrat))
                for _ in range(110):
                    run.step({soldier: (*fire_at, FIRE)})
                    if fight.is_dead(junkrat):
                        break
                self.assertTrue(fight.is_dead(junkrat))
                self.assertEqual(self.health(run, junkrat).killer, soldier.body)
                self.assertEqual(len([rocket for rocket in fight.rockets if rocket.shooter is junkrat]), 6)
                bubble = None
                if protected:
                    run.step({zarya: (0, 0, 8)})
                    bubble = fight.barriers.sync(zarya)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                with patch.object(fight, "_explode", wraps=fight._explode) as explode:
                    for _ in range(100):
                        run.step({})
                    self.assertEqual(sum(call.args[0].shooter is junkrat for call in explode.call_args_list), 6)
                self.assertFalse(any(rocket.shooter is junkrat for rocket in fight.rockets))
                if protected:
                    self.assertEqual(bubble.health, 0)
                    self.assertFalse(bubble.phase.active)
                # Four fuse blasts reach her body in this fixture. The wider barrier intercepts four blasts.
                self.assertEqual(self.health(run, zarya).total, 350 if protected else 200)
                hits = junkrat.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [50] * (1 if protected else 4))

    def test_total_mayhem_reports_six_volleys_without_dead_player_input(self):
        run, junkrat, enemy = self.setup_fight()
        x, y, z = junkrat.mover.position
        self.place(run, enemy, (x + 1, y, z))
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is junkrat)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
            fight._damage(enemy, target, 200, False, None, run.frame)
            self.assertEqual(fired.call_count, 6)
            self.assertTrue(all(call.args[2].instance.graph.index == 0x22A for call in fired.call_args_list))
            self.assertEqual(len(fight.rockets), 6)
            self.assertTrue(all(rocket.explode_on_expiry for rocket in fight.rockets))
            fight._damage(enemy, target, 200, False, None, run.frame)  # duplicate death cannot retrigger
            self.assertEqual(fired.call_count, 6)
            for offset in range(1, 100):
                at = run.frame + offset
                run.match.update(at * combat.TICK, at, frames=False)
            self.assertTrue(fight.is_dead(enemy))
            self.assertEqual(fired.call_count, 6)
        hits = junkrat.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [50, 50, 50, 50])
        self.assertEqual(self.health(run, enemy).killer, junkrat.body)

    def test_total_mayhem_blast_retains_attribution_through_deflect_and_grasp(self):
        cases = (("Genji", 16, 0x03A3, 10), ("Sigma", 8, 0x1D02, 1))
        for name, button, graph_id, state_index in cases:
            with self.subTest(hero=name):
                run, junkrat, enemy = self.setup_fight()
                fight = run.match.combat
                x, y, z = junkrat.mover.position
                run.match.switch_hero(enemy, find_hero(name))
                self.place(run, enemy, (x + 1, y, z))
                for _ in range(25):
                    run.step({})
                facing = aim(fight._eye(enemy), fight._eye(junkrat))
                run.step({enemy: (*facing, button)})
                instance = next(i for i in enemy.body_script.component.instances.values()
                                if i.graph.index == graph_id)
                state = instance.states.get(state_index)
                self.assertIsNotNone(state)
                self.assertTrue(state.active)
                target = next(t for t in fight._targets() if t.owner is junkrat)
                owner_body = junkrat.body
                fight._damage(enemy, target, 200, False, None, run.frame)
                self.assertEqual(len(fight.rockets), 6)
                enemy_health = self.health(run, enemy)
                initial_health = enemy_health.total
                explode = fight._explode
                protected_hit = False

                def observe(rocket, point):
                    nonlocal protected_hit
                    before, active = enemy_health.total, state.active
                    result = explode(rocket, point)
                    protected_hit |= active and enemy_health.total < before
                    return result

                with patch.object(fight, "_explode", side_effect=observe):
                    for _ in range(100):
                        run.step({enemy: (*facing, 0)})
                        if not fight.rockets:
                            break
                self.assertTrue(protected_hit)
                self.assertFalse(fight.rockets)
                hits = junkrat.client.messages(combat.HIT)
                self.assertTrue(hits)
                self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == enemy.body for hit in hits))
                if name == "Sigma":
                    self.assertEqual(instance.vars[21312].value(), 0)
                    self.assertLess(enemy_health.total, initial_health)
                else:
                    self.assertTrue(enemy_health.dead)
                    self.assertEqual(enemy_health.killer, owner_body)

    def test_total_mayhem_respects_active_wraith_and_cryo(self):
        cases = (
            ("Reaper", immunity.wraith_active, False),
            ("Mei", immunity.cryo_active, False),
            ("Mei", immunity.cryo_active, True),
        )
        for name, active, cancel in cases:
            with self.subTest(hero=name, cancel=cancel):
                run, junkrat, enemy = self.setup_fight()
                fight = run.match.combat
                x, y, z = junkrat.mover.position
                run.match.switch_hero(enemy, find_hero(name))
                self.place(run, enemy, (x + 1, y, z))
                for _ in range(25):
                    run.step({})
                run.step({enemy: (0, 0, 8)})
                self.assertTrue(active(enemy, enemy.body))
                target = next(t for t in fight._targets() if t.owner is junkrat)
                body = junkrat.body
                fight._damage(enemy, target, 200, False, None, run.frame)
                self.assertEqual(len(fight.rockets), 6)
                if cancel:
                    for _ in range(20):
                        run.step({})
                    run.step({enemy: (0, 0, 2)})
                    self.assertFalse(active(enemy, enemy.body))
                for _ in range(100):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                health = self.health(run, enemy)
                if cancel:
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, body)
                else:
                    self.assertTrue(active(enemy, enemy.body))
                    self.assertEqual(health.total, 250)
                    self.assertFalse(junkrat.client.messages(combat.HIT))

    def test_total_mayhem_respects_team_and_wall_at_detonation(self):
        for mode in ("clear", "ally", "wall"):
            with self.subTest(mode=mode):
                run, junkrat, target = self.setup_fight()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = junkrat.mover.position
                self.place(run, target, (x, y, z + 1.5))
                for _ in range(25):
                    run.step({})
                body = next(t for t in fight._targets() if t.owner is junkrat)
                fight._damage(target, body, 200, False, None, run.frame)
                self.assertEqual(len(fight.rockets), 6)
                if mode == "ally":
                    target.team = junkrat.team
                elif mode == "wall":
                    plane = z + .75
                    match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x - 15, plane, x + 15, plane, y0=y, y1=y + 10)
                    )
                for _ in range(100):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                health = self.health(run, target)
                if mode == "clear":
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, body.entity)
                else:
                    self.assertEqual(health.total, 200)
                    self.assertFalse(junkrat.client.messages(combat.HIT))

    def test_fortify_scales_each_total_mayhem_blast_before_armour(self):
        run, junkrat, orisa = self.setup_fight()
        fight = run.match.combat
        x, y, z = junkrat.mover.position
        run.match.switch_hero(orisa, find_hero("Orisa"))
        self.place(run, orisa, (x + 1, y, z))
        for _ in range(25):
            run.step({})
        run.step({orisa: (0, 0, 8)})
        scale = immunity.damage_scale(orisa, orisa.body)
        self.assertLess(scale, 1)
        body = next(t for t in fight._targets() if t.owner is junkrat)
        fight._damage(orisa, body, 200, False, None, run.frame)
        self.assertEqual(len(fight.rockets), 6)
        impact = fight.rockets[0].direct.amount
        blast = fight.rockets[0].blast.amount
        health = self.health(run, orisa)
        initial = health.total
        damage = fight._damage
        events = []

        def observe(source, target, amount, *args, **kwargs):
            if source is junkrat and target.owner is orisa:
                self.assertAlmostEqual(immunity.damage_scale(orisa, orisa.body), scale)
                self.assertGreater(health.now[combat.ARMOUR_POOL], 0)
                before = health.total
                damage(source, target, amount, *args, **kwargs)
                reduced = amount * scale
                self.assertAlmostEqual(before - health.total, max(reduced - 5, reduced / 2))
                events.append((amount, before - health.total))
            else:
                damage(source, target, amount, *args, **kwargs)

        with patch.object(fight, "_damage", side_effect=observe):
            for _ in range(100):
                run.step({})
                if not fight.rockets:
                    break
        self.assertFalse(fight.rockets)
        self.assertCountEqual([amount for amount, _ in events], [impact, *([blast] * 6)])
        self.assertAlmostEqual(initial - health.total, sum(applied for _, applied in events))

    def test_total_mayhem_scatters_six_actual_death_projectiles(self):
        run, junkrat, enemy = self.setup_fight()
        fight = run.match.combat
        run.match.collision_world = World([])
        body = next(target for target in fight._targets() if target.owner is junkrat)
        fight._damage(enemy, body, 200, False, None, run.frame)
        rockets = tuple(fight.rockets)
        self.assertEqual(len(rockets), 6)
        self.assertEqual(len({rocket.position for rocket in rockets}), 1)
        self.assertEqual(len({rocket.direction for rocket in rockets}), 6)
        for rocket in rockets:
            self.assertAlmostEqual(sum(axis * axis for axis in rocket.direction), 1, places=5)
        for _ in range(10):
            run.step({})
        self.assertEqual(len({rocket.position for rocket in rockets}), 6)
        self.assertTrue(all(rocket in fight.rockets for rocket in rockets))

    def test_total_mayhem_rebounds_from_floor_and_walls_until_six_fuses(self):
        for walled in (False, True):
            with self.subTest(walled=walled):
                run, junkrat, enemy = self.setup_fight()
                x, y, z = junkrat.mover.position
                fight = run.match.combat
                fire_at = aim(fight._eye(enemy), fight._eye(junkrat))
                for _ in range(120):
                    run.step({enemy: (*fire_at, FIRE)})
                    if fight.is_dead(junkrat):
                        break
                self.assertTrue(fight.is_dead(junkrat))
                self.assertEqual(self.health(run, junkrat).killer, enemy.body)
                rockets = tuple(fight.rockets)
                self.assertEqual(len(rockets), 6)
                self.assertEqual({rocket.terrain_bounce_limit for rocket in rockets}, {9999})
                for rocket in rockets:
                    restitution, friction, retention = rocket.terrain_bounce
                    self.assertEqual((restitution, retention), (.5, 1.0))
                    self.assertAlmostEqual(friction, .0016)
                deadlines = [rocket.flown + rocket.left for rocket in rockets]
                terrain = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                if walled:
                    for plane in (z - .25, z + .25):
                        terrain += wall(x - 15, plane, x + 15, plane, y0=y, y1=y + 10)
                run.match.collision_world = World(terrain)
                first = run.frame
                floor_contacts = set()
                wall_contacts = set()
                with patch.object(fight, "_explode", wraps=fight._explode) as explode:
                    for offset in range(1, 100):
                        at = first + offset
                        prior = [(rocket.terrain_bounces, rocket.direction) for rocket in rockets]
                        run.match.update(at * combat.TICK, at, frames=False)
                        for index, (rocket, (count, direction)) in enumerate(zip(rockets, prior, strict=True)):
                            if rocket.terrain_bounces > count:
                                if direction[1] < 0 < rocket.direction[1]:
                                    floor_contacts.add(index)
                                if direction[2] * rocket.direction[2] < 0:
                                    wall_contacts.add(index)
                    self.assertEqual(explode.call_count, 6)
                    for rocket, deadline in zip(rockets, deadlines, strict=True):
                        self.assertAlmostEqual(rocket.flown, deadline)
                        self.assertEqual(sum(call.args[0] is rocket for call in explode.call_args_list), 1)
                self.assertEqual(len(floor_contacts), 6)
                self.assertEqual(bool(wall_contacts), walled)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, enemy).total, 200)

    def test_total_mayhem_catchup_keeps_blast_order_and_damage(self):
        def fly(stride):
            run, junkrat, enemy = self.setup_fight()
            x, y, z = junkrat.mover.position
            fight = run.match.combat
            fire_at = aim(fight._eye(enemy), fight._eye(junkrat))
            for _ in range(120):
                run.step({enemy: (*fire_at, FIRE)})
                if fight.is_dead(junkrat):
                    break
            self.assertTrue(fight.is_dead(junkrat))
            rockets = tuple(fight.rockets)
            self.assertEqual(len(rockets), 6)
            enemy.mover.position = [x + 1.4, y, z]
            run.match.collision_world = World(box(x - 15, x + 15, y - 1, y, z - 15, z + 15))
            launch_tick = run.frame
            blasts = []
            explode = fight._explode

            def observe(rocket, point, **kwargs):
                blasts.append((fight.tick - launch_tick, rockets.index(rocket), point))
                return explode(rocket, point, **kwargs)

            with patch.object(fight, "_explode", side_effect=observe):
                for offset in range(stride, 97, stride):
                    at = launch_tick + offset
                    run.match.update(at * combat.TICK, at, frames=False)
            self.assertFalse(fight.rockets)
            self.assertEqual(len(blasts), 6)
            self.assertTrue(all(rocket.terrain_bounces for rocket in rockets))
            hits = [hit["+0x78"]["+0x4"] for hit in junkrat.client.messages(combat.HIT)]
            self.assertTrue(hits)
            return blasts, hits, self.health(run, enemy).total

        contiguous = fly(1)
        self.assertEqual([tick for tick, _, _ in contiguous[0]], sorted(tick for tick, _, _ in contiguous[0]))
        for stride in (8, 96):
            with self.subTest(stride=stride):
                self.assertEqual(fly(stride), contiguous)

    def test_total_mayhem_has_six_bounded_fuses(self):
        run, junkrat, enemy = self.setup_fight()
        fight = run.match.combat
        run.match.collision_world = World([])  # leave the six fuses uninterrupted by an impact
        target = next(t for t in fight._targets() if t.owner is junkrat)
        fight._damage(enemy, target, 200, False, None, run.frame)
        projectiles = list(fight.rockets)
        self.assertEqual(len(projectiles), 6)
        self.assertEqual({rocket.max_distance for rocket in projectiles}, {100})
        bounds = sorted(rocket.left for rocket in projectiles)
        for actual, expected in zip(bounds, (.8, .9, 1, 1.1, 1.2, 1.3), strict=True):
            self.assertAlmostEqual(actual, expected, places=5)
        for offset in range(1, 100):
            at = run.frame + offset
            run.match.update(at * combat.TICK, at, frames=False)
        self.assertFalse(fight.rockets)
        for rocket, bound in zip(sorted(projectiles, key=lambda r: r.flown), bounds, strict=True):
            self.assertAlmostEqual(rocket.flown, bound)
        self.assertEqual(self.health(run, enemy).total, 200)


if __name__ == "__main__":
    unittest.main()
