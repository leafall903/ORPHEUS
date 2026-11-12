"""Native-backed literal values used by placed component startup."""
import unittest

from ow174.game.script import expr, runtime, startup
from tests.test_script_runtime_budget import graph

RESOURCES = (
    ("STUConfigVarVoiceStimulus", "m_voiceStimulus", 0x0EE00000000009C6),
    ("STUConfigVoiceDefinition", "m_C6C61CB0", 0x07A000000000013B),
    ("STUConfigVarAnimation", "m_animation", 0x0A000000000051F5),
    ("STUConfigVarAnimCategory", "m_category", 0x0280000000000004),
    ("STUConfigVarHero", "m_hero", 0x02E0000000000015),
    ("STUConfigVarTexture", "m_texture", 0x0C00000000000001),
    ("STU_08305297", "m_C21DB248", 0x09A000000000059A),
    ("STU_43CF7A79", "m_entityDef", 0x00400000000020D8),
    ("STU_8556841E", "m_entityDef", 0x04000000000016BE),
    ("STUConfigVarSkinTheme", "m_skinTheme", 0x0A50000000016970),
)


class LiteralContext(expr.Context):
    def native(self, cfg):
        raise AssertionError(f"Literal unexpectedly asked for world services: {cfg}")


class NativeContext(expr.Context):
    def __init__(self, result):
        super().__init__()
        self.result = result
        self.reads = []

    def native(self, cfg):
        self.reads.append(cfg)
        return self.result


class LiteralConfigTests(unittest.TestCase):
    def test_value_presence_distinguishes_false_from_null_and_failed_evaluation(self):
        cfg = {'$': 'STU_58AA0A3B', 'm_value': {'$': 'FixtureConfig'}}
        for value, expected in ((None, False), (expr.EVALUATION_FAILED, False), (False, True),
                                (0, True), ('', True), ((), True), (expr.Asset(0), True)):
            with self.subTest(value=value):
                context = NativeContext(value)
                self.assertIs(expr.evaluate(cfg, context), expected)
                self.assertEqual(context.reads, [cfg['m_value']])
        self.assertIs(expr.evaluate({'$': 'STU_58AA0A3B'}, LiteralContext()), False)

    def test_authored_empty_string_overwrites_the_destination(self):
        cfg = {"$": "STU_92CA44CC", "m_value": None}
        self.assertEqual(expr.evaluate(cfg, LiteralContext(), initial=42), "")

    def test_unkeyed_resources_keep_asset_type_and_exact_guid(self):
        for cls, field, guid in RESOURCES:
            for key in (None, 0, "0x0000000000000000"):
                for value in (None, 0, f"0x{guid:016X}"):
                    with self.subTest(cls=cls, key=key, value=value):
                        config = {"$": cls, "m_resourceKey": key, field: value}
                        self.assertEqual(expr.evaluate(config, LiteralContext(), initial=42),
                                         expr.Asset(guid if value else 0))

    def test_nonzero_resource_key_requires_context_availability(self):
        for cls, field, guid in RESOURCES:
            config = {"$": cls, "m_resourceKey": "0x0F20000000000001", field: f"0x{guid:016X}"}
            context = NativeContext(expr.Asset(0))
            self.assertEqual(expr.evaluate(config, context), expr.Asset(0))
            self.assertEqual(context.reads, [config])

    def test_failed_keyed_evaluation_preserves_destination(self):
        for cls, field, guid in RESOURCES:
            config = {"$": cls, "m_resourceKey": "0x0F20000000000001", field: f"0x{guid:016X}"}
            self.assertEqual(expr.evaluate(config, NativeContext(expr.EVALUATION_FAILED), initial=19), 19)
            self.assertIsNone(expr.evaluate(config, NativeContext(None), initial=19))

    def test_strict_unknown_keyed_services_still_fault_component(self):
        for cls, field, guid in RESOURCES:
            component = runtime.Component(0x800001A8, strict=True)
            instance = component.create(graph(), init=False)
            with self.assertRaises(runtime.UnsupportedScriptError), component.operation():
                instance.evaluate({"$": cls, "m_resourceKey": "0x0F20000000000001", field: f"0x{guid:016X}"})
            self.assertIsInstance(component.fault, runtime.UnsupportedScriptError)

    def test_team_index_is_signed_integer_including_zero_and_sentinels(self):
        for value, expected in ((None, 0), (0, 0), (1, 1), (4, 4), (-1, -1),
                                (0x7FFFFFFF, 0x7FFFFFFF), (0x80000000, -0x80000000), (0xFFFFFFFF, -1)):
            with self.subTest(value=value):
                result = expr.evaluate({"$": "STU_BD6CC023", "m_6CE7A900": value}, LiteralContext())
                self.assertIs(type(result), int)
                self.assertEqual(result, expected)

    def test_component_startup_queues_typed_literals_before_entry(self):
        source = graph()
        source.index = 0xF65
        configs = [{"$": cls, field: f"0x{guid:016X}", "m_resourceKey": None}
                   for cls, field, guid in RESOURCES]
        configs.append({"$": "STU_BD6CC023", "m_6CE7A900": -1})
        definition = {"$": "STUStatescriptComponent", "m_B634821A": [
            {"$": "STUStatescriptGraphWithOverrides", "m_graph": "0x0580000000000F65",
             "m_1EB5A024": [{"m_0D09D2D9": f"0x0D800000000000{index:02X}", "m_value": cfg}
                            for index, cfg in enumerate(configs, 1)]}]}
        component = runtime.Component(0x800001A8, strict=True)
        created = startup.ComponentStartup(component, definition, graph_loader=lambda _: source).start()
        self.assertEqual(len(created), 1)
        instance = created[0]
        self.assertTrue(instance.initialization_pending)
        self.assertFalse(instance.initialized)
        self.assertEqual([event.kind for event in instance.queue], [runtime.ASSIGN] * len(configs))
        self.assertEqual([event.assignment[2] for event in instance.queue],
                         [*(expr.Asset(guid) for _, _, guid in RESOURCES), -1])


if __name__ == "__main__":
    unittest.main()
