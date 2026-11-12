import os
from pathlib import Path
from unittest.mock import patch
import unittest

from ow174.game import collision, content, props, volskaya, world
from ow174.game.bits import BitReader
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.script import runtime
from ow174.game.server import GameServer
from ow174.game.server_snapshot import export_server_state, snapshot_digest
from ow174.game.collision_fingerprint import world_fingerprint
from ow174.game.physics_snapshot import snapshot_mover
import test_game_combat as fixtures
from test_game_world import read_frame_start, read_movement, read_record
from test_game_mover import box
from ow174.game.commands import Command

folder = volskaya.PLATFORM_DATA.parent
runtime_dir = Path(os.environ.get('ORPHEUS_RUNTIME_DIR', 'runtime-104319'))

def decode_create(match, viewer):
    create, = (entry for entry in match.spawn_updates(viewer) if entry.entity == volskaya.ENTITY)
    payload = world.frame(1234, 1234, (), (), [create])
    reader, tick, ack, count = read_frame_start(payload)
    assert (tick, ack, count, reader.entity_id()) == (1234, 1234, 1, volskaya.ENTITY)
    assert [reader.bit() for _ in range(7)] == [1, 1, 0, 1, 0, 0, 1]
    assert (reader.bit(), reader.bit(), reader.bits(2)) == (0, 1, world.OP_CREATE)
    length = reader.bits(14)
    start = reader.pos
    assert (reader.bit(), reader.bits(32), reader.bit()) == (1, volskaya.FLAT, 1)
    state = read_movement(reader, world.CREATE_BASE)
    assert reader.bits(8) == 0 and reader.pos - start == length and reader.bit() == 0
    return create, state

def decode_pillar(update):
    out = update.build(0)
    reader = BitReader(out.getvalue())
    assert (reader.bit(), reader.bit(), reader.bits(12)) == (0, 1, 0x932)
    assert reader.bits(8) == world.WITH_TRANSFORM | world.WITH_SCALE | world.WITH_MOVEMENT
    for _ in range(10):
        reader.bits(32)
    return read_movement(reader, world.CREATE_BASE, parent=volskaya.ENTITY)


@unittest.skipUnless(volskaya.PLATFORM_DATA.is_file(), "requires original00DD verified platform bundle")
class NormalVolskayaTests(unittest.TestCase):
    def test_missing_bundle_preserves_ordinary_match(self):
        game = content.game_map(volskaya.MAP, volskaya.ASSAULT, 'Assault')
        with patch.object(volskaya, 'PLATFORM_DATA', folder / 'absent-platform-data.json.gz'):
            run = fixtures.Run(game)
        self.assertIsNone(run.match.scene_host_factory)
        self.assertNotIn(volskaya.ENTITY, run.match.placeable_ids())

    def test_normal_match_moves_platform_and_reports_pose_before_ice_wall(self):
        game = content.game_map(volskaya.MAP, volskaya.ASSAULT, 'Assault')
        base = collision.World.load(runtime_dir / 'collision/01DB.col')
        asset = world_fingerprint(base)
        with patch.object(volskaya, 'PLATFORM_DATA', folder / 'volskaya-platform-104319.json.gz'), patch.object(
                fixtures, 'flat_world', return_value=base):
            run = fixtures.Run(game, ('Mei', 'rider'))
        match = run.match
        mei, rider = run.players
        self.assertTrue(match.switch_hero(mei, content.heroes()[0x02E00000000000DD]))
        for _ in range(100):
            run.step({})
            self.assertIsNone(match.scene_host_error)
        self.assertIn(volskaya.ENTITY, match.placeable_ids())
        host = match.scene_host
        lift = host.lift
        x, y, z = lift.origin
        self.assertEqual(lift.position, lift.origin)
        early, initial = decode_create(match, mei)
        self.assertFalse(any(entry.entity == volskaya.ENTITY for entry in match.remote_movements(mei)))
        match.sent_updates(mei, [early])
        for player, start_z in ((mei, z - 2.2), (rider, z)):
            floor = lift.collider.ground(x, start_z, y + 1, y - 1)
            match.combat.walls.place_player(player, (x, floor.y, start_z), ((0, 1, 0),))
            player.mover.velocity = (0, 0, 0)
        for _ in range(165):
            run.step({})
            self.assertIsNone(match.scene_host_error)
            if lift.position[1] - y > 1.6:
                break
        self.assertGreater(lift.position[1] - y, 1.6)
        late = match.add_player(3, 'late viewer', content.SOLDIER, 0, False)
        late_create, current = decode_create(match, late)
        self.assertGreater(current['position'][1] - initial['position'][1], 1.6)
        self.assertFalse(any(entry.entity == volskaya.ENTITY for entry in match.remote_movements(late)))
        match.sent_updates(late, [late_create])
        update, = (entry for entry in match.remote_movements(late) if entry.entity == volskaya.ENTITY)
        self.assertEqual(read_record(update.movement, match.tick)['position'], current['position'])
        for button in (16, 0, 1, 0):
            height = lift.position[1]
            floor = lift.collider.ground(x, z, height + .1, height - .1)
            angles = fixtures.aim(match.combat._eye(mei), (x, floor.y, z))
            run.step({mei: (*angles, button)})
            self.assertIsNone(match.scene_host_error)
        pillar, = match.combat.walls.pieces.values()
        self.assertAlmostEqual(rider.mover.position[1], pillar.feet[1] + 4.4, places=3)
        self.assertGreater(match.combat.walls.instance(mei).vars[61].value(), 11.9)
        self.assertIn(pillar.entity, lift.attached_walls)
        first_pillar, = (item for item in mei.client.entities if item.entity == pillar.entity
                         and item.op == world.OP_CREATE)
        relative = decode_pillar(first_pillar)['position']
        qx, qy, qz, qw = lift.rotation
        expected = props._rotate((-qx, -qy, -qz, qw), tuple(
            a - b for a, b in zip(pillar.feet, lift.position, strict=True)))
        self.assertEqual(relative, tuple(round(axis * 1024) / 1024 for axis in expected))
        controller = host.units[volskaya.ENTITY].component
        before_rng = controller.rng.getstate()
        with patch.object(runtime.Var, 'value', side_effect=AssertionError('snapshot may not evaluate')):
            before_vm = export_component_vm(controller)
            self.assertEqual(before_vm, export_component_vm(controller))
        self.assertEqual(controller.rng.getstate(), before_rng)
        server = GameServer(environment=match.environment, skin_of=match.skin_of)
        server.matches.append(match)
        def capture_domain():
            # The fixture's packet receivers are stand-ins; the stored server
            # graph is captured without those test-owned endpoint objects.
            clients = [player.client for player in match.players]
            try:
                for player in match.players:
                    player.client = None
                return export_server_state(server, collision_provider=match.collision_provider)
            finally:
                for player, client in zip(match.players, clients, strict=True):
                    player.client = client
        with patch.object(runtime.Var, 'value', side_effect=AssertionError('snapshot may not evaluate')):
            baseline_domain = capture_domain()
        baseline_digest = snapshot_digest(baseline_domain)
        stored = {node['type'] for node in baseline_domain['nodes']}
        self.assertIn('ow174.game.volskaya.LiftHost', stored)
        self.assertIn('ow174.game.volskaya.LiftScene', stored)
        self.assertIn('ow174.game.collision.LayeredWorld', stored)
        lift.attached_walls.remove(pillar.entity)
        try:
            self.assertNotEqual(snapshot_digest(capture_domain()), baseline_digest)
        finally:
            lift.attached_walls.add(pillar.entity)
        self.assertEqual(snapshot_digest(capture_domain()), baseline_digest)
        before_physics = snapshot_mover(rider.mover)
        old_height, old_feet, old_rider = lift.position[1], pillar.feet[1], rider.mover.position[1]
        for _ in range(3):
            run.step({})
        delta = lift.position[1] - old_height
        self.assertGreater(delta, .2)
        self.assertAlmostEqual(pillar.feet[1] - old_feet, delta, places=6)
        self.assertAlmostEqual(rider.mover.position[1] - old_rider, delta, places=3)
        self.assertNotEqual(snapshot_digest(capture_domain()), baseline_digest)
        after_vm = export_component_vm(controller)
        self.assertNotEqual(before_vm['value'], after_vm['value'])
        after_physics = snapshot_mover(rider.mover)
        self.assertNotEqual(before_physics['world_descriptor'], after_physics['world_descriptor'])
        self.assertEqual(after_physics, snapshot_mover(rider.mover,
                         world_descriptor=after_physics['world_descriptor']) | {'world_validation': 'fingerprinted'})

        fixtures.in_world(match, late)
        late.client.queue_entities(match.spawn_updates(late))
        run.step({})
        late_pillar, = (item for item in late.client.entities if item.entity == pillar.entity
                        and item.op == world.OP_CREATE)
        self.assertEqual(decode_pillar(late_pillar)['position'], relative)
        for button in (16, 0):
            run.step({mei: (*angles, button)})
        self.assertFalse(match.combat.walls.pieces)
        self.assertFalse(lift.attached_walls)
        for player, start_z in ((mei, z + 7), (rider, z + 9)):
            floor = base.ground(x, start_z, y + 1, y - 5)
            match.combat.walls.place_player(player, (x, floor.y, start_z), ((0, 1, 0),))
            player.mover.velocity = (0, 0, 0)
        for _ in range(110):
            run.step({})
            self.assertIsNone(match.scene_host_error)
            if lift.segment is None:
                break
        self.assertIsNone(lift.segment)
        self.assertAlmostEqual(lift.position[1], y, places=5)
        for _ in range(75):
            run.step({})
        self.assertEqual(lift.segment, None)
        floor = lift.collider.ground(x, z, y + .1, y - .1)
        match.combat.walls.place_player(rider, (x, floor.y, z), ((0, 1, 0),))
        rider.mover.velocity = (0, 0, 0)
        for _ in range(160):
            run.step({})
            self.assertIsNone(match.scene_host_error)
            if lift.position[1] - y > .2:
                break
        self.assertGreater(lift.position[1] - y, .2)
        for player in (mei, rider, late):
            match.leave(player)
        self.assertTrue(host.closed)
        self.assertIsNone(host.error)
        self.assertIs(match.collision_world, base)
        self.assertEqual(world_fingerprint(base), asset)
        print('normal Match lift', initial['position'], current['position'],
              'wall y', pillar.feet[1], 'closed', host.closed, flush=True)

    def make_platform_match(self, base, names):
        game = content.game_map(volskaya.MAP, volskaya.ASSAULT, 'Assault')
        with patch.object(volskaya, 'PLATFORM_DATA', folder / 'volskaya-platform-104319.json.gz'), patch.object(
                fixtures, 'flat_world', return_value=base):
            run = fixtures.Run(game, names)
        mei = run.players[0]
        self.assertTrue(run.match.switch_hero(mei, content.heroes()[0x02E00000000000DD]))
        run.step({})
        self.assertIsNone(run.match.scene_host_error)
        return run, run.match.scene_host.lift

    def test_moving_hull_omits_columns_in_normal_assault_match(self):
        floor_y = -1.9615392684936523 + .25
        run, lift = self.make_platform_match(collision.flat_world(floor_y), ('Mei', 'trigger'))
        x, y, z = lift.origin
        mei, trigger = run.players
        walls = run.match.combat.walls
        for player, start in ((mei, (x + 2.7, floor_y, z - 5)), (trigger, (x + 2.7, floor_y, z))):
            walls.place_player(player, start, ((0, 1, 0),))
            player.mover.velocity = (0, 0, 0)
        for _ in range(6):
            run.step({})
        angles = fixtures.aim(run.match.combat._eye(mei), (x + 2.7, floor_y, z))
        cmd = Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1, extra=0)
        self.assertEqual(len(walls.placement(mei, cmd, False)[0]), 5)
        for _ in range(165):
            run.step({})
            self.assertIsNone(run.match.scene_host_error)
            if lift.position[1] - y > 1.5:
                break
        self.assertGreater(lift.position[1] - y, 1.5)
        self.assertEqual(len(walls.placement(mei, cmd, False)[0]), 2)
        for button in (16, 0, 1, 0):
            run.step({mei: (*angles, button)})
        self.assertEqual(len(walls.pieces), 2)
        self.assertFalse(lift.attached_walls)
        self.assertGreater(walls.instance(mei).vars[61].value(), 11.9)
        print('normal moving obstruction reduced five clear columns to two', flush=True)

    def test_low_ceiling_holds_the_lift_short_of_the_grounded_rider(self):
        x, y, z = -64.68500518798828, -1.9615392684936523, 53.190574645996094
        roof = y + 2.7
        floor = [(-150, -10, -150, -150, -10, 150, 150, -10, 150),
                 (-150, -10, -150, 150, -10, 150, 150, -10, -150)]
        base = collision.World(floor + box(x - .8, x + .8, roof, roof + .2, z - .8, z + .8))
        run, lift = self.make_platform_match(base, ('Mei',))
        rider, = run.players
        walls = run.match.combat.walls
        support = lift.collider.ground(x, z, y + .1, y - .1)
        walls.place_player(rider, (x, support.y, z), ((0, 1, 0),))
        rider.mover.velocity = (0, 0, 0)
        for _ in range(150):
            run.step({})
            self.assertIsNone(run.match.scene_host_error)
        stopped_y = lift.position[1]
        self.assertGreater(stopped_y, y + .2)
        self.assertLess(stopped_y, y + 2.)
        for _ in range(20):
            run.step({})
            self.assertIsNone(run.match.scene_host_error)
        self.assertEqual(lift.position[1], stopped_y)
        self.assertEqual(lift.velocity, 0.)
        radius, segment = rider.mover._capsule()
        top = rider.mover.position[1] + rider.mover._lift() + 2 * radius + segment
        self.assertLessEqual(top, roof + .002)
        walls.place_player(rider, (x + 8., -10., z), ((0, 1, 0),))
        rider.mover.velocity = (0, 0, 0)
        for _ in range(24):
            run.step({})
            self.assertIsNone(run.match.scene_host_error)
        self.assertGreater(lift.position[1], stopped_y + .4)
        print('rider top', top, 'roof', roof, 'blocked lift', stopped_y, 'resumed lift', lift.position[1], flush=True)

    def test_attached_wall_blocks_lift_below_ceiling_until_cancel(self):
        x, y, z = -64.68500518798828, -1.9615392684936523, 53.190574645996094
        roof = y + 5.6
        floor = [(-150, -10, -150, -150, -10, 150, 150, -10, 150),
                 (-150, -10, -150, 150, -10, 150, 150, -10, -150)]
        base = collision.World(floor + box(x - 1., x + 1., roof, roof + .2, z - .6, z + .6))
        run, lift = self.make_platform_match(base, ('Mei',))
        mei, = run.players
        walls = run.match.combat.walls
        start = lift.collider.ground(x, z - 2.2, y + .1, y - .1)
        walls.place_player(mei, (x, start.y, z - 2.2), ((0, 1, 0),))
        mei.mover.velocity = (0, 0, 0)
        for _ in range(120):
            run.step({})
            if lift.position[1] - y > .2:
                break
        self.assertGreater(lift.position[1] - y, .2)
        for button in (16, 0, 1, 0):
            height = lift.position[1]
            support = lift.collider.ground(x, z, height + .1, height - .1)
            angles = fixtures.aim(run.match.combat._eye(mei), (x, support.y, z))
            run.step({mei: (*angles, button)})
        pillar, = walls.pieces.values()
        self.assertIn(pillar.entity, lift.attached_walls)
        for _ in range(18):
            run.step({})
            self.assertIsNone(run.match.scene_host_error)
        paused_y = lift.position[1]
        self.assertGreater(paused_y, y + .7)
        self.assertLessEqual(pillar.feet[1] + pillar.height, roof)
        self.assertLess(roof - (pillar.feet[1] + pillar.height), .15)
        for _ in range(8):
            run.step({})
        self.assertEqual(lift.position[1], paused_y)
        for button in (16, 0):
            run.step({mei: (*angles, button)})
        self.assertFalse(lift.attached_walls)
        self.assertFalse(walls.pieces)
        self.assertGreater(lift.position[1], paused_y)
        print('attached wall paused lift at', paused_y, 'and cancelled while cooldown stayed active', flush=True)



if __name__ == '__main__':
    unittest.main(verbosity=2)
