"""Actual 1647 entry -> mover/physics relocation with declared entry inputs.

These fixtures do not supply portal placement, pair membership or transit input.
"""

import copy
import unittest

from test_symmetra_combat import arena, fire, health

from ow174.game import symmetra
from ow174.game.commands import Command
from ow174.game.script import expr, graph, runtime

DESTINATION = 0x0A40000000000078


def relocate(player, destination):
    component = player.body_script.component
    instance = component.play_loaded_graph(
        graph.graph(0x1647), source=player.body, params=((DESTINATION, destination),), policy=1)
    component.run_now()
    return instance


class TeleportActionTests(unittest.TestCase):
    def test_authored_use_graph_self_play_preserves_entry_destination_and_pair_inputs(self):
        run, owner, other = arena()
        fire(run, owner, buttons=0)
        component = owner.body_script.component
        use = component.play_loaded_graph(
            graph.graph(0x15F5), source=other.body,
            params=((0x0A40000000000008, expr.Entity(other.body)),), policy=1)
        component.run_now()
        # Declared host-admitted transit destination. The external placement,
        # trigger/code-State and eligibility services are not supplied here.
        use.write((0, 431), expr.Vec3(0, 1.5, 20))
        use.write((0, 61), 1.0)
        pair = (expr.Entity(other.body), expr.Entity(owner.body))
        use.write((0, 15820), pair)
        use.run_node(use.graph.node(10), None)
        component.run_now()
        [transit] = [i for i in component.instances.values() if i.graph.index == 0x1647]
        self.assertEqual(transit.find_var(0, 431).value(), expr.Vec3(0, 1.5, 20))
        self.assertEqual(transit.find_var(0, 15820).value(), pair)
        self.assertEqual(owner.mover.position, [0, 1.5, 20])

    def test_use_graph_position_query_resolves_known_body_only(self):
        run, owner, other = arena()
        fire(run, owner, buttons=0)
        component = owner.body_script.component
        use = component.create(graph.graph(0x15F5))
        cfg = use.graph.node(3).field('m_value')['m_configVars'][0]
        use.write((0, 13046), expr.Entity(other.body))
        self.assertEqual(use.evaluate(cfg), expr.Vec3(*other.mover.position))
        use.write((0, 13046), expr.Entity(0xAFFFFFF0))
        self.assertIsNone(use.evaluate(cfg))

    def test_authored_entry_moves_symmetra_and_other_hero_physics_and_next_command(self):
        for member in (0, 1):
            with self.subTest(member=member):
                run, owner, other = arena()
                player = (owner, other)[member]
                fire(run, owner, buttons=0)  # allocate the actual physics body
                before = health(run, player).total
                body = player.mover.body
                relocate(player, expr.Vec3(0, 1, 20))
                self.assertIs(player.mover.body, body)
                self.assertEqual(player.mover.position, [0, 1, 20])
                self.assertEqual((body.position[0], body.position[2]), (0, 20))
                player.take_commands([Command(run.frame + 1)])
                self.assertEqual(player.mover.position, [0, 1, 20])
                self.assertEqual(health(run, player).total, before)

    def test_relocation_preserves_existing_velocity_and_orientation_and_replaces_current_pose(self):
        run, owner, _other = arena()
        fire(run, owner, buttons=0)
        mover = owner.mover
        mover.yaw, mover.pitch, mover.velocity = 1234, -567, (1, 2, 3)
        frame = mover.history[-1].frame
        old_count = len(mover.history)
        relocate(owner, expr.Vec3(4.125, 8.25, 12.5))
        self.assertEqual((mover.yaw, mover.pitch, mover.velocity), (1234, -567, (1, 2, 3)))
        self.assertEqual(len(mover.history), old_count)
        self.assertEqual(mover.history[-1].frame, frame)
        self.assertEqual(mover.history[-1].position, (4.125, 8.25, 12.5))

    def test_combat_pose_cannot_interpolate_across_the_discontinuity(self):
        run, owner, _other = arena()
        fire(run, owner, 3, buttons=0)
        fight = run.match.combat
        self.assertTrue(fight.history[owner.body])
        relocate(owner, expr.Vec3(0, 1, 20))
        target = next(t for t in fight._targets() if t.owner is owner)
        self.assertIsNone(fight.seen(target, run.frame - 1))
        self.assertEqual(fight.seen(target, run.frame).feet, (0, 1, 20))

    def test_bad_destination_does_not_mutate_position_or_physics(self):
        for destination in (None, 1, expr.Entity(0), expr.Vec3(float('nan'), 1, 20),
                            expr.Vec3(0, float('inf'), 20), expr.Vec3(1e100, 1, 20)):
            with self.subTest(destination=destination):
                run, owner, _other = arena()
                fire(run, owner, buttons=0)
                before = tuple(owner.mover.position), owner.mover.body.position
                relocate(owner, destination)
                self.assertEqual((tuple(owner.mover.position), owner.mover.body.position), before)

    def test_dead_body_cannot_relocate(self):
        run, owner, enemy = arena()
        fire(run, owner, buttons=0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        before = tuple(owner.mover.position), owner.mover.body.position
        relocate(owner, expr.Vec3(0, 1, 20))
        self.assertEqual((tuple(owner.mover.position), owner.mover.body.position), before)

    def test_ended_match_cannot_relocate(self):
        run, owner, _other = arena()
        fire(run, owner, buttons=0)
        run.match.ended = True
        before = tuple(owner.mover.position), owner.mover.body.position
        relocate(owner, expr.Vec3(0, 1, 20))
        self.assertEqual((tuple(owner.mover.position), owner.mover.body.position), before)

    def test_uninstalled_world_and_other_transform_profiles_remain_unsupported(self):
        run, owner, _other = arena()
        fire(run, owner, buttons=0)
        component = owner.body_script.component
        instance = component.create(graph.graph(0x1647))
        # Alter only the supplied action profile; do not edit shared graph data.
        original = instance.graph.node(1)
        record = {'pos': original.pos, 'class': original.cls, 'uid': original.uid,
                  'rid': original.rid, 'remote': original.remote, 'state': original.state,
                  'client_only': original.client_only, 'server_only': original.server_only,
                  'fields': copy.deepcopy(original.fields)}
        record['fields']['m_target'] = {'$': 'STUConfigVarInt', 'm_value': 1}
        node = graph.Node(record)
        self.assertIs(symmetra.teleport_body(owner.body_script.world, instance, node), NotImplemented)
        self.assertIs(runtime.World().teleport_body(instance, original), NotImplemented)


if __name__ == '__main__':
    unittest.main()
