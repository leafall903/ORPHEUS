"""Actual0065 quick melee discriminates two exchanged target identities."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run, aim
from ow174.game import combat, junkrat
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import find_hero


class JunkratMeleeViewClockTests(unittest.TestCase):
    def test_fractional_view_and_creation_fallback(self):
        for anchor in (None, (1000, 9000)):
            with self.subTest(anchor=anchor):
                script = SimpleNamespace(world_frame_anchor=anchor, junkrat_birth=(1000, 9000), frame_ms=15)
                self.assertEqual(junkrat.view_world_tick(script, Command(9041, extra=8), 15000), 1037.9375)

    def exercise(self, birth):
        run = Run(names=("Junkrat", "TargetA", "TargetB"))
        player, a, b = run.players
        npcs = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(npcs), 15)
        player.commands.last_frame = birth
        run.match.switch_hero(player, find_hero("Junkrat"))
        self.assertEqual(player.body_script.junkrat_birth, (1000, birth))
        run.match.collision_world = flat_world(0)
        positions = ((999.5, 0, 1001.4), (1004, 0, 1001.4))
        for owner, pos in ((player, (1000, 0, 1000)), (a, positions[0]), (b, positions[1])):
            owner.mover.position = list(pos)
            owner.mover.body = None
            owner.body_script.create_arrived = True
        fight = run.match.combat
        submitted = []
        forward = fight._submit_attack

        def submit(shooter, frame, payload):
            if shooter is player and isinstance(payload, combat.MeleeContact):
                submitted.append((frame, payload.view_tick))
            return forward(shooter, frame, payload)

        yaw, pitch = aim(fight._eye(player), (999.5, 1.1, 1001.4))
        with patch.object(fight, "_submit_attack", submit):
            for offset in range(1, 61):
                a.mover.position[:] = positions[int(offset > 40)]
                b.mover.position[:] = positions[int(offset <= 40)]
                tick = 1000 + offset
                run.match.update(tick * .016, tick)
                player.take_commands([Command(birth + offset, yaw=yaw, pitch=pitch,
                                               buttons=0x800 if offset == 41 else 0, extra=128)])
        targets = {target.owner: target for target in fight._targets()}
        health = tuple(fight.vitals_of(targets[owner]).total for owner in (a, b))
        hits = player.client.messages(combat.HIT)
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), npcs)
        return submitted, health, [(hit["+0x78"]["+0x0"]["+0x0"], hit["+0x84"]) for hit in hits], a.body

    def test_swapped_target_identity_preserves_owner_hit_frame(self):
        for birth in (1000, 9000):
            with self.subTest(owner_birth=birth):
                submitted, health, hits, a = self.exercise(birth)
                self.assertEqual(submitted, [(birth + 41, 1033.0)])
                self.assertEqual(health, (170, 200))
                self.assertEqual(hits, [(a, birth + 41)])
