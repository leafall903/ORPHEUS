"""104319 owner-only hammer repair versus allied/enemy deployables."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_torbjorn_defenses as defenses
import test_game_torbjorn_turret as turret_tests
from test_game_combat import aim, in_world
from test_game_mover import box
from test_game_mover import wall as wall_mesh

from ow174.game import fortify, hero_torbjorn, npc, npc_control, phase_defenses, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared turret bundle")
class TorbjornSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        turret_tests.TorbjornTurretTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        turret_tests.TorbjornTurretTests.tearDownClass()

    def arena(self, *, friendly=True, free_for_all=False, own=False):
        fixture = turret_tests.TorbjornTurretTests()
        run, helper, other, _ = fixture.arena(100.0, deploy=False)
        self.assertTrue(run.match.switch_hero(other, heroes()[hero_torbjorn.HERO]))
        x, y, z = helper.mover.position
        other.mover.position = [x, y, z + 100]
        run.match.game_map = replace(run.match.game_map, free_for_all=free_for_all)
        other.team = helper.team if friendly else 1 - helper.team
        turret = hero_torbjorn.deploy_turret(run.match.combat, helper if own else other,
                                           (x, y, z + 1.5))
        fixture.advance(run, 190)
        hp = run.match.combat.vitals[turret.actor.entity]
        hp.damage(80.0)
        run.match.combat._sync_body_health(turret.actor, hp)
        run.frame += 1
        helper.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, 45)
        return run, helper, other, turret, hp

    @staticmethod
    def swing(run, helper):
        for _ in range(45):
            run.step({helper: (0, 0, 1)})

    def test_other_owner_turret_is_never_repaired_and_hostile_one_takes_hammer_damage(self):
        for friendly, ffa, expected in ((True, False, 170.0), (False, False, 115.0),
                                         (True, True, 115.0)):
            with self.subTest(friendly=friendly, free_for_all=ffa):
                run, helper, _, _, hp = self.arena(friendly=friendly, free_for_all=ffa)
                self.swing(run, helper)
                self.assertEqual(hp.total, expected)
                self.assertEqual(helper.stats.value(stats.HERO_DAMAGE), 170.0 - expected)

    def test_one_swing_repairs_own_actor_and_leaves_overlapping_allied_actor_unchanged(self):
        run, helper, _, other, hp = self.arena()
        x, y, z = other.actor.position
        own = hero_torbjorn.deploy_turret(run.match.combat, helper, (x + 0.2, y, z))
        turret_tests.TorbjornTurretTests.advance(run, 190)
        own_hp = run.match.combat.vitals[own.actor.entity]
        own_hp.damage(80.0)
        run.match.combat._sync_body_health(own.actor, own_hp)
        self.swing(run, helper)
        self.assertEqual(own_hp.total, 220.0)
        self.assertEqual(hp.total, 170.0)
        self.assertEqual(helper.stats.value(stats.HERO_DAMAGE), 0.0)

    def test_owned_repair_caps_at_maximum_without_damage_or_combat_lock_side_effect(self):
        run, helper, _, turret, hp = self.arena(own=True)
        hp.heal(70.0)
        self.assertEqual(hp.total, 240.0)
        self.swing(run, helper)
        self.assertEqual(hp.total, 250.0)
        self.assertIsNone(turret.last_damaged)
        self.assertEqual(helper.stats.value(stats.HERO_DAMAGE), 0.0)

    def test_authored_repair_filter_compares_candidate_with_owned_actor(self):
        condition = graph.graph(0x168C).node(33).field("m_filter")["m_condition"]
        self.assertEqual(expr.source(condition), "(param(8) == #679e)")
        _, helper, _, turret, _ = self.arena(own=True)

        class Candidate(expr.Context):
            def param(self, key):
                return self.candidate if key == 8 else None

            def var(self, scope, key):
                return helper.body_script.component.entity_var(key).value()

        context = Candidate()
        context.candidate = expr.Entity(turret.actor.entity)
        self.assertTrue(expr.evaluate(condition, context))
        context.candidate = expr.Entity(turret.actor.entity + 1)
        self.assertFalse(expr.evaluate(condition, context))

    def test_repair_uses_owner_centred_sphere_and_real_wall_occlusion(self):
        for position, wall, expected in ((-1.5, False, 220.0), (2.5, False, 170.0),
                                           (1.5, True, 170.0)):
            with self.subTest(position=position, wall=wall):
                run, helper, _, turret, hp = self.arena(own=True)
                x, y, z = helper.mover.position
                turret.actor.position = (x, y, z + position)
                if wall:
                    front = z + 0.75
                    run.match.collision_world = World([
                        (x-100, y, z-100, x-100, y, z+100, x+100, y, z+100),
                        (x-100, y, z-100, x+100, y, z+100, x+100, y, z-100),
                        (x-3, y, front, x+3, y, front, x+3, y+4, front),
                        (x-3, y, front, x+3, y+4, front, x-3, y+4, front),
                    ], name="repair-wall")
                self.swing(run, helper)
                self.assertEqual(hp.total, expected)

    def test_repair_capsule_contact_on_both_sides_of_authored_range(self):
        for inside in (True, False):
            with self.subTest(inside=inside):
                run, helper, _, turret, hp = self.arena(own=True)
                fight = run.match.combat
                track = graph.graph(0x168C).node(33)
                instance = next(
                    i for i in helper.body_script.component.instances.values() if i.graph.index == 0x168C
                )
                offset = instance.evaluate(track.field("m_05BE62DD"))
                reach = instance.evaluate(track.field("m_radius"))
                target = next(target for target in fight._targets() if target.owner is turret.actor)
                lower, upper = target.segment()
                x, y, z = helper.mover.position
                centre_y = y + offset.y
                vertical = max(lower[1] - centre_y, centre_y - upper[1], 0)
                horizontal = math.sqrt((reach + target.radius) ** 2 - vertical ** 2)
                gap = horizontal + (-.01 if inside else .01)
                turret.actor.position = (x + offset.x, y, z + offset.z + gap)
                self.swing(run, helper)
                self.assertEqual(hp.total, 220 if inside else 170)

    def test_wall_removal_only_recovers_repair_inside_the_same_swing_window(self):
        for opening in (20, 33):
            with self.subTest(opening=opening):
                run, helper, _, turret, hp = self.arena(own=True)
                match = run.match
                x, y, z = helper.mover.position
                clear = match.collision_world
                match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall_mesh(x - 3, z + .75, x + 3, z + .75, y0=y, y1=y + 4)
                )
                for _ in range(opening):
                    run.step({helper: (0, 0, 1)})
                contact = match.combat.hammer_contacts[helper.body]
                self.assertNotIn(turret.actor.entity, contact.repaired)
                self.assertEqual(hp.total, 170)
                match.collision_world = clear
                for _ in range(8):
                    run.step({})
                self.assertEqual(hp.total, 220 if opening == 20 else 170)
                self.assertEqual(turret.actor.entity in contact.repaired, opening == 20)
                if opening == 33:
                    self.swing(run, helper)
                    self.assertEqual(hp.total, 220)

    def test_overload_uses_earlier_authored_impact_for_repair(self):
        run, helper, _, _, hp = self.arena(own=True)
        run.step({helper: (0, 0, 16)})
        turret_tests.TorbjornTurretTests.advance(run, 45)
        for _ in range(9):
            run.step({helper: (0, 0, 1)})
        self.assertEqual(hp.total, 170.0)
        run.step({helper: (0, 0, 1)})
        self.assertEqual(hp.total, 220.0)

    def test_owned_turret_entering_late_repairs_once_and_outside_window_does_not(self):
        for entry in (20, 33):
            with self.subTest(entry=entry):
                run, helper, _, turret, hp = self.arena(own=True)
                x, y, z = helper.mover.position
                turret.actor.position = (x, y, z + 5)
                for _ in range(entry):
                    run.step({helper: (0, 0, 1)})
                self.assertEqual(hp.total, 170)
                turret.actor.position = (x, y, z + 1.5)
                for _ in range(5):
                    run.step({})
                self.assertEqual(hp.total, 220 if entry == 20 else 170)

    def test_full_health_contact_cannot_repair_new_damage_again_in_same_window(self):
        run, helper, _, turret, hp = self.arena(own=True)
        hp.heal(80)
        for _ in range(20):
            run.step({helper: (0, 0, 1)})
        self.assertIn(turret.actor.entity, run.match.combat.hammer_contacts[helper.body].repaired)
        hp.damage(80)
        run.match.combat._sync_body_health(turret.actor, hp)
        for _ in range(10):
            run.step({helper: (0, 0, 1)})
        self.assertEqual(hp.total, 170)

    def test_one_hammer_swing_repairs_owned_turret_and_damages_enemy_once(self):
        for boosted, impact in ((False, 20), (True, 10)):
            with self.subTest(overload=boosted):
                self.check_combined_hammer_contacts(boosted, impact)

    def combined_hammer_arena(self, enemy_hero=None):
        fixture = turret_tests.TorbjornTurretTests()
        run, owner, enemy, _ = fixture.arena(100, deploy=False)
        match = run.match
        fight = match.combat
        if enemy_hero is not None:
            self.assertTrue(match.switch_hero(enemy, enemy_hero))
            x, y, z = owner.mover.position
            enemy.mover.position = [x, y, z + 100]
            enemy.mover.body = None
        run.step({owner: (0, 4000, 8)})
        fixture.advance(run, 225)
        turret = fight.turrets[owner.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertAlmostEqual(hp.total, 250)
        hp.damage(80)
        fight._sync_body_health(turret.actor, hp)
        tx, y, z = turret.actor.position
        x = tx - 1.5
        owner.mover.position = [x, y, z]
        owner.mover.body = None
        # Cover the turret-to-enemy lane while leaving both hammer tracks exposed.
        match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall_mesh(x + .35, z + .8, x + 1.15, z + .8, y0=y, y1=y + 4)
        )
        enemy.mover.position = [x, y, z + 1.6]
        enemy.mover.body = None
        enemy_hp = fight.vitals[enemy.body]
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, 45)
        return fixture, run, owner, enemy, turret, hp, enemy_hp

    def check_combined_hammer_contacts(self, boosted, impact):
        fixture, run, owner, enemy, turret, hp, enemy_hp = self.combined_hammer_arena()
        fight = run.match.combat
        if boosted:
            run.step({owner: (0, 0, 16)})
            fixture.advance(run, 45)
        shots = turret.shots
        self.assertEqual(shots, 0)
        self.assertEqual(enemy_hp.total, 200)
        self.assertAlmostEqual(hp.total, 170)
        for _ in range(impact - 1):
            run.step({owner: (0, 0, 1)})
            self.assertAlmostEqual(hp.total, 170)
            self.assertEqual(enemy_hp.total, 200)
        run.step({owner: (0, 0, 1)})
        contact = fight.hammer_contacts[owner.body]
        self.assertEqual(contact.repaired, {turret.actor.entity})
        self.assertEqual(contact.hit, {enemy.body})
        self.assertAlmostEqual(hp.total, 220)
        self.assertEqual(enemy_hp.total, 145)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 55)
        fixture.advance(run, 15)
        self.assertAlmostEqual(hp.total, 220)
        self.assertEqual(enemy_hp.total, 145)
        self.assertEqual(turret.shots, shots)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 55)

    def test_fortify_expiry_changes_hammer_damage_without_changing_repair(self):
        expiry = 249
        cases = ((248, False, 28), (249, False, 28), (249, True, 50), (250, False, 50))
        for impact, orisa_first, damage in cases:
            with self.subTest(impact=impact, orisa_first=orisa_first):
                fixture, run, torb, orisa, turret, hp, orisa_hp = self.combined_hammer_arena(
                    heroes()[fortify.HERO])
                fight = run.match.combat
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(0)
                charge = torb.body_script.component.entity_var(342)
                before = orisa_hp.total
                run.step({orisa: (0, 0, 8)})
                start = run.frame
                self.assertIsNotNone(fortify.state_for(orisa))
                fixture.advance(run, impact - 20)
                run.step({torb: (0, 0, 1)})
                fixture.advance(run, 18)
                contact = fight.hammer_contacts[torb.body]
                self.assertEqual(run.frame, start + impact - 1)
                self.assertEqual(hp.total, 170)
                self.assertEqual(orisa_hp.total, before)
                self.assertEqual(fortify.state_for(orisa) is not None, impact <= expiry)

                run.frame += 1
                run.match.update(run.frame * .016, run.frame)
                command = Command(run.frame)
                players = (orisa, torb) if orisa_first else (torb, orisa)
                for player in players:
                    player.take_commands([command])
                self.assertEqual(fortify.state_for(orisa) is not None, impact < expiry)
                self.assertIs(fight.hammer_contacts[torb.body], contact)
                self.assertEqual(contact.repaired, {turret.actor.entity})
                self.assertEqual(contact.hit, {orisa.body})
                self.assertEqual(hp.total, 220)
                self.assertAlmostEqual(orisa_hp.total, before - damage, places=5)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), damage, places=5)
                self.assertAlmostEqual(charge.value(), damage, places=5)

                fixture.advance(run, 15)
                self.assertEqual(hp.total, 220)
                self.assertAlmostEqual(orisa_hp.total, before - damage, places=5)
                self.assertEqual(turret.shots, 0)
                self.assertIsNone(fortify.state_for(orisa))

    def test_protected_hammer_hit_still_repairs_and_fresh_swing_hits_after_expiry(self):
        by_name = {hero.name: hero for hero in heroes().values()}
        cases = (("Genji", False), ("Genji", True), ("Zarya", False), ("Zarya", True))
        for name, exposed_mid_swing in cases:
            with self.subTest(hero=name, exposed_mid_swing=exposed_mid_swing):
                fixture, run, owner, enemy, turret, hp, enemy_hp = self.combined_hammer_arena(by_name[name])
                fight = run.match.combat
                base_health = enemy_hp.total
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0)
                if name == "Genji":
                    defenses.TorbjornDefenseTests().cast(run, owner, enemy)
                    bubble = None
                else:
                    run.step({enemy: (0, 0, 8)})
                    bubble = fight.bubbles.for_target(enemy)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                    if exposed_mid_swing:
                        bubble.health = 40
                for _ in range(19):
                    run.step({owner: (0, 0, 1), enemy: (-32768, 0, 0)})
                    self.assertEqual(hp.total, 170)
                    self.assertEqual(enemy_hp.total, base_health)
                run.step({owner: (0, 0, 1), enemy: (-32768, 0, 0)})
                contact = fight.hammer_contacts[owner.body]
                self.assertEqual(contact.repaired, {turret.actor.entity})
                self.assertEqual(contact.hit, {enemy.body})
                self.assertEqual(hp.total, 220)
                self.assertEqual(enemy_hp.total, base_health)
                if bubble is not None:
                    self.assertEqual(bubble.health, 0 if exposed_mid_swing else 145)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                if exposed_mid_swing:
                    if bubble is not None:
                        self.assertIsNone(fight.bubbles.for_target(enemy))
                    hp.damage(12)
                    fight._sync_body_health(turret.actor, hp)
                    run.step({owner: (0, 0, 1), enemy: (0, 0, 0)})
                    self.assertLess(owner.body_script.component.now, contact.end)
                    self.assertIs(fight.hammer_contacts[owner.body], contact)
                    for _ in range(14):
                        run.step({owner: (0, 0, 1), enemy: (0, 0, 0)})
                else:
                    fixture.advance(run, 15)
                self.assertEqual(hp.total, 208 if exposed_mid_swing else 220)
                self.assertEqual(enemy_hp.total, base_health)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                if bubble is not None:
                    self.assertEqual(bubble.health, 0 if exposed_mid_swing else 145)
                fixture.advance(run, 145)
                for _ in range(20):
                    run.step({owner: (0, 0, 1), enemy: (-32768, 0, 0)})
                self.assertEqual(hp.total, 250)
                self.assertEqual(enemy_hp.total, base_health - 55)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 55)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 55)
                self.assertEqual(turret.shots, 0)

    def test_projected_barrier_cast_during_hammer_windup_breaks_without_spill(self):
        fixture, run, owner, enemy, turret, hp, enemy_hp = self.combined_hammer_arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        enemy.team = 1 - owner.team
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        zarya = match.add_player(8, "Zarya", hero.guid, enemy.team, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, hero))
        x, y, z = owner.mover.position
        defense = defenses.TorbjornDefenseTests()
        defense.place(zarya, (x - 8, y, z))
        match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall_mesh(x + .35, z + .8, x + 1.15, z + .8, y0=y, y1=y + 4)
            + wall_mesh(x - 3, z - .5, x - 3, z + .7, y0=y, y1=y + 4)
        )
        fixture.advance(run, 35)
        script = owner.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        charge = script.component.entity_var(342)
        self.assertEqual(hp.total, 170)
        self.assertEqual(enemy_hp.total, 200)
        self.assertEqual(turret.shots, 0)
        run.step({owner: (0, 0, 1)})
        contact = fight.hammer_contacts[owner.body]
        fixture.advance(run, 8)
        run.step({zarya: (*defense.body_aim(run, zarya, enemy), 16)})
        bubble = fight.bubbles.for_target(enemy)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        self.assertLess(script.component.now, contact.begin)
        bubble.health = 40

        for _ in range(20):
            if bubble.health == 0:
                break
            run.step({})
        self.assertIs(fight.hammer_contacts[owner.body], contact)
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.bubbles.for_target(enemy))
        self.assertEqual(contact.repaired, {turret.actor.entity})
        self.assertEqual(contact.hit, {enemy.body})
        self.assertEqual(hp.total, 220)
        self.assertEqual(enemy_hp.total, 200)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(charge.value(), 0)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 8, places=4)
        hp.damage(12)
        fight._sync_body_health(turret.actor, hp)
        run.step({})
        self.assertLess(script.component.now, contact.end)
        self.assertEqual(hp.total, 208)
        self.assertEqual(enemy_hp.total, 200)
        fixture.advance(run, 55)
        run.step({owner: (0, 0, 1)})
        fixture.advance(run, 35)
        self.assertEqual(hp.total, 250)
        self.assertEqual(enemy_hp.total, 145)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 55)
        self.assertEqual(charge.value(), 55)
        self.assertEqual(turret.shots, 0)

    def test_phase_entered_during_hammer_windup_blocks_damage_but_allows_repair(self):
        by_name = {hero.name: hero for hero in heroes().values()}
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                fixture, run, owner, enemy, turret, hp, enemy_hp = self.combined_hammer_arena(by_name[name])
                fight = run.match.combat
                script = owner.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                charge = script.component.entity_var(342)
                before = enemy_hp.total
                self.assertEqual(hp.total, 170)
                self.assertFalse(phase_defenses.phase(enemy)[0])
                run.step({owner: (0, 0, 1)})
                contact = fight.hammer_contacts[owner.body]
                self.assertLess(script.component.now, contact.begin)
                fixture.advance(run, 8)
                run.step({enemy: (-32768, 0, 8)})
                self.assertTrue(phase_defenses.phase(enemy)[0])
                self.assertLess(script.component.now, contact.begin)
                self.assertEqual(hp.total, 170)
                for _ in range(20):
                    if hp.total > 170:
                        break
                    run.step({})
                self.assertTrue(phase_defenses.phase(enemy)[0])
                self.assertIs(fight.hammer_contacts[owner.body], contact)
                self.assertEqual(contact.repaired, {turret.actor.entity})
                self.assertEqual(hp.total, 220)
                self.assertEqual(enemy_hp.total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(charge.value(), 0)

                for _ in range(350):
                    if not phase_defenses.phase(enemy)[0]:
                        break
                    run.step({})
                self.assertFalse(phase_defenses.phase(enemy)[0])
                self.assertEqual(hp.total, 220)
                self.assertEqual(enemy_hp.total, before)
                run.step({owner: (0, 0, 1)})
                fixture.advance(run, 35)
                self.assertEqual(hp.total, 250)
                self.assertEqual(enemy_hp.total, before - 55)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 55)
                self.assertEqual(charge.value(), 55)
                self.assertEqual(turret.shots, 0)

    def test_spray_freeze_before_or_after_hammer_contact_preserves_only_completed_effects(self):
        mei_hero = next(hero for hero in heroes().values() if hero.name == "Mei")
        defense = defenses.TorbjornDefenseTests()
        for contact_first in (False, True):
            with self.subTest(contact_first=contact_first):
                _, run, owner, mei, turret, hp, enemy_hp = self.combined_hammer_arena(mei_hero)
                fight = run.match.combat
                base_health = enemy_hp.total
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0)
                spray_aim = defense.body_aim(run, mei, owner)
                threshold = .72 if contact_first else .96
                for _ in range(140):
                    run.step({mei: (*spray_aim, 1)})
                    sample = fight.frost.sample(owner, run.frame)
                    if sample is not None and sample[0] >= threshold - 1e-6:
                        break
                self.assertIsNotNone(sample)
                self.assertGreaterEqual(sample[0], threshold - 1e-6)
                self.assertLess(sample[0], threshold + .04)
                self.assertFalse(fight.is_stunned(owner))
                run.step({owner: (0, 0, 1), mei: (*spray_aim, 1)})
                contact = fight.hammer_contacts[owner.body]
                self.assertLess(owner.body_script.component.now, contact.begin)
                for _ in range(40):
                    if fight.is_stunned(owner):
                        break
                    run.step({owner: (0, 0, 1), mei: (*spray_aim, 1)})
                self.assertTrue(fight.is_stunned(owner))
                self.assertTrue(contact.cancelled)
                self.assertEqual(contact.repaired, {turret.actor.entity} if contact_first else set())
                self.assertEqual(contact.hit, {mei.body} if contact_first else set())
                repair = 50 if contact_first else 0
                damage = 55 if contact_first else 0
                self.assertEqual(hp.total, 170 + repair)
                self.assertEqual(enemy_hp.total, base_health - damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), damage)
                thaw = fight.frost.sample(owner, run.frame)[2]
                while (run.frame + 1) * 16 < thaw:
                    run.step({owner: (0, 0, 1)})
                    self.assertEqual(hp.total, 170 + repair)
                    self.assertEqual(enemy_hp.total, base_health - damage)
                run.step({})
                self.assertFalse(fight.is_stunned(owner))
                self.assertEqual(hp.total, 170 + repair)
                for _ in range(20):
                    run.step({owner: (0, 0, 1)})
                self.assertEqual(hp.total, min(250, 220 + repair))
                self.assertEqual(enemy_hp.total, base_health - damage - 55)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage + 55)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), damage + 55)
                self.assertEqual(turret.shots, 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs authored Assassin data")
    def test_assassin_pin_interrupts_pending_hammer_damage_and_repair_together(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        definition = next(row for row in catalog.values() if row.name == "Assassin")
        fixture = defenses.TorbjornDefenseTests()
        for contact_first in (False, True):
            with self.subTest(contact_first=contact_first):
                _, run, owner, enemy, turret, hp, _ = self.combined_hammer_arena()
                match = run.match
                fight = match.combat
                position = tuple(enemy.mover.position)
                x, y, z = owner.mover.position
                fixture.place(enemy, (x + 100, y, z))
                bot = npc.spawn(match, definition, position, team=1 - owner.team)
                bot_hp = fight.vitals[bot.entity]
                owner_hp = fight.vitals[owner.body]
                for viewer in run.players:
                    viewer.client.bot_stream = lambda *args: None
                for _ in range(35):
                    run.step({})
                    if run.frame >= bot.records_from:
                        break
                self.assertEqual(turret.shots, 0)
                before = bot_hp.total
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0)
                run.step({owner: (0, 0, 1)})
                contact = fight.hammer_contacts[owner.body]
                self.assertLess(owner.body_script.component.now, contact.begin)
                if contact_first:
                    for _ in range(19):
                        run.step({owner: (0, 0, 1)})
                    self.assertEqual(contact.hit, {bot.entity})
                    self.assertEqual(contact.repaired, {turret.actor.entity})
                self.assertEqual(npc_control.start(match, owner), bot.entity)
                for _ in range(50):
                    run.step({owner: (0, 0, 1)})
                    if fight.pins.get(bot.entity) == owner.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertTrue(fight.is_stunned(owner))
                repair = 50 if contact_first else 0
                damage = 55 if contact_first else 0
                self.assertEqual(hp.total, 170 + repair)
                self.assertEqual(bot_hp.total, before - damage)
                if not contact_first:
                    self.assertTrue(contact.cancelled)
                    self.assertFalse(contact.hit or contact.repaired)
                for _ in range(25):
                    run.step({owner: (0, 0, 1)})
                    self.assertEqual(fight.pins.get(bot.entity), owner.body)
                    self.assertEqual(hp.total, 170 + repair)
                    self.assertEqual(bot_hp.total, before - damage)
                self.assertLess(owner_hp.total, 250)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), damage)
                npc_control.stop(match)
                self.assertFalse(fight.is_stunned(owner))
                for _ in range(45):
                    run.step({})
                self.assertEqual(hp.total, 170 + repair)
                self.assertEqual(bot_hp.total, before - damage)
                for _ in range(20):
                    run.step({owner: (0, 0, 1)})
                self.assertEqual(hp.total, min(250, 220 + repair))
                self.assertEqual(bot_hp.total, before - damage - 55)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage + 55)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), damage + 55)
                self.assertEqual(turret.shots, 0)

    def test_repair_and_lethal_soldier_hit_resolve_in_contact_frame_command_order(self):
        for repair_first in (False, True):
            with self.subTest(repair_first=repair_first):
                self.check_repair_and_lethal_hit_order(repair_first)

    def check_repair_and_lethal_hit_order(self, repair_first):
        fixture = turret_tests.TorbjornTurretTests()
        run, owner, enemy, _ = fixture.arena(100, deploy=False)
        match = run.match
        fight = match.combat
        run.step({owner: (0, 4000, 8)})
        fixture.advance(run, 225)
        turret = fight.turrets[owner.entity]
        entity = turret.actor.entity
        hp = fight.vitals[entity]
        x, y, z = turret.actor.position
        owner.mover.position = [x, y, z - 1.5]
        owner.mover.body = None
        enemy.mover.position = [x + 5, y, z]
        enemy.mover.body = None
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, 45)
        self.assertEqual(hp.total, 250)
        hp.damage(230)  # One body shot is lethal unless the pending repair lands first.
        fight._sync_body_health(turret.actor, hp)
        for _ in range(19):
            run.step({owner: (0, 0, 1)})
        contact = fight.hammer_contacts[owner.body]
        self.assertEqual(hp.total, 20)
        self.assertNotIn(entity, contact.repaired)

        run.frame += 1
        match.update(run.frame * .016, run.frame)
        yaw, pitch = aim(fight._eye(enemy), (x, y + .5, z))
        shot = Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)
        swing = Command(run.frame)
        commands = ((owner, swing), (enemy, shot))
        for player, command in commands if repair_first else reversed(commands):
            player.take_commands([command])

        self.assertLessEqual(contact.begin, owner.body_script.component.now)
        self.assertLess(owner.body_script.component.now, contact.end)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 20)
        if repair_first:
            self.assertIs(fight.turrets[owner.entity], turret)
            self.assertIn(entity, contact.repaired)
            self.assertEqual(hp.total, 50)
            self.assertFalse(turret.actor.dead)
            fixture.advance(run, 15)
            self.assertEqual(hp.total, 50)
        else:
            self.assertTrue(turret.actor.dead)
            self.assertEqual(hp.killer, enemy.body)
            self.assertNotIn(entity, contact.repaired)
            self.assertGreater(hero_torbjorn.turret_cooldown_remaining(owner), 9.9)
            fixture.advance(run, 15)
            self.assertEqual(hp.total, 0)
            self.assertNotIn(owner.entity, fight.turrets)
            enemy.mover.position = [x + 100, y, z + 100]
            enemy.mover.body = None
            run.step({owner: (0, 4000, 8)})
            self.assertFalse(any(rocket.spawn == hero_torbjorn.TURRET for rocket in fight.rockets))
            for _ in range(650):
                if hero_torbjorn.turret_cooldown_remaining(owner) == 0:
                    break
                run.step({})
            self.assertEqual(hero_torbjorn.turret_cooldown_remaining(owner), 0)
            run.step({owner: (0, 4000, 8)})
            fixture.advance(run, 225)
            fresh = fight.turrets[owner.entity]
            self.assertNotEqual(fresh.actor.entity, entity)
            fresh_hp = fight.vitals[fresh.actor.entity]
            self.assertAlmostEqual(fresh_hp.total, 250)
            x, y, z = fresh.actor.position
            owner.mover.position = [x, y, z - 1.5]
            owner.mover.body = None
            enemy.mover.position = [x + 5, y, z]
            enemy.mover.body = None
            fixture.advance(run, 10)
            direction = aim(fight._eye(enemy), (x, y + .5, z))
            for _ in range(40):
                if fresh_hp.total <= 190 + 1e-6:
                    break
                run.step({enemy: (*direction, 1)})
            self.assertAlmostEqual(fresh_hp.total, 190)
            self.swing(run, owner)
            self.assertAlmostEqual(fresh_hp.total, 240)
            self.assertEqual(hp.total, 0)
