"""Actual McCree commands against player and training-NPC collision fixtures."""

import dataclasses
import unittest
from dataclasses import replace

from ow174.game import combat, mccree, world
from ow174.game.collision import World, flat_world
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE
from tests.runtime.test_game_combat import Run, aim


class McCreeMeleeTests(unittest.TestCase):
    def arena(self, *, npc=False, wall=False, ally=False, distance=2.0):
        run = Run(names=("McCree", "Target"))
        match = run.match
        attacker, other = run.players
        self.assertTrue(match.switch_hero(attacker, heroes()[0x02E0000000000042]))
        floor = attacker.mover.position[1]
        attacker.mover.position[:] = (0.0, floor, 0.0)
        other.mover.position[:] = (0.0, floor, distance if not npc else 30.0)
        target = other
        if npc:
            target = match.bots[0]
            target.position = (0.0, floor, distance)
            target.route = ()
            target.firing = False
            target.team = 1
            match.bots[:] = [target]
        else:
            match.bots.clear()
        if ally:
            target.team = attacker.team
        collision = flat_world(floor)
        if wall:
            lo, hi = floor, floor + 4.0
            triangles = [
                (-50, floor, -50, -50, floor, 50, 50, floor, 50),
                (-50, floor, -50, 50, floor, 50, 50, floor, -50),
                (-5, lo, 1, 5, lo, 1, 5, hi, 1),
                (-5, lo, 1, 5, hi, 1, -5, hi, 1),
                (-5, lo, 1, 5, hi, 1, 5, lo, 1),
                (-5, lo, 1, -5, hi, 1, 5, hi, 1),
            ]
            collision = World(triangles, name="melee wall fixture")
        match.collision_world = collision
        for player in run.players:
            player.mover.use(world=collision)
        for _ in range(20):
            run.step({})
        return run, attacker, target

    def strike(self, run, attacker, target):
        position = target.mover.position if hasattr(target, "mover") else target.position
        yaw, pitch = aim(run.match.combat._eye(attacker),
                         (position[0], position[1] + 1.0, position[2]))
        run.step({attacker: (yaw, pitch, 0x800)})
        for _ in range(40):
            run.step({attacker: (yaw, pitch, 0)})

    def health(self, run, target):
        body = next(item for item in run.match.combat._targets() if item.owner is target)
        return run.match.combat.vitals_of(body).total

    def test_visible_enemy_player_and_npc_take_one_thirty_damage_hit(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, attacker, target = self.arena(npc=npc)
                before = self.health(run, target)
                self.strike(run, attacker, target)
                self.assertEqual(self.health(run, target), before - 30)
                hits = attacker.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [30])

    def test_wall_blocks_enemy_player_and_npc_without_hit_notices(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, attacker, target = self.arena(npc=npc, wall=True)
                before = self.health(run, target)
                self.strike(run, attacker, target)
                self.assertEqual(self.health(run, target), before)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def test_allies_and_out_of_range_enemies_take_no_damage(self):
        for npc in (False, True):
            for options in ({"ally": True}, {"distance": 4.0}):
                with self.subTest(npc=npc, options=options):
                    run, attacker, target = self.arena(npc=npc, **options)
                    before = self.health(run, target)
                    self.strike(run, attacker, target)
                    self.assertEqual(self.health(run, target), before)
                    self.assertFalse(attacker.client.messages(combat.HIT))

    def test_rialto_manual_npc_damage_charges_once_with_no_extra_kill_bonus(self):
        run, attacker, victim = self.arena(npc=True)
        # Exercise the real map policy in both the generic charge dispatcher
        # and the Cassidy sandbox adapter, rather than patching only one ID.
        run.match.game_map = replace(run.match.game_map, map_guid=mccree.RIALTO)
        victim.spawner = {**victim.spawner, "manual_spawn": True}
        fight = run.match.combat
        target = next(item for item in fight._targets() if item.owner is victim)
        fight.vitals_of(target).now[world.HEALTH_POOL] = 35.0
        script = attacker.body_script
        script.set_ult_charge(0.0)
        meter = script.component.entity_var(ULT_CHARGE)
        fight._damage(attacker, target, 30.0, False, None, run.frame)
        self.assertEqual(meter.value(), 30.0)
        fight._damage(attacker, target, 30.0, False, None, run.frame)
        self.assertEqual(meter.value(), 35.0)
        self.assertTrue(victim.dead)
