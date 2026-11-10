"""The native wheel graphs enter the ordinary player body stream."""

import json
import struct
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, in_world
from test_game_entity_lifecycle import client as game_client

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content.collection import Collection
from ow174.game import combat, messages, observers, props, statescript
from ow174.game.bits import BitWriter
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import RayAttachment, RayHit, RayWorld, StaticCollisionProvider, World, flat_world
from ow174.game.commands import FIRE, Command
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes
from ow174.game.match import Match
from ow174.game.world import OP_CREATE, EntityUpdate
from ow174.game.script import decode, expr, graph, runtime, spray_placement
from ow174.game.script.driver import BodyScript, BodyStream, ULT_COST
from ow174.paths import EMOTE_ALIAS_DATA, EMOTE_ANY_CATEGORY, EMOTE_BLEND_TREE_DATA, EMOTE_DURATION_DATA, EMOTE_TAGGED_DATA, GREETING_DURATION_DATA, VOICE_EFFECT_DATA


class CommunicationWheelTests(unittest.TestCase):
    def test_body_create_mounts_and_replicates_the_authored_wheels(self):
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        body = player.body_script
        client = decode.ClientBody.for_hero(SOLDIER)
        self.assertEqual(body.frames(), [])

        create.stream.arrived(0)
        [tree] = body.frames()
        client.apply(tree)
        body.stream.arrived(tree.chunk_last)
        instances = {index: instance for instance, index in client.graphs.items()}
        parent = instances[0x004B]
        self.assertEqual(client.records[instances[0x02CF]], (0x02CF, (parent, 6)))
        self.assertEqual(client.records[instances[0x05AD]], (0x05AD, (parent, 7)))
        self.assertNotIn(0x01CB, instances)  # 004B's separate tagged filter

        body.command(body.frame + 1, 0)
        [owner] = body.frames()
        client.apply(owner)
        communication = instances[0x05AD]
        for state in (25, 26, 40, 41, 42):
            self.assertTrue(client.states[communication, state])
        self.assertTrue(client.states[instances[0x02CF], 20])

    def test_client_out_instance_action_reaches_the_current_body(self):
        def receive(connection, sequence, action, confirmed):
            # Protocol 1, message offset 8, then u64, bool and nested u32.
            record = b"\x01\x08" + struct.pack("<QBI", action, confirmed, 0)
            self.assertEqual(messages.encode(21608, {
                "+0x78": action, "+0x80": confirmed, "+0x84": {"+0x0": 0},
            }), record)
            packet = BitWriter()
            messages.write_channel(packet, [(sequence, record)])
            packet.bit(0)
            connection._payload_in(packet.getvalue(), 17.0)

        run = Run(names=("Source", "Observer"))
        player, observer = run.players
        connection = game_client()
        connection.player = player
        player.client = connection
        source = player.body_script
        wheel = next(i for i in source.component.instances.values() if i.graph.index == 0x05AD)
        selected = 0x0A9000000000016B
        for sequence, action in enumerate((0x0A9000010000016B, 0x0A90000000000001)):
            receive(connection, sequence, action, True)
        self.assertEqual(run.match._body_messages, [])

        receive(connection, 2, selected, True)
        receive(connection, 2, selected, True)
        self.assertEqual(len(run.match._body_messages), 1)
        run.step({})
        self.assertEqual(wheel.vars[29699].value(), expr.Asset(selected))
        self.assertIs(wheel.vars[9253].value(), True)
        [callout] = [i for i in source.component.instances.values() if i.graph.index == 0x0686]
        self.assertEqual(callout.vars[3793].value(), expr.Asset(0x0C00000000005A0B))
        self.assertFalse(any(i.graph.index == 0x0686 for i in observer.body_script.component.instances.values()))

        receive(connection, 3, selected, False)
        run.step({})
        self.assertIs(wheel.vars[9253].value(), False)

    def test_acknowledge_marker_selects_the_authored_target_branch(self):
        run = Run(names=("Speaker", "Teammate"))
        speaker, teammate = run.players
        teammate.team = speaker.team
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        teammate.mover.position = (0.0, 0.0, 10.0)
        connection = game_client()
        connection.player = speaker
        speaker.client = connection
        wheel = next(i for i in speaker.body_script.component.instances.values() if i.graph.index == 0x05AD)
        marker = 0xA0000123
        action = 0x0A90000000000170
        selected = []
        native_action = runtime.HANDLERS["STU_631D36C3"]

        def target_branch(instance, node, caller):
            selected.append((instance.id, node.pos, instance.evaluate(node.field("m_4A5CA514"))))
            with patch.object(instance, "evaluate", side_effect=AssertionError("base Action read marker data")):
                self.assertIs(native_action(instance, node, caller), True)
            return True

        def receive(sequence, selected_action, entity):
            packet = BitWriter()
            record = b"\x01\x08" + struct.pack("<QBI", selected_action, True, entity)
            messages.write_channel(packet, [(sequence, record)])
            packet.bit(0)
            connection._payload_in(packet.getvalue(), 17.0)

        with patch.dict(runtime.HANDLERS, {"STU_631D36C3": target_branch}):
            receive(0, action, marker)
            run.match.deliver_body_messages()
            self.assertEqual(selected, [(wheel.id, 165, expr.Entity(marker))])
            self.assertEqual(wheel.vars[16376].value(), expr.Entity(marker))
            self.assertEqual(run.match._body_messages, [])
            self.assertEqual(speaker.body_script.world.wheel_callouts, [])
            speaker.body_script.command(speaker.body_script.frame + 150, 0)

            receive(1, action, 0)
            run.match.deliver_body_messages()
            self.assertIsNone(wheel.vars[16376].value())
            self.assertEqual(len(selected), 1)
            self.assertEqual([row[3] for row in run.match._body_messages], [0x02400000000005AC])
            run.match.deliver_body_messages()
            speaker.body_script.command(speaker.body_script.frame + 150, 0)

            receive(2, 0x0A9000000000016B, marker)
            run.match.deliver_body_messages()
            self.assertIsNone(wheel.vars[16376].value())
            self.assertEqual(len(selected), 1)

    def test_wheel_tracks_visible_players_before_the_authored_recipient_filter(self):
        run = Run(names=("Speaker", "Aligned", "Nearer"))
        speaker, aligned, nearer = run.players
        match = run.match
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        aligned.mover.position = (0.0, 0.0, 15.0)
        nearer.mover.position = (0.8, 0.0, 8.0)
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)

        def select(action=0x1E5):
            script.command(script.frame + 150, 0)
            match.communication_action(speaker, {"+0x78": 0x0A90000000000000 | action, "+0x80": True})
            match.deliver_body_messages()
            return wheel.vars[375].value()

        self.assertEqual(select(), (expr.Entity(aligned.body),))

        wall = ((-0.4, 0, 5, 0.4, 0, 5, -0.4, 4, 5),
                (0.4, 0, 5, 0.4, 4, 5, -0.4, 4, 5))
        match.collision_world = World(wall)
        self.assertEqual(select(), (expr.Entity(nearer.body),))
        match.collision_world = World(())
        match.collision_world.ray_geometry = RayWorld(wall, masks=(0x01, 0x01))
        self.assertEqual(select(), (expr.Entity(nearer.body),))  # 0079 static category includes 0x01
        with patch.object(script.world, "tracked_targets", wraps=script.world.tracked_targets) as track:
            self.assertEqual(select(0x16C), (expr.Entity(nearer.body),))
            self.assertEqual(track.call_args.args[0].node.pos, 115)
        match.collision_world.ray_geometry = RayWorld(wall, masks=(0x20, 0x20))
        self.assertEqual(select(), (expr.Entity(aligned.body),))
        nearer.mover.position = (0.0, 0.0, 41.0)
        match.collision_world = World(wall)
        self.assertEqual(select(), ())
        self.assertFalse(wheel.node_state(wheel.graph.node(115)).active)

        mercy = next(hero for hero in heroes().values() if hero.body == 0x04000000000000E4)
        self.assertTrue(match.switch_hero(speaker, mercy))
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        nearer.mover.position = (0.8, 0.0, 8.0)
        match.collision_world.ray_geometry = RayWorld(wall, masks=(0x01, 0x01))
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)
        with patch.object(script.world, "tracked_targets", wraps=script.world.tracked_targets) as track:
            self.assertEqual(select(0x16C), (expr.Entity(nearer.body),))
            self.assertEqual(track.call_args.args[0].node.pos, 113)

    def test_wheel_distinguishes_target_owned_ray_hits_from_blockers(self):
        for owner in ("target", "other", "world"):
            with self.subTest(owner=owner):
                run = Run(names=("Speaker", "Target"))
                speaker, target = run.players
                speaker.mover.position = (0.0, 0.0, 0.0)
                speaker.mover.yaw = speaker.mover.pitch = 0
                target.mover.position = (0.0, 0.0, 15.0)
                rays = World(())
                run.match.collision_world = rays
                attachment = None
                if owner != "world":
                    entity = target.body if owner == "target" else 0x8000022E
                    attachment = RayAttachment(entity, (0.0, 0.0, 5.0), (0.0, 0.0, 0.0, 1.0))
                hit = RayHit(5.0, (0.0, 1.55, 5.0), (0.0, 0.0, -1.0), 1, 0, attachment)
                script = speaker.body_script
                wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)

                script.command(script.frame + 150, 0)
                with patch.object(rays, "cast_ray", return_value=hit) as cast_ray:
                    run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
                    run.match.deliver_body_messages()
                fractions = (0.3,) if owner == "target" else (0.3, 0.6)
                self.assertEqual(cast_ray.call_count, len(fractions))
                height = combat.mover_data(target.hero.body).stand_height
                for call, fraction in zip(cast_ray.call_args_list, fractions, strict=True):
                    origin, direction, length, mask = call.args
                    for actual, expected in zip(origin, (0.0, 1.0, 0.0), strict=True):
                        self.assertAlmostEqual(actual, expected)
                    self.assertEqual(mask, 0x1FDF)
                    endpoint = tuple(a + b * length for a, b in zip(origin, direction, strict=True))
                    self.assertAlmostEqual(endpoint[1], fraction * height)
                    self.assertAlmostEqual(endpoint[2], 15.0)
                expected = (expr.Entity(target.body),) if owner == "target" else ()
                self.assertEqual(wheel.vars[375].value(), expected)

    def test_model_pose_composes_sampled_parent_and_reference_child(self):
        key = 0x0DC0000000000001
        root = props.BoneTransform((0, 0, 0, 1), (1, 1, 1), (0, 0, 0))
        child = replace(root, translation=(1, 2, 3))
        reference = {5: (None, root), 9: (5, child)}
        hardpoints = {key: (9, (0.25, 0.5, 1.0))}
        world = (0, 0, -2, 0, 0, 3, 0, 0, 2, 0, 0, 0, 10, 20, 30, 1)
        base = props.ModelPose.from_local_bones(hardpoints, reference, world, {})
        self.assertEqual(base.hardpoint_position(key), (18.0, 27.5, 27.5))

        sampled = replace(root, scale=(2, 3, 4), translation=(1, 0.5, 2))
        active = props.ModelPose.from_local_bones(hardpoints, reference, world, {5: sampled})
        self.assertEqual(active.hardpoint_position(key), (46.0, 44.0, 23.0))
        self.assertEqual(base.hardpoint_position(key), (18.0, 27.5, 27.5))

    def test_wheel_rays_use_current_model_anchors_in_order(self):
        run = Run(names=("Speaker", "Target"))
        speaker, target = run.players
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        target.mover.position = (0.0, 0.0, 15.0)
        rays = run.match.collision_world = World(())
        primary, secondary = 0x0DC0000000000001, 0x0DC00000000001CD
        height = combat.mover_data(target.hero.body).stand_height
        obstruction = RayHit(5.0, (0.0, 1.0, 5.0), (0.0, 0.0, -1.0), 1, 0)
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)
        positions = {5: (0.0, 1.5, 15.0), 6: (0.0, 1.2, 15.0)}
        lookups = []

        def matrix(bone):
            lookups.append(bone)
            if bone not in positions:
                return None
            return (0, 0, -2, 0, 0, 3, 0, 0, 2, 0, 0, 0, *positions[bone], 1)

        pose = props.ModelPose(
            {primary: (5, (0.25, 0.125, 0.25)), secondary: (6, (0.0, 0.1, 0.0))},
            matrix,
        )
        run.match.combat.model_pose_for = lambda entity: pose if entity == target.body else None
        for blocked, remove_secondary, expected, bones in (
            (0, False, [(0.5, 1.875, 14.5)], [5]),
            (1, False, [(0.5, 1.875, 14.5), (0, 0.3 * height, 15)], [5]),
            (2, False, [(0.5, 1.875, 14.5), (0, 0.3 * height, 15), (0, 1.5, 15)], [5, 6]),
            (2, True, [(0.5, 1.875, 14.5), (0, 0.3 * height, 15), (0, 0.6 * height, 15)], [5, 6]),
        ):
            with self.subTest(blocked=blocked, remove_secondary=remove_secondary):
                lookups.clear()
                if remove_secondary:
                    del positions[6]
                script.command(script.frame + 150, 0)
                with patch.object(rays, "cast_ray", side_effect=[obstruction] * blocked + [None]) as ray:
                    run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
                    run.match.deliver_body_messages()
                self.assertEqual(wheel.vars[375].value(), (expr.Entity(target.body),))
                self.assertEqual(ray.call_count, len(expected))
                self.assertEqual(lookups, bones)
                for call, wanted in zip(ray.call_args_list, expected, strict=True):
                    origin, direction, length, _ = call.args
                    endpoint = tuple(a + b * length for a, b in zip(origin, direction, strict=True))
                    for actual, coordinate in zip(endpoint, wanted, strict=True):
                        self.assertAlmostEqual(actual, coordinate)

    def test_wheel_orders_admitted_actor_shapes_with_static_hits(self):
        run = Run(names=("Speaker", "Target", "Blocker"))
        speaker, target, blocker = run.players
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        target.mover.position = (0.0, 0.0, 15.0)
        blocker.mover.position = (8.0, 0.0, 5.0)
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)
        poses = {}
        for player, depth in ((blocker, 5.0), (target, 14.0)):
            matrix = (0, 0, -1, 0, 0, 2, 0, 0, 1, 0, 0, 0, 0, 1, depth, 1)
            poses[player.body] = props.ModelPose({}, {5: matrix}.get, (
                (5, 0x0100, ("sphere", 0x17DF, (0, 0, 0), 0.5)),
            ))
        run.match.combat.model_pose_for = poses.get
        run.match.collision_world = World(())
        blocker.team = speaker.team
        args = speaker.body, expr.Vec3(0, 1.5, 0), 25, 20, 0x1FDF
        for profile in (0x0690000000000079, 0x0690000000000111):
            with self.subTest(default_profile=profile):
                with patch.dict(poses, {blocker.body: None}):
                    self.assertEqual(run.match.combat.communication_target(*args, profile=profile), target.body)
                self.assertIsNone(run.match.combat.communication_target(*args, profile=profile))
        script.command(script.frame + 150, 0)
        run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
        run.match.deliver_body_messages()
        self.assertEqual(wheel.vars[375].value(), ())

        wall = World(((-2, 0, 14.8, 2, 0, 14.8, -2, 3, 14.8),
                      (2, 0, 14.8, 2, 3, 14.8, -2, 3, 14.8)))
        for admission, world, expected in (
            (blocker.body, World(()), ()),
            (None, World(()), (expr.Entity(target.body),)),
            (target.body, wall, (expr.Entity(target.body),)),
            (None, wall, ()),
        ):
            with self.subTest(admission=admission, wall=bool(world)):
                run.match.collision_world = world

                def admits(profile, source, actor, category, mask):
                    self.assertEqual((profile, source, category, mask),
                                     (0x0690000000000079, speaker.body, 0x0100, 0x17DF))
                    return actor == admission

                run.match.combat.wheel_actor_admits = admits
                script.command(script.frame + 150, 0)
                run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
                run.match.deliver_body_messages()
                self.assertEqual(wheel.vars[375].value(), expected)

    def test_wheel_retries_above_partial_body_cover(self):
        run = Run(names=("Speaker", "Target"))
        speaker, target = run.players
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        target.mover.position = (0.0, 0.0, 12.0)
        height = combat.mover_data(target.hero.body).stand_height
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)
        for cutoff, expected in ((0.5 + 0.225 * height, (expr.Entity(target.body),)), (2.0, ())):
            with self.subTest(cutoff=cutoff):
                wall = ((-1, 0, 6, 1, 0, 6, -1, cutoff, 6),
                        (1, 0, 6, 1, cutoff, 6, -1, cutoff, 6))
                world = run.match.collision_world = World(wall)
                script.command(script.frame + 150, 0)
                with patch.object(world, "cast_ray", wraps=world.cast_ray) as ray:
                    run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
                    run.match.deliver_body_messages()
                self.assertEqual(ray.call_count, 2)
                self.assertEqual(wheel.vars[375].value(), expected)

    def test_wheel_ranks_the_projected_rectangle_corner(self):
        run = Run(names=("Speaker", "Aligned", "Nearer"))
        speaker, aligned, nearer = run.players
        run.match.collision_world = World(())
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        aligned.mover.position = (0.0, 0.0, 15.0)
        dimensions = combat.mover_data(nearer.hero.body)
        eye = combat.eye_height(speaker.hero.body)
        # The reticle touches the upper corner of the nearer body's screen rectangle.
        nearer.mover.position = (0.95 * dimensions.stand_radius, eye - dimensions.stand_height + 0.01, 8.0)
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)

        script.command(script.frame + 150, 0)
        run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
        run.match.deliver_body_messages()
        self.assertEqual(wheel.vars[375].value(), (expr.Entity(nearer.body),))

    def test_wheel_uses_candidate_origin_for_world_distance(self):
        run = Run(names=("Speaker", "Lower", "Higher"))
        speaker, lower, higher = run.players
        run.match.collision_world = World(())
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        # Both screen rectangles cover the reticle. The lower player's
        # capsule center is closer, while the higher player's root is closer.
        dimensions = combat.mover_data(lower.hero.body)
        lower.mover.position = (0.0, 1.0 - dimensions.stand_height * 0.5, 8.0)
        higher.mover.position = (0.0, 1.0, 8.01)
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)

        script.command(script.frame + 150, 0)
        run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
        run.match.deliver_body_messages()
        self.assertEqual(wheel.vars[375].value(), (expr.Entity(higher.body),))

    def test_wheel_pitches_search_origin_around_first_person_pivot(self):
        run = Run(names=("Speaker", "Target"))
        speaker, _ = run.players
        run.match.collision_world = World(())
        speaker.mover.position = (2.0, 3.0, 4.0)
        speaker.mover.yaw = 16384
        speaker.mover.pitch = 8192
        pivot = combat.eye_height(speaker.hero.body)
        rotated = (1.0 - pivot) * (0.5 ** 0.5)
        expected = (2.0 + rotated, 3.0 + pivot + rotated, 4.0)

        script = speaker.body_script
        script.command(script.frame + 150, 0)
        with patch.object(combat, "segment_distance", wraps=combat.segment_distance) as distance:
            run.match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
            run.match.deliver_body_messages()
        origin = distance.call_args_list[0].args[0]
        for actual, wanted in zip(origin, expected, strict=True):
            self.assertAlmostEqual(actual, wanted)

    def test_confirmed_action_directs_callout_only_to_the_aimed_teammate(self):
        cases = (
            (0x1E5, 0x12F4, PRACTICE_RANGE, True),
            (0x170, 0x05AC, PRACTICE_RANGE, True),  # Acknowledge without a selected UI marker
            (0x16C, 0x05AA, PRACTICE_RANGE, True),  # Need Healing from Soldier
            (0x16C, 0x0689, PRACTICE_RANGE, True),  # Need Healing from Mercy
            (0x1E5, 0x12F4, PRACTICE_RANGE, False),
            (0x1E5, 0x12F4, KANEZAKA_DM, True),
        )
        for action, target_message, game_map, same_team in cases:
            with self.subTest(action=hex(action), mode=game_map.mode_guid, same_team=same_team):
                directed = same_team and not game_map.free_for_all
                run = Run(game_map, names=("Speaker", "Target", "Other"))
                speaker, target, other = run.players
                if target_message == 0x0689:
                    mercy = next(hero for hero in heroes().values() if hero.name == "Mercy")
                    run.match.switch_hero(speaker, mercy)
                target.team = speaker.team if same_team else 1 - speaker.team
                other.team = speaker.team
                speaker.mover.position = (0.0, 0.0, 0.0)
                speaker.mover.yaw = speaker.mover.pitch = 0
                target.mover.position = (0.0, 0.0, 10.0)
                other.mover.position = (10.0, 0.0, 0.0)
                connection = game_client()
                connection.player = speaker
                speaker.client = connection

                record = b"\x01\x08" + struct.pack("<QBI", 0x0A90000000000000 | action, True, 0)
                packet = BitWriter()
                messages.write_channel(packet, [(0, record)])
                packet.bit(0)
                connection._payload_in(packet.getvalue(), 17.0)
                run.match.deliver_body_messages()
                queued = run.match._body_messages
                if directed:
                    [(source, recipient, _, message, _)] = queued
                    self.assertIs(source, speaker.body_script)
                    self.assertIs(recipient, target)
                    self.assertEqual(message, 0x0240000000000000 | target_message)
                else:
                    self.assertEqual(queued, [])
                    self.assertTrue(any(i.graph.index == 0x0686
                                        for i in speaker.body_script.component.instances.values()))
                run.match.deliver_body_messages()
                for player in (target, other):
                    wheel = next(i for i in player.body_script.component.instances.values()
                                 if i.graph.index == 0x05AD)
                    caller = expr.Entity(speaker.body) if directed and player is target else None
                    self.assertEqual(wheel.vars[40].value() if 40 in wheel.vars else None, caller)
                    self.assertEqual(wheel.vars[41].value() if 41 in wheel.vars else None, caller)
                if action == 0x16C:
                    nodes = (94, 14) if target_message == 0x0689 else (166,)
                    self.assertEqual(tuple(event.node for event in target.body_script.world.wheel_callouts), nodes)
                    self.assertEqual(other.body_script.world.wheel_callouts, [])

    def test_selection_message_takes_the_authored_category_branches(self):
        run_node = runtime.Instance.run_node
        visited = set()

        def record(instance, node, caller):
            visited.add((instance.graph.index, node.pos))
            return run_node(instance, node, caller)

        cases = (
            (0x16B, (0x05AD, 140)),  # fixed communication
            (0x1E5, (0x05AD, 140)),  # configurable communication
            (0x172, (0x05AD, 107)),  # default emote is also in the main wheel
            (0x19F, (0x05AD, 107)),  # alternate emote
            (0x173, (0x02CF, 33)),   # default spray slot
            (0x1A5, (0x02CF, 33)),   # alternate spray slot
        )
        branches = {branch for _, branch in cases}
        with patch.object(runtime.Instance, "run_node", record):
            for choice, expected in cases:
                with self.subTest(choice=hex(choice)):
                    body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
                    visited.clear()
                    runtime.send_message(
                        body.component, 0x02400000000002A3,
                        {0x8FC: expr.Asset(0x0A90000000000000 | choice), 0x3E1: True}, body.entity,
                    )
                    body.component.run_now()
                    self.assertEqual(visited & branches, {expected})

    def test_category_query_requires_the_full_action_guid(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        main = next(instance for instance in body.component.instances.values() if instance.graph.index == 0x05AD)
        condition = main.graph.node(182).field("m_condition")
        for value, expected in (
            (expr.Asset(0x0A90000000000170), True),
            (expr.Asset(0x0A90000100000170), False),
            (expr.Asset(0x02E0000000000170), False),
            (0x170, False),
        ):
            with self.subTest(value=value):
                main.write((expr.INSTANCE, 29699), value)
                self.assertIs(main.evaluate(condition), expected)

    def test_emote_and_spray_actions_use_native_slot_order(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        main, spray = instances[0x05AD], instances[0x02CF]
        emote_slot = main.graph.node(107).field("m_condition")["m_configVars"][0]["m_slot"]
        spray_slot = spray.graph.node(33).field("m_value")
        for instance, config, actions, unmatched in (
            (main, emote_slot, (0x172, 0x1A1, 0x1A0, 0x19F), 0x1A2),
            (spray, spray_slot, (0x173, 0x1A5, 0x1A4, 0x1A3), 0x1A6),
        ):
            for slot, action in enumerate(actions):
                with self.subTest(action=hex(action)):
                    instance.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | action))
                    self.assertEqual(instance.evaluate(config), slot)
            instance.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | unmatched))
            self.assertEqual(instance.evaluate(config), -1)

        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001A4)}, body.entity,
        )
        body.component.run_now()
        self.assertEqual(spray.vars[9259].value(), 2)

    def test_equipped_animation_selection_replicates_authored_emote_graphs(self):
        # Soldier's Heroic and Take a Knee unlocks from 104319.
        heroic, kneel = "0x0250000000000904", "0x0250000000000BE2"
        default_alias, chosen_alias = 0x0C8000000000045C, 0x0C80000000000476
        default_blend, chosen_blend = 0x00400000000006AD, 0x00400000000006B2
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / EMOTE_ALIAS_DATA).write_text(
                json.dumps({heroic: hex(default_alias), kneel: hex(chosen_alias)}), encoding="utf-8",
            )
            (data / EMOTE_BLEND_TREE_DATA).write_text(
                json.dumps({heroic: hex(default_blend), kneel: hex(chosen_blend)}), encoding="utf-8",
            )
            items = ItemDB(data)
        collection = Collection(RetailTemplates(), items)
        profile = Profile()
        self.assertEqual(collection.emote_aliases(profile, SOLDIER), (default_alias,))
        profile.loadouts[f"0x{SOLDIER:016X}"] = {"+0x88": [heroic, kneel, "0x0"]}
        match = Match(
            PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)),
            emote_aliases_of=lambda account, hero: collection.emote_aliases(profile, hero) if account == 1 else (),
            emote_blend_trees_of=lambda account, hero: collection.emote_blend_trees(profile, hero) if account == 1 else (),
        )
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        body = player.body_script
        client = decode.ClientBody.for_hero(SOLDIER)
        create.stream.arrived(0)

        def replicate():
            for update in body.frames():
                client.apply(update)
                body.stream.arrived(update.chunk_last)

        replicate()
        self.assertEqual(body.world.emote_aliases, (default_alias, chosen_alias, 0))
        self.assertEqual(body.world.emote_blend_trees, (default_blend, chosen_blend, 0))
        wheel = next(instance for instance in body.component.instances.values() if instance.graph.index == 0x05AD)
        query = wheel.graph.node(107).field("m_condition")["m_configVars"][0]
        for choice, expected in ((0x172, default_alias), (0x1A1, chosen_alias), (0x1A0, 0), (0x19F, 0), (0x1A2, 0)):
            with self.subTest(choice=hex(choice)):
                wheel.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | choice))
                self.assertEqual(wheel.evaluate(query), expr.Asset(expected))

        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001A1), 0x3E1: True}, body.entity,
        )
        body.command(body.frame + 1, 0)
        replicate()

        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        emote, animation, helper = (instances[index] for index in (0x069A, 0x023A, 0x1040))
        self.assertIs(animation.parent, emote)
        self.assertEqual(animation.vars[2254].value(), expr.Asset(chosen_alias))
        self.assertEqual(animation.vars[8908].value(), expr.Asset(chosen_blend))
        self.assertEqual(client.vars[animation.id, 8908], expr.Asset(chosen_blend))
        self.assertEqual(client.graphs[emote.id], 0x069A)
        self.assertEqual(client.records[animation.id], (0x023A, (emote.id, animation.parent_state)))
        self.assertEqual(client.graphs[helper.id], 0x1040)

    def test_emote_completion_releases_the_authored_stack_and_allows_another_selection(self):
        aliases = (0x0C8000000000045C, 0x0C80000000000476)
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True, emote_aliases=aliases)
        create = EntityUpdate(body.entity, OP_CREATE)
        body.created(create)
        create.stream.arrived(0)
        client = decode.ClientBody.for_hero(SOLDIER)

        def replicate():
            for update in body.frames():
                client.apply(update)
                body.stream.arrived(update.chunk_last)

        def select(action):
            runtime.send_message(
                body.component, 0x02400000000002A3,
                {0x8FC: expr.Asset(0x0A90000000000000 | action), 0x3E1: True}, body.entity,
            )
            body.command(body.frame + 1, 0)
            replicate()
            return {i.graph.index: i for i in body.component.instances.values()}

        replicate()
        current = select(0x172)
        emote, animation, helper = (current[index] for index in (0x069A, 0x023A, 0x1040))
        body.command(body.frame + 32, 0)
        self.assertIs(select(0x1A1)[0x023A], animation)
        anim_state = animation.node_state(animation.graph.node(0))
        self.assertTrue(anim_state.active)
        anim_state.request_finish()
        body.command(body.frame + 1, 0)
        replicate()

        for instance in (emote, animation, helper):
            self.assertNotIn(instance.id, body.component.instances)
            self.assertNotIn(instance.id, client.graphs)
        # 05AD's authored selection cooldown still applies after cleanup.
        body.command(body.frame + 32, 0)
        current = select(0x1A1)
        self.assertIsNot(current[0x023A], animation)
        self.assertEqual(current[0x023A].vars[2254].value(), expr.Asset(aliases[1]))

        # Model a competing body state outranking 069A on the existing stack.
        animation = current[0x023A]
        competing = animation.node_state(animation.graph.node(65))
        self.assertTrue(competing.active)
        competing.link((expr.ENTITY, 33), priority=1_000, stack=True)
        body.command(body.frame + 1, 0)
        replicate()
        for index in (0x069A, 0x023A, 0x1040):
            self.assertNotIn(current[index].id, body.component.instances)
            self.assertNotIn(current[index].id, client.graphs)

    def test_heroic_finishes_with_equipped_or_pilot_body_tree(self):
        alias = 0x0C8000000000045C
        for hero, blend, model_blend in (
            (SOLDIER, 0x00400000000006AD, 0x00400000000006AD),
            (0x02E000000000007A, 0, 0x004000000000048A),
        ):
            with self.subTest(hero=hero):
                with TemporaryDirectory() as directory:
                    data = Path(directory)
                    (data / EMOTE_DURATION_DATA).write_text(json.dumps({
                        hex(model_blend): {hex(alias): {hex(EMOTE_ANY_CATEGORY): 5.0000005}},
                    }), encoding="utf-8")
                    with patch("ow174.game.script.driver.DATA_DIR", data):
                        body = BodyScript(hero, 0xA0000101, 1000, communication_wheel=True,
                                          emote_aliases=(alias,), emote_blend_trees=(blend,))
                        runtime.send_message(
                            body.component, 0x02400000000002A3,
                            {0x8FC: expr.Asset(0x0A90000000000172), 0x3E1: True}, body.entity,
                        )
                        body.command(1001, 0, grounded=True)
                        animation = next(i for i in body.component.instances.values() if i.graph.index == 0x023A)
                        state = animation.node_state(animation.graph.node(0))
                        for frame in range(1002, 1120):
                            if state.active:
                                break
                            body.command(frame, 0, grounded=True)
                        self.assertTrue(state.active)
                        self.assertEqual(animation.vars[8908].value(), expr.Asset(blend))
                        self.assertEqual([event.time - state.start for event in animation.queue if event.state is state],
                                         [5000, 5000])
                        self.assertEqual(body.world.animation_duration(animation, animation.graph.node(19)),
                                         expr.f32(5.0000005))
                        end_frame = (state.start + 5000) // body.frame_ms
                        body.command(end_frame - 1, 0, grounded=True)
                        self.assertTrue(state.active)
                        body.command(end_frame, 0, grounded=True)
                        self.assertTrue(animation.stopped)
                        self.assertFalse({0x069A, 0x1040} & {i.graph.index for i in body.component.instances.values()})

    def test_tagged_dva_unlock_uses_body_tags_and_pilot_fallback(self):
        dva, mech, pilot = 0x02E000000000007A, 0x0400000000000407, 0x040000000000040A
        unlock, alias, blend = 0x0250000000000BFC, 0x0C80000000000476, 0x0040000000000690
        fallback = 0x0C8000000000045C
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / EMOTE_TAGGED_DATA).write_text(json.dumps({
                "unlocks": {hex(unlock): {"alias": hex(alias), "blend": hex(blend), "tags": ["0x0D60000000000069"]}},
                "bodies": {hex(mech): ["0x0D60000000000069"], hex(pilot): ["0x0D60000000000121"]},
            }), encoding="utf-8")
            collection = Collection(RetailTemplates(), ItemDB(data))
        profile = Profile()
        profile.loadouts[f"0x{dva:016X}"] = {"+0x88": [hex(unlock), "0x0"]}
        self.assertEqual(heroes()[dva].body, pilot)
        self.assertEqual(collection.emote_aliases(profile, dva), (fallback, 0))
        self.assertEqual(collection.emote_blend_trees(profile, dva), (0, 0))
        mech_hero = replace(heroes()[dva], body=mech)
        self.assertEqual(collection.emote_aliases(profile, dva, body_definition=mech), (alias, 0))
        self.assertEqual(collection.emote_blend_trees(profile, dva, body_definition=mech), (blend, 0))

        match = Match(
            PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)),
            emote_aliases_of=lambda _, hero, **form: collection.emote_aliases(profile, hero, **form),
            emote_blend_trees_of=lambda _, hero, **form: collection.emote_blend_trees(profile, hero, **form),
        )
        player = match.add_player(1, "D.Va", dva, 0, False)
        match.own_body_updates(player)
        body = player.body_script
        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A90000000000172), 0x3E1: True}, body.entity,
        )
        body.command(body.frame + 1, 0)
        animation = next(i for i in body.component.instances.values() if i.graph.index == 0x023A)
        self.assertEqual(animation.vars[2254].value(), expr.Asset(fallback))
        self.assertEqual(animation.vars[8908].value(), expr.Asset(0))

        # The base runtime has no 0407 graph bundle. Use a form record with
        # the common wheel roots to isolate Match's body and loadout selection.
        form_record = dict(graph.bodies()[pilot], hero=None)
        in_world(match, player)
        with patch.object(graph, "entity_body", side_effect=lambda body: form_record if body == mech else None):
            self.assertTrue(match.switch_hero(player, mech_hero))
        mech_script = player.body_script
        self.assertIs(mech_script.record, form_record)
        self.assertEqual(mech_script.world.body_definition, mech)
        match.communication_action(player, {"+0x78": 0x0A90000000000172, "+0x80": True})
        match.deliver_body_messages()
        mech_script.command(mech_script.frame + 1, 0, grounded=True)
        animation = next(i for i in mech_script.component.instances.values() if i.graph.index == 0x023A)
        self.assertEqual(animation.vars[2254].value(), expr.Asset(alias))
        self.assertEqual(animation.vars[8908].value(), expr.Asset(blend))
        self.assertTrue(match.switch_hero(player, heroes()[dva]))
        self.assertTrue(mech_script.retired)
        self.assertEqual(player.body_script.world.body_definition, pilot)
        self.assertEqual(player.body_script.world.emote_aliases, (fallback, 0))

    def test_emote_scalar_binds_authored_slot_and_is_removed_on_end(self):
        alias, blend = 0x0C80000000000477, 0x0040000000000649
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "emote_scalar_tracks_174.json").write_text(json.dumps({
                hex(blend): {hex(alias): {
                    hex(EMOTE_ANY_CATEGORY): {"times": [0], "values": [0.03487373]},
                    "0x028000000000006E": {"times": [0, 1], "values": [0, 1]},
                    "0x0280000000000082": {"times": [], "values": []},
                }},
            }), encoding="utf-8")
            with patch("ow174.game.script.driver.DATA_DIR", data):
                body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
                component = body.component
                body.command(body.frame + 1, 0, grounded=True)
                parent = component.create(graph.graph(0x069A), init=False)
                animation = component.create(graph.graph(0x023A), parent=parent, init=False)
                animation.write((expr.INSTANCE, 2254), expr.Asset(alias))
                animation.write((expr.INSTANCE, 8908), expr.Asset(blend))
                animation.write((expr.INSTANCE, 681), False)
                variable = animation.vars[681]

                intro = animation.node_state(animation.graph.node(19))
                intro.begin()
                self.assertIs(variable.value(), False)
                intro.end(False)

                exit_anim = animation.node_state(animation.graph.node(52))
                exit_anim.begin()
                self.assertIs(type(variable.value()), float)
                self.assertEqual(variable.value(), 0.0)
                exit_anim.end(False)

                loop = animation.node_state(animation.graph.node(18))
                loop.begin()
                self.assertEqual(variable.value(), expr.f32(0.03487373))
                self.assertEqual([(link.state, link.slot) for link in variable.links], [(loop, 2)])
                guard = animation.node_state(animation.graph.node(77))
                self.assertTrue(guard.evaluate(guard.node.field("m_condition")))
                export_body_vm(body)
                loop.end(False)
                self.assertEqual(variable.links, [])
                # The authored onEnd plug writes its own zero vector after unlinking.
                self.assertEqual(variable.value(), expr.Vec3(0.0, 0.0, 0.0))

    def test_equipped_kneel_timing_and_cancel_follow_body_commands(self):
        alias, blend = 0x0C80000000000476, 0x00400000000006B2
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "emote_durations_174.json").write_text(json.dumps({
                hex(blend): {hex(alias): {
                    "0x028000000000006E": 1.0666667,
                    "0x0280000000000082": 0.36666667,
                    "0x0": 8.933333,
                }},
            }), encoding="utf-8")
            with patch("ow174.game.script.driver.DATA_DIR", data):
                match = Match(
                    PRACTICE_RANGE,
                    collision_provider=StaticCollisionProvider(flat_world(PRACTICE_RANGE.spawns[0].position[1])),
                    emote_aliases_of=lambda *_: (alias,), emote_blend_trees_of=lambda *_: (blend,),
                )
                player = match.add_player(1, "Soldier", SOLDIER, 0, False)
                in_world(match, player)
                match.tick = 1000
                match.switch_hero(player, player.hero)
                body = player.body_script
                player.take_commands([Command(body.frame + 1)])
                runtime.send_message(
                    body.component, 0x02400000000002A3,
                    {0x8FC: expr.Asset(0x0A90000000000172), 0x3E1: True}, body.entity,
                )
                player.take_commands([Command(body.frame + 1)])
                animation = next(i for i in body.component.instances.values() if i.graph.index == 0x023A)
                intro = animation.node_state(animation.graph.node(19))
                for frame in range(body.frame + 1, body.frame + 120):
                    player.take_commands([Command(frame)])
                    if intro.active:
                        break
                self.assertTrue(intro.active)
                self.assertEqual([event.time - intro.start for event in animation.queue if event.state is intro],
                                 [1067, 1067])
                # Stay idle until 023A:50 has chased the exit gate to zero.
                while body.component.now < intro.start + 2400:
                    player.take_commands([Command(body.frame + 1)])
                self.assertFalse(intro.active)
                loop = animation.node_state(animation.graph.node(18))
                self.assertTrue(loop.active)
                self.assertTrue(body.component.entity_var(545).value())
                self.assertFalse(body.component.entity_var(2070).value())
                cancel = animation.node_state(animation.graph.node(38))
                self.assertTrue(cancel.active)
                self.assertFalse(expr.truthy(animation.find_var(expr.INSTANCE, 488).value()))

                # 0015:58 is the authored disable state for primary fire.
                blocker = body.component.create(graph.graph(0x0015), init=False)
                blocker.initialized = True
                disable_fire = blocker.node_state(blocker.graph.node(58))
                disable_fire.begin()
                player.take_commands([Command(body.frame + 1, buttons=FIRE)])
                self.assertIn(20, body.component.held)
                self.assertFalse(animation.find_var(expr.INSTANCE, 488).value())
                self.assertTrue(cancel.active)
                self.assertTrue(loop.active)
                player.take_commands([Command(body.frame + 1)])
                disable_fire.end(False)

                # Enabled fire cancels via held button 20 while still grounded.
                player.take_commands([Command(body.frame + 1, buttons=FIRE)])
                self.assertTrue(animation.find_var(expr.INSTANCE, 488).value())
                self.assertFalse(cancel.active)
                self.assertFalse(loop.active)
                exit_anim = animation.node_state(animation.graph.node(52))
                self.assertTrue(exit_anim.active)
                self.assertEqual([event.time - exit_anim.start for event in animation.queue
                                  if event.state is exit_anim], [367, 367])
                while body.component.now < exit_anim.start + 1000:
                    player.take_commands([Command(body.frame + 1)])
                self.assertTrue(animation.stopped)
                self.assertNotIn(0x069A, {i.graph.index for i in body.component.instances.values()})
                self.assertNotIn(0x1040, {i.graph.index for i in body.component.instances.values()})

    def test_authored_emote_replaces_horizontal_velocity_until_exit(self):
        alias, blend = 0x0C80000000000477, 0x0040000000000649
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "emote_scalar_tracks_174.json").write_text(json.dumps({
                hex(blend): {hex(alias): {
                    hex(EMOTE_ANY_CATEGORY): {"times": [0], "values": [0.03487373]},
                    "0x0280000000000082": {"times": [], "values": []},
                }},
            }), encoding="utf-8")
            (data / EMOTE_DURATION_DATA).write_text(json.dumps({
                hex(blend): {hex(alias): {hex(EMOTE_ANY_CATEGORY): 0.5}},
            }), encoding="utf-8")
            with patch("ow174.game.script.driver.DATA_DIR", data):
                match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(
                    flat_world(PRACTICE_RANGE.spawns[0].position[1]),
                ), emote_aliases_of=lambda *_: (alias,), emote_blend_trees_of=lambda *_: (blend,))
                player = match.add_player(1, "Soldier", SOLDIER, 0, False)
                in_world(match, player)
                match.tick = 1000
                match.switch_hero(player, player.hero)
                body = player.body_script
                player.take_commands([Command(body.frame + 1)])
                runtime.send_message(body.component, 0x02400000000002A3,
                                     {0x8FC: expr.Asset(0x0A90000000000172), 0x3E1: True}, body.entity)
                player.take_commands([Command(body.frame + 1)])
                animation = next(i for i in body.component.instances.values() if i.graph.index == 0x023A)
                loop = animation.node_state(animation.graph.node(18))
                for _ in range(120):
                    if loop.active:
                        break
                    player.take_commands([Command(body.frame + 1)])
                self.assertTrue(loop.active)
                while body.component.now < loop.start + 2400:
                    player.take_commands([Command(body.frame + 1)])
                self.assertTrue(animation.node_state(animation.graph.node(78)).active)
                self.assertFalse(animation.node_state(animation.graph.node(110)).active)

                x, _, z = player.mover.position
                player.mover.velocity = (3.0, 0.0, 2.0)
                player.take_commands([Command(body.frame + 1)])
                self.assertEqual((player.mover.position[0], player.mover.position[2]), (x, z))
                player.take_commands([Command(body.frame + 1, buttons=FIRE)])
                self.assertFalse(loop.active)
                self.assertFalse(body.world.wheel_replaces_horizontal_velocity(body.component))

                player.mover.velocity = (3.0, 0.0, 2.0)
                player.take_commands([Command(body.frame + 1)])
                self.assertGreater(player.mover.position[0], x)
                self.assertGreater(player.mover.position[2], z)

    def test_emote_cancel_detects_a_fall_without_buttons_or_axes(self):
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(
            flat_world(PRACTICE_RANGE.spawns[0].position[1]),
        ))
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        in_world(match, player)
        match.switch_hero(player, player.hero)
        body = player.body_script
        player.take_commands([Command(body.frame + 1)])
        component = body.component
        animation = component.create(graph.graph(0x023A), init=False)
        animation.initialized = True
        animation.write((expr.INSTANCE, 488), False)
        cancel = animation.node_state(animation.graph.node(38))
        cancel.begin()
        self.assertTrue(component.entity_var(545).value())
        self.assertTrue(cancel.active)

        # Lift the feet clear of the floor while sending an idle command.
        player.mover.position[1] += 2.0
        player.take_commands([Command(body.frame + 1)])
        self.assertTrue(player.mover.airborne)
        self.assertFalse(component.entity_var(545).value())
        self.assertFalse(component.entity_var(2070).value())
        self.assertEqual(component.held, [])
        self.assertTrue(animation.vars[488].value())
        self.assertFalse(cancel.active)

    def test_emote_cancel_reads_axes_without_a_grounded_sample(self):
        for forward, right in ((127, 0), (0, -127)):
            with self.subTest(forward=forward, right=right):
                body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
                component = body.component
                body.command(body.frame + 1, 0, grounded=True)
                animation = component.create(graph.graph(0x023A), init=False)
                animation.initialized = True
                animation.write((expr.INSTANCE, 488), False)
                cancel = animation.node_state(animation.graph.node(38))
                cancel.begin()
                body.command(body.frame + 1, 0)
                self.assertTrue(cancel.active)
                self.assertFalse(component.entity_var(2070).value())

                body.command(body.frame + 1, 0, forward=forward, right=right)
                self.assertTrue(component.entity_var(545).value())
                self.assertTrue(component.entity_var(2070).value())
                self.assertTrue(animation.vars[488].value())
                self.assertFalse(cancel.active)

    def test_emote_intro_with_no_alias_takes_its_authored_loop_transition(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        component = body.component
        parent = component.create(graph.graph(0x069A), init=False)
        animation = component.create(graph.graph(0x023A), parent=parent, init=False)
        animation.initialized = True
        animation.write((expr.INSTANCE, 2254), expr.Asset(0))
        intro = animation.node_state(animation.graph.node(19))
        loop = animation.node_state(animation.graph.node(18))

        # Supply a duration to isolate the authored loop flag from the
        # missing-duration case. The intro still has no duration or alias.
        duration = {"$": "STUConfigVarFloat", "m_value": 1.0}
        with patch.dict(loop.node.fields, {"m_86701C3E": duration}), patch.object(loop, "timer_at") as timer:
            intro.begin()
            self.assertTrue(intro.active)
            animation.update()
            self.assertFalse(intro.active)
            self.assertEqual(loop.counter, 1)
            timer.assert_not_called()

    def test_voice_lookup_uses_the_authored_equipped_slot_config(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        voice = body.component.create(graph.graph(0x0686), init=False)
        config = voice.graph.node(67).field("m_value")["m_slot"]
        for action, expected in (
            (expr.Asset(0x0A9000000000016F), 0),
            (expr.Asset(0x0A9000000000019D), 1),
            (expr.Asset(0x0A9000000000019C), 2),
            (expr.Asset(0x0A9000000000019B), 3),
            (expr.Asset(0x0A9000000000019E), -1),
            (expr.Asset(0x0A9000010000016F), -1),
            (expr.Asset(0x02E000000000016F), -1),
            (0x16F, -1),
        ):
            with self.subTest(action=action):
                voice.write((expr.INSTANCE, 29699), action)
                self.assertEqual(voice.evaluate(config), expected)

    def test_spray_unlock_and_voice_effect_use_their_own_equipped_slots(self):
        default_spray, chosen_spray = 0x025000000000008D, 0x02500000000007F1
        default_voice, chosen_voice = 0x02500000000008D7, 0x0250000000000A31
        default_effect, chosen_effect = 0x03000000000018C4, 0x0300000000001879
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / VOICE_EFFECT_DATA).write_text(
                json.dumps({hex(default_voice): hex(default_effect), hex(chosen_voice): hex(chosen_effect)}),
                encoding="utf-8",
            )
            items = ItemDB(data)
        collection = Collection(RetailTemplates(), items)
        profile = Profile()
        self.assertEqual(collection.wheel_cosmetics(profile, SOLDIER), ((default_spray,), (default_effect,)))
        profile.loadouts[f"0x{SOLDIER:016X}"] = {
            "+0x58": [hex(default_spray), hex(chosen_spray), "0x0"],
            "+0x70": [hex(default_voice), hex(chosen_voice), "0x0"],
        }
        match = Match(
            PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)),
            wheel_cosmetics_of=lambda _, hero: collection.wheel_cosmetics(profile, hero),
        )
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        create.stream.arrived(0)
        body = player.body_script
        for action in (0x1A5, 0x19D):
            runtime.send_message(
                body.component, 0x02400000000002A3,
                {0x8FC: expr.Asset(0x0A90000000000000 | action), 0x3E1: True}, body.entity,
            )
            body.command(body.frame + 1, 0)
        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        spray, voice = instances[0x02CF], instances[0x0686]
        self.assertEqual(spray.vars[9259].value(), 1)
        spray_query = spray.graph.node(2).field("m_E441DC25")
        voice_query = voice.graph.node(67).field("m_value")
        self.assertEqual(spray.evaluate(spray_query), expr.Asset(chosen_spray))
        self.assertEqual(voice.evaluate(voice_query), expr.Asset(chosen_effect))
        self.assertFalse(spray.state(1).active)
        self.assertNotIn((spray.id, 1), body.snapshot().states)
        self.assertNotIn((spray.id, 1), observers.snapshot(body).states)

        for slot, expected in ((0, default_spray), (2, 0), (3, 0), (-1, 0)):
            spray.write((expr.INSTANCE, 9259), slot)
            self.assertEqual(spray.evaluate(spray_query), expr.Asset(expected))
        for action, expected in ((0x16F, default_effect), (0x19C, 0), (0x19B, 0), (0x19E, 0)):
            voice.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | action))
            self.assertEqual(voice.evaluate(voice_query), expr.Asset(expected))

    def test_spray_selection_places_on_the_nearest_eligible_static_surface(self):
        def wall(z):
            return (-3, 0, z, 3, 0, z, -3, 4, z), (3, 0, z, 3, 4, z, -3, 4, z)

        pixel, alternate = 0x02500000000007F1, 0x0250000000000004
        for distance, unlocks, yaw, expected in (
            (3.0, (pixel, alternate), 0, True),
            (4.51, (pixel,), 0, False),
            (3.0, (pixel,), 32768, False),
            (3.0, (), 0, False),
        ):
            with self.subTest(distance=distance, unlocks=unlocks, yaw=yaw):
                world = World(wall(1.0), masks=(0x18, 0x18))
                world.ray_geometry = RayWorld(wall(distance), masks=(0x40, 0x40))
                match = Match(
                    PRACTICE_RANGE, collision_provider=StaticCollisionProvider(world),
                    wheel_cosmetics_of=lambda *_: (unlocks, ()),
                )
                self.assertTrue(match._refresh_collision())
                player = match.add_player(1, "Soldier", SOLDIER, 0, False)
                player.has_body = True
                [create] = match.own_body_updates(player)
                body = player.body_script
                player.mover.position = (0.0, 0.0, 0.0)
                player.mover.yaw = yaw
                player.mover.pitch = 0
                create.stream.arrived(0)
                owner = decode.ClientBody.for_hero(SOLDIER)
                [tree] = body.frames()
                owner.apply(tree)
                body.stream.arrived(tree.chunk_last)
                runtime.send_message(
                    body.component, 0x02400000000002A3,
                    {0x8FC: expr.Asset(0x0A90000000000173), 0x3E1: True}, body.entity,
                )
                body.command(body.frame + 1, 0)
                spray = next(i for i in body.component.instances.values() if i.graph.index == 0x02CF)
                state = spray.state(1)
                self.assertEqual(state.active, expected)
                owner_updates = body.frames()
                owner_frames = [owner.apply(update) for update in owner_updates]
                self.assertEqual(
                    [action[1:] for frame in owner_frames for action in frame.actions],
                    [(spray.id, 8 if expected else 3)],
                )
                watcher = decode.ClientBody.for_hero(SOLDIER)
                stream = BodyStream(body.entity)
                baseline = observers.snapshot(body)
                tree = stream.full(observers.full_frame(body, baseline), baseline)
                watched = watcher.apply(tree).instances[spray.id]
                stream.arrived(tree.chunk_last)
                self.assertEqual(owner.states.get((spray.id, 1), False), expected)
                self.assertEqual(watched.states.get(1, (False, {}))[0], expected)
                if expected:
                    self.assertEqual(state.payload(), {
                        "direction": (0.0, 0.0, 1.0),
                        "point": (0.0, combat.eye_height(player.hero.body), distance),
                        "normal": (0.0, 0.0, -1.0),
                        "unlock": pixel,
                    })
                    result = (True, spray_placement.wire_fields(state.payload()))
                    self.assertEqual(watched.states[1], result)
                    self.assertIn(result, [
                        frame.instances[spray.id].states.get(1) for frame in owner_frames if spray.id in frame.instances
                    ])
                    self.assertTrue(spray.state(14).active)
                    self.assertEqual(spray.state(14).pulses, 1)
                    for update in owner_updates:
                        body.stream.arrived(update.chunk_last)
                    # A new look at the identical point must make both clients read placement again.
                    body.command(body.frame + 300, 0)
                    runtime.send_message(
                        body.component, 0x02400000000002A3,
                        {0x8FC: expr.Asset(0x0A900000000001A5), 0x3E1: True}, body.entity,
                    )
                    body.command(body.frame + 1, 0)
                    self.assertEqual(state.payload()["unlock"], alternate)
                    self.assertEqual(spray_placement.wire_fields(state.payload()), result[1])
                    owner_frames = [owner.apply(update) for update in body.frames()]
                    self.assertIn(result, [
                        frame.instances[spray.id].states.get(1) for frame in owner_frames if spray.id in frame.instances
                    ])
                    snap = observers.snapshot(body)
                    delta, keys = observers.delta(body, stream, snap)
                    observed = watcher.apply(stream.delta(delta, snap, keys, 0))
                    self.assertEqual(observed.instances[spray.id].states[1], result)
                    self.assertEqual([action[1:] for action in observed.actions], [(spray.id, 8)])
                    self.assertEqual(spray.state(14).pulses, 2)
                    # A failed replacement cannot carry the new slot with the old geometry.
                    player.mover.yaw = 32768
                    body.command(body.frame + 300, 0)
                    runtime.send_message(
                        body.component, 0x02400000000002A3,
                        {0x8FC: expr.Asset(0x0A90000000000173), 0x3E1: True}, body.entity,
                    )
                    body.command(body.frame + 1, 0)
                    self.assertFalse(state.active)
                    self.assertEqual(state.payload(), {})
                    self.assertEqual(spray.state(14).pulses, 2)
                else:
                    self.assertEqual(state.payload(), {})
                    self.assertFalse(spray.state(14).active)

    def test_attached_spray_uses_anchor_space_in_owner_and_observer_frames(self):
        class AttachedSurface(World):
            def __init__(self, key):
                super().__init__(())
                self.key = key

            def cast_ray(self, origin, direction, length, category):
                self.request = (origin, direction, length, category)
                # A half turn about Y translates the hit by two metres and
                # reverses its world Z normal in the attachment's space.
                anchor = RayAttachment(0x8000022E, (2.0, origin[1], 3.0), (0.0, 1.0, 0.0, 0.0), self.key)
                return RayHit(3.0, (0.0, origin[1], 3.0), (0.0, 0.0, -1.0), category, 0, anchor)

        for key in (0, 0x137):
            with self.subTest(key=key):
                surface = AttachedSurface(key)
                match = Match(
                    PRACTICE_RANGE, collision_provider=StaticCollisionProvider(surface),
                    wheel_cosmetics_of=lambda *_: ((0x02500000000007F1,), ()),
                )
                self.assertTrue(match._refresh_collision())
                player = match.add_player(1, "Soldier", SOLDIER, 0, False)
                player.has_body = True
                [create] = match.own_body_updates(player)
                body = player.body_script
                player.mover.position = (0.0, 0.0, 0.0)
                player.mover.yaw = player.mover.pitch = 0
                create.stream.arrived(0)
                owner = decode.ClientBody.for_hero(SOLDIER)
                [tree] = body.frames()
                owner.apply(tree)
                body.stream.arrived(tree.chunk_last)

                runtime.send_message(
                    body.component, 0x02400000000002A3,
                    {0x8FC: expr.Asset(0x0A90000000000173), 0x3E1: True}, body.entity,
                )
                body.command(body.frame + 1, 0)
                spray = next(i for i in body.component.instances.values() if i.graph.index == 0x02CF)
                self.assertEqual(surface.request, (
                    (0.0, combat.eye_height(player.hero.body), 0.0), (0.0, 0.0, 1.0), 4.5, 0x40,
                ))
                payload = spray.state(1).payload()
                self.assertEqual(payload, {
                    "direction": (0.0, 0.0, 1.0), "point": (2.0, 0.0, 0.0), "normal": (0.0, 0.0, 1.0),
                    "entity": 0x8000022E, "extra": key, "unlock": 0x02500000000007F1,
                })
                wire = spray_placement.wire_fields(payload)
                self.assertEqual(wire["entity"], 0x8000022E)
                self.assertEqual(wire["has_extra"], bool(key))
                self.assertEqual(wire.get("extra", 0), key)
                expected = (True, wire)
                owner_frames = [owner.apply(update) for update in body.frames()]
                remote = statescript.chunk(observers.full_frame(body, observers.snapshot(body)), 0, 1)
                watched = decode.read_chunk(remote.getvalue(), remote.count).instances[spray.id]
                self.assertEqual(watched.states[1], expected)
                self.assertIn(expected, [
                    frame.instances[spray.id].states.get(1) for frame in owner_frames if spray.id in frame.instances
                ])

    def test_client_unlock_use_report_reaches_only_the_current_wheel_instance(self):
        run = Run(names=("Speaker",))
        player, = run.players
        match = run.match
        script = player.body_script
        connection = game_client()
        connection.player = player
        player.client = connection
        pixel, effect = 0x02500000000007F1, 0x0300000000001879
        script.world.spray_unlocks = (pixel,)
        script.world.voice_effects = (effect,)
        player.mover.position = (0.0, 0.0, 0.0)
        player.mover.yaw = player.mover.pitch = 0
        match.collision_world = World(((-3, 0, 3, 3, 0, 3, -3, 4, 3),), masks=(0x40,))

        def select(action):
            match.communication_action(player, {"+0x78": 0x0A90000000000000 | action, "+0x80": True})
            match.deliver_body_messages()
            script.command(script.frame + 1, 0)

        def report(sequence, target, instance, asset, tag=4):
            blob = bytes.fromhex("00 00 00 00 01 C3 3B 05 40 0A") + bytes((tag,))
            blob += struct.pack("<QII", asset, 0, 0)
            record = messages.encode(21616, {
                "+0x78": {"+0x0": target}, "+0x80": blob,
                "+0xA8": 0x0240000000001313, "+0xB0": instance.id,
            })
            packet = BitWriter()
            messages.write_channel(packet, [(sequence, record)])
            packet.bit(0)
            connection._payload_in(packet.getvalue(), 17.0)
            script.command(script.frame + 1, 0)

        select(0x173)
        select(0x16F)
        instances = {instance.graph.index: instance for instance in script.component.instances.values()}
        spray, voice = instances[0x02CF], instances[0x0686]
        for sequence, target, instance, asset, tag in (
            (0, player.entity, spray, pixel, 4),
            (1, player.body, voice, pixel, 4),
            (2, player.body, spray, pixel, 2),
            (3, player.body, instances[0x05AD], pixel, 4),
        ):
            report(sequence, target, instance, asset, tag)
        self.assertNotIn(8022, spray.vars)
        self.assertNotIn(8022, voice.vars)

        native_action = runtime.HANDLERS["STU_94771561"]
        reported_nodes = []

        def use_branch(instance, node, caller):
            reported_nodes.append((instance.graph.index, node.pos))
            with patch.object(instance, "evaluate", side_effect=AssertionError("base Action read unlock data")):
                self.assertIs(native_action(instance, node, caller), True)
            return True

        with patch.dict(runtime.HANDLERS, {"STU_94771561": use_branch}):
            report(4, player.body, spray, pixel)
            self.assertEqual(spray.vars[8022].value(), expr.Asset(pixel))
            self.assertNotIn(8022, voice.vars)
            report(5, player.body, voice, effect)
        self.assertEqual(reported_nodes, [(0x02CF, 39), (0x0686, 70)])
        self.assertEqual(voice.vars[8022].value(), expr.Asset(effect))
        self.assertEqual(spray.vars[8022].value(), expr.Asset(pixel))
        self.assertTrue(spray.node_state(spray.graph.node(38)).active)
        self.assertTrue(voice.node_state(voice.graph.node(71)).active)

    def test_spray_retry_keeps_the_placement_and_slot_until_ack(self):
        for action, miss, x, slot in ((0x1A5, False, -1.0, 1), (0x1A5, True, -1.0, 1), (0x173, False, 0.0, 0)):
            with self.subTest(action=hex(action), miss=miss):
                run = Run(names=("Speaker",))
                player, = run.players
                body = player.body_script
                body.world.spray_unlocks = (0x02500000000007F1, 0x0250000000000004)
                player.mover.position = (0.0, 0.0, 0.0)
                player.mover.yaw = player.mover.pitch = 0
                run.match.collision_world = World(((-3, 0, 3, 3, 0, 3, -3, 4, 3),), masks=(0x40,))
                owner = decode.ClientBody.for_hero(SOLDIER)
                [tree] = body.frames()
                owner.apply(tree)
                body.stream.arrived(tree.chunk_last)
                body.command(body.frame + 1, 0)
                [initial] = body.frames()
                owner.apply(initial)
                body.stream.arrived(initial.chunk_last)

                watcher = decode.ClientBody.for_hero(SOLDIER)
                stream = BodyStream(body.entity)
                baseline = observers.snapshot(body)
                tree = stream.full(observers.full_frame(body, baseline), baseline)
                watcher.apply(tree)
                stream.arrived(tree.chunk_last)

                def select(choice):
                    run.match.communication_action(player, {"+0x78": 0x0A90000000000000 | choice, "+0x80": True})
                    run.match.deliver_body_messages()
                    body.command(body.frame + 1, 0)

                def updates():
                    [local] = body.frames()
                    snap = observers.snapshot(body)
                    frame, keys = observers.delta(body, stream, snap)
                    return local, stream.delta(frame, snap, keys, 0)

                def check(sent, stamps, selected, node, geometry, pulse):
                    for client, update, stamp in zip((owner, watcher), sent, stamps, strict=True):
                        frame = client.apply(update)
                        self.assertEqual(frame.actions, [(stamp, spray.id, node)])
                        self.assertEqual(client.vars[spray.id, 9259], selected)
                        states = frame.instances[spray.id].states
                        self.assertEqual(states.get(1), geometry)
                        self.assertEqual(states.get(14), pulse)

                body.command(body.frame + 16, 0)
                select(0x173)
                [spray] = [i for i in body.component.instances.values() if i.graph.index == 0x02CF]
                original = (True, spray_placement.wire_fields(spray.state(1).payload()))
                first = updates()
                stamps = tuple(update.chunk_last for update in first)
                pulse = (True, {"fresh": True, "count": 1})
                check(first, stamps, 0, 8, original, pulse)
                for update in first:
                    update.stream.lost(update.chunk_last)

                body.command(body.frame + 64, 0)
                player.mover.yaw = 32768 if miss else 0
                player.mover.position = (x, 0.0, 0.0)
                select(action)
                export_body_vm(body)
                retry = updates()
                check(retry, stamps, 0, 8, original, pulse)
                for update in retry:
                    update.stream.arrived(update.chunk_last)

                body.command(body.frame + 1, 0)
                later = updates()
                if miss:
                    geometry, feedback, pulse = (False, {}), 3, None
                else:
                    geometry = (True, spray_placement.wire_fields(spray.state(1).payload()))
                    self.assertEqual(geometry == original, x == 0.0)
                    feedback, pulse = 8, (True, {"fresh": True, "count": 2})
                check(later, tuple(update.chunk_last for update in later), slot, feedback, geometry, pulse)

    def test_delayed_spray_use_report_matches_a_recently_sent_placement(self):
        run = Run(names=("Speaker",))
        player, = run.players
        script = player.body_script
        first, second = 0x02500000000007F1, 0x0250000000000004
        script.world.spray_unlocks = (first, second)
        player.mover.position = (0.0, 0.0, 0.0)
        player.mover.yaw = player.mover.pitch = 0
        run.match.collision_world = World(((-3, 0, 3, 3, 0, 3, -3, 4, 3),), masks=(0x40,))
        owner = decode.ClientBody.for_hero(SOLDIER)
        [tree] = script.frames()
        owner.apply(tree)
        script.stream.arrived(tree.chunk_last)

        def select(action):
            run.match.communication_action(player, {"+0x78": 0x0A90000000000000 | action, "+0x80": True})
            run.match.deliver_body_messages()
            script.command(script.frame + 1, 0)

        def report(unlock, accepted):
            payload = bytes.fromhex("00 00 00 00 01 C3 3B 05 40 0A 04") + struct.pack("<QII", unlock, 0, 0)
            with patch.object(runtime, "send_instance_message", wraps=runtime.send_instance_message) as send:
                run.match.game_message(player, {
                    "+0x78": {"+0x0": player.body}, "+0x80": payload,
                    "+0xA8": 0x0240000000001313, "+0xB0": spray.id,
                }, 17.0)
            if accepted:
                send.assert_called_once_with(spray, 0x0240000000001313, {0x53B: expr.Asset(unlock)}, player.body)
            else:
                send.assert_not_called()

        select(0x173)
        [spray] = [i for i in script.component.instances.values() if i.graph.index == 0x02CF]
        self.assertEqual(spray.state(1).payload()["unlock"], first)
        script.command(script.frame + 16, 0)
        [sent] = script.frames()
        frame = owner.apply(sent)
        self.assertEqual(owner.vars[spray.id, 9259], 0)
        self.assertTrue(frame.instances[spray.id].states[1][0])
        run.match.sent_updates(player, (sent,))
        script.stream.arrived(sent.chunk_last)

        script.command(script.frame + 48, 0)
        select(0x1A5)
        self.assertEqual(spray.state(1).payload()["unlock"], second)
        [unsent] = script.frames()
        script.stream.lost(unsent.chunk_last)
        report(first, True)

        player.mover.yaw = 32768
        script.command(script.frame + 64, 0)
        select(0x1A5)
        self.assertFalse(spray.state(1).active)
        report(second, False)
        report(first, True)
        script.command(script.frame + 1, 0)
        self.assertEqual(spray.vars[8022].value(), expr.Asset(first))

        script.command(script.frame + 130, 0)
        report(first, False)

    def test_callout_and_voice_selections_replicate_the_shared_graph(self):
        cases = (
            (0x16B, 3793, expr.Asset(0x0C00000000005A0B), 27),
            (0x1E5, 29862, expr.Map((
                (2464, expr.Asset(0x0DE0000000010AF3)),
                (1384, expr.Asset(0x0D800000000074BD)),
            )), 59),
            (0x19D, 3793, expr.Asset(0x0C00000000007114), None),
        )
        for action, variable, value, node in cases:
            with self.subTest(action=hex(action)):
                match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
                player = match.add_player(1, "Soldier", SOLDIER, 0, False)
                [create] = match.own_body_updates(player)
                body = player.body_script
                client = decode.ClientBody.for_hero(SOLDIER)
                create.stream.arrived(0)
                [tree] = body.frames()
                client.apply(tree)
                body.stream.arrived(tree.chunk_last)

                choice = expr.Asset(0x0A90000000000000 | action)
                runtime.send_message(
                    body.component, 0x02400000000002A3,
                    {0x8FC: choice, 0x3E1: True}, body.entity,
                )
                body.command(body.frame + 1, 0)
                frames = [client.apply(update) for update in body.frames()]

                [callout] = [number for number, index in client.graphs.items() if index == 0x0686]
                self.assertEqual(client.vars[callout, 29699], choice)
                self.assertEqual(client.vars[callout, variable], value)
                actions = [item for frame in frames for item in frame.actions]
                self.assertEqual(actions, [] if node is None else [(frames[-1].last, callout, node)])
                remote = statescript.chunk(observers.full_frame(body, observers.snapshot(body)), 0, 1)
                remote_callout = decode.read_chunk(remote.getvalue(), remote.count).instances[callout]
                self.assertEqual(remote_callout.vars[variable][0], value)
                self.assertEqual(client.states.get((callout, 13), False), action == 0x19D)
                if action == 0x19D:
                    [pulse] = [
                        frame.instances[callout].states[13] for frame in frames
                        if callout in frame.instances and 13 in frame.instances[callout].states
                    ]
                    self.assertEqual(pulse, (True, {"fresh": True, "count": 1}))
                    self.assertEqual(remote_callout.states[13], pulse)

    def test_voice_pulse_retries_with_its_slot_until_each_stream_acknowledges(self):
        run = Run(names=("Speaker",))
        player, = run.players
        body = player.body_script
        older_effect, newer_effect, unused_effect = (0x0300000000001879, 0x03000000000018C4, 0x03000000000018C5)
        body.world.voice_effects = (older_effect, newer_effect, unused_effect)
        owner = decode.ClientBody.for_hero(SOLDIER)
        [tree] = body.frames()
        owner.apply(tree)
        body.stream.arrived(tree.chunk_last)
        body.command(body.frame + 1, 0)
        [initial] = body.frames()
        owner.apply(initial)
        body.stream.arrived(initial.chunk_last)

        watcher = decode.ClientBody.for_hero(SOLDIER)
        stream = BodyStream(body.entity)
        baseline = observers.snapshot(body)
        tree = stream.full(observers.full_frame(body, baseline), baseline)
        watcher.apply(tree)
        stream.arrived(tree.chunk_last)

        def select(action):
            run.match.communication_action(player, {"+0x78": action, "+0x80": False})
            run.match.deliver_body_messages()
            body.command(body.frame + 1, 0)

        def observer_update():
            snap = observers.snapshot(body)
            frame, keys = observers.delta(body, stream, snap)
            return stream.delta(frame, snap, keys, 0)

        def check(client, update, count, action):
            frame = client.apply(update)
            self.assertEqual(frame.actions, [])
            self.assertEqual(frame.instances[voice.id].states[13], (True, {"fresh": True, "count": count}))
            self.assertEqual(client.vars[voice.id, 29699], expr.Asset(action))

        def report(effect, accepted):
            payload = bytes.fromhex("00 00 00 00 01 C3 3B 05 40 0A 04") + struct.pack("<QII", effect, 0, 0)
            with patch.object(runtime, "send_instance_message", wraps=runtime.send_instance_message) as send:
                run.match.game_message(player, {
                    "+0x78": {"+0x0": player.body}, "+0x80": payload,
                    "+0xA8": 0x0240000000001313, "+0xB0": voice.id,
                }, 17.0)
            if accepted:
                send.assert_called_once_with(voice, 0x0240000000001313, {0x53B: expr.Asset(effect)}, player.body)
            else:
                send.assert_not_called()

        first, second = 0x0A9000000000016F, 0x0A9000000000019D
        body.command(body.frame + 16, 0)
        select(first)
        [voice] = [i for i in body.component.instances.values() if i.graph.index == 0x0686]
        [local] = body.frames()
        observed = observer_update()
        check(watcher, observed, 1, first)
        body.stream.lost(local.chunk_last)  # the queued owner frame was never transmitted
        stream.arrived(observed.chunk_last)

        body.command(body.frame + 32, 0)
        select(second)
        [local] = body.frames()
        observed = observer_update()
        report(older_effect, False)
        run.match.sent_updates(player, (local,))
        check(owner, local, 1, first)
        check(watcher, observed, 2, second)
        report(older_effect, True)
        body.stream.lost(local.chunk_last)
        report(older_effect, True)
        stream.arrived(observed.chunk_last)

        body.command(body.frame + 1, 0)
        [local] = body.frames()
        run.match.sent_updates(player, (local,))
        check(owner, local, 1, first)
        body.stream.arrived(local.chunk_last)

        body.command(body.frame + 1, 0)
        [local] = body.frames()
        run.match.sent_updates(player, (local,))
        check(owner, local, 2, second)
        body.stream.arrived(local.chunk_last)
        report(older_effect, True)
        report(unused_effect, False)
        body.command(body.frame + 1, 0)
        self.assertEqual(voice.vars[8022].value(), expr.Asset(older_effect))

    def test_wheel_effect_actions_replicate_in_authored_order(self):
        for action, nodes in ((0x16C, (40,)), (0x16D, (6, 41))):
            with self.subTest(action=hex(action)):
                run = Run(names=("Speaker",))
                player, = run.players
                body = player.body_script
                connection = game_client()
                connection.player = player
                player.client = connection

                owner = decode.ClientBody.for_hero(SOLDIER)
                [tree] = body.frames()
                owner.apply(tree)
                body.stream.arrived(tree.chunk_last)
                body.command(body.frame + 1, 0)
                [initial] = body.frames()
                owner.apply(initial)
                body.stream.arrived(initial.chunk_last)

                watcher = decode.ClientBody.for_hero(SOLDIER)
                stream = BodyStream(body.entity)
                baseline = observers.snapshot(body)
                tree = stream.full(observers.full_frame(body, baseline), baseline)
                watcher.apply(tree)
                stream.arrived(tree.chunk_last)

                record = b"\x01\x08" + struct.pack("<QBI", 0x0A90000000000000 | action, False, 0)
                packet = BitWriter()
                messages.write_channel(packet, [(0, record)])
                packet.bit(0)
                connection._payload_in(packet.getvalue(), 17.0)
                run.match.deliver_body_messages()
                body.command(body.frame + 16, 0)
                [callout] = [i for i in body.component.instances.values() if i.graph.index == 0x0686]

                def updates():
                    [local] = body.frames()
                    snapshot = observers.snapshot(body)
                    frame, keys = observers.delta(body, stream, snapshot)
                    return local, stream.delta(frame, snapshot, keys, 0)

                for node in nodes:
                    sent = updates()
                    stamps = tuple(update.chunk_last for update in sent)
                    for client, update, stamp in zip((owner, watcher), sent, stamps, strict=True):
                        self.assertEqual(client.apply(update).actions, [(stamp, callout.id, node)])
                    if node == 40:
                        for update in sent:
                            update.stream.lost(update.chunk_last)
                        body.command(body.frame + 1, 0)
                        sent = updates()
                        for client, update, stamp in zip((owner, watcher), sent, stamps, strict=True):
                            self.assertEqual(client.apply(update).actions, [(stamp, callout.id, node)])
                    for update in sent:
                        update.stream.arrived(update.chunk_last)
                    body.command(body.frame + 1, 0)
                snapshot = body.callout_snapshot()
                self.assertIsNone(snapshot.next_callout(body.stream.base.callout))
                self.assertIsNone(snapshot.next_callout(stream.base.callout))

    def test_configured_callout_textures_replicate_after_wire_selection(self):
        for action, texture in ((0x1E5, 0x2A9FB), (0x1F7, 0x2A9FE), (0x1F9, 0x3A73)):
            with self.subTest(action=hex(action)):
                run = Run(names=("Speaker",))
                player, = run.players
                body = player.body_script
                connection = game_client()
                connection.player = player
                player.client = connection
                owner = decode.ClientBody.for_hero(SOLDIER)
                [tree] = body.frames()
                owner.apply(tree)
                body.stream.arrived(tree.chunk_last)

                record = b"\x01\x08" + struct.pack("<QBI", 0x0A90000000000000 | action, False, 0)
                packet = BitWriter()
                messages.write_channel(packet, [(0, record)])
                packet.bit(0)
                connection._payload_in(packet.getvalue(), 17.0)
                run.match.deliver_body_messages()
                body.command(body.frame + 1, 0)

                [update] = body.frames()
                frame = owner.apply(update)
                [callout] = [i for i in body.component.instances.values() if i.graph.index == 0x0686]
                expected = expr.Asset(0x0C00000000000000 | texture)
                self.assertEqual(frame.actions, [(frame.last, callout.id, 59)])
                self.assertEqual(owner.vars[callout.id, 3793], expected)
                remote = statescript.chunk(observers.full_frame(body, observers.snapshot(body)), 0, 1)
                observed = decode.read_chunk(remote.getvalue(), remote.count).instances[callout.id]
                self.assertEqual(observed.vars[3793][0], expected)

                config = callout.graph.node(58).field("m_value")
                for invalid in (action, expr.Asset(0x0A90000100000000 | action), expr.Asset(0x0A9000000000019E)):
                    callout.write((expr.INSTANCE, 29699), invalid)
                    self.assertEqual(callout.evaluate(config), expr.Asset(0))

    def test_greeting_followup_uses_the_authored_source_body_tag_outside_practice(self):
        for game_map, hero_name, follows_up in (
            (PRACTICE_RANGE, "Soldier: 76", True),
            (KANEZAKA_DM, "Soldier: 76", False),
            (KANEZAKA_DM, "Ashe", True),
        ):
            with self.subTest(mode=game_map.mode_guid, hero=hero_name):
                run = Run(game_map, names=("Speaker",))
                player, = run.players
                if hero_name != "Soldier: 76":
                    hero = next(hero for hero in heroes().values() if hero.name == hero_name)
                    run.match.switch_hero(player, hero)
                run.match.communication_action(player, {"+0x78": 0x0A9000000000016B, "+0x80": True})
                run.match.deliver_body_messages()
                body = player.body_script
                body.command(body.frame + 10, 0)
                [gesture] = [i for i in body.component.instances.values() if i.graph.index == 0x0341]
                followup = gesture.node_state(gesture.graph.node(15))
                self.assertEqual(bool(followup and followup.active), follows_up)

    def test_greeting_finishes_at_the_first_authored_model_animation_end(self):
        run = Run(names=("Speaker",))
        player, = run.players
        body = player.body_script
        category = 0x0280000000000004
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / GREETING_DURATION_DATA).write_text(json.dumps({
                hex(body.world.body_definition): {
                    "0x0C800000000002DE": {hex(EMOTE_ANY_CATEGORY): 1.5},
                    "0x0C800000000001EE": {hex(category): 0.75, hex(EMOTE_ANY_CATEGORY): 1.75},
                },
            }), encoding="utf-8")
            with patch("ow174.game.script.driver.DATA_DIR", data):
                self.assertTrue(run.match.queue_body_message(
                    player.body, 0x02400000000002A3,
                    {0x8FC: expr.Asset(0x0A9000000000016B), 0x3E1: True, 0x5FE: expr.Asset(category)},
                    player.body,
                ))
                run.match.deliver_body_messages()
                body.command(body.frame + 10, 0)
                [gesture] = [i for i in body.component.instances.values() if i.graph.index == 0x0341]
                for index, duration in ((5, 1500), (6, 750)):
                    state = gesture.state(index)
                    self.assertTrue(state.active)
                    self.assertEqual([event.time - state.start for event in gesture.queue if event.state is state],
                                     [duration, duration])
                ending_frame = (gesture.state(6).start + 750) // body.frame_ms
                body.command(ending_frame - 1, 0)
                self.assertIn(gesture.id, body.component.instances)
                body.command(ending_frame, 0)
                self.assertNotIn(gesture.id, body.component.instances)
                self.assertIn(gesture.parent.id, body.component.instances)

    def test_greeting_category_replicates_and_expired_callout_can_restart(self):
        run = Run(names=("Speaker",))
        player, = run.players
        body = player.body_script
        owner = decode.ClientBody.for_hero(SOLDIER)

        def apply_owner_updates():
            for update in body.frames():
                owner.apply(update)
                body.stream.arrived(update.chunk_last)

        apply_owner_updates()
        action = 0x0A9000000000016B
        category = expr.Asset(0x0280000000000004)
        self.assertTrue(run.match.queue_body_message(
            player.body, 0x02400000000002A3,
            {0x8FC: expr.Asset(action), 0x3E1: True, 0x5FE: category}, player.body,
        ))
        wheel = next(i for i in body.component.instances.values() if i.graph.index == 0x05AD)
        first = None
        for expected in (category, None):
            if expected is None:
                run.match.communication_action(player, {"+0x78": action, "+0x80": True})
            run.match.deliver_body_messages()
            body.command(body.frame + 10, 0)
            apply_owner_updates()
            self.assertEqual(wheel.vars[15207].value(), expected)
            self.assertEqual(wheel.vars[15231].value(), expr.Asset(0))
            [callout] = [i for i in body.component.instances.values() if i.graph.index == 0x0686]
            [greeting] = [i for i in body.component.instances.values() if i.graph.index == 0x0341]
            self.assertIs(greeting.parent, callout)
            self.assertFalse(callout.stopped)
            if first is not None:
                self.assertNotEqual(callout.id, first.id)
                self.assertNotIn(first.id, owner.graphs)
            remote = statescript.chunk(observers.full_frame(body, observers.snapshot(body)), 0, 1)
            observed = decode.read_chunk(remote.getvalue(), remote.count).instances[greeting.id]
            self.assertEqual(owner.vars.get((greeting.id, 15207)), expected)
            self.assertEqual(observed.vars.get(15207, (None,))[0], expected)
            for state in (5, 6):
                self.assertTrue(owner.states[greeting.id, state])
                self.assertTrue(observed.states[state][0])
            if expected is not None:
                first = callout
                body.command(body.frame + 250, 0)  # beyond the authored four-second wait
                apply_owner_updates()
                self.assertTrue(first.stopped)
                self.assertNotIn(first.id, body.component.instances)
                self.assertNotIn(greeting.id, body.component.instances)
            else:
                body.command(body.frame + 1, FIRE)
                body.command(body.frame + 10, FIRE)
                apply_owner_updates()
                self.assertNotIn(greeting.id, body.component.instances)
                self.assertNotIn(greeting.id, owner.graphs)
                self.assertIn(callout.id, owner.graphs)

    def test_reused_callout_retries_previous_selection_before_sending_next(self):
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        body = player.body_script
        owner = decode.ClientBody.for_hero(SOLDIER)
        create.stream.arrived(0)
        [tree] = body.frames()
        owner.apply(tree)
        body.stream.arrived(tree.chunk_last)
        body.command(body.frame + 1, 0)
        [initial] = body.frames()
        owner.apply(initial)
        body.stream.arrived(initial.chunk_last)

        watcher = decode.ClientBody.for_hero(SOLDIER)
        stream = BodyStream(body.entity)
        baseline = observers.snapshot(body)
        tree = stream.full(observers.full_frame(body, baseline), baseline)
        watcher.apply(tree)
        stream.arrived(tree.chunk_last)

        def observer_update():
            snap = observers.snapshot(body)
            frame, keys = observers.delta(body, stream, snap)
            return stream.delta(frame, snap, keys, 0) if frame is not None else None

        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001E5), 0x3E1: True}, body.entity,
        )
        body.command(body.frame + 16, 0)
        [owner_first] = body.frames()
        viewer_first = observer_update()
        first_seen = watcher.apply(viewer_first).actions
        [callout] = [instance for instance in body.component.instances.values() if instance.graph.index == 0x0686]
        first_text = watcher.vars[callout.id, 29862]
        body.stream.lost(owner_first.chunk_last)
        stream.lost(viewer_first.chunk_last)

        body.command(body.frame + 32, 0)
        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001E6), 0x3E1: True}, body.entity,
        )
        body.command(body.frame + 1, 0)
        second_text = callout.vars[29862].value()
        self.assertNotEqual(first_text, second_text)
        [owner_retry] = body.frames()
        viewer_retry = observer_update()
        self.assertEqual(owner.apply(owner_retry).actions, [(owner_first.chunk_last, callout.id, 59)])
        self.assertEqual(first_seen, [(viewer_first.chunk_last, callout.id, 59)])
        self.assertEqual(watcher.apply(viewer_retry).actions, first_seen)
        for client in (owner, watcher):
            self.assertEqual(client.vars[callout.id, 29862], first_text)
        body.stream.arrived(owner_retry.chunk_last)
        stream.arrived(viewer_retry.chunk_last)

        body.command(body.frame + 1, 0)
        [owner_second] = body.frames()
        viewer_second = observer_update()
        for client, update in ((owner, owner_second), (watcher, viewer_second)):
            self.assertEqual(client.apply(update).actions, [(update.chunk_last, callout.id, 59)])
            self.assertEqual(client.vars[callout.id, 29862], second_text)
            update.stream.arrived(update.chunk_last)

        body.command(body.frame + 1, 0)
        [owner_next] = body.frames()
        self.assertEqual(owner.apply(owner_next).actions, [])
        viewer_next = observer_update()
        if viewer_next is not None:
            self.assertEqual(watcher.apply(viewer_next).actions, [])
        body.command(body.frame + 130, 0)
        self.assertEqual(body.callout_snapshot().callouts, ())

    def test_repeated_ultimate_status_replicates_after_the_voice_stack_expires(self):
        run = Run(names=("Speaker",))
        player, = run.players
        body = player.body_script
        owner = decode.ClientBody.for_hero(SOLDIER)
        [tree] = body.frames()
        owner.apply(tree)
        body.stream.arrived(tree.chunk_last)
        body.command(body.frame + 1, 0)
        [initial] = body.frames()
        owner.apply(initial)
        body.stream.arrived(initial.chunk_last)

        watcher = decode.ClientBody.for_hero(SOLDIER)
        stream = BodyStream(body.entity)
        baseline = observers.snapshot(body)
        tree = stream.full(observers.full_frame(body, baseline), baseline)
        watcher.apply(tree)
        stream.arrived(tree.chunk_last)

        def updates():
            [owner_update] = body.frames()
            snap = observers.snapshot(body)
            frame, keys = observers.delta(body, stream, snap)
            return owner_update, stream.delta(frame, snap, keys, 0)

        def check(updates, node, percent, stamps):
            for client, update, stamp in zip((owner, watcher), updates, stamps, strict=True):
                self.assertEqual(client.apply(update).actions, [(stamp, callout.id, node)])
                if percent is not None:
                    self.assertEqual(client.vars[0, 7730], percent)

        cost = body.component.entity_var(ULT_COST).value()
        first_id = None
        for charge, percent, node in ((0.895, 89.0, 13), (0.905, 90.0, 39), (1.0, None, 7)):
            body.set_ult_charge(cost * charge)
            body.component.run_now()
            run.match.communication_action(player, {"+0x78": 0x0A9000000000016E, "+0x80": False})
            run.match.deliver_body_messages()
            body.command(body.frame + 16, 0)
            [callout] = [i for i in body.component.instances.values() if i.graph.index == 0x0686]
            if first_id is None:
                first_id = callout.id
            self.assertEqual(callout.id, first_id)
            if node == 7:
                self.assertFalse(callout.state(10).active)
            sent = updates()
            stamps = tuple(update.chunk_last for update in sent)
            check(sent, node, percent, stamps)
            if node == 13:
                for update in sent:
                    update.stream.lost(update.chunk_last)
                body.set_ult_charge(cost * 0.95)
                body.command(body.frame + 1, 0)
                self.assertEqual(body.component.entity_var(7730).value(), 95.0)
                sent = updates()
                check(sent, node, percent, stamps)
            for update in sent:
                update.stream.arrived(update.chunk_last)
            body.command(body.frame + 64, 0)
            feedback = updates()
            check(feedback, 14, None, tuple(update.chunk_last for update in feedback))
            for update in feedback:
                update.stream.arrived(update.chunk_last)

    def test_targeted_wheel_message_table_routes_to_the_selected_body(self):
        run = Run(names=("Speaker", "Target", "Bystander"))
        speaker, target, bystander = run.players
        run.step({})

        def wheel(player):
            return next(instance for instance in player.body_script.component.instances.values()
                        if instance.graph.index == 0x05AD)

        source, receiver, other = (wheel(player) for player in run.players)
        source.write((expr.INSTANCE, 375), (expr.Entity(target.body),))
        dispatch = source.graph.node(183)
        rows = source.graph.node(240).field("m_value")["m_configVars"]
        self.assertEqual(len(rows), 20)
        for row in rows:
            action = expr.guid_of(row["m_key"]["m_548E5344"])
            message = expr.guid_of(row["m_value"]["m_gameMessage"])
            with self.subTest(action=hex(action)):
                [entry] = runtime.message_entries(receiver, message)
                source.write((expr.INSTANCE, 29699), expr.Asset(action))
                with patch.object(receiver, "deliver", wraps=receiver.deliver) as receive:
                    source.run_node(dispatch, None)
                    receive.assert_not_called()
                    run.match.deliver_body_messages()
                    [delivery] = receive.call_args.args
                self.assertIs(delivery.node, entry)
                self.assertEqual(delivery.message, (message, {}, speaker.body))
                self.assertEqual(receiver.vars[40].value(), expr.Entity(speaker.body))
                self.assertEqual(receiver.vars[41].value(), expr.Entity(speaker.body))
                self.assertNotIn(40, other.vars)

        source.write((expr.INSTANCE, 29699), expr.Asset(0x0A900001000001E5))
        source.run_node(dispatch, None)
        self.assertEqual(run.match._body_messages, [])

    def test_directed_callouts_keep_each_speaker_until_its_action_is_acknowledged(self):
        run = Run(names=("First speaker", "Second speaker", "Recipient"))
        first, second, recipient = run.players
        run.step({})
        body = recipient.body_script
        owner = decode.ClientBody.for_hero(SOLDIER)
        watcher = decode.ClientBody.for_hero(SOLDIER)
        tree, baseline = body.tree_frame()
        update = body.stream.full(tree, baseline)
        owner.apply(update)
        body.stream.arrived(update.chunk_last)
        watch = observers.Watch(body.entity)
        observer = observers.Observers(run.match)
        with patch.object(first.client, "queue_entities") as send:
            observer._frames(first, recipient, watch, observers.snapshot(body))
        [update] = send.call_args.args[0]
        watcher.apply(update)
        watch.stream.arrived(update.chunk_last)

        def wheel(player):
            return next(instance for instance in player.body_script.component.instances.values()
                        if instance.graph.index == 0x05AD)

        for speaker, action in ((first, 0x1E5), (second, 0x1E6)):
            source = wheel(speaker)
            source.write((expr.INSTANCE, 375), (expr.Entity(recipient.body),))
            source.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | action))
            source.run_node(source.graph.node(183), None)
        run.match.deliver_body_messages()
        main = wheel(recipient)
        self.assertEqual(main.vars[40].value(), expr.Entity(second.body))
        export_body_vm(body)

        def transmit():
            body.command(body.frame + 16, 0)
            [owner_update] = body.frames()
            with patch.object(first.client, "queue_entities") as send:
                observer._frames(first, recipient, watch, observers.snapshot(body))
            [viewer_update] = send.call_args.args[0]
            return owner_update, viewer_update

        def check(updates, speaker, node, stamps):
            for client, update, stamp in zip((owner, watcher), updates, stamps, strict=True):
                self.assertEqual(client.apply(update).actions, [(stamp, main.id, node)])
                if speaker is not None:
                    self.assertEqual(client.vars[main.id, 40], expr.Entity(speaker.body))

        first_updates = transmit()
        stamps = tuple(update.chunk_last for update in first_updates)
        check(first_updates, first, 188, stamps)
        for update in first_updates:
            update.stream.lost(update.chunk_last)

        run.match.tick += observers.LAZY_TICKS
        retry = transmit()
        check(retry, first, 188, stamps)
        for update in retry:
            update.stream.arrived(update.chunk_last)

        for speaker, node in ((None, 14), (second, 195), (None, 14)):
            run.match.tick += 1
            updates = transmit()
            check(updates, speaker, node, tuple(update.chunk_last for update in updates))
            for update in updates:
                update.stream.arrived(update.chunk_last)
