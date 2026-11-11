"""Mei Ice Wall deployed by actual commands against Pharah's live projectiles."""

import unittest
from dataclasses import replace

import test_pharah_defenses as defense
import test_pharah_mei_blizzard as blizzard

from ow174.game.commands import Command


class PharahIceWallTests(unittest.TestCase):
    def arena(self, rotate=False, lift=False):
        fixture = defense.PharahDefenseTests()
        run, pharah, mei, other = fixture.encounter("Mei")
        other.mover.position[0] = 20
        other.mover.body = None
        fight = run.match.combat
        facing = (-32768, 9500)
        run.step({mei: (*facing, 16)})
        self.assertTrue(fight.walls.inputs[mei.body].preview)
        run.step({mei: (*facing, 2 if rotate else 0)})
        if lift:
            command = Command(run.frame, yaw=facing[0], pitch=facing[1], buttons=1)
            points, _ = fight.walls.placement(mei, command, rotate)
            pharah.mover.position = list(points[2])
            pharah.mover.body = None
        run.step({mei: (*facing, 1)})
        pieces = list(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        self.assertFalse(fight.walls.inputs[mei.body].preview)
        self.assertGreater(fight.walls.instance(mei).vars[61].value(), 11.9)
        return fixture, run, pharah, mei, pieces

    def test_primary_breaks_center_without_leaking_same_explosion(self):
        fixture, run, pharah, mei, pieces = self.arena()
        centre = pieces[2]
        vitals = fixture.health(run, mei)
        fixture.fire(run, pharah)
        self.assertEqual(centre.health, 280)
        self.assertEqual(vitals.total, 250)
        for _ in range(35):
            run.step({})
        centre.health = 20
        fixture.fire(run, pharah)
        self.assertNotIn(centre.entity, run.match.combat.walls.pieces)
        self.assertEqual(vitals.total, 250)
        for _ in range(35):
            run.step({})
        fixture.fire(run, pharah)
        self.assertEqual(vitals.total, 130)

    def test_concussion_is_blocked_and_active_wall_can_be_cancelled(self):
        fixture, run, pharah, mei, pieces = self.arena()
        before = list(mei.mover.position)
        run.step({pharah: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertEqual(mei.mover.position, before)
        ability = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x7B)
        self.assertEqual(ability.states[0].counter, 1)
        self.assertEqual(pieces[2].health, 400)
        run.step({mei: (-32768, 9500, 16)})
        self.assertFalse(run.match.combat.walls.pieces)
        self.assertGreater(run.match.combat.walls.instance(mei).vars[61].value(), 11)

    def test_rotation_changes_the_column_axis_and_pieces_expire(self):
        _, run, _, _, pieces = self.arena(rotate=True)
        self.assertAlmostEqual(abs(pieces[4].feet[2] - pieces[0].feet[2]), 6.2)
        while run.frame < pieces[0].expires:
            run.step({})
        self.assertFalse(run.match.combat.walls.pieces)

    def test_barrage_damages_wall_before_mei(self):
        fixture, run, pharah, mei, pieces = self.arena()
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (0, 0, 32)})
        for _ in range(60):
            run.step({pharah: (0, 0, 0)})
            if any(piece.health < 400 for piece in pieces):
                break
        self.assertTrue(any(piece.health < 400 for piece in pieces))
        self.assertEqual(fixture.health(run, mei).total, 250)

    def test_blizzard_lands_on_own_wall_and_remains_occluded_until_cancel(self):
        fixture, run, pharah, mei, pieces = self.arena()
        area = blizzard.PharahBlizzardTests().launch(run, mei)
        self.assertAlmostEqual(area.point[2], pieces[2].feet[2] + .5, delta=.002)
        vitals = fixture.health(run, pharah)
        for _ in range(30):
            run.step({})
        self.assertEqual(vitals.total, 200)
        run.step({mei: (-32768, 9500, 16)})
        self.assertFalse(run.match.combat.walls.pieces)
        for _ in range(20):
            run.step({})
        self.assertLess(vitals.total, 200)

    def test_hitscan_hits_live_pillar_even_without_a_body_behind_it(self):
        _, run, pharah, mei, pieces = self.arena()
        shooter = run.players[2]
        shooter.mover.position = [0, pharah.mover.position[1], 0]
        shooter.mover.body = None
        for player in (pharah, mei):
            player.mover.position[0] = 20
            player.mover.body = None
        run.step({shooter: (0, 0, 1)})
        for _ in range(12):
            run.step({})
        self.assertLess(pieces[2].health, 400)
        self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not pieces[2]))

    def melee(self, run, pharah):
        run.step({pharah: (0, 0, 0x800)})
        for _ in range(70):
            run.step({})

    def test_melee_breaks_pillar_without_hitting_body_until_a_later_swing(self):
        fixture, run, pharah, mei, pieces = self.arena()
        centre = pieces[2]
        pharah.mover.position[2] = centre.feet[2] - 1
        pharah.mover.body = None
        vitals = fixture.health(run, mei)
        self.melee(run, pharah)
        self.assertEqual(centre.health, 370)
        self.assertEqual(vitals.total, 250)
        centre.health = 20
        self.melee(run, pharah)
        self.assertNotIn(centre.entity, run.match.combat.walls.pieces)
        self.assertEqual(vitals.total, 250)
        self.melee(run, pharah)
        self.assertEqual(vitals.total, 220)

    def test_friendly_pillar_blocks_melee_without_taking_damage(self):
        fixture, run, pharah, mei, pieces = self.arena()
        centre = pieces[2]
        enemy = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = mei.team = centre.team
        enemy.team = 1 - centre.team
        pharah.mover.position[2] = centre.feet[2] - 1
        mei.mover.position[0] = 20
        enemy.mover.position = [0, centre.feet[1], centre.feet[2] + 1]
        for player in run.players:
            player.mover.body = None
        self.melee(run, pharah)
        self.assertEqual(centre.health, 400)
        self.assertEqual(fixture.health(run, enemy).total, 200)

    def walk(self, run, player, frames):
        for _ in range(frames):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
            for actor in run.players:
                actor.take_commands([Command(run.frame, forward=127 if actor is player else 0)])

    def test_live_wall_stops_pharah_walking_until_cancelled(self):
        _, run, pharah, mei, pieces = self.arena()
        centre = pieces[2]
        mei.mover.position[0] = 20
        mei.mover.body = None
        self.walk(run, pharah, 130)
        radius, _ = pharah.mover._capsule()
        boundary = centre.feet[2] - centre.depth - radius
        self.assertAlmostEqual(pharah.mover.position[2], boundary, delta=.015)
        self.assertAlmostEqual(pharah.mover.velocity[2], 0, delta=.02)
        self.assertAlmostEqual(pharah.mover.history[-1].position[2], boundary, delta=.015)
        run.step({mei: (-32768, 9500, 16)})
        self.assertFalse(run.match.combat.walls.pieces)
        self.walk(run, pharah, 50)
        self.assertGreater(pharah.mover.position[2], centre.feet[2] + 1)

    def test_wall_deployment_lifts_pharah_and_cancel_restores_fall(self):
        _, run, pharah, mei, pieces = self.arena(lift=True)
        centre = pieces[2]
        height = centre.feet[1] + centre.height
        self.assertAlmostEqual(pharah.mover.position[1], height, delta=.02)
        for _ in range(35):
            run.step({})
        self.assertAlmostEqual(pharah.mover.position[1], height, delta=.02)
        self.assertFalse(pharah.mover.airborne)
        run.step({mei: (-32768, 9500, 16)})
        for _ in range(90):
            run.step({})
        self.assertAlmostEqual(pharah.mover.position[1], centre.feet[1], delta=.02)
        self.assertFalse(pharah.mover.airborne)

    def test_actual_jump_jet_can_clear_live_wall(self):
        _, run, pharah, mei, pieces = self.arena()
        centre = pieces[2]
        top = centre.feet[1] + centre.height
        pharah.mover.position[2] = centre.feet[2] - 1.3
        mei.mover.position[0] = 20
        pharah.mover.body = mei.mover.body = None
        run.step({pharah: (0, 0, 8)})
        self.assertEqual(pharah.mover.velocity[1], 20)
        crossed = False
        for _ in range(100):
            self.walk(run, pharah, 1)
            if pharah.mover.position[2] > centre.feet[2]:
                self.assertGreaterEqual(pharah.mover.position[1], top - .02)
                crossed = True
                break
        self.assertTrue(crossed)
        self.assertIn(centre.entity, run.match.combat.walls.pieces)
