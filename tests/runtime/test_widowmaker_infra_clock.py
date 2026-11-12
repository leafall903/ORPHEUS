"""Committed0067 cast/effect windows on world time while owner commands pause."""

import unittest

from test_widowmaker_combat import _WidowmakerMatch

from ow174.game.commands import Command


class WidowmakerInfraClockTests(_WidowmakerMatch, unittest.TestCase):
    def cast(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        self.press(32)
        return script, next(i for i in script.component.instances.values() if i.graph.index == 0x67)

    def advance_world(self, ticks):
        self.run.frame += ticks
        self.run.match.update(self.run.frame * 0.016, self.run.frame)

    def test_paused_cast_reveals_on_time_and_late_vm_cannot_refresh_expired_window(self):
        script, instance = self.cast()
        owner_frame = script.frame
        window = script.infra_window()
        self.advance_world(20)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.advance_world(30)
        self.assertEqual(script.frame, owner_frame)
        self.assertFalse(instance.state(0).active)
        self.assertTrue(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.advance_world(1000)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.assertFalse(script.ultimate_active())
        self.a.take_commands([Command(self.run.frame, buttons=0, extra=0)])
        self.assertEqual(script.infra_window(), window)
        self.assertGreater(instance.state(0).start, window[1])
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))

    def test_late_presentation_scope_does_not_suppress_post_expiry_poison_charge(self):
        script, instance = self.cast()
        self.advance_world(1050)
        self.a.take_commands([Command(self.run.frame, buttons=0, extra=0)])
        self.assertTrue(instance.state(0).active)
        self.assertFalse(script.ultimate_active())
        self.press(16)
        self.press(0, 180)
        self.assertLess(self.health(self.b), 200)
        self.assertGreater(script.component.vars[342].value(), 0)
        self.assertFalse(script.ultimate_active())

    def test_future_cast_cannot_reveal_earlier_world(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        self.a.take_commands([Command(self.run.frame + 100, buttons=32, extra=0)])
        self.assertFalse(script.ultimate_active(at_time=round(self.run.match.combat.now * 1000)))
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.advance_world(145)
        self.assertTrue(self.run.match.combat.revealed_to(self.a, self.b.body))

    def test_world_window_uses_authored_wait_and_effect_variables(self):
        instance = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x67)
        from ow174.game.script import expr

        instance.write((expr.INSTANCE, 670), 1.2)
        instance.write((expr.INSTANCE, 567), 3.0)
        script, _ = self.cast()
        window = script.infra_window()
        self.assertEqual(window[1] - window[0], 1200)
        self.assertEqual(window[2] - window[1], 3000)
        self.advance_world(50)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.advance_world(40)
        self.assertTrue(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.advance_world(200)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
