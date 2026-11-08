"""Actual enemy Flashbang against Roadhog's running skills; control reconstructed."""

import unittest
from unittest.mock import patch

import test_mccree_flashbang as flashbang_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ROADHOG = 0x02E0000000000040
MCCREE = 0x02E0000000000042


class RoadhogStunTests(unittest.TestCase):
    step = flashbang_tests.FlashbangTests.step
    cast = flashbang_tests.FlashbangTests.cast

    def session(self):
        run, caster, hog = flashbang_tests.FlashbangTests().arena(distance=3)
        position = tuple(hog.mover.position)
        run.match.switch_hero(hog, heroes()[ROADHOG])
        hog.mover.position[:] = position
        # Retain the hostile caster through a full channel without retail-HP claims.
        run.match.combat.vitals[caster.body] = combat.Vitals(caster.body, 20000)
        targets = {t.owner: t for t in run.match.combat._targets()}
        return run, caster, hog, targets

    def ability(self, hog, index):
        return next(s for i in hog.body_script.component.instances.values()
                    if i.graph.index == index for s in i.states.values()
                    if isinstance(s, nodes.Ability))

    def test_flashbang_interrupts_whole_hog_without_refund_or_delayed_restart(self):
        run, caster, hog, targets = self.session()
        script = hog.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(run, caster, hog, target_buttons=32)
        for _ in range(80):
            self.step(run, caster, hog)
        ultimate, = script.ultimates
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        pool = run.match.combat.vitals_of(targets[caster])
        self.assertLess(pool.total, 20000)
        self.cast(run, caster, hog)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        stopped = pool.total
        deadline = run.match.combat.stuns[hog.body]
        position = tuple(hog.mover.position)
        own = run.match.combat.vitals_of(targets[hog]).total
        while run.frame < deadline - 1:
            self.step(run, caster, hog, target_buttons=32 | 1 | 2 | 8 | 16 | 0x800, forward=127)
            self.assertEqual(tuple(hog.mover.position), position)
            self.assertEqual(pool.total, stopped)
            self.assertEqual(run.match.combat.vitals_of(targets[hog]).total, own)
            self.assertFalse(script.component.held)
            self.assertFalse(self.ability(hog, 0x0240).flags & nodes.Ability.RUNNING)
        for _ in range(30):
            self.step(run, caster, hog)
        self.assertEqual(pool.total, stopped)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.step(run, caster, hog, target_buttons=1)
        for _ in range(20):
            self.step(run, caster, hog)
        self.assertLess(pool.total, stopped)

    def test_flashbang_interrupts_breather_heal_and_reduction_keeps_cooldown(self):
        run, caster, hog, targets = self.session()
        fight = run.match.combat
        pool = fight.vitals_of(targets[hog])
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        self.step(run, caster, hog, target_buttons=16)
        for _ in range(50):
            self.step(run, caster, hog)
        self.assertGreater(pool.total, 200)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        self.cast(run, caster, hog)
        stopped = pool.total
        ability = self.ability(hog, 0x0240)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        heal = next(s for i in hog.body_script.component.instances.values()
                    if i.graph.index == 0x0240 for s in i.states.values() if s.node.pos == 18)
        self.assertEqual(heal.counter, 1)
        for _ in range(150):
            self.step(run, caster, hog, target_buttons=16)
        self.assertEqual(pool.total, stopped)
        self.assertEqual(heal.counter, 1)
        while ability.remaining() > 0:
            self.step(run, caster, hog)
        self.step(run, caster, hog, target_buttons=16)
        for _ in range(180):
            self.step(run, caster, hog)
        self.assertEqual(heal.counter, 2)
        self.assertGreater(pool.total, stopped)

    def hook_scene(self):
        run = Run(KANEZAKA_DM, ("Roadhog", "McCree", "Victim"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, caster, victim = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        match.switch_hero(caster, heroes()[MCCREE])
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + 2.5, y, z)
        victim.mover.position[:] = (x, y, z + 18)
        return run, hog, caster, victim

    def hook_step(self, run, hog, caster, victim, *, hook=False, flash=False):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
        hog.take_commands([Command(run.frame, buttons=8 if hook else 0)])
        yaw, pitch = aim(match.combat._eye(caster), match.combat._eye(hog))
        caster.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=16 if flash else 0)])
        victim.take_commands([Command(run.frame)])

    def test_flashbang_cancels_hook_flight_and_active_pull(self):
        for caught in (False, True):
            with self.subTest(caught=caught):
                run, hog, caster, victim = self.hook_scene()
                fight = run.match.combat
                self.hook_step(run, hog, caster, victim, hook=True)
                for _ in range(60):
                    self.hook_step(run, hog, caster, victim)
                    if (fight.hooks.controlled(victim.body) if caught else hog.body in fight.hooks.flights):
                        break
                self.assertIn(hog.body, fight.hooks.flights)
                self.assertEqual(fight.hooks.controlled(victim.body), caught)
                observed = []
                apply_effect = fight._hit_effect

                def record_effect(target, effect, fight=fight, hog=hog,
                                  observed=observed, apply_effect=apply_effect):
                    if target.entity == hog.body and effect == combat.FLASHBANG_EFFECT:
                        flight = fight.hooks.flights.get(hog.body)
                        observed.append(None if flight is None else
                                        (flight.target is not None, flight.arrived_at))
                    return apply_effect(target, effect)

                with patch.object(fight, "_hit_effect", side_effect=record_effect):
                    self.hook_step(run, hog, caster, victim, flash=True)
                    for _ in range(40):
                        self.hook_step(run, hog, caster, victim)
                        if fight.is_stunned(hog):
                            break
                self.assertEqual(observed, [(caught, None)])
                self.assertTrue(fight.is_stunned(hog))
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(victim.body))
                position = tuple(victim.mover.position)
                for _ in range(80):
                    self.hook_step(run, hog, caster, victim)
                self.assertEqual(tuple(victim.mover.position), position)
                self.assertGreater(self.ability(hog, 0x01C8).remaining(), 0)


if __name__ == "__main__":
    unittest.main()
