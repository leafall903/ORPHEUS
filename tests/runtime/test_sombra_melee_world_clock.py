"""Real Sombra 0043 producer against canonical force API; native adoption pending."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, heroes

SOMBRA = 0x02E000000000012E


class SombraMeleeWorldClockTests(unittest.TestCase):
    def test_real_melee_keeps_target_identity_world_impact_and_one_force(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/sombra-melee-world-clock-v1.json"
        expected = json.loads(path.read_text(encoding="utf-8"))["expected"]
        run = Run(PRACTICE_RANGE, ("Sombra", "TargetA", "TargetB"))
        player, a, b = run.players
        match, fight = run.match, run.match.combat
        player.commands.reset(last_frame=9000)
        match.switch_hero(player, heroes()[SOMBRA], start_frame=9000)
        match.switch_hero(a, a.hero, preserve_pose=True, start_frame=14000)
        match.switch_hero(b, b.hero, preserve_pose=True, start_frame=18000)
        floor = PRACTICE_RANGE.spawns[0].position[1]
        player.mover.teleport_grounded((100, floor, 0))
        submitted, forces, displacement = [], [], []
        forward_attack, forward_force = fight._submit_attack, fight.knockbacks.apply

        def attack(shooter, frame, payload):
            if shooter is player:
                submitted.append((frame, payload.view_tick, payload.impact_tick))
            return forward_attack(shooter, frame, payload)

        def force(target, impulse, **kwargs):
            forces.append((target.entity, tuple(impulse), kwargs))
            return forward_force(target, impulse, **kwargs)

        with patch.object(fight, "_submit_attack", attack), patch.object(fight.knockbacks, "apply", force):
            for offset in range(1, 66):
                first, second = (98, floor, 1.5), (102, floor, 1.5)
                a.mover.position[:] = first if offset <= 33 else second
                b.mover.position[:] = second if offset <= 33 else first
                tick = 1000 + offset
                match.update(tick * .016, tick)
                yaw, pitch = aim(fight._eye(player), (98, floor + .45, 1.5))
                player.take_commands([Command(9000 + offset, yaw=yaw, pitch=pitch,
                                              buttons=0x800 if offset == 41 else 0, extra=512)])
                before = tuple(a.mover.position)
                a.take_commands([Command(14000 + offset)])
                b.take_commands([Command(18000 + offset)])
                if forces and tuple(a.mover.position) != before:
                    displacement.append((before, tuple(a.mover.position)))
        targets = {t.owner: t for t in fight._targets()}
        health = [fight.vitals_of(targets[p]).total for p in (a, b)]
        self.assertEqual(len(submitted), 1)
        frame, view, impact = submitted[0]
        self.assertEqual((view, impact), (frame - 9000 + 1000 - 32, frame - 9000 + 1000))
        self.assertEqual((frame, view, impact), (expected["source_owner_frame"],
                                              expected["view_world_tick"], expected["impact_world_tick"]))
        self.assertEqual(health, expected["target_health"])
        self.assertEqual(len(forces), expected["forces"])
        target, impulse, kwargs = forces[0]
        self.assertEqual(target, a.body)
        self.assertEqual(kwargs, {"impact_tick": impact, "source_entity": player.body})
        self.assertAlmostEqual(sum(v * v for v in impulse), expected["impulse_squared_length"], places=5)
        self.assertEqual(impulse[1], expected["vertical_impulse"])
        self.assertEqual(fight.knockbacks.serial, 1)
        self.assertTrue(displacement)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([(h["+0x78"]["+0x0"]["+0x0"], h["+0x84"]) for h in hits], [(a.body, frame)])
        self.assertEqual(len(match.bots), expected["npc_count"])
