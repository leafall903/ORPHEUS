"""Normal source14 query/publication with actual contact effects left authoritative."""

import unittest
from unittest.mock import patch

import test_doomfist_source14_query as source_query_cases
from test_doomfist_combat import DoomfistFixture

from ow174.game import combat, doomfist
from ow174.game.script import expr


class Normal14QueryTests(DoomfistFixture, unittest.TestCase):
    def case(self, *, bot=False):
        source, recipient = source_query_cases.Source14QueryTests.scene(self, bot=bot)
        entity = recipient.entity if bot else recipient.body
        world = source.component.world
        published, queries, damage, phases = [], [], [], []
        original_write, original_query = source.write, world.tracked_targets
        original_damage = self.fight._damage
        serial = self.fight.knockbacks.serial
        target = next(t for t in self.fight._targets() if t.entity == entity)
        before = self.fight.vitals_of(target).total

        def query(state):
            result = original_query(state)
            if state.instance is source and state.node.pos == 14:
                queries.append((self.match.tick, source.component.frame, result))
            return result

        def write(key, value):
            if key == (expr.INSTANCE, 375):
                published.append((self.match.tick, value))
            return original_write(key, value)

        def hit(*args, **kwargs):
            if args[4] == doomfist.PUNCH and args[1].entity == entity:
                slot = source.vars.get(375)
                values = slot.value() if slot is not None else None
                ids = [v.id for v in values] if isinstance(values, tuple) else values
                phases.append({'world': self.match.tick, 'caller': source.component.frame,
                               'recipient': recipient.body_script.component.frame,
                               'published375': ids})
            result = original_damage(*args, **kwargs)
            if args[4] == doomfist.PUNCH and args[1].entity == entity:
                damage.append((self.match.tick, args[2], result))
            return result

        with patch.object(world, 'tracked_targets', side_effect=query), \
             patch.object(source, 'write', side_effect=write), \
             patch.object(self.fight, '_damage', side_effect=hit):
            self.run.step({self.a: (0, 0, 2)})
            for _ in range(25):
                self.run.step({})
        self.assertTrue(queries)
        self.assertTrue(all(result is not NotImplemented for _, _, result in queries))
        selected = [(tick, value) for tick, value in published if value == (expr.Entity(entity),)]
        self.assertTrue(selected)
        self.assertTrue(all(world_tick == caller_tick for world_tick, caller_tick, _ in queries))
        self.assertEqual([(amount, accepted) for _, amount, accepted in damage], [(50, True)])
        self.assertEqual(before-self.fight.vitals_of(target).total, 50)
        self.assertEqual(self.fight.knockbacks.serial-serial, 1)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertNotIn(23530, source.vars)
        self.assertFalse(any(i.graph.index == 0xD07
                             for i in recipient.body_script.component.instances.values()))
        return {'bot': bot, 'entity': entity, 'queries': [(t, f, list(v)) for t, f, v in queries],
                'published': [(t, [item.id for item in v]) for t, v in published], 'damage': damage,
                'contactPhases': phases,
                'normal16HelperCreated': False, 'paired23530Written': False}

    def test_normal_player_query_publishes_without_a_fixture_query_binding(self):
        self.case()

    def test_normal_npc_query_keeps_the_single_contact_producer(self):
        self.case(bot=True)
