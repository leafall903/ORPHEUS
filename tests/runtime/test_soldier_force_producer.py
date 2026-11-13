"""Real Soldier inputs on canonical force host; native adoption remains pending."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, correction, defenses
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from tests.runtime.test_game_combat import Run

FIXTURE_ROOT = Path(os.environ.get(
    "ORPHEUS_BEHAVIOR_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures/behavior",
))
CONTRACT = json.loads((FIXTURE_ROOT / "soldier-force-producer-v1.json").read_text(encoding="utf-8"))


class SoldierForceProducerTests(unittest.TestCase):
    def scene(self, wall=False):
        run = Run(names=('Soldier', 'Ashe'))
        soldier, victim = run.players
        births = {soldier: 9000, victim: 12000}
        for player, hero in ((soldier, SOLDIER), (victim, 0x02E0000000000200)):
            player.commands.last_frame = births[player]
            run.match.switch_hero(player, heroes()[hero])
            player.mover.position[:] = [1000, 0, 1000]
            player.mover.body = None
            player.body_script.create_arrived = True
        victim.team = 1 - soldier.team
        run.match.collision_world = flat_world(0)
        if wall:
            run.match.collision_world = World([
                (900, 0, 900, 900, 0, 1200, 1200, 0, 1200),
                (900, 0, 900, 1200, 0, 1200, 1200, 0, 900),
                (990, -10, 1000.6, 1010, -10, 1000.6, 1010, 10, 1000.6),
                (990, -10, 1000.6, 1010, 10, 1000.6, 990, 10, 1000.6),
            ])
        victim.mover.position[:] = [1001.1, 0, 1000.2]
        return run, soldier, victim, births

    def step(self, run, births, *, soldier_buttons=0, victim_buttons=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for index, player in enumerate(run.players):
            player.take_commands([Command(births[player] + run.frame - 1000,
                                          buttons=soldier_buttons if index == 0 else victim_buttons)])

    def test_actual_helix_enemy_and_self_use_one_world_force_each_then_land(self):
        run, soldier, victim, births = self.scene(wall=True)
        fight = run.match.combat
        npc_ids = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(npc_ids), 15)
        for _ in range(20):
            self.step(run, births)
        target = next(t for t in fight._targets() if t.owner is victim)
        own = next(t for t in fight._targets() if t.owner is soldier)
        pools = (fight.vitals_of(own), fight.vitals_of(target))
        before = tuple(pool.total for pool in pools)
        positions = (tuple(soldier.mover.position), tuple(victim.mover.position))
        serial = fight.knockbacks.serial
        calls = []
        apply = fight.knockbacks.apply

        def capture(target, impulse, **kwargs):
            accepted = apply(target, impulse, **kwargs)
            calls.append((target.entity, tuple(impulse), dict(kwargs), accepted))
            return accepted

        with patch.object(fight.knockbacks, 'apply', capture):
            self.step(run, births, soldier_buttons=2)
            rocket, = [r for r in fight.rockets if r.shooter is soldier]
            source = rocket.source_entity
            for _ in range(100):
                self.step(run, births)
                if rocket not in fight.rockets:
                    break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(
            tuple(a - b.total for a, b in zip(before, pools, strict=True)),
            tuple(CONTRACT["helix"]["damage_self_enemy"]),
        )
        self.assertEqual(len(calls), CONTRACT["helix"]["force_events"])
        self.assertEqual({c[0] for c in calls}, {soldier.body, victim.body})
        for _entity, impulse, kwargs, accepted in calls:
            self.assertTrue(accepted)
            self.assertEqual(kwargs['source_entity'], source)
            self.assertEqual(kwargs['impact_tick'], run.frame)
            self.assertNotIn('impact_frame', kwargs)
            self.assertGreater(impulse[1], 0)
        self.assertEqual(fight.knockbacks.serial - serial, 2)
        self.assertIsNotNone(victim.impulse_correction)
        peak = 0
        for _ in range(20):
            self.step(run, births)
            peak = max(peak, victim.mover.position[1])
        self.assertGreater(peak, .2)
        self.assertGreater(victim.mover.position[0], positions[1][0] + .3)
        self.assertLess(soldier.mover.position[2], positions[0][2] - .3)
        self.assertIsNotNone(correction.own_record(victim, run.frame))
        for _ in range(180):
            self.step(run, births)
        for owner in (soldier, victim):
            self.assertFalse(owner.mover.airborne)
            self.assertAlmostEqual(owner.mover.position[1], 0, delta=.05)
            self.assertLess(sum(abs(v) for v in owner.mover.velocity), .01)
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), npc_ids)

    def test_melee_views_swapped_target_in_world_time_without_duplicate_force(self):
        run, soldier, a, _births = self.scene()
        b = run.match.add_player(3, 'TargetB', SOLDIER, 2, False)
        # Use the same normal client harness as Run's original players.
        from tests.runtime.test_game_combat import in_world

        in_world(run.match, b)
        run.match.switch_hero(b, heroes()[SOLDIER])
        b.mover.body = None
        b.team = a.team
        b.mover.position[:] = [1002, 0, 1001.5]
        b.body_script.create_arrived = True
        a_pos, b_pos = [1000, 0, 1001.5], [1002, 0, 1001.5]
        for offset in range(1, 42):
            a.mover.position[:] = b_pos if offset > 33 else a_pos
            b.mover.position[:] = a_pos if offset > 33 else b_pos
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            if offset < 41:
                soldier.take_commands([Command(9000 + offset)])
        fight = run.match.combat
        targets = {t.owner: t for t in fight._targets()}
        pools = [fight.vitals_of(targets[p]) for p in (a, b)]
        serial = fight.knockbacks.serial
        soldier.take_commands([Command(9041, buttons=0x800, extra=128)])
        for offset in range(42, 48):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            soldier.take_commands([Command(9000 + offset)])
        self.assertEqual([p.total for p in pools], CONTRACT["melee"]["health_a_b"])
        self.assertEqual(fight.knockbacks.serial - serial, 1)
        hits = soldier.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], a.body)
        self.assertEqual(hits[0]['+0x84'], 9041)
        self.assertEqual(len(run.match.bots), 15)

    def test_actual_fortify_takes_reduced_helix_damage_without_force(self):
        run, soldier, victim, births = self.scene(wall=True)
        run.match.switch_hero(victim, heroes()[defenses.ORISA])
        victim.mover.position[:] = [1001.1, 0, 1000.2]
        victim.mover.body = None
        victim.body_script.create_arrived = True
        for index in range(20):
            self.step(run, births, victim_buttons=8 if index == 0 else 0)
        self.assertAlmostEqual(defenses.fortify_scale(victim), .6)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        health = fight.vitals_of(target).total
        start = tuple(victim.mover.position)
        calls = []
        original = fight.knockbacks.apply

        def capture(target, impulse, **kwargs):
            calls.append(target.entity)
            return original(target, impulse, **kwargs)

        with patch.object(fight.knockbacks, 'apply', capture):
            self.step(run, births, soldier_buttons=2)
            rocket, = [r for r in fight.rockets if r.shooter is soldier]
            for _ in range(100):
                self.step(run, births)
                if rocket not in fight.rockets:
                    break
        self.assertGreater(health - fight.vitals_of(target).total, 0)
        self.assertNotIn(victim.body, calls)
        self.assertEqual(tuple(victim.mover.position), start)
        self.assertEqual(len(run.match.bots), 15)
