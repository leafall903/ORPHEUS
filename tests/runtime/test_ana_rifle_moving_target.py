"""Actual hip-rifle packets share one target movement and world timeline."""

import json
import unittest
from pathlib import Path

from ow174.game import combat
from ow174.game.commands import Command
from tests.runtime import test_game_ana as ana_fixture
from tests.runtime.test_game_combat import aim

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / "fixtures/behavior/ana-rifle-moving-target-v1.json").read_text())


class AnaRifleMovingTargetTests(unittest.TestCase):
    def replay(self, packet_size, separate_epoch):
        cfg = CONTRACT["input"]
        run, ana, victim, target = ana_fixture.AnaRifleTests().setup_fight(
            enemy=True, distance=cfg["distance"])
        fight = run.match.combat
        if separate_epoch:
            run.match.switch_hero(ana, ana.hero, preserve_pose=True, start_frame=9000)
            ana.commands.reset(last_frame=9000)
            for offset in range(1, 26):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                ana.take_commands([Command(9000 + offset)])
                victim.take_commands([Command(run.frame)])
        direction = aim(fight._eye(ana), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        start, owner_start = run.frame, ana.commands.last_frame
        pending = []
        for offset in range(1, cfg["sample_frames"] + 1):
            tick = start + offset
            run.match.update(tick * combat.TICK, tick)
            victim.take_commands([Command(tick, right=cfg["target_right"])])
            pending.append(Command(owner_start + offset, yaw=direction[0], pitch=direction[1],
                                   buttons=1 if offset == 1 else 0))
            if offset % packet_size == 0:
                ana.take_commands(pending)
                pending = []
        return {"health": fight.vitals_of(target).total, "hits": ana.client.messages(combat.HIT),
                "live": len(fight.rockets), "target_final": victim.mover.position}

    def test_delayed_rifle_preserves_damage_and_hit_identity_labels(self):
        for separate_epoch in (False, True):
            with self.subTest(separate_epoch=separate_epoch):
                direct, delayed = [self.replay(size, separate_epoch)
                                   for size in CONTRACT["input"]["source_packet_sizes"]]
                self.assertEqual(direct["health"], 200 - CONTRACT["expected"]["damage"])
                self.assertEqual(direct["health"], delayed["health"])
                self.assertEqual(direct["target_final"], delayed["target_final"])
                self.assertEqual(direct["live"], delayed["live"])
                for outcome in (direct, delayed):
                    self.assertTrue(outcome["hits"])
                    self.assertEqual(outcome["live"], 0)
                # The existing timed dose coalesces damage receipts differently
                # on late admission. This change preserves its identity/frame/flags,
                # not a new equal-count or equal-per-slice HIT contract.
                def labels(outcome):
                    return {(h["+0x78"]["+0x0"]["+0x0"], h["+0x78"]["+0x8"], h["+0x84"])
                            for h in outcome["hits"]}

                self.assertEqual(labels(direct), labels(delayed))
