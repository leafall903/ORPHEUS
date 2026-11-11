"""Actual Moira beam inputs through Sigma's Grasp and Barrier services."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_sigma_barrier as barrier_fixtures
import test_sigma_combat as combat_fixtures
import test_sigma_flux_placement as placement
from test_game_combat import aim

from ow174.game import combat, moira_beams, sigma
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes
from ow174.game.script import expr, nodes


class SigmaMoiraTests(unittest.TestCase):
    def prepare(self, run, player, moira):
        position = list(moira.mover.position)
        self.assertTrue(run.match.switch_hero(moira, heroes()[moira_beams.HERO]))
        moira.mover.position = position
        run.step({})
        moira.body_script.ult_seconds = 0
        moira.body_script.set_ult_charge(0)
        return self.vitals(run, player), self.vitals(run, moira)

    @staticmethod
    def vitals(run, player):
        fight = run.match.combat
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is player))

    def scene(self, distance=6):
        run, player, moira, _ = combat_fixtures.SigmaCombatTests().arena(offset=0, distance=distance)
        target, own = self.prepare(run, player, moira)
        return run, player, moira, target, own

    @staticmethod
    def fire(run, player, moira, buttons, *, defense=0):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(moira), fight._eye(player))
        run.step({player: (0, 0, defense), moira: (yaw, pitch, buttons)})

    def start_beam(self, run, player, moira):
        script = moira.body_script
        instance = next(i for i in script.component.instances.values()
                        if i.graph.index == moira_beams.ULTIMATE)
        script.set_ult_charge(script.component.entity_var(338).value())
        self.fire(run, player, moira, 0x20)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(60):
            if 20 in instance.states and instance.states[20].active:
                break
            self.fire(run, player, moira, 0)
        self.assertTrue(instance.states[20].active)
        return instance

    @staticmethod
    def prepare_grasp_fuel(moira):
        weapon = next(i for i in moira.body_script.component.instances.values()
                      if i.graph.index == moira_beams.WEAPON)
        slot = expr.lvalue(weapon.graph.node(48).field("m_out_Var"))
        weapon.write(slot, 50)
        return weapon.find_var(*slot)

    def test_damage_grasp_bypasses_kinetic_grasp_and_heals_moira_on_contact(self):
        run, player, moira, target, own = self.scene()
        own.now[combat.HEALTH_POOL] -= 50
        fuel = self.prepare_grasp_fuel(moira)
        for frame in range(20):
            self.fire(run, player, moira, SECONDARY, defense=8 if frame == 0 else 0)
        grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertFalse(target.temporary_shields)
        self.assertAlmostEqual(target.total, 384)
        self.assertAlmostEqual(own.total, 157.68)
        self.assertEqual(fuel.value(), 100)
        for _ in range(12):
            self.fire(run, player, moira, 0)
        self.assertAlmostEqual(target.total, 384)
        self.assertAlmostEqual(own.total, 157.68)

    @unittest.skipUnless(barrier_fixtures.MODEL, "requires the external Experimental Barrier mesh")
    def test_damage_grasp_hits_experimental_barrier_without_body_damage_or_self_heal(self):
        run, player, (moira,), barrier, _ = barrier_fixtures.SigmaBarrierTests().arena()
        target, own = self.prepare(run, player, moira)
        own.now[combat.HEALTH_POOL] -= 50
        fuel = self.prepare_grasp_fuel(moira)
        for frame in range(20):
            self.fire(run, player, moira, SECONDARY, defense=8 if frame == 0 else 0)
        grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertAlmostEqual(barrier.vitals.total, 684)
        self.assertEqual(target.total, 400)
        self.assertEqual(own.total, 150)
        self.assertEqual(fuel.value(), 50)

    @unittest.skipUnless(barrier_fixtures.MODEL, "requires the external Experimental Barrier mesh")
    def test_coalescence_crosses_barrier_and_kinetic_grasp(self):
        run, player, (moira,), barrier, _ = barrier_fixtures.SigmaBarrierTests().arena()
        target, own = self.prepare(run, player, moira)
        own.now[combat.HEALTH_POOL] -= 50
        beam = self.start_beam(run, player, moira)
        before, self_before = target.total, own.total
        for frame in range(20):
            self.fire(run, player, moira, 0, defense=8 if frame == 0 else 0)
        grasp = combat_fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertFalse(target.temporary_shields)
        self.assertTrue(beam.states[20].active)
        self.assertAlmostEqual(before - target.total, 22.4)
        self.assertAlmostEqual(own.total - self_before, 16)
        self.assertEqual(barrier.vitals.total, 700)
        self.assertEqual(moira.body_script.component.entity_var(342).value(), 0)

    def test_coalescence_heals_allied_sigma(self):
        run, player, moira, target, _ = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        moira.team = player.team
        target.now[combat.HEALTH_POOL] -= 70
        for _ in range(20):
            self.fire(run, player, moira, SECONDARY)
        self.assertEqual(target.total, 330)
        self.fire(run, player, moira, 0)
        self.start_beam(run, player, moira)
        before = target.total
        for _ in range(20):
            self.fire(run, player, moira, 0)
        self.assertAlmostEqual(target.total - before, 44.8)

    def test_static_cover_and_range_bound_both_enemy_beams(self):
        for ultimate in (False, True):
            for reason, distance in (("inside", 29 if ultimate else 19),
                                     ("outside", 31 if ultimate else 21), ("wall", 6)):
                with self.subTest(ultimate=ultimate, reason=reason):
                    run, player, moira, target, _ = self.scene(distance=distance)
                    if reason == "wall":
                        combat_fixtures.SigmaCombatTests().wall(run, sideways=False, offset=3)
                    if ultimate:
                        self.start_beam(run, player, moira)
                    before = target.total
                    for _ in range(20):
                        self.fire(run, player, moira, 0 if ultimate else SECONDARY)
                    expected = (22.4 if ultimate else 16) if reason == "inside" else 0
                    self.assertAlmostEqual(before - target.total, expected)

    def test_accretion_stops_coalescence_without_refund_or_delayed_resume(self):
        run, player, moira, target, own = self.scene()
        script = moira.body_script
        self.fire(run, player, moira, 0x20)
        beam = next(i for i in script.component.instances.values() if i.graph.index == moira_beams.ULTIMATE)
        self.assertFalse(beam.states[0].flags & nodes.Ability.RUNNING)
        self.fire(run, player, moira, 0)
        self.start_beam(run, player, moira)
        fight = run.match.combat
        x, y, z = moira.mover.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        for frame in range(80):
            if fight.is_stunned(moira):
                break
            moira_yaw, moira_pitch = aim(fight._eye(moira), fight._eye(player))
            run.step({player: (yaw, pitch, 0x10 if frame == 0 else 0), moira: (moira_yaw, moira_pitch, 0)})
        self.assertTrue(fight.is_stunned(moira))
        self.assertEqual(own.total, 130)
        self.assertFalse(beam.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(beam.states[20].active)
        before = target.total
        for _ in range(70):
            self.fire(run, player, moira, 0x20)
        self.assertFalse(fight.is_stunned(moira))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(target.total, before)

    def test_lifted_moira_can_continue_coalescence_and_still_takes_the_flux_slam(self):
        run, player, moira, target, _ = self.scene()
        beam = self.start_beam(run, player, moira)
        fight = run.match.combat
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        flux_hits = []
        damage = fight._damage

        def observe(shooter, victim, amount, *args, **kwargs):
            if shooter is player and victim.owner is moira:
                flux_hits.append(amount)
            return damage(shooter, victim, amount, *args, **kwargs)

        with patch.object(fight, "_damage", side_effect=observe):
            placement.SigmaFluxPlacementTests().select(run, player, tuple(moira.mover.position))
            for _ in range(140):
                if moira.mover.airborne:
                    break
                run.step({})
            self.assertTrue(moira.mover.airborne)
            self.assertTrue(beam.states[20].active)
            self.assertEqual(flux_hits, [50])
            before = target.total
            for _ in range(20):
                self.fire(run, player, moira, 0)
            self.assertTrue(moira.mover.airborne)
            self.assertAlmostEqual(before - target.total, 22.4)
            for _ in range(320):
                if len(flux_hits) == 2:
                    break
                run.step({})
        self.assertEqual(flux_hits, [50, 100])
        self.assertFalse(moira.mover.airborne)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_coalescence_ends_on_its_own_timer_and_held_input_cannot_restart_it(self):
        run, player, moira, target, own = self.scene(distance=31)
        beam = self.start_beam(run, player, moira)
        for _ in range(550):
            self.fire(run, player, moira, 0x20)
        self.assertFalse(beam.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(beam.states[20].active)
        self.assertEqual(target.total, 400)
        self.assertEqual(moira.body_script.component.entity_var(342).value(), 0)
        own.now[combat.HEALTH_POOL] -= 50
        for _ in range(20):
            self.fire(run, player, moira, 0x20)
        self.assertEqual(own.total, 150)
