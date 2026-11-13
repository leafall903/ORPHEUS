"""Real Echo graph commands and enemy melee exercise the shared force/mover entry."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_echo_movement as movement_tests
from test_game_combat import Run, aim

from ow174.game import combat, echo_movement
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import Mover

EXPECTED = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/echo-flight-force-r2-v1.json').read_text())['expected']


class EchoForceR2Tests(unittest.TestCase):
    def test_authored_strafe_launch_is_consumed_once_and_cancellation_returns_control(self):
        fixture = movement_tests.EchoMovementTests()
        run, player = fixture.session()
        original = player.mover._apply_velocity_mods
        consumed = []

        def apply(mods, normal):
            authored = [mod.value for mod in mods if mod.value == (-28.0, 0.0, 0.0)]
            before = player.mover.velocity
            result = original(mods, normal)
            consumed.extend(authored)
            if authored:
                self.assertAlmostEqual(player.mover.velocity[0] - before[0], -28, delta=.001)
            return result

        with patch.object(player.mover, '_apply_velocity_mods', side_effect=apply):
            fixture.step(run, player, 8, right=127)
            fixture.step(run, player, 8, right=127)
            self.assertLess(player.mover.velocity[0], -27.9)
            for _ in range(18):
                fixture.step(run, player, 8, right=127)
        self.assertEqual(consumed, [tuple(EXPECTED['self_velocity_delta'])])
        self.assertEqual(run.match.combat.vitals_of(next(t for t in run.match.combat._targets()
                                                       if t.owner is player)).total, 200)
        fixture.step(run, player)
        fixture.step(run, player, 8)
        for _ in range(100):
            fixture.step(run, player)
        self.assertIsNone(echo_movement.flight(player))
        self.assertFalse(player.mover.airborne)

    def test_enemy_melee_damages_and_forces_flying_echo_once_on_recipient_clock(self):
        run = Run(movement_tests.SCENE, ('Echo', 'Enemy'))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        victim, attacker = run.players
        for player, epoch in ((victim, 9000), (attacker, 5000)):
            player.commands.last_frame = epoch
            run.match.switch_hero(player, heroes()[echo_movement.ECHO])

        def step(*, flight=False, strike=False):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
            offset = run.frame - 1000
            victim.take_commands([Command(9000 + offset, buttons=8 if flight else 0, forward=127)])
            x, y, z = victim.mover.position
            yaw, pitch = aim(run.match.combat._eye(attacker), (x, y + 1, z))
            attacker.take_commands([Command(5000 + offset, yaw=yaw, pitch=pitch,
                                             buttons=0x800 if strike else 0)])

        for _ in range(30):
            step()
        for _ in range(20):
            step(flight=True)
        self.assertIsNotNone(echo_movement.flight(victim))
        x, y, z = victim.mover.position
        attacker.mover = Mover((x - 2, y, z), world=run.match.collision_world,
                               data=attacker.mover.data)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        original = fight.knockbacks.apply
        with patch.object(fight.knockbacks, 'apply', wraps=original) as apply:
            step(flight=True, strike=True)
        self.assertEqual(fight.vitals_of(target).total, 200 - EXPECTED['melee_damage'])
        self.assertEqual(apply.call_count, EXPECTED['melee_force_applications'])
        recipient, impulse = apply.call_args.args
        self.assertIs(recipient.owner, victim)
        self.assertAlmostEqual(impulse.x, 1, delta=.02)
        self.assertEqual(apply.call_args.kwargs['impact_tick'], run.frame)
        self.assertEqual(apply.call_args.kwargs['source_entity'], attacker.body)
        queued = list(victim.mover.velocity_changes)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].frame, 9000 + run.frame - 1000 + 1)
        self.assertEqual(queued[0].frame, EXPECTED['recipient_next_frame'])
        hit = attacker.client.messages(combat.HIT)
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]['+0x84'], 5000 + run.frame - 1000)
        self.assertEqual(hit[0]['+0x84'], EXPECTED['owner_hit_frame'])
        before = victim.mover.position[0]
        step(flight=True)
        self.assertGreater(victim.mover.position[0], before)
        self.assertFalse(victim.mover.velocity_changes)


if __name__ == '__main__':
    unittest.main()
