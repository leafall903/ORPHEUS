"""Mei's real 104319 commands against Sigma Barrier and Kinetic Grasp."""

import math
import os
import unittest
from unittest import mock

import test_mei_combat as fixtures
import test_mei_wall as walls
import test_server_snapshot as snapshots
from test_game_combat import aim
from test_game_server import pick

from ow174.game import combat, replay_manifest, sigma_barrier, sigma_grasp
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script import expr, runtime
from ow174.game.server_snapshot import snapshot_digest


def scene(test, distance):
    fixture = fixtures.MeiCombatTests()
    run, mei, sigma, _ = fixture.duel()
    fight = run.match.combat
    test.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_barrier.HERO]))
    x, y, z = mei.mover.position
    fight.walls.place_player(sigma, (x, y, z + distance))
    for _ in range(30):
        run.step({})
    target = next(t for t in fight._targets() if t.owner is sigma)
    return fixture, run, mei, sigma, fight, target


@unittest.skipUnless(
    os.environ.get('ORPHEUS_SIGMA_BARRIER_MODEL'), 'external 104319 Sigma C015 mesh required',
)
class MeiSigmaBarrierTests(unittest.TestCase):
    def arena(self, park=True):
        fixture, run, mei, sigma, fight, target = scene(self, 8.)
        for _ in range(12):
            run.step({sigma: (-32768, 0, SECONDARY)})
        actor, = fight.sigma_barriers.actors.values()
        self.assertEqual(actor.phase, 2)
        if park:
            for frame in range(53):
                buttons = SECONDARY if frame < 8 else 0
                run.step({sigma: (-32768, 0, buttons)})
            self.assertEqual(actor.phase, 3)
        return fixture, run, mei, sigma, fight, target, actor

    def test_held_gap_then_parked_interception_and_recall(self):
        fixture, run, mei, sigma, fight, target, actor = self.arena(park=False)
        component = sigma.body_script.component
        self.assertEqual(component.entity_var(512).value(), expr.Entity(actor.entity))
        self.assertTrue(actor.child_request_seen)
        self.assertEqual((actor.vitals.total, len(actor.geometry[1])), (700, 120))
        before = actor.position
        for _ in range(8):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(actor.phase, 2)
        self.assertAlmostEqual(math.dist(before, actor.position), 8 * combat.TICK * 16.5)
        for _ in range(45):
            run.step({sigma: (-32768, 0, 0)})
        parked = actor.position
        self.assertEqual(actor.phase, 3)
        pool = fight.vitals_of(target)
        fixture.fire(run, mei, target, SECONDARY, .9)
        self.assertEqual((actor.position, actor.vitals.total, pool.total), (parked, 625., 400.))
        fixture.fire(run, mei, target, FIRE, .3)
        self.assertEqual((actor.vitals.total, pool.total), (611.25, 400.))
        self.assertIsNone(fight.frost(sigma))
        run.step({sigma: (-32768, 0, SECONDARY)})
        run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(actor.phase, 1)
        self.assertFalse(actor.active)
        fixture.fire(run, mei, target, SECONDARY, .9)
        self.assertEqual(pool.total, 325.)
        for _ in range(200):
            run.step({})
        self.assertEqual(actor.vitals.total, 700.)
        self.assertTrue(run.match.switch_hero(sigma, mei.hero))
        self.assertFalse(fight.sigma_barriers.actors)

    def test_breaking_icicle_does_not_reach_owner(self):
        fixture, run, mei, _sigma, fight, target, actor = self.arena()
        pool = fight.vitals_of(target)
        for count in range(1, 11):
            fixture.fire(run, mei, target, SECONDARY, .9, frames=60)
            self.assertEqual((actor.vitals.total, pool.total), (max(0, 700 - 75 * count), 400))
        self.assertFalse(actor.active)
        self.assertEqual(actor.phase, 1)
        self.assertIsNotNone(actor.recharge_at)
        fixture.fire(run, mei, target, SECONDARY, .9, frames=60)
        self.assertEqual(pool.total, 325.)

    def test_ice_wall_priority_then_visible_mesh_takes_melee(self):
        fixture, run, mei, _sigma, fight, target, actor = self.arena()
        x, y, z = mei.mover.position
        distance, _ = fight.sigma_barriers.trace(mei, fight._eye(mei), (0., 0., 1.), 7.)
        wall = walls.WallTests()
        pieces, angle = wall.place(run, mei, distance=1.5)
        self.assertEqual(len(pieces), 5)
        fixture.fire(run, mei, target, SECONDARY, .9)
        self.assertEqual(actor.vitals.total, 700.)
        wall.tap(run, mei, 16, angle)
        fight.walls.place_player(mei, (x, y, z + distance - 1.35))
        for _ in range(18):
            run.step({})
        fixture.fire(run, mei, target, 0x800, .9, frames=32)
        self.assertEqual((actor.vitals.total, fight.vitals_of(target).total), (670., 400.))

    def test_landed_blizzard_exposes_owner_across_parked_mesh(self):
        _, run, mei, sigma, fight, target, actor = self.arena()
        x, y, z = mei.mover.position
        angle = aim(fight._eye(mei), (x, y, z + 1.))
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        for frame in range(240):
            run.step({mei: (*angle, 32 if frame == 0 else 0)})
            if fight.frozen(sigma):
                break
        area, = fight.blizzard.areas.values()
        origin = (area.position[0], area.position[1] + .5, area.position[2])
        destination = fight._eye(sigma)
        length = math.dist(origin, destination)
        direction = tuple((b - a) / length for a, b in zip(origin, destination, strict=True))
        self.assertIsNotNone(actor.intersection(origin, direction, length))
        self.assertTrue(fight.frozen(sigma))
        self.assertLess(fight.vitals_of(target).total, 400.)
        self.assertEqual(actor.vitals.total, 700.)

    def test_active_pet_pose_is_in_passive_capture_and_mesh_in_replay_identity(self):
        fixture = snapshots.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        owner, = players
        peers[0].message(21616, pick(owner.entity, sigma_barrier.HERO), now=100.8)
        session.tick(100.960)
        owner.take_commands([Command(owner.body_script.frame + 1, buttons=SECONDARY)])
        session.tick(101.120)
        actor, = owner.match.combat.sigma_barriers.actors.values()
        self.assertTrue(actor.child_request_seen)
        config = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        self.assertEqual(config["ow174.game.sigma_barrier.model_sha256"], actor.geometry[0])
        with (
            mock.patch.object(runtime.Component, 'run_now', side_effect=AssertionError('VM evaluated')),
            mock.patch.object(runtime.Var, 'value', side_effect=AssertionError('variable evaluated')),
        ):
            before = snapshot_digest(fixture.capture(session))
            actor.position = tuple(point + 1 for point in actor.position)
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)


class MeiSigmaGraspTests(unittest.TestCase):
    def arena(self, distance=5.):
        _, run, mei, sigma, fight, target = scene(self, distance)
        pool = fight.vitals_of(target)
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        angle = aim(fight._eye(mei), (target.feet[0], target.feet[1] + .9, target.feet[2]))
        return run, mei, sigma, fight, pool, instance, angle

    def test_frontal_icicle_absorption_grants_then_decays_only_temporary_shields(self):
        run, mei, sigma, fight, pool, instance, angle = self.arena()
        charge = mei.body_script.component.entity_var(338).value()
        for frame in range(44):
            run.step({sigma: (-32768, 0, 8 if frame == 0 else 0),
                      mei: (*angle, SECONDARY if frame < 20 else 0)})
        self.assertTrue(instance.states[1].active)
        self.assertEqual((pool.total, instance.vars[21312].value()), (400., 75.))
        self.assertFalse(mei.client.messages(combat.HIT))
        self.assertLess(mei.body_script.component.entity_var(338).value() - charge, 5.)
        for _ in range(108):
            run.step({})
        temporary = pool.temporary_shields[sigma_grasp.SHIELD_TAG]
        self.assertAlmostEqual(temporary.now, 45., places=4)
        until = fight.grasp_shields[sigma.body].starts
        while fight.now + .016 < until:
            run.step({})
        self.assertAlmostEqual(temporary.now, 45., places=4)
        for _ in range(40):
            run.step({})
        self.assertAlmostEqual(temporary.now, 45 - 5 * (fight.now - until), places=4)
        self.assertEqual(pool.now[combat.SHIELDS_POOL], 100.)

    def test_rear_icicle_spray_and_nearer_cover(self):
        for yaw, fire, cover, expected in (
            (0, SECONDARY, False, 325.),
            (-32768, FIRE, False, 380.75),
            (-32768, SECONDARY, True, 400.),
        ):
            with self.subTest(yaw=yaw, fire=fire, cover=cover):
                run, mei, sigma, _fight, pool, instance, angle = self.arena()
                if cover:
                    fixtures.static_wall(run, mei, distance=2.)
                for frame in range(44):
                    run.step({sigma: (yaw, 0, 8 if frame == 0 else 0),
                              mei: (*angle, fire if frame < 20 else 0)})
                self.assertTrue(instance.states[1].active)
                self.assertEqual((pool.total, instance.vars[21312].value()), (expected, 0))

    def test_quick_melee_hits_through_active_grasp_without_generating_shields(self):
        run, mei, sigma, _fight, pool, instance, angle = self.arena(distance=2.)
        run.step({sigma: (-32768, 0, 8)})
        self.assertTrue(instance.states[1].active)
        run.step({mei: (*angle, 0x800)})
        self.assertTrue(instance.states[1].active)
        self.assertEqual((pool.total, instance.vars[21312].value()), (370., 0.))
        self.assertEqual(len(mei.client.messages(combat.HIT)), 1)
        for _ in range(130):
            run.step({})
        self.assertFalse(instance.states[1].active)
        self.assertNotIn(sigma_grasp.SHIELD_TAG, pool.temporary_shields)

    def test_airborne_blizzard_is_consumed_without_a_landed_area_or_shield_grant(self):
        run, mei, sigma, fight, pool, instance, _ = self.arena()
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        with mock.patch.object(sigma_grasp, 'absorbed', wraps=sigma_grasp.absorbed) as absorb:
            for frame in range(135):
                run.step({sigma: (-32768, 0, 8 if frame == 0 else 0),
                          mei: (0, 0, 32 if frame == 0 else 0)})
        absorb.assert_called_once_with(instance.states[1], 0.)
        self.assertFalse(fight.blizzard.flights or fight.blizzard.areas)
        self.assertEqual((pool.total, instance.vars[21312].value()), (400., 0))
        self.assertFalse(pool.temporary_shields)



class MeiSigmaIncomingTests(unittest.TestCase):
    def test_accretion_interrupts_icicle_windup_and_preserves_impact_momentum(self):
        fixture, run, mei, sigma, fight, _ = scene(self, 5.)
        target = next(t for t in fight._targets() if t.owner is mei)
        vitals = fight.vitals_of(target)
        x, y, z = mei.mover.position
        rock_aim = aim(fight._eye(sigma), (x, y + .9, z))
        icicle_aim = aim(fight._eye(mei), (x, y + .9, z + 5.))
        controlled = 0
        for frame in range(115):
            run.step({sigma: (*rock_aim, 16 if frame == 0 else 0),
                      mei: (*icicle_aim, SECONDARY if 36 <= frame <= 65 else 0)})
            controlled += fight.is_stunned(mei)
            self.assertFalse(any(rocket.shooter is mei for rocket in fight.rockets))
        self.assertEqual((vitals.total, controlled), (180., 50))
        self.assertLess(mei.mover.position[2], z - 1.)
        sigma_target = next(t for t in fight._targets() if t.owner is sigma)
        self.assertEqual(fight.vitals_of(sigma_target).total, 400.)
        fixture.fire(run, mei, sigma_target, SECONDARY, .9)
        self.assertEqual(fight.vitals_of(sigma_target).total, 325.)

    def test_accretion_splash_displaces_without_direct_hit_control(self):
        _, run, mei, sigma, fight, _ = scene(self, 5.)
        target = next(t for t in fight._targets() if t.owner is mei)
        x, y, z = mei.mover.position
        rock_aim = aim(fight._eye(sigma), (x + .9, y - .6, z))
        for frame in range(115):
            run.step({sigma: (*rock_aim, 16 if frame == 0 else 0)})
            self.assertFalse(fight.is_stunned(mei))
        self.assertEqual(fight.vitals_of(target).total, 210.)
        self.assertLess(mei.mover.position[0], x - .1)

    def test_accretion_is_stopped_by_cryo_ice_wall_and_terrain(self):
        for mode in ('cryo', 'ice', 'static'):
            with self.subTest(mode=mode):
                _, run, mei, sigma, fight, _ = scene(self, 5.)
                target = next(t for t in fight._targets() if t.owner is mei)
                x, y, z = mei.mover.position
                rock_aim = aim(fight._eye(sigma), (x, y + .9, z))
                if mode == 'ice':
                    pieces, _ = walls.WallTests().place(run, mei, distance=2.)
                if mode == 'static':
                    fixtures.static_wall(run, sigma, distance=-2.)
                for frame in range(115):
                    run.step({sigma: (*rock_aim, 16 if frame == 0 else 0),
                              mei: (0, 0, 8 if mode == 'cryo' and frame == 16 else 0)})
                    self.assertFalse(fight.is_stunned(mei))
                self.assertEqual(fight.vitals_of(target).total, 250.)
                self.assertAlmostEqual(mei.mover.position[2], z)
                if mode == 'ice':
                    self.assertLess(min(piece.health for piece in pieces), 400.)

    def test_hypersphere_pair_hits_ricochets_and_airbursts_once(self):
        from test_game_combat import KANEZAKA_DM, Run
        from test_game_mover import box

        from ow174.game.collision import World

        for mode, offset, distance, health, hits in (
            ('direct', 0., 5., 140., 4),
            ('airburst', 1., 22., 190., 2),
            ('ricochet', 0., -2., 140., 4),
            ('cryo', 0., 5., 250., 0),
            ('ice', 0., 5., 250., 12),
        ):
            with self.subTest(mode=mode):
                run = Run(KANEZAKA_DM, ('Sigma', 'Mei'))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                sigma, mei = run.players
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_barrier.HERO]))
                self.assertTrue(run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
                x, y, z = sigma.mover.position
                fight = run.match.combat
                fight.walls.place_player(mei, (x + offset, y, z + distance))
                if mode == 'ricochet':
                    floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                             (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
                    collision = World(floor + box(x - 4, x + 4, y, y + 4, z + 6, z + 7))
                    run.match.collision_world = collision
                    for actor in run.players:
                        actor.mover.use(world=collision)
                for _ in range(25):
                    run.step({})
                if mode == 'ice':
                    pieces, _ = walls.WallTests().place(run, mei, distance=-1.5)
                target = next(t for t in fight._targets() if t.owner is mei)
                bounces = set()
                for frame in range(78):
                    run.step({sigma: (0, 0, FIRE if frame == 0 else 0),
                              mei: (0, 0, 8 if mode == 'cryo' and frame == 0 else 0)})
                    for rocket in fight.rockets:
                        bounced = rocket.sphere is not None and rocket.sphere.bounces == 99
                        if bounced and rocket.direction[2] < 0:
                            bounces.add(rocket.definition)
                self.assertEqual(fight.vitals_of(target).total, health)
                self.assertEqual(len(sigma.client.messages(combat.HIT)), hits)
                self.assertEqual(len(bounces), 2 if mode == 'ricochet' else 0)
                self.assertFalse(fight.rockets)
                if mode == 'ice':
                    self.assertLess(min(piece.health for piece in pieces), 400.)


@unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "external Sigma mesh required")
class MeiSigmaCooldownTests(unittest.TestCase):
    def test_barrier_cooldown_refuses_early_input_then_allows_fresh_redeployment(self):
        fixture, run, mei, sigma, fight, target, actor = MeiSigmaBarrierTests().arena()
        run.step({sigma: (-32768, 0, SECONDARY)})
        run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(actor.phase, 1)
        cooldown = actor.instance.states[111]
        self.assertGreater(cooldown.remaining(), 1.9)
        for frame in range(45):
            run.step({sigma: (-32768, 0, SECONDARY if frame % 2 == 0 else 0)})
            self.assertFalse(actor.active)
        fixture.fire(run, mei, target, SECONDARY, .9)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 325.)
        for _ in range(125):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(cooldown.remaining(), 0.)
        for frame in range(30):
            run.step({sigma: (-32768, 0, SECONDARY if frame < 20 else 0)})
        self.assertTrue(actor.active)
        self.assertEqual(actor.phase, 3)
        fixture.fire(run, mei, target, SECONDARY, .9)
        self.assertEqual((pool.total, actor.vitals.total), (325., 625.))

