"""Actual0BDD projectile and scoped224E/676B sleep interaction with Reaper."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_reaper_ana as ana_tests
import test_reaper_control as control_tests
from test_game_combat import aim

from ow174.game import ana, combat, defenses, dva_matrix, pools, reaper, sigma_grasp, stats, zarya_barriers
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, find_hero, heroes


class ReaperSleepTests(unittest.TestCase):
    def scene(self, **kwargs):
        run, target, caster = ana_tests.ReaperAnaTests().scene(**kwargs)
        attacker = run.players[2]
        run.match.switch_hero(attacker, heroes()[reaper.HERO])
        x, y, z = target.mover.position
        attacker.mover.position[:] = [x + 3, y, z]
        for _ in range(20):
            run.step({})
        return run, target, caster

    def health(self, run, actor):
        return run.match.combat.vitals[actor.body].total

    def step(self, run, caster, target, *, buttons=0):
        x, y, z = target.mover.position
        yaw, pitch = aim(run.match.combat._eye(caster), (x, y + 1, z))
        run.step({caster: (yaw, pitch, buttons)})

    def sleep(self, run, caster, target):
        self.step(run, caster, target, buttons=8)
        for _ in range(40):
            self.step(run, caster, target)

    def advance(self, run, tick):
        run.frame = tick
        run.match.update(tick * combat.TICK, tick, frames=False)

    def reflect_dart(self, run, caster, genji, guard, heading, *, held=None):
        fight = run.match.combat
        held = {} if held is None else held
        run.step(held | {caster: (*heading, 8), genji: (*guard, 0)})
        for _ in range(30):
            if fight.rockets:
                break
            run.step(held | {caster: (*heading, 0), genji: (*guard, 0)})
        original, = fight.rockets
        self.assertIs(original.shooter, caster)
        self.assertEqual(original.direct.effect, ana.SLEEP_EFFECT)
        for _ in range(30):
            run.step(held | {genji: (*guard, 0)})
            if any(rocket.reflected for rocket in fight.rockets):
                break
        returned, = fight.rockets
        self.assertTrue(returned.reflected)
        self.assertIs(returned.shooter, genji)
        self.assertEqual(returned.team, genji.team)
        self.assertEqual(returned.direct.effect, ana.SLEEP_EFFECT)
        return returned

    def test_actual_genji_reflected_sleep_dart_sleeps_anas_ally(self):
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene()
        fight = run.match.combat
        self.reflect_dart(run, caster, genji, guard, heading)
        for _ in range(30):
            run.step({genji: (*guard, 0)})
            if target.body in fight.ana_sleeps:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(self.health(run, target), 245)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 5)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
        sleep = fight.ana_sleeps[target.body]
        self.assertEqual(sleep["end"] - sleep["start"], 313)
        run.step({target: (0, 0, 8)})
        self.assertFalse(reaper.intangible(target))
        self.advance(run, sleep["end"] - 1)
        self.assertTrue(fight.is_stunned(target))
        self.advance(run, sleep["end"])
        self.assertFalse(fight.is_stunned(target))
        run.step({target: (0, 0, 8)})
        self.assertTrue(reaper.intangible(target))

    def test_actual_projected_barrier_spends_five_on_reflected_sleep_dart(self):
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene(
            names=("Reaper", "Ana", "Genji", "Zarya")
        )
        fight = run.match.combat
        zarya = run.players[3]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        zarya.team = target.team
        x, y, z = target.mover.position
        zarya.mover.position[:] = (x + 4, y, z)
        zarya.mover.body = None
        run.step({genji: (*guard, 0)})
        self.reflect_dart(run, caster, genji, guard, heading)
        shield_aim = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (*shield_aim, 16)})
        bubble = fight.zarya_bubbles.for_target(target)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        for _ in range(30):
            if not fight.rockets:
                break
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(bubble.health, 195)
        self.assertEqual(self.health(run, target), 250)
        self.assertNotIn(target.body, fight.ana_sleeps)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        run.step({target: (0, 0, 8)})
        self.assertTrue(reaper.intangible(target))

    def projected_reflected_dart_scene(self):
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene(
            activate=False, names=("Reaper", "Ana", "Genji", "Zarya"),
        )
        fight = run.match.combat
        zarya = run.players[3]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        zarya.team = target.team
        x, y, z = target.mover.position
        zarya.mover.position[:] = (x + 4, y, z)
        zarya.mover.body = None
        shield_aim = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (*shield_aim, 16)})
        bubble = fight.zarya_bubbles.for_target(target)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        return run, target, caster, genji, guard, heading, bubble

    def launch_projected_shadow_dart(self, run, target, caster, genji, guard, heading):
        run.step({genji: (*guard, 16)})
        record = control_tests.ReaperControlTests().start_shadow(run, target)
        dart = self.reflect_dart(run, caster, genji, guard, heading)
        self.assertIn(dart, run.match.combat.rockets)
        self.assertFalse(reaper.intangible(target))
        return dart, record

    def projected_reflected_dart_trace(self, expiry, travel, *, protected, skipped):
        run, target, caster, genji, guard, heading, bubble = self.projected_reflected_dart_scene()
        fight = run.match.combat
        origin = tuple(target.mover.position)
        surface = expiry - int(protected)
        self.advance(run, surface - travel)
        dart, record = self.launch_projected_shadow_dart(run, target, caster, genji, guard, heading)
        self.assertEqual(dart.last_tick, run.frame)
        contacts = []
        landed = fight._landed

        def observe(rocket):
            if rocket is dart:
                contacts.append((fight.tick, fight.zarya_bubbles.for_target(target) is bubble))
            landed(rocket)

        end = run.frame + 165
        with patch.object(fight, "_landed", side_effect=observe):
            while run.frame < end:
                self.advance(run, end if skipped else run.frame + 1)
        self.assertFalse(fight.rockets)
        self.assertIsNone(fight.zarya_bubbles.for_target(target))
        self.assertEqual(len(contacts), 1)
        at, had_bubble = contacts[0]
        self.assertEqual(had_bubble, protected)
        self.assertEqual(bubble.health, 195 if protected else 200)
        self.assertEqual(self.health(run, target), 250 if protected else 245)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0 if protected else 5)
        sleep = fight.ana_sleeps.get(target.body)
        if protected:
            self.assertEqual(at, surface)
            self.assertIsNone(sleep)
            expected = record["target"]
        else:
            self.assertGreaterEqual(at, surface)
            self.assertIsNotNone(sleep)
            self.assertEqual((sleep["start"], sleep["end"]), (at, at + 313))
            expected = origin
        self.assertFalse(reaper.intangible(target))
        position = tuple(target.mover.position)
        for actual, desired in zip(position, expected, strict=True):
            self.assertAlmostEqual(actual, desired, delta=0.02)
        cooldown = control_tests.ReaperControlTests().shadow(target).vars[61].value()
        self.assertGreater(cooldown, 0)
        return contacts, sleep, position, cooldown

    def test_actual_reflected_sleep_dart_resolves_projection_expiry_during_shadow(self):
        run, target, caster, genji, guard, heading, bubble = self.projected_reflected_dart_scene()
        fight = run.match.combat
        start = run.frame
        self.launch_projected_shadow_dart(run, target, caster, genji, guard, heading)
        for _ in range(30):
            self.advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertEqual(bubble.health, 195)
        travel = run.frame - start
        for _ in range(150):
            self.advance(run, run.frame + 1)
            if fight.zarya_bubbles.for_target(target) is None:
                break
        self.assertIsNone(fight.zarya_bubbles.for_target(target))
        expiry = run.frame
        for protected in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    outcome = self.projected_reflected_dart_trace(
                        expiry, travel, protected=protected, skipped=skipped,
                    )
                    outcomes.append(outcome)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_grasp_converts_reflected_sleep_dart_to_sigma_shields(self):
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene(
            names=("Reaper", "Ana", "Genji", "Sigma")
        )
        fight = run.match.combat
        sigma = run.players[3]
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        sigma.team = target.team
        x, y, z = target.mover.position
        sigma.mover.position[:] = (x, y, z + 1.5)
        sigma.mover.body = None
        run.step({sigma: (0, 0, 8), genji: (*guard, 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        self.reflect_dart(run, caster, genji, guard, heading)
        for _ in range(30):
            if not fight.rockets:
                break
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(grasp.vars[21312].value(), 5)
        self.assertEqual(self.health(run, target), 250)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertNotIn(target.body, fight.ana_sleeps)
        self.assertNotIn(sigma.body, fight.ana_sleeps)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

        for _ in range(220):
            if sigma.body in fight.grasp_shields:
                break
            run.step({})
        self.assertFalse(grasp.states[1].active)
        shield = fight.vitals[sigma.body].temporary_shields[sigma_grasp.SHIELD_TAG]
        self.assertAlmostEqual(shield.now, 3)
        self.assertAlmostEqual(self.health(run, sigma), 403)

    def grasp_expiry_reflected_dart_scene(self):
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene(
            activate=False, names=("Reaper", "Ana", "Genji", "Sigma"),
        )
        sigma = run.players[3]
        sigma.team = target.team
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        x, y, z = target.mover.position
        sigma.mover.position[:] = (x + 1.1, y, z + 1.5)
        sigma.mover.body = None
        for _ in range(12):
            run.step({})
        run.step({sigma: (0, 0, 8)})
        self.assertTrue(sigma_grasp.active(sigma))
        return run, target, caster, genji, guard, heading, sigma

    def launch_reflected_dart_during_blossom(self, run, target, caster, genji, guard, heading):
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32), genji: (*guard, 16)})
        self.assertIsNotNone(reaper.blossom_state(target.body_script))
        return self.reflect_dart(run, caster, genji, guard, heading)

    def reflected_dart_grasp_expiry_trace(self, expiry, travel, *, protected, skipped):
        run, target, caster, genji, guard, heading, sigma = self.grasp_expiry_reflected_dart_scene()
        fight = run.match.combat
        sigma_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
        surface_tick = expiry - int(protected)
        self.advance(run, surface_tick - travel)
        self.assertTrue(sigma_grasp.active(sigma))
        dart = self.launch_reflected_dart_during_blossom(run, target, caster, genji, guard, heading)
        self.assertEqual(dart.last_tick, run.frame)
        if protected:
            self.assertTrue(sigma_grasp.active(sigma))
        contacts = []
        landed = fight._landed

        def observe(rocket):
            if rocket is dart:
                contacts.append((fight.tick, sigma_grasp.active(sigma), self.health(run, genji)))
            landed(rocket)

        end = run.frame + 165
        with patch.object(fight, "_landed", side_effect=observe):
            while run.frame < end:
                self.advance(run, end if skipped else run.frame + 1)
        self.assertFalse(fight.rockets)
        self.assertEqual(len(contacts), 1)
        at, was_grasping, defender_health = contacts[0]
        self.assertEqual(was_grasping, protected)
        self.assertFalse(sigma_grasp.active(sigma))
        self.assertNotIn(sigma.body, fight.ana_sleeps)
        self.assertEqual(sigma_pool.total, 403 if protected else 400)
        shield = sigma_pool.temporary_shields.get(sigma_grasp.SHIELD_TAG)
        self.assertEqual(shield.now if shield is not None else 0, 3 if protected else 0)
        self.assertEqual(self.health(run, target), 250 if protected else 245)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0 if protected else 5)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
        sleep = fight.ana_sleeps.get(target.body)
        if protected:
            self.assertEqual(at, surface_tick)
            self.assertIsNone(sleep)
            self.assertLess(self.health(run, genji), defender_health)
        else:
            self.assertGreaterEqual(at, surface_tick)
            self.assertIsNotNone(sleep)
            self.assertEqual((sleep["start"], sleep["end"]), (at, at + 313))
            self.assertEqual(self.health(run, genji), defender_health)
            self.assertTrue(fight.reaper_blossoms[target.body]["ended"])
        self.assertEqual(target.body_script.component.vars[342].value(), 0)
        return contacts, sleep, self.health(run, genji)

    def test_actual_reflected_sleep_resolves_grasp_expiry_during_blossom(self):
        run, target, caster, genji, guard, heading, sigma = self.grasp_expiry_reflected_dart_scene()
        fight = run.match.combat
        start = run.frame
        self.launch_reflected_dart_during_blossom(run, target, caster, genji, guard, heading)
        for _ in range(30):
            self.advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertEqual(grasp.vars[21312].value(), 5)
        travel = run.frame - start
        for _ in range(180):
            self.advance(run, run.frame + 1)
            if not sigma_grasp.active(sigma):
                break
        self.assertFalse(sigma_grasp.active(sigma))
        expiry = run.frame
        for protected in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    outcome = self.reflected_dart_grasp_expiry_trace(
                        expiry, travel, protected=protected, skipped=skipped,
                    )
                    outcomes.append(outcome)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_matrix_absorbs_genji_reflected_sleep_dart(self):
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_MOVER_SHA256 and DVA_GRAPH_SHA256 and dva_matrix.TAG_CATALOG_SHA256):
            self.skipTest("requires the pinned D.Va supplements and projectile catalog")
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene(
            activate=False, names=("Reaper", "Ana", "Genji", "Dva")
        )
        fight = run.match.combat
        mech = run.players[3]
        self.assertTrue(run.match.switch_hero(mech, replace(find_hero("dva"), body=pools.DVA_MECH)))
        mech.team = target.team
        x, y, z = target.mover.position
        mech.mover.position[:] = (x, y, z + 1.5)
        mech.mover.body = None
        for _ in range(100):
            run.step({})
        held = {mech: (0, 0, 2)}
        for frame in range(12):
            run.step(held | {genji: (*guard, 16 if frame == 0 else 0)})
        matrix = next(i for i in mech.body_script.component.instances.values()
                      if i.graph.index == dva_matrix.GRAPH)
        self.assertTrue(matrix.states[dva_matrix.STATE].active)
        returned = self.reflect_dart(run, caster, genji, guard, heading, held=held)
        self.assertEqual(returned.definition, 0x04000000000015E8)
        self.assertTrue(dva_matrix.eligible(returned.definition))
        for _ in range(30):
            if not fight.rockets:
                break
            run.step(held)
        self.assertFalse(fight.rockets)
        self.assertTrue(matrix.states[dva_matrix.STATE].active)
        self.assertEqual(self.health(run, target), 250)
        self.assertEqual(self.health(run, mech), 600)
        self.assertNotIn(target.body, fight.ana_sleeps)
        self.assertNotIn(mech.body, fight.ana_sleeps)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        run.step(held | {target: (0, 0, 8)})
        self.assertTrue(reaper.intangible(target))

    def reflected_dart_overlapping_absorbers(self, *, layout="staggered"):
        run, target, caster, genji, _, _ = ana_tests.ReaperAnaTests().deflect_scene(
            activate=False, names=("Reaper", "Ana", "Genji", "Dva", "Sigma"),
        )
        mech, sigma = run.players[3:5]
        self.assertTrue(run.match.switch_hero(mech, replace(find_hero("dva"), body=pools.DVA_MECH)))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        mech.team = sigma.team = target.team
        x, y, z = target.mover.position
        matrix_z, grasp_z = {"staggered": (2, 1.5), "inside": (8, 13), "aligned": (4.25, 12)}[layout]
        mech.mover.position[:] = (x + 1.2, y, z + matrix_z)
        sigma.mover.position[:] = (x + 0.95, y, z + grasp_z)
        genji.mover.position[:] = (x, y, z + 16)
        mech.mover.body = sigma.mover.body = genji.mover.body = None
        for _ in range(100):
            run.step({})

        fight = run.match.combat
        x, y, z = target.mover.position
        guard = aim(fight._eye(genji), (x, y + 1, z))
        x, y, z = genji.mover.position
        heading = aim(fight._eye(caster), (x, y + 1, z))
        held = {mech: (0, 0, 2)}
        run.step(held | {genji: (*guard, 16), sigma: (0, 0, 8)})
        for _ in range(11):
            run.step(held | {genji: (*guard, 0)})
        dart = self.reflect_dart(run, caster, genji, guard, heading, held=held)
        self.assertTrue(sigma_grasp.active(sigma))
        if layout == "aligned":
            def front_depth(player, graph, state_id):
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == graph)
                state = instance.states[state_id]
                return max(state.evaluate(box["m_AEF67365"], watch=False).z
                           for box in state.node.field("m_boxes"))

            # Align the resource faces exactly after pose quantization.
            matrix_front = mech.mover.position[2] + front_depth(mech, dva_matrix.GRAPH, dva_matrix.STATE)
            sigma.mover.position[2] = matrix_front - front_depth(sigma, sigma_grasp.GRASP, 1)
        front = fight._projectile_defense_contact(
            genji, dart.position, dart.direction, 30, moving=True, definition=dart.definition,
        )
        rear = fight._projectile_defense_contact(
            genji, dart.position, dart.direction, 30, moving=True, definition=dart.definition,
            passed_defenders=(mech.body,),
        )
        self.assertIsNotNone(front)
        self.assertIsNotNone(rear)
        self.assertIs(front[1], mech)
        self.assertIs(rear[1], sigma)
        if layout == "inside":
            self.assertEqual((front[0], rear[0]), (0, 0))
        elif layout == "aligned":
            self.assertEqual(front[0], rear[0])
            self.assertGreater(front[0], 0)
            self.assertLess(front[0], dart.speed * combat.TICK)
        else:
            self.assertLess(front[0], rear[0])
        return run, target, genji, mech, sigma, held

    def reflected_dart_absorber_trace(self, travel, *, matrix_holds, skipped):
        run, target, genji, mech, sigma, held = self.reflected_dart_overlapping_absorbers()
        fight = run.match.combat
        expected = run.frame + travel
        last_held = expected - (1 if matrix_holds else 2)
        while run.frame < last_held:
            run.step(held)
        last_command = mech.commands.last_frame
        contact = fight._projectile_defense_contact
        contacts = []

        def observe(shooter, *args, **kwargs):
            result = contact(shooter, *args, **kwargs)
            if shooter is genji and result is not None:
                contacts.append((fight.tick, result[1]))
            return result

        with patch.object(fight, "_projectile_defense_contact", side_effect=observe):
            if skipped:
                self.advance(run, expected + 155)
            else:
                while run.frame < expected + 155:
                    self.advance(run, run.frame + 1)

        (tick, defender), = contacts
        self.assertIs(defender, mech if matrix_holds else sigma)
        if matrix_holds:
            self.assertEqual(tick, expected)
        else:
            self.assertGreater(tick, expected)
        self.assertFalse(fight.rockets)
        self.assertEqual(mech.commands.last_frame, last_command)
        for actor in (target, mech, sigma):
            self.assertNotIn(actor.body, fight.ana_sleeps)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.health(run, target), 250)
        self.assertEqual(self.health(run, mech), 600)
        self.assertEqual(self.health(run, sigma), 400 if matrix_holds else 403)
        return tick

    def test_actual_reflected_dart_transfers_from_expiring_matrix_to_grasp(self):
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_MOVER_SHA256 and DVA_GRAPH_SHA256 and dva_matrix.TAG_CATALOG_SHA256):
            self.skipTest("requires the pinned D.Va supplements and projectile catalog")

        run, _, _, _, _, held = self.reflected_dart_overlapping_absorbers()
        reflected = run.frame
        for _ in range(30):
            run.step(held)
            if not run.match.combat.rockets:
                break
        self.assertFalse(run.match.combat.rockets)
        travel = run.frame - reflected
        self.assertGreater(travel, 2)
        for matrix_holds in (True, False):
            with self.subTest(matrix_holds=matrix_holds):
                stepped = self.reflected_dart_absorber_trace(
                    travel, matrix_holds=matrix_holds, skipped=False,
                )
                skipped = self.reflected_dart_absorber_trace(
                    travel, matrix_holds=matrix_holds, skipped=True,
                )
                self.assertEqual(stepped, skipped)

    def tied_reflected_dart_trace(self, *, layout, grasp_first, skipped):
        run, target, genji, mech, sigma, _ = self.reflected_dart_overlapping_absorbers(layout=layout)
        fight = run.match.combat
        query = fight._projectile_defense_contact
        dart, = fight.rockets
        distance, *_ = query(
            genji, dart.position, dart.direction, dart.speed * combat.TICK,
            moving=True, definition=dart.definition,
        )
        if grasp_first:
            players = run.match.players
            mech_slot, sigma_slot = players.index(mech), players.index(sigma)
            players[mech_slot], players[sigma_slot] = sigma, mech
        contacts = []

        def observe(shooter, *args, **kwargs):
            result = query(shooter, *args, **kwargs)
            if shooter is genji and result is not None:
                contacts.append((fight.tick, result[0], result[1]))
            return result

        frames = mech.commands.last_frame, sigma.commands.last_frame
        next_tick = run.frame + 1
        end = run.frame + 155
        with patch.object(fight, "_projectile_defense_contact", side_effect=observe):
            while run.frame < end:
                self.advance(run, end if skipped else run.frame + 1)
        self.assertEqual(contacts, [(next_tick, distance, sigma if grasp_first else mech)])
        self.assertEqual((mech.commands.last_frame, sigma.commands.last_frame), frames)
        self.assertFalse(fight.rockets)
        self.assertFalse(sigma_grasp.active(sigma))
        for actor in (target, mech, sigma):
            self.assertNotIn(actor.body, fight.ana_sleeps)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.health(run, target), 250)
        self.assertEqual(self.health(run, mech), 600)
        self.assertEqual(self.health(run, sigma), 403 if grasp_first else 400)
        return contacts[0][:2], self.health(run, sigma)

    def test_actual_reflected_dart_resolves_tied_matrix_and_grasp(self):
        self.check_tied_reflected_dart(layout="inside")

    def test_actual_reflected_dart_resolves_tied_absorber_front_faces(self):
        self.check_tied_reflected_dart(layout="aligned")

    def check_tied_reflected_dart(self, *, layout):
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_MOVER_SHA256 and DVA_GRAPH_SHA256 and dva_matrix.TAG_CATALOG_SHA256):
            self.skipTest("requires the pinned D.Va supplements and projectile catalog")
        for grasp_first in (False, True):
            with self.subTest(grasp_first=grasp_first):
                stepped = self.tied_reflected_dart_trace(
                    layout=layout, grasp_first=grasp_first, skipped=False,
                )
                skipped = self.tied_reflected_dart_trace(
                    layout=layout, grasp_first=grasp_first, skipped=True,
                )
                self.assertEqual(stepped, skipped)

    def reflected_dart_projection_grasp_trace(self, *, projected, skipped, surface_offset=None):
        run, owner, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene(
            activate=False, names=("Reaper", "Ana", "Genji", "Sigma", "Zarya"),
        )
        sigma, zarya = run.players[3:5]
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        sigma.team = zarya.team = owner.team
        x, y, z = owner.mover.position
        sigma.mover.position[:] = (x + 0.75, y, z + 3)
        zarya.mover.position[:] = (x + 7, y, z + 3)
        sigma.mover.body = zarya.mover.body = None
        for _ in range(12):
            run.step({})

        fight = run.match.combat
        receiver = sigma if surface_offset is None else owner
        projection = aim(fight._eye(zarya), fight._eye(receiver))
        if surface_offset is not None:
            guard = -32768, 0
        run.step({
            genji: (*guard, 16), sigma: (0, 0, 8), zarya: (*projection, 16 if projected else 0),
        })
        bubble = fight.zarya_bubbles.for_target(receiver)
        self.assertEqual(bubble is not None, projected)
        if bubble is not None:
            self.assertEqual(bubble.health, 200)
        dart = self.reflect_dart(run, caster, genji, guard, heading)
        self.assertTrue(sigma_grasp.active(sigma))
        barrier_distance = 0
        grasp_distance = 0
        if surface_offset is not None:
            # A horizontal return through the sphere centre gives an exact front contact.
            # Place the resource box face on either side of it or exactly on it.
            x, y, z = dart.position
            centre_height = bubble.centre()[1] - owner.mover.position[1]
            barrier_distance = 0.5
            owner.mover.position[:] = (x, y - centre_height, z - (zarya_barriers.RADIUS + barrier_distance))
            grasp = next(i for i in sigma.body_script.component.instances.values()
                         if i.graph.index == sigma_grasp.GRASP).states[1]
            front = max(grasp.evaluate(box["m_AEF67365"], watch=False).z
                        for box in grasp.node.field("m_boxes"))
            sigma.mover.position[2] = z - barrier_distance - front + surface_offset
            grasp_distance = barrier_distance - surface_offset
        reach = dart.speed * combat.TICK
        defense = fight._projectile_defense_contact(
            genji, dart.position, dart.direction, reach, moving=True, definition=dart.definition,
        )
        self.assertIsNotNone(defense)
        self.assertEqual(defense[:2], (grasp_distance, sigma))
        barrier = fight.zarya_bubbles.intercept(genji, dart.position, dart.direction, reach)
        self.assertEqual(barrier, (barrier_distance, bubble) if projected else None)
        projection_wins = projected and barrier_distance <= grasp_distance

        frames = sigma.commands.last_frame, zarya.commands.last_frame
        reflected = run.frame
        contacts = []
        landed = fight._landed

        def observe(rocket):
            if rocket is dart:
                contacts.append(fight.tick)
            landed(rocket)

        end = reflected + 155
        with patch.object(fight, "_landed", side_effect=observe), patch.object(
            fight, "_absorb_hit", wraps=fight._absorb_hit,
        ) as absorbed:
            while run.frame < end:
                self.advance(run, end if skipped else run.frame + 1)
        self.assertEqual(contacts, [reflected + 1])
        self.assertEqual(absorbed.call_count, int(not projection_wins))
        if bubble is not None:
            self.assertEqual(bubble.health, 195 if projection_wins else 200)
        if not projection_wins:
            self.assertEqual(absorbed.call_args.args[2], 5)
        self.assertEqual((sigma.commands.last_frame, zarya.commands.last_frame), frames)
        self.assertFalse(fight.rockets)
        self.assertFalse(sigma_grasp.active(sigma))
        self.assertNotIn(owner.body, fight.ana_sleeps)
        self.assertNotIn(sigma.body, fight.ana_sleeps)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.health(run, owner), 250)
        self.assertEqual(self.health(run, sigma), 400 if projection_wins else 403)
        return contacts, self.health(run, sigma)

    def test_actual_reflected_dart_prefers_tied_projection_over_grasp(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                stepped = self.reflected_dart_projection_grasp_trace(projected=projected, skipped=False)
                skipped = self.reflected_dart_projection_grasp_trace(projected=projected, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_actual_reflected_dart_resolves_projection_grasp_front_contacts(self):
        for surface_offset in (-0.125, 0, 0.125):
            with self.subTest(surface_offset=surface_offset):
                stepped = self.reflected_dart_projection_grasp_trace(
                    projected=True, skipped=False, surface_offset=surface_offset,
                )
                skipped = self.reflected_dart_projection_grasp_trace(
                    projected=True, skipped=True, surface_offset=surface_offset,
                )
                self.assertEqual(stepped, skipped)

    def test_actual_dart_blocks_reaper_input_then_world_only_expiry_recovers(self):
        run, target, caster = self.scene()
        self.sleep(run, caster, target)
        fight = run.match.combat
        self.assertEqual(self.health(run, target), 245)
        self.assertTrue(fight.is_stunned(target))
        record = fight.ana_sleeps[target.body]
        self.assertEqual(record['end'] - record['start'], 313)
        position = tuple(target.mover.position)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        target.take_commands([Command(run.frame, yaw=0, pitch=0, buttons=1 | 8 | 16 | 32 | 0x800,
                                      forward=127, extra=0)])
        self.assertEqual(tuple(target.mover.position), position)
        self.assertEqual(self.health(run, caster), 200)
        self.assertFalse(target.body_script.component.held)
        self.assertIsNone(reaper.blossom_state(target.body_script))
        self.advance(run, record['end'])
        self.assertFalse(fight.is_stunned(target))
        self.assertNotIn(target.body, fight.ana_sleeps)
        reaper_tests.ReaperTests().fire(run, target, caster)
        self.assertEqual(self.health(run, caster), 80)

    def hit(self, run, attacker, target):
        reaper_tests.ReaperTests().fire(run, attacker, target)

    def test_damage_after_grace_wakes_with_half_second_recovery_and_no_extension(self):
        run, target, caster = self.scene()
        self.sleep(run, caster, target)
        fight = run.match.combat
        record = fight.ana_sleeps[target.body]
        self.advance(run, record['start'] + 40)
        attacker = run.players[2]
        x, y, z = target.mover.position
        attacker.mover.position[:] = [x + 3, y, z]
        self.hit(run, attacker, target)
        self.assertLess(self.health(run, target), 245)
        self.assertTrue(record['waking'])
        deadline = record['end']
        self.assertEqual(deadline - fight.tick, 32)
        for _ in range(8):
            run.step({})
        self.hit(run, attacker, target)
        self.assertEqual(record['end'], deadline)
        self.advance(run, deadline - 1)
        self.assertTrue(fight.is_stunned(target))
        self.advance(run, deadline)
        self.assertFalse(fight.is_stunned(target))

    def test_damage_in_first_half_second_does_not_wake(self):
        run, target, caster = self.scene()
        self.step(run, caster, target, buttons=8)
        fight = run.match.combat
        for _ in range(60):
            self.step(run, caster, target)
            if target.body in fight.ana_sleeps:
                break
        record = fight.ana_sleeps[target.body]
        deadline = record['end']
        attacker = run.players[2]
        x, y, z = target.mover.position
        attacker.mover.position[:] = [x + 3, y, z]
        self.hit(run, attacker, target)
        self.assertLess(self.health(run, target), 245)
        self.assertFalse(record['waking'])
        self.assertEqual(record['end'], deadline)

    def test_wraith_zen_mei_protection_reject_dart_damage_and_sleep(self):
        for hero, button in [(reaper.HERO, 8), (defenses.ZENYATTA, 32), (defenses.MEI, 8)]:
            with self.subTest(hero=hero):
                run, target, caster = self.scene(hero=hero)
                target.body_script.set_ult_charge(10000)
                run.step({target: (0, 0, button)})
                before = self.health(run, target)
                self.sleep(run, caster, target)
                self.assertEqual(self.health(run, target), before)
                self.assertNotIn(target.body, run.match.combat.ana_sleeps)

    def test_actual_sleep_interrupts_blossom_without_refund_or_late_damage(self):
        run, target, caster = self.scene()
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(target.body_script))
        self.sleep(run, caster, target)
        fight = run.match.combat
        self.assertTrue(fight.is_stunned(target))
        self.assertIsNone(reaper.blossom_state(target.body_script))
        self.assertEqual(target.body_script.component.vars[342].value(), 0)
        before = self.health(run, caster)
        self.advance(run, fight.tick + 200)
        self.assertEqual(self.health(run, caster), before)

    def test_actual_reflected_sleep_dart_interrupts_blossom(self):
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene()
        fight = run.match.combat
        self.reflect_dart(run, caster, genji, guard, heading)
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32), genji: (*guard, 0)})
        self.assertIsNotNone(reaper.blossom_state(target.body_script))
        self.assertEqual(self.health(run, genji), 166)
        self.assertEqual(self.health(run, target), 250)
        for _ in range(25):
            run.step({genji: (*guard, 0)})
            if target.body in fight.ana_sleeps:
                break
        self.assertEqual(self.health(run, target), 245)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 5)
        self.assertTrue(fight.is_stunned(target))
        self.assertIsNone(reaper.blossom_state(target.body_script))
        self.assertTrue(fight.reaper_blossoms[target.body]["ended"])
        self.assertEqual(target.body_script.component.vars[342].value(), 0)
        self.advance(run, fight.tick + 200)
        self.assertEqual(self.health(run, genji), 166)
        self.assertEqual(target.body_script.component.vars[342].value(), 0)

    def reflected_dart_after_projection_break_trace(self, *, skipped):
        run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene(
            names=("Reaper", "Ana", "Genji", "Zarya", "Second Ana"),
        )
        fight = run.match.combat
        zarya, second = run.players[3:]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        self.assertTrue(run.match.switch_hero(second, heroes()[ana.HERO]))
        zarya.team = second.team = target.team
        x, y, z = target.mover.position
        zarya.mover.position[:] = (x + 4, y, z)
        second.mover.position[:] = (x - 2, y, z - 3)
        zarya.mover.body = second.mover.body = None
        for _ in range(20):
            run.step({genji: (*guard, 0)})
        shield_aim = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (*shield_aim, 16), genji: (*guard, 0)})
        bubble = fight.zarya_bubbles.for_target(target)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 5

        first = self.reflect_dart(run, caster, genji, guard, heading)
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32), genji: (*guard, 0)})
        self.assertIsNotNone(reaper.blossom_state(target.body_script))
        for _ in range(20):
            if first not in fight.rockets:
                break
            run.step({genji: (*guard, 0)})
        self.assertFalse(fight.rockets)
        self.assertEqual(bubble.health, 0)
        self.assertFalse(bubble.phase.active)
        self.assertIsNone(fight.zarya_bubbles.for_target(target))
        self.assertEqual(self.health(run, target), 250)
        self.assertNotIn(target.body, fight.ana_sleeps)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        channel = fight.reaper_blossoms[target.body]
        self.assertFalse(channel["ended"])

        second_heading = aim(fight._eye(second), (x, y + 1, z + 5))
        dart = self.reflect_dart(run, second, genji, guard, second_heading)
        self.assertEqual(dart.last_tick, run.frame)
        self.assertIsNotNone(reaper.blossom_state(target.body_script))
        contacts = []
        landed = fight._landed

        def observe(rocket):
            if rocket is dart:
                contacts.append((fight.tick, self.health(run, genji)))
            landed(rocket)

        end = run.frame + 165
        with patch.object(fight, "_landed", side_effect=observe):
            while run.frame < end:
                self.advance(run, end if skipped else run.frame + 1)
        self.assertFalse(fight.rockets)
        self.assertEqual(len(contacts), 1)
        at, defender_health = contacts[0]
        self.assertGreater(defender_health, 0)
        self.assertLess(defender_health, 166)
        self.assertEqual(self.health(run, genji), defender_health)
        self.assertEqual(self.health(run, target), 245)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 5)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(second.stats.value(stats.HERO_DAMAGE), 0)
        self.assertIsNone(reaper.blossom_state(target.body_script))
        self.assertTrue(channel["ended"])
        self.assertEqual(target.body_script.component.vars[342].value(), 0)
        sleep = fight.ana_sleeps[target.body]
        self.assertEqual((sleep["start"], sleep["end"]), (at, at + 313))
        return contacts, sleep

    def test_actual_reflected_sleep_darts_break_projection_then_interrupt_blossom(self):
        stepped = self.reflected_dart_after_projection_break_trace(skipped=False)
        self.assertEqual(stepped, self.reflected_dart_after_projection_break_trace(skipped=True))

    def test_actual_sleep_aborts_shadow_selector(self):
        run, target, caster = self.scene()
        run.step({target: (16384, 5000, 16)})
        run.step({target: (16384, 5000, 0)})
        self.assertTrue(control_tests.ReaperControlTests().shadow(target).state(0).active)
        self.sleep(run, caster, target)
        self.assertTrue(run.match.combat.is_stunned(target))
        shadow = control_tests.ReaperControlTests().shadow(target)
        self.assertFalse(shadow.state(0).active)
        self.assertFalse(shadow.state(6).active)
        self.assertEqual(shadow.vars[61].value(), 0)

    def test_friendly_and_miss_do_not_apply_sleep(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, target, caster = self.scene(friendly=friendly)
                if friendly:
                    self.sleep(run, caster, target)
                else:
                    run.step({caster: (0, 0, 8)})
                    for _ in range(60):
                        run.step({caster: (0, 0, 0)})
                self.assertEqual(self.health(run, target), 250)
                self.assertNotIn(target.body, run.match.combat.ana_sleeps)

    def test_caster_switch_keeps_applied_sleep_recipient_switch_clears_it(self):
        run, target, caster = self.scene()
        self.sleep(run, caster, target)
        fight = run.match.combat
        run.match.switch_hero(caster, heroes()[SOLDIER])
        self.assertTrue(fight.is_stunned(target))
        old = target.body
        run.match.switch_hero(target, heroes()[SOLDIER])
        self.assertNotIn(old, fight.ana_sleeps)
        self.assertFalse(fight.is_stunned(target))

    def test_lethal_actual_shell_clears_sleep_without_reviving_recipient(self):
        run, target, caster = self.scene()
        self.sleep(run, caster, target)
        fight = run.match.combat
        attacker = run.players[2]
        for _ in range(3):
            self.hit(run, attacker, target)
            for _ in range(40):
                run.step({})
        self.assertTrue(fight.is_dead(target))
        self.assertNotIn(target.body, fight.ana_sleeps)

    def test_flashbang_overlap_has_independent_deadline(self):
        fixture = control_tests.ReaperControlTests()
        run, flash, target = fixture.scene()
        caster = run.players[0]
        origin = tuple(caster.mover.position)
        # The source sends a dart, then switches back to the Flashbang body.
        run.match.switch_hero(caster, heroes()[ana.HERO])
        caster.mover.position[:] = origin
        caster.mover.body = None
        for _ in range(20):
            run.step({})
        self.sleep(run, caster, target)
        fight = run.match.combat
        self.assertTrue(fight.is_stunned(target))
        sleep_end = fight.ana_sleeps[target.body]["end"]
        run.match.switch_hero(flash, heroes()[0x02E0000000000042])
        flash.mover.position[:] = origin
        flash.mover.body = None
        for _ in range(20):
            run.step({})
        fixture.step(run, flash, target, cast=16)
        for _ in range(50):
            fixture.step(run, flash, target)
            if target.body in fight.stuns:
                break
        stun_end = fight.stuns[target.body]
        self.assertLess(fight.ana_sleeps[target.body]["end"], sleep_end)
        wake_end = fight.ana_sleeps[target.body]["end"]
        self.assertLess(wake_end, stun_end)
        self.advance(run, wake_end)
        self.assertTrue(fight.is_stunned(target))
        self.advance(run, stun_end)
        self.assertFalse(fight.is_stunned(target))

    def test_confirmed_shadow_immunity_rejects_actual_dart(self):
        fixture = control_tests.ReaperControlTests()
        run, caster, target = fixture.scene()
        origin = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[ana.HERO])
        caster.mover.position[:] = origin
        caster.mover.body = None
        for _ in range(20):
            run.step({})
        fixture.start_shadow(run, target)
        for _ in range(55):
            run.step({})
        before = self.health(run, target)
        self.sleep(run, caster, target)
        self.assertEqual(self.health(run, target), before)
        self.assertNotIn(target.body, run.match.combat.ana_sleeps)

    def test_actual_reflected_sleep_dart_resolves_shadow_immunity_boundary(self):
        fixture = control_tests.ReaperControlTests()
        for immune in (False, True):
            with self.subTest(immune=immune):
                run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene()
                fight = run.match.combat
                start = tuple(target.mover.position)
                if immune:
                    record = fixture.start_shadow(run, target)
                    for _ in range(55):
                        run.step({genji: (*guard, 0)})
                    self.reflect_dart(run, caster, genji, guard, heading)
                else:
                    self.reflect_dart(run, caster, genji, guard, heading)
                    record = fixture.start_shadow(run, target)
                destination = record["target"]
                for _ in range(30):
                    if not fight.rockets:
                        break
                    run.step({genji: (*guard, 0)})
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, target), 250 if immune else 245)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0 if immune else 5)
                self.assertEqual(target.body in fight.ana_sleeps, not immune)
                self.assertEqual(fixture.shadow(target).state(32).active, immune)
                cooldown = fixture.shadow(target).vars[61]
                if immune:
                    self.assertEqual(cooldown.value(), 0)
                else:
                    self.assertGreater(cooldown.value(), 9)
                for _ in range(110):
                    run.step({})
                position = tuple(target.mover.position)
                self.assertGreater(cooldown.value(), 7)
                if immune:
                    for actual, expected in zip(position, destination, strict=True):
                        self.assertAlmostEqual(actual, expected, delta=0.1)
                else:
                    self.assertEqual(position, start)

    def test_actual_reflected_sleep_dart_resolves_shadow_across_world_gap(self):
        fixture = control_tests.ReaperControlTests()
        for immune in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(immune=immune, skipped=skipped):
                    run, target, caster, genji, guard, heading = ana_tests.ReaperAnaTests().deflect_scene()
                    fight = run.match.combat
                    origin = tuple(target.mover.position)
                    if immune:
                        record = fixture.start_shadow(run, target)
                        for _ in range(55):
                            run.step({genji: (*guard, 0)})
                        dart = self.reflect_dart(run, caster, genji, guard, heading)
                    else:
                        dart = self.reflect_dart(run, caster, genji, guard, heading)
                        record = fixture.start_shadow(run, target)
                    self.assertIn(dart, fight.rockets)
                    self.assertIsNotNone(dart.last_tick)
                    self.assertEqual(reaper.intangible(target), immune)
                    until = run.frame + 120
                    while run.frame < until:
                        self.advance(run, until if skipped else run.frame + 1)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(reaper.intangible(target))
                    self.assertEqual(self.health(run, target), 250 if immune else 245)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0 if immune else 5)
                    sleep = fight.ana_sleeps.get(target.body)
                    if immune:
                        self.assertIsNone(sleep)
                        expected = record["target"]
                    else:
                        self.assertIsNotNone(sleep)
                        self.assertEqual(sleep["end"] - sleep["start"], 313)
                        expected = origin
                    position = tuple(target.mover.position)
                    for actual, desired in zip(position, expected, strict=True):
                        self.assertAlmostEqual(actual, desired, delta=0.1)
                    cooldown = fixture.shadow(target).vars[61].value()
                    self.assertGreater(cooldown, 7)
                    outcomes.append((sleep, position, cooldown))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_sleep_before_shadow_immunity_aborts_locked_cast_and_preserves_cooldown(self):
        fixture = control_tests.ReaperControlTests()
        run, caster, target = fixture.scene()
        origin = tuple(caster.mover.position)
        run.match.switch_hero(caster, heroes()[ana.HERO])
        caster.mover.position[:] = origin
        caster.mover.body = None
        for _ in range(20):
            run.step({})
        record = fixture.start_shadow(run, target)
        start = tuple(target.mover.position)
        self.sleep(run, caster, target)
        self.assertEqual(self.health(run, target), 245)
        self.assertTrue(run.match.combat.is_stunned(target))
        self.assertFalse(fixture.shadow(target).state(19).active)
        self.assertFalse(fixture.shadow(target).state(32).active)
        self.assertGreater(fixture.shadow(target).vars[61].value(), 9)
        for _ in range(150):
            run.step({})
        self.assertEqual(tuple(target.mover.position), start)
        self.assertNotEqual(tuple(target.mover.position), record["target"])
