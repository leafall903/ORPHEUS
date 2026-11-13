"""Actual Genji shuriken uses physical-time target poses during packet catch-up."""

import json
from pathlib import Path
from types import SimpleNamespace

from test_game_combat import aim
from test_genji_combat import GenjiFixture

from ow174.game import combat, genji
from ow174.game.commands import Command

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / "fixtures/behavior/genji-primary-moving-target-v1.json").read_text(encoding="utf-8"))


class GenjiPrimaryMovingTargetTests(GenjiFixture):
    def test_history_keeps_construct_route_and_retained_pose(self):
        data = CASE["construct_history"]
        owner, construct = object(), object()
        target = combat.Target(data["entity"], 1, tuple(data["latest_feet"]), 1.8, .5, 1.6,
                               owner, construct=construct)
        fight = object.__new__(combat.Combat)
        fight.tick = data["physical_tick"]
        fight.match = SimpleNamespace(tick=data["world_tick"])
        fight.replacement_ticks = {}
        fight.history = {target.entity: [(fight.tick, tuple(data["retained_feet"]), 1.8, 1.6)]}
        rocket = SimpleNamespace(source_graph=0x0304, definition=0x04000000000007CA,
                                 last_tick=fight.tick)
        shown, = genji.projectile_targets(fight, rocket, (target,))
        self.assertIs(shown.construct, construct)
        self.assertIs(shown.owner, owner)
        self.assertEqual(shown.entity, target.entity)
        self.assertEqual(shown.feet, tuple(data["retained_feet"]))
        self.assertEqual(target.feet, tuple(data["latest_feet"]))

    def encounter(self, packet_size):
        inputs = CASE["input"]
        run, shooter = self.spawn(inputs["distance"])
        victim = run.players[1]
        for _ in range(inputs["warmup_frames"]):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
        world_start, owner_start = run.frame, shooter.commands.last_frame
        pending = []
        for offset in range(1, inputs["sample_frames"] + 1):
            tick = world_start + offset
            run.match.update(tick * combat.TICK, tick)
            victim.take_commands([Command(tick, right=inputs["target_right"])])
            pending.append(Command(owner_start + offset, yaw=yaw, pitch=pitch,
                                   buttons=inputs["source_button"] if offset == 1 else 0))
            if offset % packet_size == 0:
                shooter.take_commands(pending)
                pending = []
        return {"health": self.health(run, victim), "hits": shooter.client.messages(combat.HIT),
                "live": len(fight.rockets)}

    def test_packet_delay_preserves_actual_moving_target_hit_and_full_feedback(self):
        direct, delayed = [self.encounter(size) for size in CASE["input"]["source_packet_sizes"]]
        self.assertEqual(direct, delayed)
        expected = CASE["expected"]
        self.assertEqual(direct["health"], 200 - expected["damage"])
        self.assertEqual(len(direct["hits"]), expected["hit_notices"])
        self.assertEqual(direct["live"], expected["live_projectiles"])
