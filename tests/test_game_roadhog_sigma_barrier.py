import math
import os
import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
from test_game_combat import aim

from ow174.game import combat, defenses, npc
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr, runtime


@unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_COLLISION"), "104319 Sigma collision input required")
class RoadhogSigmaBarrierTests(unittest.TestCase):
    step = phases.RoadhogDefenseTests.step
    health = phases.RoadhogDefenseTests.health

    def session(self, distance=8):
        run, hog, caster, rear = phases.RoadhogDefenseTests().session("Sigma", distance=distance)
        rear.mover.position[0] = 20
        rear.mover.body = None
        for _ in range(30):
            self.step(run, hog, caster)
        actor = run.match.combat.sigma_barriers.actors[caster.body]
        self.assertEqual(expr.to_entity(actor.source.component.entity_var(512).value()), actor.body)
        self.assertEqual(actor.health, 700)
        self.assertEqual(actor.speed, 16.5)
        self.assertFalse(actor.active())
        return run, hog, caster, actor

    def deploy(self, run, hog, caster, actor):
        for _ in range(15):
            self.step(run, hog, caster, cast=2)
        self.assertEqual(actor.mode, 2)
        self.step(run, hog, caster)
        self.assertEqual(actor.mode, 3)
        self.assertTrue(actor.active())
        return actor

    def test_projecting_mesh_cuts_only_crossing_live_hook_without_spending_health(self):
        for lateral, cuts in ((1.8, True), (10, False)):
            with self.subTest(cuts=cuts):
                self.check_projecting_mesh_live_hook(lateral, cuts)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_projecting_mesh_cuts_crossing_live_hook_on_each_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for lateral, cuts in ((1.8, True), (10, False)):
                with self.subTest(npc=definition.name, cuts=cuts):
                    self.check_projecting_mesh_live_hook(lateral, cuts, definition=definition, client=actual.NpcClient)

    def check_projecting_mesh_live_hook(self, lateral, cuts, *, definition=None, client=None):
        run, hog, caster, actor = self.session()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        rear = run.players[2]
        hog.team, caster.team, rear.team = 0, 1, 1
        y = hog.mover.position[1]
        caster.mover.position[:] = (lateral, y, 5)
        caster.mover.body = None
        if definition is None:
            victim = rear
            victim.mover.position[:] = (0, y, 17)
            victim.mover.body = None
        else:
            for player in run.players:
                player.client = client(player)
            victim = npc.spawn(run.match, definition, (0, y, 17), team=1)
        for _ in range(10):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is victim)
        entity = target.entity
        pool = fight.vitals_of(target)

        def depth():
            return (victim.mover.position if definition is None else victim.position)[2]

        before = pool.total
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        look = aim(fight._eye(hog), centre)
        run.step({hog: (*look, 8)})
        for _ in range(55):
            run.step({hog: (*look, 0)})
            if fight.hooks.controlled(entity):
                break
        self.assertTrue(fight.hooks.controlled(entity))
        self.assertEqual(pool.total, before - 30)
        self.assertFalse(actor.active())
        position = depth()
        for _ in range(8):
            run.step({caster: (-32768, 0, 2)})
            if not fight.hooks.controlled(entity):
                break
        self.assertTrue(actor.active())
        self.assertEqual(fight.hooks.controlled(entity), not cuts)
        current = next(t for t in fight._targets() if t.owner is victim)
        origin = fight._eye(hog)
        x, y, z = current.feet
        delta = (x - origin[0], y + current.height / 2 - origin[1], z - origin[2])
        length = math.hypot(*delta)
        direction = tuple(axis / length for axis in delta)
        contacts = fight._barrier_contacts(hog, origin, direction, length - current.radius)
        self.assertEqual(any(hit[1] is actor for hit in contacts), cuts)
        if cuts:
            position = depth()
        for _ in range(6):
            run.step({})
        if cuts:
            self.assertEqual(depth(), position)
        else:
            self.assertLess(depth(), position)
        if definition is not None and cuts:
            self.assertEqual(victim.velocity, (0, 0, 0))
        self.assertEqual(actor.health, 700)
        self.assertEqual(pool.total, before - 30)

    def test_authored_press_hold_release_recall_and_cooldown(self):
        run, hog, caster, actor = self.session()
        for _ in range(35):
            self.step(run, hog, caster, cast=2)
        self.assertEqual(actor.mode, 2)
        origin = actor.position
        for _ in range(6):
            run.step({caster: (0, 0, 2)})
        self.assertAlmostEqual(origin[2]-actor.position[2], 6*16.5*combat.TICK, places=4)
        self.step(run, hog, caster)
        settled = actor.position
        for _ in range(20):
            self.step(run, hog, caster)
        self.assertEqual(actor.mode, 3)
        self.assertEqual(actor.position, settled)
        self.step(run, hog, caster, cast=2)
        self.assertFalse(actor.active())
        self.assertGreater(actor.source.vars[61].value(), 1.9)
        self.step(run, hog, caster)
        for _ in range(30):
            self.step(run, hog, caster, cast=2)
        self.assertEqual(actor.mode, 1)
        for _ in range(130):
            self.step(run, hog, caster)
        self.assertEqual(actor.source.vars[61].value(), 0)
        self.deploy(run, hog, caster, actor)

    def test_primary_slug_fragments_hook_and_melee_intercept_actual_mesh(self):
        for button, distance in ((1, 8), (2, 8), (2, 16), (8, 8), (0x800, 8)):
            with self.subTest(button=button, distance=distance):
                run, hog, caster, actor = self.session(distance)
                self.deploy(run, hog, caster, actor)
                if button == 0x800:
                    hog.mover.position[2] = actor.position[2]-1.8
                    hog.mover.body = None
                    self.step(run, hog, caster)
                self.step(run, hog, caster, fire=button)
                for _ in range(48):
                    self.step(run, hog, caster)
                self.assertLess(actor.health, 700)
                self.assertEqual(self.health(run, caster), 20000)
                self.assertFalse(run.match.combat.hooks.controlled(caster.body))
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_actual_grasp_and_experimental_barrier_intercept_in_distance_order(self):
        for grasp_first in (False, True):
            with self.subTest(grasp_first=grasp_first):
                run, hog, caster, actor = self.session()
                self.deploy(run, hog, caster, actor)
                fight = run.match.combat
                if grasp_first:
                    caster.mover.position[2] = actor.position[2] - 2
                    caster.mover.body = None
                self.step(run, hog, caster, cast=8)
                for _ in range(6):
                    self.step(run, hog, caster)
                origin = fight._eye(hog)
                direction = (0, 0, 1)
                barrier = fight._barrier_contact(hog, origin, direction, 20)
                gate = defenses.projectile_gate(origin, direction, 20, fight._enemies(hog))
                self.assertIsNotNone(barrier)
                self.assertIsNotNone(gate)
                self.assertIs(barrier[1], actor)
                self.assertIs(gate[1].owner, caster)
                self.assertEqual(gate[0] < barrier[0], grasp_first)

                self.step(run, hog, caster, fire=2)
                for _ in range(45):
                    self.step(run, hog, caster)
                grasp = next(i for i in caster.body_script.component.instances.values()
                             if i.graph.index == 0x1D02)
                self.assertEqual(expr.to_float(grasp.find_var(0, 21312).value()), 50 if grasp_first else 0)
                self.assertEqual(actor.health, 700 if grasp_first else 650)
                self.assertEqual(self.health(run, caster), 20000)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_absorbed_scrap_converts_on_grasp_exit_and_the_extra_pool_expires(self):
        run, hog, caster, _ = self.session()
        fight = run.match.combat
        self.step(run, hog, caster, cast=8)
        for _ in range(6):
            self.step(run, hog, caster)
        self.step(run, hog, caster, fire=2)
        for _ in range(150):
            self.step(run, hog, caster)

        body = caster.body
        vitals = fight.vitals[body]
        extra = vitals.temporary_shields[defenses.SHIELD_TAG]
        self.assertAlmostEqual(extra.now, 30, places=5)
        self.assertEqual(extra.now, extra.most)
        self.assertAlmostEqual(vitals.total, 20030, places=5)
        self.assertEqual(caster.body_script.component.entity_var(1732).value(), vitals.total)
        slot = combat.SHIELDS_POOL * combat.POOLS
        parts = vitals.component(vitals.ids)[51]
        if combat.SHIELDS_POOL in vitals.ids:
            self.assertNotEqual(parts[slot], parts[slot + 1])
            slot += 1
        wire = struct.unpack("<IffI4B", parts[slot])
        self.assertEqual(wire[1:4], (extra.now, extra.most, combat.SHIELDS_POOL))

        base = dict(vitals.now)
        target = next(target for target in fight._enemies(hog) if target.entity == body)
        charge = hog.body_script.component.entity_var(342).value()
        fight._damage(hog, target, 10, False, None, run.frame)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), charge)
        self.assertEqual(vitals.ult_healing_deficit, 0)
        self.assertEqual(vitals.now, base)
        self.assertEqual(vitals.heal(10), 0)
        self.assertAlmostEqual(extra.now, 20, places=5)
        decay = fight.grasp_shields[body]
        run.frame = math.ceil(decay.starts / combat.TICK) - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(extra.now, 20, places=5)
        run.frame += 126
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(extra.now, 10, delta=0.081)
        run.frame += 300
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(extra.now, 0)
        self.assertNotIn(body, fight.grasp_shields)
        self.assertEqual(vitals.now, base)
        self.assertEqual(struct.unpack("<IffI4B", vitals.component(vitals.ids)[51][slot])[1:3], (0, 0))
        fight.retire_player_body(caster)
        self.assertNotIn(body, fight.grasp_grants)
        self.assertNotIn(body, fight.vitals)

    def test_authored_break_threshold_retires_mesh_at_one_health(self):
        run, hog, caster, actor = self.session()
        self.deploy(run, hog, caster, actor)
        shields = run.match.combat.sigma_barriers
        shields.damage(actor, 698)
        self.assertEqual(actor.health, 2)
        self.assertTrue(actor.active())
        shields.damage(actor, 1)
        self.assertEqual(actor.health, 1)
        self.assertFalse(actor.active())
        self.assertEqual(actor.source.vars[61].value(), 5)

    def test_recalled_pool_recovers_only_after_original_wait(self):
        run, hog, caster, actor = self.session()
        self.deploy(run, hog, caster, actor)
        self.step(run, hog, caster, fire=1)
        for _ in range(30):
            self.step(run, hog, caster)
        damaged = actor.health
        self.assertLess(damaged, 700)
        for _ in range(140):
            self.step(run, hog, caster)
        self.assertEqual(actor.health, damaged)
        self.step(run, hog, caster, cast=2)
        for _ in range(120):
            self.step(run, hog, caster)
        self.assertEqual(actor.health, damaged)
        for _ in range(30):
            self.step(run, hog, caster)
        before = actor.health
        self.assertGreater(before, damaged)
        for _ in range(10):
            self.step(run, hog, caster)
        self.assertAlmostEqual(actor.health-before, 12.8, places=2)
        self.assertEqual(actor.source.vars[586].value(), actor.health)

    def test_repair_agrees_across_a_gap_that_crosses_the_wait_deadline(self):
        samples = []
        for gap in (False, True):
            run, hog, caster, actor = self.session()
            self.deploy(run, hog, caster, actor)
            fight = run.match.combat
            fight.sigma_barriers.damage(actor, 250)
            for _ in range(20):
                self.step(run, hog, caster)
            self.step(run, hog, caster, cast=2)
            self.assertEqual(actor.mode, 1)
            if gap:
                tick = run.frame+170
                fight.update(tick*combat.TICK, tick, frames=False)
            else:
                for _ in range(170):
                    self.step(run, hog, caster)
            samples.append(actor.health)
        self.assertGreater(samples[0], 450)
        self.assertAlmostEqual(*samples, places=1)

    def test_held_projection_stops_at_world_and_release_stays_stationary(self):
        from test_game_mover import FLOOR, wall

        run, hog, caster, actor = self.session()
        run.match.collision_world = World(FLOOR + wall(-10, 4, 10, 4))
        for _ in range(60):
            self.step(run, hog, caster, cast=2)
        self.assertTrue(actor.blocked)
        self.assertEqual(actor.mode, 2)
        self.assertAlmostEqual(actor.position[2], 4.9, places=4)
        stopped = actor.position
        self.step(run, hog, caster)
        for _ in range(30):
            self.step(run, hog, caster)
        self.assertEqual(actor.mode, 3)
        self.assertEqual(actor.position, stopped)
        self.step(run, hog, caster, fire=1)
        for _ in range(30):
            self.step(run, hog, caster)
        self.assertEqual(actor.health, 700)
        self.assertEqual(self.health(run, caster), 20000)

    def test_whole_hog_consumes_pool_before_body_force_and_starts_break_cooldown(self):
        run, hog, caster, actor = self.session()
        self.deploy(run, hog, caster, actor)
        hog.mover.position[2] = actor.position[2]-2
        hog.mover.body = None
        self.step(run, hog, caster)
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, caster, fire=32)
        for _ in range(170):
            self.step(run, hog, caster)
            if actor.active():
                self.assertEqual(self.health(run, caster), 20000)
                self.assertNotIn(caster.body, run.match.combat.impulses.live)
            if self.health(run, caster) < 20000:
                break
        self.assertEqual(actor.health, 0)
        self.assertEqual(actor.mode, 1)
        self.assertGreater(actor.source.vars[61].value(), 4)
        self.assertLess(self.health(run, caster), 20000)
        for _ in range(40):
            self.step(run, hog, caster, cast=2)
        self.assertEqual(actor.mode, 1)
        self.assertFalse(actor.active())

    def test_whole_hog_resolves_live_barrier_and_grasp_by_contact_order(self):
        for barrier_first in (False, True):
            for separate in (False, True):
                with self.subTest(barrier_first=barrier_first, separate_casters=separate):
                    self.check_whole_hog_barrier_grasp_order(barrier_first, separate)

    def check_whole_hog_barrier_grasp_order(self, barrier_first, separate):
        run, hog, barrier_owner, shield = self.session()
        fight = run.match.combat
        self.deploy(run, hog, barrier_owner, shield)
        y = hog.mover.position[1]
        sigma = barrier_owner
        if separate:
            sigma = run.players[2]
            self.assertTrue(run.match.switch_hero(sigma, barrier_owner.hero))
            fight.vitals[sigma.body] = combat.Vitals(sigma.body, 20000)
            barrier_owner.mover.position[:] = (20, y, shield.position[2])
            barrier_owner.mover.body = None
        hog.mover.position[2] = shield.position[2] - 1
        hog.mover.body = None
        sigma.mover.position[:] = (0, y, shield.position[2] + (5.5 if barrier_first else 1.4))
        sigma.mover.body = None
        for _ in range(30):
            self.step(run, hog, sigma)
        self.step(run, hog, sigma, cast=8)
        for _ in range(6):
            self.step(run, hog, sigma)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.states[1].active)
        self.assertTrue(shield.active())
        fight.sigma_barriers.damage(shield, 694)
        self.assertEqual(shield.health, 6)
        vitals = fight.vitals[sigma.body]
        before = vitals.total
        base = dict(vitals.now)

        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        samples = []
        shoot = fight._shoot_pellet

        def record(*args, **kwargs):
            ray = args[5:7]
            mesh = fight._barrier_contact(hog, *ray, 20)
            live_gate = defenses.projectile_gate(*ray, 20, fight._enemies(hog))
            hits = (mesh[0] if mesh else None, live_gate[0] if live_gate else None)
            shoot(*args, **kwargs)
            samples.append((shield.health, grasp.vars[21312].value(), vitals.total, hits))

        with patch.object(fight, "_shoot_pellet", side_effect=record):
            self.step(run, hog, sigma, fire=32)
            for _ in range(90):
                if samples:
                    break
                self.step(run, hog, sigma)
        self.assertEqual(len(samples), 16)
        health, absorbed = samples[0][:2]
        mesh, gate_distance = samples[0][3]
        self.assertIsNotNone(mesh, samples)
        self.assertIsNotNone(gate_distance, samples)
        self.assertEqual(mesh < gate_distance, barrier_first)
        self.assertEqual(health, 0 if barrier_first else 6, samples)
        self.assertEqual(samples[-1][0], health)
        if barrier_first:
            self.assertEqual(absorbed, 0)
        else:
            self.assertGreater(absorbed, 0)
        self.assertGreater(samples[-1][1], absorbed)
        self.assertTrue(all(sample[2] == before for sample in samples))
        self.assertNotIn(sigma.body, fight.impulses.live)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(hog.client.messages(combat.HIT))

        for _ in range(180):
            self.step(run, hog, sigma)
            if sigma.body in fight.impulses.live:
                break
        self.assertFalse(grasp.states[1].active)
        self.assertFalse(shield.active())
        self.assertIn(sigma.body, fight.impulses.live)
        bonus = vitals.temporary_shields[defenses.SHIELD_TAG]
        self.assertGreater(bonus.now, 0)
        self.assertLess(bonus.now, 400)
        self.assertEqual(vitals.now, base)
        ultimate, = script.ultimates
        self.assertTrue(ultimate.flags & 1)

    def test_ally_can_shoot_through_mesh_and_death_retires_pet(self):
        run, hog, caster, actor = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = caster.team = actor.team = 0
        rear = run.players[2]
        rear.team = 1
        rear.mover.position[:] = (0, hog.mover.position[1], 9)
        rear.mover.body = None
        self.deploy(run, hog, caster, actor)
        self.step(run, hog, caster, fire=1)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertLess(self.health(run, rear), 20000)
        self.assertEqual(actor.health, 700)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is caster)
        fight._damage(rear, target, 50000, False, None, run.frame)
        run.step({})
        self.assertNotIn(caster.body, fight.sigma_barriers.actors)
        self.assertFalse(actor.active())

    def test_actor_vm_export_reads_stored_pool_and_mode(self):
        run, hog, caster, actor = self.session()
        self.deploy(run, hog, caster, actor)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("evaluation during export")):
            before = snapshot_digest(export_component_vm(actor.component))
            self.assertEqual(snapshot_digest(export_component_vm(actor.component)), before)
        run.match.combat.sigma_barriers.damage(actor, 30)
        self.assertNotEqual(snapshot_digest(export_component_vm(actor.component)), before)

    def test_server_snapshot_tracks_deployed_pet_pose_without_evaluation(self):
        import test_server_snapshot as stored

        from ow174.game.server_snapshot import snapshot_digest as server_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        caster = players[0]
        session.apply("switch_hero", {"account": caster.account_lo, "hero": 0x02E000000000023B})
        fight = caster.match.combat
        for offset in range(78):
            frame = caster.body_script.frame+1
            caster.match.tick = fight.tick = frame
            fight.now = frame*combat.TICK
            caster.body_script.command(frame, 2 if 35 <= offset < 69 else 0)
            fight.sigma_barriers.update(frame, combat.TICK)
        actor = fight.sigma_barriers.actors[caster.body]
        self.assertEqual(actor.mode, 3)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("evaluation during export")):
            before = server_digest(case.capture(session))
            self.assertEqual(server_digest(case.capture(session)), before)
            x, y, z = actor.position
            actor.position = x+.125, y, z
            self.assertNotEqual(server_digest(case.capture(session)), before)

    def layered_meshes(self, *, sigma_x=2.2):
        run, hog, sigma, near = self.session()
        orisa = run.players[2]
        run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
        y = hog.mover.position[1]
        sigma.mover.position[:] = (sigma_x, y, 8)
        orisa.mover.position[:] = (0, y, 10)
        sigma.mover.body = orisa.mover.body = None
        for _ in range(35):
            run.step({})
        run.step({sigma: (-32768, 0, 2), orisa: (-32768, 8192, 16)})
        for _ in range(14):
            run.step({sigma: (-32768, 0, 2), orisa: (-32768, 8192, 0)})
        run.step({})
        for _ in range(20):
            run.step({})
        far = run.match.combat.orisa_barriers.actors[orisa.body]
        self.assertTrue(near.active())
        self.assertTrue(far.active())
        return run, hog, sigma, near, orisa, far

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION"), "104319 Orisa collision input required")
    def test_two_deployed_meshes_use_nearest_contact_then_recall_exposes_rear_mesh(self):
        run, hog, sigma, near, _, far = self.layered_meshes()
        run.step({hog: (0, 0, 1)})
        for _ in range(50):
            run.step({})
        self.assertLess(near.health, 700)
        self.assertEqual(far.health, 600)
        run.step({sigma: (-32768, 0, 2)})
        self.assertFalse(near.active())
        run.step({hog: (0, 0, 1)})
        for _ in range(40):
            run.step({})
        self.assertLess(far.health, 600)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION"), "104319 Orisa collision input required")
    def test_whole_hog_one_volley_breaks_near_then_far_mesh_before_body(self):
        run, hog, sigma, near, orisa, far = self.layered_meshes(sigma_x=0)
        sigma.mover.position[0] = 20
        hog.mover.position[2] = near.position[2] - 1
        orisa.mover.position[2] = far.position[2] + 1
        sigma.mover.body = hog.mover.body = orisa.mover.body = None
        for _ in range(10):
            run.step({})
        fight = run.match.combat
        self.assertTrue(near.active(), near.mode)
        fight.sigma_barriers.damage(near, 694)
        fight.orisa_barriers.damage(far, 599)
        before = self.health(run, orisa)
        self.assertEqual((near.health, far.health), (6, 1))
        self.assertTrue(near.active(), near.mode)
        self.assertTrue(far.active())
        pellet = fight._shoot_pellet
        samples = []

        def observe(*args, **kwargs):
            pellet(*args, **kwargs)
            samples.append((near.health, far.health, self.health(run, orisa),
                            orisa.body in fight.impulses.live))

        hog.body_script.set_ult_charge(2240)
        x, y, z = orisa.mover.position
        angles = aim(fight._eye(hog), (x, y + 1.25, z))
        with patch.object(fight, "_shoot_pellet", side_effect=observe):
            run.step({hog: (*angles, 32)})
            for _ in range(90):
                if samples:
                    break
                run.step({hog: (*angles, 0)})
        self.assertEqual(len(samples), 16)
        self.assertEqual(samples[0], (0, 1, before, False), samples)
        self.assertEqual(samples[1], (0, 0, before, False), samples)
        self.assertLess(samples[-1][2], before)
        self.assertTrue(samples[-1][3])
        self.assertFalse(near.active())
        self.assertFalse(far.active())

    def test_whole_hog_breaks_mesh_then_projected_barrier_before_body_force(self):
        from test_game_roadhog_zarya import RoadhogZaryaTests

        run, hog, sigma, mesh = self.session()
        match = run.match
        fight = match.combat
        zarya = run.players[2]
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, sigma.team, zarya.team, mesh.team = 0, 1, 1, 1
        x, y, z = sigma.mover.position
        zarya.mover.position[:] = (x - 4, y, z)
        zarya.mover.body = None
        self.deploy(run, hog, sigma, mesh)
        hog.mover.position[2] = mesh.position[2] - 1
        hog.mover.body = None
        for _ in range(25):
            run.step({})
        cast_aim = aim(fight._eye(zarya), fight._eye(sigma))
        run.step({zarya: (*cast_aim, 0)})
        bubble = RoadhogZaryaTests().cast(run, hog, zarya, sigma, cast_aim, projected=True)
        fight.sigma_barriers.damage(mesh, 694)
        bubble.health = 1
        self.assertTrue(mesh.active())

        energy = zarya.body_script.component.entity_var(87)
        before_energy = expr.to_float(energy.value())
        before_health = self.health(run, sigma)
        samples = []
        shoot_pellet = fight._shoot_pellet

        def observe(*args, **kwargs):
            shoot_pellet(*args, **kwargs)
            samples.append((mesh.health, bubble.health, self.health(run, sigma),
                            sigma.body in fight.impulses.live))

        look = aim(fight._eye(hog), (x, y + 1.25, z))
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        with patch.object(fight, "_shoot_pellet", side_effect=observe):
            run.step({hog: (*look, 32)})
            for _ in range(90):
                if samples:
                    break
                run.step({hog: (*look, 0)})

        self.assertEqual(len(samples), 16)
        self.assertEqual(samples[:2], [(0, 1, before_health, False), (0, 0, before_health, False)])
        self.assertLess(samples[-1][2], before_health)
        self.assertTrue(samples[-1][3])
        self.assertGreater(fight.impulses.velocity(sigma.body)[2], 0)
        self.assertEqual(len(fight.impulses.seen), 1)
        self.assertFalse(mesh.active())
        self.assertIsNone(fight.zarya_bubbles.for_target(sigma))
        self.assertAlmostEqual(expr.to_float(energy.value()) - before_energy, 0.2, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_five_npc_targets_are_protected_by_the_deployed_mesh(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                with self.subTest(npc=definition.name):
                    run, hog, caster, actor = self.session()
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, caster.team, actor.team = 0, 1, 1
                    self.deploy(run, hog, caster, actor)
                    bot = npc.spawn(run.match, definition, (1, hog.mover.position[1], 9), team=1)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    before = fight.vitals_of(target).total
                    for _ in range(3):
                        run.step({})
                    angles = aim(fight._eye(hog), (1, target.feet[1]+1, 9))
                    run.step({hog: (*angles, 1)})
                    for _ in range(40):
                        run.step({hog: (*angles, 0)})
                    self.assertLess(actor.health, 700)
                    self.assertEqual(fight.vitals_of(target).total, before)
                    self.assertEqual(self.health(run, caster), 20000)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_four_manual_npc_guns_hit_mesh_before_allied_roadhog(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc, npc_control

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                if definition.name == "Assassin":
                    continue
                with self.subTest(npc=definition.name):
                    run, hog, caster, actor = self.session()
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = caster.team = actor.team = 0
                    caster.mover.position[0] = 2.4
                    caster.mover.body = None
                    for player in run.players:
                        player.client = actual.NpcClient(player)
                    self.deploy(run, hog, caster, actor)
                    npc.spawn(run.match, definition, (0, hog.mover.position[1], 12), team=1)
                    for _ in range(15):
                        run.step({})
                    hog.mover.yaw = hog.mover.pitch = 0
                    npc_control.start(run.match, hog)
                    for _ in range(280):
                        run.step({})
                        if actor.health < 700:
                            break
                    self.assertLess(actor.health, 700)
                    self.assertEqual(self.health(run, hog), 600)
                    npc_control.stop(run.match)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()
