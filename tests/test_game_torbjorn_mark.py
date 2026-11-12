"""Actual030E primary impacts and16FC states under the scoped turret priority host."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
import test_game_torbjorn_phases as phase_tests
import test_game_torbjorn_turret as turret_tests
from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, hero_torbjorn, npc, phase_defenses, stats, torbjorn_charge, torbjorn_mark
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs030E prepared bundle")
class TorbjornMarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        turret_tests.TorbjornTurretTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        turret_tests.TorbjornTurretTests.tearDownClass()

    def arena(self, *, range_case=False, occluded=False, far_hero=None, far_parked=False, thrown=False):
        run = Run(KANEZAKA_DM, names=("Torbjorn", "Near", "Marked"))
        owner, near, far = run.players
        run.match.switch_hero(owner, heroes()[hero_torbjorn.HERO])
        if far_hero:
            run.match.switch_hero(far, heroes()[far_hero])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = owner.mover.position
        near.mover.position = [x + 3, y, z - 27 if range_case else z + 6]
        far.mover.position = [x + (100 if far_parked else 0), y, z + 12]
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        self.advance(run, 35)
        if thrown:
            run.step({owner: (0, 4000, 8)})
            self.advance(run, 215)
            turret = run.match.combat.turrets[owner.entity]
            return run, owner, near, far, turret
        turret = hero_torbjorn.deploy_turret(run.match.combat, owner,
                                           (x + (4 if occluded else 0), y, z - 35 if range_case else z + 1.5))
        if occluded:
            # Owner's x=0 rivet sees the far target; turret's diagonal ray does not.
            plane = z + 6
            run.match.collision_world = World([
                (x-100, y, z-100, x-100, y, z+100, x+100, y, z+100),
                (x-100, y, z-100, x+100, y, z+100, x+100, y, z-100),
                (x+1.5, y, plane, x+2.8, y, plane, x+2.8, y+4, plane),
                (x+1.5, y, plane, x+2.8, y+4, plane, x+1.5, y+4, plane),
            ], name="mark-occlusion")
        self.advance(run, 190)
        return run, owner, near, far, turret

    @staticmethod
    def advance(run, ticks, owner=None, buttons=0, victim=None):
        for _ in range(ticks):
            controls = {}
            if owner:
                x, y, z = victim.mover.position
                yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1, z))
                controls[owner] = (yaw, pitch, buttons)
            run.step(controls)

    def fire(self, run, owner, victim, button=1):
        self.advance(run, 1, owner, button, victim)

    def test_primary_impact_redirects_real_damage_then_expiry_restores_nearest(self):
        run, owner, near, far, turret = self.arena()
        fight = run.match.combat
        self.assertEqual(turret.target, near.body)
        before = fight.vitals[near.body].total
        self.fire(run, owner, far)
        self.advance(run, 20)
        mark = fight.torbjorn_marks[owner.entity, far.body]
        self.assertTrue(torbjorn_mark.active(mark))
        self.assertEqual(mark.script.component.instances[mark.instance].graph.index, 0x16FC)
        self.assertEqual(turret.target, far.body)
        self.assertLess(fight.vitals[far.body].total, 130.0)  # primary70 plus actual turret fire
        self.assertEqual(fight.vitals[near.body].total, before)
        self.advance(run, 40)
        self.assertNotIn((owner.entity, far.body), fight.torbjorn_marks)
        self.assertNotIn(mark.instance, mark.script.component.instances)
        self.assertEqual(turret.target, near.body)
        self.assertLess(fight.vitals[near.body].total, before)

    def two_thrown_turrets(self):
        run = Run(KANEZAKA_DM, names=("First", "Second", "Near", "Far"))
        first, second, near, far = run.players
        match = run.match
        fight = match.combat
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        x, y, z = first.mover.position
        for player, offset in ((first, -.5), (second, .5), (far, 0)):
            self.assertTrue(match.switch_hero(player, heroes()[hero_torbjorn.HERO]))
            player.team = 1 if player is far else 0
            player.mover.position = [x + offset, y, z + (12 if player is far else 0)]
            player.mover.body = None
        near.team = 1
        near.mover.position = [x + 3, y, z + 7]
        near.mover.body = None
        self.advance(run, 35)
        run.step(dict.fromkeys((first, second), (0, 4000, 8)))
        for _ in range(250):
            if (len(fight.turrets) == 2
                    and all(turret.target == near.body for turret in fight.turrets.values())):
                break
            run.step({})
        one, two = (fight.turrets[player.entity] for player in (first, second))
        self.assertEqual((one.target, two.target), (near.body, near.body))
        return run, first, second, near, far, one, two

    def test_two_thrown_turrets_follow_only_their_owners_marks(self):
        run, first, second, near, far, one, two = self.two_thrown_turrets()
        fight = run.match.combat
        first_key, second_key = ((player.entity, far.body) for player in (first, second))
        self.fire(run, first, far)
        self.advance(run, 20)
        first_mark = fight.torbjorn_marks[first_key]
        self.assertNotIn(second_key, fight.torbjorn_marks)
        self.assertEqual((one.target, two.target), (far.body, near.body))
        self.fire(run, second, far)
        self.advance(run, 12)
        second_mark = fight.torbjorn_marks[second_key]
        self.assertIs(fight.torbjorn_marks[first_key], first_mark)
        self.assertNotEqual(first_mark.instance, second_mark.instance)
        self.assertGreater(second_mark.expires, first_mark.expires)
        self.assertEqual((one.target, two.target), (far.body, far.body))
        for _ in range(50):
            if first_key not in fight.torbjorn_marks:
                break
            run.step({})
        self.assertNotIn(first_key, fight.torbjorn_marks)
        self.assertIs(fight.torbjorn_marks[second_key], second_mark)
        self.assertEqual((one.target, two.target), (near.body, far.body))
        self.advance(run, 25)
        self.assertFalse(fight.torbjorn_marks)
        self.assertEqual((one.target, two.target), (near.body, near.body))

    def test_two_turrets_arbitrate_different_live_marks_and_recover_after_team_change(self):
        run, first, second, near, far, one, two = self.two_thrown_turrets()
        match = run.match
        fight = match.combat
        later = match.add_player(8, "Later", hero_torbjorn.HERO, far.team, False)
        in_world(match, later)
        run.players.append(later)
        self.assertTrue(match.switch_hero(later, heroes()[hero_torbjorn.HERO]))
        x, y, z = first.mover.position
        defenses.TorbjornDefenseTests.place(later, (x - 4, y, z + 10))
        run.step({first: (0, 0, 16)})
        self.advance(run, 45)
        first_shot = run.frame + 1
        old_key = first.entity, far.body
        other_key = second.entity, later.body
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            self.fire(run, first, far)
            self.fire(run, second, later)
            self.advance(run, 14)
            old_mark = fight.torbjorn_marks[old_key]
            other_mark = fight.torbjorn_marks[other_key]
            self.assertEqual((one.target, two.target), (far.body, later.body))
            self.advance(run, first_shot + 28 - run.frame)
        hits = [(call.args[0], call.args[1].owner) for call in damage.call_args_list]
        self.assertIn((one.actor, far), hits)
        self.assertIn((two.actor, later), hits)
        self.assertNotIn((one.actor, later), hits)
        self.assertNotIn((two.actor, far), hits)
        new_key = first.entity, later.body
        self.fire(run, first, later)
        for _ in range(16):
            if new_key in fight.torbjorn_marks:
                break
            run.step({})
        new_mark = fight.torbjorn_marks[new_key]
        self.assertGreater(new_mark.expires, old_mark.expires)
        self.assertIs(fight.torbjorn_marks[old_key], old_mark)
        self.assertIs(fight.torbjorn_marks[other_key], other_mark)
        self.assertEqual((one.target, two.target), (later.body, later.body))
        later.team = first.team
        run.step({})
        self.assertEqual((one.target, two.target), (far.body, near.body))
        self.assertIs(fight.torbjorn_marks[new_key], new_mark)
        self.assertIs(fight.torbjorn_marks[other_key], other_mark)
        later.team = far.team
        run.step({})
        self.assertEqual((one.target, two.target), (later.body, later.body))

        for _ in range(45):
            if old_key not in fight.torbjorn_marks and other_key not in fight.torbjorn_marks:
                break
            run.step({})
        self.assertNotIn(old_key, fight.torbjorn_marks)
        self.assertNotIn(other_key, fight.torbjorn_marks)
        self.assertIs(fight.torbjorn_marks[new_key], new_mark)
        self.assertEqual((one.target, two.target), (later.body, near.body))
        self.advance(run, 35)
        self.assertFalse(fight.torbjorn_marks)
        self.assertEqual((one.target, two.target), (near.body, near.body))

    def mark_barrier_arena(self, projected):
        zarya_hero = heroes()[0x02E0000000000068]
        run, owner, near, far, turret = self.arena(far_hero=None if projected else zarya_hero.guid)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        near.team = far.team = 1 - owner.team
        if projected:
            position = list(near.mover.position)
            self.assertTrue(match.switch_hero(near, zarya_hero))
            near.mover.position = position
            near.mover.body = None
            self.advance(run, 25)
        self.assertEqual(turret.target, near.body)
        return run, owner, near, far, turret

    def cast_mark_barrier(self, run, near, far, projected):
        fight = run.match.combat
        caster = near if projected else far
        if projected:
            yaw, pitch = aim(fight._eye(caster), fight._eye(far))
            run.step({caster: (yaw, pitch, 16)})
        else:
            run.step({caster: (0, 0, 8)})
        bubble = fight.bubbles.for_target(far)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, caster)
        return bubble

    def test_personal_and_projected_barriers_deny_mark_until_a_fresh_body_hit(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, owner, near, far, turret = self.mark_barrier_arena(projected)
                fight = run.match.combat
                bubble = self.cast_mark_barrier(run, near, far, projected)
                self.assertEqual(bubble.health, 200)
                target = next(target for target in fight._targets() if target.owner is far)
                hp = fight.vitals_of(target)
                before = hp.total
                key = owner.entity, far.body
                self.fire(run, owner, far)
                self.advance(run, 20)
                self.assertEqual(bubble.health, 130)
                self.assertEqual(hp.total, before)
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertEqual(turret.target, near.body)
                for _ in range(140):
                    if fight.bubbles.for_target(far) is None:
                        break
                    run.step({})
                self.assertIsNone(fight.bubbles.for_target(far))
                self.assertNotIn(key, fight.torbjorn_marks)
                self.fire(run, owner, far)
                self.advance(run, 20)
                self.assertLessEqual(hp.total, before - 70)
                self.assertTrue(torbjorn_mark.active(fight.torbjorn_marks[key]))
                self.assertEqual(turret.target, far.body)

    def test_barrier_absorbs_marked_turret_fire_without_resetting_existing_mark(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, owner, near, far, turret = self.mark_barrier_arena(projected)
                fight = run.match.combat
                key = owner.entity, far.body
                self.fire(run, owner, far)
                for _ in range(20):
                    if key in fight.torbjorn_marks and turret.target == far.body:
                        break
                    run.step({})
                mark = fight.torbjorn_marks[key]
                expires = mark.expires
                self.assertEqual(turret.target, far.body)
                hp = fight.vitals[far.body]
                bubble = self.cast_mark_barrier(run, near, far, projected)
                before = hp.total
                shield = bubble.health
                for _ in range(20):
                    if bubble.health < shield:
                        break
                    run.step({})
                self.assertEqual(bubble.health, shield - 14)
                self.assertEqual(hp.total, before)
                self.assertIs(fight.torbjorn_marks[key], mark)
                self.assertEqual(mark.expires, expires)
                self.assertEqual(turret.target, far.body)
                for _ in range(60):
                    if key not in fight.torbjorn_marks:
                        break
                    run.step({})
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertNotIn(mark.instance, mark.script.component.instances)
                self.assertIs(fight.bubbles.for_target(far), bubble)
                self.assertEqual(hp.total, before)
                self.assertEqual(turret.target, near.body)

    def turret_defense_arena(self, hero):
        fixture = defenses.TorbjornDefenseTests()
        run, owner, defender, near = fixture.arena(hero)
        fight = run.match.combat
        x, y, z = owner.mover.position
        fixture.place(near, (x + 7.5, y, z))
        turret = hero_torbjorn.deploy_turret(fight, owner, (x + 6, y, z))
        fixture.advance(run, owner, defender, 190)
        self.assertEqual(turret.target, near.body)
        return fixture, run, owner, defender, near, turret

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "needs authored projectile tags")
    def test_reflected_rivet_hits_shooter_without_mark_or_turret_redirect(self):
        fixture, run, owner, genji, near, turret = self.turret_defense_arena("Genji")
        fight = run.match.combat
        owner_hp = fixture.health(run, owner)
        genji_hp = fixture.health(run, genji)
        before = genji_hp.total
        fixture.cast(run, owner, genji)
        fixture.step(run, owner, genji, fire=1)
        fixture.advance(run, owner, genji, 20)
        self.assertEqual(owner_hp.total, 185, genji_hp.total)
        self.assertEqual(genji_hp.total, before)
        self.assertFalse(fight.torbjorn_marks)
        self.assertEqual(turret.target, near.body)
        for _ in range(140):
            if not any(
                state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
                for instance in genji.body_script.component.instances.values()
                for state in instance.states.values()
            ):
                break
            run.step({})
        self.assertEqual(turret.target, near.body)
        fixture.step(run, owner, genji, fire=1)
        fixture.advance(run, owner, genji, 20)
        self.assertLessEqual(genji_hp.total, before - 70)
        self.assertIn((owner.entity, genji.body), fight.torbjorn_marks)
        self.assertEqual(turret.target, genji.body)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "needs authored projectile tags")
    def test_grasp_consumes_rivet_without_mark_and_fresh_hit_redirects_turret(self):
        fixture, run, owner, sigma, near, turret = self.turret_defense_arena("Sigma")
        fight = run.match.combat
        hp = fixture.health(run, sigma)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        fixture.cast(run, owner, sigma)
        fixture.step(run, owner, sigma, fire=1)
        fixture.advance(run, owner, sigma, 20)
        self.assertEqual(grasp.vars[21312].value(), 70)
        self.assertEqual(hp.total, 400)
        self.assertFalse(fight.torbjorn_marks)
        self.assertEqual(turret.target, near.body)
        for _ in range(145):
            if hp.total > 400:
                break
            run.step({})
        self.assertEqual(hp.total, 442)
        self.assertFalse(grasp.state(1).active)
        self.assertFalse(fight.torbjorn_marks)
        self.assertEqual(turret.target, near.body)
        fixture.step(run, owner, sigma, fire=1)
        fixture.advance(run, owner, sigma, 20)
        self.assertLessEqual(hp.total, 372)
        self.assertTrue(torbjorn_mark.active(fight.torbjorn_marks[owner.entity, sigma.body]))
        self.assertEqual(turret.target, sigma.body)

    def test_secondary_hit_does_not_apply_primary_mark(self):
        run, owner, near, far, turret = self.arena()
        self.fire(run, owner, far, 2)
        self.advance(run, 25)
        self.assertLess(run.match.combat.vitals[far.body].total, 200.0)
        self.assertEqual(run.match.combat.torbjorn_marks, {})
        self.assertEqual(turret.target, near.body)

    def test_adaptive_shield_body_hit_marks_until_only_armor_spill_grants_charge(self):
        run, owner, near, ball, turret = self.arena(
            far_hero=torbjorn_charge.WRECKING_BALL, far_parked=True, thrown=True,
        )
        fixture = defenses.TorbjornDefenseTests()
        fight = run.match.combat
        hp = fight.vitals[ball.body]
        base = hp.total
        run.step({ball: (0, 0, 16)})
        self.advance(run, 3)
        tag = torbjorn_charge.ADAPTIVE_SHIELD_TAG
        self.assertEqual(hp.temporary_shields[tag].now, 100)
        self.assertEqual(hp.total, base + 100)
        x, y, z = owner.mover.position
        fixture.place(ball, (x, y, z + 12))
        shots = turret.shots
        for _ in range(24):
            if turret.shots > shots:
                break
            run.step({})
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(turret.target, near.body)
        shots = turret.shots
        near_hp = fight.vitals[near.body]
        near_before = near_hp.total
        credit = owner.stats.value(stats.HERO_DAMAGE)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0)
        charge = owner.body_script.component.entity_var(342)
        self.fire(run, owner, ball)
        key = owner.entity, ball.body
        for _ in range(24):
            if key in fight.torbjorn_marks:
                break
            run.step({})
        mark = fight.torbjorn_marks[key]
        self.assertTrue(torbjorn_mark.active(mark))
        self.assertEqual(turret.target, ball.body)
        self.assertEqual(turret.shots, shots)
        self.assertEqual(hp.temporary_shields[tag].now, 30)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 70)
        self.assertEqual(charge.value(), 0)
        for index, remaining in enumerate((16, 2), 1):
            for _ in range(24):
                if turret.shots == shots + index:
                    break
                run.step({})
            self.assertEqual(turret.shots, shots + index)
            self.assertEqual(turret.target, ball.body)
            self.assertIs(fight.torbjorn_marks[key], mark)
            self.assertEqual(hp.temporary_shields[tag].now, remaining)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 70 + 14 * index)
            self.assertEqual(charge.value(), 0)
            self.assertEqual(near_hp.total, near_before)
        for _ in range(24):
            if turret.shots > shots + 2:
                break
            run.step({})
        self.assertNotIn(key, fight.torbjorn_marks)
        self.assertNotIn(mark.instance, mark.script.component.instances)
        self.assertEqual(turret.target, near.body)
        self.assertEqual(near_hp.total, near_before - 14)
        self.assertEqual(hp.total, base + 2)

        owner.body_script.set_ult_charge(0)
        credit = owner.stats.value(stats.HERO_DAMAGE)
        shots = turret.shots
        self.fire(run, owner, ball)
        for _ in range(24):
            if key in fight.torbjorn_marks:
                break
            run.step({})
        renewed = fight.torbjorn_marks[key]
        self.assertNotEqual(renewed.instance, mark.instance)
        self.assertEqual(turret.target, ball.body)
        self.assertEqual(turret.shots, shots)
        self.assertEqual(hp.total, base - 63)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 65)
        self.assertEqual(charge.value(), 63)
        for _ in range(24):
            if turret.shots > shots:
                break
            run.step({})
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(turret.target, ball.body)
        self.assertIs(fight.torbjorn_marks[key], renewed)
        self.assertEqual(hp.total, base - 72)
        self.assertEqual(charge.value(), 72)

    def test_mark_does_not_override_turret_range_or_static_visibility(self):
        for options in ({"range_case": True}, {"occluded": True}):
            with self.subTest(options=options):
                run, owner, near, far, turret = self.arena(**options)
                self.fire(run, owner, far)
                self.advance(run, 25)
                self.assertIn((owner.entity, far.body), run.match.combat.torbjorn_marks)
                self.assertEqual(turret.target, near.body)
                self.assertEqual(run.match.combat.vitals[far.body].total, 130.0)

    def test_missed_primary_and_friendly_primary_apply_no_mark(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, owner, _, far, _ = self.arena()
                if friendly:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    far.team = owner.team
                    self.fire(run, owner, far)
                else:
                    run.step({owner: (16384, 0, 1)})
                self.advance(run, 25)
                self.assertEqual(run.match.combat.torbjorn_marks, {})
                self.assertEqual(run.match.combat.vitals[far.body].total, 200.0)

    def test_repeated_boosted_primary_replaces_and_refreshes_existing_tag(self):
        run, owner, _, far, _ = self.arena(far_hero=hero_torbjorn.HERO)
        run.step({owner: (0, 0, 16)})
        self.advance(run, 45)
        self.advance(run, 20, owner, 1, far)
        fight = run.match.combat
        first = fight.torbjorn_marks[owner.entity, far.body]
        self.advance(run, 25, owner, 1, far)
        second = fight.torbjorn_marks[owner.entity, far.body]
        self.assertNotEqual(first.instance, second.instance)
        self.assertGreater(second.expires, first.expires)
        self.assertNotIn(first.instance, first.script.component.instances)
        self.advance(run, 15)
        self.assertTrue(torbjorn_mark.active(second))
        self.advance(run, 25)
        self.assertNotIn((owner.entity, far.body), fight.torbjorn_marks)

    def test_owner_death_keeps_existing_and_in_flight_marks_for_surviving_turret(self):
        for in_flight in (False, True):
            with self.subTest(in_flight=in_flight):
                run, owner, near, far, turret = self.arena()
                fight = run.match.combat
                key = owner.entity, far.body
                x, y, z = owner.mover.position
                enemy_aim = aim(fight._eye(near), (x, y + .6, z))
                target = next(target for target in fight._targets() if target.owner is owner)
                owner_hp = fight.vitals_of(target)
                owner_hp.damage(owner_hp.total - 1)
                self.fire(run, owner, far)
                for _ in range(25):
                    if in_flight and any(rocket.shooter is owner for rocket in fight.rockets):
                        break
                    if not in_flight and key in fight.torbjorn_marks:
                        break
                    run.step({})
                existing = fight.torbjorn_marks.get(key)
                if in_flight:
                    self.assertIsNone(existing)
                    self.assertTrue(any(rocket.shooter is owner for rocket in fight.rockets))
                else:
                    self.assertIsNotNone(existing)
                    self.assertEqual(turret.target, far.body)
                run.step({near: (*enemy_aim, 1)})
                self.assertTrue(owner_hp.dead)
                self.assertEqual(owner_hp.killer, near.body)
                if in_flight:
                    self.assertNotIn(key, fight.torbjorn_marks)
                else:
                    self.assertIs(fight.torbjorn_marks[key], existing)
                for _ in range(20):
                    if key in fight.torbjorn_marks and turret.target == far.body:
                        break
                    run.step({})
                mark = fight.torbjorn_marks[key]
                if existing is not None:
                    self.assertIs(mark, existing)
                self.assertEqual(turret.target, far.body)
                hp = fight.vitals[far.body]
                before = hp.total
                self.advance(run, 20)
                self.assertLess(hp.total, before)
                self.advance(run, 40)
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertNotIn(mark.instance, mark.script.component.instances)
                self.assertIs(fight.turrets[owner.entity], turret)
                self.assertEqual(turret.target, near.body)

    def test_switching_away_and_back_before_world_tick_retires_turret_and_mark(self):
        for case in ("existing", "leased", "unleased"):
            with self.subTest(case=case):
                run, owner, near, far, turret = self.arena()
                match = run.match
                fight = match.combat
                key = owner.entity, far.body
                position = list(owner.mover.position)
                if case == "unleased":
                    with patch.object(fight.projectiles, "fired", return_value=None):
                        self.fire(run, owner, far)
                else:
                    self.fire(run, owner, far)
                if case == "existing":
                    self.advance(run, 20)
                previous = fight.torbjorn_marks.get(key)
                if case != "existing":
                    self.assertIsNone(previous)
                    rocket, = (rocket for rocket in fight.rockets if rocket.shooter is owner)
                    self.assertEqual(rocket.shot is None, case == "unleased")
                else:
                    self.assertIsNotNone(previous)
                    self.assertEqual(turret.target, far.body)
                hp = fight.vitals_of(next(target for target in fight._targets() if target.owner is far))
                before = hp.total
                self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
                self.assertNotIn(owner.entity, fight.turrets)
                self.assertNotIn(key, fight.torbjorn_marks)
                if previous is not None:
                    self.assertNotIn(previous.instance, previous.script.component.instances)
                self.assertTrue(match.switch_hero(owner, heroes()[hero_torbjorn.HERO]))
                owner.mover.position = position
                owner.mover.body = None
                self.advance(run, 35)
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertEqual(hp.total, before - (70 if case == "unleased" else 0))
                run.step({owner: (0, 4000, 8)})
                self.advance(run, 225)
                replacement = fight.turrets[owner.entity]
                self.assertIsNot(replacement, turret)
                self.assertEqual(replacement.target, near.body)
                self.fire(run, owner, far)
                self.advance(run, 20)
                self.assertTrue(torbjorn_mark.active(fight.torbjorn_marks[key]))
                self.assertEqual(replacement.target, far.body)

    def test_target_death_or_body_replacement_and_owner_departure_remove_tag(self):
        for case in ("death", "replacement", "departure"):
            with self.subTest(case=case):
                run, owner, _, far, _ = self.arena()
                self.fire(run, owner, far)
                self.advance(run, 20)
                fight = run.match.combat
                mark = fight.torbjorn_marks[owner.entity, far.body]
                if case == "death":
                    target = next(t for t in fight._targets() if t.owner is far)
                    fight._damage(owner, target, 1000.0, False, None, run.frame)
                elif case == "replacement":
                    run.match.switch_hero(far, far.hero)
                else:
                    owner.client = None
                if case == "departure":
                    run.frame += 1
                    run.match.update(run.frame * 0.016, run.frame)
                else:
                    self.advance(run, 1)
                self.assertEqual(fight.torbjorn_marks, {})
                self.assertNotIn(mark.instance, mark.script.component.instances)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five-NPC catalog")
    def test_actual_marked_player_and_five_npcs_die_to_turret_then_it_retargets(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in (None, *catalog.values()):
            with self.subTest(target="player" if definition is None else definition.name):
                run, owner, near, far, turret = self.arena(far_parked=definition is not None, thrown=True)
                match = run.match
                fight = match.combat
                fixture = defenses.TorbjornDefenseTests()
                x, y, z = owner.mover.position
                position = (x, y, z + 12)
                if definition is None:
                    entity = far.body
                else:
                    bot = npc.spawn(match, definition, position, team=1 - owner.team)
                    entity = bot.entity
                hp = fight.vitals[entity]
                hp.damage(hp.total - 90, armour_reduction=False)
                self.assertEqual(turret.target, near.body)
                self.assertEqual(hp.total, 90)
                credit = owner.stats.value(stats.HERO_DAMAGE)
                eliminations = owner.stats.value(stats.ELIMINATIONS)
                run.step({owner: (*aim(fight._eye(owner), (x, y + 1, z + 12)), 1)})
                key = owner.entity, entity
                for _ in range(22):
                    if key in fight.torbjorn_marks:
                        break
                    run.step({})
                mark = fight.torbjorn_marks[key]
                self.assertTrue(torbjorn_mark.active(mark))
                self.assertEqual(turret.target, entity)
                self.assertIn(hp.total, (6, 20))
                shots = turret.shots
                near_hp = fight.vitals[near.body]
                near_before = near_hp.total
                for _ in range(35):
                    if hp.dead:
                        break
                    run.step({})
                self.assertTrue(hp.dead)
                self.assertEqual(hp.killer, owner.body)
                self.assertGreater(turret.shots, shots)
                self.assertLess(fight.now, mark.expires)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 90)
                self.assertEqual(owner.stats.value(stats.ELIMINATIONS) - eliminations, 1)
                self.assertEqual(near_hp.total, near_before)
                run.step({})
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertNotIn(mark.instance, mark.script.component.instances)
                self.assertEqual(turret.target, near.body)
                for _ in range(24):
                    if near_hp.total < near_before:
                        break
                    run.step({})
                self.assertEqual(near_hp.total, near_before - 14)
                if definition is not None:
                    self.assertEqual(bot.body_script.death_frame, hp.death_tick)
                    continue

                near_position = tuple(near.mover.position)
                fixture.place(near, (x + 100, y, z))
                for _ in range(650):
                    run.step({})
                    if far.body != entity:
                        break
                self.assertNotEqual(far.body, entity)
                self.assertFalse(fight.is_dead(far))
                fixture.place(far, position)
                fixture.place(near, near_position)
                run.step({})
                self.assertFalse(fight.torbjorn_marks)
                self.assertEqual(turret.target, near.body)
                self.fire(run, owner, far)
                self.advance(run, 18)
                self.assertEqual(turret.target, far.body)
                self.assertIn((owner.entity, far.body), fight.torbjorn_marks)

    def test_actual_phases_clear_mark_and_redirect_the_thrown_turret(self):
        fixture = defenses.TorbjornDefenseTests()
        phase = phase_tests.TorbjornPhaseTests()
        by_name = {hero.name: hero.guid for hero in heroes().values()}
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(hero=name):
                run, owner, near, far, turret = self.arena(far_hero=by_name[name], thrown=True)
                fight = run.match.combat
                self.assertEqual(turret.target, near.body)
                near_hp = fight.vitals[near.body]
                far_hp = fight.vitals[far.body]
                key = owner.entity, far.body
                self.fire(run, owner, far)
                for _ in range(24):
                    if key in fight.torbjorn_marks:
                        break
                    run.step({})
                mark = fight.torbjorn_marks[key]
                self.assertEqual(turret.target, far.body)
                self.assertTrue(torbjorn_mark.active(mark))
                near_before = near_hp.total
                far_before = far_hp.total
                phase.cast(run, far)
                self.assertLess(fight.now, mark.expires)
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertNotIn(mark.instance, mark.script.component.instances)
                self.assertEqual(turret.target, near.body)
                for _ in range(22):
                    if near_hp.total < near_before:
                        break
                    run.step({})
                self.assertTrue(phase_defenses.phase(far)[0])
                self.assertEqual(near_hp.total, near_before - 14)
                self.assertGreaterEqual(far_hp.total, far_before)

                near_position = tuple(near.mover.position)
                x, y, z = owner.mover.position
                fixture.place(near, (x + 100, y, z))
                for _ in range(350):
                    if not phase_defenses.phase(far)[0]:
                        break
                    run.step({})
                self.assertFalse(phase_defenses.phase(far)[0])
                self.assertNotIn(key, fight.torbjorn_marks)
                fixture.place(near, near_position)
                run.step({})
                self.assertEqual(turret.target, near.body)
                far_before = far_hp.total
                self.fire(run, owner, far)
                for _ in range(24):
                    if key in fight.torbjorn_marks:
                        break
                    run.step({})
                new_mark = fight.torbjorn_marks[key]
                self.assertNotEqual(new_mark.instance, mark.instance)
                self.assertEqual(turret.target, far.body)
                self.assertLessEqual(far_hp.total, far_before - 70)
                before_shots = turret.shots
                for _ in range(24):
                    if turret.shots > before_shots:
                        break
                    run.step({})
                self.assertEqual(turret.shots, before_shots + 1)
                self.assertEqual(turret.target, far.body)
                self.assertLessEqual(far_hp.total, far_before - 84)

    def test_phase_input_before_or_on_turret_shot_frame_preserves_settlement_order(self):
        by_name = {hero.name: hero.guid for hero in heroes().values()}
        for name in ("Reaper", "Moira", "Mei"):
            for early in (False, True):
                with self.subTest(hero=name, early=early):
                    run, owner, near, far, turret = self.arena(far_hero=by_name[name], thrown=True)
                    fight = run.match.combat
                    key = owner.entity, far.body
                    self.fire(run, owner, far)
                    for _ in range(24):
                        if key in fight.torbjorn_marks:
                            break
                        run.step({})
                    mark = fight.torbjorn_marks[key]
                    self.assertTrue(torbjorn_mark.active(mark))
                    self.assertEqual(turret.target, far.body)
                    due = math.ceil((turret.next_fire - combat.EPSILON) / combat.TICK)
                    activation = due - int(early)
                    if activation <= run.frame:
                        self.advance(run, due - run.frame)
                        due = math.ceil((turret.next_fire - combat.EPSILON) / combat.TICK)
                        activation = due - int(early)
                    self.assertGreater(activation, run.frame)
                    self.assertLess(due * combat.TICK, mark.expires)
                    self.advance(run, activation - run.frame - 1)
                    near_hp = fight.vitals[near.body]
                    near_before = near_hp.total
                    credit = owner.stats.value(stats.HERO_DAMAGE)
                    shots = turret.shots
                    with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                        run.step({far: (-32768, 0, 8)})
                        self.assertTrue(phase_defenses.phase(far)[0])
                        self.assertFalse(torbjorn_mark.active(mark))
                        if early:
                            self.assertEqual(turret.shots, shots)
                            run.step({})
                        self.assertEqual(run.frame, due)
                        self.assertEqual(turret.shots, shots + 1)
                        self.assertEqual(
                            [call.args[1].owner for call in damage.call_args_list
                             if call.args[0] is turret.actor],
                            [near if early else far],
                        )
                    self.assertEqual(near_hp.total, near_before - (14 if early else 0))
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), credit + 14)
                    near_before = near_hp.total
                    for _ in range(24):
                        if turret.shots == shots + 2:
                            break
                        run.step({})
                    self.assertEqual(turret.shots, shots + 2)
                    self.assertEqual(turret.target, near.body)
                    self.assertEqual(near_hp.total, near_before - 14)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), credit + 28)
                    self.assertNotIn(key, fight.torbjorn_marks)

    def test_authored_gate_flags_refuse_new_tags_and_cancel_existing_tags(self):
        for flag in (6604, 8440):
            for before_hit in (True, False):
                with self.subTest(flag=flag, before_hit=before_hit):
                    run, owner, near, far, turret = self.arena()
                    component = far.body_script.component
                    if before_hit:
                        component.set(flag, True)
                    self.fire(run, owner, far)
                    self.advance(run, 20)
                    fight = run.match.combat
                    if not before_hit:
                        self.assertIn((owner.entity, far.body), fight.torbjorn_marks)
                        component.set(flag, True)
                        # Command processing advances the victim VM after the
                        # world status pass; the next pass sees its gate.
                        self.advance(run, 2)
                    self.assertEqual(fight.torbjorn_marks, {})
                    self.assertEqual(turret.target, near.body)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five-NPC catalog")
    def test_actual_five_npc_marks_yield_to_cover_then_expire_to_nearest(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, near, _, turret = self.arena(far_parked=True)
                match = run.match
                fight = match.combat
                x, y, z = owner.mover.position
                actor = npc.spawn(match, definition, (x, y, z + 12), team=1 - owner.team)
                hp = fight.vitals[actor.entity]
                self.advance(run, 10)
                self.assertEqual(turret.target, near.body)
                yaw, pitch = aim(fight._eye(owner), (x, y + 1, z + 12))
                run.step({owner: (yaw, pitch, 1)})
                self.advance(run, 20)
                key = owner.entity, actor.entity
                mark = fight.torbjorn_marks[key]
                self.assertTrue(torbjorn_mark.active(mark))
                self.assertEqual(turret.target, actor.entity)
                before = hp.total
                clear = match.collision_world
                match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall(x - 3, z + 9, x + 3, z + 9, y0=y, y1=y + 4)
                )
                self.advance(run, 6)
                self.assertIs(fight.torbjorn_marks[key], mark)
                self.assertEqual(turret.target, near.body)
                self.assertEqual(hp.total, before)
                match.collision_world = clear
                self.advance(run, 15)
                self.assertIs(fight.torbjorn_marks[key], mark)
                self.assertEqual(turret.target, actor.entity)
                self.assertLess(hp.total, before)
                self.advance(run, 35)
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertNotIn(mark.instance, mark.script.component.instances)
                self.assertEqual(turret.target, near.body)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five-NPC catalog")
    def test_live_npc_allegiance_filters_thrown_turret_without_refreshing_mark(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, near, _, turret = self.arena(far_parked=True, thrown=True)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                enemy_team = 1 - owner.team
                near.team = enemy_team
                x, y, z = owner.mover.position
                actor = npc.spawn(match, definition, (x, y, z + 12), team=enemy_team)
                hp = fight.vitals[actor.entity]
                near_hp = fight.vitals[near.body]
                key = owner.entity, actor.entity
                before = hp.total
                self.assertEqual(turret.target, near.body)
                run.step({owner: (*aim(fight._eye(owner), (x, y + 1, z + 12)), 1)})
                for _ in range(22):
                    if key in fight.torbjorn_marks:
                        break
                    run.step({})
                mark = fight.torbjorn_marks[key]
                deadline = mark.expires
                self.assertTrue(torbjorn_mark.active(mark))
                self.assertEqual(turret.target, actor.entity)
                self.assertIn(before - hp.total, (70, 84))

                for team, victim, other, target in ((owner.team, near_hp, hp, near.body),
                                                    (enemy_team, hp, near_hp, actor.entity)):
                    actor.team = team
                    victim_before, other_before = victim.total, other.total
                    credit = owner.stats.value(stats.HERO_DAMAGE)
                    shots = turret.shots
                    for _ in range(24):
                        run.step({})
                        self.assertEqual(turret.target, target)
                        self.assertEqual(other.total, other_before)
                        if victim.total < victim_before:
                            break
                    self.assertEqual(victim.total, victim_before - 14)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), credit + 14)
                    self.assertEqual(turret.shots, shots + 1)
                    self.assertIs(fight.torbjorn_marks[key], mark)
                    self.assertEqual(mark.expires, deadline)

                actor.team = owner.team
                before = hp.total
                while fight.now < deadline:
                    self.assertIs(fight.torbjorn_marks[key], mark)
                    run.step({})
                    self.assertEqual(turret.target, near.body)
                    self.assertEqual(hp.total, before)
                self.assertNotIn(key, fight.torbjorn_marks)
                self.assertNotIn(mark.instance, mark.script.component.instances)
                actor.team = enemy_team
                near_before = near_hp.total
                for _ in range(24):
                    run.step({})
                    self.assertEqual(turret.target, near.body)
                    if near_hp.total < near_before:
                        break
                self.assertEqual(near_hp.total, near_before - 14)
                self.assertEqual(hp.total, before)

    def test_active_tag_and_expiration_are_captured_in_passive_snapshot(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        owner, victim = players
        session.apply("switch_hero", {"account": owner.account_lo, "hero": hero_torbjorn.HERO})
        fight = owner.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        torbjorn_mark.apply(fight, owner, target)
        mark = fight.torbjorn_marks[owner.entity, victim.body]
        first = snapshot_digest(case.capture(session))
        mark.expires += 0.1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))
