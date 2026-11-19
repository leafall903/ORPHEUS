import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import collision, content, escort
from ow174.game.commands import Command
from ow174.game.mover import Mover, MoverData
from ow174.game.script import expr
from tests.public.test_escort_objective_placeables_contract import FIXTURES


class EscortRoundContractTests(unittest.TestCase):
    def setUp(self):
        contract = json.loads((FIXTURES / "escort-round-v1.json").read_text(encoding="utf-8"))
        self.request = contract["input"]
        self.expected = contract["expected"]
        self.assembly_ends = self.request["assembly_ends"]
        line = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (14.0, 0.0, 0.0))
        goals = self.request["checkpoints"]
        rail = (
            content.EscortRailPoint((0.0, 0.0, 0.0), line),
            content.EscortRailPoint((14.0, 0.0, 0.0), line, goals[0]),
            content.EscortRailPoint((28.0, 0.0, 0.0), line, goals[1]),
            content.EscortRailPoint((42.0, 0.0, 0.0), checkpoint=goals[2]),
        )
        self.round = escort.EscortRound(content.EscortMap(
            self.request["placement"], rail, (), tuple(self.request["stage_seconds"]),
            room_placeables=tuple(
                content.EscortRoomPlacement(
                    row["index"], tuple(tuple(room) for room in row["rooms"]), row["door"],
                    () if row.get("viewers") else (
                        ("sphere", collision.MOVER if row["door"] else
                         (collision.TEAM1, collision.TEAM0)[row["rooms"][0][1]], (0, 0, 0), 1),
                    ),
                    (row["index"], 0, 0), viewers=tuple(row.get("viewers", ())),
                    open_seconds=row.get("open_seconds", 2), close_seconds=row.get("close_seconds", 2),
                    open_delay=row.get("open_delay", 0),
                )
                for row in self.request["room_placeables"]
            ),
        ))

    def test_push_through_both_checkpoints_and_finish_once(self):
        mode = self.round
        self.assertEqual(mode.mode_state(0, self.assembly_ends).entity_vars[escort.COUNTDOWN][0],
                         self.assembly_ends)
        mode.start_setup(self.assembly_ends)
        hud = self.expected["payload_hud"]
        for step, want in zip(self.request["steps"], self.expected["steps"], strict=True):
            with self.subTest(now=step["now"]):
                mode.advance(step["now"], step["players"])
                snapshot = mode.mode_state(step["now"], self.assembly_ends)
                self.assertEqual({
                    "phase": mode.phase.name,
                    "stage": mode.stage,
                    "distance": round(mode.distance, 5),
                    "seconds": snapshot.entity_vars[escort.COUNTDOWN][0],
                    "contested": mode.contested,
                    "winner": mode.winner,
                }, want)
                cart = mode.payload_state()
                self.assertEqual(cart.route_length, hud["route_length"])
                self.assertEqual(cart.checkpoint_fractions, tuple(hud["checkpoint_fractions"]))
                self.assertEqual(cart.distance, want["distance"])
                # The native presenter checks 02BB first, then falls back based on 0270.
                selector = "02BB" if cart.active else "60BA" if cart.enabled else "2A56"
                self.assertEqual(selector, hud["visual_selectors"][want["phase"]])

    def test_payload_hud_tracks_occupancy_and_movement(self):
        mode = self.round
        hud = self.expected["payload_hud"]
        pushers_key = int(hud["pusher_list_variable"], 16)
        blockers_key = int(hud["blocker_list_variable"], 16)
        direction_key = int(hud["direction_variable"], 16)
        mode.start_setup(self.assembly_ends)
        start = mode.deadline
        previous = None
        for step, wanted in zip(self.request["payload_occupancy"], hud["occupancy_steps"], strict=True):
            with self.subTest(seconds=step["seconds"]):
                position = mode.movement().position
                x, y, z = position
                attackers = range(101, 101 + step["attackers"])
                defenders = range(201, 201 + step["defenders"])
                mode.advance(start + step["seconds"], [
                    *((body, escort.ATTACKERS, position) for body in attackers),
                    *((body, escort.DEFENDERS, position) for body in defenders),
                    (301, escort.ATTACKERS, (x + 6, y, z)),
                    (302, escort.DEFENDERS, (x, y + 5, z)),
                ])
                cart = mode.payload_state()
                self.assertEqual(len(cart.pushers), wanted["count"])
                self.assertEqual(cart.direction, wanted["direction"])
                self.assertEqual(mode.contested, wanted["contested"])
                self.assertAlmostEqual(mode.speed, wanted["speed"])
                definition = SimpleNamespace(state=lambda _: SimpleNamespace(cls="synthetic"))
                writer = "remote_full_frame" if previous is None else "remote_delta"
                with (
                    patch.object(escort.graph, "graph", return_value=definition),
                    patch.object(escort, writer) as write,
                ):
                    cart.frame(previous)
                (instance,), entity_vars, _ = write.call_args.args
                for values, key, attribute, expected in (
                    (entity_vars, direction_key, "direction", wanted["direction"]),
                    (instance.vars, pushers_key, "pushers", tuple(map(expr.Entity, attackers))),
                    (instance.vars, blockers_key, "blockers", tuple(map(expr.Entity, defenders))),
                ):
                    if previous is None or getattr(previous, attribute) != expected:
                        self.assertEqual(values[key], (expected, ()))
                    else:
                        self.assertNotIn(key, values)
                previous = cart

    def test_overtime_expires_when_attackers_leave_the_cart(self):
        mode = self.round
        mode.start_setup(self.assembly_ends)
        mode.advance(mode.deadline, ())
        end = mode.deadline
        at_cart = [(101, escort.ATTACKERS, (0, 0, 0)), (102, escort.DEFENDERS, (0, 0, 0))]
        mode.advance(end, at_cart)
        self.assertTrue(mode.overtime)
        self.assertEqual(mode.mode_state(end, self.assembly_ends).entity_vars[6971][0],
                         self.expected["overtime_grace"])
        mode.advance(end + 1, ())
        mode.advance(end + self.expected["overtime_grace"] + 1, at_cart)
        self.assertEqual(mode.winner, self.expected["timeout_winner"])
        self.assertEqual(mode.phase, escort.Phase.COMPLETE)

    def test_presentation_follows_phase_and_waits_for_hero_select_receiver(self):
        mode = self.round
        expected = self.expected["presentation"]
        waiting = mode.mode_state(0, None)
        before = mode.mode_state(0, self.assembly_ends, hero_select_ready=False)
        assembly = mode.mode_state(1, self.assembly_ends, hero_select_ready=True)
        wait_state = expected["waiting_sender_state"]
        assembly_state = expected["assembly_sender_state"]
        self.assertIn(wait_state, waiting.states)
        self.assertNotIn(escort.COUNTDOWN, waiting.entity_vars)
        self.assertNotIn(assembly_state, before.states)
        self.assertNotIn(wait_state, before.states)
        self.assertIn(assembly_state, assembly.states)
        self.assertNotIn(wait_state, assembly.states)
        self.assertEqual(assembly.entity_vars[1923][0], expr.Asset(int(expected["assembly_heading"], 16)))
        self.assertEqual(assembly.entity_vars[escort.OBJECTIVE_KIND][0],
                         expr.Asset(int(expected["escort_objective"], 16)))
        mode.start_setup(self.assembly_ends)
        setup = mode.mode_state(self.assembly_ends, self.assembly_ends)
        self.assertNotIn(assembly_state, setup.states)
        self.assertIn(expected["setup_sender_state"], setup.states)
        self.assertEqual(setup.phase_states, tuple(expected["setup_receiver_states"]))
        self.assertNotIn(expected["escort_hud_state"], setup.states)
        self.assertEqual(setup.entity_vars[1923][0], expr.Asset(int(expected["setup_heading"], 16)))
        start = mode.deadline
        mode.advance(start + 5, [(101, escort.ATTACKERS, (0, 0, 0))])
        active = mode.mode_state(start + 5, self.assembly_ends)
        self.assertNotIn(expected["setup_sender_state"], active.states)
        self.assertIn(expected["active_sender_state"], active.states)
        self.assertEqual(active.phase_states, tuple(expected["active_receiver_states"]))
        self.assertIn(expected["escort_hud_state"], active.states)
        self.assertTrue(mode.payload_state().active)
        self.assertEqual(active.entity_vars[1923][0], expr.Asset(int(expected["defend_heading"], 16)))
        attacker = mode.mode_state(start + 5, self.assembly_ends, viewer_team=escort.ATTACKERS)
        self.assertEqual(attacker.entity_vars[1923][0], expr.Asset(int(expected["attack_heading"], 16)))
        self.assertEqual(active.entity_vars[13468][0], 0.5)
        def graph_stub(index):
            return SimpleNamespace(index=index, state=lambda _: SimpleNamespace(cls="synthetic"))

        with (
            patch.object(escort.graph, "graph", side_effect=graph_stub),
            patch.object(escort, "remote_full_frame") as write_frame,
        ):
            active.frame(None)
        listed_graphs = [f"{item.graph.index:04X}" for item in write_frame.call_args.args[0]]
        self.assertEqual(listed_graphs, expected["active_mode_frame_graphs"])
        self.assertEqual(escort.HUD_PROGRESS_BINDINGS, tuple(expected["escort_hud_progress_bindings"]))
        context = active.entity_vars[int(expected["escort_hud_round_data"], 16)][0]
        self.assertEqual(context.get(int(expected["escort_hud_clock"], 16)), 55)
        self.assertEqual(context.get(escort.CHECKPOINT_INDEX), 0)
        self.assertIs(setup.entity_vars[int(expected["escort_hud_live_flag"], 16)][0], False)
        self.assertIs(active.entity_vars[int(expected["escort_hud_live_flag"], 16)][0], True)
        self.assertEqual(active.entity_vars[59][0], escort.Phase.ACTIVE)
        self.assertEqual(active.entity_vars[2190][0], expr.Entity(mode.payload))
        self.assertEqual(active.instance_vars[int(expected["escort_hud_cart_reference"], 16)][0],
                         expr.Entity(mode.payload))

    def test_gate_presentation_plays_once_and_late_viewers_get_the_open_pose(self):
        mode = self.round
        gate = self.expected["gate_presentation"]
        entity = escort.MAP_ENTITY | gate["index"]
        other = escort.MAP_ENTITY | gate["remaining_gate"]
        mode.start_setup(self.assembly_ends)
        baseline = mode.door_state(entity, self.assembly_ends, None)
        sample = mode.door_state(entity, self.assembly_ends, baseline)
        closed = mode.door_state(entity, self.assembly_ends, sample)
        self.assertEqual([sorted(state.states) for state in (baseline, sample, closed)],
                         gate["closed_wire_states"])
        mode.advance(mode.deadline, ())
        mode.advance(gate["opens_at"], [(101, escort.ATTACKERS, (0, 0, 0))])
        release = mode.door_state(entity, gate["opens_at"], closed)
        self.assertEqual(release.states, closed.states)
        self.assertEqual(mode.door_state(entity, gate["clip_starts_at"] - 0.1, release), release)
        opening = mode.door_state(entity, gate["clip_starts_at"], release)
        self.assertEqual(opening.ends_at, gate["settles_at"])
        self.assertEqual(mode.door_state(entity, gate["settles_at"] - 0.1, opening), opening)
        opened = mode.door_state(entity, gate["settles_at"], opening)
        self.assertEqual([sorted(state.states) for state in (opening, opened)], gate["open_wire_states"])
        self.assertEqual([s.pose.name for s in (closed, opening, opened)], gate["states"])
        late = mode.door_state(entity, gate["settles_at"], None)
        self.assertEqual(late.pose, opened.pose)
        late_sample = mode.door_state(entity, gate["settles_at"], late)
        self.assertEqual(mode.door_state(entity, gate["settles_at"], late_sample), opened)
        self.assertEqual(mode.door_state(other, gate["settles_at"], None).pose, closed.pose)
        movement = mode.door_movement(entity)
        self.assertEqual(movement.position, tuple(gate["position"]))
        self.assertEqual(movement.velocity, (0.0, 0.0, 0.0))

    def test_rooms_release_walls_and_open_doors_in_checkpoint_order(self):
        mode = self.round
        world = mode.collision_world(collision.World([]))
        mode.start_setup(self.assembly_ends)
        for step, wanted in zip(self.request["steps"], self.expected["rooms"], strict=True):
            with self.subTest(now=step["now"]):
                mode.advance(step["now"], step["players"])
                for field, team, mask in (
                    ("visible_defense", escort.DEFENDERS, collision.TEAM0),
                    ("visible_attack", escort.ATTACKERS, collision.TEAM1),
                ):
                    self.assertEqual(mode.room_barriers(team),
                                     {escort.MAP_ENTITY | i for i in wanted[field]})
                    shapes = world.engine.query((38, -2, -2), (64, 2, 2))
                    self.assertCountEqual(
                        [shape.frame[0][0] for shape in shapes if shape.mask & mask],
                        wanted["closed"] + wanted[field],
                    )
                    effects = [70 + team] if mode.stage == 0 else []
                    self.assertEqual(mode.room_visuals(team),
                                     {escort.MAP_ENTITY | i for i in wanted[field] + effects})
                self.assertEqual(
                    {entity & ~escort.MAP_ENTITY for entity in mode.doors if not mode.door_open(entity)},
                    set(wanted["closed"]),
                )

    def test_payload_collision_follows_the_cart_without_mutating_the_map_cache(self):
        mode = self.round
        mode.payload_shapes = (collision.engine_shape(("sphere", 0x18, (0, 0, 0), 1.0)),)
        base = collision.World([])
        world = mode.collision_world(base)
        near = (-2, -2, -2), (2, 2, 2)
        self.assertEqual(base.engine.query(*near), [])
        self.assertTrue(world.engine.query(*near))
        mode.start_setup(self.assembly_ends)
        mode.advance(mode.deadline + 5, [(101, escort.ATTACKERS, (0, 0, 0))])
        self.assertEqual(world.engine.query(*near), [])
        self.assertTrue(world.engine.query((5, -2, -2), (9, 2, 2)))

    def test_cart_supports_a_rider_while_advancing_uphill(self):
        sample, expected = self.request["rider"], self.expected["rider"]
        end = tuple(sample["checkpoint"])
        rail = (
            content.EscortRailPoint((0, 0, 0), ((0, 0, 0), (0, 0, 0), end)),
            content.EscortRailPoint(end, checkpoint=30),
        )
        shape = ("sphere", collision.MOVER, (0, 0, 0), sample["support_radius"])
        mode = escort.EscortRound(content.EscortMap(20, rail, (), (240.0,), (shape,)))
        world = mode.collision_world(collision.World([]))
        geometry = mode.payload_collision
        support = world.ground(0, 0, 2, 0.5)
        rider = Mover((0, support.y, 0), 0, world, MoverData(), category=collision.TEAM1)
        mode.start_setup(0)
        now, dt = mode.deadline, sample["seconds"]
        mode.advance(now, ())
        start = geometry.rider(rider)
        for tick in range(1, sample["ticks"] + 1):
            rider.step(Command(tick), dt)
            anchor = geometry.rider(rider)
            self.assertIsNotNone(anchor)
            mode.advance(now + tick * dt, [(101, escort.ATTACKERS, rider.position)])
            geometry.carry(rider, anchor, dt)
        self.assertEqual(mode.movement().body_pitch, expected["body_pitch"])
        self.assertGreater(mode.distance, expected["min_progress_m"])
        self.assertLess(math.dist(geometry.rider(rider), start), expected["max_drift_m"])
