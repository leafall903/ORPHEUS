"""Actual source identity reaches owner/observer descriptors; native colors stay pending."""

import json
import os
import unittest
from pathlib import Path

from ow174.game import lucio, sound_barrier
from ow174.game.bits import BitReader
from ow174.game.collision import flat_world
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.decode import ClientBody
from ow174.game.script.entity_team import index_from_filters
from tests.runtime.test_game_combat import Run, in_world
from tests.runtime.test_game_team_list import read_create


class LucioEffectSourceTests(unittest.TestCase):
    def contract(self):
        fixtures = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                        or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        return json.loads((fixtures / "lucio-effect-source-identity-v1.json").read_text())

    def scene(self):
        inputs = self.contract()["input"]
        run = Run(names=("Caster", "Ally", "Enemy"))
        caster, ally, enemy = run.players
        caster.team = ally.team = inputs["caster_team"]
        enemy.team = inputs["enemy_team"]
        for actor, hero, point in ((caster, lucio.HERO, (1000, 0, 1000)),
                                   (ally, SOLDIER, (1000, 0, 1004)),
                                   (enemy, SOLDIER, (1020, 0, 1004))):
            run.match.switch_hero(actor, heroes()[hero])
            actor.mover.position[:] = point
            actor.mover.body = None
            actor.body_script.create_arrived = True
        run.match.collision_world = flat_world(0)
        for _ in range(inputs["warmup_commands"]):
            run.step({})
        return run, caster, ally, enemy

    def assert_sources(self, run, caster, recipient, instances, ally_viewer, enemy_viewer):
        # Native team lookup uses the source body's transmitted component26,
        # rather than a palette selected globally or rewritten for each viewer.
        record = run.match._body_builder(caster, None)(0)
        fields = read_create(BitReader(record.getvalue()))
        self.assertEqual(fields[26][0], caster.team_bit)
        self.assertEqual(index_from_filters(fields[26][0]), caster.team)
        owner = ClientBody.for_hero(recipient.hero.guid)
        initial = recipient.body_script.spawn_frames()[0]
        owner.apply(initial)
        initial.stream.arrived(initial.chunk_last)
        for instance in instances:
            self.assertEqual(instance.source_entity(), caster.body)
            self.assertEqual(owner.sources[instance.id], caster.body)
        run.step({})
        updates = recipient.body_script.frames(replace_initial=True)
        self.assertTrue(updates)
        frame = owner.apply(updates[-1])
        self.assertTrue(frame.owner)
        for instance in instances:
            self.assertEqual(owner.sources[instance.id], caster.body)
        for viewer in (ally_viewer, enemy_viewer):
            create = run.match.remote_body_create(viewer, recipient)
            viewer.client.queue_entities([create])
            run.match.combat.observers.update()
            updates = [u for u in viewer.client.entities
                       if u.entity == recipient.body and u.chunk is not None]
            self.assertTrue(updates)
            client = ClientBody.for_hero(recipient.hero.guid)
            client.apply(updates[-1])
            for instance in instances:
                self.assertEqual(client.sources[instance.id], caster.body)
        # A new observer receives the existing effect, after the actual create-ACK gate.
        late = run.match.add_player(4, "LateEnemy", SOLDIER, self.contract()["input"]["enemy_team"], False)
        in_world(run.match, late)
        caster_create = run.match.remote_body_create(late, caster)
        create = run.match.remote_body_create(late, recipient)
        watch = run.match.combat.observers.watches[(late.slot, recipient.body)]
        self.assertFalse(watch.arrived)
        run.match.combat.observers.update()
        self.assertFalse(watch.stream.last)
        late.client.queue_entities([create])
        run.match.combat.observers.update()
        updates = [u for u in late.client.entities
                   if u.entity == recipient.body and u.chunk is not None]
        self.assertTrue(updates)
        client = ClientBody.for_hero(recipient.hero.guid)
        client.apply(updates[-1])
        for instance in instances:
            self.assertEqual(client.sources[instance.id], caster.body)
        # The instigator descriptor must not become the recipient while the
        # caster's separate body create is still awaiting delivery.
        self.assertFalse(late.seen[caster.body])
        late.client.queue_entities([caster_create])
        self.assertTrue(late.seen[caster.body])
        for instance in instances:
            self.assertEqual(client.sources[instance.id], caster.body)

    def test_crossfade_receiver_and_controller_publish_original_instigator(self):
        run, caster, ally, enemy = self.scene()
        aura = run.match.combat.lucio_auras.leases[(caster.body, ally.body)].instance
        controller = next(i for i in ally.body_script.component.instances.values()
                          if i.graph.index == 0x07C7)
        self.assert_sources(run, caster, ally, (aura, controller), caster, enemy)
        own_aura = run.match.combat.lucio_auras.leases[(caster.body, caster.body)].instance
        self.assertEqual(caster.body_script.snapshot().sources.get(own_aura.id), caster.body)

    def test_sound_barrier_receiver_publishes_caster_without_recoloring_owner(self):
        inputs = self.contract()["input"]
        run, caster, ally, enemy = self.scene()
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(inputs["initial_charge"])
        for index in range(inputs["cast_commands"]):
            run.step({caster: (0, 0, inputs["cast_button"] if index < inputs["held_commands"] else 0)})
        manager = run.match.combat.sound_barriers
        receiver = manager.shielded[ally.body].application
        self.assert_sources(run, caster, ally, (receiver,), caster, enemy)
        self.assertEqual(receiver.graph.index, sound_barrier.RECEIVER)
        self.assertEqual(ally.team, caster.team)
        self.assertNotEqual(enemy.team, caster.team)
        self.assertNotIn(enemy.body, manager.shielded)
