"""Exercise the authored 104319 Adaptive Shield graph through real match commands."""

import struct
import unittest
from unittest.mock import patch

from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.content import find_hero
from ow174.game.script.driver import HUD_HEALTH, HUD_MAX_HEALTH
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, POOLS, SHIELDS_POOL
from tests.runtime.test_game_combat import Run

ROLL_BUTTON = 0x08
SHIELD_BUTTON = 0x10
SHIELD_ID = 0x3EBB
TEMPORARY_SLOT = SHIELDS_POOL * POOLS + 2


class AdaptiveShieldTests(unittest.TestCase):
    def setup_match(self, names=("Ball",)):
        run = Run(names=names)
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        ball = run.players[0]
        self.assertTrue(match.switch_hero(ball, find_hero("Wrecking Ball")))
        for _ in range(80):
            run.step({})
        target = next(target for target in match.combat._targets() if target.owner is ball)
        return run, ball, target

    def pool(self, vitals):
        raw = vitals.component(vitals.most)[51][TEMPORARY_SLOT]
        return None if raw is None else struct.unpack("<IffI4B", raw)[:4]

    def test_cast_and_expiry_are_replicated_through_health_updates(self):
        run, ball, target = self.setup_match()
        vitals = run.match.combat.vitals_of(target)
        self.assertIsNone(self.pool(vitals))
        run.step({ball: (0, 0, SHIELD_BUTTON)})
        self.assertEqual(self.pool(vitals), (SHIELD_ID, 100.0, 100.0, SHIELDS_POOL))
        self.assertEqual(vitals.total, 700.0)
        script = ball.body_script
        self.assertEqual(script.component.entity_var(HUD_HEALTH).value(), 700.0)
        self.assertEqual(script.component.entity_var(HUD_MAX_HEALTH).value(), 700.0)
        export_body_vm(script)  # the new pool state, target-count binding and active ultimate meter
        with patch.object(ball.client, "queue_body_health", wraps=ball.client.queue_body_health) as queue:
            run.step({})
            components = queue.call_args.args[1]
            expected = vitals.component(vitals.most)[51][TEMPORARY_SLOT]
            self.assertEqual(components[51][TEMPORARY_SLOT], expected)
            for _ in range(550):
                run.step({})
            self.assertEqual(vitals.total, 700.0)
            for _ in range(15):
                run.step({})
            self.assertEqual(self.pool(vitals), (SHIELD_ID, 0.0, 0.0, SHIELDS_POOL))
            self.assertEqual(vitals.total, 600.0)
            self.assertEqual(script.component.entity_var(HUD_MAX_HEALTH).value(), 600.0)
            components = queue.call_args.args[1]
            expected = vitals.component(vitals.most)[51][TEMPORARY_SLOT]
            self.assertEqual(components[51][TEMPORARY_SLOT], expected)
            # A newer health snapshot can replace an unacknowledged removal.
            vitals.damage(20.0)
            run.step({})
            self.assertEqual(queue.call_args.args[1][51][TEMPORARY_SLOT], components[51][TEMPORARY_SLOT])

    def test_enemy_count_is_sampled_at_cast_and_the_pool_absorbs_damage_without_healing(self):
        run, ball, target = self.setup_match(("Ball", "Near Enemy", "Far Enemy", "Ally"))
        _, near, far, ally = run.players
        ball.team = ally.team = 1
        near.team = far.team = 2
        x, y, z = ball.mover.position
        near.mover.position[:] = (x + 4, y, z)
        far.mover.position[:] = (x + 12, y, z)
        ally.mover.position[:] = (x + 4, y, z + 1)
        vitals = run.match.combat.vitals_of(target)
        run.step({ball: (0, 0, SHIELD_BUTTON)})
        self.assertEqual(self.pool(vitals), (SHIELD_ID, 175.0, 175.0, SHIELDS_POOL))
        far.mover.position[:] = (x + 5, y, z)
        run.step({})
        self.assertEqual(vitals.damage(150.0), 150.0)
        self.assertEqual(self.pool(vitals), (SHIELD_ID, 175.0, 25.0, SHIELDS_POOL))
        self.assertEqual(vitals.heal(100.0), 0.0)
        self.assertEqual((vitals.now[HEALTH_POOL], vitals.now[ARMOUR_POOL]), (500.0, 100.0))
        run.step({ball: (0, 0, SHIELD_BUTTON)})
        self.assertEqual(self.pool(vitals), (SHIELD_ID, 175.0, 25.0, SHIELDS_POOL))
        run.match.combat._damage(near, target, 2000.0, False, None, run.frame)
        self.assertTrue(vitals.dead)
        self.assertEqual(self.pool(vitals), (SHIELD_ID, 0.0, 0.0, SHIELDS_POOL))

    def test_casting_shield_preserves_roll_form(self):
        run, ball, target = self.setup_match()
        vitals = run.match.combat.vitals_of(target)
        run.step({ball: (0, 0, ROLL_BUTTON)})
        run.step({})
        roll_profile = ball.mover.script_data
        self.assertEqual(roll_profile.run_forward, 10.0)

        run.step({ball: (0, 0, SHIELD_BUTTON)})
        run.step({})
        self.assertEqual(vitals.total, 700.0)
        self.assertEqual(ball.mover.script_data, roll_profile)

    def test_body_retirement_clears_a_live_pool(self):
        run, ball, target = self.setup_match()
        vitals = run.match.combat.vitals_of(target)
        run.step({ball: (0, 0, SHIELD_BUTTON)})
        ball.body_script.retire()
        self.assertEqual(self.pool(vitals), (SHIELD_ID, 0.0, 0.0, SHIELDS_POOL))
        self.assertEqual(vitals.total, 600.0)
