"""Identical accepted Hanzo commands must keep their world flight epoch."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hero_tests
from test_game_hanzo_dragons import prepared_profile
from test_game_hanzo_sonic import profile as sonic_profile

from ow174.game import bodytrace, combat, movelog
from ow174.game.commands import Command
from ow174.game.collision import World


class HanzoPacketClockTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def play(self, kind, batch, *, hit=False):
        run, hero, target = hero_tests.HanzoCombatTests().arena()
        fight = run.match.combat
        if not hit:
            target.spawned = False
            run.match.collision_world = World([], name="synthetic empty projectile clock fixture")
        if kind == "sonic":
            fight.configure_sonic(sonic_profile())
        if kind == "dragon":
            fight.configure_dragonstrike(prepared_profile())
            hero.body_script.ult_seconds = 0
            hero.body_script.set_ult_charge(1680)
        start = run.frame
        commands = []
        for offset in range(1, 129):
            buttons = (32 if offset == 1 else 0) if kind == "dragon" else (
                8 if kind == "sonic" and offset == 1 else
                16 if kind == "storm" and offset == 1 else
                1 if 4 <= offset <= 73 else 0)
            commands.append(Command(start + offset, buttons=buttons))
        launches = []
        submit = fight._submit_attack

        def observe(shooter, frame, payload):
            if isinstance(payload, combat.Rocket):
                launches.append((frame, payload.started_tick, payload.last_tick, payload.definition))
            return submit(shooter, frame, payload)

        with patch.object(fight, "_submit_attack", side_effect=observe):
            for offset in range(0, len(commands), batch):
                group = commands[offset:offset + batch]
                tick = group[-1].frame
                run.match.update(tick * combat.TICK, tick)
                hero.take_commands(group)
        query = start + 129
        run.match.update(query * combat.TICK, query)
        result = {
            "launches": launches,
            "rockets": [(r.position, r.flown, r.left, r.started_tick, r.last_tick, r.definition)
                        for r in fight.rockets],
            "dragons": [(r.origin, r.age, r.last_tick) for r in fight.dragons],
            "health": fight.vitals[target.body].total if hit else None,
            "hits": [message["+0x78"]["+0x4"] for message in hero.client.messages(combat.HIT)],
            "sonars": [(b.born, b.position, b.attached) for b in fight.sonars],
        }
        terminal = query + 1400
        run.match.update(terminal * combat.TICK, terminal)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.dragons)
        self.assertFalse(fight.sonars)
        return result

    def assert_same(self, actual, expected):
        if isinstance(expected, dict):
            self.assertEqual(actual.keys(), expected.keys())
            for key in expected:
                self.assert_same(actual[key], expected[key])
        elif isinstance(expected, (tuple, list)):
            self.assertEqual(len(actual), len(expected))
            for first, second in zip(actual, expected, strict=True):
                self.assert_same(first, second)
        elif isinstance(expected, float):
            self.assertAlmostEqual(actual, expected, places=7)
        else:
            self.assertEqual(actual, expected)

    def test_packet_grouping_keeps_flight_and_retirement(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/hanzo-projectile-clock-v1.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for kind, count in fixture["expected"]["launch_counts"].items():
            with self.subTest(kind=kind):
                reference = self.play(kind, 1)
                self.assertEqual(len(reference["launches"]), count)
                self.assertTrue(reference["rockets"] or reference["dragons"])
                for batch in (8, 32):
                    with self.subTest(batch=batch):
                        self.assert_same(self.play(kind, batch), reference)

    def test_packet_grouping_keeps_real_hit_damage_and_sonic_deployment(self):
        for kind in ("bow", "sonic"):
            with self.subTest(kind=kind):
                reference = self.play(kind, 1, hit=True)
                self.assertEqual(reference["health"], 75)
                self.assertEqual(reference["hits"], [125])
                self.assertEqual(len(reference["sonars"]), int(kind == "sonic"))
                for batch in (8, 32):
                    with self.subTest(batch=batch):
                        self.assert_same(self.play(kind, batch, hit=True), reference)
