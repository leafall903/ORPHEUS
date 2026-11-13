"""Production input batching against a fixed command/world epoch.

Prepared-runtime execution establishes server timing and damage. Original-client
projectile and hit presentation remain pending.
"""

import json
import math
import unittest
from pathlib import Path

from ow174.game import combat
from ow174.game.commands import FIRE, SECONDARY, Command
from tests.runtime import test_zenyatta_combat as fixtures
from tests.runtime.test_game_combat import aim

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/behavior/zenyatta-projectile-clock-v1.json"


class ZenyattaProjectileClockTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def schedule(self, mode, batch, *, target=False):
        run, zen, enemy, _ = fixtures.ZenyattaCombatTests().arena()
        fight = run.match.combat
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z + 20] if target else [x + 100, y, z]
        enemy.mover.body = None
        health = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
        direction = aim(fight._eye(zen), (x, y + 0.75, z + 20)) if target else (0, -6000)
        start = run.frame
        query = self.contract["input"][mode]["query_offset"]
        for first in range(1, query + 1, batch):
            last = min(first + batch - 1, query)
            run.frame = start + last
            run.match.update(run.frame * combat.TICK, run.frame)
            commands = []
            for offset in range(first, last + 1):
                buttons = 0
                if mode == "primary" and offset == 1:
                    buttons = FIRE
                elif mode == "charged" and offset <= 130:
                    buttons = SECONDARY
                commands.append(
                    Command(start + offset, yaw=direction[0], pitch=direction[1], buttons=buttons)
                )
            zen.take_commands(commands)
        return run, zen, health, start

    def test_launch_flight_and_timeout_are_invariant_to_command_batching(self):
        for mode in ("primary", "charged"):
            expected = self.contract["expected"][mode]
            reference = None
            for batch in self.contract["input"]["batch_sizes"]:
                with self.subTest(mode=mode, batch=batch):
                    run, zen, health, start = self.schedule(mode, batch)
                    fight = run.match.combat
                    rockets = list(fight.rockets)
                    self.assertEqual(len(rockets), len(expected["launch_offsets"]))
                    self.assertEqual(
                        [orb.started_tick - start for orb in rockets], expected["launch_offsets"]
                    )
                    for orb, ticks in zip(rockets, expected["flight_ticks"], strict=True):
                        self.assertEqual(orb.last_tick, run.frame)
                        self.assertAlmostEqual(orb.flown, ticks * combat.TICK)
                        self.assertAlmostEqual(orb.left, 2 - ticks * combat.TICK)
                        self.assertEqual(orb.speed, self.contract["expected"]["speed"])
                        self.assertEqual(orb.direct.amount, 48)
                    if reference is None:
                        reference = [orb.position for orb in rockets]
                    else:
                        for orb, position in zip(rockets, reference, strict=True):
                            self.assertLess(math.dist(orb.position, position), 1e-6)
                    self.assertEqual(health.total, 1000)
                    self.assertFalse(zen.client.messages(combat.HIT))
                    timeout = start + self.contract["input"]["timeout_query_offset"]
                    for tick in range(run.frame + 1, timeout + 1):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertEqual(
                        len(fight.rockets), self.contract["expected"]["timeout_live_projectiles"]
                    )
                    self.assertTrue(all(orb.ended == "expired" for orb in rockets))
                    self.assertFalse(fight.projectiles.shots)

    def test_fixed_world_query_agrees_on_hits_damage_and_retirement(self):
        for mode in ("primary", "charged"):
            for batch in self.contract["input"]["batch_sizes"]:
                with self.subTest(mode=mode, batch=batch):
                    run, zen, health, _ = self.schedule(mode, batch, target=True)
                    expected = self.contract["expected"][mode]
                    self.assertEqual(health.total, 1000 - expected["body_damage"])
                    self.assertEqual([packet["+0x78"]["+0x4"] for packet in zen.client.messages(combat.HIT)],
                                     [48] * len(expected["launch_offsets"]))
                    self.assertFalse(run.match.combat.rockets)

    def test_future_input_waits_for_world_admission(self):
        run, zen, _, _ = fixtures.ZenyattaCombatTests().arena()
        future = run.frame + 4
        zen.take_commands([Command(future, pitch=-6000, buttons=FIRE)])
        orb, = run.match.combat.rockets
        self.assertEqual(orb.flown, 0)
        run.match.update((future - 1) * combat.TICK, future - 1)
        self.assertEqual(orb.flown, 0)
        run.match.update(future * combat.TICK, future)
        self.assertEqual(orb.flown, 0)
        run.match.update((future + 1) * combat.TICK, future + 1)
        self.assertEqual(orb.flown, combat.TICK)
        self.assertEqual(orb.last_tick, future + 1)
