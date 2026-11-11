"""Live cost dependency changes settle/rebase a firing segment,not its State."""

import unittest

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import runtime
from tests.test_script_runtime_budget import graph, node


def dynamic(identifier, scope=0):
    return {"$": "STUConfigVarDynamic", "m_identifier": identifier, "m_60DB8F99": scope}


def integer(value):
    return {"$": "STUConfigVarInt", "m_value": value}


def setup(cost):
    component = runtime.Component(123, strict=True)
    component.now = 1000
    component.set(77, cost)
    component.set(78, 5)
    instance = component.create(graph([node(0, "STUStatescriptStateWeaponVolley", state=0,
        m_5F40D9D3=dynamic(77, 1), m_numShotsPerSecond=dynamic(78, 1),
        m_24E351A4=dynamic(53), m_A17BE89B=integer(999), m_A55BC904=integer(1))]), init=False)
    instance.initialized = True
    instance.write((0, 53), 20)
    state = instance.state(0)
    state.begin()
    component.run_now()
    component.now = 1350
    state.report(component.now)
    return component, instance, state


class VolleyRebindTests(unittest.TestCase):
    def test_free_to_paid_preserves_next_shot_and_never_charges_old_free_shots(self):
        c, i, s = setup(0)
        old = list(c.shots)
        c.set(77, 1)
        c.run_now()
        self.assertEqual((s.counter, s.start, s.volleys, s.per_shot, s.ammo0), (1, 1400, 2, 1, 20))
        self.assertEqual(c.shots, old)
        self.assertEqual([r[2] for r in old], [1000, 1200])
        self.assertTrue(all(r[3] == (1, 1000, 1) for r in old))
        self.assertEqual(i.vars[53].value(), 20)
        c.now = 1399
        c.run_now()
        s.report(c.now)
        self.assertEqual(c.shots, old)
        c.now = 1400
        c.run_now()
        s.report(c.now)
        self.assertEqual(c.shots[-1][1:], (0, 1400, (1, 1400, 2)))
        self.assertEqual(i.vars[53].value(), 19)
        c.run_now()
        s.report(c.now)
        self.assertEqual(len(c.shots), 3)

    def test_paid_to_free_keeps_already_consumed_ammo(self):
        c, i, s = setup(1)
        self.assertEqual(i.vars[53].value(), 18)
        c.set(77, 0)
        c.run_now()
        self.assertEqual((s.start, s.ammo0, s.per_shot), (1400, 18, 0))
        c.now = 3400
        c.run_now()
        s.report(c.now)
        self.assertEqual(i.vars[53].value(), 18)

    def test_resampled_rate_keeps_old_next_boundary_then_uses_new_intervals(self):
        c, _, s = setup(0)
        c.set(78, 10)  # represented native compile input; cost invalidation triggers resample
        c.set(77, 1)
        c.run_now()
        c.now = 1600
        c.run_now()
        s.report(c.now)
        self.assertEqual([r[2] for r in c.shots], [1000, 1200, 1400, 1500, 1600])

    def test_old_timers_are_replaced_using_remaining_ammo(self):
        c, i, s = setup(0)
        c.set(77, 1)
        c.run_now()
        timers = sorted((e.param, e.time - c.now) for e in i.queue if e.kind == runtime.TIMER)
        self.assertEqual(timers, [(1, 50), (2, 3850)])
        self.assertIsNone(s.flags_check or s.flags_changed or None)

    def test_release_before_rebased_first_shot_does_not_consume_or_emit(self):
        c, i, s = setup(0)
        c.set(77, 1)
        c.run_now()
        s.end(False)
        self.assertEqual(i.vars[53].value(), 20)
        self.assertFalse(any(e.state is s for e in i.queue))
        c.now = 2000
        c.run_now()
        self.assertEqual(len(c.shots), 2)

    def test_pending_old_firing_generation_is_passive_causal_snapshot_state(self):
        c, _, _ = setup(0)
        c.set(77, 1)
        c.run_now()
        original = snapshot_digest(export_component_vm(c))
        old = c.shots[0]
        c.shots[0] = (*old[:3], (*old[3][:2], 9))
        self.assertNotEqual(snapshot_digest(export_component_vm(c)), original)
        c.shots[0] = old
        self.assertEqual(snapshot_digest(export_component_vm(c)), original)

    def test_firing_generation_wraps_only_at_wire_boundary(self):
        c, _, s = setup(0)
        s.volleys = 255
        c.set(77, 1)
        c.run_now()
        self.assertEqual(s.volleys, 256)
        self.assertEqual(s.payload()["volleys"], 0)
        s.report(s.start)
        self.assertEqual(c.shots[-1][3], (1, 1400, 256))
