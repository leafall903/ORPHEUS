"""Run authored movement states through the player's command and collision path."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import bodytrace, bots, combat, movelog, replay_values, stats
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import StaticCollisionProvider, World, flat_world
from ow174.game.commands import CROUCH, JUMP, Command
from ow174.game.content import PRACTICE_RANGE, SOLDIER, Spawn, heroes
from ow174.game.match import Match
from ow174.game.mover import STEP, WORLD_GRAVITY
from ow174.game.physics_snapshot import snapshot_mover
from ow174.game.script import nodes, runtime
from ow174.game.script.expr import INSTANCE, Entity, Vec3
from tests.runtime.test_game_combat import in_world
from tests.runtime.test_game_mover import FLOOR, box, wall
from tests.runtime.test_game_notices import into_world

START = 100
DOOMFIST = 0x02E000000000012F
UPPERCUT = 0x0DD8
UPPERCUT_VICTIM = 0x0E7D
SHIFT = 0x08
DESTINATION = Vec3(0.0, 1.0, 3.0)
SCENE = replace(PRACTICE_RANGE, spawns=(Spawn(None, (0.0, 1.0, 0.0), 0.0),))
AUTHORED_STATES = (
    (0x02E0000000000029, 0x01DB, 1, 8041, 50.0),  # Genji Swift Strike
    (0x02E0000000000004, 0x006E, 0, 431, 17.0),  # Mercy Guardian Angel's default speed branch
)


class FixedTargetMoveToTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))

    def spawn(self, hero_guid, graph_id, geometry):
        hero = heroes()[hero_guid]
        match = Match(SCENE, collision_provider=StaticCollisionProvider(geometry))
        player = match.add_player(1, hero.name, hero.guid, 0, False)
        into_world(match, player, START)
        self.assertTrue(match.switch_hero(player, hero))
        instances = player.body_script.component.instances.values()
        instance = next(instance for instance in instances if instance.graph.index == graph_id)
        return player, instance

    def start(self, case, geometry, destination=DESTINATION):
        hero_guid, graph_id, state_index, target_var, speed = case
        player, instance = self.spawn(hero_guid, graph_id, geometry)
        state = instance.state(state_index)
        self.assertEqual(state.node.cls, "STUStatescriptStateMoveTo")
        self.assertIsNone(state.node.fields["m_destinationEntity"])
        self.assertIsNone(state.node.fields["m_destinationBone"])
        # The ray and ally selection use independent engine queries. Supply only their resolved endpoint.
        instance.write((INSTANCE, target_var), destination)
        self.assertEqual(state.evaluate(state.node.fields["m_865F3D01"]), destination)
        self.assertEqual(state.evaluate(state.node.fields["m_speed"]), speed)
        ended = self.enterContext(patch.object(state, "on_end", wraps=state.on_end))
        state.request(runtime.BEGIN)
        instance.component.run_now()
        self.assertTrue(state.active)
        self.assertEqual(player.mover.position, [0.0, 1.0, 0.0])
        return player, state, ended

    @staticmethod
    def command(player, frame, **inputs):
        player.match.tick = frame
        player.take_commands([Command(frame, **inputs)])
        return player.mover.history[-1]

    def commands(self, player, frames):
        for frame in frames:
            self.command(player, frame)
        return list(player.mover.history)

    def test_genji_first_shift_reads_unit_scaled_cast_length(self):
        hero_guid, graph_id, state_index, _, speed = AUTHORED_STATES[0]
        player, instance = self.spawn(hero_guid, graph_id, flat_world(1.0))
        self.assertEqual(instance.vars[1648].value(), 15.0)
        cast_lengths = []

        def observe_ray(caller, node, _state):
            if caller is instance:
                cast_lengths.append(caller.evaluate(node.field("m_castLength")))
            caller.follow(node, "m_outPlug", None)
            return True

        with patch.dict(runtime.HANDLERS, {"STU_08D6BA6E": observe_ray}):
            player.match.tick = START + 1
            player.take_commands([Command(START + 1, buttons=0x08)])

        self.assertEqual(cast_lengths, [15.0])
        state = instance.state(state_index)
        self.assertTrue(state.active)
        self.assertEqual(state.evaluate(state.node.field("m_speed")), speed)
        self.assertEqual(state.move_request(), (Vec3(0.0, 1.0, 15.0), speed))

    def test_genji_authored_miss_uses_the_world_body_position(self):
        hero_guid, graph_id, state_index, _, speed = AUTHORED_STATES[0]
        origin = Vec3(0.0, 2.2, 0.0)
        direction = Vec3(0.0, 0.0, 1.0)
        hit_point = Vec3(0.0, 1.0, 3.0)
        for point, expected in ((None, Vec3(0.0, 1.0, 15.0)), (hit_point, hit_point)):
            with self.subTest(hit=point is not None):
                player, instance = self.spawn(hero_guid, graph_id, flat_world(1.0))
                world_class = type(instance.component.world)
                native = world_class.native

                def resolve_origin(world, cfg, reader, *, source=instance, original=native):
                    if reader is source and cfg.get("$") == "STU_21A13145":
                        return origin
                    return original(world, cfg, reader)

                def complete_ray(world, request, *, point=point):
                    self.assertEqual(
                        (request.origin, request.direction, request.length), (origin, direction, 15.0),
                    )
                    self.assertEqual(request.ignored_entities, (world.body,))
                    return runtime.RaycastResult(point is not None, None, point, None)

                with (
                    patch.object(world_class, "native", resolve_origin),
                    patch.object(world_class, "raycast", complete_ray),
                ):
                    player.match.tick = START + 1
                    player.take_commands([Command(START + 1, buttons=0x08)])
                self.assertEqual(instance.state(state_index).move_request(), (expected, speed))

    def test_doomfist_slam_selector_requires_airborne_clearance(self):
        for jump, clear_ground in ((False, False), (True, False), (True, True)):
            with self.subTest(jump=jump, clear_ground=clear_ground):
                player, slam = self.spawn(DOOMFIST, 0x0B27, flat_world(1.0))
                component = slam.component
                movement = next(inst for inst in component.instances.values() if inst.graph.index == 0x01C7)
                ability = slam.state(0)
                selector = slam.state(6)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertFalse(selector.active)

                player.match.tick = START + 1
                player.take_commands([Command(START + 1, buttons=JUMP if jump else 0)])
                if clear_ground:
                    # The ledge ends under the airborne player. Preserve the jump sample.
                    geometry = flat_world(-2.5)
                    player.match.collision_world = geometry
                    player.mover.use(world=geometry)
                player.match.tick = START + 2
                player.take_commands([Command(START + 2, buttons=0x10)])  # E / Ability 2

                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(player.mover.airborne, jump)
                airborne = component.entity_var(417, create=False)
                self.assertIs(airborne.value() if airborne else None, True if jump else None)
                self.assertEqual(selector.active, clear_ground)
                if jump:
                    self.assertIs(slam.vars[2413].value(), not clear_ground)
                sample = (1 << 11) | (1 << 4) | (1 << 2) | 1 if jump else 1 << 1
                self.assertEqual(
                    player.body_script.snapshot().states[(movement.id, 4)],
                    ("STU_96F1DBCF", True, (("value", sample),)),
                )
                vm = replay_values.decode(export_body_vm(player.body_script)["value"])
                saved = next(item for item in vm["objects"] if item["ref"] == ["state", movement.id, 4])
                self.assertEqual(dict(saved["fields"])["sample"], sample)

                if jump:
                    self.commands(player, range(START + 3, START + 100))
                    self.assertFalse(player.mover.airborne)
                    self.assertIs(airborne.value(), False)
                    self.assertFalse(selector.active)
                    self.assertEqual(movement.state(4).payload(), {"value": 1 << 1})

    def test_doomfist_uppercut_applies_lift_and_attenuation_on_the_authored_frames(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                player, uppercut = self.spawn(DOOMFIST, UPPERCUT, flat_world(1.0))
                ability = uppercut.state(0)
                self.command(player, START + 1, buttons=JUMP if jump else 0)
                self.assertEqual(player.mover.airborne, jump)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)

                cast = START + 2
                started = self.command(player, cast, pitch=4096, buttons=SHIFT)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertLess(started.velocity[1], 10.0)
                self.assertEqual(started.velocity[2], 0.0)
                # The pitched aim supplies yaw. The body-facing vector plus 0.3 up supplies pitch.
                basis_length = math.hypot(1.0, 0.3)
                vertical, forward = 3.0 / basis_length, 10.0 / basis_length
                physics = replay_values.decode(snapshot_mover(player.mover)["state"])
                modifiers = [dict(node["fields"]) for node in physics["nodes"] if node["class"] == "Mod"]
                queued = [mod for mod in modifiers if (mod["start"], mod["end"]) == (cast + 1, cast + 2)]
                self.assertEqual(len(queued), 3)
                delta_ref = next(mod["value"]["node"] for mod in queued if isinstance(mod["value"], dict))
                delta = physics["nodes"][delta_ref]["items"]
                self.assertAlmostEqual(delta[1], vertical, delta=STEP)
                self.assertAlmostEqual(delta[2], forward, delta=STEP)

                lunge = self.command(player, cast + 1)
                self.assertAlmostEqual(lunge.velocity[1], vertical, delta=STEP)
                self.assertAlmostEqual(lunge.velocity[2], forward, delta=STEP)
                self.commands(player, range(cast + 2, cast + 9))
                before_lift = self.command(player, cast + 9, yaw=16384)
                self.assertLess(before_lift.velocity[1], 10.0)
                # The 150 ms action reads +X at frame 9. The next pose faces +Z.
                lift = self.command(player, cast + 10)
                self.assertTrue(player.mover.airborne)
                self.assertAlmostEqual(lift.velocity[0], 1.0, delta=STEP)
                self.assertAlmostEqual(lift.velocity[1], 40.0, delta=STEP)
                self.assertGreater(lift.position[1], before_lift.position[1])

                history = tuple(player.mover.history)
                player.take_commands([Command(cast + 9, yaw=16384), Command(cast + 10)])
                self.assertEqual(tuple(player.mover.history), history)
                self.assertEqual(player.body_script.frame, cast + 10)

                after_lift = self.command(player, cast + 11)
                gravity_per_frame = WORLD_GRAVITY * player.mover.values.gravity_scale * player.quanta / 1e6
                self.assertAlmostEqual(
                    after_lift.velocity[1], lift.velocity[1] - gravity_per_frame, delta=STEP,
                )
                self.commands(player, range(cast + 12, cast + 16))
                before_slowdown = player.mover.history[-1]
                self.assertGreater(before_slowdown.velocity[1], 35.0)
                slowed = self.command(player, cast + 16)
                expected = (before_slowdown.velocity[1] - gravity_per_frame) * 0.2
                self.assertAlmostEqual(slowed.velocity[1], expected, delta=STEP)
                next_pose = self.command(player, cast + 17)
                self.assertAlmostEqual(
                    next_pose.velocity[1], slowed.velocity[1] - gravity_per_frame, delta=STEP,
                )

    def test_doomfist_uppercut_hits_a_ceiling_and_lands_on_the_floor(self):
        ceiling = 4.4
        geometry = World(FLOOR + box(-30.0, 30.0, ceiling, 5.0, -30.0, 30.0))
        player, _ = self.spawn(DOOMFIST, UPPERCUT, geometry)
        cast = START + 1
        self.command(player, cast, buttons=SHIFT)
        peak = 0.0
        for frame in range(cast + 1, cast + 121):
            self.command(player, frame)
            body = player.mover.body
            peak = max(peak, body.position[1] + body.top + body.radius)
        self.assertLessEqual(peak, ceiling + STEP)
        self.assertGreaterEqual(peak, ceiling - 0.01)
        self.assertAlmostEqual(player.mover.position[1], 1.0, delta=STEP)
        self.assertFalse(player.mover.airborne)

    def uppercut_duel(self, geometry, team, position):
        with patch.object(bots, "make", return_value=[]):
            player, uppercut = self.spawn(DOOMFIST, UPPERCUT, geometry)
        match = player.match
        in_world(match, player)
        opponent = match.add_player(2, "Soldier", SOLDIER, team, False)
        in_world(match, opponent)
        self.assertTrue(match.switch_hero(opponent, opponent.hero))
        opponent.mover.position[:] = position
        target = next(target for target in match.combat._targets() if target.owner is opponent)
        return player, uppercut, target, match.combat.vitals_of(target)

    def uppercut_frame(self, player, frame, buttons=0):
        self.command(player, frame, buttons=buttons)
        player.match.update(frame * player.quanta / 1_000_000, frame, frames=False)

    @staticmethod
    def uppercut_victim_instances(player):
        return [
            instance for instance in player.body_script.component.instances.values()
            if instance.graph.index == UPPERCUT_VICTIM
        ]

    def test_doomfist_uppercut_resets_airborne_victim_and_releases_input_on_landing(self):
        player, _, target, vitals = self.uppercut_duel(flat_world(1.0), 1, (0.0, 1.0, 2.5))
        opponent = target.owner
        cast = START + 1
        jumping = self.command(opponent, cast, buttons=JUMP)
        self.assertTrue(opponent.mover.airborne)
        self.assertGreater(jumping.velocity[1], 0.0)

        self.uppercut_frame(player, cast, SHIFT)
        self.assertEqual(vitals.total, 150.0)
        helper, = self.uppercut_victim_instances(opponent)
        self.assertEqual(self.uppercut_victim_instances(player), [])
        self.assertFalse(helper.stopped)
        self.assertEqual(opponent.body_script.snapshot().instances[helper.id], (UPPERCUT_VICTIM, None))
        export_body_vm(opponent.body_script)

        halted = self.command(opponent, cast + 1, forward=127, right=127, buttons=CROUCH)
        self.assertEqual(halted.position, jumping.position)
        self.assertEqual(halted.velocity, (0.0, 0.0, 0.0))
        self.assertFalse(opponent.mover.crouched)
        falling = self.command(opponent, cast + 2, forward=127, right=127, buttons=CROUCH)
        self.assertEqual(falling.velocity[::2], (0.0, 0.0))
        self.assertLess(falling.velocity[1], 0.0)

        for frame in range(cast + 3, cast + 38):
            self.command(opponent, frame)
            if not opponent.mover.airborne:
                break
        else:
            self.fail("Uppercut victim did not land")
        self.assertEqual(self.uppercut_victim_instances(opponent), [])
        self.assertNotIn(helper.id, opponent.body_script.snapshot().instances)
        resumed = self.command(opponent, frame + 1, forward=127)
        self.assertGreater(resumed.position[2], falling.position[2])

    def test_doomfist_uppercut_discards_victim_request_after_body_replacement(self):
        player, _, target, vitals = self.uppercut_duel(flat_world(1.0), 1, (0.0, 1.0, 2.5))
        opponent = target.owner
        old_body = opponent.body
        cast = START + 1
        match = player.match
        self.command(opponent, cast)
        with patch.object(match, "queue_body_play", wraps=match.queue_body_play) as queued:
            self.command(player, cast, buttons=SHIFT)
        self.assertEqual(vitals.total, 150.0)
        queued.assert_called_once()
        self.assertEqual(queued.call_args.args[1].targets, (old_body,))

        self.assertTrue(match.switch_hero(opponent, opponent.hero))
        self.assertNotEqual(opponent.body, old_body)
        match.update(cast * player.quanta / 1_000_000, cast, frames=False)
        self.assertEqual(self.uppercut_victim_instances(opponent), [])
        jumping = self.command(opponent, cast + 1, buttons=JUMP)
        self.assertGreater(jumping.velocity[1], 0.0)

    def test_doomfist_uppercut_damages_a_tracked_enemy_once_per_cast(self):
        feet = (0.0, 1.0, 2.5)
        player, uppercut, target, vitals = self.uppercut_duel(flat_world(1.0), 1, feet)
        opponent = target.owner
        ability, tracker, modify_health = (uppercut.state(index) for index in (0, 9, 10))
        cast = START + 1
        self.command(opponent, cast)
        self.uppercut_frame(player, cast, SHIFT)
        first_helper, = self.uppercut_victim_instances(opponent)
        self.assertTrue(tracker.active and modify_health.active)
        opponent.mover.position[:] = (0.0, 1.0, 12.0)
        self.uppercut_frame(player, cast + 1)
        self.assertEqual(uppercut.vars[375].value(), (Entity(target.entity),))
        self.assertEqual(tracker.payload(), {"targets": ()})
        opponent.mover.position[:] = feet
        vm = replay_values.decode(export_body_vm(player.body_script)["value"])
        self.assertIn(["state", uppercut.id, 9], [obj["ref"] for obj in vm["objects"]])
        settled = cast + 24
        for frame in range(cast + 2, settled):
            self.uppercut_frame(player, frame)
        self.assertFalse(tracker.active or modify_health.active)
        self.assertEqual(vitals.total, 150.0)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 50.0)
        hit = {"+0x0": {"+0x0": target.entity}, "+0x4": 50, "+0x8": 0}
        self.assertEqual([notice["+0x78"] for notice in player.client.messages(combat.HIT)], [hit])
        # Grounded victims leave the helper through its three-second fallback timer.
        self.command(opponent, cast + 187)
        self.assertEqual(self.uppercut_victim_instances(opponent), [first_helper])
        self.command(opponent, cast + 188)
        self.assertEqual(self.uppercut_victim_instances(opponent), [])

        for frame in range(settled, cast + 480):
            self.uppercut_frame(player, frame)
            if not (ability.flags & nodes.Ability.RUNNING) and ability.remaining() == 0.0:
                break
        else:
            self.fail("Uppercut did not finish its cooldown")
        self.assertFalse(player.mover.airborne)
        x, y, z = player.mover.position
        ready = frame + 1
        self.command(opponent, ready)
        opponent.mover.position[:] = (x, y, z + feet[2])
        self.uppercut_frame(player, ready, SHIFT)
        second_helper, = self.uppercut_victim_instances(opponent)
        self.assertNotEqual(first_helper.id, second_helper.id)
        self.assertTrue(tracker.active and modify_health.active)
        for frame in range(ready + 1, ready + 24):
            self.uppercut_frame(player, frame)
        self.assertEqual(vitals.total, 100.0)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 100.0)
        self.assertEqual([notice["+0x78"] for notice in player.client.messages(combat.HIT)], [hit, hit])

    def test_doomfist_uppercut_samples_targets_on_its_authored_frames(self):
        player, uppercut, target, vitals = self.uppercut_duel(flat_world(1.0), 1, (0.0, 1.0, 12.0))
        self.assertEqual((player.body, uppercut.state(9).node.rid), (0xA0000101, 15))
        self.uppercut_frame(player, 101, SHIFT)
        self.uppercut_frame(player, 102)
        target.owner.mover.position[:] = (0.0, 1.0, 2.5)
        for frame in (103, 104):
            self.uppercut_frame(player, frame)
            self.assertEqual(vitals.total, 200.0)
            self.assertEqual(uppercut.vars[375].value(), ())

        self.uppercut_frame(player, 105)
        self.assertEqual(vitals.total, 150.0)
        self.assertEqual(uppercut.vars[375].value(), (Entity(target.entity),))

    def test_doomfist_uppercut_ignores_friendly_distant_and_blocked_bodies(self):
        geometry = flat_world(1.0)
        blocked = World(FLOOR + wall(-4.0, 1.4, 4.0, 1.4))
        cases = (
            ("friendly", geometry, 0, (0.0, 1.0, 2.5)),
            ("distant", geometry, 1, (8.0, 1.0, 2.5)),
            ("blocked", blocked, 1, (0.0, 1.0, 2.5)),
        )
        for name, world, team, position in cases:
            with self.subTest(target=name):
                player, uppercut, _, vitals = self.uppercut_duel(world, team, position)
                self.uppercut_frame(player, START + 1, SHIFT)
                self.assertTrue(uppercut.state(9).active)
                for frame in range(START + 2, START + 25):
                    self.uppercut_frame(player, frame)
                self.assertEqual(vitals.total, 200.0)
                self.assertEqual(player.client.messages(combat.HIT), [])

    def test_entity_position_tracks_the_current_body_and_clears_on_retirement(self):
        hero_guid, graph_id, *_ = AUTHORED_STATES[0]
        player, instance = self.spawn(hero_guid, graph_id, flat_world(1.0))
        match = player.match
        script_world = instance.component.world
        other = match.add_player(2, "Other", hero_guid, 0, False)
        into_world(match, other, START)
        self.assertTrue(match.switch_hero(other, heroes()[hero_guid]))
        bot = match.bots[0]

        self.assertEqual(script_world.position(player.body), Vec3(0.0, 1.0, 0.0))
        self.assertEqual(script_world.position(bot.entity), Vec3(*bot.position))
        other.mover.position[:] = (3.0, 1.0, 8.0)
        self.assertEqual(script_world.position(other.body), Vec3(3.0, 1.0, 8.0))
        other.spawned = False
        self.assertEqual(script_world.position(other.body), Vec3(0.0, 0.0, 0.0))
        self.assertEqual(other.body_script.world.position(other.body), Vec3(3.0, 1.0, 8.0))
        other.spawned = True

        old_body = other.body
        self.assertTrue(match.switch_hero(other, heroes()[hero_guid]))
        self.assertEqual(script_world.position(old_body), Vec3(0.0, 0.0, 0.0))
        self.assertEqual(script_world.position(other.body), Vec3(*other.spawn[0]))
        match.stop_body_script(player)
        self.assertEqual(script_world.position(player.body), Vec3(0.0, 0.0, 0.0))

    def test_entity_scale_requires_own_body_and_unit_override(self):
        hero_guid, graph_id, *_ = AUTHORED_STATES[0]
        _, instance = self.spawn(hero_guid, graph_id, flat_world(1.0))
        owner_scale = {"$": "STU_FDBB0B67", "m_entity": {"$": "STU_B5A0CAF0"}}
        dynamic_scale = {
            "$": "STU_FDBB0B67",
            "m_entity": {"$": "STUConfigVarDynamic", "m_identifier": 38, "m_60DB8F99": INSTANCE},
        }
        self.assertEqual(instance.evaluate(owner_scale), 1.0)
        self.assertIsNone(instance.evaluate(dynamic_scale))
        instance.write((INSTANCE, 38), Entity(instance.component.entity + 1))
        self.assertIsNone(instance.evaluate(dynamic_scale))
        instance.write((INSTANCE, 38), Entity(instance.component.entity))
        self.assertEqual(instance.evaluate(dynamic_scale), 1.0)

        for override, result in ((0.5, None), (True, None), (1.0, 1.0), (None, 1.0)):
            with self.subTest(override=override):
                instance.component.set(31210, override)
                self.assertEqual(instance.evaluate(owner_scale), result)

    def test_both_authored_states_reach_and_hold_the_resolved_endpoint(self):
        for case in AUTHORED_STATES:
            with self.subTest(graph=f"{case[1]:04X}"):
                player, state, ended = self.start(case, flat_world(1.0))
                path = self.commands(player, range(START + 1, START + 26))
                self.assertGreater(path[0].position[2], 0.0)
                maximum = case[-1] * player.quanta / 1_000_000 + STEP
                last = 0.0
                for pose in path:
                    x, y, z = pose.position
                    self.assertEqual((x, y), (0.0, 1.0))
                    self.assertLessEqual(z, DESTINATION.z)
                    self.assertGreaterEqual(z - last, 0.0)
                    self.assertLessEqual(z - last, maximum)
                    last = z
                self.assertEqual([pose.position for pose in path[-4:]], [(0.0, 1.0, 3.0)] * 4)
                self.assertEqual(path[-1].velocity, (0.0, 0.0, 0.0))
                self.assertFalse(state.active)
                ended.assert_called_once_with(True)

    def test_both_authored_states_stop_at_the_same_wall(self):
        geometry = World(FLOOR + wall(-5.0, 2.0, 5.0, 2.0))
        for case in AUTHORED_STATES:
            with self.subTest(graph=f"{case[1]:04X}"):
                player, state, ended = self.start(case, geometry)
                path = self.commands(player, range(START + 1, START + 26))
                stop = path[-1].position
                self.assertGreater(stop[2], 0.5)
                self.assertLessEqual(stop[2] + player.mover.data.stand_radius, 2.0 + STEP)
                self.assertEqual([pose.position for pose in path[-4:]], [stop] * 4)
                self.assertEqual(path[-1].velocity, (0.0, 0.0, 0.0))
                self.assertFalse(state.active)
                ended.assert_called_once_with(False)

    def test_world_target_can_be_above_the_ground(self):
        destination = Vec3(0.0, 4.0, 0.0)
        for case in AUTHORED_STATES:
            with self.subTest(graph=f"{case[1]:04X}"):
                player, state, ended = self.start(case, flat_world(1.0), destination)
                finish = START + math.ceil(3.0 / (case[-1] * player.quanta / 1_000_000))
                path = self.commands(player, range(START + 1, finish + 1))
                self.assertEqual(path[-1].position, tuple(destination))
                self.assertFalse(state.active)
                ended.assert_called_once_with(True)
                path = self.commands(player, (finish + 1,))
                self.assertLess(path[-1].position[1], destination.y)

    def test_unresolved_or_out_of_range_target_does_not_drive_a_pose(self):
        case = AUTHORED_STATES[0]
        for destination in (0.0, Vec3(0.0, 1.0, 65536.0)):
            with self.subTest(destination=destination):
                player, state, ended = self.start(case, flat_world(1.0), destination)
                path = self.commands(player, range(START + 1, START + 5))
                self.assertTrue(all(pose.position == (0.0, 1.0, 0.0) for pose in path))
                self.assertTrue(state.active)
                ended.assert_not_called()

    def test_downward_target_cannot_put_feet_below_walkable_ground(self):
        case = AUTHORED_STATES[0]
        player, state, ended = self.start(case, flat_world(1.0), Vec3(0.0, -2.0, 0.0))
        path = self.commands(player, range(START + 1, START + 5))
        self.assertTrue(all(pose.position == (0.0, 1.0, 0.0) for pose in path))
        self.assertFalse(state.active)
        ended.assert_called_once_with(False)

    def test_bounded_missing_commands_reproduce_the_same_path(self):
        geometry = flat_world(1.0)
        for case in AUTHORED_STATES:
            with self.subTest(graph=f"{case[1]:04X}"):
                contiguous, state, ended = self.start(case, geometry)
                sparse, sparse_state, sparse_ended = self.start(case, geometry)
                expected = self.commands(contiguous, range(START + 1, START + 19))
                actual = self.commands(sparse, (START + 1, START + 8, START + 18))
                self.assertEqual(actual, expected)
                self.assertEqual(actual[-1].position, (0.0, 1.0, 3.0))
                self.assertFalse(state.active or sparse_state.active)
                ended.assert_called_once_with(True)
                sparse_ended.assert_called_once_with(True)

    def test_abort_stops_before_the_next_command_pose(self):
        geometry = flat_world(1.0)
        for case in AUTHORED_STATES:
            with self.subTest(graph=f"{case[1]:04X}"):
                player, state, ended = self.start(case, geometry)
                path = self.commands(player, (START + 1, START + 2))
                stopped = path[-1].position
                self.assertGreater(stopped[2], 0.0)
                self.assertLess(stopped[2], DESTINATION.z)
                state.request(runtime.ABORT)
                state.instance.component.run_now()
                path = self.commands(player, range(START + 3, START + 9))
                self.assertEqual([pose.position for pose in path[2:]], [stopped] * 6)
                self.assertEqual(path[-1].velocity, (0.0, 0.0, 0.0))
                self.assertFalse(state.active)
                ended.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
