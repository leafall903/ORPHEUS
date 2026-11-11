"""Actual Ice Wall input against full-volume/support fixtures; native policy open."""

import math
import unittest

import test_mei_combat as combat_fixtures
import test_mei_wall as fixtures
from test_game_combat import PRACTICE_RANGE
from test_game_mover import box

from ow174.game import collision, mei_wall
from ow174.game.collision import World
from ow174.game.collision_fingerprint import world_fingerprint
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


class WallPlacementTests(unittest.TestCase):
    scene = fixtures.WallTests.scene
    place = fixtures.WallTests.place
    tap = fixtures.WallTests.tap

    def install(self, run, triangles):
        scene = World(triangles, name='Mei placement static volume fixture')
        run.match.collision_world = scene
        for player in run.players:
            player.mover.use(world=scene)
        for _ in range(20):
            run.step({})
        return scene

    def floor(self, x, y, z, *, half=200., slope=0.):
        def p(dx, dz):
            return (x + dx, y + slope * dx, z + dz)
        return [(*p(-half, -200.), *p(-half, 200.), *p(half, 200.)),
                (*p(-half, -200.), *p(half, 200.), *p(half, -200.))]

    def test_offcentre_static_obstruction_omits_intersecting_column_in_both_rotations(self):
        for rotated in (False, True):
            with self.subTest(rotated=rotated):
                run, owner, _enemy, _target = self.scene()
                x, y, z = owner.mover.position
                bounds = ((x - .1, x + .1, y + 2., y + 2.2, z + 3.5, z + 3.7)
                          if rotated else (x + .5, x + .7, y + 2., y + 2.2, z + 2.8, z + 3.2))
                scene = self.install(run, self.floor(x, y, z) + box(*bounds))
                before = world_fingerprint(scene)
                pieces, _aim = self.place(run, owner, rotated=rotated)
                self.assertEqual(len(pieces), 4)
                for piece in pieces:
                    along = piece.feet[2] - (z + 3.) if rotated else piece.feet[0] - x
                    self.assertGreater(abs(along), 1.)
                self.assertGreater(run.match.combat.walls.instance(owner).vars[61].value(), 11.9)
                self.assertEqual(world_fingerprint(scene), before)

    def test_full_low_ceiling_rejects_wall_without_spending_cooldown(self):
        run, owner, _enemy, _target = self.scene()
        x, y, z = owner.mover.position
        self.install(run, self.floor(x, y, z) + box(x - 5., x + 5., y + 4., y + 4.2, z + 2., z + 4.))
        pieces, _aim = self.place(run, owner)
        self.assertFalse(pieces)
        self.assertTrue(run.match.combat.walls.inputs[owner.body].preview)
        self.assertEqual(run.match.combat.walls.instance(owner).vars[61].value(), 0.)

    def test_off_axis_roof_blocks_occupied_column_but_allows_partial_wall(self):
        run, owner, _, _ = self.scene()
        x, y, z = owner.mover.position
        radius = owner.mover.data.stand_radius
        self.install(run, self.floor(x, y, z) + box(
            x + radius * .5, x + radius, y + 4.6, y + 4.8, z - 1., z + 1.,
        ))
        pieces, _ = self.place(run, owner, distance=.25)
        self.assertEqual(len(pieces), 4)
        self.assertTrue(all(abs(piece.feet[0] - x) > 1. for piece in pieces))
        self.assertGreater(run.match.combat.walls.instance(owner).vars[61].value(), 11.9)
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(owner.mover.position[1], y)

    def test_lift_checks_the_swept_path_even_when_the_destination_is_clear(self):
        run, owner, occupant, _ = self.scene()
        x, y, z = owner.mover.position
        walls = run.match.combat.walls
        walls.place_player(occupant, (x + .65, y, z + 3.))
        self.install(run, self.floor(x, y, z) + box(
            x + .85, x + 1.1, y + 3., y + 3.2, z + 2., z + 4.,
        ))
        pieces, _ = self.place(run, owner)
        self.assertEqual(len(pieces), 3)
        self.assertTrue(all(abs(piece.feet[0] - x) > 1. for piece in pieces))
        self.assertAlmostEqual(occupant.mover.position[1], y)
        self.assertGreater(walls.instance(owner).vars[61].value(), 11.9)

    def test_wall_lifts_occupant_whose_feet_round_below_the_floor(self):
        run, owner, occupant, _ = self.scene()
        x, y, z = owner.mover.position
        floor_y = y + .00035
        walls = run.match.combat.walls
        walls.place_player(occupant, (x, floor_y, z + 3.))
        self.install(run, self.floor(x, floor_y, z))

        self.assertEqual(occupant.mover.position[1], y)
        pieces, _ = self.place(run, owner)
        self.assertEqual(len(pieces), 5)
        self.assertAlmostEqual(pieces[2].feet[1], floor_y)
        self.assertAlmostEqual(occupant.mover.position[1], floor_y + 4.4, places=3)



    def test_unsafe_lift_rejects_only_supported_column_then_allows_fresh_confirmation(self):
        run, owner, _, _ = self.scene()
        x, y, z = owner.mover.position
        self.install(run, self.floor(x, y, z, half=.8) + box(
            x - 2., x + 2., y + 4.6, y + 4.8, z - 1., z + 1.,
        ))
        pieces, _ = self.place(run, owner, distance=.25)
        walls = run.match.combat.walls
        cooldown = walls.instance(owner).vars[61]
        self.assertFalse(pieces)
        self.assertTrue(walls.inputs[owner.body].preview)
        self.assertEqual(cooldown.value(), 0.)
        self.assertAlmostEqual(owner.mover.position[1], y)

        walls.place_player(owner, (x, y, z - 3.))
        aim = combat_fixtures.aim(run.match.combat._eye(owner), (x, y, z + .25))
        self.tap(run, owner, 1, aim)
        self.assertEqual(len(walls.pieces), 1)
        self.assertFalse(walls.inputs[owner.body].preview)
        self.assertGreater(cooldown.value(), 11.9)

    def test_lift_clearance_uses_standing_or_crouched_capsule(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, owner, _, _ = self.scene()
                x, y, z = owner.mover.position
                data = owner.mover.data
                roof = y + 4.4 + (data.stand_height + data.crouch_height) / 2
                self.install(run, self.floor(x, y, z) + box(
                    x - 1., x + 1., roof, roof + .2, z - 1., z + 1.,
                ))
                held = CROUCH if crouched else 0
                for _ in range(15):
                    run.step({owner: (0, 0, held)})
                self.assertEqual(owner.mover.crouched, crouched)
                angles = combat_fixtures.aim(run.match.combat._eye(owner), (x, y, z + .25))
                for buttons in (16, 0, 1, 0):
                    run.step({owner: (*angles, buttons | held)})
                self.assertEqual(len(run.match.combat.walls.pieces), 5 if crouched else 4)
                self.assertAlmostEqual(owner.mover.position[1], y + (4.4 if crouched else 0.), places=3)

    def test_supported_centre_with_unsupported_footprint_is_rejected(self):
        run, owner, _enemy, _target = self.scene()
        x, y, z = owner.mover.position
        self.install(run, self.floor(x, y, z, half=.4))
        pieces, _aim = self.place(run, owner)
        self.assertFalse(pieces)
        self.assertEqual(run.match.combat.walls.instance(owner).vars[61].value(), 0.)

    def test_uneven_walkable_terrain_within_existing_foundation_tolerance_creates_five(self):
        run, owner, _enemy, _target = self.scene()
        x, y, z = owner.mover.position
        self.install(run, self.floor(x, y, z, slope=.03))
        pieces, _aim = self.place(run, owner)
        self.assertEqual(len(pieces), 5)
        heights = [p.feet[1] for p in pieces]
        self.assertGreater(max(heights) - min(heights), .15)
        for p in pieces:
            self.assertAlmostEqual(p.feet[1], y + .03 * (p.feet[0] - x), places=4)

    def test_walkable_slope_exceeding_footprint_support_tolerance_rejects_columns(self):
        run, owner, _enemy, _target = self.scene()
        x, y, z = owner.mover.position
        self.install(run, self.floor(x, y, z, slope=.8))
        pieces, _aim = self.place(run, owner)
        self.assertFalse(pieces)
        self.assertEqual(run.match.combat.walls.instance(owner).vars[61].value(), 0.)

    def test_existing_wall_blocks_overlap_but_touching_adjacent_columns_remain_valid(self):
        run, owner, enemy, _target = self.scene()
        x, y, z = owner.mover.position
        # Preexisting opposite-owner wall fixture; the candidate uses actual E/confirm input.
        run.match.combat.walls.create(enemy, [(x, y, z + 3.)], (1., 0., 0.))
        existing = next(iter(run.match.combat.walls.pieces.values()))
        self.place(run, owner)
        created = [p for p in run.match.combat.walls.pieces.values() if p.owner == owner.body]
        self.assertEqual(len(created), 4)
        self.assertEqual(existing.health, 400.)
        self.assertTrue(all(not mei_wall.overlap(p, existing) for p in created))

    def test_oriented_volume_overlap_accepts_touching_faces_and_rejects_crossing(self):
        a = mei_wall.Pillar(1, 1, 0, (0., 0., 0.), (1., 0., 0.), 400., 100)
        b = mei_wall.Pillar(2, 2, 1, (0., 0., 0.), (math.sqrt(.5), 0., math.sqrt(.5)), 400., 100)
        self.assertTrue(mei_wall.overlap(a, b))
        b.feet = (0., a.height, 0.)
        self.assertFalse(mei_wall.overlap(a, b))
        b.feet = (0., 0., 3.)
        self.assertFalse(mei_wall.overlap(a, b))

    def test_two_actual_walls_reject_full_overlap_then_allow_rotated_partial_wall(self):
        run, first, second, _target = self.scene()
        self.assertTrue(run.match.switch_hero(second, heroes()[combat_fixtures.MEI]))
        fight = run.match.combat
        x, y, z = first.mover.position
        fight.walls.place_player(second, (x, y, z - 2.))
        for _ in range(30):
            run.step({})
        first_pieces, first_aim = self.place(run, first)
        self.assertEqual(len(first_pieces), 5)

        aim = combat_fixtures.aim(fight._eye(second), (x, y, z + 3.))
        cooldown = fight.walls.instance(second).vars[61]
        self.tap(run, second, 16, aim)
        self.tap(run, second, 1, aim)
        self.assertEqual(list(fight.walls.pieces.values()), first_pieces)
        self.assertTrue(fight.walls.inputs[second.body].preview)
        self.assertEqual(cooldown.value(), 0.)

        self.tap(run, second, 2, aim)
        self.tap(run, second, 1, aim)
        created = [piece for piece in fight.walls.pieces.values() if piece.owner == second.body]
        self.assertEqual(len(created), 4)
        self.assertTrue(all(not mei_wall.overlap(a, b) for a in created for b in first_pieces))
        self.assertGreater(cooldown.value(), 11.9)
        self.tap(run, first, 16, first_aim)
        self.assertEqual(list(fight.walls.pieces.values()), created)

    def test_same_frame_wall_confirmation_charges_only_one_overlapping_cast(self):
        run, first, second, _ = self.scene()
        self.assertTrue(run.match.switch_hero(second, heroes()[combat_fixtures.MEI]))
        fight = run.match.combat
        x, y, z = first.mover.position
        fight.walls.place_player(second, (x, y, z - 2.0))
        for _ in range(30):
            run.step({})
        point = (x, y, z + 3.0)
        aims = {actor: combat_fixtures.aim(fight._eye(actor), point) for actor in (first, second)}
        run.step({actor: (*aim, 16) for actor, aim in aims.items()})
        run.step({actor: (*aim, 0) for actor, aim in aims.items()})
        run.step({actor: (*aim, 1) for actor, aim in aims.items()})
        run.step({actor: (*aim, 0) for actor, aim in aims.items()})
        pieces = tuple(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        winner = next(actor for actor in (first, second) if actor.body == pieces[0].owner)
        waiting = second if winner is first else first
        self.assertTrue(all(piece.owner == winner.body and piece.health == 400.0 for piece in pieces))
        self.assertGreater(fight.walls.instance(winner).vars[61].value(), 11.9)
        self.assertTrue(fight.walls.inputs[waiting.body].preview)
        self.assertEqual(fight.walls.instance(waiting).vars[61].value(), 0.0)

        self.tap(run, winner, 16, aims[winner])
        self.assertFalse(fight.walls.pieces)
        self.tap(run, waiting, 1, aims[waiting])
        replacement = tuple(fight.walls.pieces.values())
        self.assertEqual(len(replacement), 5)
        self.assertTrue(all(piece.owner == waiting.body for piece in replacement))
        self.assertGreater(fight.walls.instance(waiting).vars[61].value(), 11.9)
        self.assertGreater(fight.walls.instance(winner).vars[61].value(), 11.8)

    def test_wall_top_supports_player_but_does_not_anchor_second_wall(self):
        run, first, second, _target = self.scene()
        self.assertTrue(run.match.switch_hero(second, heroes()[combat_fixtures.MEI]))
        fight = run.match.combat
        x, y, z = first.mover.position
        fight.walls.place_player(second, (x, y, z + 3.))
        for _ in range(30):
            run.step({})
        lower, _aim = self.place(run, first)
        self.assertEqual(len(lower), 5)
        top = lower[2].feet[1] + lower[2].height
        self.assertAlmostEqual(second.mover.position[1], top, places=3)

        down = (0, 16384)
        cooldown = fight.walls.instance(second).vars[61]
        self.tap(run, second, 16, down)
        self.tap(run, second, 1, down)
        self.assertEqual(list(fight.walls.pieces.values()), lower)
        self.assertTrue(fight.walls.inputs[second.body].preview)
        self.assertEqual(cooldown.value(), 0.)

        clear = combat_fixtures.aim(fight._eye(second), (x, y, z + 6.))
        self.tap(run, second, 1, clear)
        upper_owner_pieces = [piece for piece in fight.walls.pieces.values() if piece.owner == second.body]
        self.assertEqual(len(upper_owner_pieces), 5)
        self.assertTrue(all(abs(piece.feet[1] - y) < .001 for piece in upper_owner_pieces))
        self.assertGreater(cooldown.value(), 11.9)

    def test_practice_range_static_props_omit_two_columns_from_same_aim(self):
        path = collision.cache_path(PRACTICE_RANGE.map_guid)
        if collision.cache_version(path) != collision.VERSION:
            self.skipTest("104319 Practice Range collision cache required")
        with path.open('rb') as stream:
            header = collision._read_cache_header(stream)
            meshes, hulls, _models, _placements = collision._unpack_geometry(stream.read(), header)
        core = World(*collision.probe_triangles(meshes, hulls))
        full = World.load(path)
        for scene, expected_x in ((core, (43.25, 44.8, 46.35)), (full, (46.35,))):
            with self.subTest(columns=len(expected_x)):
                run, owner, _enemy, _target = self.scene()
                fight = run.match.combat
                run.match.collision_world = scene
                fight.walls.place_player(owner, (43.25, 1., 36.5))
                for _ in range(20):
                    run.step({})
                aim = combat_fixtures.aim(fight._eye(owner), (43.25, 1., 33.5))
                self.tap(run, owner, 16, aim)
                self.tap(run, owner, 1, aim)
                pieces = sorted(fight.walls.pieces.values(), key=lambda piece: piece.feet[0])
                self.assertEqual(len(pieces), len(expected_x))
                for piece, x in zip(pieces, expected_x, strict=True):
                    self.assertAlmostEqual(piece.feet[0], x, delta=.01)
                    self.assertAlmostEqual(piece.feet[2], 33.5, delta=.01)
                self.assertGreater(fight.walls.instance(owner).vars[61].value(), 11.9)
