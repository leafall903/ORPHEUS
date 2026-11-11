"""Zarya attacks against the actual Sigma Experimental Barrier input path."""

import os
import unittest

from test_game_combat import Run, aim

from ow174.game import combat, sigma_barrier
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires verified external Sigma C015")
class ZaryaSigmaBarrierTests(unittest.TestCase):
    def arena(self, *, hold=8, allied=False):
        run = Run(names=("Sigma", "Zarya", "Target"))
        sigma, zarya, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_barrier.HERO]))
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        sigma.team, zarya.team, target.team = 0, 0 if allied else 1, 1 if allied else 0
        sigma.mover.position[:] = [0, 1, 0]
        zarya.mover.position[:] = [0, 1, 10]
        target.mover.position[:] = [0 if allied else 10, 1, -3]
        for frame in range(50):
            run.step({sigma: (0, 0, 2 if frame < hold else 0)})
        fight = run.match.combat
        for body in fight._targets():
            fight.vitals_of(body)
        actor, = fight.reinhardt_barriers.experimental.values()
        self.assertEqual((actor.phase, actor.active, actor.vitals.total), (3, True, 700))
        self.assertEqual(len(actor.geometry), 120)
        self.assertEqual(actor.instance.component.entity_var(512).value(), expr.Entity(actor.entity))
        return run, fight, sigma, zarya, target, actor

    def test_beam_hits_enemy_barrier_and_recall_exposes_sigma(self):
        run, fight, sigma, zarya, _, actor = self.arena()
        angles = aim(fight._eye(zarya), (0, 1.8, 0))
        for _ in range(31):
            run.step({zarya: (*angles, 1)})
        self.assertAlmostEqual(actor.vitals.total, 664)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertFalse(zarya.client.messages(combat.HIT))
        run.step({sigma: (0, 0, 2)})
        run.step({})
        self.assertFalse(actor.active)
        for _ in range(31):
            run.step({zarya: (*angles, 1)})
        self.assertLess(fight.vitals[sigma.body].total, 400)

    def test_sparse_grenade_uses_experimental_barrier_until_recall_admission(self):
        for delay in (2, 25):
            for sparse in (False, True):
                with self.subTest(delay=delay, sparse=sparse):
                    run, fight, sigma, zarya, _, actor = self.arena()
                    match = run.match
                    angles = aim(fight._eye(zarya), (0, 1.8, 0))
                    run.step({zarya: (*angles, 2)})
                    rocket, = fight.rockets
                    admission = run.frame + delay
                    endpoint = run.frame + 45
                    if not sparse:
                        for tick in range(run.frame + 1, admission + 1):
                            match.update(tick * combat.TICK, tick, frames=False)
                    match.tick = admission
                    sigma.take_commands([Command(admission, buttons=2)])
                    ticks = (endpoint,) if sparse else range(admission + 1, endpoint + 1)
                    for tick in ticks:
                        match.update(tick * combat.TICK, tick, frames=False)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertFalse(actor.active)
                    self.assertEqual(actor.vitals.total, 700 if delay == 2 else 653)
                    self.assertEqual(fight.vitals[sigma.body].total, 353 if delay == 2 else 400)
                    self.assertAlmostEqual(actor.recharge_at, (admission + 1) * combat.TICK + 2)

    def test_sparse_grenade_cannot_hit_a_late_experimental_barrier_redeployment(self):
        for delay in (1, 30):
            positions = []
            for sparse in (False, True):
                with self.subTest(delay=delay, sparse=sparse):
                    run, fight, sigma, zarya, _, actor = self.arena()
                    match = run.match
                    run.step({sigma: (0, 0, 2)})
                    run.step({})
                    self.assertFalse(actor.active)
                    run.frame += 150
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    angles = aim(fight._eye(zarya), (0, 1.8, 0))
                    run.step({zarya: (*angles, 2)})
                    rocket, = fight.rockets
                    admission = run.frame + delay
                    endpoint = run.frame + 45
                    sigma_position = tuple(sigma.mover.position)
                    for tick in range(run.frame + 1, endpoint + 1):
                        if not sparse or tick == endpoint:
                            match.update(tick * combat.TICK, tick, frames=False)
                        if admission <= tick <= admission + 8:
                            match.tick = tick
                            sigma.take_commands([Command(tick, buttons=2 if tick < admission + 8 else 0)])
                            # Isolate shield travel from body motion after a grenade hit.
                            sigma.mover.position[:] = sigma_position
                            sigma.mover.body = None
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual((actor.active, actor.phase), (True, 3))
                    self.assertEqual(actor.vitals.total, 653 if delay == 1 else 700)
                    self.assertEqual(fight.vitals[sigma.body].total, 400 if delay == 1 else 353)
                    positions.append(actor.position)
            if len(positions) == 2:
                for paced, caught_up in zip(*positions, strict=True):
                    self.assertAlmostEqual(caught_up, paced, msg=f"delay={delay}, positions={positions}")

    def test_particle_grenade_detonates_on_the_barrier_mesh(self):
        run, fight, sigma, zarya, _, actor = self.arena()
        angles = aim(fight._eye(zarya), (0, 1.8, 0))
        run.step({zarya: (*angles, 2)})
        for _ in range(55):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(actor.vitals.total, 653)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertFalse(zarya.client.messages(combat.HIT))

    def test_ally_beam_passes_barrier_and_hits_enemy(self):
        run, fight, sigma, zarya, target, actor = self.arena(allied=True)
        angles = aim(fight._eye(zarya), (0, 1.8, -3))
        for _ in range(31):
            run.step({zarya: (*angles, 1)})
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertAlmostEqual(fight.vitals[target.body].total, 164)

    def test_breaking_particle_grenade_keeps_barrier_cover_for_that_explosion(self):
        run, fight, sigma, zarya, _, actor = self.arena(hold=2)
        fight.reinhardt_barriers.damage(actor, 670)
        self.assertEqual(actor.vitals.total, 30)
        angles = aim(fight._eye(zarya), (0, 1.8, 0))
        run.step({zarya: (*angles, 2)})
        for _ in range(45):
            run.step({})
        self.assertFalse(actor.active)
        self.assertEqual(actor.vitals.total, 0)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertFalse(zarya.client.messages(combat.HIT))
        self.assertIsInstance(export_body_vm(sigma.body_script), dict)

    def test_graviton_hits_enemy_mesh_then_field_pulls_sigma_through_it(self):
        run, fight, sigma, zarya, _, actor = self.arena()
        start = tuple(sigma.mover.position)
        field = self.fire_graviton(run, fight, zarya, (0, 1.8, 0))
        self.assertEqual(actor.vitals.total, 695)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertGreater(field.position[2], actor.position[2])
        self.assertIsNotNone(actor.intersection(field.position, (0, 0, -1), 0.05))
        self.assertEqual(field.end_tick - field.last_tick, 250)
        for _ in range(32):
            run.step({})
        self.assertTrue(actor.active)
        self.assertGreater(sigma.mover.position[2] - start[2], 1)
        self.assertAlmostEqual(fight.vitals[sigma.body].total, 400 - 32 * 0.08)
        while fight.tick < field.end_tick:
            run.step({})
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertAlmostEqual(fight.vitals[sigma.body].total, 380)
        self.assertEqual(actor.vitals.total, 695)

    def test_graviton_projectile_crosses_allied_barrier(self):
        run, fight, sigma, zarya, target, actor = self.arena(allied=True)
        field = self.fire_graviton(run, fight, zarya, (0, 2.55, -3))
        self.assertLess(field.position[2], 0)
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(fight.vitals[target.body].total, 195)
        self.assertEqual(fight.vitals[sigma.body].total, 400)

    def test_replacing_sigma_body_retires_deployed_barrier(self):
        run, fight, sigma, zarya, _, actor = self.arena()
        old_body = sigma.body
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_barrier.HERO]))
        self.assertNotEqual(sigma.body, old_body)
        run.step({})
        self.assertNotIn(actor.entity, fight.reinhardt_barriers.experimental)
        self.assertIsNone(fight.reinhardt_barriers.trace(zarya, fight._eye(zarya), (0, 0, -1), 10))
        for frame in range(50):
            run.step({sigma: (0, 0, 2 if frame < 8 else 0)})
        replacement, = fight.reinhardt_barriers.experimental.values()
        self.assertEqual(replacement.owner_body, sigma.body)
        self.assertNotEqual(replacement.entity, actor.entity)
        self.assertTrue(replacement.active)

    def test_recalled_barrier_cooldown_and_recharge_after_zarya_damage(self):
        run, fight, sigma, zarya, _, actor = self.arena()
        angles = aim(fight._eye(zarya), (0, 1.8, 0))
        run.step({zarya: (*angles, 2)})
        for _ in range(150):
            run.step({})
        self.assertEqual(actor.vitals.total, 653)
        run.step({sigma: (0, 0, 2)})
        run.step({})
        self.assertFalse(actor.active)
        self.assertAlmostEqual(actor.recharge_at - fight.now, 2)
        self.assertGreater(actor.instance.states[111].remaining(), 1.9)
        run.step({sigma: (0, 0, 2)})
        run.step({})
        self.assertFalse(actor.active)
        for _ in range(118):
            run.step({})
        self.assertEqual(actor.vitals.total, 653)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(actor.vitals.total, 685)
        for _ in range(25):
            run.step({})
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(actor.instance.states[111].remaining(), 0)
        for frame in range(30):
            run.step({sigma: (0, 0, 2 if frame < 20 else 0)})
        self.assertTrue(actor.active)
        self.assertEqual(actor.vitals.total, 700)

    def fire_graviton(self, run, fight, zarya, point):
        component = zarya.body_script.component
        zarya.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
        angles = aim(fight._eye(zarya), point)
        run.step({zarya: (*angles, 0x20)})
        for _ in range(100):
            run.step({})
            if fight.zarya_gravitons.fields:
                field, = fight.zarya_gravitons.fields
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
                return field
        self.fail("actual ultimate input never deployed its field")

    def test_quick_melee_resolves_mesh_and_body_independently(self):
        for case in ("both", "mesh", "ally", "wall", "miss"):
            with self.subTest(case=case):
                run, fight, sigma, zarya, target, actor = self.arena(hold=2, allied=case == "ally")
                zarya.mover.position[:] = [0, 1, 3.4 if case == "mesh" else 2.4]
                zarya.mover.body = None
                if case == "ally":
                    target.mover.position[:] = [0, 1, 0.3]
                    target.mover.body = None
                if case == "wall":
                    run.match.collision_world = World([
                        (-10, 0, 2, 10, 0, 2, 10, 5, 2),
                        (-10, 0, 2, 10, 5, 2, -10, 5, 2),
                        (-10, 1, -10, -10, 1, 10, 10, 1, 10),
                        (-10, 1, -10, 10, 1, 10, 10, 1, -10),
                    ])
                run.step({})
                angles = (16384, 0) if case == "miss" else aim(fight._eye(zarya), (0, 2.4, 0))
                for frame in range(30):
                    run.step({zarya: (*angles, 0x800 if frame == 0 else 0)})
                self.assertEqual(actor.vitals.total, 670 if case in ("both", "mesh") else 700)
                self.assertEqual(fight.vitals[sigma.body].total, 370 if case == "both" else 400)
                self.assertEqual(fight.vitals[target.body].total, 170 if case == "ally" else 200)

    def test_experimental_barrier_stops_earthshatter_before_zarya_even_on_break(self):
        for case in ("enemy", "break", "ally", "adjacent", "adjacent break", "in front"):
            with self.subTest(case=case):
                run = Run(names=("Reinhardt", "Zarya", "Sigma"))
                rein, zarya, sigma = run.players
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                match.collision_world = flat_world(1)
                self.assertTrue(match.switch_hero(rein, heroes()[0x02E0000000000007]))
                self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                self.assertTrue(match.switch_hero(sigma, heroes()[sigma_barrier.HERO]))
                rein.team, zarya.team, sigma.team = 0, 1, 0 if case == "ally" else 1
                for player, z in ((rein, 0), (zarya, 9), (sigma, 10)):
                    player.mover.position[:] = [0, 1, z]
                    player.mover.body = None
                for frame in range(50):
                    run.step({sigma: (-32768, 4096, 2 if frame < 28 else 0)})
                fight = match.combat
                actor, = fight.reinhardt_barriers.experimental.values()
                self.assertTrue(actor.active)
                contact = actor.intersection((0, 1.1, 0), (0, 0, 1), 8)
                self.assertIsNotNone(contact)
                if case.startswith("adjacent") or case == "in front":
                    side = -0.1 if case == "in front" else 0.1
                    zarya.mover.position[2] = contact + side
                    zarya.mover.body = None
                breaking = case.endswith("break")
                if breaking:
                    fight.reinhardt_barriers.damage(actor, 675)
                cost = rein.body_script.component.entity_var(ULT_COST).value()
                rein.body_script.set_ult_charge(cost)
                run.step({rein: (0, 0, 0x20)})
                for _ in range(70):
                    run.step({})
                self.assertEqual(actor.vitals.total, 0 if breaking else 700 if case == "ally" else 650)
                target = next(t for t in fight._targets() if t.owner is zarya)
                exposed = case in ("ally", "in front")
                self.assertEqual(fight.vitals_of(target).total, 350 if exposed else 400)
                self.assertEqual(fight.is_stunned(zarya), exposed)
