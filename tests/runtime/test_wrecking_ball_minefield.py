"""Minefield deployment and damage driven by the 104319 Wrecking Ball graph."""

import math
import struct
import unittest
from collections import Counter
from unittest.mock import patch

from ow174.game import combat, projectiles, world
from ow174.game.collision import RayWorld
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import export_server_state, snapshot_digest
from tests.runtime import test_server_journal as journal
from tests.runtime.test_game_combat import Run, aim
from tests.runtime.test_game_entity_lifecycle import sent

MINEFIELD = 0x20


class MinefieldTests(unittest.TestCase):
    def arena(self, names=("Wrecking Ball",)):
        run = Run(names=names)
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        ball, *others = run.players
        self.assertTrue(match.switch_hero(ball, find_hero("Wrecking Ball")))
        x, y, z = ball.mover.position
        for index, player in enumerate(others):
            player.mover.position[:] = x + 25, y, z + index * 2
        for _ in range(80):
            run.step({})
        return run, ball

    def cast(self, run, ball):
        script = ball.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1540.0)
        script.set_ult_charge(cost)
        run.step({ball: (0, 0, MINEFIELD)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0.0)
        launch_speeds = {}
        mines = run.match.combat.projectiles.mines
        for _ in range(24):
            run.step({})
            for entity, mine in mines.items():
                if entity not in launch_speeds:
                    if not launch_speeds:
                        x, y, z = ball.mover.position
                        self.assertAlmostEqual(mine.position[0], x)
                        self.assertAlmostEqual(mine.position[1] - y, projectiles.MINE_MUZZLE_HEIGHT)
                        self.assertAlmostEqual(mine.position[2] - z, projectiles.MINE_MUZZLE_FORWARD)
                    launch_speeds[entity] = round(math.hypot(*mine.velocity), 3)
            if len(mines) == 15:
                break
        self.assertEqual(Counter(launch_speeds.values()), {10.0: 1, 11.0: 6, 12.0: 8})
        self.assertEqual({mine.gravity for mine in mines.values()}, {30.0})
        return mines

    def deploy(self, run, ball):
        mines = self.cast(run, ball)
        for _ in range(100):
            run.step({})
            if all(mine.armed_at is not None for mine in mines.values()):
                break
        self.assertEqual(len(mines), 15)
        self.assertTrue(all(mine.armed_at is not None for mine in mines.values()))
        return mines

    def test_authored_volleys_land_and_start_independent_lifetime_clocks(self):
        run, ball = self.arena()
        mines = self.cast(run, ball)
        entities = set(mines)
        self.assertFalse(any(item.entity in entities for item in ball.client.entities))

        # A single large scheduling gap still sweeps the airborne trajectories.
        run.frame += 100
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(set(mines), entities)
        self.assertEqual(len({mine.position for mine in mines.values()}), 15)
        for mine in mines.values():
            self.assertAlmostEqual(mine.position[1], ball.mover.position[1])
            self.assertEqual(mine.armed_at - mine.tick, projectiles.MINE_ARM_TICKS)
            self.assertGreaterEqual((mine.expires - mine.tick) * combat.TICK, 18.0)
            self.assertLess((mine.expires - mine.tick) * combat.TICK, 20.0 + combat.TICK)
        creates = [item for item in ball.client.entities if item.entity in entities]
        self.assertEqual({item.entity for item in creates}, entities)
        self.assertTrue(all(item.op == world.OP_CREATE for item in creates))
        with patch.object(world, "create", wraps=world.create) as build:
            creates[0].build(0)
        self.assertEqual(build.call_args.args[1], projectiles.MINE_ENTITY)

        run.frame = max(mine.expires for mine in mines.values())
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(mines, {})
        self.assertEqual(
            Counter(item.entity for item in ball.client.entities
                    if item.entity in entities and item.op == world.OP_DESTROY),
            Counter(entities),
        )

    def test_lifetime_uses_the_authored_owner_scale(self):
        run, ball = self.arena()
        ball.body_script.component.set(projectiles.MINE_LIFETIME_SCALE, 0.5)
        mines = self.deploy(run, ball)
        for mine in mines.values():
            seconds = (mine.expires - mine.tick) * combat.TICK
            self.assertGreaterEqual(seconds, 9.0)
            self.assertLess(seconds, 10.0 + combat.TICK)

    def test_armed_mine_only_triggers_for_visible_enemies(self):
        run, ball = self.arena(("Ball", "Enemy", "Ally"))
        enemy, ally = run.players[1:]
        ally.team = ball.team
        fight = run.match.combat
        mines = self.deploy(run, ball)
        mine = next(iter(mines.values()))
        for other in tuple(mines.values()):
            if other is not mine:
                fight.projectiles.remove_mine(other)

        x, y, z = mine.position
        enemy.mover.position[:] = x + 1.0, y, z
        ally.mover.position[:] = x - 1.0, y, z
        targets = {target.owner: target for target in fight._targets()}
        enemy_vitals = fight.vitals_of(targets[enemy])
        ally_vitals = fight.vitals_of(targets[ally])
        run.frame = mine.armed_at - 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(enemy_vitals.total, 200.0)

        wall_x = x + 0.5
        # The obstruction participates in the 002C ray profile but not mover collision.
        run.match.collision_world.ray_geometry = RayWorld([
            (wall_x, y - 1, z - 3, wall_x, y + 3, z - 3, wall_x, y + 3, z + 3),
            (wall_x, y - 1, z - 3, wall_x, y + 3, z + 3, wall_x, y - 1, z + 3),
        ], masks=[0x04, 0x04])
        run.step({})
        self.assertIn(mine.entity, mines)
        self.assertEqual(enemy_vitals.total, 200.0)
        enemy.mover.position[:] = x - 1.0, y, z + 0.25
        run.step({})
        self.assertNotIn(mine.entity, mines)
        self.assertEqual(enemy_vitals.total, 70.0)
        self.assertEqual(ally_vitals.total, 200.0)
        self.assertEqual(fight.vitals_of(targets[ball]).total, 600.0)

    def test_enemy_weapon_can_destroy_unarmed_mine_and_late_viewer_gets_current_health(self):
        run, ball = self.arena(("Ball", "Enemy", "Late Viewer"))
        enemy, late = run.players[1:]
        late.spawned = False
        fight = run.match.combat
        mines = self.deploy(run, ball)
        mine = next(iter(mines.values()))
        for other in tuple(mines.values()):
            if other is not mine:
                fight.projectiles.remove_mine(other)
        x, y, z = mine.position
        ball.spawn = ((x + 20.0, y, z), 0.0)
        ball.mover = ball.new_mover()
        enemy.spawn = ((x, y, z - 2.0), 0.0)
        enemy.mover = enemy.new_mover()
        yaw, pitch = aim(fight._eye(enemy), (x, y + projectiles.MINE_HIT_RADIUS, z))
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertIn(mine.entity, mines)
        self.assertEqual(fight.vitals[mine.entity].total, 30.0)

        late.spawned = True
        run.step({})
        records = [record for record in late.client.entities if record.entity == mine.entity]
        self.assertEqual([record.op for record in records], [world.OP_CREATE])
        with patch.object(world, "create", wraps=world.create) as build:
            records[0].build(0)
        health = build.call_args.args[2][51][0]
        self.assertEqual(struct.unpack("<IffI4B", health)[1:3], (50.0, 30.0))
        for _ in range(20):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertNotIn(mine.entity, mines)
        self.assertNotIn(mine.entity, fight.vitals)
        self.assertFalse(enemy.client.messages(combat.KILL_NOTICE))

    def test_mine_health_waits_for_create_and_destroy_cancels_old_records(self):
        case = journal.ServerJournalTests()
        session, wires = case.make()
        _, players = case.connected(session, wires, two=True)
        owner, enemy = players
        match = owner.match
        fight = match.combat
        store = fight.projectiles
        tick = match.tick
        mine = projectiles.Mine(
            projectiles.MINE_FIRST, owner, owner.hero.guid, owner.team, tuple(owner.mover.position),
            (0.0, 0.0, 0.0), 30.0, tick, tick + 1200, 1200, armed_at=tick + 94,
        )
        store.mines[mine.entity] = mine
        store._deliver_mine(mine)
        target, = store.combat._enemy_mines(enemy)
        viewer = enemy.client
        create, = (item for item in viewer.entities if item.entity == mine.entity)
        sequence = max(viewer.in_flight, default=0) + 1
        sent(viewer, sequence, [create])
        fight._damage(enemy, target, 10.0, False, None, tick)
        stale = viewer._body_health[mine.entity]
        fight._damage(enemy, target, 10.0, False, None, tick)
        health = viewer._body_health[mine.entity]
        self.assertIsNot(health, stale)
        self.assertFalse(viewer.bot_create_acked(mine.entity))
        self.assertNotIn(health, viewer.entities)

        def digest():
            return snapshot_digest(export_server_state(
                session.server, archives=session._matches.values(),
                collision_provider=session._collision_reads, assets=session.registry,
            ))

        before = digest()
        mine.expires += 1
        self.assertNotEqual(digest(), before)

        viewer.acked(sequence + 1, 1)
        self.assertIn(health, viewer.entities)
        self.assertNotIn(stale, viewer.entities)
        sent(viewer, sequence + 1, [health])
        fight._damage(enemy, target, 30.0, False, None, tick)
        destroy, = (item for item in viewer.entities if item.entity == mine.entity)
        self.assertEqual(destroy.op, world.OP_DESTROY)
        sent(viewer, sequence + 2, [destroy])
        viewer.acked(sequence + 3, 0b1)  # destroyed; previous health datagram was lost
        self.assertFalse(any(item.entity == mine.entity for item in viewer.entities))
        self.assertNotIn(mine.entity, viewer._body_health)
        self.assertNotIn(mine.entity, viewer._player_body_reuse_after)
