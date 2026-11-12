"""Run grounded Seismic Slam through real command input and static collision."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import bodytrace, bots, combat, movelog, observers
from ow174.game.collision import World
from ow174.game.content import SOLDIER
from ow174.game.script import decode, nodes
from ow174.game.script.driver import BodyStream
from ow174.game.script.expr import Entity
from ow174.game.world import OP_CREATE
from tests.runtime import test_game_move_to as movement
from tests.runtime.test_game_combat import in_world
from tests.runtime.test_game_mover import FLOOR, box, wall


class GroundedSeismicSlamTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))
        self.harness = movement.FixedTargetMoveToTests()

    def spawn(self, geometry):
        with patch.object(bots, "make", return_value=[]):
            player, slam = self.harness.spawn(movement.DOOMFIST, 0x0B27, geometry)
        [create] = [update for update in player.client.entities
                    if update.entity == player.body and update.op == OP_CREATE]
        create.stream.arrived(create.chunk_last)
        match = player.match
        match.game_map = replace(match.game_map, free_for_all=False, team_sizes=(2, 8))
        in_world(match, player)
        return player, slam

    def land(self, player, slam):
        cast = movement.START + 2
        self.harness.command(player, cast - 1)
        start = self.harness.command(player, cast, buttons=0x10)
        self.assertTrue(slam.state(0).flags & nodes.Ability.RUNNING)
        self.assertEqual(slam.vars[59].value(), 1.0)
        self.assertTrue(slam.state(1).active)
        self.assertEqual(start.position, (0.0, 1.0, 0.0))

        launch = self.harness.command(player, cast + 1)
        self.assertGreater(launch.velocity[1], 5.0)
        self.assertGreater(launch.velocity[2], 18.0)
        self.assertTrue(player.mover.airborne)
        self.assertGreater(launch.position[2], start.position[2])

        for frame in range(cast + 2, cast + 37):
            landing = self.harness.command(player, frame)
            if slam.state(58).active:
                break
        else:
            self.fail("Grounded Seismic Slam did not enter its impact phase")
        self.assertFalse(slam.state(1).active)
        self.assertEqual(slam.vars[59].value(), 3)
        self.assertAlmostEqual(landing.position[1], 1.0)
        self.assertGreater(landing.position[2], 7.0)
        self.assertLess(landing.position[2], 9.0)
        return frame, landing.position

    def place_targets(self, match, position, cases):
        # Spawn after landing so target contacts do not change the launch distance.
        x, y, z = position
        targets = []
        for slot, (name, team, dx, dz) in enumerate(cases, 2):
            target = match.add_player(slot, name, SOLDIER, team, False)
            in_world(match, target)
            self.assertTrue(match.switch_hero(target, target.hero))
            target.mover.position[:] = (x + dx, y, z + dz)
            targets.append(target)
        return targets

    def assert_hit_targets(self, player, slam, frame, targets, count):
        vitals = [player.match.combat.body_vitals(target.body, target) for target in targets]
        self.harness.commands(player, range(frame + 1, frame + 45))
        self.assertIsNone(slam.component.fault)
        self.assertFalse(slam.state(0).flags & nodes.Ability.RUNNING)
        hit_bodies = [target.body for target in targets[:count]]
        damage = slam.vars[476].value()
        self.assertGreater(damage, 1.0)
        for target in vitals[:count]:
            self.assertAlmostEqual(target.total, 200.0 - damage, places=4)
        self.assertEqual([target.total for target in vitals[count:]], [200.0] * (len(targets) - count))
        recipients = [notice["+0x78"]["+0x0"]["+0x0"] for notice in player.client.messages(combat.HIT)]
        self.assertEqual(recipients, hit_bodies)

    def test_ground_press_launches_lands_and_applies_close_damage_once(self):
        geometry = World(FLOOR + wall(-0.7, 5.0, -0.7, 12.0, 1.0, 4.0))
        player, slam = self.spawn(geometry)
        frame, position = self.land(player, slam)
        targets = self.place_targets(player.match, position, (
            ("visible", 1, 1.25, 0.65), ("also visible", 1, 1.25, 1.6),
            ("third", 1, 1.15, 0.75), ("fourth", 1, 1.3, 0.7),
            ("fifth", 1, 1.1, 1.1), ("sixth", 1, 1.2, 1.2),
            ("occluded", 1, -1.25, 0.65), ("ally", 0, 1.2, 0.65), ("out of range", 1, 3.0, 0.65),
        ))
        self.assert_hit_targets(player, slam, frame, targets, 6)
        self.assertEqual(slam.vars[1182].value(), tuple(Entity(target.body) for target in targets[:6]))

    def test_ground_shockwave_follows_the_sector_and_excludes_close_hits(self):
        geometry = World(
            box(-30, 30, 0, 1, -30, 13) + box(-30, 30, 0, 1, 15, 30)
            + wall(-1.0, 9.5, -1.0, 13.0, 1.0, 5.0)
        )
        player, slam = self.spawn(geometry)
        frame, position = self.land(player, slam)
        targets = self.place_targets(player.match, position, (
            ("close in sector", 1, 0.2, 1.8), ("wave only", 1, 0.0, 4.5),
            ("outside sector", 1, 3.5, 3.0), ("behind wall", 1, -1.6, 4.5),
            ("across gap", 1, 0.0, 7.7), ("out of range", 1, 0.0, 9.0), ("ally", 0, 1.0, 4.5),
        ))
        self.assert_hit_targets(player, slam, frame, targets, 2)
        self.assertEqual(slam.vars[1182].value(), (Entity(targets[0].body),))
        self.assertEqual(slam.vars[375].value(), (Entity(targets[1].body),))
        self.assertFalse(slam.state(44).active)
        self.assertFalse(slam.state(42).active)

    def test_ground_impact_actions_reach_owner_and_observer_with_stable_stamps(self):
        player, slam = self.spawn(movement.flat_world(1.0))
        body = player.body_script
        owner = decode.ClientBody.for_hero(movement.DOOMFIST)
        [tree] = body.frames()
        self.assertEqual(owner.apply(tree).actions, [])
        body.stream.arrived(tree.chunk_last)
        [initial] = body.frames()
        self.assertEqual(owner.apply(initial).actions, [])
        body.stream.arrived(initial.chunk_last)

        def join_observer():
            client = decode.ClientBody.for_hero(movement.DOOMFIST)
            stream = BodyStream(body.entity)
            baseline = observers.snapshot(body)
            full = stream.full(observers.full_frame(body, baseline), baseline)
            self.assertEqual(client.apply(full).actions, [])
            stream.arrived(full.chunk_last)
            return client, stream

        def observer_update(stream):
            snapshot = observers.snapshot(body)
            frame, keys = observers.delta(body, stream, snapshot)
            return stream.delta(frame, snapshot, keys, 0) if frame is not None else None

        observer, stream = join_observer()
        landed, position = self.land(player, slam)
        [local] = body.frames()
        remote = observer_update(stream)
        self.assertIsNotNone(remote)
        for client, update in ((owner, local), (observer, remote)):
            self.assertEqual(client.apply(update).actions, [(update.chunk_last, slam.id, 46)])
            update.stream.arrived(update.chunk_last)

        [victim] = self.place_targets(player.match, position, (("wave victim", 1, 0.0, 4.5),))
        wave = slam.state(44)
        for frame in range(landed + 1, landed + 20):
            self.harness.command(player, frame)
            if wave.counter and not wave.active:
                break
        else:
            self.fail("Seismic Slam's impact wave did not finish")
        late, late_stream = join_observer()

        [local] = body.frames()
        remote = observer_update(stream)
        self.assertIsNotNone(remote)
        sent = ((owner, local), (observer, remote))
        stamps = [update.chunk_last for _, update in sent]
        for client, update in sent:
            self.assertEqual(client.apply(update).actions, [(update.chunk_last, slam.id, 102)])
            self.assertTrue(client.states[slam.id, 41])
            self.assertEqual(client.vars[slam.id, 375], (Entity(victim.body),))
            update.stream.lost(update.chunk_last)

        self.harness.command(player, frame + 1)
        [local] = body.frames()
        remote = observer_update(stream)
        self.assertIsNotNone(remote)
        for (client, update), stamp in zip(((owner, local), (observer, remote)), stamps, strict=True):
            self.assertEqual(client.apply(update).actions, [(stamp, slam.id, 102)])
            update.stream.arrived(update.chunk_last)

        self.harness.command(player, frame + 2)
        [local] = body.frames()
        self.assertEqual(owner.apply(local).actions, [])
        for client, viewer_stream in ((observer, stream), (late, late_stream)):
            update = observer_update(viewer_stream)
            if update is not None:
                self.assertEqual(client.apply(update).actions, [])
