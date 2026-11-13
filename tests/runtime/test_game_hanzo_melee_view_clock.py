"""Real melee commands against distinct swapped identities, not static current poses."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from ow174.game import bodytrace, combat, movelog, sonic
from ow174.game.commands import Command
from ow174.game.content import heroes


class HanzoMeleeViewClockTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def test_swapped_target_identity_uses_world_history_and_preserves_owner_hit_frame(self):
        fixture = Path(__file__).resolve().parents[2] / "fixtures/behavior/hanzo-melee-view-clock-v1.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        self.actual_cases = []
        for owner_birth in data["input"]["owner_births"]:
            with self.subTest(owner_birth=owner_birth):
                run = Run(KANEZAKA_DM, ("Hanzo", "ViewedTargetA", "OtherTargetB"))
                hero, a, b = run.players
                match = run.match
                npc_count = len(match.bots)
                self.assertEqual(npc_count, 15)
                hero.commands.last_frame = owner_birth
                match.switch_hero(hero, heroes()[sonic.HANZO], start_frame=owner_birth)
                self.assertEqual(hero.body_script.world_frame_anchor, (1000, owner_birth))
                x, y, z = hero.mover.position
                near, far = (x, y, z + 2), (x + 8, y, z + 2)
                fight = match.combat
                contacts = []
                submit = fight._submit_attack

                def observe(shooter, frame, payload):
                    if shooter is hero and isinstance(payload, combat.MeleeContact):
                        contacts.append((frame, payload.view_tick))
                    return submit(shooter, frame, payload)

                with patch.object(fight, "_submit_attack", side_effect=observe):
                    for offset in range(1, 81):
                        swapped = offset >= 41
                        a.mover.position[:] = far if swapped else near
                        b.mover.position[:] = near if swapped else far
                        tick = 1000 + offset
                        match.update(tick * combat.TICK, tick)
                        hero.take_commands([Command(owner_birth + offset, buttons=0x800 if offset == 41 else 0,
                                                    extra=500)])
                targets = {target.owner: target for target in fight._targets()}
                health = [fight.vitals_of(targets[player]).total for player in (a, b)]
                hits = hero.client.messages(combat.HIT)
                self.actual_cases.append({"world_birth":1000, "owner_birth":owner_birth,
                                          "A":a.body,"B":b.body,"health_A_B":health,
                                          "contacts":contacts,"hits":[{"target":hit["+0x78"]["+0x0"]["+0x0"],
                                                                       "owner_frame":hit["+0x84"]} for hit in hits],
                                          "npc_count":len(match.bots)})
                self.assertEqual(health, data["expected"]["health_A_B"], (owner_birth, contacts))
                self.assertEqual(len(contacts), 1)
                owner_frame, world_view = contacts[0]
                self.assertEqual(world_view, 1000 + owner_frame - owner_birth - 500 / 16)
                self.assertEqual(world_view, data["expected"]["world_history_query"])
                self.assertEqual(owner_frame, owner_birth + data["input"]["melee_offset"])
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], a.body)
                self.assertEqual(hits[0]["+0x84"], owner_frame)
                self.assertEqual(len(match.bots), npc_count)
