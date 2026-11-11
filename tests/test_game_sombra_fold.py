"""Original0B8B with real bodies; itemized message fixtures are not native event production."""

import os
import unittest
from unittest.mock import patch

import test_game_sombra_charge as charge
import test_game_sombra_npc as actual_npcs

from ow174.game import ultimate_charge
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import FIRE
from ow174.game.script import expr, runtime

EXCLUDED = 0x0D80000000003EBB


def item(amount, tag=EXCLUDED):
    return expr.Map(((16056, expr.Asset(tag)), (499, amount)))


def root_of(f):
    return next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0xB8B)


def send(f, amount, items, *, tags=(), target=None):
    script = f.a.body_script
    runtime.send_message(script.component, ultimate_charge.DAMAGE_DEALT, {
        0x13: expr.f32(amount), 0x15: expr.Entity(target or f.b.body),
        0x3D: tuple(expr.Asset(tag) for tag in tags), 0x624: items,
    }, f.a.body)
    script.component.run_now()


class SombraFoldTests(unittest.TestCase):
    def make(self):
        case = charge.SombraChargeTests()
        case.setUp()
        return case.make()

    def test_actual_pistol_empty_fold_keeps_typed_null_without_world_shortcut(self):
        f = self.make()
        manager = f.run.match.combat.ultimate_charge
        with patch.object(manager, 'native', wraps=manager.native) as called:
            f.press(FIRE)
        self.assertEqual(f.health(f.b), 192)
        self.assertEqual(charge.SombraChargeTests.charge(f), 8)
        self.assertIsNone(root_of(f).vars[16102].value())
        self.assertFalse(any(c.args[1].get('$') == 'STU_A6B80C63' for c in called.call_args_list))

    def test_original_event_folds_matching_records_before_profiles_and_cap(self):
        f = self.make()
        component = f.a.body_script.component
        component.set(9532, 2)
        component.set(13022, 3)
        before = f.health(f.b)
        send(f, 100, (item(20), (item(90, 0), item(5))))
        root = root_of(f)
        self.assertEqual(root.vars[16102].value(), 25)
        self.assertEqual(charge.SombraChargeTests.charge(f), 450)
        self.assertEqual(f.health(f.b), before)  # raw event fixture, not a weapon damage claim
        f.a.body_script.set_ult_charge(1399)
        send(f, 100, (item(20),))
        self.assertEqual(charge.SombraChargeTests.charge(f), 1400)

    def test_full_exclusion_adds_no_charge_and_unmatched_record_is_not_subtracted(self):
        f = self.make()
        f.a.body_script.set_ult_charge(9)
        send(f, 100, (item(100), item(200, 0)))
        self.assertEqual(root_of(f).vars[16102].value(), 100)
        self.assertEqual(charge.SombraChargeTests.charge(f), 9)

    def test_original_expression_preserves_typed_identifiers_and_missing_map_members(self):
        f = self.make()
        send(f, 100, (item(25), expr.Map(((16056, EXCLUDED), (499, 90))),
                      expr.Map(((16056, expr.Asset(EXCLUDED)),)), None))
        self.assertEqual(root_of(f).vars[16102].value(), 25)
        self.assertEqual(charge.SombraChargeTests.charge(f), 75)

    def test_itemized_event_still_obeys_original_excluded_tags_and_enemy_gate(self):
        for mode in ('emp', 'excluded', 'ally'):
            with self.subTest(mode=mode):
                f = self.make()
                tags = ()
                if mode == 'ally':
                    match = f.run.match
                    match.game_map = match.game_map.__class__(**{
                        **match.game_map.__dict__, 'free_for_all': False,
                    })
                    f.b.team = f.a.team
                else:
                    tags = (0x0D8000000000025A if mode == 'emp' else 0x0D800000000001FD,)
                send(f, 100, (item(20),), tags=tags)
                self.assertEqual(charge.SombraChargeTests.charge(f), 0)

    def test_live_fold_provider_is_passive_causal_snapshot_input_and_unwinds(self):
        f = self.make()
        root = root_of(f)
        step = root.graph._by_pos[83].field('m_value')['m_C239F989']
        original = runtime.Instance.evaluate
        captures = []

        def evaluate(instance, cfg, *args, **kwargs):
            if instance is root and cfg is step:
                provider = instance.component.parameter_scopes[-1]
                with patch.object(runtime.Var, 'value', side_effect=AssertionError('passive capture')):
                    initial = snapshot_digest(export_body_vm(f.a.body_script))
                    saved = provider[0x50D]
                    provider[0x50D] = 987
                    changed = snapshot_digest(export_body_vm(f.a.body_script))
                    provider[0x50D] = saved
                    restored = snapshot_digest(export_body_vm(f.a.body_script))
                self.assertNotEqual(initial, changed)
                self.assertEqual(initial, restored)
                captures.append(provider.copy())
            return original(instance, cfg, *args, **kwargs)

        with patch.object(runtime.Instance, 'evaluate', evaluate):
            send(f, 100, (item(20), item(5)))
        self.assertEqual(len(captures), 2)
        self.assertEqual([p[0x0C] for p in captures], [0, 1])
        self.assertEqual([p[0x50D] for p in captures], [0, 20])
        self.assertFalse(root.component.parameter_scopes)
        self.assertEqual(charge.SombraChargeTests.charge(f), 75)


class SombraFoldNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('ORPHEUS_ULTIMATE_DATA'):
            raise unittest.SkipTest('set immutable ultimate context')
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_original_itemized_event_accepts_each_real_npc_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                f.a.body_script.ult_seconds = 0
                f.press(0)
                f.a.body_script.set_ult_charge(0)
                send(f, 60, (item(10), item(100, 0)), target=bot.entity)
                self.assertEqual(root_of(f).vars[16102].value(), 10)
                self.assertEqual(charge.SombraChargeTests.charge(f), 50)
