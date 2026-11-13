"""Actual 104319 command regressions; synthetic wall, external runtime resources.

Contract: E grants one 175 shield record identified by authored 0x3EBB;
held hook keeps Roll and reports the same world contact for rendering/physics.
These are server assertions; native rendered shield/rope still need client QA.
"""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_delivered_wrecking_ball_combat as ball_combat
import test_server_journal as journal_tests
from test_delivered_wrecking_ball_grapple import hold, scene
from test_game_combat import aim
from test_script_driver import ClientModel

from ow174.game import ball, movelog, observers, world
from ow174.game.commands import Command
from ow174.game.script.driver import BodyStream
from ow174.game.script.expr import f32


class LiveFixTests(unittest.TestCase):
    def test_roll_typed_mover_logging_preserves_command_progress(self):
        run, player, _, _, _ = ball_combat.BallCombatTests().scenario()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(movelog, 'ENABLED', True),
            patch.object(movelog, 'FOLDER', Path(directory)),
        ):
            try:
                run.step({player: (0, 0, 8)})
                run.step({})
                run.step({})
                self.assertTrue(ball.rolling(player))
                self.assertEqual(player.body_script.frame, run.frame)
                path = movelog.log_of(player).path
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                profiles = [mod[2] for row in rows if row['k'] == 'mods' for mod in row['mods']
                            if isinstance(mod[2], dict)]
                self.assertTrue(profiles)
                self.assertEqual(profiles[-1]['run_forward'], 10)
                self.assertEqual(profiles[-1]['stand_radius'], 0.5)
                for _ in range(40):
                    run.step({})
                run.step({player: (0, 0, 1)})
                self.assertFalse(ball.rolling(player))
                self.assertEqual(player.body_script.frame, run.frame)
            finally:
                for owner in run.players:
                    movelog.close(owner)

    def test_cannon_hit_reaches_owner_and_observer_encrypted_peers(self):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, (player, victim) = case.connected(session, sent, first_hero=ball.HERO)
        script = player.body_script
        peers[0].commands(script.frame + 1, 40, 0, now=100.944)
        fight = player.match.combat
        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1.1, z))
        first_packet = len(sent)
        peers[0].commands(script.frame + 1, 1, 1, yaw=yaw, pitch=pitch, now=100.944)
        peers[0].commands(script.frame + 1, 1, 0, yaw=yaw, pitch=pitch, now=100.944)
        for tick in range(12):
            now = 100.96 + tick * 0.016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(fight.vitals[victim.body].total, 195)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        hits = [notice['+0x78'] for notice in peers[0].hit_notices(first_packet)]
        self.assertEqual(hits, [{'+0x0': {'+0x0': victim.body}, '+0x4': 5, '+0x8': 0}])
        for peer in peers:
            self.assertIn((world.OP_UPDATE, 200, 195), peer.health_updates(first_packet, victim.body))
        self.assertTrue(fight.observers.has(victim, player.body))

    def test_held_roll_survives_missing_commands_then_explicit_release_allows_toggle(self):
        run, player, _, _, _ = ball_combat.BallCombatTests().scenario()
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/ball-held-input-gap-v1.json').read_text())
        for step, expected in zip(fixture['input']['steps'], fixture['expected']['rolling_after_step'],
                                  strict=True):
            run.frame += step['frame_delta']
            run.match.tick = run.frame
            player.take_commands([Command(run.frame, buttons=step['buttons'])])
            self.assertEqual(ball.rolling(player), expected, step)

    def test_e_single_native_pool_and_real_damage(self):
        run, player, _, _, _ = ball_combat.BallCombatTests().scenario()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        run.step({player: (0, 0, 16)})
        pools = [p for p in vitals.extra.values() if not p.removed]
        self.assertEqual(len(pools), 1)
        pool = pools[0]
        self.assertEqual((pool.identifier, pool.pool_id, pool.slot), (0x3EBB, 0x3EBB, 2))
        self.assertEqual((vitals.total, pool.now), (775, 175))
        record = vitals.component({world.SHIELDS_POOL})[51][world.SHIELDS_POOL * world.POOLS + pool.slot]
        self.assertEqual(struct.unpack('<IffI4B', record)[:4], (0x3EBB, 175, 175, 2))
        vitals.damage(75)
        for _ in range(4):
            run.step({})
        self.assertEqual((pool.now, vitals.total), (100, 700))
        self.assertEqual(pool.slot, 2)

    def test_hook_contact_render_and_roll_share_static_anchor(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/ball-static-hook-position-v1.json').read_text())
        inputs = fixture['input']
        run, player, _, _, _ = scene(inputs['static_wall_distance'])
        hold(run, player, inputs['frames_to_attach'])
        owned = run.match.combat.ball_grapples[player.body]
        graph = ball.instance(player, ball.GRAPPLE)
        self.assertTrue(owned.attached)
        self.assertEqual(tuple(graph.vars[431].value()), owned.anchor)
        self.assertEqual(tuple(graph.vars[1220].value()), owned.anchor)
        self.assertEqual(tuple(graph.vars[432].value()), owned.anchor)
        self.assertEqual(tuple(graph.vars[434].value()), owned.normal)
        self.assertTrue(graph.vars[1221].value())
        # Actual command ingestion reconstructs four missing input frames.
        player.take_commands([Command(run.frame + 5, yaw=0, pitch=0, buttons=2, extra=0)])
        self.assertTrue(ball.rolling(player))
        self.assertIn(player.body, run.match.combat.ball_grapples)
        run.frame += 5
        hold(run, player, inputs['attached_followup_frames'])
        self.assertEqual(ball.rolling(player), fixture['expected']['rolling_while_held'])
        self.assertIn(player.body, run.match.combat.ball_grapples)
        self.assertEqual(tuple(graph.vars[432].value()), owned.anchor)
        self.assertIsInstance(owned.rope, float)
        script = player.body_script
        remote = observers.snapshot(script)
        for frame, snapshot in (script.tree_frame(), (observers.full_frame(script, remote), remote)):
            model = ClientModel(ball.HERO)
            update = BodyStream(player.body).full(frame, snapshot)
            model.apply(update)
            self.assertEqual(tuple(model.vars[(graph.id, 432)]), tuple(f32(v) for v in owned.anchor))
        run.step({})
        self.assertEqual(player.body in run.match.combat.ball_grapples,
                         fixture['expected']['released_tether_present'])

    def test_gap_keeps_only_confirmed_secondary_hold(self):
        script = SimpleNamespace(hero=ball.HERO, buttons=2, component=SimpleNamespace(strict=False))
        self.assertEqual(ball.grapple_gap_buttons(script, 2), 2)
        self.assertEqual(ball.grapple_gap_buttons(script, 0), 0)
        self.assertEqual(ball.grapple_gap_buttons(script, 3), 0)
        script.buttons = 0
        self.assertEqual(ball.grapple_gap_buttons(script, 2), 0)
