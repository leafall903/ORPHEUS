"""Original typed-mover policy on the existing mod ledger, without a second profile host."""
import unittest
from dataclasses import replace
from ow174.game import mover
from ow174.game.commands import Command
from ow174.game.script import graph, runtime
from test_game_graph_analysis import make_graph, node
import test_server_snapshot as snapshot_tests
from ow174.game.server_snapshot import snapshot_digest

class DeliveredTypedMoverTests(unittest.TestCase):
    def test_typed_override_scalar_order_expiration_and_base_restoration(self):
        body = mover.Mover((0, 1, 0), 0, data=mover.MoverData())
        slow = replace(body.data, run_forward=3, accel_air=20)
        fast = replace(body.data, run_forward=7, accel_air=35)
        a, b, c = (1, 0, 0), (1, 0, 1), (1, 0, 2)
        body.track_mods({a: (-1, mover.SET, slow, 0), b: (-1, mover.SET, fast, 2),
                         c: (mover.SPEED, mover.MULTIPLY, 2, 0)}, 10)
        def active(frame):
            return sorted((m for m in body.mods.values() if m.acts(frame)), key=mover.Mod.order)
        self.assertIs(mover.modded(body.data, active(10)), body.data)
        self.assertEqual(mover.modded(body.data, active(11)).run_forward, 14)
        self.assertEqual(mover.modded(body.data, active(11)).accel_air, 35)
        body.track_mods({}, 12)
        self.assertEqual(mover.modded(body.data, active(12)).run_forward, 14)
        self.assertIs(mover.modded(body.data, active(13)), body.data)


    def test_real_typed_rows_publish_on_same_mover_frame_not_component_epoch(self):
        raw = next(row["m_9B7A63EA"] for n in graph.graph(0x047E).nodes
                   for row in n.field("m_BC5E91CF") or () if row.get("$") == "STU_ED2B22BD")
        slow, fast = dict(raw), dict(raw)
        forward = next(key for key, value in mover.COMPONENT_FIELDS.items() if value == "run_forward")
        slow["m_" + forward], fast["m_" + forward] = 3, 7
        nodes = [node(i, mover.MOVEMENT_MOD, state=i, fields={"m_BC5E91CF": [
            {"$": "STU_ED2B22BD", "m_9B7A63EA": profile,
             "m_priority": {"$": "STUConfigVarFloat", "m_value": 0}},
        ]}) for i, profile in enumerate((slow, fast))]
        component = runtime.Component(0xA0000100)
        component.frame, component.now = 100, 1600
        instance = component.create(make_graph(0xF131, nodes, states=(0, 1)), init=False)
        instance.state(1).begin()
        component.now += 30
        instance.state(0).begin()
        body = mover.Mover((0, 1, 0))
        body.track_mods(mover.statescript_mods(component), 10)
        body.script_data = mover.statescript_mover_data(component, mover=body, frame=11)
        self.assertEqual(body.script_data.run_forward, 7)
        self.assertEqual({mod.start for mod in body.mods.values()}, {11})
        body.step(Command(10, forward=127), .016, 10)
        self.assertEqual(body.values.run_forward, body.data.run_forward)
        body.step(Command(11, forward=127), .016, 11)
        self.assertEqual(body.values.run_forward, 7)
        instance.state(1).end(False)
        body.track_mods(mover.statescript_mods(component), 12, posed=False)
        self.assertEqual(mover.modded(body.data, sorted((m for m in body.mods.values() if m.acts(12)),
                                                      key=mover.Mod.order)).run_forward, 7)
        self.assertEqual(mover.modded(body.data, sorted((m for m in body.mods.values() if m.acts(13)),
                                                      key=mover.Mod.order)).run_forward, 3)

    def test_typed_override_is_causal_passive_storage(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        body = players[0].mover
        key = (1, 2, 3)
        body.track_mods({key: (-1, mover.SET, replace(body.data, accel_air=35), 0)}, 100)
        first = snapshot_digest(case.capture(session))
        body.mods[key].value = replace(body.mods[key].value, accel_air=36)
        self.assertNotEqual(snapshot_digest(case.capture(session)), first)


    def test_explicit_external_flight_profile_keeps_existing_precedence(self):
        body = mover.Mover((0, 1, 0))
        body.track_mods({(1, 0, 0): (mover.MOVER_COMPONENT, mover.SET,
                                   replace(body.data, run_forward=7), 0)}, 10)
        external = replace(body.data, run_forward=2, gravity_scale=0)
        body.step_pose(Command(11, forward=127), .016, 11, flight=external)
        self.assertEqual(body.values.run_forward, 2)
        self.assertEqual(body.values.gravity_scale, 0)

    def test_original_complete_component_parser_preserves_strict_integer_fields(self):
        raw = next(row["m_9B7A63EA"] for n in graph.graph(0x047E).nodes
                   for row in n.field("m_BC5E91CF") or () if row.get("$") == "STU_ED2B22BD")
        data = mover.component_data(raw)
        self.assertEqual((data.stand_radius, data.accel_air), (mover.f32(0.7), 35))
        for field, value in (("m_E631BA84", None), ("m_CFD3E635", float("nan")),
                             ("m_5C6B4602", 1.5)):
            with self.assertRaises(ValueError):
                mover.component_data({**raw, field: value})
