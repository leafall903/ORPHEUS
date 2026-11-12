"""Execute the target's charge graph with explicit missing-service inputs.

These are resource/VM probes, not actual Combat charge acceptance. World inputs
and lifecycle are declared explicitly; production damage-event emission remains open.
"""

import unittest

from ow174.game.script import entity_filter, entity_hostility, entity_relation, entity_tags, expr, runtime
from ow174.game.script.driver import BodyScript
from ow174.game.script.entity_team import FilterBits

HERO = 0x02E0000000000065
BODY, CONTROLLER, OPPONENT = 0xA0000101, 0xA0000100, 0xA0000201
MESSAGE = 0x0240000000000000


class JunkratChargeResourceTests(unittest.TestCase):
    def body(self, *, services=False, tags=(), admit=True):
        body = BodyScript(HERO, BODY, 0, ult_seconds=0)
        self.assertEqual(body.component.entity_var(338).value(), 1925)
        self.assertEqual(sum(i.graph.index == 0x0B8B
                             for i in body.component.instances.values()), 1)
        if services:
            actors = {
                BODY: entity_hostility.Target(BODY, 0, False, None),
                OPPONENT: entity_hostility.Target(OPPONENT, 1, False, None),
            }
            hostility = entity_hostility.Context(BODY, True, lookup=actors.get,
                                                watch_environment=None)
            body.world.entity_hostility_context = lambda owner, component=None: hostility
            tag_targets = {OPPONENT: entity_tags.Target(OPPONENT, entity_tags.Tags(base=tags))}
            tag_context = entity_tags.Context(BODY, True, lookup=tag_targets.get)
            body.world.entity_tag_context = lambda owner, component=None: tag_context
            filter_targets = {
                BODY: entity_filter.Target(BODY, FilterBits(1), True, None, None, None),
                OPPONENT: entity_filter.Target(OPPONENT, FilterBits(1 if admit else 0), True,
                                              tag_targets[OPPONENT].tags, None, None),
            }

            def relation_enemy(spectator, target, source):
                if spectator:
                    return NotImplemented  # fixture declares no spectator relationship service
                return entity_hostility.compare(actors.get(source.entity), actors.get(target.entity), False,
                                                 lookup=actors.get, fallback_owner=lambda actor: None,
                                                 same_group=lambda source, target: False)

            subscriptions = []  # fixture observation registrations; VM native queries also poll
            filter_context = entity_filter.Context(
                BODY, True, lookup=filter_targets.get, source=filter_targets[BODY],
                relation_enemy=relation_enemy,
                watch_filter=lambda state, actor, bits: subscriptions.append(('filter', actor.entity)),
                watch_tags=lambda state, actor: subscriptions.append(('tags', actor.entity)),
                watch_environment=lambda state, kind: subscriptions.append(('environment', kind)))
            body.world.entity_filter_context = lambda owner, component=None: filter_context

            relations = {BODY: entity_relation.Target(BODY, entity_relation.Relation(CONTROLLER))}
            relation_context = entity_relation.Context(
                BODY, True, True, True, lookup=relations.get, watch_environment=None,
                watch_relation=lambda state, target, relation: subscriptions.append(
                    ('relation', target.entity)))
            body.world.entity_relation_context = lambda owner, component=None: relation_context
        return body

    def send(self, body, index, params=None):
        self.assertGreater(runtime.send_message(body.component, MESSAGE | index,
                                               params or {}, BODY), 0)
        body.component.run_now()

    def charge(self, body):
        return body.component.entity_var(342).value()

    def enable(self, body):
        # The actual 01C7 receiver starts its #2426 stack on this message.
        # Supplying it here does not prove when the native engine emits it.
        self.send(body, 0x694)
        self.assertIs(body.component.entity_var(2426).value(), True)

    def test_loaded_child_does_not_charge_without_lifecycle_and_possession_services(self):
        body = self.body()
        body.command(125, 0)
        self.assertEqual(self.charge(body), 0)
        self.enable(body)
        body.command(250, 0)
        self.assertEqual(self.charge(body), 0)

    def test_authored_wait_adds_five_at_one_second_deadlines(self):
        body = self.body(services=True)
        self.enable(body)
        for frame, points in ((62, 0), (63, 5), (124, 5), (125, 10), (187, 10), (188, 15)):
            body.command(frame, 0)
            self.assertEqual(self.charge(body), points, frame)

    def test_authored_passive_multipliers_cap_and_disable_gate(self):
        body = self.body(services=True)
        component = body.component
        component.set(9532, 2.0)
        component.set(12918, 3.0)
        self.enable(body)
        body.command(63, 0)
        self.assertEqual(self.charge(body), 30)
        component.set(842, True)
        component.run_now()
        body.command(250, 0)
        self.assertEqual(self.charge(body), 30)
        component.set(842, False)
        body.set_ult_charge(1920)
        component.run_now()
        body.command(313, 0)
        self.assertEqual(self.charge(body), 1925)
        body.command(438, 0)
        self.assertEqual(self.charge(body), 1925)

    def test_explicit_charge_message_applies_multiplier_clamps_and_running_gate(self):
        body = self.body(services=True)
        self.enable(body)
        body.component.set(9532, 2.0)
        self.send(body, 0xEB, {0x2A: 120.0})
        self.assertEqual(self.charge(body), 240)
        self.send(body, 0xEB, {0x2A: 2000.0})
        self.assertEqual(self.charge(body), 1925)
        self.send(body, 0xEB, {0x2A: -2000.0})
        self.assertEqual(self.charge(body), 0)
        body.component.set(842, True)
        self.send(body, 0xEB, {0x2A: 120.0})
        self.assertEqual(self.charge(body), 0)

    def test_damage_message_folds_itemized_exclusions_and_authored_multipliers(self):
        body = self.body(services=True)
        self.enable(body)
        body.component.set(9532, 2.0)
        body.component.set(13022, 3.0)
        self.send(body, 0x12, {0x13: 120.0, 0x15: expr.Entity(OPPONENT),
                             0x3D: expr.Asset(0), 0x624: (
                                 expr.Map(((16056, expr.Asset(0x0D80000000003EBB)), (499, 20.0))),
                                 expr.Map(((16056, expr.Asset(0)), (499, 90.0))),
                             )})
        self.assertEqual(self.charge(body), 600)

    def test_damage_message_rejects_excluded_identifier_and_unadmitted_entity(self):
        body = self.body(services=True)
        self.enable(body)
        for target, identifier in ((OPPONENT, 0x0D800000000001FD),
                                   (OPPONENT, 0x0D8000000000025A), (BODY, 0)):
            self.send(body, 0x12, {0x13: 120.0, 0x15: expr.Entity(target),
                                    0x3D: expr.Asset(identifier)})
            self.assertEqual(self.charge(body), 0)

    def test_alternate_damage_message_requires_its_authored_identifier(self):
        body = self.body(services=True)
        self.enable(body)
        for identifier, expected in ((0, 0), (0x0D8000000000090D, 120)):
            self.send(body, 0x148, {0x13: 120.0, 0x15: expr.Entity(OPPONENT),
                                    0x3D: expr.Asset(identifier)})
            self.assertEqual(self.charge(body), expected)

    def test_authored_tag_reductions_and_priority_execute_through_tag_service(self):
        for tags, expected in (((0x3C3,), 60), ((0x11E,), 90), ((0x3C3, 0x11E), 60)):
            with self.subTest(tags=tags):
                body = self.body(services=True, tags=tags)
                self.enable(body)
                self.send(body, 0x12, {0x13: 120.0, 0x15: expr.Entity(OPPONENT), 0x3D: ()})
                self.assertEqual(self.charge(body), expected)

    def test_authored_tag_alternative_admits_target_when_static_class_bit_rejects(self):
        for tags, expected in (((), 0), ((0x2ED,), 120)):
            with self.subTest(tags=tags):
                body = self.body(services=True, tags=tags, admit=False)
                self.enable(body)
                self.send(body, 0x12, {0x13: 120.0, 0x15: expr.Entity(OPPONENT), 0x3D: ()})
                self.assertEqual(self.charge(body), expected)

    def test_authored_filter_required_tag_admits_without_static_class_bit(self):
        body = self.body(services=True, tags=(0x121,), admit=False)
        self.enable(body)
        self.send(body, 0x12, {0x13: 120.0, 0x15: expr.Entity(OPPONENT), 0x3D: ()})
        self.assertEqual(self.charge(body), 120)

    def test_removed_required_tag_rejects_through_full_filter(self):
        body = self.body(services=True, tags=(0x121,), admit=False)
        removed = entity_tags.Tags((0x121,), removed=(0x121,))
        body.world.entity_filter_context(BODY).lookup(OPPONENT).tags = removed
        body.world.entity_tag_context(BODY).lookup(OPPONENT).tags = removed
        self.enable(body)
        self.send(body, 0x12, {0x13: 120.0, 0x15: expr.Entity(OPPONENT), 0x3D: ()})
        self.assertEqual(self.charge(body), 0)


if __name__ == "__main__":
    unittest.main()
