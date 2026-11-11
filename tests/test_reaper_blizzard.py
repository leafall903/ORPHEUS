"""Actual Blizzard throw, area lifecycle and Reaper interactions."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_reaper_control as control_tests
import test_reaper_frost as frost_tests
from test_game_combat import aim

from ow174.game import combat, reaper, sigma_grasp
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ReaperBlizzardTests(unittest.TestCase):
    def scene(self, **kwargs):
        run, owner, caster, pool = frost_tests.ReaperFrostTests().scene(**kwargs)
        caster.body_script.ult_seconds = 0
        return run, owner, caster, pool

    def throw(self, run, caster, *, button=32):
        x, y, z = caster.mover.position
        yaw, pitch = aim(run.match.combat._eye(caster), (x, y, z - 5))
        for frame in range(32):
            run.step({caster: (yaw, pitch, button if frame == 0 else 0)})

    def cast(self, run, caster):
        script = caster.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1610)
        script.set_ult_charge(cost)
        self.throw(run, caster)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        manager = run.match.combat.blizzard
        flight, = manager.flights
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        self.assertEqual(flight.left, 60)
        for _ in range(120):
            run.step({})
            if manager.areas:
                break
        area, = manager.areas.values()
        self.assertFalse(manager.flights)
        self.assertEqual(area.starts - run.frame * 16, 200)
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertIsNotNone(flight.shot)
        self.assertIsNotNone(flight.shot.release_at)
        return area

    def until(self, run, milliseconds):
        while run.frame * 16 < milliseconds:
            run.step({})

    def test_actual_charge_refusal_then_area_damage_freeze_and_expiry(self):
        run, owner, caster, pool = self.scene()
        manager = run.match.combat.blizzard
        caster.body_script.set_ult_charge(1609)
        self.throw(run, caster)
        self.assertFalse(manager.flights)
        self.assertFalse(manager.areas)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 1609)
        area = self.cast(run, caster)
        self.assertEqual(pool.total, 250)
        self.until(run, area.starts + 50)
        fight = run.match.combat
        self.assertLess(pool.total, 250)
        self.assertAlmostEqual(fight.frost(owner)[0], 0.5)
        self.until(run, area.starts + 2500)
        self.assertFalse(fight.frozen(owner))
        self.until(run, area.starts + 2650)
        sample = fight.frost(owner)
        self.assertTrue(fight.frozen(owner))
        self.assertEqual(sample[2] - sample[1], 1300)
        self.until(run, area.ends)
        self.assertFalse(manager.areas)
        self.assertAlmostEqual(pool.total, 250 - 85)
        self.until(run, area.ends + 1100)
        self.assertAlmostEqual(pool.total, 165)
        self.assertFalse(fight.frozen(owner))
        self.assertIsNone(fight.frost(owner))

    def test_actual_wraith_cleans_blizzard_and_area_reacquires_after_immunity(self):
        run, owner, caster, pool = self.scene()
        area = self.cast(run, caster)
        fight = run.match.combat
        self.until(run, area.starts + 90)
        self.assertAlmostEqual(fight.frost(owner)[0], 0.5)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertIsNone(fight.frost(owner))
        before = pool.total
        for _ in range(120):
            run.step({})
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(pool.total, before)
        self.assertNotIn(owner.body, area.frost_due)
        self.until(run, area.starts + 3900)
        self.assertFalse(reaper.intangible(owner))
        self.assertLess(pool.total, before)
        self.assertLess(fight.frost(owner)[0], 0.7)

    def test_actual_blizzard_keeps_shadow_immunity_and_hit_frames_across_skips(self):
        for airborne in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(airborne=airborne, skipped=skipped):
                    run, owner, caster, pool = self.scene()
                    fight = run.match.combat
                    manager = fight.blizzard
                    if airborne:
                        caster.body_script.set_ult_charge(1610)
                        self.throw(run, caster)
                        self.assertEqual(len(manager.flights), 1)
                    else:
                        area = self.cast(run, caster)
                        self.until(run, area.starts + 32)
                    angles = (16384, 5000)
                    record = control_tests.ReaperControlTests().start_shadow(run, owner, angles=angles)
                    self.assertFalse(reaper.intangible(owner))
                    self.assertEqual(bool(manager.flights), airborne)
                    before = pool.total
                    notices = len(caster.client.messages(combat.HIT))
                    end = run.frame + 235
                    immune = set()
                    if skipped:
                        run.frame = end
                        run.match.update(end * combat.TICK, end, frames=False)
                    else:
                        affect = manager.affect

                        def observe(area, now, *, owner=owner, immune=immune, fight=fight, affect=affect):
                            if reaper.intangible(owner):
                                immune.add(fight.tick)
                            affect(area, now)

                        with patch.object(manager, "affect", side_effect=observe):
                            while run.frame < end:
                                run.step({owner: (*angles, 0)})

                    self.assertTrue(record["moved"])
                    self.assertFalse(reaper.intangible(owner))
                    self.assertLess(pool.total, before)
                    frames = tuple(hit["+0x84"] for hit in caster.client.messages(combat.HIT)[notices:])
                    if not skipped:
                        self.assertTrue(immune)
                        self.assertTrue(immune.isdisjoint(frames))
                        if not airborne:
                            self.assertLess(frames[0], min(immune))
                        self.assertGreater(frames[-1], max(immune))
                    area, = manager.areas.values()
                    outcomes.append((pool.total, frames, fight.frost(owner), tuple(owner.mover.position),
                                     area.position, area.starts, area.frost_due[owner.body]))
            if len(outcomes) == 2:
                with self.subTest(airborne=airborne):
                    self.assertAlmostEqual(outcomes[0][0], outcomes[1][0], places=5)
                    self.assertEqual(outcomes[0][1:], outcomes[1][1:])

    def test_actual_area_freeze_interrupts_blossom_without_late_pulses(self):
        run, owner, caster, _ = self.scene()
        area = self.cast(run, caster)
        fight = run.match.combat
        self.until(run, area.starts + 2290)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        channel = fight.reaper_blossoms[owner.body]
        self.assertFalse(channel["ended"])
        self.until(run, area.starts + 2650)
        self.assertTrue(fight.frozen(owner))
        self.assertTrue(channel["ended"])
        self.assertFalse(channel["targets"])
        before = fight.vitals[caster.body].total
        self.until(run, area.ends + 1500)
        self.assertEqual(fight.vitals[caster.body].total, before)
        self.assertLess(owner.body_script.component.vars[342].value(), 30)

    def test_actual_area_uses_range_wall_and_faction_without_retroactive_damage(self):
        run, owner, caster, pool = self.scene()
        area = self.cast(run, caster)
        x, y, z = area.position
        owner.mover.position[:] = (x + 12, y, z)
        owner.mover.body = None
        self.until(run, area.starts + 500)
        self.assertEqual(pool.total, 250)
        original = run.match.collision_world
        wall = x + 2
        run.match.collision_world = World([
            (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
            (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
            (wall,y,z-5,wall,y+5,z-5,wall,y+5,z+5),
            (wall,y,z-5,wall,y+5,z+5,wall,y,z+5),
        ], name="Blizzard area cover")
        owner.mover.position[:] = (x + 4, y, z)
        owner.mover.body = None
        self.until(run, area.starts + 1000)
        self.assertEqual(pool.total, 250)
        fight = run.match.combat
        self.assertIsNone(fight.frost(owner))
        run.match.collision_world = original
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = caster.team
        self.until(run, area.starts + 1500)
        self.assertEqual(pool.total, 250)
        owner.team = 1 - caster.team
        run.step({})
        self.assertAlmostEqual(pool.total, 249.68)
        self.assertAlmostEqual(fight.frost(owner)[0], 0.5)

    def grasp_scene(self):
        run, owner, caster, pool = self.scene(names=("Reaper", "Mei", "Sigma"), distance=6)
        sigma = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = sigma.team = 0
        caster.team = 1
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        x, y, z = caster.mover.position
        sigma.mover.position[:] = (x, y, z - 3)
        sigma.mover.body = None
        for _ in range(30):
            run.step({})
        return run, owner, caster, sigma, pool

    def test_actual_sigma_grasp_absorbs_throw_before_reaper_area(self):
        run, owner, caster, sigma, pool = self.grasp_scene()
        fight = run.match.combat
        run.step({sigma: (0, 0, 8)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.throw(run, caster)
        flight, = fight.blizzard.flights
        self.assertTrue(grasp.states[1].active)
        for _ in range(130):
            run.step({})
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        self.assertLess(flight.flown, 0.3)
        self.assertEqual(pool.total, 250)
        self.assertIsNone(fight.frost(owner))
        self.assertEqual(grasp.vars[21312].value(), 0)

    def test_actual_blizzard_grasp_expiry_preserves_landing_across_skips(self):
        run, _, caster, sigma, _ = self.grasp_scene()
        manager = run.match.combat.blizzard
        run.step({sigma: (0, 0, 8)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        state = grasp.states[1]
        self.assertEqual(grasp.vars[567].value(), 2)
        caster.body_script.set_ult_charge(1610)
        self.throw(run, caster)
        for _ in range(110):
            if not manager.flights:
                break
            run.step({})
        self.assertFalse(manager.flights)
        self.assertFalse(manager.areas)
        contact = run.frame
        for _ in range(150):
            if not state.active:
                break
            run.step({})
        self.assertAlmostEqual(state.ended_at - state.start, 2000, delta=1)
        expiry = state.ended_at // grasp.component.frame_ms + 1
        lead = expiry - contact
        self.assertGreater(lead, 1)

        for absorbed in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(absorbed=absorbed, skipped=skipped):
                    run, owner, caster, sigma, pool = self.grasp_scene()
                    fight = run.match.combat
                    manager = fight.blizzard
                    run.step({sigma: (0, 0, 8)})
                    for _ in range(lead - int(absorbed)):
                        run.step({})
                    caster.body_script.set_ult_charge(1610)
                    self.throw(run, caster)
                    flight, = manager.flights
                    grasp = next(i for i in sigma.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertTrue(grasp.states[1].active)
                    end = run.frame + 95
                    if skipped:
                        run.frame = end
                        run.match.update(end * combat.TICK, end, frames=False)
                    else:
                        while run.frame < end:
                            run.step({})
                    self.assertFalse(grasp.states[1].active)
                    self.assertFalse(manager.flights)
                    self.assertEqual(grasp.vars[21312].value(), 0)
                    self.assertNotIn(sigma.body, fight.grasp_shields)
                    self.assertEqual(bool(manager.areas), not absorbed)
                    if absorbed:
                        self.assertEqual(pool.total, 250)
                        self.assertIsNone(fight.frost(owner))
                    else:
                        area, = manager.areas.values()
                        self.assertIs(area.owner, caster)
                        self.assertLess(pool.total, 250)
                        self.assertIsNotNone(fight.frost(owner))
                    areas = tuple((area.position, area.starts) for area in manager.areas.values())
                    hits = tuple(hit["+0x84"] for hit in caster.client.messages(combat.HIT))
                    outcomes.append((flight.flown, areas, hits, pool.total, fight.frost(owner)))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_reaper_kill_leaves_landed_blizzard_active(self):
        run, owner, caster, pool = self.scene()
        area = self.cast(run, caster)
        fight = run.match.combat
        enemy = fight.vitals[caster.body]
        enemy.damage(enemy.total - 50)
        reaper_tests.ReaperTests().fire(run, owner, caster)
        self.assertTrue(enemy.dead)
        self.assertEqual(enemy.killer, owner.body)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.until(run, area.starts + 800)
        self.assertLess(pool.total, 250)
        self.assertEqual(fight.vitals[owner.body].killer, None)

    def test_actual_area_freeze_interrupts_shadow_cast_with_cooldown(self):
        run, owner, caster, _ = self.scene()
        area = self.cast(run, caster)
        fight = run.match.combat
        self.until(run, area.starts + 2280)
        fixture = control_tests.ReaperControlTests()
        record = fixture.start_shadow(run, owner)
        self.until(run, area.starts + 2650)
        self.assertTrue(fight.frozen(owner))
        shadow = fixture.shadow(owner)
        self.assertFalse(shadow.state(19).active)
        self.assertFalse(shadow.state(32).active)
        self.assertGreater(shadow.vars[61].value(), 9)
        self.until(run, area.ends)
        self.assertNotEqual(tuple(owner.mover.position), record["target"])

    def deflect_scene(self):
        run, owner, caster, pool = self.scene(names=("Reaper", "Mei", "Genji"), distance=6)
        genji = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = caster.team = 0
        genji.team = 1
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        x, y, z = caster.mover.position
        genji.mover.position[:] = (x, y, z - 3)
        genji.mover.body = None
        owner.mover.position[:] = (x + 2, y, z + 2)
        owner.mover.body = None
        for _ in range(30):
            run.step({})
        return run, owner, caster, genji, pool

    def test_actual_genji_reflects_throw_and_reassigns_blizzard_to_enemy(self):
        run, owner, caster, genji, pool = self.deflect_scene()
        fight = run.match.combat
        run.step({genji: (0, 0, 16)})
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.throw(run, caster)
        flight, = fight.blizzard.flights
        for _ in range(110):
            run.step({})
            if fight.blizzard.areas:
                break
        area, = fight.blizzard.areas.values()
        self.assertIs(flight.owner, genji)
        self.assertIs(area.owner, genji)
        self.assertEqual(area.body, genji.body)
        self.until(run, area.starts + 500)
        self.assertLess(pool.total, 250)
        self.assertIsNotNone(fight.frost(owner))

    def test_actual_blizzard_deflect_expiry_preserves_ownership_across_skips(self):
        run, _, caster, genji, _ = self.deflect_scene()
        run.step({genji: (0, 0, 16)})
        instance = next(i for i in genji.body_script.component.instances.values()
                        if i.graph.index == 0x03A3)
        wait = instance.states[1]
        timeout = wait.evaluate(wait.node.field("m_timeout"), watch=False)
        self.assertEqual(timeout, 2)
        expiry = int((wait.start + timeout * 1000) // instance.component.frame_ms) + 1
        caster.body_script.set_ult_charge(1610)
        self.throw(run, caster)
        flight, = run.match.combat.blizzard.flights
        for _ in range(110):
            if flight.owner is not caster or flight not in run.match.combat.blizzard.flights:
                break
            run.step({})
        self.assertIs(flight.owner, genji)
        lead = expiry - run.frame
        self.assertGreater(lead, 1)

        for reflected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(reflected=reflected, skipped=skipped):
                    run, owner, caster, genji, pool = self.deflect_scene()
                    fight = run.match.combat
                    manager = fight.blizzard
                    run.step({genji: (0, 0, 16)})
                    for _ in range(lead - int(reflected)):
                        run.step({})
                    caster.body_script.set_ult_charge(1610)
                    self.throw(run, caster)
                    flight, = manager.flights
                    state = next(i.states[10] for i in genji.body_script.component.instances.values()
                                 if i.graph.index == 0x03A3)
                    self.assertTrue(state.active)
                    end = run.frame + 95
                    if skipped:
                        run.frame = end
                        run.match.update(end * combat.TICK, end, frames=False)
                    else:
                        while run.frame < end:
                            run.step({})
                    area, = manager.areas.values()
                    self.assertFalse(state.active)
                    source = genji if reflected else caster
                    self.assertIs(flight.owner, source)
                    self.assertIs(area.owner, source)
                    self.assertEqual(area.body, source.body)
                    if reflected:
                        self.assertLess(pool.total, 250)
                        self.assertIsNotNone(fight.frost(owner))
                    else:
                        self.assertEqual(pool.total, 250)
                        self.assertIsNone(fight.frost(owner))
                    hits = tuple(hit["+0x84"] for hit in source.client.messages(combat.HIT))
                    outcomes.append((area.position, area.starts, hits, pool.total, fight.frost(owner)))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def matrix_scene(self, *, matrix_distance=3, extra=()):
        from ow174.game import dva_matrix, mei_blizzard, pools

        if not dva_matrix.eligible(mei_blizzard.DEFINITION):
            self.skipTest("requires Blizzard's pinned projectile tags")
        run, owner, caster, pool = self.scene(names=("Reaper", "Mei", "D.Va", *extra), distance=6)
        mech = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = mech.team = 0
        caster.team = 1
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[pools.DVA_HERO], body=pools.DVA_MECH)))
        x, y, z = caster.mover.position
        mech.mover.position[:] = (x, y, z - matrix_distance)
        mech.mover.body = None
        for _ in range(100):
            run.step({})
        return run, owner, caster, mech, pool

    def test_actual_defense_matrix_consumes_tagged_throw_before_area(self):
        from ow174.game import dva_matrix

        run, owner, caster, mech, pool = self.matrix_scene()
        fight = run.match.combat
        x, y, z = caster.mover.position
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        yaw, pitch = aim(fight._eye(caster), (x, y, z - 5))
        for frame in range(70):
            run.step({caster: (yaw, pitch, 32 if frame == 0 else 0),
                      mech: (0, 0, 2 if frame >= 20 else 0)})
        matrix = next(i for i in mech.body_script.component.instances.values()
                      if i.graph.index == dva_matrix.GRAPH)
        self.assertTrue(matrix.states[dva_matrix.STATE].active)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        self.assertEqual(pool.total, 250)
        self.assertIsNone(fight.frost(owner))

    def test_actual_blizzard_matrix_releases_during_missing_input(self):
        from ow174.game import dva_matrix

        outcomes = []
        for mode in ("held", "released", "skipped"):
            with self.subTest(mode=mode):
                run, owner, caster, mech, pool = self.matrix_scene(matrix_distance=11.5)
                fight = run.match.combat
                for _ in range(30):
                    run.step({mech: (0, 0, 2)})
                caster.body_script.set_ult_charge(1610)
                x, y, z = caster.mover.position
                yaw, pitch = aim(fight._eye(caster), (x, y, z - 5))
                for frame in range(32):
                    run.step({mech: (0, 0, 2), caster: (yaw, pitch, 32 if frame == 0 else 0)})
                flight, = fight.blizzard.flights
                matrix = next(i for i in mech.body_script.component.instances.values()
                              if i.graph.index == dva_matrix.GRAPH)
                self.assertTrue(matrix.states[dva_matrix.STATE].active)
                end = run.frame + 95
                if mode == "skipped":
                    run.frame = end
                    run.match.update(end * combat.TICK, end, frames=False)
                else:
                    while run.frame < end:
                        held = mode == "held" and bool(fight.blizzard.flights)
                        run.step({mech: (0, 0, 2)} if held else {})
                self.assertFalse(fight.blizzard.flights)
                if mode == "held":
                    self.assertGreater(flight.flown, 0)
                    self.assertFalse(fight.blizzard.areas)
                    self.assertEqual(pool.total, 250)
                    self.assertIsNone(fight.frost(owner))
                    continue
                self.assertEqual(len(fight.blizzard.areas), 1)
                area, = fight.blizzard.areas.values()
                self.assertIs(area.owner, caster)
                self.assertFalse(matrix.states[dva_matrix.STATE].active)
                self.assertLess(pool.total, 250)
                self.assertIsNotNone(fight.frost(owner))
                hits = tuple(hit["+0x84"] for hit in caster.client.messages(combat.HIT))
                outcomes.append((flight.flown, area.position, area.starts,
                                 hits, pool.total, fight.frost(owner)))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_blizzard_passes_destroyed_matrix_before_and_after_pilot_replacement(self):
        from ow174.game import dva_matrix, pools

        for replace_body in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(replace_body=replace_body, skipped=skipped):
                    run, owner, caster, mech, pool = self.matrix_scene(
                        matrix_distance=11.5, extra=("Enemy Reaper",),
                    )
                    attacker = run.players[-1]
                    self.assertTrue(run.match.switch_hero(attacker, heroes()[reaper.HERO]))
                    attacker.team = caster.team
                    x, y, z = mech.mover.position
                    attacker.mover.position[:] = (x, y, z - 1.5)
                    attacker.mover.body = None
                    fight = run.match.combat
                    for _ in range(30):
                        run.step({mech: (0, 0, 2)})
                    caster.body_script.set_ult_charge(1610)
                    x, y, z = caster.mover.position
                    throw = aim(fight._eye(caster), (x, y, z - 5))
                    for frame in range(32):
                        run.step({mech: (0, 0, 2), caster: (*throw, 32 if frame == 0 else 0)})
                    flight, = fight.blizzard.flights
                    old_body, old_script = mech.body, mech.body_script
                    matrix = next(i for i in old_script.component.instances.values()
                                  if i.graph.index == dva_matrix.GRAPH)
                    self.assertTrue(matrix.states[dva_matrix.STATE].active)
                    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is mech))
                    vitals.damage(vitals.total - 20)
                    x, y, z = mech.mover.position
                    shot = aim(fight._eye(attacker), (x, y + 1, z))

                    run.step({attacker: (*shot, 1), mech: (0, 0, 2)})

                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, attacker.body)
                    self.assertIn(flight, fight.blizzard.flights)
                    if replace_body:
                        pilot = heroes()[pools.DVA_HERO]
                        self.assertTrue(run.match.switch_hero(mech, pilot, preserve_pose=True))
                        self.assertTrue(old_script.retired)
                        self.assertNotEqual(mech.body, old_body)
                        self.assertNotEqual(mech.hero.body, pools.DVA_MECH)
                        self.assertIsNone(mech.body_script.world_frame_anchor)
                    end = run.frame + 95
                    if skipped:
                        run.frame = end
                        run.match.update(end * combat.TICK, end, frames=False)
                    else:
                        while run.frame < end:
                            run.step({})
                    self.assertFalse(fight.blizzard.flights)
                    area, = fight.blizzard.areas.values()
                    self.assertIs(area.owner, caster)
                    self.assertLess(pool.total, 250)
                    self.assertIsNotNone(fight.frost(owner))
                    hits = tuple(hit["+0x84"] for hit in caster.client.messages(combat.HIT)
                                 if hit["+0x78"]["+0x0"]["+0x0"] == owner.body)
                    self.assertTrue(hits)
                    outcomes.append((flight.flown, area.position, area.starts,
                                     hits, pool.total, fight.frost(owner)))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])
