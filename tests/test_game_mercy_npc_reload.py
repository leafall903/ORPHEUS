"""Authored Trooper and Enforcer magazines on the live combat clock."""

import unittest
from unittest.mock import Mock, patch

import test_game_mercy_npc as npc_tests
from test_game_mercy_blaster_critical import equip, shot

from ow174.game import npc, npc_control


class MercyNpcReloadTests(unittest.TestCase):
    def encounter(self, name, graph_id, distance):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        definition = next(d for d in fixture.catalog.values() if d.name == name)
        run, mercy, bot, _ = fixture.spawn(definition, 1)
        mercy.client.bot_stream = Mock(return_value=None)
        bot.position = (0, 1, distance)
        fixture.tick(run, mercy, bot, 3, buttons=0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        # Keep the target alive while the actual weapon consumes its full magazine.
        health.invulnerable_until = fight.now + 60
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == graph_id)
        self.assertEqual(npc_control.start(run.match, mercy), bot.entity)
        return run, mercy, bot, health, fixture, weapon

    def verify_cycle(self, name, graph_id, rounds, damage, *, lazy_ammo=False):
        run, mercy, bot, health, fixture, weapon = self.encounter(
            name, graph_id, 2 if name == "Enforcer" else 5,
        )
        fight = run.match.combat

        def value(index):
            var = weapon.vars.get(index)
            return var.value() if var is not None else None

        self.assertEqual(value(53), None if lazy_ammo else rounds)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(1300):
                fixture.tick(run, mercy, bot, 1, buttons=0)
                if shoot.called:
                    self.assertEqual(value(53), rounds - shoot.call_count)
                if value(53) == 0:
                    break
            self.assertEqual(shoot.call_count, rounds)
            self.assertEqual({c.args[2].instance.graph.index for c in shoot.call_args_list}, {graph_id})
            for _ in range(20):
                if value(51):
                    break
                fixture.tick(run, mercy, bot, 1, buttons=0)
            self.assertTrue(value(51))
            fixture.tick(run, mercy, bot, 12, buttons=0)
            self.assertTrue(bot.attack_input)
            self.assertTrue(value(51))
            self.assertEqual(shoot.call_count, rounds)
            # Idle world time still runs the authored reload timer.
            npc_control.stop(run.match)
            start = run.frame
            for _ in range(150):
                fixture.tick(run, mercy, bot, 1, buttons=0)
                if not value(51):
                    break
                self.assertIn(value(53), (0, rounds))
                self.assertEqual(shoot.call_count, rounds)
            self.assertFalse(value(51))
            self.assertGreater(run.frame - start, 10)
            self.assertEqual(value(53), rounds)
            self.assertEqual(shoot.call_count, rounds)
            self.assertEqual(health.total, 200)

            health.invulnerable_until = 0
            npc_control.start(run.match, mercy)
            for _ in range(160):
                fixture.tick(run, mercy, bot, 1, buttons=0)
                if shoot.call_count > rounds:
                    break
            self.assertEqual(shoot.call_count, rounds + 1)
            self.assertEqual(value(53), rounds - 1)
            for _ in range(12):
                fixture.tick(run, mercy, bot, 1, buttons=0)
                if health.total < 200:
                    if name == "Enforcer":
                        fixture.tick(run, mercy, bot, 2, buttons=0)
                    break
            self.assertEqual(shoot.call_count, rounds + 1)
            self.assertEqual(health.total, 200 - damage)

    def test_trooper_held_fire_empties_thirty_and_reload_restores_twenty_damage(self):
        self.verify_cycle("Trooper", 0x102E, 30, 20, lazy_ammo=True)

    def test_enforcer_empties_four_and_automatic_reload_restores_fifty_damage(self):
        self.verify_cycle("Enforcer", 0x13EB, 4, 50)

    def test_trooper_manual_reload_refills_a_partial_magazine_while_idle(self):
        run, mercy, bot, health, fixture, weapon = self.encounter("Trooper", 0x102E, 5)
        match = run.match
        for _ in range(120):
            fixture.tick(run, mercy, bot, 1, buttons=0)
            if 53 in weapon.vars and weapon.vars[53].value() == 29:
                break
        self.assertEqual(weapon.vars[53].value(), 29)
        npc_control.stop(match)
        fixture.tick(run, mercy, bot, 1, buttons=0)
        self.assertEqual(npc_control.reload(match, mercy), bot.entity)
        fixture.tick(run, mercy, bot, 1, buttons=0)
        self.assertTrue(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 29)
        for _ in range(150):
            fixture.tick(run, mercy, bot, 1, buttons=0)
            if not weapon.vars[51].value():
                break
        self.assertFalse(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 30)
        self.assertFalse(bot.attack_input)
        self.assertEqual(health.total, 200)

    def test_trooper_blaster_hit_preserves_reload_but_death_prevents_fire(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, mercy, bot, health, fixture, weapon = self.encounter("Trooper", 0x102E, 5)
                match, fight = run.match, run.match.combat
                npc_control.stop(match)
                equip(run, mercy)
                target = next(t for t in fight._targets() if t.owner is bot)
                attacker_health = fight.vitals_of(target)
                remaining = 15 if lethal else 35
                fight._damage(mercy, target, attacker_health.total - remaining, False, None, run.frame)
                self.assertEqual(attacker_health.total, remaining)

                health.invulnerable_until = 0
                self.assertEqual(npc_control.start(match, mercy), bot.entity)
                for _ in range(120):
                    fixture.tick(run, mercy, bot, 1, buttons=0)
                    if health.total < 200:
                        break
                self.assertEqual(health.total, 180)
                self.assertEqual(weapon.vars[53].value(), 29)
                npc_control.stop(match)
                fixture.tick(run, mercy, bot, 1, buttons=0)
                self.assertEqual(npc_control.reload(match, mercy), bot.entity)
                fixture.tick(run, mercy, bot, 1, buttons=0)
                self.assertTrue(weapon.vars[51].value())

                shot(run, mercy, bot.position, 0.8, frames=10)
                self.assertEqual(attacker_health.total, 0 if lethal else 15)
                self.assertEqual(bot.dead, lethal)
                self.assertEqual(health.total, 180)
                self.assertEqual(weapon.vars[53].value(), 29)
                if lethal:
                    self.assertEqual(attacker_health.killer, mercy.body)
                    mercy.client.queue_bot_death.assert_called_once()
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                        fixture.tick(run, mercy, bot, 150, buttons=0)
                        shoot.assert_not_called()
                    self.assertFalse(bot.attack_input)
                    self.assertTrue(bot.body_script.component.owner_dead)
                    self.assertEqual(health.total, 200)  # Mercy's passive recovered the first hit.
                    self.assertFalse(weapon.vars[51].value())
                    self.assertEqual(weapon.vars[53].value(), 30)

                    definition = next(d for d in fixture.catalog.values() if d.name == "Trooper")
                    replacement = npc.spawn(match, definition, bot.position, team=bot.team)
                    self.assertNotEqual(replacement.entity, bot.entity)
                    self.assertTrue(bot.dead)
                    self.assertIsNot(replacement.body_script.component, weapon.component)
                    new_weapon = next(i for i in replacement.body_script.component.instances.values()
                                      if i.graph.index == weapon.graph.index)
                    self.assertNotIn(53, new_weapon.vars)
                    self.assertEqual(npc_control.start(match, mercy), replacement.entity)
                    for _ in range(120):
                        fixture.tick(run, mercy, replacement, 1, buttons=0)
                        if health.total < 200:
                            break
                    self.assertEqual(health.total, 180)
                    self.assertEqual(new_weapon.vars[53].value(), 29)
                    self.assertEqual(weapon.vars[53].value(), 30)
                else:
                    self.assertTrue(weapon.vars[51].value())
                    for _ in range(150):
                        fixture.tick(run, mercy, bot, 1, buttons=0)
                        if not weapon.vars[51].value():
                            break
                    self.assertFalse(weapon.vars[51].value())
                    self.assertEqual(weapon.vars[53].value(), 30)
                    self.assertEqual(npc_control.start(match, mercy), bot.entity)
                    for _ in range(120):
                        fixture.tick(run, mercy, bot, 1, buttons=0)
                        if weapon.vars[53].value() == 29:
                            break
                    self.assertEqual(weapon.vars[53].value(), 29)
                    for _ in range(30):
                        fixture.tick(run, mercy, bot, 1, buttons=0)
                        if health.total < 180:
                            break
                    self.assertEqual(health.total, 160)
