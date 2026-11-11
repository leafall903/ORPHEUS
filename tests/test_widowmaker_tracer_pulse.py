"""Actual Tracer Pulse Bomb input against Widowmaker."""

import os
import unittest
from dataclasses import replace

from test_game_combat import aim, in_world
from test_game_mover import box
from test_widowmaker_combat import _WidowmakerMatch

from ow174.game import brigitte_shield, combat, mei_frost, pulse_bomb, rein_barrier, server_snapshot, sigma
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class WidowmakerPulseBombTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.b, heroes()[pulse_bomb.HERO]))
        self.a.mover.position[:] = (0, 0, 3)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.manager = self.fight.pulse_bombs
        self.defender = None
        self.defender_buttons = 0

    @property
    def cost(self):
        return self.b.body_script.component.entity_var(338).value()

    @property
    def charge(self):
        return self.b.body_script.component.entity_var(342).value()

    def step(self, inputs=None):
        posed = {self.defender: (-32768, 0, self.defender_buttons)} if self.defender is not None else {}
        posed.update(inputs or {})
        self.run.step(posed)

    def settle(self, count):
        for _ in range(count):
            self.step()

    def charge_normally(self):
        self.assertGreater(self.cost, 0)
        start = self.run.frame
        while self.charge < self.cost:
            self.assertLess(self.run.frame - start, 3900)
            first = self.run.frame + 1
            self.run.frame += 32
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
            for player in self.run.players:
                player.take_commands([Command(frame) for frame in range(first, self.run.frame + 1)])

    def launch(self):
        self.step({self.b: (0, 0, 32)})
        self.assertEqual(self.manager.bombs, [])
        for _ in range(20):
            if self.manager.bombs:
                break
            self.settle(1)
        self.assertEqual(len(self.manager.bombs), 1)
        bomb = self.manager.bombs[0]
        self.assertAlmostEqual(bomb.velocity[1] / bomb.velocity[2], .2, places=6)
        return bomb

    def contact(self, bomb):
        for _ in range(75):
            if bomb.stuck:
                return
            self.settle(1)
        self.fail(f"Pulse Bomb did not contact a surface: {bomb.position}")

    def test_actual_charge_gate_attachment_fuse_lethality_and_no_recast(self):
        self.step({self.b: (0, 0, 32)})
        self.settle(20)
        self.assertEqual(self.manager.bombs, [])
        self.charge_normally()
        bomb = self.launch()
        self.assertAlmostEqual(bomb.blast.amount, 350)
        self.contact(bomb)
        self.assertEqual(bomb.attached, self.a.body)
        self.assertEqual(self.health(self.a), 170)
        self.assertGreater(bomb.fuse_left, .98)
        self.a.mover.position[0] += 1
        self.a.mover.body = None
        before = bomb.position[0]
        self.settle(2)
        self.assertAlmostEqual(bomb.position[0] - before, 1, places=3)
        self.b.mover.position[2] = -8
        self.b.mover.body = None
        owner_frame = self.b.body_script.frame
        for index in range(70):
            self.run.frame += 1
            tick = self.run.frame
            self.match.update(tick * combat.TICK, tick)
            self.a.take_commands([Command(tick)])
            if index == 44:
                self.assertEqual(self.health(self.a), 170)
        self.assertEqual(self.b.body_script.frame, owner_frame)
        self.assertEqual(self.health(self.a), 0)
        self.assertEqual(self.manager.bombs, [])
        self.assertEqual(self.health(self.b), 150)
        self.assertLess(self.charge, 50)
        self.step({self.b: (0, 0, 32)})
        self.settle(20)
        self.assertEqual(self.manager.bombs, [])

    def test_static_wall_rejects_attachment_and_explosion_los(self):
        self.b.body_script.set_ult_charge(self.cost)
        world = World(box(-20, 20, -1, 0, -20, 20) + box(-8, 8, 0, 6, 1, 1.4))
        self.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        bomb = self.launch()
        self.contact(bomb)
        self.assertIsNone(bomb.attached)
        self.assertLess(bomb.position[2], 1)
        self.settle(80)
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(self.manager.bombs, [])

    def test_ally_is_not_stuck_or_damaged(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.a.team = self.b.team
        self.b.body_script.set_ult_charge(self.cost)
        bomb = self.launch()
        self.contact(bomb)
        self.assertIsNone(bomb.attached)
        self.settle(80)
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(self.manager.bombs, [])

    def add_player(self, hero, team, position):
        player = self.match.add_player(3, "Defender", hero.guid, team, False)
        in_world(self.match, player)
        self.assertTrue(self.match.switch_hero(player, hero))
        player.mover.position[:] = position
        player.mover.body = None
        self.run.players.append(player)
        self.settle(10)
        self.defender = player
        return player

    def test_genji_returns_a_friendly_bomb_into_widowmaker(self):
        self.b.team = self.a.team
        self.a.mover.position[:] = (0, 0, -2)
        self.a.mover.body = None
        genji = self.add_player(heroes()[0x02E0000000000029], 1 - self.a.team, (0, 0, 3))
        self.run.step({genji: (-32768, 0, 16)})
        self.settle(4)
        self.b.body_script.set_ult_charge(self.cost)
        bomb = self.launch()
        self.contact(bomb)
        self.assertIs(bomb.shooter, genji)
        self.assertEqual(bomb.attached, self.b.body)
        self.assertEqual(self.health(genji), 200)
        self.settle(80)
        self.assertLess(self.health(self.a), 175)
        self.assertEqual(self.manager.bombs, [])

    def test_sigma_grasp_consumes_bomb_before_widowmaker_contact(self):
        grasp = self.add_player(heroes()[sigma.HERO], self.a.team, (0, 0, 2))
        self.step({grasp: (-32768, 0, 8)})
        self.settle(3)
        self.b.body_script.set_ult_charge(self.cost)
        bomb = self.launch()
        self.assertTrue(self.fight.projectile_catalog.absorbable(bomb.definition))
        self.settle(30)
        self.assertEqual(self.manager.bombs, [])
        self.assertFalse(bomb.stuck)
        self.assertEqual(self.health(self.a), 175)
        instance = next(i for i in grasp.body_script.component.instances.values()
                        if i.graph.index == sigma.GRASP)
        self.assertAlmostEqual(instance.vars[21312].value(), 355)

    def test_actual_ice_wall_receives_blast_and_occludes_widowmaker(self):
        mei = self.add_player(heroes()[mei_frost.HERO], self.a.team, (0, 0, 9))
        placement = aim(self.fight._eye(mei), (0, 0, 1.8))
        for buttons in (16, 0, 1, 0):
            self.step({mei: (*placement, buttons)})
        self.assertEqual(len(self.fight.walls.pieces), 5)
        centre = min(self.fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        self.b.body_script.set_ult_charge(self.cost)
        bomb = self.launch()
        self.contact(bomb)
        self.assertIsNone(bomb.attached)
        self.settle(80)
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(centre.health, 50)
        self.assertEqual(self.manager.bombs, [])

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_BARRIER_GEOMETRY"), "requires shield mesh")
    def test_bomb_follows_raised_reinhardt_shield_and_damages_it_once(self):
        self.a.mover.position[2] = 6
        self.a.mover.body = None
        rein = self.add_player(heroes()[rein_barrier.REINHARDT], self.a.team, (0, 0, 4))
        self.defender_buttons = 2
        self.settle(20)
        shield = rein_barrier.active(rein)
        self.assertIsNotNone(shield)
        self.b.body_script.set_ult_charge(self.cost)
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.attached_shield, ("rein", rein.body))
        self.assertIsNone(bomb.attached)
        self.assertEqual(shield.vars[586].value(), 1595)
        before = bomb.position[0]
        rein.mover.position[0] += 1
        rein.mover.body = None
        self.settle(2)
        self.assertAlmostEqual(bomb.position[0] - before, 1, places=3)
        self.settle(80)
        self.assertEqual(shield.vars[586].value(), 1245)
        self.assertEqual(self.health(self.a), 175)

    def test_brigitte_shield_releases_bomb_at_last_position_when_lowered(self):
        self.a.mover.position[2] = 6
        self.a.mover.body = None
        brig = self.add_player(heroes()[brigitte_shield.HERO], self.a.team, (0, 0, 4))
        self.defender_buttons = 2
        self.settle(20)
        self.b.body_script.set_ult_charge(self.cost)
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.attached_shield, ("brigitte", brig.body), (bomb.position, bomb.attached))
        shield = self.fight.brigitte_shields.by_body[brig.body]
        self.assertEqual(shield.health, 245)
        before = bomb.position[0]
        brig.mover.position[0] += 1
        brig.mover.body = None
        self.settle(2)
        self.assertAlmostEqual(bomb.position[0] - before, 1, places=3)
        self.defender_buttons = 0
        self.settle(3)
        self.assertIsNone(bomb.attached_shield)
        fixed = bomb.position
        brig.mover.position[0] += 6
        brig.mover.body = None
        self.settle(80)
        self.assertEqual(bomb.position, fixed)
        self.assertLess(self.health(self.a), 175)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires shield mesh")
    def test_sigma_barrier_recall_detaches_bomb_without_later_shield_damage(self):
        self.a.mover.position[2] = 6
        self.a.mover.body = None
        owner = self.add_player(heroes()[sigma.HERO], self.a.team, (0, 0, 6))
        self.defender_buttons = 2
        self.settle(13)
        self.defender_buttons = 0
        self.settle(37)
        barrier, = self.fight.sigma_barriers.actors.values()
        self.assertTrue(barrier.active)
        self.b.body_script.set_ult_charge(self.cost)
        bomb = self.launch()
        self.contact(bomb)
        self.assertEqual(bomb.attached_shield, ("sigma", barrier.entity))
        self.assertEqual(barrier.health, 695)
        self.step({owner: (-32768, 0, 2)})
        self.settle(3)
        self.assertIsNone(bomb.attached_shield)
        fixed = bomb.position
        self.settle(80)
        self.assertEqual(bomb.position, fixed)
        self.assertEqual(barrier.health, 695)

    def test_in_flight_state_serializes_and_body_replacement_retires_it(self):
        self.b.body_script.set_ult_charge(self.cost)
        self.launch()
        boundaries = {
            'combat': self.fight, 'match': self.match,
            'caster_peer': self.b.client, 'target_peer': self.a.client,
        }
        graph = server_snapshot._Graph(boundaries, server_snapshot.WorldRegistry())
        graph.value(self.manager)
        graph.finish()
        self.assertTrue(self.match.switch_hero(self.b, self.a.hero))
        self.assertEqual(self.manager.bombs, [])
        self.settle(85)
        self.assertEqual(self.health(self.a), 175)
