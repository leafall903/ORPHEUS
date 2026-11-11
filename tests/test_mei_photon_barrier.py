"""Actual Mei attacks and controls against the 104319 Photon Barrier."""

import math
import os
import unittest
from unittest import mock

import test_mei_combat as fixtures
import test_mei_wall as walls
import test_server_snapshot as snapshots
from test_game_server import pick

from ow174.game import combat, photon_barrier, replay_manifest
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script import runtime
from ow174.game.server_snapshot import snapshot_digest


@unittest.skipUnless(
    os.environ.get("ORPHEUS_SYMMETRA_COLLISION") and photon_barrier.body_definition(),
    "verified external 104319 Photon Barrier resources required",
)
class MeiPhotonBarrierTests(unittest.TestCase):
    def scene(self):
        fixture = fixtures.MeiCombatTests()
        run, mei, owner, _ = fixture.duel()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(owner, heroes()[photon_barrier.HERO]))
        x, y, z = mei.mover.position
        fight.walls.place_player(owner, (x, y, z + 6.))
        for _ in range(35):
            run.step({owner: (-32768, 8192, 0)})
        return fixture, run, mei, owner, fight

    def cast(self, run, owner, fight):
        script = owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        for frame in range(37):
            run.step({owner: (-32768, 8192, 32 if frame == 0 else FIRE if frame == 12 else 0)})
        barrier, = fight.photon_barriers.actors.values()
        self.assertEqual(script.component.entity_var(342).value(), 0.)
        self.assertEqual(barrier.health, 4000.)
        self.assertTrue(any(i.graph.index == 0x156C for i in barrier.component.instances.values()))
        return barrier

    def test_spray_melee_ice_wall_priority_and_breaking_icicle(self):
        fixture, run, mei, owner, fight = self.scene()
        actor = self.cast(run, owner, fight)
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        wall = walls.WallTests()
        _, angle = wall.place(run, mei, distance=1.2)
        fixture.fire(run, mei, target, SECONDARY, .9)
        self.assertEqual((actor.health, vitals.total), (4000., 225.))
        wall.tap(run, mei, 16, angle)
        fixture.fire(run, mei, target, FIRE, .3)
        self.assertEqual((actor.health, vitals.total), (3983.5, 225.))
        self.assertIsNone(fight.frost(owner))
        distance, _ = fight.photon_barriers.trace(mei, fight._eye(mei), (0., 0., 1.), 6.)
        x, y, z = mei.mover.position
        fight.walls.place_player(mei, (x, y, z + distance - 1.25))
        for _ in range(18):
            run.step({})
        fixture.fire(run, mei, target, 0x800, .9, frames=32)
        self.assertEqual(actor.health, 3953.5)
        before = vitals.total
        actor.health = 40.
        fixture.fire(run, mei, target, SECONDARY, .9)
        self.assertEqual((actor.health, vitals.total), (0., before))
        self.assertFalse(fight.photon_barriers.actors)
        fixture.fire(run, mei, target, SECONDARY, .9)
        self.assertEqual(vitals.total, before - 75.)

    def test_landed_blizzard_freezes_across_the_mesh(self):
        _, run, mei, owner, fight = self.scene()
        actor = self.cast(run, owner, fight)
        x, y, z = mei.mover.position
        angle = fixtures.aim(fight._eye(mei), (x, y, z + .8))
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        for frame in range(220):
            run.step({mei: (*angle, 32 if frame == 0 else 0)})
            if fight.frozen(owner):
                break
        area, = fight.blizzard.areas.values()
        origin = (area.position[0], area.position[1] + .5, area.position[2])
        destination = fight._eye(owner)
        length = math.dist(origin, destination)
        direction = tuple((b - a) / length for a, b in zip(origin, destination, strict=True))
        hit = fight.photon_barriers.geometry.cast(actor.position, actor.yaw, origin, direction, length)
        self.assertIsNotNone(hit)
        self.assertTrue(fight.frozen(owner))
        self.assertEqual(actor.health, 4000.)

    def test_sky_refusal_rotated_retry_and_expiry_after_death(self):
        _, run, mei, owner, fight = self.scene()
        script = owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        for frame in range(10):
            run.step({owner: (-32768, -8192, 32 if frame == 0 else 0)})
        run.step({owner: (-32768, -8192, FIRE)})
        self.assertFalse(fight.photon_barriers.actors)
        self.assertEqual(script.component.entity_var(342).value(), cost)
        for frame in range(10):
            run.step({owner: (-32768, 8192, 32 if frame == 0 else 0)})
        run.step({owner: (-32768, 8192, FIRE)})
        for _ in range(25):
            run.step({})
        actor, = fight.photon_barriers.actors.values()
        self.assertAlmostEqual(abs(actor.yaw), math.pi / 2, places=3)
        owner_target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(mei, owner_target, 999., False, None, run.frame)
        self.assertTrue(fight.vitals_of(owner_target).dead)
        for tick in range(run.frame + 1, actor.expires):
            run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertTrue(fight.photon_barriers.actors)
        run.match.update(actor.expires * combat.TICK, actor.expires, frames=False)
        self.assertFalse(fight.photon_barriers.actors)

    def test_active_actor_is_in_passive_snapshot_and_hashes_are_in_replay_identity(self):
        config = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        self.assertEqual(config["ow174.game.photon_barrier.mesh_sha256"], photon_barrier.GEOMETRY_SHA256)
        fixture = snapshots.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        owner, = players
        peers[0].message(21616, pick(owner.entity, photon_barrier.HERO), now=100.8)
        session.tick(100.960)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        for offset, buttons in enumerate([32] * 10 + [0, FIRE] + [0] * 28):
            session.tick(100.976 + offset * .016)
            owner.take_commands([Command(owner.body_script.frame + 1, pitch=8192, buttons=buttons)])
        actor, = owner.match.combat.photon_barriers.actors.values()
        self.assertEqual(actor.health, 4000.)
        with mock.patch.object(runtime.Component, 'run_now', side_effect=AssertionError('VM evaluated')):
            before = snapshot_digest(fixture.capture(session))
            actor.health -= 1
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)

