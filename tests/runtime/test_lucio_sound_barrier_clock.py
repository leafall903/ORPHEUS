"""Sound Barrier lifecycle uses the cast's command/world association."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, sound_barrier
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime import test_lucio_combat as lucio_tests


class SoundBarrierClockTests(unittest.TestCase):
    def test_same_landing_keeps_grant_absorption_decay_and_end_across_batches_and_epochs(self):
        root = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                    or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        contract = json.loads((root / "lucio-sound-barrier-command-world-clock-v1.json").read_text(
            encoding="utf-8-sig",
        ))
        inputs, expected = contract["input"], contract["expected"]
        for epoch_offset in inputs["command_epoch_offsets"]:
            for batch in inputs["batch_sizes"]:
                for damage in inputs["damage_samples"]:
                    with self.subTest(epoch_offset=epoch_offset, batch=batch, damage=damage):
                        self.check_cast(inputs, expected, epoch_offset, batch, damage)

    def check_cast(self, inputs, expected, epoch_offset, batch, damage):
        run, source, enemy, _ = lucio_tests.LucioCombatTests().scenario()
        ground = inputs["floor_height"]
        run.match.collision_world = flat_world(ground)
        self.assertTrue(run.match.switch_hero(
            source, heroes()[sound_barrier.HERO], start_frame=run.frame + epoch_offset,
        ))
        source.mover.position[:] = (0, ground, 0)
        source.mover.body = None
        command_start = source.body_script.frame
        for number in range(1, inputs["warmup_commands"] + 1):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            source.take_commands([Command(command_start + number)])
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(inputs["initial_charge"])
        command_start, world_start = source.body_script.frame, run.frame
        fight = run.match.combat
        manager = fight.sound_barriers
        target = next(target for target in fight._targets() if target.owner is source)
        vitals = fight.vitals_of(target)
        grants = []
        original = manager.grant

        def observe_grant(player, wave, targets, at):
            present = source.body in manager.shielded
            result = original(player, wave, targets, at)
            if not present and source.body in manager.shielded:
                grants.append((at, wave.until))
            return result

        with patch.object(manager, "grant", observe_grant):
            for first in range(1, inputs["commands"] + 1, batch):
                last = min(first + batch - 1, inputs["commands"])
                for number in range(first, last + 1):
                    run.frame = world_start + number
                    run.match.update(run.frame * combat.TICK, run.frame)
                source.take_commands([
                    Command(command_start + number,
                            buttons=inputs["cast_button"] if number <= inputs["held_commands"] else 0)
                    for number in range(first, last + 1)
                ])
        grant_at = (world_start + expected["effective_grant_world_offset"]) * combat.TICK
        self.assertEqual(len(grants), 1)
        self.assertAlmostEqual(grants[0][0], grant_at)
        self.assertAlmostEqual(grants[0][1] - grant_at, expected["wave_window_seconds"])
        pool = vitals.extra[sound_barrier.POOL_KEY]
        self.assertEqual(pool.most, expected["capacity"])
        self.assertFalse(pool.removed)
        self.assertEqual(vitals.base_total, expected["base_health"])
        self.assertEqual(pool.now, expected["pool_at_128_before_damage"])
        self.assertEqual(manager.shielded[source.body].rate, expected["decay_per_second"])
        receiver = manager.shielded[source.body].application
        fight._damage(enemy, target, damage, False, None, run.frame)
        self.assertEqual(pool.now, expected["pool_at_128_before_damage"] - damage)
        self.assertEqual(vitals.base_total, expected["base_health"])
        expiry = expected["expiry_world_offsets"][str(damage)]
        for offset in range(inputs["commands"] + 1, expiry + 1):
            run.frame = world_start + offset
            run.match.update(run.frame * combat.TICK, run.frame)
            if offset < expiry:
                self.assertFalse(pool.removed)
            if offset == 256:
                self.assertEqual(pool.now, expected["pool_at_256_without_damage"] - damage)
        self.assertTrue(pool.removed)
        self.assertEqual(pool.now, 0)
        self.assertNotIn(source.body, manager.shielded)
        self.assertNotIn(receiver.id, receiver.component.instances)
        self.assertFalse(manager.pending)
        self.assertFalse(manager.waves)
        self.assertEqual(vitals.base_total, expected["base_health"])
        fight._damage(enemy, target, expected["post_expiry_damage"], False, None, run.frame)
        self.assertEqual(vitals.base_total, expected["base_health"] - expected["post_expiry_damage"])
