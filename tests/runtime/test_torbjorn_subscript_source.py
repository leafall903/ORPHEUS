"""Actual Torbjorn weapon switches preserve inherited source before child initialization."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn as scenarios

from ow174.game import combat, hero_torbjorn
from ow174.game.commands import Command
from ow174.game.script import expr, runtime


class TorbjornSubScriptSourceTests(unittest.TestCase):
    def test_actual_weapon_switches_and_overload_keep_child_sources_before_init_and_hits(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / 'fixtures/behavior/torbjorn-subscript-source-v1.json').read_text())
        seen = []
        initialize = runtime.Instance.init

        def observe(instance, **kwargs):
            if (instance.parent is not None
                    and instance.component.world.hero == hero_torbjorn.HERO):
                seen.append((instance.graph.index, instance.component.entity, instance.cached_source_entity,
                             instance.context().var(expr.INSTANCE, 27), instance.descriptor_source_entity))
            return initialize(instance, **kwargs)

        with patch.object(runtime.Instance, 'init', observe):
            run, owner, victim = scenarios.TorbjornProjectileTests().arena(1.5)
            fight = run.match.combat
            target = next(t for t in fight._targets() if t.owner is victim)
            hp = fight.vitals_of(target)
            stages = []

            def check_weapon(expected):
                instances = [i for i in owner.body_script.component.instances.values()
                             if i.graph.index == expected]
                self.assertEqual(len(instances), 1)
                item = instances[0]
                stages.append(item.graph.index)
                self.assertIsNone(item.parent)  # Weapon slots are body roots, not SubScripts.
                with self.subTest(stage=len(stages), graph=expected):
                    self.assertEqual(item.cached_source_entity, owner.body)
                    self.assertEqual(item.context().var(expr.INSTANCE, 27), expr.Entity(owner.body))
                    self.assertTrue(item.states[26 if expected == 0x1683 else 0].active)
                children = [i for i in owner.body_script.component.instances.values() if i.parent is not None]
                self.assertTrue(children)
                for child in children:
                    with self.subTest(stage=len(stages), child=child.graph.index):
                        self.assertEqual(child.cached_source_entity, owner.body)
                        self.assertEqual(child.context().var(expr.INSTANCE, 27), expr.Entity(owner.body))
                        self.assertEqual(child.parent.source_entity(), owner.body)
                        self.assertIsNone(child.descriptor_source_entity)
                    self.assertIsNone(item.descriptor_source_entity)

            check_weapon(0x1683)
            run.step({owner: (0, 0, 1)})
            for _ in range(25):
                run.step({})
            self.assertEqual(hp.total, 130)
            run.frame += 1
            owner.take_commands([Command(run.frame, action=2)])
            for _ in range(45):
                run.step({})
            check_weapon(0x168C)
            for _ in range(21):
                run.step({owner: (0, 0, 1)})
            run.step({})
            self.assertEqual(hp.total, 75)
            self.assertEqual(len(owner.client.messages(combat.HIT)), 2)
            run.frame += 1
            owner.take_commands([Command(run.frame, action=1)])
            for _ in range(45):
                run.step({})
            check_weapon(0x1683)
            self.assertEqual(stages, fixture['expected']['weapon_graphs'])
            run.step({owner: (0, 0, 16)})
            overload = next(i for i in owner.body_script.component.instances.values()
                            if i.graph.index == 0x1721)
            self.assertIsNone(overload.parent)  # Ability is a declared body root too.
            self.assertEqual(overload.cached_source_entity, owner.body)
            self.assertTrue(overload.states[3].active)
            self.assertEqual(fight.vitals[owner.body].over_armour.amount, 100)
            self.assertEqual(len(owner.client.messages(combat.HIT)), 2)
        self.assertTrue(seen)
        self.assertTrue({0x01CF, 0x0E8F, 0x0D24} <= {row[0] for row in seen})
        for graph_index, body, cached, typed, descriptor in seen:
            with self.subTest(before_init_graph=graph_index):
                self.assertEqual(cached, body)
                self.assertEqual(typed, expr.Entity(body))
                self.assertIsNone(descriptor)
