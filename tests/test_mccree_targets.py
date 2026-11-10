"""Deadeye bridge geometry and ownership; effect fixture does not model damage."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import Run
from test_game_graph_analysis import make_graph

from ow174.game import combat, mccree
from ow174.game.bits import BitReader, BitWriter
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import heroes
from ow174.game.script import decode, expr, graph
from ow174.game.statescript import write_payload


class DeadeyeTargetsTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("caster", "enemy", "ally"))
        self.match = self.run.match
        self.match.bots.clear()
        self.player, self.enemy, self.ally = self.run.players
        self.assertTrue(self.match.switch_hero(self.player, heroes()[mccree.HERO]))
        self.script = self.player.body_script
        self.component = self.script.component
        self.world = self.script.world
        self.root = next(i for i in self.component.instances.values() if i.graph.index == mccree.ULTIMATE)
        self.query = self.root.state(self.root.graph.node(26).state)
        self.visible = self.root.state(self.root.graph.node(86).state)
        self.effects = self.root.state(self.root.graph.node(11).state)
        self.enemy.team, self.ally.team = 1, self.player.team
        self.place(self.enemy, 3, 8)
        self.place(self.ally, -3, 8)
        self.player.mover.yaw = self.player.mover.pitch = 0
        # Only the lifetime/query tests use this inert graph. Actual01EE is an
        # external runtime input, verified separately with real input and damage.
        self.definition = make_graph(mccree.TARGET_EFFECT)
        original = graph.graph
        self.loader = patch.object(graph, "graph", side_effect=lambda index:
                                  self.definition if index == mccree.TARGET_EFFECT else original(index))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def place(self, player, dx, dz):
        x, y, z = self.player.mover.position
        player.mover.position[:] = [x + dx, y, z + dz]

    def select(self):
        self.query.refresh()
        self.visible.refresh()
        mccree.effects(self.world, self.effects)

    def test_only_living_enemy_in_authored_range_and_cone(self):
        self.assertEqual(mccree.targets(self.world, self.query), (self.enemy.body,))
        for dx, dz in ((0, -8), (20, 2), (0, 201)):
            self.place(self.enemy, dx, dz)
            self.assertEqual(mccree.targets(self.world, self.query), ())
        self.place(self.enemy, 0, 8)
        target = next(t for t in self.match.combat._targets() if t.entity == self.enemy.body)
        self.match.combat.vitals_of(target).died_at = 0
        self.assertEqual(mccree.targets(self.world, self.query), ())

    def test_occlusion_separates_lifetime_and_visible_charge_queries(self):
        with patch("ow174.game.combat.capsule_visible", return_value=False):
            self.select()
        self.assertEqual(self.query.targets, (self.enemy.body,))
        self.assertEqual(self.visible.targets, ())
        self.assertEqual(set(self.effects.children), {self.enemy.body})
        child = self.component.instances[self.effects.children[self.enemy.body]]
        self.assertIsNone(mccree.shot_aim(self.match.combat, self.player,
                                       type("Volley", (), {"instance": child})()))

    def test_off_crosshair_aim_rejects_retired_or_newly_occluded_target(self):
        self.select()
        child = self.component.instances[self.effects.children[self.enemy.body]]
        state = type("Volley", (), {"instance": child})()
        origin, direction = mccree.shot_aim(self.match.combat, self.player, state)
        self.assertEqual(origin, self.match.combat._eye(self.player))
        self.assertGreater(direction[0], 0.3)
        with patch("ow174.game.combat.capsule_visible", return_value=False):
            self.assertIsNone(mccree.shot_aim(self.match.combat, self.player, state))
        self.enemy.spawned = False
        self.assertIsNone(mccree.shot_aim(self.match.combat, self.player, state))

    def test_target_exit_destroys_child_and_reentry_restarts_instance(self):
        self.select()
        first = self.effects.children[self.enemy.body]
        self.select()
        self.assertEqual(self.effects.children[self.enemy.body], first)
        self.place(self.enemy, 0, -8)
        self.select()
        self.assertNotIn(first, self.component.instances)
        self.assertFalse(self.effects.children)
        self.place(self.enemy, 3, 8)
        self.select()
        second = self.effects.children[self.enemy.body]
        self.assertNotEqual(first, second)
        self.effects.on_end(False)
        self.assertNotIn(second, self.component.instances)
        self.assertFalse(self.effects.children)

    def test_short_capsule_is_hit_even_when_its_generic_eye_is_above_the_body(self):
        self.select()
        child = self.component.instances[self.effects.children[self.enemy.body]]
        state = type("Volley", (), {"instance": child})()
        target = next(t for t in self.match.combat._targets() if t.owner is self.enemy)
        short = replace(target, height=1.5, radius=0.5, eye=1.6)
        with patch.object(self.match.combat, "_targets", return_value=[short]):
            origin, direction = mccree.shot_aim(self.match.combat, self.player, state)
        a, b = short.segment()
        self.assertIsNotNone(combat.ray_capsule(origin, direction, a, b, short.radius))

    def test_query_end_retains_authored_array_for_firing_and_payload_roundtrips(self):
        self.select()
        payload = self.query.payload()
        out = BitWriter()
        write_payload(out, self.query.node.cls, payload, 0, 16)
        reader = BitReader(out.getvalue())
        got = decode.read_payload(reader, self.query.node.cls, 0, 16, owner=True, full=False)
        self.assertEqual(reader.pos, out.count)
        self.assertEqual(got["targets"], ((self.enemy.body, 0),))
        self.query.on_end(False)
        self.assertFalse(self.query.targets)
        self.assertEqual(self.component.entity_var(2551).value(), (expr.Entity(self.enemy.body),))

    def test_query_and_children_are_causal_snapshot_state(self):
        before = snapshot_digest(export_component_vm(self.component))
        self.select()
        after = snapshot_digest(export_component_vm(self.component))
        self.assertNotEqual(before, after)
        self.effects.children.clear()
        self.assertNotEqual(after, snapshot_digest(export_component_vm(self.component)))

    def test_missing_external_effect_cannot_silently_create_targets(self):
        with patch.object(graph, "graph", return_value=None):
            self.assertEqual(mccree.targets(self.world, self.query), ())
            self.assertTrue(mccree.effects(self.world, self.effects))
        self.assertFalse(self.effects.children)

    def test_bridge_is_scoped_to_mccree_and_root_nodes(self):
        self.world.hero = 0
        self.assertEqual(mccree.targets(self.world, self.query), ())
        self.assertIs(mccree.effects(self.world, self.effects), NotImplemented)
        self.assertIs(mccree.targets(self.world, self.effects), NotImplemented)

    def test_body_retirement_releases_effect_instances_without_more_ticks(self):
        self.select()
        child = self.effects.children[self.enemy.body]
        self.script.retire()
        self.assertNotIn(child, self.component.instances)
        self.assertFalse(self.effects.children)
        self.assertIsNone(self.world.match)

    def test_invalid_owner_releases_existing_effect_instances(self):
        self.select()
        child = self.effects.children[self.enemy.body]
        self.player.gone = True
        mccree.effects(self.world, self.effects)
        self.assertNotIn(child, self.component.instances)
        self.assertFalse(self.effects.children)
