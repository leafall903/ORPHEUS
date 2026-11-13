"""Skip unrelated VM scans without gating a Reaper source by its recipient's hero."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import reaper, world
from ow174.game import reaper_shadow_actor as actors
from ow174.game.content import find_hero
from ow174.game.script import expr, graph
from tests.runtime import test_game_reaper as scenarios


class CountedInstances(dict):
    def __init__(self, values):
        super().__init__(values)
        self.scans = 0

    def values(self):
        self.scans += 1
        return super().values()


def original_live(match, source_entity=None):
    for player in match.players:
        script = player.body_script
        if script is None or script.retired:
            continue
        if source_entity is not None and script.component.entity != source_entity:
            continue
        for instance in script.component.instances.values():
            if instance.graph.index != reaper.SHADOW_GRAPH:
                continue
            state = instance.states.get(43)
            if type(state) is actors.ShadowActor and state.actor_entity and state.actor_point is not None:
                yield state


class ReaperShadowScanTests(unittest.TestCase):
    def test_equivalent_sources_nonreaper_observer_ack_and_real_source_replacement(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / "fixtures/behavior/reaper-shadow-scan-v1.json").read_text(encoding="utf-8"))
        run, owner, observer, _target = scenarios.ReaperTests().scenario()
        script = owner.body_script
        shadow = next(i for i in script.component.instances.values() if i.graph.index == reaper.SHADOW_GRAPH)
        state = shadow.state(43)
        run.step({owner: (0, 5000, 16)})
        for _ in range(8):
            run.step({owner: (0, 5000, 0)})
        run.step({owner: (0, 5000, 1)})
        self.assertGreater(state.actor_entity, 0)
        self.assertIs(actors._source(state), owner)
        # An additional allowed00F2 instance must not disappear behind a root-id shortcut.
        axes = zip(state.actor_point, fixture["extraPointOffset"], strict=True)
        point = expr.Vec3(*(p + d for p, d in axes))
        extra = script.component.create(graph.graph(reaper.SHADOW_GRAPH),
                                        values={(expr.INSTANCE, 985): point}, init=False)
        extra_state = extra.state(43)
        extra_state.begin()
        self.assertIs(actors._source(extra_state), owner)
        self.assertGreater(extra_state.actor_entity, 0)
        expected = list(original_live(run.match))
        self.assertEqual(len(expected), fixture["actorCount"])
        counted = CountedInstances(observer.body_script.component.instances)
        observer.body_script.component.instances = counted
        source = script.component.entity
        for target in (None, source, observer.body, 0xA0007FFF):
            with self.subTest(source=target):
                before = list(original_live(run.match, target))
                counted.scans = 0
                self.assertEqual(list(actors.live_actors(run.match, source_entity=target)), before)
                self.assertEqual(counted.scans, fixture["unrelatedInstanceScans"])
        creates = [u.entity for u in actors.spawn_updates(run.match)]
        self.assertEqual(creates, [s.actor_entity for s in expected])
        self.assertIs(actors.lookup(run.match, extra_state.actor_entity), extra_state)
        self.assertFalse(reaper.is_reaper(observer))
        client = SimpleNamespace(player=observer, actor_generation=lambda _entity: fixture["generation"],
                                 _actor_create_acks={})
        record = world.EntityUpdate(source, chunk=object())
        self.assertFalse(actors.record_ready(client, record))
        client._actor_create_acks[state.actor_entity] = fixture["generation"]
        client._actor_create_acks[extra_state.actor_entity] = fixture["staleGeneration"]
        self.assertFalse(actors.record_ready(client, record))
        client._actor_create_acks[extra_state.actor_entity] = fixture["generation"]
        self.assertTrue(actors.record_ready(client, record))
        client.actor_generation = lambda _entity: fixture["generation"] + 1
        self.assertFalse(actors.record_ready(client, record))
        old_ids = [s.actor_entity for s in expected]
        original_new_body = owner.new_body
        transitions = []

        def observe_replacement(entity):
            self.assertFalse(reaper.is_reaper(owner))
            self.assertIs(owner.body_script, script)
            self.assertFalse(script.retired)
            self.assertEqual(list(actors.live_actors(run.match, source_entity=source)), expected)
            original_new_body(entity)
            self.assertNotEqual(owner.body, source)
            self.assertEqual(list(actors.live_actors(run.match, source_entity=source)), expected)
            transitions.append(entity)

        with patch.object(owner, "new_body", side_effect=observe_replacement):
            self.assertTrue(run.match.switch_hero(owner, find_hero("soldier")))
        self.assertEqual(len(transitions), 1)
        self.assertTrue(script.retired)
        self.assertEqual([s.actor_entity for s in expected], [0, 0])
        self.assertEqual(list(actors.live_actors(run.match)), [])
        for entity in old_ids:
            self.assertIsNone(actors.lookup(run.match, entity))
        self.assertTrue(actors.record_ready(client, record))
