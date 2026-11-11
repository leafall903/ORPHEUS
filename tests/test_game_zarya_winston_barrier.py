"""Actual Zarya attacks and Winston Barrier Projector deployment."""

import math
import unittest

import test_game_zarya_npcs as fixtures
import test_server_snapshot as snapshot_fixtures
from test_game_combat import Run, aim
from test_game_zarya_tesla import health, setup
from test_game_zarya_winston_jump import move

from ow174.game import combat, npc, winston_barrier, winston_jump
from ow174.game.commands import Command
from ow174.game.script import expr
from ow174.game.server_snapshot import snapshot_digest


def arena(*, allied=False, receiver_z=4):
    run = Run(names=("Winston", "Zarya", "Receiver"))
    winston, zarya, receiver = setup(run)
    winston.team = 0 if allied else 1
    zarya.team, receiver.team = 0, 1
    move(zarya, (0, 1, 8))
    move(receiver, (0, 1, receiver_z))
    return run, winston, zarya, receiver


def deploy(run, winston):
    fight = run.match.combat
    service = fight.reinhardt_barriers
    run.step({winston: (0, 0, 16)})
    instance = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0x00FA)
    if service.winston:
        raise AssertionError("dome deployed before the authored wait")
    for _ in range(5):
        run.step({})
        if service.winston:
            break
    actor, = service.winston.values()
    if instance.find_var(0, 38).value() != expr.Entity(actor.entity):
        raise AssertionError("deployment did not bind the authored output")
    move(winston, (20, 1, 0))
    return actor, instance


def cast(run, zarya, point, *, ultimate=False):
    fight = run.match.combat
    angles = aim(fight._eye(zarya), point)
    if ultimate:
        zarya.body_script.set_ult_charge(2100)
    run.step({zarya: (*angles, 32 if ultimate else 2)})
    rocket, = fight.rockets
    for _ in range(80):
        run.step({})
        if rocket not in fight.rockets:
            break
    return rocket


class ZaryaWinstonBarrierTests(unittest.TestCase):
    def test_pending_dome_releases_during_owner_silence_on_original_clock(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                run, winston, _, _ = arena()
                fight = run.match.combat
                service = fight.reinhardt_barriers
                run.step({winston: (0, 0, 16)})
                start = run.frame
                self.assertFalse(service.winston)
                if not sparse:
                    for tick in range(start + 1, start + 4):
                        run.match.update(tick * combat.TICK, tick, frames=False)
                        self.assertFalse(service.winston)
                run.frame = start + (30 if sparse else 4)
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                actor, = service.winston.values()
                self.assertAlmostEqual(actor.expires, (start + 3) * combat.TICK + 9)
                self.assertEqual(actor.health, 700)
                self.assertEqual(winston.body_script.frame, start + 3)
                run.step({})
                self.assertIs(service.winston[actor.entity], actor)

                # A fresh press cannot replace a live dome during authored cooldown.
                run.step({winston: (0, 0, 16)})
                run.step({})
                self.assertEqual(list(service.winston), [actor.entity])
                run.frame = math.ceil(actor.expires / combat.TICK)
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(service.winston)
                run.frame = start + math.ceil(13 / combat.TICK) + 8
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                replacement, _ = deploy(run, winston)
                self.assertNotEqual(replacement.entity, actor.entity)
                self.assertEqual(replacement.health, 700)

    def test_pending_dome_preserves_pre_and_post_release_projectile_order_in_gaps(self):
        for before_release in (False, True):
            for sparse in (False, True):
                with self.subTest(before_release=before_release, sparse=sparse):
                    run, winston, zarya, receiver = arena()
                    fight = run.match.combat
                    move(zarya, (0, 1, 5.6 if before_release else 8))
                    angles = aim(fight._eye(zarya), fight._eye(receiver))
                    before = health(fight, receiver)
                    run.step({winston: (0, 0, 16), zarya: (*angles, 2)})
                    launch = run.frame
                    self.assertEqual(len(fight.rockets), 1)
                    run.match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                    self.assertEqual(len(fight.rockets), 1)
                    ticks = (launch + 25,) if sparse else range(launch + 2, launch + 26)
                    for tick in ticks:
                        run.match.update(tick * combat.TICK, tick, frames=False)
                    actor, = fight.reinhardt_barriers.winston.values()
                    self.assertFalse(fight.rockets)
                    self.assertAlmostEqual(700 - actor.health, 0 if before_release else 47)
                    self.assertAlmostEqual(before - health(fight, receiver), 47 if before_release else 0)

    def test_dome_cast_admitted_during_gap_does_not_protect_earlier_contact(self):
        for delay in (1, 18):
            with self.subTest(delay=delay):
                run, winston, zarya, receiver = arena()
                match = run.match
                fight = match.combat
                service = fight.reinhardt_barriers
                before = health(fight, receiver)
                angles = aim(fight._eye(zarya), fight._eye(receiver))
                run.step({zarya: (*angles, 2)})
                launch = run.frame
                rocket, = fight.rockets
                admission = launch + delay
                match.tick = admission
                winston.take_commands([Command(admission + 500, buttons=16)])
                self.assertFalse(service.winston)
                release = service.winston_pending[winston.body]
                endpoint = launch + 35
                match.update(endpoint * combat.TICK, endpoint, frames=False)
                actor, = service.winston.values()
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(actor.health, 653 if delay == 1 else 700)
                self.assertEqual(before - health(fight, receiver), 0 if delay == 1 else 47)
                self.assertEqual(release.tick, admission + 3)
                self.assertAlmostEqual(actor.expires, release.tick * combat.TICK + 9)

    def test_dome_release_command_does_not_backdate_shell_contact_or_lifetime(self):
        for before_release in (False, True):
            with self.subTest(before_release=before_release):
                run, winston, zarya, receiver = arena()
                match = run.match
                fight = match.combat
                service = fight.reinhardt_barriers
                move(zarya, (0, 1, 5.6 if before_release else 8))
                angles = aim(fight._eye(zarya), fight._eye(receiver))
                before = health(fight, receiver)
                run.step({winston: (0, 0, 16), zarya: (*angles, 2)})
                release = service.winston_pending[winston.body]
                match.tick = release.tick
                winston.take_commands([Command(release.frame)])
                actor, = service.winston.values()
                self.assertLess(fight.tick, release.tick)
                endpoint = release.tick + 25
                match.update(endpoint * combat.TICK, endpoint, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual(actor.health, 700 if before_release else 653)
                self.assertEqual(before - health(fight, receiver), 47 if before_release else 0)
                self.assertAlmostEqual(actor.expires, release.tick * combat.TICK + 9)

    def test_dome_expiry_preserves_inflight_grenade_order_in_sparse_updates(self):
        for before_expiry in (False, True):
            with self.subTest(before_expiry=before_expiry):
                run, winston, zarya, receiver = arena()
                fight = run.match.combat
                actor, _ = deploy(run, winston)
                move(zarya, (0, 1, 5.6 if before_expiry else 8))
                expiry_tick = math.ceil(actor.expires / combat.TICK)
                run.frame = expiry_tick - 5
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                angles = aim(fight._eye(zarya), fight._eye(receiver))
                before = health(fight, receiver)
                run.step({zarya: (*angles, 2)})
                launch = run.frame
                self.assertEqual(len(fight.rockets), 1)
                run.match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                self.assertEqual(len(fight.rockets), 1)
                run.match.update((launch + 25) * combat.TICK, launch + 25, frames=False)
                self.assertFalse(fight.rockets)
                self.assertNotIn(actor.entity, fight.reinhardt_barriers.winston)
                self.assertAlmostEqual(700 - actor.health, 47 if before_expiry else 0)
                self.assertAlmostEqual(before - health(fight, receiver), 0 if before_expiry else 47)

    def test_aborted_deployment_never_creates_dome_even_with_cooldown_written(self):
        run, winston, _, _ = arena()
        fight = run.match.combat
        run.step({winston: (0, 0, 16)})
        pending = fight.reinhardt_barriers.winston_pending
        release = pending[winston.body]
        instance = release.wait.instance
        instance.state(2).stop()
        release.wait.end(False)
        instance.component.run_now()
        instance.write((0, 61), 13.0)
        run.frame += 30
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(pending)
        self.assertFalse(fight.reinhardt_barriers.winston)
        self.assertGreater(instance.state(2).remaining(), 0)
        run.step({})
        self.assertFalse(fight.reinhardt_barriers.winston)

    def test_live_dome_capacity_and_expiry_are_causal_in_server_snapshot(self):
        fixture = snapshot_fixtures.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        winston = players[0]
        match = winston.match
        peers[0].message(21616, snapshot_fixtures.pick(winston.entity, winston_jump.HERO), now=100.95)
        self.assertEqual(winston.hero.guid, winston_jump.HERO)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for buttons in (16, 0, 0, 0, 0):
            tick = match.tick + 1
            match.update(tick * combat.TICK, tick)
            winston.take_commands([Command(tick, buttons=buttons, extra=0)])
            if buttons:
                pending = match.combat.reinhardt_barriers.winston_pending[winston.body]
                scheduled = snapshot_digest(fixture.capture(session))
                pending.tick += 1
                self.assertNotEqual(snapshot_digest(fixture.capture(session)), scheduled)
                pending.tick -= 1
        actor, = match.combat.reinhardt_barriers.winston.values()
        before = snapshot_digest(fixture.capture(session))
        actor.health -= 47
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
        actor.health += 47
        expires = actor.expires
        actor.expires += 1
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
        actor.expires = expires
        actor.starts_tick += 1
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
        actor.starts_tick -= 1
        self.assertEqual(snapshot_digest(fixture.capture(session)), before)

    def test_actual_beam_and_grenade_respect_dome_team(self):
        for allied in (False, True):
            for secondary in (False, True):
                with self.subTest(allied=allied, secondary=secondary):
                    run, winston, zarya, receiver = arena(allied=allied)
                    fight = run.match.combat
                    actor, instance = deploy(run, winston)
                    self.assertEqual(actor.health, 700)
                    self.assertEqual(actor.centre, (0, 1.1, 0))
                    self.assertAlmostEqual(actor.expires - fight.now, 9)
                    self.assertGreater(instance.state(2).remaining(), 12.9)
                    before = health(fight, receiver)
                    point = fight._eye(receiver)
                    if secondary:
                        self.assertNotIn(cast(run, zarya, point), fight.rockets)
                    else:
                        angles = aim(fight._eye(zarya), point)
                        for _ in range(31):
                            run.step({zarya: (*angles, 1)})
                    damage = 47 if secondary else 36
                    self.assertAlmostEqual(700 - actor.health, 0 if allied else damage)
                    self.assertAlmostEqual(before - health(fight, receiver), damage if allied else 0)
                    self.assertEqual(bool(zarya.client.messages(combat.HIT)), allied)

    def test_grenade_break_retains_dome_cover_for_that_explosion(self):
        run, winston, zarya, receiver = arena()
        fight = run.match.combat
        actor, _ = deploy(run, winston)
        actor.health = 30
        before = health(fight, receiver)
        self.assertNotIn(cast(run, zarya, fight._eye(receiver)), fight.rockets)
        self.assertEqual(actor.health, 0)
        self.assertEqual(health(fight, receiver), before)
        self.assertEqual(receiver.mover.velocity, (0, 0, 0))
        self.assertFalse(zarya.client.messages(combat.HIT))

    def test_beam_from_inside_only_hits_dome_when_crossing_its_exit(self):
        for outside in (False, True):
            with self.subTest(outside=outside):
                run, winston, zarya, receiver = arena(receiver_z=6 if outside else 4)
                fight = run.match.combat
                actor, _ = deploy(run, winston)
                move(zarya, (0, 1, 1))
                before = health(fight, receiver)
                angles = aim(fight._eye(zarya), fight._eye(receiver))
                for _ in range(31):
                    run.step({zarya: (*angles, 1)})
                self.assertAlmostEqual(700 - actor.health, 36 if outside else 0)
                self.assertAlmostEqual(before - health(fight, receiver), 0 if outside else 36)

    def test_graviton_deploys_on_enemy_dome_and_crosses_allied_dome(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, winston, zarya, receiver = arena(allied=allied, receiver_z=1)
                fight = run.match.combat
                actor, _ = deploy(run, winston)
                before = health(fight, receiver)
                self.assertNotIn(cast(run, zarya, fight._eye(receiver), ultimate=True), fight.rockets)
                field, = fight.zarya_gravitons.fields
                self.assertAlmostEqual(700 - actor.health, 0 if allied else 5)
                self.assertAlmostEqual(before - health(fight, receiver), 5 if allied else 0)
                self.assertEqual(field.position[2] < 2, allied)
                if not allied:
                    distance = math.dist(field.position, actor.centre)
                    self.assertGreater(distance, winston_barrier.RADIUS)
                    self.assertLessEqual(distance, winston_barrier.RADIUS + 0.010001)
                run.step({})
                self.assertAlmostEqual(before - health(fight, receiver), 5.08 if allied else 0.08)

    def test_dome_expires_on_world_clock_with_no_fresh_owner_command(self):
        run, winston, zarya, _ = arena()
        fight = run.match.combat
        actor, instance = deploy(run, winston)
        self.assertIsNotNone(fight.reinhardt_barriers.trace(zarya, fight._eye(zarya), (0, 0, -1), 10))
        run.frame = math.ceil(actor.expires / combat.TICK)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(actor.entity, fight.reinhardt_barriers.winston)
        self.assertEqual(instance.find_var(0, 38).value(), expr.Entity(0))
        self.assertIsNone(fight.reinhardt_barriers.trace(zarya, fight._eye(zarya), (0, 0, -1), 10))

    def test_dome_survives_its_owner_dying_to_actual_beam(self):
        run, winston, zarya, receiver = arena()
        fight = run.match.combat
        actor, _ = deploy(run, winston)
        move(winston, (4, 1, 8))
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is winston))
        vitals.now = dict.fromkeys(vitals.now, 0.0)
        vitals.now[combat.HEALTH_POOL] = 1
        angles = aim(fight._eye(zarya), fight._eye(winston))
        for _ in range(8):
            run.step({zarya: (*angles, 1)})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertIs(fight.reinhardt_barriers.winston[actor.entity], actor)
        for _ in range(10):
            run.step({})
        before = health(fight, receiver)
        self.assertNotIn(cast(run, zarya, fight._eye(receiver)), fight.rockets)
        self.assertEqual(actor.health, 653)
        self.assertEqual(health(fight, receiver), before)


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual five-NPC graphs and movers")
class ZaryaWinstonBarrierNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def test_dome_protects_each_actual_npc_on_grenade_break(self):
        for definition in fixtures.ZaryaFiveNpcTests.catalog.values():
            with self.subTest(npc=definition.name):
                run, winston, zarya, receiver = arena()
                fight = run.match.combat
                move(receiver, (30, 1, 30))
                actor, _ = deploy(run, winston)
                bot = npc.spawn(run.match, definition, (0, 1, 3.6), team=winston.team)
                actor.health = 30
                before = health(fight, bot)
                self.assertNotIn(cast(run, zarya, (0, 2.3, 3.6)), fight.rockets)
                self.assertEqual(actor.health, 0)
                self.assertEqual(health(fight, bot), before)
                self.assertIsNone(bot.impulse_mover)
                self.assertFalse(zarya.client.messages(combat.HIT))
