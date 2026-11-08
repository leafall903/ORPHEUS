"""Actual enemy Flashbang inputs against Lucio's weapons, buffs and ultimate.

The imported server stun is explicitly reconstructed; these fixtures do not
prove native immunity, status replication, animation or original-client behavior.
"""

import unittest

import test_lucio_combat as fixtures
from test_game_combat import aim

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import graph


@unittest.skipUnless(graph.graph(0x0267) and graph.graph(0x073B), "requires actual Lucio status graphs")
class LucioControlTests(unittest.TestCase):
    def arena(self):
        helper = fixtures.LucioCombatTests()
        run, lucio, caster, ally = helper.scenario()
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = (0, 1, 3)
        ally.mover.position[:] = (6, 1, 0)
        lucio.body_script.ult_seconds = 0
        return helper, run, lucio, caster, ally

    def step(self, run, lucio, caster, *, buttons=0, cast=0, forward=0):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
            if player is caster:
                x, y, z = lucio.mover.position
                yaw, pitch = aim(match.combat._eye(caster), (x, y + 1, z))
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)
            elif player is lucio:
                yaw, pitch = aim(match.combat._eye(lucio), match.combat._eye(caster))
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons, forward=forward)
            else:
                command = Command(run.frame)
            player.take_commands([command])

    def stun(self, run, lucio, caster):
        self.step(run, lucio, caster, cast=16)
        for _ in range(50):
            if run.match.combat.is_stunned(lucio):
                return
            self.step(run, lucio, caster)
        self.fail("Actual Flashbang input did not stun Lucio")

    def vitals(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))

    def test_stun_rejects_all_attack_skill_and_movement_inputs_then_soundwave_recovers(self):
        helper, run, lucio, caster, _ = self.arena()
        lucio.body_script.set_ult_charge(2940)
        self.stun(run, lucio, caster)
        fight = run.match.combat
        self.assertEqual(helper.health(run, lucio), 175)
        before = helper.health(run, caster)
        position = tuple(lucio.mover.position)
        end = fight.stuns[lucio.body]
        weapon = next(i for i in lucio.body_script.component.instances.values() if i.graph.index == 0x776)
        while run.frame < end - 1:
            self.step(run, lucio, caster, buttons=1 | 2 | 8 | 16 | 32 | 0x40 | 0x800, forward=127)
            self.assertEqual(tuple(lucio.mover.position), position)
            self.assertEqual(helper.health(run, caster), before)
            self.assertFalse(lucio.body_script.component.held)
            self.assertEqual(weapon.vars[53].value(), 20)
            self.assertEqual(lucio.body_script.component.entity_var(342).value(), 2940)
        self.step(run, lucio, caster, buttons=2)
        self.assertFalse(fight.is_stunned(lucio))
        self.assertEqual(helper.health(run, caster), before - 25)

    def test_existing_crossfade_healing_continues_during_stun(self):
        helper, run, lucio, caster, ally = self.arena()
        self.vitals(run, ally).damage(100)
        self.step(run, lucio, caster, buttons=8)
        for _ in range(30):
            self.step(run, lucio, caster)
        self.stun(run, lucio, caster)
        before = helper.health(run, ally)
        for _ in range(20):
            self.step(run, lucio, caster)
            self.assertTrue(run.match.combat.is_stunned(lucio))
        self.assertAlmostEqual(helper.health(run, ally) - before, 16 * 20 * combat.TICK, places=5)

    def test_existing_amp_healing_lease_continues_during_stun(self):
        helper, run, lucio, caster, ally = self.arena()
        self.vitals(run, ally).damage(100)
        self.step(run, lucio, caster, buttons=8)
        for _ in range(30):
            self.step(run, lucio, caster)
        self.step(run, lucio, caster, buttons=16)
        for _ in range(20):
            self.step(run, lucio, caster)
        self.stun(run, lucio, caster)
        before = helper.health(run, ally)
        for _ in range(20):
            self.step(run, lucio, caster)
            self.assertTrue(run.match.combat.is_stunned(lucio))
        self.assertAlmostEqual(helper.health(run, ally) - before, 52 * 20 * combat.TICK, places=5)

    def test_stun_during_barrier_cast_prevents_late_slam_and_keeps_charge_consumed(self):
        _, run, lucio, caster, ally = self.arena()
        lucio.body_script.set_ult_charge(2940)
        self.step(run, lucio, caster, buttons=32)
        self.assertEqual(lucio.body_script.component.entity_var(342).value(), 0)
        self.stun(run, lucio, caster)
        for _ in range(100):
            self.step(run, lucio, caster)
        self.assertFalse(self.vitals(run, lucio).temporary_shields)
        self.assertFalse(self.vitals(run, ally).temporary_shields)
        self.assertEqual(lucio.body_script.component.entity_var(342).value(), 0)

    def test_amp_expires_on_its_existing_timer_while_stunned_and_cannot_restart_on_cooldown(self):
        _, run, lucio, caster, _ = self.arena()
        self.step(run, lucio, caster, buttons=8)
        for _ in range(30):
            self.step(run, lucio, caster)
        self.step(run, lucio, caster, buttons=16)
        for _ in range(160):
            self.step(run, lucio, caster)
        marker = lucio.body_script.component.entity_var(2381)
        self.assertTrue(marker.value())
        self.stun(run, lucio, caster)
        self.assertTrue(marker.value())
        end = run.match.combat.stuns[lucio.body]
        expired_under_control = False
        while run.frame < end - 1:
            self.step(run, lucio, caster, buttons=16)
            if not marker.value():
                expired_under_control = True
        self.assertTrue(expired_under_control)
        for offset in range(40):
            self.step(run, lucio, caster, buttons=16 if offset % 2 else 0)
            self.assertFalse(marker.value())

    def test_stun_after_slam_preserves_already_granted_ally_shields(self):
        _, run, lucio, caster, ally = self.arena()
        lucio.body_script.set_ult_charge(2940)
        self.step(run, lucio, caster, buttons=32)
        for _ in range(41):
            self.step(run, lucio, caster)
        self.assertTrue(self.vitals(run, ally).temporary_shields)
        self.stun(run, lucio, caster)
        for _ in range(20):
            self.step(run, lucio, caster)
        self.assertTrue(self.vitals(run, ally).temporary_shields)
        self.assertGreater(self.vitals(run, ally).total, 200)
