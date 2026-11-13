"""Actual Torb melee input selects historical identities; not native acceptance."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat, hero_torbjorn
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes


class TorbjornViewClockTests(unittest.TestCase):
    def test_creation_clock_keeps_fractional_view_time(self):
        script = SimpleNamespace(world_frame_anchor=None, torbjorn_birth=(1000, 9000, 15), frame_ms=15)
        self.assertEqual(hero_torbjorn.view_world_tick(script, Command(9041, extra=8), 15000),
                         1037.9375)

    def replay(self, kind, birth):
        run = Run(names=("Torb", "TargetA", "TargetB"))
        player, a, b = run.players
        npcs = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(npcs), 15)
        player.commands.last_frame = birth
        run.match.switch_hero(player, heroes()[hero_torbjorn.HERO])
        self.assertEqual(player.body_script.torbjorn_birth, (1000, birth, 16))
        run.match.collision_world = flat_world(0)
        near, far = (1000, 0, 1001.5), (1005, 0, 1001.5)
        for owner, position in ((player, (1000, 0, 1000)), (a, near), (b, far)):
            owner.mover.position = list(position)
            owner.mover.body = None
            owner.body_script.create_arrived = True
        fight = run.match.combat
        queries = []
        seen = fight.seen

        def observe(target, tick):
            if target.owner in (a, b):
                queries.append(tick)
            return seen(target, tick)

        with patch.object(fight, "seen", observe):
            for offset in range(1, 70):
                a.mover.position[:] = far if offset > 33 else near
                b.mover.position[:] = near if offset > 33 else far
                tick = 1000 + offset
                run.match.update(tick * .016, tick)
                player.take_commands([Command(
                    birth + offset, yaw=0, pitch=0,
                    action=2 if kind == "hammer" and offset == 1 else 0,
                    buttons=(1 if kind == "hammer" else 0x800) if offset == 41 else 0,
                    extra=512,
                )])
                hits = player.client.messages(combat.HIT)
                if hits:
                    break
        self.assertTrue(hits)
        targets = {t.owner: t for t in fight._targets()}
        health = tuple(fight.vitals_of(targets[p]).total for p in (a, b))
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), npcs)
        return {"kind": kind, "birth": birth, "impactOffset": offset,
                "expectedView": 1000 + offset - 32, "queries": queries,
                "health": health, "expectedTarget": a.body,
                "hits": [{"target": h['+0x78']['+0x0']['+0x0'], "frame": h['+0x84']} for h in hits]}

    def test_actual_melee_target_identity_and_owner_hit_frame_survive_epoch_offset(self):
        for kind, damage in (("quick_melee", 30), ("hammer", 55)):
            for birth in (1000, 9000):
                with self.subTest(kind=kind, birth=birth):
                    row = self.replay(kind, birth)
                    self.assertEqual(row["health"], (200-damage, 200))
                    self.assertEqual(row["hits"], [{"target": row["expectedTarget"],
                                                   "frame": birth+row["impactOffset"]}])
                    self.assertIn(row["expectedView"], row["queries"])
