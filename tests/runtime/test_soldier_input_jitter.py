"""Accepted Soldier input survives a sustained increase in local packet delay."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import SOLDIER
from ow174.game.script import expr, nodes
from ow174.game.script.decode import ClientBody
from ow174.game.script.driver import BodyStream
from tests.runtime.test_game_combat import Run, aim


class SoldierInputJitterTests(unittest.TestCase):
    def test_visor_turn_and_fire_publishes_the_registered_target_in_the_same_owner_frame(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/soldier-visor-aim-publication-v1.json'
        contract = json.loads(path.read_text())
        inputs, expected = contract['input'], contract['expected']
        run = Run(names=('Soldier', 'Enemy'))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        soldier, enemy = run.players
        for player, position in ((soldier, inputs['owner_position']), (enemy, inputs['enemy_position'])):
            player.mover.position = list(position)
            player.mover.body = None
            player.body_script.create_arrived = True
        away = inputs['preceding_yaw'], inputs['pitch'], 0
        for _ in range(40):
            run.step({soldier: away})
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        run.step({soldier: (away[0], away[1], 32)})
        for _ in range(90):
            run.step({soldier: away})
        search = next(state for instance in script.component.instances.values()
                      if instance.graph.index == 0x0259 for state in instance.states.values()
                      if state.active and isinstance(state, nodes.TrackTargets)
                      and state.node.field('m_8A2905A0.m_identifier') == 2337)
        for _ in range(4):
            if search.last_refresh == script.frame:
                break
            run.step({soldier: away})
        self.assertEqual(search.last_refresh, script.frame)
        self.assertEqual(script.component.entity_var(2337).value(), ())
        weapon = next(instance for instance in script.component.instances.values()
                      if instance.graph.index == 0x0254)
        handle = weapon.find_var(0, 49).value()
        self.assertIsInstance(handle, expr.Handle)
        client, stream = ClientBody.for_hero(SOLDIER), BodyStream(soldier.body)
        update = stream.full(*script.tree_frame())
        client.apply(update)
        stream.arrived(update.chunk_last)
        pool = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is enemy))
        before = pool.total
        run.step({soldier: (inputs['shot_yaw'], inputs['pitch'], inputs['press'])})
        self.assertEqual(before - pool.total, expected['damage'])
        self.assertEqual(script.frame - search.last_refresh, inputs['shot_after_idle_refresh_frames'])
        ranked = (expr.Entity(enemy.body),)
        self.assertEqual(
            script.component.entity_var(expected['native_launch_target_variable']).value(), ranked)
        payload, keys, snapshot = script.delta(stream.base, stream.touched(), script.frame, False)
        update = stream.delta(payload, snapshot, keys, script.frame)
        client.apply(update)
        self.assertEqual(client.vars[(0, 2337)], ranked)
        self.assertEqual(weapon.find_var(0, 49).value(), handle)
        for _ in range(8):
            run.step({soldier: (away[0], away[1], FIRE)})
        self.assertEqual(pool.total, before - expected['damage'])
        self.assertEqual(script.component.entity_var(2337).value(), ())

    def test_helix_and_field_launch_clocks_survive_full_command_packets(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/soldier-command-batches-v1.json'
        contract = json.loads(path.read_text())
        inputs = contract['input']
        for case in contract['cases']:
            ability = case['button']
            observations = []
            for batch in inputs['batch_sizes']:
                with self.subTest(ability=ability, batch=batch):
                    run = Run()
                    run.match.bots.clear()
                    run.match._pending_bot_scripts.clear()
                    player = run.players[0]
                    player.mover.position = list(inputs['position'])
                    player.mover.body = None
                    player.body_script.create_arrived = True
                    run.match.collision_world = flat_world(inputs['ground_height'])
                    pending = []
                    for tick in range(inputs['start_frame'] + 1, inputs['end_frame'] + 1):
                        run.match.update(tick * combat.TICK, tick)
                        pending.append(Command(tick, buttons=ability if tick == inputs['press_frame'] else 0))
                        if (tick - inputs['start_frame']) % batch == 0 or tick == inputs['end_frame']:
                            player.take_commands(pending)
                            pending = []
                    self.assertEqual(len(run.match.combat.rockets), 1)
                    rocket, = run.match.combat.rockets
                    expected = case['expected']
                    self.assertEqual(rocket.shot.frame, expected['shot_frame'])
                    self.assertEqual(rocket.launch_tick, expected['launch_tick'])
                    self.assertAlmostEqual(rocket.distance, expected['distance'])
                    observations.append((rocket.shot.frame, rocket.launch_tick, rocket.last_tick,
                                         rocket.position, rocket.distance))
            self.assertEqual(observations[0], observations[1])
            self.assertEqual(observations[0], observations[2])

    def test_delay_changes_and_packet_gaps_keep_real_rifle_damage_and_hit_notices(self):
        run = Run(names=("Soldier", "Enemy"))
        soldier, enemy = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.collision_world = flat_world(0)
        soldier.mover.position, enemy.mover.position = [0, 0, 0], [0, 0, 10]
        soldier.mover.body = enemy.mover.body = None
        soldier.body_script.create_arrived = True
        target = next(item for item in match.combat._targets() if item.owner is enemy)
        vitals = combat.Vitals(target.entity, 2000)
        match.combat.vitals[target.entity] = vitals
        enemy.body_script.set_health(2000, 2000)
        yaw, pitch = aim(match.combat._eye(soldier), (0, .8, 10))
        totals = []
        for step in range(1, 461):
            tick = 1000 + step
            match.update(tick * combat.TICK, tick)
            delay = 3 if step < 100 or step >= 270 else 7 if step < 180 else 13
            frame = tick - delay
            if frame > 1000 and step % 25:
                buttons = FIRE if step < 420 else 0
                if step == 230:
                    buttons = 4  # reload the exhausted 30-round magazine
                soldier.take_commands([Command(frame, yaw=yaw, pitch=pitch,
                                               buttons=buttons)])
            if step in (95, 175, 265, 419):
                totals.append(vitals.total)
        decreases = [after < before for before, after in zip([2000, *totals[:-1]], totals, strict=True)]
        self.assertTrue(all(decreases), totals)
        notices = soldier.client.messages(combat.HIT)
        self.assertGreater(len(notices), 20)
        self.assertTrue(all(notice["+0x78"]["+0x0"]["+0x0"] == enemy.body for notice in notices))
        self.assertEqual(sum(notice["+0x78"]["+0x4"] for notice in notices), 2000 - vitals.total)
        self.assertEqual(soldier.body_script.buttons, 0)

    def test_delay_change_keeps_movement_fire_and_release_live_after_create_ack(self):
        run = Run()
        player = run.players[0]
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.body_script.create_arrived = True
        positions = {}
        with patch.object(match.combat, "_shoot", wraps=match.combat._shoot) as shoot:
            for step in range(1, 341):
                tick = 1000 + step
                match.update(tick * 0.016, tick)
                delay = 3 if step < 100 else 7
                frame = tick - delay
                buttons = FIRE if step < 300 else 0
                if step in (60, 180):
                    buttons |= JUMP
                if frame > 1000:
                    player.take_commands([Command(frame, forward=127 if step < 300 else 0,
                                                  buttons=buttons)])
                if step in (95, 150, 295):
                    positions[step] = tuple(player.mover.position)
                if step == 150:
                    before = shoot.call_count
            self.assertGreater(positions[295][2] - positions[150][2], 8.0)
            self.assertGreater(positions[150][2] - positions[95][2], 2.0)
            self.assertGreater(shoot.call_count, before + 5)
            self.assertEqual(player.body_script.buttons, 0)
            self.assertEqual(player.mover.throttles, (0, 0))
            self.assertEqual(player.mover.history[-1].frame, player.commands.last_frame)
