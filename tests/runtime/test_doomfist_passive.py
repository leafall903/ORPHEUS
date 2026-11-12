"""Exercise The Best Defense through match commands and applied combat damage."""

import struct
import unittest
from unittest.mock import patch

from ow174.game import bots
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.combat import TICK
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.script.driver import DOOMFIST_SHIELD_GRAPH, HUD_HEALTH
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL
from tests.runtime.test_game_combat import Run

UPPERCUT = 0x08
ABILITY_HIT = 0x0D8000000000245A
METEOR_HIT = 0x0D800000000001FD
WALL_HIT = 0x0D80000000002BF5
NO_SHIELD = 0x0D8000000000074E
PASSIVE_POOL = 0x249D


def passive_shield(components):
    current = 0.0
    for part in components[51]:
        if part is not None:
            pool_id, _, amount, kind = struct.unpack("<IffI4B", part)[:4]
            if pool_id == PASSIVE_POOL and kind == SHIELDS_POOL:
                current += amount
    return current


class DoomfistPassiveTests(unittest.TestCase):
    def setup_match(self):
        with patch.object(bots, "make", return_value=[]):
            run = Run(names=("Doomfist", "Opponent"))
        doomfist, opponent = run.players
        self.assertTrue(run.match.switch_hero(doomfist, find_hero("Doomfist")))
        y = run.match.game_map.spawns[0].position[1]
        doomfist.mover.position[:] = (0.0, y, 0.0)
        opponent.mover.position[:] = (0.0, y, 2.5)
        return run, doomfist, opponent

    @staticmethod
    def target(run, player):
        return next(target for target in run.match.combat._targets() if target.owner is player)

    def vitals(self, run, player):
        return run.match.combat.vitals_of(self.target(run, player))

    @staticmethod
    def shield(vitals):
        return passive_shield(vitals.component(vitals.ids))

    def hit(self, run, shooter, victim, amount, *tags):
        run.match.combat._damage(
            shooter, self.target(run, victim), amount, False, None, run.frame, hit_tags=tags,
        )

    def test_uppercut_grants_and_replicates_thirty_shield(self):
        run, doomfist, opponent = self.setup_match()
        own, enemy = self.vitals(run, doomfist), self.vitals(run, opponent)
        self.assertEqual(self.shield(own), 0.0)

        client = doomfist.client
        with patch.object(client, "queue_body_health", wraps=client.queue_body_health) as send:
            run.step({doomfist: (0, 0, UPPERCUT)})
            run.step({})
        self.assertEqual(enemy.total, 150.0)
        self.assertEqual(self.shield(own), 30.0)
        updates = [call.args[1] for call in send.call_args_list if call.args[0] == doomfist.body]
        self.assertTrue(updates)
        self.assertEqual(passive_shield(updates[-1]), 30.0)
        script = doomfist.body_script
        self.assertEqual(script.component.entity_var(HUD_HEALTH).value(), own.total)
        export_body_vm(script)

    def test_hand_cannon_damage_does_not_grant_shield(self):
        run, doomfist, opponent = self.setup_match()
        enemy = self.vitals(run, opponent)
        run.step({doomfist: (0, 0, FIRE)})
        for _ in range(8):
            run.step({})
        self.assertLess(enemy.total, 200.0)
        self.assertEqual(self.shield(self.vitals(run, doomfist)), 0.0)

    def test_grants_stack_to_the_authored_cap_and_absorb_damage(self):
        run, doomfist, opponent = self.setup_match()
        own = self.vitals(run, doomfist)
        original_team = opponent.team
        opponent.team = doomfist.team
        self.hit(run, doomfist, opponent, 1.0, ABILITY_HIT)
        run.step({})
        self.assertEqual(self.shield(own), 0.0)
        opponent.team = original_team
        for tags, expected in (
            ((ABILITY_HIT, NO_SHIELD), 0.0),
            ((WALL_HIT,), 0.0),
            ((ABILITY_HIT,), 30.0),
            ((ABILITY_HIT,), 60.0),
            ((METEOR_HIT,), 135.0),
            ((METEOR_HIT,), 150.0),
            ((METEOR_HIT,), 150.0),
        ):
            with self.subTest(tags=tags, expected=expected):
                self.hit(run, doomfist, opponent, 1.0, *tags)
                run.step({})
                self.assertEqual(self.shield(own), expected)
        before = own.now[HEALTH_POOL]
        self.hit(run, opponent, doomfist, 20.0)
        self.assertEqual((self.shield(own), own.now[HEALTH_POOL]), (130.0, before))
        self.hit(run, doomfist, opponent, 1.0, METEOR_HIT)
        run.step({})
        self.assertEqual(self.shield(own), 150.0)

        self.hit(run, opponent, doomfist, 20.0)
        enemy = self.vitals(run, opponent)
        self.hit(run, doomfist, opponent, 1000.0, ABILITY_HIT)
        self.assertTrue(enemy.dead)
        run.step({})
        self.assertEqual(self.shield(own), 150.0)

    def test_decay_starts_from_the_first_grant_and_retires_with_the_body(self):
        run, doomfist, opponent = self.setup_match()
        own = self.vitals(run, doomfist)
        base = own.now[HEALTH_POOL]
        self.hit(run, doomfist, opponent, 1.0, ABILITY_HIT)
        for _ in range(30):
            run.step({})
        self.hit(run, doomfist, opponent, 1.0, ABILITY_HIT)
        for _ in range(20):
            run.step({})
        self.assertEqual(self.shield(own), 60.0)
        for _ in range(20):
            run.step({})
        current = self.shield(own)
        self.assertLess(current, 60.0)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(current - self.shield(own), 20 * TICK * 3, delta=0.08)
        self.hit(run, opponent, doomfist, self.shield(own) - 0.16)
        for _ in range(6):
            run.step({})
        self.assertEqual(self.shield(own), 0.0)
        self.assertEqual(own.now[HEALTH_POOL], base)
        instances = doomfist.body_script.component.instances.values()
        self.assertFalse(any(instance.graph.index == DOOMFIST_SHIELD_GRAPH for instance in instances))

        self.hit(run, doomfist, opponent, 1.0, ABILITY_HIT)
        run.step({})
        self.assertEqual(self.shield(own), 30.0)
        old_body, old_script = doomfist.body, doomfist.body_script
        self.hit(run, doomfist, opponent, 1.0, ABILITY_HIT)
        self.assertTrue(run.match.switch_hero(doomfist, doomfist.hero))
        run.step({})
        self.assertTrue(old_script.retired)
        self.assertNotIn(old_body, run.match.combat.vitals)
        self.assertEqual(self.shield(self.vitals(run, doomfist)), 0.0)
