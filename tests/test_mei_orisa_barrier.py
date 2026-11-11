"""Real Orisa deployable barrier against actual 104319 Mei commands."""
import math
import os
import unittest
from unittest import mock

import test_mei_combat as fixtures
import test_mei_wall as walls
import test_server_snapshot as snapshots
from test_server_journal import pick

from ow174.game import combat, orisa_barrier, replay_manifest
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import runtime
from ow174.game.server_snapshot import snapshot_digest

ORISA = 0x02E000000000013E


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION"), "external 104319 Orisa mesh required")
class MeiOrisaBarrierTests(unittest.TestCase):
    @staticmethod
    def idle(run):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)

    def scene(self):
        fixture = fixtures.MeiCombatTests()
        run, mei, orisa, _target = fixture.duel()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(orisa, heroes()[ORISA]))
        x, y, z = mei.mover.position
        fight.walls.place_player(orisa, (x, y, z + 5.))
        for _ in range(25):
            run.step({})
        manager = fight.orisa_barriers
        self.assertIsNotNone(manager.geometry)
        run.step({orisa: (-32768, 8192, 16)})
        flight = None
        for _ in range(80):
            flight = manager.flights.get(orisa.body, flight)
            if orisa.body in manager.actors:
                break
            self.idle(run)
        self.assertIsNotNone(flight)
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        shield, = manager.actors.values()
        self.assertEqual(shield.health, 600.)
        self.assertTrue(shield.active())
        self.assertLess(shield.position[2], orisa.mover.position[2])
        target = next(t for t in fight._targets() if t.owner is orisa)
        return fixture, run, mei, orisa, target, shield

    def test_spray_and_icicle_hit_landed_mesh_without_body_damage(self):
        fixture, run, mei, orisa, target, shield = self.scene()
        fight = run.match.combat
        pool = fight.vitals_of(target)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(shield.health, 525.)
        fixture.fire(run, mei, target, 1, .9, frames=1)
        for _ in range(35):
            run.step({})
        self.assertEqual(shield.health, 522.25)
        self.assertEqual(pool.total, 450.)
        self.assertIsNone(fight.frost(orisa))
        self.assertFalse(mei.client.messages(combat.HIT))
        fight.orisa_barriers.damage(shield, shield.health - 5.)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(shield.health, 0.)
        self.assertEqual(pool.total, 450.)
        self.assertFalse(mei.client.messages(combat.HIT))
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(pool.total, 380.)
        self.assertEqual(len(mei.client.messages(combat.HIT)), 1)

    def test_static_cover_then_melee_contacts_visible_mesh(self):
        fixture, run, mei, _orisa, target, shield = self.scene()
        fight = run.match.combat
        x, y, z = mei.mover.position
        contact = fight.orisa_barriers.trace(mei, fight._eye(mei), (0., 0., 1.), 7.)
        self.assertIsNotNone(contact)
        cover = contact[0] * .45
        fixtures.static_wall(run, mei, distance=cover)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(shield.health, 600.)
        offset = max(cover + .15, contact[0] - 1.35)
        fight.walls.place_player(mei, (x, y, z + offset))
        for _ in range(18):
            run.step({})
        fixture.fire(run, mei, target, 0x800, .9, frames=32)
        self.assertEqual(shield.health, 570.)

    def test_barrier_expires_without_new_owner_input(self):
        _fixture, run, _mei, _orisa, _target, shield = self.scene()
        fight = run.match.combat
        start = shield.component.frame
        for _ in range(1260):
            if not shield.active():
                break
            self.idle(run)
        self.assertFalse(shield.active())
        self.assertIn(run.match.tick - start, range(1249, 1252))
        self.assertIn(shield.source_body, fight.orisa_barriers.actors)
        for _ in range(40):
            if not fight.orisa_barriers.actors:
                break
            self.idle(run)
        self.assertFalse(fight.orisa_barriers.actors)
        self.assertIn(run.match.tick - start, range(1280, 1283))
        self.assertEqual(shield.health, 600.)

    def test_blizzard_exposes_orisa_across_landed_mesh(self):
        _fixture, run, mei, orisa, target, shield = self.scene()
        fight = run.match.combat
        x, y, z = mei.mover.position
        angle = fixtures.aim(fight._eye(mei), (x, y, z + 1.))
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        for frame in range(240):
            run.step({mei: (*angle, 32 if frame == 0 else 0)})
            if fight.frozen(orisa):
                break
        area, = fight.blizzard.areas.values()
        origin = (area.position[0], area.position[1] + .5, area.position[2])
        destination = fight._eye(orisa)
        length = math.dist(origin, destination)
        direction = tuple((b - a) / length for a, b in zip(origin, destination, strict=True))
        self.assertIsNotNone(fight.orisa_barriers.intersect(shield, origin, direction, length))
        self.assertTrue(fight.frozen(orisa))
        self.assertLess(fight.vitals_of(target).total, 450.)
        self.assertTrue(shield.active())
        self.assertEqual(shield.health, 600.)

    def test_landed_child_pool_is_registered_for_passive_capture(self):
        fixture = snapshots.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        owner, = players
        peers[0].message(21616, pick(owner.entity, ORISA), now=100.8)
        match = owner.match
        self.assertEqual(owner.hero.guid, ORISA)
        session.tick(100.960)
        owner.take_commands([Command(owner.body_script.frame + 1, pitch=8192, buttons=16)])
        manager = match.combat.orisa_barriers
        for at in range(1, 45):
            if manager.actors:
                break
            session.tick(100.960 + at * .016)
        shield, = manager.actors.values()
        self.assertEqual(shield.health, 600.)
        with (
            mock.patch.object(runtime.Component, "run_now", side_effect=AssertionError("VM evaluated")),
            mock.patch.object(runtime.Var, "value", side_effect=AssertionError("variable evaluated")),
        ):
            before = snapshot_digest(fixture.capture(session))
            shield.health -= 20.
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
            shield.health += 20.
            shield.component.frame += 1
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
            shield.component.frame -= 1
            self.assertEqual(snapshot_digest(fixture.capture(session)), before)

    def test_landed_mesh_keeps_cover_after_owner_body_replacement(self):
        fixture, run, mei, orisa, target, shield = self.scene()
        fight = run.match.combat
        original = orisa.body
        feet = target.feet
        self.assertTrue(run.match.switch_hero(orisa, mei.hero))
        self.assertNotEqual(orisa.body, original)
        fight.walls.place_player(orisa, feet)
        for _ in range(20):
            run.step({})
        self.assertIs(fight.orisa_barriers.actors[original], shield)
        replacement = next(t for t in fight._targets() if t.owner is orisa)
        fixture.fire(run, mei, replacement, 2, .9)
        self.assertEqual(shield.health, 525.)
        self.assertEqual(fight.vitals_of(replacement).total, 250.)

    def test_actual_ice_wall_takes_contact_before_orisa_mesh(self):
        fixture, run, mei, _orisa, target, shield = self.scene()
        fight = run.match.combat
        wall = walls.WallTests()
        columns, aim = wall.place(run, mei, distance=1.5)
        self.assertEqual(len(columns), 5)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(shield.health, 600.)
        self.assertTrue(all(piece.health == 400. for piece in columns))
        wall.tap(run, mei, 16, aim)
        self.assertFalse(fight.walls.pieces)
        fixture.fire(run, mei, target, 2, .9)
        self.assertEqual(shield.health, 525.)
        self.assertEqual(fight.vitals_of(target).total, 450.)

    def test_prepared_mesh_identity_is_registered(self):
        config = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        key = 'ow174.game.orisa_barrier.descriptor_sha256'
        self.assertEqual(config[key], orisa_barrier.descriptor_sha256())
