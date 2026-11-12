import unittest
from unittest.mock import patch

import test_zenyatta_control as control
from test_game_combat import aim

from ow174.game import combat, mccree_fan
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import SECONDARY


class ZenyattaFanTests(unittest.TestCase):
    def test_actual_fan_spends_six_rounds_with_or_without_transcendence(self):
        for transcendent in (False, True):
            with self.subTest(transcendent=transcendent):
                self.check_fan_against_zen(transcendent)

    def check_fan_against_zen(self, transcendent):
        helper, run, caster, zen = control.ZenyattaControlTests().arena()
        fight = run.match.combat
        if transcendent:
            zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
            helper.step(run, caster, zen, target_buttons=0x20)
            self.assertTrue(fight.zenyatta.ultimate(zen))
        x, y, z = zen.mover.position
        yaw, pitch = aim(fight._eye(caster), (x, y+1, z))
        rays = []
        fan_direction = mccree_fan.fan_direction

        def observe(player, state, forward, number, time, activation):
            ray = fan_direction(player, state, forward, number, time, activation)
            if player is caster and state.instance.graph.index == 0x1D2 and state.node.pos == 37:
                self.assertNotEqual(ray, forward)
                rays.append(ray)
            return ray

        with patch.object(mccree_fan, "fan_direction", side_effect=observe):
            for _ in range(80):
                run.step({caster: (yaw, pitch, SECONDARY)})
                if len(rays) == 6:
                    break
        component = caster.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x1D2)
        self.assertEqual(len(rays), 6)
        self.assertEqual(len(set(rays)), 6)
        self.assertEqual(weapon.vars[53].value(), 0)
        health = helper.health(run, zen)
        hits = caster.client.messages(combat.HIT)
        if transcendent:
            self.assertEqual(health, 200)
            self.assertFalse(hits)
            self.assertTrue(fight.zenyatta.ultimate(zen))
        else:
            self.assertLess(health, 200)
            self.assertTrue(hits)

    def test_baked_fan_ramp_is_causal_snapshot_state(self):
        _, run, caster, _ = control.ZenyattaControlTests().arena()
        component = caster.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x1D2)
        state = next(s for s in weapon.states.values() if s.node.pos == 37)
        for _ in range(80):
            run.step({caster: (0, 0, SECONDARY)})
            if weapon.vars[53].value() < 6:
                break
        self.assertLess(weapon.vars[53].value(), 6)
        self.assertIsNotNone(state.spread_ramp)
        ramp = state.spread_ramp
        before = snapshot_digest(export_component_vm(component))
        self.assertEqual(state.spread_ramp, ramp)
        state.spread_ramp = ramp + 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        state.spread_ramp = ramp
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
