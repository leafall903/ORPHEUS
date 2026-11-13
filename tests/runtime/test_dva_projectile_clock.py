"""D.Va command batching must preserve elapsed projectile flight."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import test_dva_delivery_created_body as created
from test_game_combat import dva_pilot_input

from ow174.game import dva
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.combat import HIT, TICK, Vitals
from ow174.game.commands import Command
from ow174.game.server_snapshot import _Graph


class DvaProjectileClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    def test_birth_epoch_mapping_contract(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/dva-projectile-clock-v1.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        for row in contract["input"]["cases"]:
            component = SimpleNamespace(frame_ms=row["body_quantum_ms"])
            state = SimpleNamespace(instance=SimpleNamespace(
                graph=SimpleNamespace(index=row["graph"]), component=component),
                node=SimpleNamespace(pos=10))
            actor = SimpleNamespace(hero=dva.HERO, body_script=SimpleNamespace(dva_birth=tuple(row["birth"])))
            with self.subTest(row=row):
                actual = dva.projectile_launch_tick(actor, state, row["event_ms"])
                self.assertEqual(actual, row["launch_tick"])

    def replay(self, pilot_form, batch, hit):
        run, pilot, enemy, _, mech = created.CreatedBodyTests().scenario()
        if pilot_form:
            dva_pilot_input(pilot)
        enemy.spawned = False
        for _ in range(100):
            run.step({})
        fight = run.match.combat
        if hit:
            origin = fight._eye(pilot if pilot_form else mech)
            enemy.mover.position[:] = (origin[0], origin[1] - .8, origin[2] + 8)
            enemy.mover.body = None
            enemy.spawned = True
            fight.vitals[enemy.body] = Vitals(enemy.body, 10000)
        start = run.frame
        for offset in range(0, 64, batch):
            end = min(offset + batch, 64)
            run.match.update((start + end) * TICK, start + end)
            pilot.take_commands([
                Command(start + index, buttons=(1 if pilot_form else 16) if index == 1 else 0)
                for index in range(offset + 1, end + 1)
            ])
        flights = [(r.last_tick, round(r.flown, 6), round(r.left, 6),
                    tuple(round(v, 5) for v in r.position)) for r in fight.rockets]
        health = fight.vitals[enemy.body].total if hit else None
        hits = len(pilot.client.messages(HIT))
        # Re-accounting the same endpoint must not advance or damage twice.
        run.match.update((start + 64) * TICK, start + 64)
        self.assertEqual(len(pilot.client.messages(HIT)), hits)
        if hit:
            self.assertEqual(fight.vitals[enemy.body].total, health)
        run.match.update((start + 264) * TICK, start + 264)
        self.assertFalse(fight.rockets)
        return flights, health, hits

    def test_actual_command_batches_preserve_flight_and_expiry(self):
        for pilot_form in (False, True):
            expected = self.replay(pilot_form, 1, False)
            self.assertEqual(len(expected[0]), 1 if pilot_form else 9)
            for batch in (8, 32):
                with self.subTest(pilot_form=pilot_form, batch=batch):
                    self.assertEqual(self.replay(pilot_form, batch, False), expected)

    def test_actual_command_batches_preserve_damage_and_owner_hits(self):
        for pilot_form in (False, True):
            expected = self.replay(pilot_form, 1, True)
            self.assertLess(expected[1], 10000)
            self.assertGreater(expected[2], 0)
            for batch in (8, 32):
                with self.subTest(pilot_form=pilot_form, batch=batch):
                    self.assertEqual(self.replay(pilot_form, batch, True), expected)

    def test_both_birth_epochs_are_causal_in_isolated_script_storage(self):
        _, pilot, _, _, _ = created.CreatedBodyTests().scenario()
        scripts = (pilot.body_script, pilot.controlled_mech.body_script)

        def capture(script):
            # Exercise the production script layout/serializer with explicit external
            # boundaries for its dependencies. Full server export has an independent
            # pre-existing Orisa wire_entities registration failure on this base.
            unique = {id(value): (name, value) for name, value in vars(script).items() if name != "dva_birth"}
            boundaries = dict(unique.values())
            graph = _Graph(boundaries, None, {})
            graph.value(script)
            return graph.finish()

        body_before = snapshot_digest(export_body_vm(scripts[0]))
        for script in scripts:
            before = capture(script)
            pair = script.dva_birth
            script.dva_birth = (pair[0] + 1, pair[1])
            self.assertNotEqual(capture(script), before)
            if script is scripts[0]:
                self.assertNotEqual(snapshot_digest(export_body_vm(script)), body_before)
            script.dva_birth = pair
            self.assertEqual(capture(script), before)
