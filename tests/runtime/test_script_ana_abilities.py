"""Ana's actual authored ability/ammunition gates, separate from combat-effect acceptance."""

import unittest

from ow174.game.script import nodes
from ow174.game.script.driver import BodyScript

ANA = 0x02E000000000013B
FIRE, SECONDARY, SLEEP, GRENADE, NANO, RELOAD, MELEE = 1, 2, 8, 16, 32, 0x400, 0x800


class Session:
    def __init__(self):
        self.body = BodyScript(ANA, 0xA0000101, 1000, ult_seconds=0)
        self.shots = []
        self.run(30)

    def run(self, frames, buttons=0):
        for _ in range(frames):
            self.body.command(self.body.frame + 1, buttons)
            self.shots.extend(self.body.component.shots)
            self.body.component.shots.clear()

    def instance(self, index):
        return next(i for i in self.body.component.instances.values() if i.graph.index == index)

    def ability(self, index):
        return next(s for s in self.instance(index).states.values() if isinstance(s, nodes.Ability))

    def count(self, index):
        return sum(s.instance.graph.index == index for s, _n, _t, _a in self.shots)


class AnaAuthoredGatesTests(unittest.TestCase):
    def cooling(self, graph, button):
        session = Session()
        session.run(1, button)
        session.run(95)
        self.assertEqual(session.count(graph), 1)
        ability = session.ability(graph)
        self.assertGreater(ability.remaining(), 1)
        return session, ability

    def test_early_sleep_grenade_press_keeps_original_timestamp_while_unready(self):
        for graph, button in ((0x0BDD, SLEEP), (0x0B97, GRENADE)):
            with self.subTest(graph=hex(graph)):
                session, ability = self.cooling(graph, button)
                session.run(1, button)
                original = tuple(ability.presses)
                self.assertTrue(original)
                session.run(4)
                self.assertEqual(tuple(ability.presses), original)

    def test_released_early_press_expires_and_cooldown_end_needs_a_fresh_press(self):
        for graph, button in ((0x0BDD, SLEEP), (0x0B97, GRENADE)):
            with self.subTest(graph=hex(graph)):
                session, ability = self.cooling(graph, button)
                session.run(1, button)
                session.run(1)
                for _ in range(900):
                    if ability.remaining() <= 0:
                        break
                    session.run(1)
                session.run(10)
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(session.count(graph), 1)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                session.run(1, button)
                session.run(95)
                self.assertEqual(session.count(graph), 2)

    def test_short_sleep_grenade_press_near_expiry_has_no_authored_buffer_window(self):
        for graph, button in ((0x0BDD, SLEEP), (0x0B97, GRENADE)):
            with self.subTest(graph=hex(graph)):
                session, ability = self.cooling(graph, button)
                while ability.remaining() > .08:
                    session.run(1)
                self.assertGreater(ability.remaining(), 0)
                session.run(1, button)
                self.assertEqual(session.count(graph), 1)
                session.run(95)
                self.assertEqual(session.count(graph), 1)
                self.assertEqual(ability.remaining(), 0)
                session.run(1, button)
                session.run(95)
                self.assertEqual(session.count(graph), 2)
                self.assertGreater(ability.remaining(), 0)

    def test_held_sleep_grenade_press_can_start_when_cooldown_finishes(self):
        for graph, button in ((0x0BDD, SLEEP), (0x0B97, GRENADE)):
            with self.subTest(graph=hex(graph)):
                session, ability = self.cooling(graph, button)
                for _ in range(900):
                    session.run(1, button)
                    if session.count(graph) == 2:
                        break
                self.assertEqual(session.count(graph), 2)
                # The volley precedes the authored cooldown-start/end path.
                session.run(95)
                self.assertGreater(ability.remaining(), 0)

    def test_sleep_and_grenade_cooldowns_reject_repeated_casts_then_expire(self):
        for graph, button, duration in ((0x0BDD, SLEEP, 12), (0x0B97, GRENADE, 10)):
            with self.subTest(graph=hex(graph)):
                session = Session()
                ability = session.ability(graph)
                self.assertEqual(ability.remaining(), 0)
                session.run(1, button)
                session.run(95)
                self.assertEqual(session.count(graph), 1)
                self.assertGreater(ability.remaining(), duration - 2)
                self.assertLessEqual(ability.remaining(), duration)
                # Repeated fresh edges and a long held button cannot skip the cooldown.
                for _ in range(4):
                    session.run(1, button)
                    session.run(1)
                session.run(60, button)
                self.assertEqual(session.count(graph), 1)
                self.assertGreater(ability.remaining(), 0)
                session.run(1)
                for _ in range(900):
                    if ability.remaining() <= 0:
                        break
                    session.run(1)
                self.assertEqual(ability.remaining(), 0)
                session.run(1, button)
                session.run(95)
                self.assertEqual(session.count(graph), 2)
                self.assertGreater(ability.remaining(), 0)

    def test_death_prevents_all_attacks_and_ability_casts(self):
        for button in (FIRE, SECONDARY | FIRE, SLEEP, GRENADE, NANO, MELEE):
            with self.subTest(button=button):
                session = Session()
                session.body.set_ult_charge(100000)
                session.body.set_health(0, 200)
                session.run(1, button)
                session.run(100)
                self.assertEqual(session.shots, [])
                self.assertFalse(any(s.flags & nodes.Ability.RUNNING
                                     for i in session.body.component.instances.values()
                                     for s in i.states.values() if isinstance(s, nodes.Ability)))

    def test_rifle_ammo_is_consumed_and_empty_weapon_reloads(self):
        session = Session()
        weapon = session.instance(0x0B94)
        self.assertEqual(weapon.vars[53].value(), 12)
        session.run(590, FIRE)
        self.assertEqual(session.count(0x0B94), 12)
        self.assertEqual(weapon.vars[53].value(), 0)
        session.run(120)
        self.assertEqual(weapon.vars[53].value(), 12)
        session.run(1, FIRE)
        session.run(10)
        self.assertEqual(session.count(0x0B94), 13)
        self.assertEqual(weapon.vars[53].value(), 11)

    def test_manual_reload_restores_ammo_and_rejects_fire_during_reload(self):
        session = Session()
        weapon = session.instance(0x0B94)
        session.run(1, FIRE)
        session.run(60)
        self.assertEqual(weapon.vars[53].value(), 11)
        session.run(1, RELOAD)
        session.run(25, FIRE)
        self.assertEqual(session.count(0x0B94), 1)
        session.run(120)
        self.assertEqual(weapon.vars[53].value(), 12)
        session.run(1, FIRE)
        self.assertEqual(session.count(0x0B94), 2)

    def test_uncharged_nano_rejects_activation_without_spending(self):
        session = Session()
        ability = session.ability(0x0B9E)
        session.run(1, NANO)
        session.run(60)
        self.assertEqual(ability.flags & nodes.Ability.RUNNING, 0)
        self.assertEqual(session.count(0x0B9E), 0)
        self.assertEqual(session.body.component.vars[342].value(), 0)

    def test_death_interrupts_sleep_cast_before_projectile_release(self):
        session = Session()
        session.run(1, SLEEP)
        self.assertEqual(session.count(0x0BDD), 0)
        self.assertTrue(session.ability(0x0BDD).flags & nodes.Ability.RUNNING)
        session.body.set_health(0, 200)
        session.run(100)
        self.assertEqual(session.count(0x0BDD), 0)
        self.assertFalse(session.ability(0x0BDD).flags & nodes.Ability.RUNNING)


if __name__ == "__main__":
    unittest.main()
