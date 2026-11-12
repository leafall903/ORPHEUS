"""Actual 104319 command regressions; synthetic wall, external runtime resources.

Contract: E grants one 175 shield record identified by authored 0x3EBB;
held hook keeps Roll and reports the same world contact for rendering/physics.
These are server assertions; native rendered shield/rope still need client QA.
"""
import struct
import unittest
from types import SimpleNamespace

import test_delivered_wrecking_ball_combat as ball_combat
from test_delivered_wrecking_ball_grapple import scene, hold
from ow174.game import ball, world
from ow174.game.commands import Command


class LiveFixTests(unittest.TestCase):
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
        run, player, _, _, _ = scene()
        hold(run, player, 5)
        owned = run.match.combat.ball_grapples[player.body]
        graph = ball.instance(player, ball.GRAPPLE)
        self.assertTrue(owned.attached)
        self.assertEqual(tuple(graph.vars[431].value()), owned.anchor)
        self.assertEqual(tuple(graph.vars[1220].value()), owned.anchor)
        self.assertEqual(tuple(graph.vars[434].value()), owned.normal)
        self.assertTrue(graph.vars[1221].value())
        # Actual command ingestion reconstructs four missing input frames.
        player.take_commands([Command(run.frame + 5, yaw=0, pitch=0, buttons=2, extra=0)])
        self.assertTrue(ball.rolling(player))
        self.assertIn(player.body, run.match.combat.ball_grapples)
        run.frame += 5
        hold(run, player, 20)
        self.assertTrue(ball.rolling(player))
        self.assertIn(player.body, run.match.combat.ball_grapples)
        run.step({})
        self.assertNotIn(player.body, run.match.combat.ball_grapples)

    def test_gap_keeps_only_confirmed_secondary_hold(self):
        script = SimpleNamespace(hero=ball.HERO, buttons=2, component=SimpleNamespace(strict=False))
        self.assertEqual(ball.grapple_gap_buttons(script, 2), 2)
        self.assertEqual(ball.grapple_gap_buttons(script, 0), 0)
        self.assertEqual(ball.grapple_gap_buttons(script, 3), 0)
        script.buttons = 0
        self.assertEqual(ball.grapple_gap_buttons(script, 2), 0)
