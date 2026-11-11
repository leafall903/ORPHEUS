"""Actual Ice Wall placement and Reinhardt static combat contacts."""

import math
import unittest
from dataclasses import replace

import test_game_reinhardt as fixtures
from test_game_combat import aim, in_world

from ow174.game import defenses
from ow174.game.content import SOLDIER, heroes


class ReinhardtIceWallTests(unittest.TestCase):
    health = fixtures.HammerTests.health
    place = staticmethod(fixtures.HammerTests.place)
    advance = fixtures.HammerTests.advance

    def arena(self, *, mei_z=6):
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.place(self.enemy, (0, 1, mei_z))
        self.place(self.other, (20, 1, 0))
        self.advance(6, buttons=0)
        return self.match.combat.walls

    def tap(self, button, angles):
        self.run.step({self.enemy: (*angles, button)})
        self.run.step({self.enemy: (*angles, 0)})

    def wall(self, z=3):
        walls = self.match.combat.walls
        angles = aim(self.match.combat._eye(self.enemy), (0, 1, z))
        self.tap(16, angles)
        self.assertTrue(walls.inputs[self.enemy.body].preview)
        self.tap(1, angles)
        pieces = list(walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        self.assertFalse(walls.inputs[self.enemy.body].preview)
        self.assertGreater(walls.instance(self.enemy).find_var(0, 61).value(), 11)
        return pieces[2]

    def test_fire_strike_hits_one_pillar_and_wall_cancel_reopens_damage(self):
        walls = self.arena()
        centre = self.wall()
        self.advance(1, buttons=16)
        self.advance(65, buttons=0)
        self.assertEqual(centre.health, 300)
        self.assertEqual(self.health(self.enemy), 250)
        self.assertTrue(all(piece.health == 400 for piece in walls.pieces.values() if piece is not centre))
        self.tap(16, (0, 0))
        self.assertFalse(walls.pieces)
        self.assertGreater(walls.instance(self.enemy).find_var(0, 61).value(), 0)
        self.advance(400, buttons=0)
        self.advance(1, buttons=16)
        self.advance(65, buttons=0)
        self.assertEqual(self.health(self.enemy), 150)

    def test_hammer_hits_pillar_once_and_does_not_hit_covered_mei(self):
        self.arena(mei_z=3.8)
        centre = self.wall(2.4)
        self.advance(1)
        self.advance(50, buttons=0)
        self.assertEqual(centre.health, 315)
        self.assertEqual(self.health(self.enemy), 250)

    def test_charge_stops_at_live_wall_and_cannot_capture_covered_body(self):
        self.arena()
        fight = self.match.combat
        centre = self.wall()
        self.advance(1, buttons=8)
        self.advance(70, buttons=0)
        self.assertFalse(fight.charge_drives)
        self.assertFalse(fight.pinned(self.enemy.body))
        self.assertGreater(self.rein.mover.position[2], 1)
        self.assertLess(self.rein.mover.position[2], centre.feet[2])
        self.assertEqual(self.health(self.enemy), 250)
        self.assertEqual(centre.health, 225)
        self.assertTrue(all(piece.health == 400 for piece in fight.walls.pieces.values() if piece is not centre))
        charge = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x007D)
        self.assertEqual(charge.states[58].counter, 1)
        self.assertEqual(charge.vars[6836].value(), ())

    def test_charge_stops_at_friendly_ice_wall_without_damaging_it(self):
        walls = self.arena()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.enemy.team = self.rein.team
        centre = self.wall()
        self.advance(1, buttons=8)
        self.advance(70, buttons=0)
        self.assertFalse(self.match.combat.charge_drives)
        self.assertLess(self.rein.mover.position[2], centre.feet[2])
        self.assertTrue(all(piece.health == 400 for piece in walls.pieces.values()))

    def test_charge_destroys_only_the_hammer_damaged_ice_pillar_and_ends(self):
        walls = self.arena(mei_z=8)
        centre = self.wall(2.4)
        for _ in range(210):
            self.advance(1, buttons=1)
            if centre.health == 145:
                break
        self.assertEqual(centre.health, 145)
        self.advance(1, buttons=0)
        self.advance(1, buttons=8)
        self.advance(72, buttons=0)
        self.assertEqual(centre.health, 0)
        self.assertNotIn(centre.entity, walls.pieces)
        self.assertEqual(len(walls.pieces), 4)
        self.assertFalse(self.match.combat.charge_drives)
        self.assertLess(self.rein.mover.position[2], centre.feet[2])
        self.assertEqual(self.health(self.enemy), 250)

    def test_charge_carries_a_victim_into_a_live_ice_wall_and_releases_it(self):
        self.arena(mei_z=12)
        centre = self.wall(8)
        roadhog = next(hero for hero in heroes().values() if hero.name == "Roadhog")
        self.assertTrue(self.match.switch_hero(self.other, roadhog))
        self.place(self.other, (0, 1, 4))
        fight = self.match.combat
        self.advance(1, buttons=8)
        for _ in range(80):
            self.advance(1, buttons=0)
            if fight.pinned(self.other.body):
                break
        self.assertTrue(fight.pinned(self.other.body))
        self.assertEqual(self.health(self.other), 600)
        for _ in range(50):
            self.advance(1, buttons=0)
            if not fight.pinned(self.other.body):
                break
        self.assertEqual(self.health(self.other), 300)
        self.assertEqual(centre.health, 400)
        self.assertLess(self.other.mover.position[2], centre.feet[2] - centre.depth)
        self.assertLess(self.rein.mover.position[2], self.other.mover.position[2])
        self.assertFalse(fight.pinned(self.other.body))
        self.assertNotIn(self.rein.body, fight.charge_contacts)
        self.assertEqual(self.health(self.enemy), 250)
        self.advance(40, buttons=0)
        self.assertEqual(self.health(self.other), 300)

    def test_shield_bash_stops_at_ice_wall_before_cancel(self):
        walls = self.arena(mei_z=8)
        centre = self.wall(2.5)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[0x02E0000000000195]))
        self.place(self.other, (0, 1, 5.5))
        fight = self.match.combat
        hold = {self.other: (-32768, 0, 2)}
        for _ in range(35):
            self.run.step(hold)
        self.run.step({self.other: (-32768, 0, 3)})
        dash = fight.brigitte_barriers.shields[self.other.body].instance.states[14]
        self.assertTrue(dash.active)
        for _ in range(8):
            self.run.step(hold)
            if not dash.active:
                break
        self.assertFalse(dash.active)
        stopped = tuple(self.other.mover.position)
        self.assertGreater(stopped[2], centre.feet[2] + centre.depth)
        self.assertLess(stopped[2], 5.5)
        self.assertEqual(self.other.mover.velocity[2], 0)
        self.run.step({**hold, self.enemy: (0, 0, 16)})
        self.assertFalse(walls.pieces)
        for _ in range(12):
            self.run.step(hold)
        self.assertEqual(tuple(self.other.mover.position), stopped)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(fight.controlled(self.rein.body))
        self.assertEqual(self.other.client.messages(fixtures.combat.HIT), [])

    def test_rocket_punch_stops_at_ice_wall_covering_reinhardt(self):
        walls = self.arena(mei_z=8)
        centre = self.wall(2.5)
        doom = next(hero for hero in heroes().values() if hero.name == "Doomfist")
        self.assertTrue(self.match.switch_hero(self.other, doom))
        self.place(self.other, (0, 1, 6))
        fight = self.match.combat
        for _ in range(80):
            self.run.step({self.other: (-32768, 0, 2)})
        self.run.step({self.other: (-32768, 0, 0)})
        motion = fight.punches.instance(self.other).states[7]
        self.assertTrue(motion.active)
        self.advance(20, buttons=0)
        self.assertFalse(motion.active)
        self.assertNotIn(self.other.body, fight.punches.dashes)
        self.assertLess(self.other.mover.position[2], 4)
        self.assertGreater(self.other.mover.position[2], centre.feet[2] + centre.depth)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.other), 250)
        self.assertEqual(self.health(self.enemy), 250)
        self.assertFalse(fight.controlled(self.rein.body))
        self.assertNotIn(self.rein.body, fight.punches.impacts)
        self.assertTrue(all(piece.health == 400 for piece in walls.pieces.values()))

    def test_rocket_punch_victim_impacts_live_ice_wall_but_not_cancelled_wall(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                walls = self.arena(mei_z=-6)
                centre = self.wall(-1.5)
                doom = next(hero for hero in heroes().values() if hero.name == "Doomfist")
                self.assertTrue(self.match.switch_hero(self.other, doom))
                self.place(self.other, (0, 1, 5))
                fight = self.match.combat
                for _ in range(80):
                    self.run.step({self.other: (-32768, 0, 2)})
                for _ in range(12):
                    if self.health(self.rein) < 500:
                        break
                    self.run.step({self.other: (-32768, 0, 0)})
                self.assertAlmostEqual(self.health(self.rein), 409.960726, places=5)
                self.assertIn(self.rein.body, fight.punches.impacts)
                if cancel:
                    self.tap(16, (0, 0))
                    self.assertFalse(walls.pieces)
                self.advance(36, buttons=0)
                self.assertNotIn(self.rein.body, fight.punches.impacts)
                self.assertFalse(fight.controlled(self.rein.body))
                if cancel:
                    self.assertAlmostEqual(self.health(self.rein), 409.960726, places=5)
                    self.assertEqual(len(self.other.client.messages(fixtures.combat.HIT)), 1)
                else:
                    self.assertAlmostEqual(self.health(self.rein), 274.882178, places=5)
                    self.assertEqual(len(self.other.client.messages(fixtures.combat.HIT)), 2)
                    self.assertLess(self.rein.mover.position[2], 0)
                    self.assertGreater(self.rein.mover.position[2], centre.feet[2] + centre.depth)
                    self.assertTrue(all(piece.health == 400 for piece in walls.pieces.values()))

    def test_earthshatter_front_stops_on_live_wall(self):
        self.arena()
        self.wall()
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.advance(1, buttons=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.advance(28, buttons=0)
        wave = self.match.combat.shatters[self.rein.body]
        self.advance(10, buttons=0)
        self.assertIn(50, wave.blocked)
        self.advance(37, buttons=0)
        self.assertEqual(self.health(self.enemy), 250)
        self.assertFalse(self.match.combat.controlled(self.enemy.body))

    def test_earthshatter_hits_exposed_body_before_the_live_wall_in_the_same_step(self):
        self.arena(mei_z=12)
        centre = self.wall(9.8)
        yaw = -1820
        angle = yaw * math.tau / 65536 + math.radians(25)
        dx, dz = math.sin(angle), math.cos(angle)
        wall_z = centre.feet[2] - centre.depth
        origin_z = wall_z - 10.82 * dz
        self.place(self.rein, (0, 1, origin_z))
        self.place(self.other, (10.42 * dx + .48 * dz, 1, origin_z + 10.42 * dz - .48 * dx))
        fight = self.match.combat
        exposed = fight._target_entity(self.other.body)
        self.assertGreater(wall_z - exposed.feet[2], exposed.radius)
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.advance(1, buttons=32, yaw=yaw)
        self.advance(44, buttons=0, yaw=yaw)
        wave = fight.shatters[self.rein.body]
        self.assertAlmostEqual(wave.travelled, 10.24, places=4)
        self.assertNotIn(100, wave.blocked)
        self.assertEqual(self.health(self.other), 200)

        self.advance(1, buttons=0, yaw=yaw)
        self.assertIn(100, wave.blocked)
        self.assertAlmostEqual(wave.fronts[100][2], wall_z, places=4)
        self.assertEqual(self.health(self.other), 150)
        self.assertTrue(fight.controlled(self.other.body))
        self.advance(45, buttons=0, yaw=yaw)
        self.assertEqual(self.health(self.enemy), 250)
        self.assertFalse(fight.controlled(self.enemy.body))

    def test_rifle_opens_only_the_ice_wall_lane_the_shatter_has_not_reached(self):
        for flank_ray, break_pillar, expect_hit in ((4, False, False), (4, True, True), (14, True, False)):
            with self.subTest(flank_ray=flank_ray, break_pillar=break_pillar):
                self.check_pillar_removal_during_shatter(flank_ray, break_pillar, expect_hit)

    def check_pillar_removal_during_shatter(self, flank_ray, break_pillar, expect_hit):
        walls = self.arena(mei_z=12)
        self.place(self.enemy, (3, 1, 12))
        centre = self.wall(6)
        self.place(self.enemy, (0, 1, 12))
        angle = math.radians(flank_ray / 2 - 25)
        dx, dz = math.sin(angle), math.cos(angle)
        self.place(self.other, (12 * dx, 1, 12 * dz))
        centre_distance, central_pillar, _ = walls.cast((0, 1.1, 0), (0, 0, 1), 20)
        flank_distance, flank_pillar, _ = walls.cast((0, 1.1, 0), (dx, 0, dz), 20)
        self.assertIs(central_pillar, centre)
        self.assertIsNot(flank_pillar, centre)
        self.assertGreater(flank_distance - centre_distance, 1)
        rifle = self.match.add_player(4, 'Rifle support', SOLDIER, 0, False)
        in_world(self.match, rifle)
        self.assertTrue(self.match.switch_hero(rifle, heroes()[SOLDIER]))
        x, y, z = flank_pillar.feet
        axis_x, _, axis_z = flank_pillar.axis
        self.place(rifle, (x - 3 * axis_z, y, z + 3 * axis_x))
        self.run.players.append(rifle)
        self.advance(20, buttons=0)
        flank_pillar.health = 10
        fight = self.match.combat
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.advance(1, buttons=32)
        for _ in range(60):
            self.advance(1, buttons=0)
            wave = fight.shatters.get(self.rein.body)
            if wave is not None and 50 in wave.blocked:
                break
        self.assertIn(50, wave.blocked)
        self.assertNotIn(flank_ray, wave.blocked)
        centre_stop = wave.fronts[50]
        self.assertEqual(centre.health, 400)
        self.assertEqual(flank_pillar.health, 10)
        if break_pillar:
            angles = aim(fight._eye(rifle), (x, y + 1.5, z))
            for _ in range(5):
                self.run.step({rifle: (*angles, 1)})
                if flank_pillar.entity not in walls.pieces:
                    break
            self.assertNotIn(flank_pillar.entity, walls.pieces)
            self.assertLess(wave.travelled, flank_distance)
            remaining = walls.cast((0, 1.1, 0), (dx, 0, dz), 20)
            self.assertEqual(remaining is None, expect_hit)
            if remaining is not None:
                self.assertGreater(remaining[0], flank_distance)
                self.assertEqual(remaining[1].health, 400)
        self.advance(45, buttons=0)
        self.assertEqual(wave.fronts[50], centre_stop)
        self.assertEqual(flank_ray in wave.blocked, not expect_hit)
        self.assertIn(centre.entity, walls.pieces)
        self.assertEqual(self.health(self.enemy), 250)
        self.assertFalse(fight.controlled(self.enemy.body))
        self.assertEqual(self.health(self.other), 150 if expect_hit else 200)
        self.assertEqual(fight.controlled(self.other.body), expect_hit)

    def test_invalid_preview_cancel_expiry_and_body_retirement(self):
        walls = self.arena()
        sky = (0, -10000)
        self.tap(16, sky)
        self.tap(1, sky)
        self.assertTrue(walls.inputs[self.enemy.body].preview)
        self.assertFalse(walls.pieces)
        self.tap(16, sky)
        self.assertFalse(walls.inputs[self.enemy.body].preview)
        self.wall()
        self.advance(318, buttons=0)
        self.assertFalse(walls.pieces)
        self.arena()
        self.wall()
        old = self.enemy.body
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        walls = self.match.combat.walls
        self.assertNotIn(old, walls.inputs)
        self.assertFalse(walls.pieces)

    def test_fire_strike_breaks_damaged_pillar_and_charge_uses_the_opening(self):
        walls = self.arena(mei_z=3.8)
        centre = self.wall(2.4)
        centre.health = 100  # pre-damaged fixture, with a real final Fire Strike contact
        self.advance(1, buttons=16)
        self.advance(80, buttons=0)
        self.assertNotIn(centre.entity, walls.pieces)
        self.assertEqual(len(walls.pieces), 4)
        self.assertEqual(self.health(self.enemy), 250)
        self.advance(1, buttons=8)
        self.advance(61, buttons=0)
        self.assertTrue(self.match.combat.pinned(self.enemy.body))

    def test_own_wall_blocks_mei_icicle_without_losing_health(self):
        walls = self.arena()
        self.wall()
        angles = aim(self.match.combat._eye(self.enemy), (0, 2, 0))
        self.tap(2, angles)
        for _ in range(65):
            self.run.step({self.enemy: (*angles, 0)})
        self.assertEqual(self.health(self.rein), 500)
        self.assertTrue(all(piece.health == 400 for piece in walls.pieces.values()))
