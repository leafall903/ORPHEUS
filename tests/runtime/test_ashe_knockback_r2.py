"""Real Ashe producers on the canonical host; original-client adoption pending."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_canonical_knockback_174 import CanonicalKnockbackTests

from ow174.game import combat
from ow174.game.content import find_hero

CONTRACT = json.loads((Path(__file__).parents[2] / 'fixtures/behavior/ashe-knockback-r2-v1.json').read_text())


class AsheKnockbackR2Tests(unittest.TestCase):
    def scene(self, offset=4000):
        fixture = CanonicalKnockbackTests()
        run, owner, enemy, _, npcs = fixture.scene(source_offset=offset, victim_birth=9000)
        position = list(owner.mover.position)
        run.match.switch_hero(owner, find_hero('Ashe'))
        owner.mover.position = position
        owner.mover.body = None
        return fixture, run, owner, enemy, npcs

    def test_real_coach_preserves_source_and_uses_contact_world_tick_once(self):
        for offset in (0, 4000):
            with self.subTest(source_offset=offset):
                fixture, run, owner, enemy, npcs = self.scene(offset)
                fight = run.match.combat
                records = []
                apply = fight.knockbacks.apply

                def capture(target, vector, *, rows=records, host=fight, forward=apply, **kwargs):
                    rows.append((target.entity, tuple(vector), kwargs, host.tick))
                    return forward(target, vector, **kwargs)

                before = tuple(enemy.mover.position)
                with patch.object(fight.knockbacks, 'apply', side_effect=capture):
                    fixture.step(run, offset, 9000, 8)
                    for _ in range(30):
                        fixture.step(run, offset, 9000)
                self.assertEqual(len(records), CONTRACT['expected']['coach_force_count'])
                self.assertEqual({row[0] for row in records}, {owner.body, enemy.body})
                for _, _, kwargs, contact_tick in records:
                    self.assertEqual(kwargs, {'impact_tick': contact_tick, 'source_entity': owner.body})
                self.assertEqual(fight.knockbacks.serial, 2)
                self.assertEqual(fight.vitals[enemy.body].total, CONTRACT['expected']['coach_health'])
                self.assertGreater(enemy.mover.position[2], before[2])
                self.assertLess(owner.mover.position[2], 1000)
                self.assertEqual(fight.knockbacks.launch_sources.get(enemy.body), owner.body)
                self.assertEqual(tuple(bot.entity for bot in run.match.bots), npcs)

    def test_real_melee_uses_only_canonical_entry(self):
        fixture, run, owner, enemy, npcs = self.scene()
        fight = run.match.combat
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as applied:
            fixture.step(run, 4000, 9000, 0x800)
            for _ in range(40):
                fixture.step(run, 4000, 9000)
        applied.assert_called_once()
        self.assertEqual(tuple(applied.call_args.args[1]), tuple(CONTRACT['expected']['melee_vector']))
        self.assertEqual(applied.call_args.kwargs['source_entity'], owner.body)
        self.assertIn('impact_tick', applied.call_args.kwargs)
        self.assertEqual(fight.vitals[enemy.body].total, 170)
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), npcs)

    def test_real_bob_uppercut_uses_bob_source_then_returns_landing_control(self):
        fixture, run, owner, enemy, npcs = self.scene()
        fight = run.match.combat
        enemy.mover.position[2] = 1012
        enemy.mover.body = None
        fight.vitals[enemy.body] = combat.Vitals(enemy.body, 10000)
        owner.body_script.set_ult_charge(2240)
        fixture.step(run, 4000, 9000, 32)
        actor, = fight.companions.active.values()
        records = []
        apply = fight.knockbacks.apply

        def capture(target, vector, **kwargs):
            records.append((target.entity, tuple(vector), kwargs, fight.tick))
            return apply(target, vector, **kwargs)

        with patch.object(fight.knockbacks, 'apply', side_effect=capture):
            for _ in range(100):
                fixture.step(run, 4000, 9000)
                if actor.phase != 'charge':
                    break
        self.assertEqual(actor.stopped_reason, 'enemy')
        self.assertEqual(len(records), 1)
        entity, vector, kwargs, tick = records[0]
        self.assertEqual(entity, enemy.body)
        self.assertEqual(vector, tuple(CONTRACT['expected']['bob_vector']))
        self.assertEqual(kwargs, {'impact_tick': tick, 'source_entity': actor.bot.entity})
        self.assertEqual(fight.vitals[enemy.body].total, 9880)
        for _ in range(8):
            fixture.step(run, 4000, 9000)
        self.assertTrue(enemy.mover.airborne)
        component = enemy.body_script.component
        self.assertTrue(any(i.graph.index == 0x17F6 for i in component.instances.values()))
        for _ in range(200):
            fixture.step(run, 4000, 9000)
        self.assertFalse(enemy.mover.airborne)
        self.assertFalse(any(i.graph.index == 0x17F6 for i in component.instances.values()))
        self.assertFalse(component.entity_var(17932, create=False).value())
        self.assertEqual(tuple(bot.entity for bot in run.match.bots if bot is not actor.bot), npcs)
