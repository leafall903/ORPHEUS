"""Flux primary filter composed with native hostility over declared actor inputs."""

import unittest

from ow174.game.script import entity_hostility as hostility
from ow174.game.script import filter_pipeline as pipeline
from ow174.game.script import graph
from ow174.game.script.entity_team import FilterBits


class FilterHostilityTests(unittest.TestCase):
    def fixture(self, target_team, source_team, *, same_group=NotImplemented, relation=None):
        target = pipeline.Subject(0xA0000123, True, FilterBits(1 | (0x800000 << target_team)))
        source = pipeline.Subject(0xA0000456, True, FilterBits(1 | (0x800000 << source_team)))
        actors = {target.entity: hostility.Target(target.entity, target_team, False, relation),
                  source.entity: hostility.Target(source.entity, source_team, False, None)}
        calls = []

        def group(first, second):
            calls.append(('group', first.entity, second.entity))
            if type(same_group) is not bool:
                raise ValueError('declared group service unavailable')
            return same_group

        def relationship(spectator):
            calls.append(('relationship', spectator))
            if spectator:
                raise ValueError('C1C690 spectator policy must be provided separately')
            return hostility.compare(actors[target.entity], actors[source.entity], False,
                                     lookup=actors.get, fallback_owner=lambda _: None, same_group=group)

        services = pipeline.Services(relationship=relationship, registry=lambda *_: 2)
        cfg = graph.graph(0x1E41).node(37).field('m_filter')
        return target, source, services, cfg, calls

    def test_ordinary_opponents_and_allies_use_the_native_relation_result(self):
        for target_team, source_team, expected in ((1, 0, True), (0, 1, True), (0, 0, False), (1, 1, False)):
            with self.subTest(target=target_team, source=source_team):
                target, source, services, cfg, calls = self.fixture(target_team, source_team)
                self.assertEqual(pipeline.resolved(None, cfg, target, source, services), expected)
                self.assertEqual(calls, [('relationship', False)])

    def test_free_for_all_and_neutral_group_inputs_are_not_replaced_by_team_inequality(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                target, source, services, cfg, calls = self.fixture(4, 4, same_group=grouped)
                self.assertEqual(pipeline.resolved(None, cfg, target, source, services), not grouped)
                self.assertEqual(calls, [('relationship', False), ('group', source.entity, target.entity)])

    def test_unknown_neutral_group_or_component50_inputs_remain_required(self):
        target, source, services, cfg, _calls = self.fixture(4, 4)
        with self.assertRaisesRegex(ValueError, 'group'):
            pipeline.resolved(None, cfg, target, source, services)
        target, source, services, cfg, _calls = self.fixture(1, 0, relation=NotImplemented)
        with self.assertRaisesRegex(ValueError, 'component50'):
            pipeline.resolved(None, cfg, target, source, services)

    def test_relation_identity_override_can_reject_apparent_opponents(self):
        relation = hostility.Relation(0xA0000456, 0, 0, 0)
        target, source, services, cfg, _calls = self.fixture(1, 0, relation=relation)
        self.assertFalse(pipeline.resolved(None, cfg, target, source, services))

    def test_self_pointer_is_excluded_even_when_the_hostility_service_would_report_enemy(self):
        target, _source, services, cfg, _calls = self.fixture(1, 0)
        self.assertFalse(pipeline.resolved(None, cfg, target, target, services))

    def test_static_bit_failure_does_not_skip_the_native_eager_relation_call(self):
        target, source, services, cfg, calls = self.fixture(1, 0)
        target.bits.bits |= 0x10
        self.assertFalse(pipeline.resolved(None, cfg, target, source, services))
        self.assertEqual(calls, [('relationship', False)])

    def test_spectator_filter_branch_requires_its_separate_native_policy(self):
        target, source, services, cfg, calls = self.fixture(1, 0)
        target.bits.bits |= 1 << 28
        with self.assertRaisesRegex(ValueError, 'C1C690'):
            pipeline.resolved(None, cfg, target, source, services)
        self.assertEqual(calls, [('relationship', True)])


if __name__ == '__main__':
    unittest.main()
