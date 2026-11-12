"""Actual Mei Ice Wall placement and Widowmaker commands in build 104319."""

import unittest

from test_game_combat import aim, in_world
from test_widowmaker_combat import FIRE, SCOPE, _WidowmakerMatch

from ow174.game import combat, mei_wall, orisa_halt, server_snapshot
from ow174.game.collision_fingerprint import world_fingerprint
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class WidowmakerIceWallTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[mei_wall.MEI]))
        self.b.mover.position[:] = (0, 0, 10)
        self.b.mover.body = None
        self.fight = self.run.match.combat
        self.walls = self.fight.walls
        self.placement = aim(self.fight._eye(self.b), (0, 0, 5))

    def tap_wall(self, button):
        self.run.step({self.b: (*self.placement, button)})
        self.run.step({self.b: (*self.placement, 0)})

    def place(self):
        self.tap_wall(16)
        self.assertTrue(self.walls.inputs[self.b.body].preview)
        self.tap_wall(1)
        self.assertEqual(len(self.walls.pieces), 5)
        self.assertFalse(self.walls.inputs[self.b.body].preview)
        self.assertGreater(self.walls.instance(self.b).state(33).remaining(), 11.9)
        return min(self.walls.pieces.values(), key=lambda piece: abs(piece.feet[0]))

    def test_actual_wall_takes_primary_and_scoped_damage_then_opens_line(self):
        original = world_fingerprint(self.run.match.collision_world)
        centre = self.place()
        self.press(FIRE)
        self.assertEqual(centre.health, 387)
        self.assertEqual(self.health(self.b), 250)
        self.press(0, 5)
        self.press(SCOPE, 80)
        self.press(SCOPE | FIRE)
        self.assertEqual(centre.health, 267)
        self.press(0, 12)
        for _ in range(195):
            self.press(FIRE)
            if centre.entity not in self.walls.pieces:
                break
        self.assertEqual(centre.health, 0)
        self.assertLess(self.run.frame, centre.expires)
        self.assertNotIn(centre.entity, self.walls.pieces)
        self.assertEqual(self.health(self.b), 250)
        self.press(0, 12)
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 237)
        self.assertEqual(world_fingerprint(self.run.match.collision_world), original)

    def test_actual_mine_attaches_to_pillar_and_retires_with_wall(self):
        centre = self.place()
        for n in range(44):
            self.run.step({self.a: (0, 0, 16 if n == 0 else 0)})
        mine, = self.fight.venom.mines
        self.assertEqual(mine.wall, centre.entity)
        self.assertAlmostEqual(mine.position[2], centre.feet[2] - centre.depth - .02)
        self.tap_wall(16)
        self.assertFalse(self.walls.pieces)
        self.assertFalse(self.fight.venom.mines)
        self.assertGreater(self.walls.instance(self.b).state(33).remaining(), 10)
        self.assertEqual(self.health(self.b), 250)

    def test_actual_grapple_selects_the_nearer_pillar_and_pulls(self):
        centre = self.place()
        for n in range(42):
            self.run.step({self.a: (0, 0, 8 if n == 0 else 0)})
        self.assertGreater(self.a.mover.position[2], 2)
        self.assertLess(self.a.mover.position[2], centre.feet[2])
        self.assertEqual(self.health(self.b), 250)

    def test_wall_health_is_stored_and_expiry_does_not_need_caster_input(self):
        centre = self.place()
        ammo_before = next(i for i in self.b.body_script.component.instances.values()
                           if i.graph.index == 0x05E6).vars[53].value()
        self.assertEqual(ammo_before, 120)

        def stored_wall():
            graph = server_snapshot._Graph({'combat': self.fight}, server_snapshot.WorldRegistry(), {})
            root = graph.value(self.walls)
            return root, graph.finish()

        stored = stored_wall()
        centre.health -= 1
        self.assertNotEqual(stored_wall(), stored)
        centre.health += 1
        frame = self.b.body_script.frame
        self.run.match.update(centre.expires * combat.TICK, centre.expires)
        self.assertFalse(self.walls.pieces)
        self.assertEqual(self.b.body_script.frame, frame)

    def test_removing_wall_cancels_grapple_before_and_after_contact(self):
        for contacted in (False, True):
            with self.subTest(contacted=contacted):
                self.setUp()
                self.place()
                hook = next(i for i in self.a.body_script.component.instances.values()
                            if i.graph.index == 0x005A)
                self.run.step({self.a: (0, 0, 8)})
                for _ in range(18):
                    if self.a.body in self.walls.anchors and hook.state(1).active == contacted:
                        break
                    self.run.step({})
                self.assertIn(self.a.body, self.walls.anchors)
                self.assertEqual(hook.state(1).active, contacted)
                start = self.a.mover.position[2]
                self.tap_wall(16)
                for _ in range(40):
                    self.run.step({})
                self.assertFalse(self.walls.anchors)
                self.assertAlmostEqual(self.a.mover.position[2], start, delta=0.4 if contacted else 0.001)
                self.assertFalse(hook.state(0).active)
                self.assertFalse(hook.state(1).active)
                if contacted:
                    self.assertGreater(hook.vars[61].value(), 9)
                else:
                    self.assertEqual(hook.vars[61].value(), 0)

    def attacker(self, hero):
        self.a.team = self.b.team
        self.a.mover.position[:] = (0, 0, 6.4)
        self.a.mover.body = None
        enemy = self.run.match.add_player(3, 'Attacker', hero, 1 - self.b.team, False)
        in_world(self.run.match, enemy)
        self.assertTrue(self.run.match.switch_hero(enemy, heroes()[hero]))
        enemy.mover.position[:] = (0, 0, 0)
        enemy.mover.body = None
        self.run.players.append(enemy)
        return enemy

    def test_actual_chain_hook_is_occluded_by_ice_wall(self):
        enemy = self.attacker(0x02E0000000000040)
        self.place()
        self.run.step({enemy: (0, 0, 8)})
        launched = False
        for _ in range(50):
            self.run.step({})
            launched |= bool(self.fight.hooks.flights)
            self.assertFalse(self.fight.hooks.controlled(self.a.body))
        self.assertTrue(launched)
        self.assertFalse(self.fight.hooks.flights)
        self.assertEqual(self.health(self.a), 175)

    def test_halt_hits_ice_wall_and_cannot_pull_widow_through_it(self):
        manager = self.fight.halt
        if manager.bundle is None:
            self.skipTest('requires authored Halt metadata')
        enemy = self.attacker(orisa_halt.HERO)
        centre = self.place()
        self.run.step({enemy: (0, 0, 2)})
        for _ in range(30):
            self.run.step({})
            if any(orb.requested is not None for orb in manager.orbs.values()):
                break
        orb, = manager.orbs.values()
        self.assertIsNotNone(orb.requested)
        self.assertAlmostEqual(orb.position[2], centre.feet[2] - centre.depth)
        for _ in range(50):
            self.run.step({})
        self.assertFalse(manager.pulls)
        self.assertAlmostEqual(self.a.mover.position[2], 6.4, delta=0.002)
        self.assertEqual(self.health(self.a), 175)

    def test_blizzard_anchors_on_front_of_ice_wall(self):
        enemy = self.attacker(mei_wall.MEI)
        centre = self.place()
        script = enemy.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({enemy: (0, 0, 32)})
        for _ in range(75):
            self.run.step({})
            if self.fight.blizzard.areas:
                break
        area, = self.fight.blizzard.areas.values()
        self.assertAlmostEqual(area.position[2], centre.feet[2] - centre.depth)
        self.assertGreater(area.position[1], 0.5)
        for _ in range(80):
            self.run.step({})
        self.assertEqual(self.health(self.a), 175)
        self.assertIsNone(self.fight.frost.sample(self.a, self.run.frame))

    def test_actual_helix_impacts_wall_without_splashing_widow_behind_it(self):
        enemy = self.attacker(SOLDIER)
        centre = self.place()
        direction = aim(self.fight._eye(enemy), (0, 1.2, centre.feet[2]))
        self.run.step({enemy: (*direction, SCOPE)})
        for _ in range(30):
            self.run.step({})
        self.assertAlmostEqual(centre.health, 280)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.fight.rockets)

    def test_actual_movement_stops_at_wall_then_crosses_after_removal(self):
        centre = self.place()

        def walk(frames):
            for _ in range(frames):
                self.run.frame += 1
                tick = self.run.frame
                self.run.match.update(tick * combat.TICK, tick)
                self.a.take_commands([Command(tick, forward=127)])
                self.b.take_commands([Command(tick)])

        walk(100)
        edge = centre.feet[2] - centre.depth - self.a.mover.data.stand_radius
        self.assertAlmostEqual(self.a.mover.position[2], edge, delta=0.005)
        self.assertAlmostEqual(self.a.mover.velocity[2], 0, delta=0.01)
        self.tap_wall(16)
        walk(45)
        self.assertGreater(self.a.mover.position[2], centre.feet[2] + centre.depth)
