"""Orisa command partition checks through Match and the normal body input path."""

import json
import math
import os
import unittest
from pathlib import Path

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as fixture
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, orisa_clock, world
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import Command
from ow174.game.content import heroes


class OrisaCommandClockTests(unittest.TestCase):
    def scene(self, offset=0):
        run = Run(KANEZAKA_DM, ("Orisa", "Enemy"))
        owner, enemy = run.players
        owner.commands.last_frame = run.frame + offset
        run.match.switch_hero(owner, heroes()[fixture.ORISA])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in run.players:
            player.client.queue_entities(run.match.remote_movements(player))
        enemy.spawned = False
        return run, owner, enemy

    def launch(self, button, batch, offset=0):
        run, owner, _ = self.scene(offset)
        if button == 32:
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        commands = [Command(frame + offset, pitch=-8192 if button == 16 else 0,
                            buttons=button if frame == 1040 else 0)
                    for frame in range(1001, 1074)]
        emitted = {}
        for start in range(0, len(commands), batch):
            packet = commands[start:start + batch]
            at = packet[-1].frame - offset
            run.match.update(at * combat.TICK, at)
            owner.take_commands(packet)
            for rocket in (*run.match.combat.rockets, *run.match.combat.ability_impacts):
                emitted[id(rocket)] = rocket
        run.match.update(1096 * combat.TICK, 1096)
        for rocket in (*run.match.combat.rockets, *run.match.combat.ability_impacts):
            emitted[id(rocket)] = rocket
        self.assertEqual(len(emitted), 1)
        return run, owner, next(iter(emitted.values()))

    def test_all_orisa_launches_use_body_birth_across_partitions_and_offset_epochs(self):
        root = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                    or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        contract = json.loads((root / "orisa-command-clock-v1.json").read_text())
        for button in (1, 2, 16, 32):
            expected = None
            for offset in (0, 6000):
                for batch in (1, 8, 32):
                    with self.subTest(button=button, offset=offset, batch=batch):
                        run, owner, rocket = self.launch(button, batch, offset)
                        self.assertEqual(run.match.tick, contract["query_tick"])
                        self.assertIsNone(owner.body_script.world_frame_anchor)
                        self.assertEqual(owner.body_script.orisa_birth, (1000, 1000 + offset))
                        self.assertEqual(rocket.launch_tick, rocket.shot.frame - offset)
                        if button == 2 and offset == 6000:
                            at = orisa_clock.event_tick(owner.body_script, contract["halt_event_ms"])
                            self.assertEqual(at, contract["halt_launch_tick"])
                            self.assertAlmostEqual(rocket.flown, contract["halt_elapsed_seconds"], places=6)
                            self.assertAlmostEqual(rocket.travelled, contract["halt_distance"], places=6)
                        row = (*rocket.position, rocket.flown, rocket.left, rocket.started_tick)
                        if expected is None:
                            expected = row
                        for actual, reference in zip(row, expected, strict=True):
                            self.assertAlmostEqual(actual, reference, places=6)

    def test_birth_epoch_participates_in_body_storage(self):
        _, owner, _ = self.scene(6000)
        script = owner.body_script
        before = snapshot_digest(export_body_vm(script))
        original = script.orisa_birth
        script.orisa_birth = original[0] + 1, original[1]
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)
        script.orisa_birth = original
        self.assertEqual(snapshot_digest(export_body_vm(script)), before)

    def test_landing_deadlines_are_independent_of_packet_partition(self):
        for button in (16, 32):
            expected = None
            for batch in (1, 8, 32):
                run, _, rocket = self.launch(button, batch, 6000)
                run.match.update(1300 * combat.TICK, 1300)
                manager = (run.match.combat.orisa_barriers if button == 16
                           else run.match.combat.orisa.superchargers)
                actor, = manager.active.values()
                row = (*actor.position, actor.until, rocket.flown)
                if expected is None:
                    expected = row
                for actual, reference in zip(row, expected, strict=True):
                    self.assertAlmostEqual(actual, reference, places=6)

    def test_late_driver_packet_registers_one_hit_after_duplicate_delivery(self):
        run, owner, enemy = self.scene(6000)
        enemy.spawned = True
        x, y, z = owner.mover.position
        barrier_fixture.BarrierTests.place(enemy, (x, y, z + 5))
        packet = [Command(frame + 6000, buttons=1 if frame == 1040 else 0)
                  for frame in range(1001, 1074)]
        run.match.update(1090 * combat.TICK, 1090)
        owner.take_commands(packet)
        self.assertEqual(len(run.match.combat.rockets), 1)
        owner.take_commands(packet)
        run.match.update(1110 * combat.TICK, 1110)
        target = fixture.OrisaCombatTests().target(run, enemy)
        self.assertEqual(run.match.combat.vitals_of(target).total, 178)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
        self.assertFalse(run.match.combat.rockets)

    def test_second_press_freezes_halt_at_its_command_time_after_late_arrival(self):
        expected = None
        for batch in (1, 8, 32):
            run, owner, _ = self.scene(6000)
            packet = [Command(frame + 6000, buttons=2 if frame in (1040, 1065) else 0)
                      for frame in range(1001, 1074)]
            for start in range(0, len(packet), batch):
                commands = packet[start:start + batch]
                tick = commands[-1].frame - 6000
                run.match.update(tick * combat.TICK, tick)
                owner.take_commands(commands)
            run.match.update(1096 * combat.TICK, 1096)
            orb, = run.match.combat.orisa.orbs.values()
            self.assertEqual(orb.requested, 1065)
            row = (*orb.rocket.position, orb.rocket.flown, orb.rocket.left)
            if expected is None:
                expected = row
            for actual, reference in zip(row, expected, strict=True):
                self.assertAlmostEqual(actual, reference, places=6)

    def test_late_halt_expires_and_repeated_packet_does_not_duplicate_cleanup(self):
        run, owner, _ = self.scene(6000)
        packet = [Command(frame + 6000, buttons=2 if frame == 1040 else 0)
                  for frame in range(1001, 1074)]
        run.match.update(1400 * combat.TICK, 1400)
        owner.take_commands(packet)
        rocket, = run.match.combat.rockets
        shot = rocket.shot
        owner.take_commands(packet)
        self.assertEqual(run.match.combat.rockets, [rocket])
        run.match.update(1500 * combat.TICK, 1500)
        end = 1500 + combat.projectiles.DELIVERY_GRACE_TICKS + 2
        run.match.update(end * combat.TICK, end)
        self.assertNotIn(rocket, run.match.combat.rockets)
        self.assertFalse(run.match.combat.orisa.orbs)
        self.assertFalse(run.match.combat.projectiles.observed)
        self.assertAlmostEqual(rocket.flown, 4.0, places=6)
        self.assertFalse(owner.client.messages(combat.HIT))
        for delivery in shot.observers:
            creates = [u for u in delivery.client.entities
                       if u.entity == delivery.record.ids[0] and u.op == world.OP_CREATE]
            destroys = [u for u in delivery.client.entities
                        if u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY]
            self.assertLessEqual(len(creates), 1)
            self.assertEqual(len(destroys), len(creates))
        self.assertTrue(math.isfinite(rocket.position[2]))
