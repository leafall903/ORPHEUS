"""Enemy control inputs against Reaper's immunity and cast lifecycle."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_mccree_flashbang as flashbang_tests
from test_game_combat import aim

from ow174.game import (
    combat,
    defenses,
    dva_matrix,
    graviton,
    lucio_barrier,
    mercy_beam,
    meteor_strike,
    orisa_halt,
    orisa_supercharger,
    pools,
    reaper,
    reinhardt_charge,
    rocket_punch,
    seismic_slam,
    sigma_flux,
    sigma_grasp,
    stats,
    winston_jump,
    winston_primal,
    zarya_barriers,
)
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.graph import LUCIO_STATUS_SHA256


class ReaperControlTests(unittest.TestCase):
    def start_winston_jump(self, run, winston, *, pitch=-16384):
        script = winston.body_script
        script.set_ult_charge(0)
        run.step({winston: (0, pitch, 8)})
        return next(i for i in script.component.instances.values() if i.graph.index == 0x010A)

    @staticmethod
    def replay_jump_landing(run, owner, launch, ticks):
        fight = run.match.combat
        contacts = []
        impact = winston_jump.impact

        def observe(*args, **kwargs):
            contacts.append((fight.tick - launch, reaper.intangible(owner)))
            return impact(*args, **kwargs)

        with patch.object(winston_jump, "impact", side_effect=observe):
            for tick in ticks:
                run.frame = tick
                run.match.update(tick * combat.TICK, tick, frames=False)
        return contacts

    def test_actual_winston_jump_launch_hits_reaper_and_refuses_early_retry(self):
        run, owner, winston, fight = self.primal_scene(distance=3)
        jump = self.start_winston_jump(run, winston, pitch=0)
        self.assertGreater(winston.mover.velocity[1], 12)
        self.assertGreater(winston.mover.velocity[2], 27)
        self.assertGreater(jump.states[19].remaining(), 5.9)
        self.assertEqual(fight.vitals[owner.body].total, 249)
        self.assertEqual(fight.npc_impulses[owner.body][1], (0, 2, 7))
        self.assertEqual(winston.body_script.component.entity_var(ULT_CHARGE).value(), 1)
        self.assertFalse(fight.is_stunned(owner))
        cast = jump.states[26]
        for button in (0, 8, 0):
            run.step({winston: (0, 0, button)})
        self.assertEqual(cast.counter, 1)
        for _ in range(110):
            if winston.body not in fight.winston_jumps:
                break
            run.step({})
        self.assertNotIn(winston.body, fight.winston_jumps)
        self.assertFalse(jump.states[2].active)
        self.assertGreater(winston.mover.position[2], 15)
        self.assertEqual(fight.vitals[owner.body].total, 249)
        for _ in range(380):
            if jump.states[19].remaining() <= 0:
                break
            run.step({})
        self.assertEqual(cast.counter, 1)
        self.assertEqual(jump.states[19].remaining(), 0)
        run.step({winston: (0, -16384, 8)})
        self.assertEqual(cast.counter, 2)
        self.assertEqual(winston.mover.velocity[1], 30)

    def test_actual_winston_jump_landing_matches_idle_and_skipped_world(self):
        outcomes = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, winston, fight = self.primal_scene(distance=4.5)
                jump = self.start_winston_jump(run, winston)
                self.assertEqual(winston.mover.velocity[1], 30)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                for delta in ((235,) if skip else (1,) * 235):
                    run.frame += delta
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertNotIn(winston.body, fight.winston_jumps)
                self.assertFalse(jump.states[2].active)
                self.assertAlmostEqual(winston.mover.position[1], 1, delta=0.01)
                self.assertGreater(owner.mover.position[2], 4.53)
                self.assertFalse(fight.is_stunned(owner))
                health = fight.vitals[owner.body].total
                self.assertLess(health, 245)
                self.assertGreater(health, 235)
                charge = winston.body_script.component.entity_var(ULT_CHARGE).value()
                self.assertAlmostEqual(charge, 250 - health, places=5)
                outcomes.append((health, tuple(owner.mover.position), tuple(winston.mover.position),
                                 fight.history[owner.body][-1], fight.history[winston.body][-1]))
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_winston_jump_respects_wraith_at_landing(self):
        run, owner, winston, fight = self.primal_scene(distance=4.5)
        self.start_winston_jump(run, winston)
        for _ in range(105):
            run.step({})
        run.step({owner: (0, 0, 8)})
        for _ in range(75):
            if winston.body not in fight.winston_jumps:
                break
            run.step({})
        self.assertNotIn(winston.body, fight.winston_jumps)
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertEqual(winston.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_winston_jump_distinguishes_last_wraith_frame_from_expiry(self):
        for delay in (38, 39):
            with self.subTest(delay=delay):
                run, owner, winston, fight = self.primal_scene(distance=4.5)
                run.step({owner: (0, 0, 8)})
                self.assertTrue(reaper.intangible(owner))
                for _ in range(delay - 1):
                    run.step({})
                self.start_winston_jump(run, winston)
                run.match.players.reverse()
                launch = run.frame
                observed = self.replay_jump_landing(run, owner, launch, (launch + 195,))
                self.assertEqual(observed, [(150, delay == 38)])
                health = fight.vitals[owner.body].total
                if delay == 38:
                    self.assertEqual(health, 250)
                    self.assertEqual(owner.mover.position[2], 4.5)
                else:
                    self.assertLess(health, 250)
                    self.assertGreater(owner.mover.position[2], 4.53)

    def test_actual_winston_jump_landing_moves_reaper_without_ending_blossom(self):
        run, owner, winston, fight = self.primal_scene(distance=4.5)
        self.start_winston_jump(run, winston)
        for _ in range(135):
            run.step({})
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(45):
            if winston.body not in fight.winston_jumps:
                break
            run.step({})
        self.assertNotIn(winston.body, fight.winston_jumps)
        self.assertIn(owner.body, fight.npc_impulses)
        self.assertLess(fight.vitals[owner.body].total, 250)
        self.assertFalse(fight.is_stunned(owner))
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))

    def test_actual_winston_jump_landing_resolves_shadow_immunity_boundary(self):
        for case, delay in (("before", 134), ("immune", 72), ("after", 0)):
            with self.subTest(case=case):
                run, owner, winston, fight = self.primal_scene(distance=4.5)
                self.start_winston_jump(run, winston)
                for _ in range(delay):
                    run.step({})
                record = self.start_shadow(run, owner, angles=(16384, 7400))
                for _ in range(170):
                    if winston.body not in fight.winston_jumps:
                        break
                    run.step({})
                self.assertNotIn(winston.body, fight.winston_jumps)
                self.assertEqual(reaper.intangible(owner), case == "immune")
                self.assertEqual(record["moved"], case == "after")
                health = fight.vitals[owner.body].total
                if case == "immune":
                    self.assertEqual(health, 250)
                    self.assertNotIn(owner.body, fight.npc_impulses)
                else:
                    self.assertLess(health, 250)
                    self.assertIn(owner.body, fight.npc_impulses)
                self.assertFalse(fight.is_stunned(owner))

    def test_actual_winston_jump_shadow_edges_agree_across_roster_order_and_world_gaps(self):
        for edge, delay in (("entry", 83), ("exit", 52)):
            outcomes = {}
            for reverse in (False, True):
                for skip in (False, True):
                    with self.subTest(edge=edge, reverse=reverse, skip=skip):
                        run, owner, winston, fight = self.primal_scene(distance=4.5)
                        self.start_winston_jump(run, winston)
                        launch = run.frame
                        for _ in range(delay):
                            run.step({})
                        self.start_shadow(run, owner, angles=(16384, 7400))
                        if reverse:
                            run.match.players.reverse()
                        end = launch + 205
                        ticks = (end,) if skip else range(run.frame + 1, end + 1)
                        contacts = self.replay_jump_landing(run, owner, launch, ticks)
                        self.assertEqual(contacts, [(150, edge == "entry")])
                        self.assertNotIn(winston.body, fight.winston_jumps)
                        self.assertFalse(reaper.intangible(owner))
                        health = fight.vitals[owner.body].total
                        if edge == "entry":
                            self.assertEqual(health, 250)
                        else:
                            self.assertLess(health, 250)
                        outcomes[reverse, skip] = (health, tuple(owner.mover.position),
                                                   tuple(winston.mover.position))
            self.assertEqual(len(outcomes), 4)
            self.assertEqual(len(set(outcomes.values())), 1)

    def test_actual_winston_jump_landing_pushes_reaper_at_immortality_floor(self):
        run, owner, winston, fight = self.primal_scene(distance=4.5, extra=("Baptiste",))
        bap = run.players[2]
        self.assertTrue(run.match.switch_hero(bap, find_hero("Baptiste")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = bap.team = 0
        winston.team = 1
        bap.mover.position[:] = (3.6, 1, 4.5)
        bap.mover.body = None
        heading = aim(fight._eye(bap), (3.6, 1, 4))
        run.step({bap: (*heading, 16)})
        for _ in range(90):
            if bap.body in fight.baptiste_fields.devices:
                break
            run.step({})
        device = fight.baptiste_fields.devices[bap.body]
        target = next(t for t in fight._targets() if t.owner is owner)
        health = fight.vitals_of(target)
        floor = fight.baptiste_fields.health_floor(target, health)
        self.assertEqual(floor, 25)
        health.damage(health.total - floor)
        self.start_winston_jump(run, winston)
        run.frame += 195
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertNotIn(winston.body, fight.winston_jumps)
        self.assertEqual(health.total, 25)
        self.assertGreater(owner.mover.position[2], 4.53)
        self.assertEqual(device.health, 150)
        self.assertFalse(winston.client.messages(combat.HIT))
        self.assertEqual(winston.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_simultaneous_winston_jump_landings_attribute_reaper_death_once(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run, owner, first, fight = self.primal_scene(distance=4.5, extra=("Second Winston",))
                second = run.players[2]
                self.assertTrue(run.match.switch_hero(second, find_hero("Winston")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, first.team, second.team = 0, 1, 1
                second.mover.position[:] = (0.25, 1, 0)
                second.mover.body = None
                for actor in (first, second):
                    actor.body_script.ult_seconds = 0
                    actor.body_script.set_ult_charge(0)
                health = fight.vitals[owner.body]
                health.damage(health.total - 5)
                run.step({first: (0, -16384, 8), second: (0, -16384, 8)})
                launch = run.frame
                self.assertEqual(len(fight.winston_jumps), 2)
                if reverse:
                    run.match.players.reverse()
                run.frame += 195
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(fight.winston_jumps)
                self.assertFalse(fight.winston_landing_pending)
                self.assertTrue(health.dead)
                self.assertEqual(health.death_tick, launch + 150)
                self.assertEqual(health.killer, first.body)
                self.assertEqual(first.body_script.component.entity_var(ULT_CHARGE).value(), 5)
                self.assertEqual(second.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_winston_jump_landing_filters_range_wall_and_faction(self):
        for case in ("range", "wall", "ally"):
            with self.subTest(case=case):
                run, owner, winston, fight = self.primal_scene(
                    distance=6 if case == "range" else 4.5, wall=2 if case == "wall" else None,
                )
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = winston.team = 0
                self.start_winston_jump(run, winston)
                run.frame += 185
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertNotIn(winston.body, fight.winston_jumps)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_winston_jump_spends_projected_barrier_before_body_force(self):
        run, owner, winston, fight = self.primal_scene(distance=4.5, extra=("Zarya",))
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, zarya.team, winston.team = 0, 0, 1
        zarya.mover.position[:] = (4.5, 1, 4.5)
        zarya.mover.body = None
        self.start_winston_jump(run, winston)
        for _ in range(135):
            run.step({})
        facing = aim(fight._eye(zarya), fight._eye(owner))
        run.step({zarya: (*facing, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        for _ in range(45):
            if winston.body not in fight.winston_jumps:
                break
            run.step({})
        self.assertNotIn(winston.body, fight.winston_jumps)
        self.assertLess(bubble.health, 195)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertEqual(winston.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_winston_jump_landing_hits_after_idle_projection_expires(self):
        run, owner, winston, fight = self.primal_scene(distance=4.5, extra=("Zarya",))
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, zarya.team, winston.team = 0, 0, 1
        zarya.mover.position[:] = (4.5, 1, 4.5)
        zarya.mover.body = None
        facing = aim(fight._eye(zarya), fight._eye(owner))
        run.step({zarya: (*facing, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.start_winston_jump(run, winston)
        run.frame += 195
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertNotIn(winston.body, fight.winston_jumps)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        self.assertIsNone(zarya.body_script.world_frame_anchor)
        self.assertEqual(bubble.health, 200)
        self.assertLess(fight.vitals[owner.body].total, 250)
        self.assertGreater(owner.mover.position[2], 4.53)

    def test_actual_flashbang_cancels_winston_jump_but_preserves_fall(self):
        run, owner, winston, fight = self.primal_scene(distance=4.5, extra=("McCree",))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, caster.team, winston.team = 0, 0, 1
        caster.mover.position[:] = (1.6, 1, 0)
        caster.mover.body = None
        self.start_winston_jump(run, winston)
        for _ in range(3):
            run.step({})
        self.assertTrue(winston.mover.airborne)
        # Lead the rising body so the actual projectile detonates in flight.
        pitch_target = winston.mover.position[1] + 4.5
        facing = aim(fight._eye(caster), (0, pitch_target, 0))
        run.step({caster: (*facing, 16)})
        for _ in range(24):
            if fight.is_stunned(winston):
                break
            run.step({})
        self.assertTrue(fight.is_stunned(winston))
        self.assertNotIn(winston.body, fight.winston_jumps)
        self.assertIn(winston.body, fight.impulse_landings)
        run.frame += 190
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertLess(winston.mover.velocity[1], 0)
        self.assertIn(winston.body, fight.impulse_landings)
        run.frame += 45
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(winston.mover.position[1], 1, delta=0.01)
        self.assertNotIn(winston.body, fight.impulse_landings)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_reaper_shell_kills_airborne_winston_and_prevents_impact(self):
        run, owner, winston, fight = self.primal_scene(distance=4.5)
        self.start_winston_jump(run, winston)
        for _ in range(124):
            run.step({})
        self.assertTrue(winston.mover.airborne)
        self.assertIn(winston.body, fight.winston_jumps)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
        health.damage(health.total - 1)
        for _ in range(6):
            point = tuple(winston.mover.position)
            yaw, pitch = aim(fight._eye(owner), (point[0], point[1] + 1, point[2]))
            run.step({owner: (yaw, pitch, 1)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, owner.body)
        self.assertNotIn(winston.body, fight.winston_jumps)
        run.frame += 85
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_primal_winston_jump_recasts_in_air_on_short_cooldown(self):
        run, _, winston, fight = self.primal_scene(distance=11)
        script = winston.body_script
        script.set_ult_charge(1540)
        run.step({winston: (0, 0, 32)})
        self.assertIsNotNone(winston_primal.pool(script.component))
        for _ in range(35):
            run.step({})
        jump = self.start_winston_jump(run, winston)
        cooldown = jump.states[19].remaining()
        self.assertGreater(cooldown, 1.9)
        self.assertLessEqual(cooldown, 2)
        cast = jump.states[26]
        for _ in range(127):
            run.step({})
        self.assertTrue(winston.mover.airborne)
        self.assertEqual(cast.counter, 1)
        run.step({winston: (0, 0, 8)})
        self.assertEqual(cast.counter, 2)
        self.assertGreater(winston.mover.velocity[2], 27)
        self.assertTrue(jump.states[2].active)
        self.assertEqual(fight.winston_jumps[winston.body][2], run.frame)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_tesla_damages_reaper_once_per_posed_frame_and_charges(self):
        run, owner, winston, fight = self.primal_scene(distance=3)
        script = winston.body_script
        script.set_ult_charge(0)
        weapon = next(i for i in script.component.instances.values()
                      if i.graph.index == winston_primal.WEAPON)
        health = fight.vitals[owner.body]
        for _ in range(30):
            run.step({winston: (0, 0, 1)})
            if health.total < 250:
                break
        self.assertAlmostEqual(health.total, 249.04)
        charge = script.component.entity_var(ULT_CHARGE)
        self.assertAlmostEqual(charge.value(), 0.96, places=5)
        winston.take_commands([Command(run.frame, buttons=1), Command(run.frame - 1, buttons=1)])
        self.assertAlmostEqual(health.total, 249.04)
        self.assertAlmostEqual(charge.value(), 0.96, places=5)
        for _ in range(18):
            run.step({winston: (0, 0, 1)})
        self.assertLess(weapon.vars[53].value(), 100)
        self.assertAlmostEqual(charge.value(), 250 - health.total, places=5)

    def test_actual_tesla_resumes_after_primal_and_grants_damage_charge(self):
        run, owner, winston, fight = self.primal_scene()
        script = winston.body_script
        script.set_ult_charge(1540)
        run.step({winston: (0, 0, 32)})
        self.assertIsNotNone(winston_primal.pool(script.component))
        run.frame += 617
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        charge = script.component.entity_var(ULT_CHARGE)
        health = fight.vitals[owner.body]
        for _ in range(30):
            self.assertEqual(health.total, 250)
            self.assertEqual(charge.value(), 0)
            run.step({winston: (0, 0, 1)})
            if winston_primal.pool(script.component) is None:
                break
        self.assertIsNone(winston_primal.pool(script.component))
        for _ in range(50):
            if health.total < 250:
                break
            run.step({winston: (0, 0, 1)})
        self.assertAlmostEqual(health.total, 249.04)
        self.assertAlmostEqual(charge.value(), 0.96, places=5)
        self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_tesla_respects_wraith_while_spending_ammo(self):
        run, owner, winston, fight = self.primal_scene(distance=3)
        script = winston.body_script
        script.set_ult_charge(0)
        run.step({owner: (0, 0, 8), winston: (0, 0, 1)})
        for _ in range(35):
            run.step({winston: (0, 0, 1)})
        self.assertTrue(reaper.intangible(owner))
        weapon = next(i for i in script.component.instances.values()
                      if i.graph.index == winston_primal.WEAPON)
        self.assertLess(weapon.vars[53].value(), 100)
        health = fight.vitals[owner.body]
        charge = script.component.entity_var(ULT_CHARGE)
        self.assertEqual(health.total, 250)
        self.assertEqual(charge.value(), 0)
        for _ in range(220):
            run.step({winston: (0, 0, 1)})
            if health.total < 250:
                break
        self.assertFalse(reaper.intangible(owner))
        self.assertAlmostEqual(health.total, 249.04)
        self.assertAlmostEqual(charge.value(), 0.96, places=5)

    def test_actual_tesla_filters_range_cone_cover_and_allies(self):
        for case, distance, wall, heading in (
            ("near range edge", 8.15, None, 0),
            ("range", 8.8, None, 0),
            ("cone", 3, None, 16384),
            ("cover", 3, 1.5, 0),
            ("ally", 3, None, 0),
        ):
            with self.subTest(case=case):
                run, owner, winston, fight = self.primal_scene(distance=distance, wall=wall)
                script = winston.body_script
                script.set_ult_charge(0)
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = winston.team = 0
                for _ in range(30):
                    run.step({winston: (heading, 0, 1)})
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == winston_primal.WEAPON)
                self.assertLess(weapon.vars[53].value(), 100)
                health = fight.vitals[owner.body]
                charge = script.component.entity_var(ULT_CHARGE).value()
                if case == "near range edge":
                    self.assertLess(health.total, 250)
                    self.assertAlmostEqual(charge, 250 - health.total, delta=0.00002)
                else:
                    self.assertEqual(health.total, 250)
                    self.assertEqual(charge, 0)

    def test_actual_tesla_resolves_shadow_immunity_boundary(self):
        run, owner, winston, fight = self.primal_scene(distance=3)
        script = winston.body_script
        script.set_ult_charge(0)
        record = self.start_shadow(run, owner)
        origin = tuple(owner.mover.position)
        health = fight.vitals[owner.body]
        charge = script.component.entity_var(ULT_CHARGE)
        for _ in range(120):
            run.step({winston: (0, 0, 1)})
            if reaper.intangible(owner):
                break
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(tuple(owner.mover.position), origin)
        self.assertLess(health.total, 250)
        protected_health, protected_charge = health.total, charge.value()
        for _ in range(24):
            run.step({winston: (0, 0, 1)})
            self.assertTrue(reaper.intangible(owner))
            self.assertEqual(health.total, protected_health)
            self.assertEqual(charge.value(), protected_charge)
        for _ in range(65):
            if record["moved"] and not reaper.intangible(owner):
                break
            run.step({})
        self.assertTrue(record["moved"])
        self.assertLess(math.dist(owner.mover.position, record["target"]), 0.002)
        heading, _ = aim(winston.mover.position, owner.mover.position)
        for _ in range(75):
            run.step({winston: (heading, 0, 1)})
            if health.total < protected_health:
                break
        self.assertAlmostEqual(health.total, protected_health - 0.96)
        self.assertAlmostEqual(charge.value() - protected_charge, 0.96, places=5)

    def test_actual_tesla_spends_enemy_dome_once_and_resumes_after_break(self):
        run, owner, winston, fight = self.primal_scene(distance=3, extra=("Dome",))
        defender = run.players[2]
        self.assertTrue(run.match.switch_hero(defender, find_hero("Winston")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, defender.team, winston.team = 0, 0, 1
        defender.mover.position[:] = (0.2, 1, 6)
        defender.mover.body = None
        run.step({defender: (32768, 0, 16)})
        for _ in range(45):
            if fight.winston_bubbles.actors:
                break
            run.step({})
        dome, = fight.winston_bubbles.actors.values()
        script = winston.body_script
        script.set_ult_charge(0)
        charge = script.component.entity_var(ULT_CHARGE)
        reaper_health = fight.vitals[owner.body]
        tank_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
        for _ in range(30):
            run.step({winston: (0, 0, 1)})
            if dome.health < 700:
                break
        self.assertAlmostEqual(dome.health, 699.04, delta=0.00005)
        self.assertEqual((reaper_health.total, tank_health.total, charge.value()), (250, 500, 0))
        dome.health = 0.2
        run.step({winston: (0, 0, 1)})
        self.assertEqual(dome.health, 0)
        self.assertEqual((reaper_health.total, tank_health.total, charge.value()), (250, 500, 0))
        run.step({winston: (0, 0, 1)})
        self.assertAlmostEqual(reaper_health.total, 249.04)
        self.assertAlmostEqual(tank_health.total, 500 - 0.96 * 0.8)
        self.assertAlmostEqual(charge.value(), 0.96 * 1.8, places=5)

    def test_actual_tesla_spends_ice_pillar_before_reaper_body(self):
        run, owner, winston, fight = self.primal_scene(distance=3, extra=("Mei",))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, find_hero("Mei")))
        caster.mover.position[:] = (0, 1, 5.5)
        caster.mover.body = None
        angles = aim(fight._eye(caster), (0, 1, 1.5))
        for button in (16, 0, 1, 0):
            run.step({caster: (*angles, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        pillar = min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        script = winston.body_script
        script.set_ult_charge(0)
        health = fight.vitals[owner.body]
        charge = script.component.entity_var(ULT_CHARGE)
        for _ in range(30):
            run.step({winston: (0, 0, 1)})
            if pillar.health < 400:
                break
        self.assertAlmostEqual(pillar.health, 399.04)
        self.assertEqual((health.total, charge.value()), (250, 0))
        run.step({caster: (*angles, 16)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(30):
            run.step({winston: (0, 0, 1)})
            if health.total < 250:
                break
        self.assertAlmostEqual(health.total, 249.04)

    def test_actual_tesla_hits_through_active_deflect_and_grasp(self):
        for hero, button in (("Genji", 16), ("Sigma", 8)):
            with self.subTest(hero=hero):
                run, owner, winston, fight = self.primal_scene(distance=3, extra=(hero,))
                defender = run.players[2]
                self.assertTrue(run.match.switch_hero(defender, find_hero(hero)))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, defender.team, winston.team = 0, 0, 1
                defender.mover.position[:] = (0.24, 1, 1.2)
                defender.mover.body = None
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                tank = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
                run.step({defender: (32768, 0, button)})
                for _ in range(30):
                    run.step({winston: (0, 0, 1), defender: (32768, 0, 0)})
                    if fight.vitals[owner.body].total < 250:
                        break
                self.assertAlmostEqual(fight.vitals[owner.body].total, 249.04)
                self.assertAlmostEqual(health.total, health.maximum - 0.96)
                self.assertEqual(tank.total, 500)
                if hero == "Genji":
                    self.assertIsNotNone(fight._deflect_direction(defender, (0, 0, 1)))
                else:
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertTrue(grasp.states[1].active)
                    self.assertFalse(grasp.vars[21312].value())

    def test_actual_tesla_damages_reaper_without_ending_death_blossom(self):
        run, owner, winston, fight = self.primal_scene(distance=3)
        script = winston.body_script
        script.set_ult_charge(0)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32), winston: (0, 0, 1)})
        own_health = fight.vitals[owner.body]
        tank_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
        for _ in range(45):
            run.step({winston: (0, 0, 1)})
            if tank_health.total < 500:
                break
        self.assertLess(own_health.total, 250)
        self.assertLess(tank_health.total, 500)
        self.assertFalse(fight.is_stunned(owner))
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(), 250 - own_health.total,
                               delta=0.0001)

    def test_actual_tesla_kill_credits_remaining_health_and_obeys_charge_cap(self):
        for initial in (0, 1539.8):
            with self.subTest(initial=initial):
                run, owner, winston, fight = self.primal_scene(distance=3)
                script = winston.body_script
                script.set_ult_charge(initial)
                health = fight.vitals[owner.body]
                health.damage(249.5)
                for _ in range(30):
                    run.step({winston: (0, 0, 1)})
                    if health.dead:
                        break
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, winston.body)
                self.assertEqual(health.death_tick, run.frame)
                charge = script.component.entity_var(ULT_CHARGE)
                expected = min(1540, initial + 0.5)
                self.assertEqual(charge.value(), expected)
                for _ in range(12):
                    run.step({winston: (0, 0, 1)})
                self.assertEqual(health.total, 0)
                self.assertEqual(charge.value(), expected)

    def test_actual_tesla_manual_reload_blocks_damage_until_weapon_unlocks(self):
        run, owner, winston, fight = self.primal_scene(distance=3)
        script = winston.body_script
        script.set_ult_charge(0)
        for _ in range(24):
            run.step({winston: (16384, 0, 1)})
        run.step({})
        run.step({winston: (0, 0, 0x400)})
        weapon = next(i for i in script.component.instances.values()
                      if i.graph.index == winston_primal.WEAPON)
        ammo, gate = weapon.vars[53], weapon.vars[51]
        health = fight.vitals[owner.body]
        self.assertLess(ammo.value(), 100)
        self.assertTrue(gate.value())
        for _ in range(120):
            run.step({winston: (0, 0, 1)})
            if not gate.value():
                break
            self.assertEqual(health.total, 250)
        self.assertFalse(gate.value())
        for _ in range(25):
            if health.total < 250:
                break
            run.step({winston: (0, 0, 1)})
        self.assertAlmostEqual(health.total, 249.04)
        self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(), 0.96, places=5)

    def primal_scene(self, *, distance=1.5, wall=None, extra=()):
        run, owner, winston, fight = self.charge_scene(
            hero="Winston", names=("Reaper", "Winston", *extra), wall=wall,
        )
        owner.mover.position[:] = (0, 1, distance)
        owner.mover.body = None
        winston.body_script.ult_seconds = 0
        return run, owner, winston, fight

    def test_actual_primal_punch_damages_and_moves_reaper(self):
        run, owner, winston, fight = self.primal_scene()
        script = winston.body_script
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
        self.assertEqual(health.maximum, 500)
        run.step({winston: (0, 0, 32)})
        self.assertIsNone(winston_primal.pool(script.component))
        run.step({})
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1540)
        script.set_ult_charge(cost)
        health.damage(50)
        run.step({winston: (0, 0, 32)})
        self.assertIsNotNone(winston_primal.pool(script.component))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(health.total, 1000)
        for _ in range(70):
            run.step({winston: (0, 0, 1)})
            if owner.body in fight.npc_impulses:
                break
        self.assertEqual(fight.vitals[owner.body].total, 210)
        _, (x, y, z) = fight.npc_impulses[owner.body]
        self.assertEqual((x, y, z), (0, 7, 25))
        before = tuple(owner.mover.position)
        for _ in range(42):
            run.step({})
        self.assertGreater(owner.mover.position[2], before[2] + 0.5)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(fight.vitals[owner.body].total, 210)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_primal_punch_respects_wraith_then_hits_after_expiry(self):
        run, owner, winston, fight = self.primal_scene()
        winston.body_script.set_ult_charge(1540)
        run.step({winston: (0, 0, 32), owner: (0, 0, 8)})
        with patch.object(winston_primal, "hit", wraps=winston_primal.hit) as attempted:
            for _ in range(70):
                run.step({winston: (0, 0, 1)})
                if attempted.called:
                    break
        self.assertEqual(attempted.call_count, 1)
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)
        for _ in range(230):
            run.step({winston: (0, 0, 0)})
            if not reaper.intangible(owner):
                break
        self.assertFalse(reaper.intangible(owner))
        for _ in range(90):
            run.step({winston: (0, 0, 1)})
            if owner.body in fight.npc_impulses:
                break
        self.assertEqual(fight.vitals[owner.body].total, 210)
        self.assertIn(owner.body, fight.npc_impulses)

    def test_actual_primal_punch_pushes_reaper_during_blossom(self):
        run, owner, winston, fight = self.primal_scene()
        script = winston.body_script
        script.set_ult_charge(1540)
        owner.body_script.set_ult_charge(2100)
        run.step({winston: (0, 0, 32), owner: (0, 0, 32)})
        for _ in range(70):
            run.step({winston: (0, 0, 1)})
            if owner.body in fight.npc_impulses:
                break
        self.assertEqual(fight.vitals[owner.body].total, 210)
        self.assertIn(owner.body, fight.npc_impulses)
        self.assertFalse(fight.is_stunned(owner))
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
        self.assertLess(health.total, 1000)

    def test_actual_primal_punch_resolves_shadow_immunity_boundary(self):
        for immune in (False, True):
            with self.subTest(immune=immune):
                run, owner, winston, fight = self.primal_scene()
                script = winston.body_script
                script.set_ult_charge(1540)
                run.step({winston: (0, 0, 32)})
                for _ in range(36):
                    run.step({winston: (0, 0, 0)})
                # Use a nearby destination to keep both endpoints in fist range.
                run.step({owner: (16384, 7400, 16)})
                run.step({owner: (16384, 7400, 0)})
                record = fight.reaper_shadows[owner.body]
                destination = record["target"]
                self.assertIsNotNone(destination)
                run.step({owner: (16384, 7400, 1)})
                self.assertTrue(self.shadow(owner).state(32).active)
                if immune:
                    for _ in range(110):
                        if reaper.intangible(owner):
                            break
                        run.step({})
                    self.assertTrue(reaper.intangible(owner))
                with patch.object(winston_primal, "hit", wraps=winston_primal.hit) as attempted:
                    for _ in range(30):
                        run.step({winston: (0, 0, 1)})
                        if attempted.called:
                            break
                self.assertEqual(attempted.call_count, 1)
                self.assertEqual(reaper.intangible(owner), immune)
                health = fight.vitals[owner.body]
                self.assertEqual(health.total, 250 if immune else 210)
                self.assertEqual(owner.body in fight.npc_impulses, not immune)
                self.assertFalse(fight.is_stunned(owner))
                for _ in range(160):
                    if record["moved"] and not reaper.intangible(owner):
                        break
                    run.step({winston: (0, 0, 0)})
                self.assertTrue(record["moved"])
                self.assertFalse(reaper.intangible(owner))
                self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                if immune:
                    heading, _ = aim(winston.mover.position, destination)
                    for _ in range(90):
                        run.step({winston: (heading, 0, 1)})
                        if owner.body in fight.npc_impulses:
                            break
                    self.assertEqual(health.total, 210)
                    self.assertIn(owner.body, fight.npc_impulses)

    def test_actual_primal_punch_respects_range_and_static_cover(self):
        for distance, wall in ((9, None), (2.5, 1.25)):
            with self.subTest(distance=distance, wall=wall):
                run, owner, winston, fight = self.primal_scene(distance=distance, wall=wall)
                winston.body_script.set_ult_charge(1540)
                run.step({winston: (0, 0, 32)})
                with patch.object(winston_primal, "hit", wraps=winston_primal.hit) as attempted:
                    for _ in range(70):
                        run.step({winston: (0, 0, 1)})
                        if attempted.called:
                            break
                self.assertEqual(attempted.call_count, 1)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_primal_bonus_expires_without_owner_input(self):
        run, _, winston, fight = self.primal_scene()
        script = winston.body_script
        script.ult_seconds = 60
        script.set_ult_charge(1540)
        run.step({winston: (0, 0, 32)})
        health = fight.vitals[winston.body]
        self.assertEqual((health.total, health.maximum), (1000, 1000))
        charge = script.component.entity_var(ULT_CHARGE)
        self.assertEqual(charge.value(), 0)
        health.damage(100)
        injured = health.total
        start = fight.tick
        fight.update((start + 620) * combat.TICK, start + 620)
        self.assertIsNotNone(winston_primal.pool(script.component))
        self.assertEqual((health.total, health.maximum), (injured, 1000))
        self.assertEqual(charge.value(), 0)
        fight.update((start + 650) * combat.TICK, start + 650)
        self.assertIsNone(winston_primal.pool(script.component))
        # Removing the temporary health pool preserves the earlier armor loss.
        self.assertEqual((health.total, health.maximum), (injured - 500, 500))
        self.assertNotIn(winston.body, fight.primal_bonus)
        self.assertIsNone(script.world_frame_anchor)
        self.assertGreater(charge.value(), 0)

    def test_actual_primal_expiry_clears_pending_fist(self):
        for start_offset in (590, 617):
            with self.subTest(start_offset=start_offset):
                run, owner, winston, fight = self.primal_scene()
                script = winston.body_script
                script.set_ult_charge(1540)
                run.step({winston: (0, 0, 32)})
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == winston_primal.WEAPON)
                run.frame += start_offset
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                run.step({winston: (0, 0, 1)})
                windup, = (state for state in weapon.states.values()
                           if state.active and state.node.pos in winston_primal.WINDUPS)
                for _ in range(50):
                    run.step({winston: (0, 0, 1)})
                    if winston_primal.pool(script.component) is None:
                        break
                self.assertIsNone(winston_primal.pool(script.component))
                self.assertFalse(windup.active)
                for _ in range(18):
                    run.step({winston: (0, 0, 1)})
                    self.assertFalse(any((state := weapon.states.get(index)) is not None and state.active
                                         for index, _ in winston_primal.FISTS))
                self.assertEqual(fight.vitals[owner.body].total, 210 if start_offset == 590 else 250)

    def test_actual_primal_punch_spends_projected_barrier_before_force(self):
        run, owner, winston, fight = self.primal_scene(extra=("Zarya",))
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, zarya.team, winston.team = 0, 0, 1
        zarya.mover.position[:] = (4, 1, 1.5)
        zarya.mover.body = None
        winston.body_script.set_ult_charge(1540)
        run.step({winston: (0, 0, 32)})
        run.step({zarya: (*aim(fight._eye(zarya), fight._eye(owner)), 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        for _ in range(70):
            run.step({winston: (0, 0, 1)})
            if bubble.health < 200:
                break
        self.assertEqual(bubble.health, 160)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_primal_cleaves_reaper_and_resolves_melee_defense(self):
        for hero, facing in (("Genji", 32768), ("Genji", 0), ("Sigma", 32768)):
            with self.subTest(hero=hero, facing=facing):
                run, owner, winston, fight = self.primal_scene(extra=(hero,))
                defender = run.players[2]
                self.assertTrue(run.match.switch_hero(defender, find_hero(hero)))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, defender.team, winston.team = 0, 0, 1
                defender.mover.position[:] = (0.24, 1, 1.2)
                defender.mover.body = None
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                maximum = health.maximum
                self.start_primal_windup(run, winston)
                button = 16 if hero == "Genji" else 8
                run.step({winston: (0, 0, 1), defender: (facing, 0, button)})
                for _ in range(24):
                    run.step({winston: (0, 0, 1), defender: (facing, 0, 0)})
                    if owner.body in fight.npc_impulses:
                        break
                self.assertEqual(fight.vitals[owner.body].total, 210)
                self.assertIn(owner.body, fight.npc_impulses)
                source = fight.vitals[winston.body]
                self.assertEqual(source.total, source.maximum)
                self.assertNotIn(winston.body, fight.npc_impulses)
                if hero == "Genji":
                    self.assertIsNotNone(fight._deflect_direction(defender, combat.aim(facing ^ 32768, 0)))
                    blocked = facing == 32768
                    self.assertEqual(health.total, maximum if blocked else maximum - 40)
                    self.assertEqual(defender.body in fight.npc_impulses, not blocked)
                else:
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertTrue(grasp.states[1].active)
                    self.assertFalse(grasp.vars[21312].value())
                    self.assertEqual(health.total, maximum - 40)
                    self.assertIn(defender.body, fight.npc_impulses)

    def start_primal_windup(self, run, winston):
        script = winston.body_script
        script.set_ult_charge(1540)
        run.step({winston: (0, 0, 32)})
        weapon = next(i for i in script.component.instances.values()
                      if i.graph.index == winston_primal.WEAPON)
        for _ in range(50):
            run.step({winston: (0, 0, 1)})
            for state in weapon.states.values():
                if state.active and state.node.pos in winston_primal.WINDUPS:
                    return state
        self.fail("actual Primal fist did not enter windup")

    def test_actual_reaper_shell_kills_primal_winston_during_fist_windup(self):
        run, owner, winston, fight = self.primal_scene()
        self.start_primal_windup(run, winston)
        health = fight.vitals[winston.body]
        health.damage(health.total - 5)
        run.step({owner: (*aim(fight._eye(owner), fight._eye(winston)), 1), winston: (0, 0, 1)})
        self.assertTrue(health.dead)
        self.assertEqual(health.maximum, 500)
        self.assertNotIn(winston.body, fight.primal_bonus)
        for _ in range(35):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_flashbang_aborts_primal_windup_without_ending_rage(self):
        run, owner, winston, fight = self.primal_scene(extra=("McCree",))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, caster.team, winston.team = 0, 0, 1
        caster.mover.position[:] = (1.6, 1, 0)
        caster.mover.body = None
        windup = self.start_primal_windup(run, winston)
        aim_stun = aim(fight._eye(caster), (0, 2, 0))
        with patch.object(winston_primal, "hit", wraps=winston_primal.hit) as attempted:
            run.step({caster: (*aim_stun, 16), winston: (0, 0, 1)})
            for _ in range(16):
                if fight.is_stunned(winston):
                    break
                run.step({winston: (0, 0, 1)})
            self.assertTrue(fight.is_stunned(winston))
            self.assertFalse(windup.active)
            self.assertIsNotNone(winston_primal.pool(winston.body_script.component))
            while fight.is_stunned(winston):
                run.step({winston: (0, 0, 0)})
            attempted.assert_not_called()
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)
        for _ in range(80):
            run.step({winston: (0, 0, 1)})
            if owner.body in fight.npc_impulses:
                break
        self.assertEqual(fight.vitals[owner.body].total, 210)
        self.assertIn(owner.body, fight.npc_impulses)

    def test_actual_primal_windup_cannot_bank_contact_without_owner_input(self):
        run, owner, winston, fight = self.primal_scene()
        windup = self.start_primal_windup(run, winston)
        for advance in (24, 18):
            run.frame += advance
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(windup.active)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)
        run.step({winston: (0, 0, 1)})
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(90):
            run.step({winston: (0, 0, 1)})
            if owner.body in fight.npc_impulses:
                break
        self.assertEqual(fight.vitals[owner.body].total, 210)
        self.assertIn(owner.body, fight.npc_impulses)

    def whole_hog_scene(self, *, distance=1.0, wall=None, extra=()):
        run, owner, hog, fight = self.charge_scene(
            hero="Roadhog", names=("Reaper", "Roadhog", *extra), wall=wall,
        )
        owner.mover.position[:] = (0, 1, distance)
        owner.mover.body = None
        hog.body_script.ult_seconds = 0
        return run, owner, hog, fight

    @staticmethod
    def aim_whole_hog(fight, hog, owner):
        x, y, z = owner.mover.position
        return aim(fight._eye(hog), (x, y + 0.9, z))

    def test_actual_whole_hog_uses_sixteen_pellets_and_one_force(self):
        run, owner, hog, fight = self.whole_hog_scene()
        health = fight.vitals[owner.body]
        script = hog.body_script
        ultimate, = script.ultimates
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2240)
        angles = self.aim_whole_hog(fight, hog, owner)
        run.step({hog: (*angles, 32)})
        self.assertFalse(ultimate.flags & ultimate.RUNNING)
        self.assertEqual(health.total, 250)
        run.step({})
        script.set_ult_charge(cost)
        run.step({hog: (*angles, 32)})
        self.assertTrue(ultimate.flags & ultimate.RUNNING)
        for _ in range(95):
            if health.total < 250:
                break
            run.step({hog: (*angles, 0)})
        hits = hog.client.messages(combat.HIT)
        self.assertEqual(len(hits), 16)
        critical = sum(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL) for hit in hits)
        first_health = health.total
        self.assertEqual(first_health, 250 - (16 + critical) * 7)
        _, (x, y, z) = fight.npc_impulses[owner.body]
        self.assertAlmostEqual(math.hypot(x, z), 10)
        self.assertAlmostEqual(y, 1.8, places=5)
        before = tuple(owner.mover.position)
        for _ in range(440):
            run.step({hog: (0, -16384, 0)})
        self.assertGreater(owner.mover.position[2], before[2])
        self.assertFalse(ultimate.flags & ultimate.RUNNING)
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertEqual(health.total, first_health)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_whole_hog_cannot_damage_or_move_wraith(self):
        run, owner, hog, fight = self.whole_hog_scene()
        health = fight.vitals[owner.body]
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        run.step({owner: (0, 0, 8), hog: (*angles, 32)})
        before = tuple(owner.mover.position)
        for _ in range(115):
            run.step({hog: (*angles, 0)})
            self.assertTrue(reaper.intangible(owner))
            self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertEqual(health.total, 250)
        self.assertEqual(tuple(owner.mover.position), before)
        for _ in range(125):
            run.step({hog: (*angles, 0)})
            if health.total < 250:
                break
        self.assertFalse(reaper.intangible(owner))
        self.assertLess(health.total, 250)
        self.assertIn(owner.body, fight.npc_impulses)

    def test_actual_whole_hog_wraith_clears_live_force(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=2)
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (*angles, 32)})
        for _ in range(95):
            if owner.body in fight.npc_impulses:
                break
            run.step({hog: (*angles, 0)})
        self.assertIn(owner.body, fight.npc_impulses)
        run.step({owner: (0, 0, 8), hog: (*angles, 0)})
        self.assertTrue(reaper.intangible(owner))
        health = fight.vitals[owner.body].total
        after = tuple(owner.mover.position)
        for _ in range(30):
            run.step({hog: (*angles, 0)})
            self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertEqual(fight.vitals[owner.body].total, health)
        self.assertEqual(tuple(owner.mover.position)[::2], after[::2])

    def test_actual_whole_hog_reaper_lands_without_owner_input(self):
        settled = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, hog, fight = self.whole_hog_scene(distance=2)
                angles = self.aim_whole_hog(fight, hog, owner)
                hog.body_script.set_ult_charge(2240)
                for frame in range(85):
                    run.step({hog: (*angles, 32 if frame == 0 else 0)})
                    if owner.body in fight.npc_impulses:
                        break
                self.assertIn(owner.body, fight.npc_impulses)
                before = tuple(owner.mover.position)
                frame = owner.commands.last_frame
                health = fight.vitals[owner.body].total
                for _ in range(1 if skip else 140):
                    run.frame += 140 if skip else 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(owner.commands.last_frame, frame)
                self.assertGreater(owner.mover.position[2], before[2] + 0.2)
                self.assertAlmostEqual(owner.mover.position[1], before[1], delta=0.01)
                self.assertFalse(owner.mover.airborne)
                self.assertEqual(owner.mover.velocity, (0, 0, 0))
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertNotIn(owner.body, fight.impulse_landings)
                self.assertEqual(fight.vitals[owner.body].total, health)
                self.assertEqual(fight.history[owner.body][-1][1], tuple(owner.mover.position))
                grounded = tuple(owner.mover.position)
                self.assertEqual(owner.body_script.frame, run.frame - 1)
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                owner.take_commands([Command(frame + 10, forward=127)])
                self.assertEqual(tuple(owner.mover.position), grounded)
                owner.take_commands([Command(run.frame, forward=127)])
                self.assertGreater(owner.mover.position[2], grounded[2])
                settled.append((grounded, tuple(owner.mover.position)))
        self.assertEqual(len(settled), 2)
        self.assertEqual(settled[0], settled[1])

    def test_actual_whole_hog_wraith_releases_force_and_lands_without_input(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=2)
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        for frame in range(85):
            run.step({hog: (*angles, 32 if frame == 0 else 0)})
            if owner.body in fight.npc_impulses:
                break
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertGreater(owner.mover.position[1], 1)
        self.assertIn(owner.body, fight.impulse_landings)
        position = tuple(owner.mover.position)
        health = fight.vitals[owner.body].total
        run.frame += 110
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(reaper.intangible(owner))
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertNotIn(owner.body, fight.impulse_landings)
        self.assertFalse(owner.mover.airborne)
        self.assertAlmostEqual(owner.mover.position[1], 1, delta=0.01)
        self.assertEqual(tuple(owner.mover.position)[::2], position[::2])
        self.assertEqual(fight.vitals[owner.body].total, health)

    def test_actual_whole_hog_lands_without_input_while_flashbang_is_active(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=2.5, extra=("McCree",))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = caster.team = 1
        owner.team = 0
        caster.mover.position[:] = (2.5, 1, 2.5)
        caster.mover.body = None
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (0, -16384, 32)})
        self.throw(run, caster, owner)
        deadline = fight.stuns[owner.body]
        for _ in range(32):
            run.step({hog: (*self.aim_whole_hog(fight, hog, owner), 0)})
            if owner.body in fight.impulse_landings:
                break
        self.assertIn(owner.body, fight.impulse_landings)
        self.assertGreater(owner.mover.position[1], 1)
        self.assertGreater(deadline - run.frame, 12)
        health = fight.vitals[owner.body].total
        frame = owner.commands.last_frame
        run.frame = deadline - 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(owner.commands.last_frame, frame)
        self.assertEqual(fight.stuns[owner.body], deadline)
        self.assertTrue(fight.is_stunned(owner))
        self.assertNotIn(owner.body, fight.impulse_landings)
        self.assertFalse(owner.mover.airborne)
        self.assertAlmostEqual(owner.mover.position[1], 1, delta=0.01)
        self.assertEqual(fight.vitals[owner.body].total, health)

    def test_actual_whole_hog_replacement_retires_pending_landing(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=2)
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        for frame in range(85):
            run.step({hog: (*angles, 32 if frame == 0 else 0)})
            if owner.body in fight.npc_impulses:
                break
        run.step({})
        retired = owner.body
        self.assertIn(retired, fight.impulse_landings)
        self.assertTrue(run.match.switch_hero(owner, find_hero("Soldier: 76")))
        self.assertNotEqual(owner.body, retired)
        self.assertNotIn(retired, fight.impulse_landings)
        self.assertNotIn(retired, fight.npc_impulses)
        position = tuple(owner.mover.position)
        run.frame += 140
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(tuple(owner.mover.position), position)
        self.assertNotIn(owner.body, fight.impulse_landings)
        self.assertNotIn(owner.body, fight.npc_impulses)

    def test_actual_whole_hog_filters_cover_miss_and_allies(self):
        for case in ("cover", "miss", "ally"):
            with self.subTest(case=case):
                run, owner, hog, fight = self.whole_hog_scene(distance=6, wall=3 if case == "cover" else None)
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = hog.team
                angles = (0, -16384) if case == "miss" else self.aim_whole_hog(fight, hog, owner)
                before = tuple(owner.mover.position)
                hog.body_script.set_ult_charge(2240)
                for index in range(92):
                    run.step({hog: (*angles, 32 if index == 0 else 0)})
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertEqual(tuple(owner.mover.position), before)

    def test_actual_whole_hog_uses_authored_maximum_range(self):
        for inside in (True, False):
            with self.subTest(inside=inside):
                run, owner, hog, fight = self.whole_hog_scene(distance=99.8 if inside else 101)
                run.match.collision_world = flat_world(1)
                angles = self.aim_whole_hog(fight, hog, owner)
                hog.body_script.set_ult_charge(2240)
                health = fight.vitals[owner.body]
                for index in range(90):
                    run.step({hog: (*angles, 32 if index == 0 else 0)})
                    if health.total < 250:
                        break
                self.assertEqual(health.total < 250, inside)
                self.assertEqual(owner.body in fight.npc_impulses, inside)

    def test_actual_whole_hog_respects_shadow_immunity_and_hits_after_landing(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=5)
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (0, -16384, 32)})
        destination = self.start_shadow(run, owner)["target"]
        for _ in range(100):
            if reaper.intangible(owner):
                break
            run.step({hog: (0, -16384, 0)})
        self.assertTrue(reaper.intangible(owner))
        health = fight.vitals[owner.body]
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as attempted:
            for _ in range(24):
                angles = self.aim_whole_hog(fight, hog, owner)
                run.step({hog: (*angles, 0)})
                self.assertTrue(reaper.intangible(owner))
                self.assertEqual(health.total, 250)
                self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertTrue(any(call.kwargs.get("volley") is not None for call in attempted.call_args_list))
        for _ in range(40):
            if not reaper.intangible(owner):
                break
            run.step({hog: (0, -16384, 0)})
        self.assertLess(math.dist(owner.mover.position, destination), 0.002)
        for _ in range(40):
            run.step({hog: (*self.aim_whole_hog(fight, hog, owner), 0)})
            if health.total < 250:
                break
        self.assertLess(health.total, 250)
        self.assertIn(owner.body, fight.npc_impulses)

    def test_actual_whole_hog_spends_projected_barrier_before_body_force(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=2, extra=("Zarya",))
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, zarya.team, hog.team = 0, 0, 1
        zarya.mover.position[:] = (4, 1, 2)
        zarya.mover.body = None
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (*angles, 32)})
        run.step({zarya: (*aim(fight._eye(zarya), fight._eye(owner)), 16), hog: (*angles, 0)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        before = tuple(owner.mover.position)
        for _ in range(85):
            if bubble.health < 200:
                break
            run.step({hog: (*angles, 0)})
        self.assertLess(bubble.health, 200)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertEqual(tuple(owner.mover.position), before)

    def whole_hog_lamp_scene(self, *, extra=()):
        run, owner, hog, fight = self.whole_hog_scene(distance=2, extra=("Baptiste", *extra))
        bap = run.players[2]
        self.assertTrue(run.match.switch_hero(bap, find_hero("Baptiste")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = bap.team = 0
        hog.team = 1
        bap.mover.position[:] = (3, 1, 2.5)
        bap.mover.body = None
        heading = aim(fight._eye(bap), (3, 1, 2))
        run.step({bap: (*heading, 16)})
        for _ in range(90):
            run.step({})
            if bap.body in fight.baptiste_fields.devices:
                break
        device = fight.baptiste_fields.devices[bap.body]
        target = next(t for t in fight._targets() if t.owner is owner)
        health = fight.vitals_of(target)
        floor = fight.baptiste_fields.health_floor(target, health)
        self.assertEqual(floor, 25)
        health.damage(health.total - floor)
        return run, owner, hog, fight, device, health

    def test_actual_whole_hog_pushes_at_the_immortality_floor_then_breaks_the_field(self):
        run, owner, hog, fight, device, health = self.whole_hog_lamp_scene()
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        for frame in range(80):
            run.step({hog: (*angles, 32 if frame == 0 else 0)})
            if owner.body in fight.npc_impulses:
                break
        self.assertEqual(health.total, 25)
        self.assertIn(owner.body, fight.npc_impulses)
        self.assertEqual(device.health, 150)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertEqual(hog.stats.value(combat.stats.HERO_DAMAGE), 0)
        before = owner.mover.position[2]
        device_aim = aim(fight._eye(hog), device.centre())
        for _ in range(240):
            run.step({hog: (*device_aim, 0)})
            if device.health == 0:
                break
        self.assertGreater(owner.mover.position[2], before)
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.body, fight.npc_impulses)
        self.assertEqual(health.total, 25)
        for _ in range(65):
            run.step({hog: (*self.aim_whole_hog(fight, hog, owner), 0)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, hog.body)

    def test_actual_whole_hog_breaks_projection_then_pushes_at_the_immortality_floor(self):
        run, owner, hog, fight, device, health = self.whole_hog_lamp_scene(extra=("Zarya",))
        zarya = run.players[3]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        zarya.team = owner.team
        zarya.mover.position[:] = (-4, 1, 2)
        zarya.mover.body = None
        for _ in range(12):
            run.step({})
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        target = next(t for t in fight._targets() if t.owner is owner)
        a, b = target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
        projection = aim(fight._eye(zarya), centre)
        run.step({hog: (*angles, 32)})
        run.step({hog: (*angles, 0), zarya: (*projection, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        for _ in range(80):
            run.step({hog: (*angles, 0)})
            if bubble.health < 200:
                break
        self.assertGreater(bubble.health, 0)
        self.assertLess(bubble.health, 200)
        self.assertNotIn(owner.body, fight.npc_impulses)
        for _ in range(100):
            run.step({hog: (*angles, 0)})
            if owner.body in fight.npc_impulses:
                break
        self.assertEqual(bubble.health, 0)
        self.assertIn(owner.body, fight.npc_impulses)
        self.assertEqual(health.total, 25)
        self.assertEqual(device.health, 150)

    def test_actual_whole_hog_resolves_genji_deflect_and_sigma_grasp(self):
        for hero, button in (("Genji", 16), ("Sigma", 8)):
            with self.subTest(hero=hero):
                run, owner, hog, fight = self.whole_hog_scene(distance=4, extra=(hero,))
                defender = run.players[2]
                self.assertTrue(run.match.switch_hero(defender, find_hero(hero)))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = defender.team = 0
                hog.team = 1
                defender.mover.position[:] = (0, 1, 0.9 if hero == "Genji" else 2)
                defender.mover.body = None
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                source = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                angles = aim(fight._eye(hog), fight._eye(defender))
                facing = aim(fight._eye(defender), fight._eye(hog))
                hog.body_script.set_ult_charge(2240)
                run.step({hog: (*angles, 32), defender: (*facing, button)})
                grasp = next((i for i in defender.body_script.component.instances.values()
                              if i.graph.index == sigma_grasp.GRASP), None)
                for _ in range(95):
                    if (source.total < source.maximum
                            or (grasp is not None and grasp.vars.get(21312) is not None
                                and grasp.vars[21312].value() > 0)):
                        break
                    run.step({hog: (*angles, 0), defender: (*facing, 0)})
                self.assertEqual(own.total, own.maximum)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertNotIn(defender.body, fight.npc_impulses)
                self.assertNotIn(owner.body, fight.npc_impulses)
                if hero == "Genji":
                    self.assertLess(source.total, source.maximum)
                    self.assertLess(fight.npc_impulses[hog.body][1][2], 0)
                    self.assertIsNone(hog.body_script.world_frame_anchor)
                    frame = hog.commands.last_frame
                    run.frame += 125
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertNotIn(hog.body, fight.npc_impulses)
                    self.assertNotIn(hog.body, fight.impulse_landings)
                    self.assertAlmostEqual(hog.mover.position[1], 1, delta=0.01)
                    self.assertIsNone(hog.body_script.world_frame_anchor)
                    self.assertEqual(hog.commands.last_frame, frame)
                    grounded = tuple(hog.mover.position)
                    hog.take_commands([Command(frame + 2, forward=127)])
                    self.assertEqual(tuple(hog.mover.position), grounded)
                    hog.take_commands([Command(run.frame, forward=127)])
                    self.assertGreater(hog.mover.position[2], grounded[2])
                else:
                    self.assertEqual(source.total, source.maximum)
                    self.assertGreater(grasp.vars[21312].value(), 0)

    def test_actual_whole_hog_matrix_blocks_damage_and_force(self):
        if not dva_matrix.eligible(0x0400000000000405):
            self.skipTest("requires the pinned Whole Hog projectile tag")
        run, owner, hog, fight = self.whole_hog_scene(distance=4, extra=("Dva",))
        dva = run.players[2]
        self.assertTrue(run.match.switch_hero(dva, replace(find_hero("dva"), body=pools.DVA_MECH)))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = dva.team = 0
        hog.team = 1
        dva.mover.position[:] = (0, 1, 2)
        dva.mover.body = None
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is dva))
        for _ in range(75):
            run.step({})
        angles = aim(fight._eye(hog), fight._eye(dva))
        facing = aim(fight._eye(dva), fight._eye(hog))
        for _ in range(12):
            run.step({dva: (*facing, 2)})
        hog.body_script.set_ult_charge(2240)
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as attempted:
            for frame in range(85):
                run.step({hog: (*angles, 32 if frame == 0 else 0), dva: (*facing, 2)})
                if attempted.called:
                    break
        self.assertEqual(attempted.call_count, 16)
        matrix = next(i for i in dva.body_script.component.instances.values()
                      if i.graph.index == dva_matrix.GRAPH)
        self.assertTrue(matrix.states[dva_matrix.STATE].active)
        self.assertEqual(health.total, health.maximum)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(dva.body, fight.npc_impulses)
        self.assertNotIn(owner.body, fight.npc_impulses)
        for _ in range(45):
            run.step({hog: (*angles, 0), dva: (*facing, 0)})
            if health.total < health.maximum:
                break
        self.assertLess(health.total, health.maximum)
        self.assertIn(dva.body, fight.npc_impulses)

    def test_actual_whole_hog_moves_reaper_without_canceling_blossom(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=3)
        health = fight.vitals[owner.body]
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        before = enemy.total
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (*angles, 32)})
        owner.body_script.set_ult_charge(2100)
        run.step({hog: (*angles, 0), owner: (0, 0, 32)})
        start = tuple(owner.mover.position)
        for _ in range(90):
            run.step({hog: (*angles, 0)})
            if owner.mover.position[2] > start[2] + 0.5:
                break
        self.assertGreater(owner.mover.position[2], start[2] + 0.5)
        self.assertLess(health.total, 250)
        self.assertLessEqual(enemy.total, before - 30)
        self.assertFalse(fight.is_stunned(owner))
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_reaper_shell_ends_whole_hog(self):
        run, owner, hog, fight = self.whole_hog_scene(distance=2)
        source = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        source.damage(source.total - 30)
        target = fight.vitals[owner.body]
        angles = self.aim_whole_hog(fight, hog, owner)
        hog.body_script.set_ult_charge(2240)
        for frame in range(85):
            run.step({hog: (*angles, 32 if frame == 0 else 0)})
            if target.total < 250:
                break
        self.assertLess(target.total, 250)
        self.assertTrue(hog.body_script.ultimates[0].flags & hog.body_script.ultimates[0].RUNNING)
        yaw, pitch = aim(fight._eye(owner), fight._eye(hog))
        run.step({owner: (yaw, pitch, 1)})
        self.assertTrue(source.dead)
        self.assertEqual(source.killer, owner.body)
        after = target.total
        for _ in range(90):
            run.step({hog: (*angles, 0)})
        self.assertEqual(target.total, after)
        self.assertNotIn(owner.body, fight.npc_impulses)

    @staticmethod
    def flux_effect(player):
        return next((i for i in player.body_script.component.instances.values()
                     if i.graph.index == sigma_flux.FLUX_EFFECT), None)

    def select_flux(self, run, sigma, point, *, charge=True, confirm=True):
        script = sigma.body_script
        script.ult_seconds = 0
        if charge:
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for frame in range(81):
            angles = aim(run.match.combat._eye(sigma), point)
            button = 32 if frame == 0 else 1 if charge and confirm and frame == 80 else 0
            run.step({sigma: (*angles, button)})
        return next(i for i in script.component.instances.values() if i.graph.index == sigma_flux.FLUX)

    def test_actual_flux_lifts_and_slams_reaper(self):
        run, owner, sigma, fight = self.charge_scene(hero="Sigma", wall=None)
        point = tuple(owner.mover.position)
        instance = self.select_flux(run, sigma, point)
        self.assertEqual(instance.vars[59].value(), 2)
        self.assertAlmostEqual(instance.vars[2478].value().z, point[2], delta=0.02)
        self.assertEqual(sigma.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        health = fight.vitals[owner.body]
        for _ in range(120):
            run.step({})
            if health.total < 250:
                break
        effect = self.flux_effect(owner)
        self.assertIsNotNone(effect)
        self.assertEqual(effect.source_entity(), sigma.body)
        self.assertEqual(health.total, 200)
        peak = owner.mover.position[1]
        for _ in range(330):
            run.step({})
            peak = max(peak, owner.mover.position[1])
            if self.flux_effect(owner) is None:
                break
        self.assertGreater(peak, point[1] + 5)
        self.assertFalse(owner.mover.airborne)
        self.assertIsNone(self.flux_effect(owner))
        self.assertEqual(health.total, 75)
        self.assertEqual(sigma.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_flux_rejects_no_charge_allies_range_and_cover(self):
        for case in ("no charge", "ally", "range", "cover"):
            with self.subTest(case=case):
                run, owner, sigma, fight = self.charge_scene(
                    hero="Sigma", wall=3 if case == "cover" else None,
                )
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = sigma.team
                point = (14, 1, 6) if case == "range" else (0, 1, 2) if case == "cover" else (0, 1, 6)
                instance = self.select_flux(run, sigma, point, charge=case != "no charge")
                for _ in range(155):
                    run.step({})
                    self.assertIsNone(self.flux_effect(owner))
                if case != "no charge":
                    self.assertEqual(instance.vars[375].value(), ())
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(owner.mover.position[1], 1)

    def test_actual_flux_wraith_rejects_selection_or_releases_lift(self):
        for before_entry in (True, False):
            with self.subTest(before_entry=before_entry):
                run, owner, sigma, fight = self.charge_scene(hero="Sigma", wall=None)
                self.select_flux(run, sigma, tuple(owner.mover.position))
                health = fight.vitals[owner.body]
                if before_entry:
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(health.total, 250)
                else:
                    for _ in range(165):
                        run.step({})
                        if owner.mover.position[1] > 3:
                            break
                    self.assertGreater(owner.mover.position[1], 3)
                    self.assertEqual(health.total, 200)
                run.step({owner: (0, 0, 8)})
                self.assertTrue(reaper.intangible(owner))
                self.assertIsNone(self.flux_effect(owner))
                before = health.total
                for _ in range(280):
                    run.step({})
                    self.assertIsNone(self.flux_effect(owner))
                self.assertEqual(health.total, before)
                self.assertFalse(owner.mover.airborne)

    def test_actual_flux_resolves_shadow_step_windup_and_immunity(self):
        for immune_on_selection in (False, True):
            with self.subTest(immune_on_selection=immune_on_selection):
                run, owner, sigma, fight = self.charge_scene(hero="Sigma", wall=None)
                point = tuple(owner.mover.position)
                cast = self.select_flux(run, sigma, point, confirm=False)
                if immune_on_selection:
                    destination = self.start_shadow(run, owner)["target"]
                    for _ in range(18):
                        run.step({})
                run.step({sigma: (*aim(fight._eye(sigma), point), 1)})
                if not immune_on_selection:
                    for _ in range(20):
                        run.step({})
                    destination = self.start_shadow(run, owner)["target"]
                health = fight.vitals[owner.body]
                seen_lift = False
                seen_immune = False
                for _ in range(345):
                    run.step({})
                    if reaper.intangible(owner):
                        seen_immune = True
                        self.assertIsNone(self.flux_effect(owner))
                    else:
                        seen_lift |= self.flux_effect(owner) is not None
                self.assertGreater(cast.states[15].counter, 0)
                self.assertEqual(seen_lift, not immune_on_selection)
                self.assertEqual(seen_immune, immune_on_selection)
                self.assertIsNone(self.flux_effect(owner))
                if immune_on_selection:
                    self.assertEqual(health.total, 250)
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                else:
                    shadow = self.shadow(owner).states[32]
                    self.assertFalse(shadow.active)
                    self.assertEqual(health.total, 75)
                    self.assertLess(math.dist(owner.mover.position, point), 0.002)

    def test_actual_flux_lifts_during_death_blossom_without_canceling(self):
        run, owner, sigma, fight = self.charge_scene(
            hero="Sigma", names=("Reaper", "Sigma", "Soldier"), wall=None,
        )
        enemy = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, sigma.team, enemy.team = 0, 1, 1
        enemy.mover.position[:] = (3, 1, 6)
        enemy.mover.body = None
        health = fight.vitals_of(next(target for target in fight._targets() if target.owner is enemy))
        self.select_flux(run, sigma, tuple(owner.mover.position))
        for _ in range(40):
            run.step({})
        before = health.total
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        for _ in range(90):
            run.step({})
            if owner.mover.position[1] > 2:
                break
        self.assertGreater(owner.mover.position[1], 2)
        self.assertIsNotNone(self.flux_effect(owner))
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertLessEqual(health.total, before - 34)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_flux_landing_matches_without_owner_input(self):
        results = {}
        for mode in ("posed", "idle", "jump"):
            with self.subTest(mode=mode):
                run, owner, sigma, fight = self.charge_scene(hero="Sigma", wall=None)
                self.select_flux(run, sigma, tuple(owner.mover.position))
                frame = owner.commands.last_frame
                for _ in range(1 if mode == "jump" else 310):
                    if mode == "posed":
                        run.step({})
                    else:
                        run.frame += 310 if mode == "jump" else 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                if mode != "posed":
                    self.assertEqual(owner.commands.last_frame, frame)
                self.assertFalse(owner.mover.airborne)
                self.assertIsNone(self.flux_effect(owner))
                self.assertEqual(fight.vitals[owner.body].total, 75)
                results[mode] = tuple(owner.mover.position), owner.mover.velocity
        self.assertEqual(len(results), 3)
        self.assertEqual(results["posed"], results["idle"])
        self.assertEqual(results["posed"], results["jump"])

    def test_actual_flux_slam_spends_projected_barrier(self):
        run, owner, sigma, fight = self.charge_scene(
            hero="Sigma", names=("Reaper", "Sigma", "Zarya"), wall=None,
        )
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, zarya.team, sigma.team = 0, 0, 1
        zarya.mover.position[:] = (4, 1, 6)
        zarya.mover.body = None
        self.select_flux(run, sigma, tuple(owner.mover.position))
        for _ in range(320):
            run.step({})
            if (self.flux_effect(owner) is not None
                    and owner.mover.velocity[1] < -1 and 3 < owner.mover.position[1] < 8):
                break
        self.assertTrue(owner.mover.airborne)
        health = fight.vitals[owner.body]
        self.assertEqual(health.total, 200)
        run.step({zarya: (*aim(fight._eye(zarya), fight._eye(owner)), 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        for _ in range(36):
            run.step({})
            if bubble.health < 200:
                break
        self.assertFalse(owner.mover.airborne)
        self.assertEqual(bubble.health, 75)
        self.assertEqual(health.total, 200)
        for _ in range(185):
            run.step({})
            if self.flux_effect(owner) is None:
                break
        self.assertIsNone(self.flux_effect(owner))

    def test_actual_graviton_pulls_laterally_during_reaper_flux(self):
        run, owner, sigma, fight = self.charge_scene(
            hero="Sigma", names=("Reaper", "Sigma", "Zarya"), wall=None,
        )
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, sigma.team, zarya.team = 0, 1, 1
        x, y, z = owner.mover.position
        zarya.mover.position[:] = (x + 2, y, z - 3)
        zarya.mover.body = None
        self.select_flux(run, sigma, tuple(owner.mover.position))
        for _ in range(44):
            run.step({})
        zarya.body_script.set_ult_charge(2100)
        yaw, pitch = aim(fight._eye(zarya), (x + 2, y, z))
        run.step({zarya: (yaw, pitch, 32)})
        health = fight.vitals[owner.body]
        peak = y
        moved_while_lifting = False
        slammed = False
        for _ in range(345):
            old_position, old_health = tuple(owner.mover.position), health.total
            run.step({})
            peak = max(peak, owner.mover.position[1])
            if self.flux_effect(owner) is not None and fight.gravitons.controls(owner):
                px, py, _ = owner.mover.position
                moved_while_lifting |= px > old_position[0] and py > old_position[1]
            if old_health - health.total > 120:
                self.assertAlmostEqual(old_health - health.total, 125, delta=0.081)
                slammed = True
        self.assertTrue(moved_while_lifting)
        self.assertGreater(peak, y + 10)
        self.assertTrue(slammed)
        self.assertFalse(owner.mover.airborne)
        self.assertIsNone(self.flux_effect(owner))

    def test_actual_whole_hog_during_flux_preserves_slam_across_skipped_ticks(self):
        self.check_whole_hog_flux_overlap(descent=False)

    def test_actual_whole_hog_during_flux_descent_preserves_slam_across_skipped_ticks(self):
        self.check_whole_hog_flux_overlap(descent=True)

    def test_actual_repeated_whole_hog_during_flux_descent_preserves_slam_across_skipped_ticks(self):
        self.check_whole_hog_flux_overlap(descent=True, volleys=2)

    def test_actual_staggered_whole_hog_combines_residual_force_during_flux_descent(self):
        outcomes = {}
        for first_hits, skipped in ((True, False), (True, True), (False, False)):
            with self.subTest(first_hits=first_hits, skipped=skipped):
                run, owner, sigma, fight = self.charge_scene(
                    hero="Sigma", names=("Reaper", "Sigma", "Roadhog", "Roadhog2"), wall=None,
                )
                first, second = run.players[2:]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, sigma.team = 0, 1
                for hog, position in ((first, (-13, 1, 6)), (second, (0, 1, 19))):
                    self.assertTrue(run.match.switch_hero(hog, find_hero("Roadhog")))
                    hog.team = 1
                    hog.mover.position[:] = position
                    hog.mover.body = None
                start = tuple(owner.mover.position)
                self.select_flux(run, sigma, start)
                for hog in (first, second):
                    script = hog.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({hog: (0, -16384, 32)})
                idle = {first: (0, -16384, 0), second: (0, -16384, 0)}
                # Align the staggered volleys with the first two updates after descent commits.
                for _ in range(185):
                    run.step(idle)
                effect = self.flux_effect(owner)
                self.assertIsNotNone(effect)
                self.assertTrue(effect.states[16].counter)
                self.assertFalse(effect.states[16].active)
                health = fight.vitals[owner.body]
                self.assertEqual(health.total, 200)
                first_aim = self.aim_whole_hog(fight, first, owner) if first_hits else (0, -16384)
                run.step({**idle, first: (*first_aim, 0)})
                if first_hits:
                    tick, (x, _, z) = fight.npc_impulses[owner.body]
                    self.assertEqual(tick, run.frame)
                    self.assertGreater(x, 1)
                    self.assertAlmostEqual(z, 0)
                else:
                    self.assertNotIn(owner.body, fight.npc_impulses)
                before_second = health.total
                second_aim = self.aim_whole_hog(fight, second, owner)
                run.step({**idle, second: (*second_aim, 0)})
                self.assertLess(owner.mover.velocity[1], 0)
                tick, (x, _, z) = fight.npc_impulses[owner.body]
                self.assertEqual(tick, run.frame)
                self.assertLess(z, -1)
                if first_hits:
                    self.assertGreater(x, 0.5)
                else:
                    self.assertAlmostEqual(x, 0)
                before_slam = health.total
                self.assertLess(before_slam, before_second)
                run.step(idle)
                frame = owner.commands.last_frame
                until = run.frame + 270
                while run.frame < until:
                    run.frame = until if skipped else run.frame + 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(owner.commands.last_frame, frame)
                self.assertEqual(health.total, before_slam - 125)
                self.assertFalse(owner.mover.airborne)
                self.assertAlmostEqual(owner.mover.position[1], start[1], delta=0.01)
                self.assertIsNone(self.flux_effect(owner))
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertNotIn(owner.body, fight.impulse_landings)
                first_frames = {event["+0x84"] for event in first.client.messages(combat.HIT)}
                second_frames = {event["+0x84"] for event in second.client.messages(combat.HIT)}
                self.assertEqual(len(first_frames), int(first_hits))
                self.assertEqual(second_frames, {tick})
                if first_hits:
                    self.assertEqual(first_frames, {tick - 1})
                sigma_frames = [event["+0x84"] for event in sigma.client.messages(combat.HIT)]
                self.assertEqual(len(sigma_frames), 2)
                self.assertLess(sigma_frames[0], tick - 1)
                self.assertGreater(sigma_frames[-1], tick)
                outcomes[first_hits, skipped] = (
                    tuple(owner.mover.position), owner.mover.velocity, health.total, sigma_frames,
                )
        if len(outcomes) == 3:
            combined = outcomes[True, False]
            second_only = outcomes[False, False]
            self.assertEqual(combined, outcomes[True, True])
            self.assertGreater(combined[0][0], second_only[0][0] + 0.1)
            self.assertEqual(combined[-1], second_only[-1])

    def whole_hog_flux_lift_boundary_trace(self, *, offset, skipped):
        run, owner, sigma, fight = self.charge_scene(
            hero="Sigma", names=("Reaper", "Sigma", "Roadhog"), wall=None,
        )
        hog = run.players[2]
        self.assertTrue(run.match.switch_hero(hog, find_hero("Roadhog")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, sigma.team, hog.team = 0, 1, 1
        hog.mover.position[:] = (-13, 1, 6)
        hog.mover.body = None
        origin = tuple(owner.mover.position)
        self.select_flux(run, sigma, origin)
        # The fixture aligns the first 01C6 volley with the first 1DBC hit.
        for _ in range(31 + offset):
            run.step({})
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(ULT_COST).value())
        run.step({hog: (0, -16384, 32)})
        for _ in range(65):
            run.step({hog: (*self.aim_whole_hog(fight, hog, owner), 0)})
            if hog.client.messages(combat.HIT):
                break
        volley = run.frame
        self.assertIn(owner.body, fight.npc_impulses)
        run.step({hog: (0, -16384, 0)})
        last_command = owner.commands.last_frame
        end = run.frame + 270
        while run.frame < end:
            run.frame = end if skipped else run.frame + 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(owner.commands.last_frame, last_command)
        hog_hits = {event["+0x84"] for event in hog.client.messages(combat.HIT)}
        sigma_hits = [(event["+0x84"], event["+0x78"]["+0x4"]) for event in sigma.client.messages(combat.HIT)]
        self.assertEqual(hog_hits, {volley})
        self.assertEqual([damage for _, damage in sigma_hits], [50, 125])
        self.assertEqual(volley - sigma_hits[0][0], offset)
        self.assertLess(volley, sigma_hits[1][0])
        self.assertFalse(owner.mover.airborne)
        self.assertIsNone(self.flux_effect(owner))
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertNotIn(owner.body, fight.impulse_landings)
        self.assertAlmostEqual(owner.mover.position[1], origin[1], delta=0.01)
        hog_damage = hog.stats.value(stats.HERO_DAMAGE)
        self.assertGreater(hog_damage, 0)
        health = fight.vitals[owner.body].total
        self.assertAlmostEqual(health, 75 - hog_damage)
        return sigma_hits, volley, health, tuple(owner.mover.position), owner.mover.velocity

    def test_actual_whole_hog_hits_on_both_sides_of_flux_lift(self):
        for offset in (-1, 0, 1):
            with self.subTest(offset=offset):
                stepped = self.whole_hog_flux_lift_boundary_trace(offset=offset, skipped=False)
                skipped = self.whole_hog_flux_lift_boundary_trace(offset=offset, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_actual_whole_hog_hits_on_both_sides_of_flux_slam(self):
        timings = {}
        for offset in (-1, 0, 1):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(offset=offset, skipped=skipped):
                    run, owner, sigma, fight = self.charge_scene(
                        hero="Sigma", names=("Reaper", "Sigma", "Roadhog"), wall=None,
                    )
                    hog = run.players[2]
                    self.assertTrue(run.match.switch_hero(hog, find_hero("Roadhog")))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team, sigma.team, hog.team = 0, 1, 1
                    hog.mover.position[:] = (-13, 1, 6)
                    hog.mover.body = None
                    start = tuple(owner.mover.position)
                    self.select_flux(run, sigma, start)
                    # In this fixture the first 01C6 volley aligns with the slam after this delay.
                    for _ in range(194 + offset):
                        run.step({})
                    script = hog.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({hog: (0, -16384, 32)})
                    health = fight.vitals[owner.body]
                    for _ in range(200):
                        angles = self.aim_whole_hog(fight, hog, owner)
                        run.step({hog: (*angles, 0)})
                        if hog.client.messages(combat.HIT):
                            break
                    volley_tick = run.frame
                    self.assertIn(owner.body, fight.npc_impulses)
                    grounded_at_volley = not owner.mover.airborne
                    frame = owner.commands.last_frame
                    until = volley_tick + 270
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(owner.commands.last_frame, frame)
                    self.assertFalse(owner.mover.airborne)
                    self.assertIsNone(self.flux_effect(owner))
                    self.assertNotIn(owner.body, fight.npc_impulses)
                    self.assertNotIn(owner.body, fight.impulse_landings)
                    self.assertAlmostEqual(owner.mover.position[1], start[1], delta=0.01)
                    hog_hits = {event["+0x84"] for event in hog.client.messages(combat.HIT)}
                    sigma_hits = [event["+0x84"] for event in sigma.client.messages(combat.HIT)]
                    self.assertEqual(hog_hits, {volley_tick})
                    self.assertEqual(len(sigma_hits), 2)
                    slam_delay = int(offset == -1)
                    self.assertEqual(volley_tick - sigma_hits[1], offset - slam_delay)
                    self.assertEqual(grounded_at_volley, offset >= 0)
                    self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 175)
                    hog_damage = hog.stats.value(stats.HERO_DAMAGE)
                    self.assertGreater(hog_damage, 0)
                    self.assertGreater(health.total, 0)
                    self.assertAlmostEqual(health.total, 75 - hog_damage)
                    outcomes.append((
                        tuple(owner.mover.position), owner.mover.velocity,
                        health.total, sigma_hits, volley_tick,
                    ))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])
                timings[offset] = (outcomes[0][-1], outcomes[0][-2][1])
        if len(timings) == 3:
            baseline, slam = timings[0]
            self.assertEqual(timings[-1], (baseline - 1, slam + 1))
            self.assertEqual(timings[1], (baseline + 1, slam))

    def check_whole_hog_flux_overlap(self, *, descent, volleys=1):
        outcomes = {}
        volley_delay = 235
        schedules = [(volleys, False), (volleys, True), (0, False)]
        if volleys > 1:
            schedules.append((1, False))
        for aimed_volleys, skipped in schedules:
            with self.subTest(aimed_volleys=aimed_volleys, skipped=skipped):
                run, owner, sigma, fight = self.charge_scene(
                    hero="Sigma", names=("Reaper", "Sigma", "Roadhog"), wall=None,
                )
                hog = run.players[2]
                self.assertTrue(run.match.switch_hero(hog, find_hero("Roadhog")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, sigma.team, hog.team = 0, 1, 1
                hog.mover.position[:] = (-13, 1, 6)
                hog.mover.body = None
                start = tuple(owner.mover.position)
                self.select_flux(run, sigma, start)
                hog.body_script.ult_seconds = 0
                hog.body_script.set_ult_charge(
                    hog.body_script.component.entity_var(ULT_COST).value(),
                )
                run.step({hog: (0, -16384, 32)})
                health = fight.vitals[owner.body]
                landed = 0
                for elapsed in range(1, volley_delay + 1):
                    effect = self.flux_effect(owner)
                    lift = effect.states.get(16) if effect is not None else None
                    aiming = (landed < aimed_volleys and lift is not None
                              and lift.counter and lift.active != descent
                              and owner.mover.position[1] > start[1])
                    angles = (self.aim_whole_hog(fight, hog, owner)
                              if aiming else (0, -16384))
                    previous_health = health.total
                    had_impulse = owner.body in fight.npc_impulses
                    run.step({hog: (*angles, 0)})
                    if aiming and health.total < previous_health:
                        self.assertFalse(had_impulse)
                        self.assertIn(owner.body, fight.npc_impulses)
                        landed += 1
                    if landed == volleys:
                        if not skipped:
                            volley_delay = elapsed
                        break
                self.assertEqual(landed, aimed_volleys)
                effect = self.flux_effect(owner)
                self.assertIsNotNone(effect)
                self.assertEqual(effect.states[16].active, not descent)
                self.assertGreater(owner.mover.position[1], start[1])
                self.assertEqual(owner.body in fight.npc_impulses, aimed_volleys == volleys)
                before_slam = health.total
                self.assertGreater(before_slam, 125)
                self.assertEqual(before_slam < 200, aimed_volleys > 0)
                run.step({hog: (0, -16384, 0)})
                frame = owner.commands.last_frame
                for _ in range(1 if skipped else 270):
                    run.frame += 270 if skipped else 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(owner.commands.last_frame, frame)
                self.assertEqual(health.total, before_slam - 125)
                self.assertFalse(owner.mover.airborne)
                self.assertAlmostEqual(owner.mover.position[1], start[1], delta=0.01)
                self.assertIsNone(self.flux_effect(owner))
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertNotIn(owner.body, fight.impulse_landings)
                hog_hits = [event["+0x84"] for event in hog.client.messages(combat.HIT)]
                self.assertEqual(len(set(hog_hits)), aimed_volleys)
                sigma_hits = [event["+0x84"] for event in sigma.client.messages(combat.HIT)]
                self.assertEqual(len(sigma_hits), 2)
                if aimed_volleys:
                    self.assertLess(sigma_hits[0], hog_hits[0])
                    self.assertLess(hog_hits[-1], sigma_hits[-1])
                outcomes[aimed_volleys, skipped] = (
                    tuple(owner.mover.position), owner.mover.velocity, health.total, hog_hits, sigma_hits,
                )
        if len(outcomes) == len(schedules):
            final = outcomes[volleys, False]
            self.assertGreater(final[0][0], outcomes[0, False][0][0] + 0.1)
            self.assertEqual(final, outcomes[volleys, True])
            if descent:
                self.assertEqual(final[-1], outcomes[0, False][-1])
            if volleys > 1:
                single = outcomes[1, False]
                self.assertGreater(final[0][0], single[0][0] + 0.1)
                self.assertLess(final[2], single[2])
                self.assertEqual(final[-1], single[-1])

    def halt_flux_scene(self, delay):
        if not orisa_halt.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Halt supplement")
        run, owner, sigma, fight = self.charge_scene(
            hero="Sigma", names=("Reaper", "Sigma", "Orisa"), wall=None,
        )
        orisa = run.players[2]
        self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, sigma.team, orisa.team = 0, 1, 1
        orisa.mover.position[:] = (2, 1, 0)
        orisa.mover.body = None
        self.select_flux(run, sigma, tuple(owner.mover.position))
        for _ in range(delay):
            run.step({})
        self.detonate_halt(run, orisa, fight)
        self.assertIn(owner.body, fight.halts.pulls)
        return run, owner, fight

    def test_actual_halt_pulls_laterally_during_reaper_flux(self):
        run, owner, fight = self.halt_flux_scene(9)
        moved_while_lifting = False
        peak = owner.mover.position[1]
        for _ in range(340):
            x, y, _ = owner.mover.position
            run.step({})
            px, py, _ = owner.mover.position
            peak = max(peak, py)
            if self.flux_effect(owner) is not None and owner.body in fight.halts.pulls:
                moved_while_lifting |= px > x and py > y
        self.assertTrue(moved_while_lifting)
        self.assertGreater(peak, 10)
        self.assertFalse(owner.mover.airborne)
        self.assertIsNone(self.flux_effect(owner))
        self.assertNotIn(owner.body, fight.halts.pulls)
        self.assertEqual(fight.vitals[owner.body].total, 75)

    def test_actual_halt_and_flux_overlap_matches_a_skipped_window(self):
        poses = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, fight = self.halt_flux_scene(9)
                original = tuple(owner.mover.position)
                frame = owner.commands.last_frame
                for _ in range(1 if skip else 340):
                    run.frame += 340 if skip else 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(owner.commands.last_frame, frame)
                self.assertGreater(owner.mover.position[0], original[0] + 0.5)
                self.assertFalse(owner.mover.airborne)
                self.assertIsNone(self.flux_effect(owner))
                self.assertNotIn(owner.body, fight.halts.pulls)
                self.assertEqual(fight.vitals[owner.body].total, 75)
                poses.append((tuple(owner.mover.position), owner.mover.velocity))
        self.assertEqual(len(poses), 2)
        self.assertEqual(poses[0], poses[1])

    def test_actual_flux_can_prevent_pending_halt_from_starting_move_to(self):
        run, owner, fight = self.halt_flux_scene(15)
        pull = fight.halts.pulls[owner.body]
        original = tuple(owner.mover.position)
        for _ in range(340):
            run.step({})
            move = pull.script.states.get(pull.script.graph.node(3).state)
            self.assertFalse(move and move.counter)
        self.assertIsNone(self.flux_effect(owner))
        self.assertNotIn(owner.body, fight.halts.pulls)
        self.assertEqual(tuple(owner.mover.position), original)
        self.assertEqual(fight.vitals[owner.body].total, 75)

    def test_actual_halt_flux_lateral_sweep_stops_at_a_wall(self):
        run, owner, fight = self.halt_flux_scene(9)
        wall = 0.5
        run.match.collision_world = World([
            (-30, 1, -30, -30, 1, 80, 30, 1, 80),
            (-30, 1, -30, 30, 1, 80, 30, 1, -30),
            (wall, 1, -10, wall, 31, -10, wall, 31, 20),
            (wall, 1, -10, wall, 31, 20, wall, 1, 20),
        ], name="Static wall introduced after Halt detonation")
        peak = owner.mover.position[1]
        for _ in range(340):
            run.step({})
            x, y, _ = owner.mover.position
            self.assertLess(x, wall)
            peak = max(peak, y)
        self.assertGreater(peak, 10)
        self.assertGreater(owner.mover.position[0], 0)
        self.assertFalse(owner.mover.airborne)
        self.assertNotIn(owner.body, fight.halts.pulls)
        self.assertEqual(fight.vitals[owner.body].total, 75)

    def test_actual_flux_retires_on_caster_or_recipient_replacement(self):
        for replace_caster in (False, True):
            with self.subTest(replace_caster=replace_caster):
                run, owner, sigma, fight = self.charge_scene(hero="Sigma", wall=None)
                self.select_flux(run, sigma, tuple(owner.mover.position))
                health = fight.vitals[owner.body]
                for _ in range(155):
                    run.step({})
                    if health.total == 200:
                        break
                self.assertIsNotNone(self.flux_effect(owner))
                replace_player = sigma if replace_caster else owner
                self.assertTrue(run.match.switch_hero(replace_player, find_hero("Soldier: 76")))
                self.assertIsNone(self.flux_effect(owner))
                for _ in range(230):
                    run.step({})
                self.assertIsNone(self.flux_effect(owner))
                target = next(target for target in fight._targets() if target.owner is owner)
                current = fight.vitals_of(target)
                self.assertEqual(current.total, 200 if replace_caster else current.maximum)

    def test_actual_reaper_shell_interrupts_flux_lift_and_preserves_committed_slam(self):
        for descent in (False, True):
            with self.subTest(descent=descent):
                run, owner, sigma, fight = self.charge_scene(
                    hero="Sigma", names=("Reaper", "Sigma", "Ally"), wall=None,
                )
                ally = run.players[2]
                self.assertTrue(run.match.switch_hero(ally, find_hero("Reaper")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, ally.team, sigma.team = 0, 0, 1
                ally.mover.position[:] = (0, 1, -4)
                ally.mover.body = None
                self.select_flux(run, sigma, tuple(owner.mover.position))
                for _ in range(235):
                    run.step({})
                    effect = self.flux_effect(owner)
                    state = effect.states.get(16) if effect else None
                    if state and state.counter and state.active != descent:
                        break
                self.assertIsNotNone(state)
                self.assertEqual(state.active, not descent)
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
                vitals.damage(vitals.total - 20)
                angles = aim(fight._eye(ally), fight._eye(sigma))
                run.step({ally: (*angles, 1)})
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, ally.body)
                self.assertEqual(self.flux_effect(owner) is not None, descent)
                for _ in range(125):
                    run.step({})
                    if not owner.mover.airborne:
                        break
                self.assertFalse(owner.mover.airborne)
                self.assertEqual(fight.vitals[owner.body].total, 75 if descent else 200)

    def graviton_scene(self, *, land=True, extra=()):
        run, owner, zarya, fight = self.charge_scene(
            hero="Zarya", names=("Reaper", "Zarya", *extra), wall=None,
        )
        owner.mover.position[:] = (2, 1, 4)
        owner.mover.body = None
        zarya.body_script.ult_seconds = 0
        yaw, pitch = aim(fight._eye(zarya), (0, 1, 4))
        run.step({zarya: (yaw, pitch, 32)})
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.gravitons.fields)
        run.step({})
        zarya.body_script.set_ult_charge(2100)
        run.step({zarya: (yaw, pitch, 32)})
        rocket, = fight.rockets
        self.assertEqual(rocket.graviton_body, zarya.body)
        self.assertEqual(rocket.definition, 0x04000000000008CE)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertFalse(fight.gravitons.fields)
        self.assertEqual(zarya.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        if not land:
            return run, owner, zarya, fight
        for _ in range(32):
            run.step({})
            if fight.gravitons.fields:
                break
        field, = fight.gravitons.fields
        self.assertEqual(field.body, zarya.body)
        self.assertEqual(field.radius, 6)
        self.assertEqual(field.until - field.last_tick, 250)
        return run, owner, zarya, fight

    def test_actual_graviton_world_impact_pull_damage_and_expiry(self):
        outcomes = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, zarya, fight = self.graviton_scene(land=False)
                before = tuple(owner.mover.position)
                if skip:
                    run.frame += 325
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(325):
                        run.step({})
                self.assertGreater(math.dist(before, owner.mover.position), 0.8)
                self.assertFalse(fight.is_stunned(owner))
                self.assertAlmostEqual(fight.vitals[owner.body].total, 250 - 4 * graviton.DAMAGE_RATE)
                self.assertFalse(fight.gravitons.fields)
                self.assertFalse(fight.gravitons.pulled)
                self.assertEqual(zarya.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                outcomes.append(tuple(owner.mover.position))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_graviton_wraith_suspends_and_reacquires(self):
        run, owner, _, fight = self.graviton_scene()
        for _ in range(14):
            run.step({})
        self.assertTrue(fight.gravitons.controls(owner))
        health = fight.vitals[owner.body]
        before = health.total
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertFalse(fight.gravitons.controls(owner))
        phased_health = health.total
        self.assertLess(phased_health, before)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        position = tuple(owner.mover.position)
        owner.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(owner.mover.position[2], position[2])
        for _ in range(200):
            if not reaper.intangible(owner):
                break
            run.step({})
        self.assertFalse(reaper.intangible(owner))
        run.step({})
        self.assertTrue(fight.gravitons.controls(owner))
        self.assertGreaterEqual(health.total, phased_health - 2 * graviton.DAMAGE_RATE * combat.TICK - 1e-9)
        self.assertLess(health.total, phased_health)

    def test_actual_graviton_moves_reaper_without_canceling_blossom(self):
        run, owner, zarya, fight = self.graviton_scene()
        field, = fight.gravitons.fields
        run.step({})
        self.assertTrue(fight.gravitons.controls(owner))
        owner.body_script.set_ult_charge(2100)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is zarya))
        before = health.total
        distance = math.dist(owner.mover.position, field.position)
        run.step({owner: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertLess(health.total, before - 30)
        self.assertLess(math.dist(owner.mover.position, field.position), distance - 0.5)

    def test_actual_graviton_respects_shadow_immunity_and_reacquires_after_landing(self):
        run, owner, _, fight = self.graviton_scene()
        field, = fight.gravitons.fields
        destination = self.start_shadow(run, owner)["target"]
        health = fight.vitals[owner.body]
        self.assertLess(math.dist(destination, field.position), field.radius)
        self.assertTrue(fight.gravitons.controls(owner))
        self.assertFalse(reaper.intangible(owner))
        before = health.total
        for _ in range(95):
            run.step({})
            if reaper.intangible(owner):
                break
        self.assertTrue(reaper.intangible(owner))
        self.assertLess(health.total, before)
        phased_health = health.total
        for _ in range(125):
            if not reaper.intangible(owner):
                break
            run.step({})
            if reaper.intangible(owner):
                self.assertEqual(health.total, phased_health)
                self.assertFalse(fight.gravitons.controls(owner))
        self.assertFalse(reaper.intangible(owner))
        self.assertLess(math.dist(owner.mover.position, destination), 0.002)
        run.step({})
        self.assertTrue(fight.gravitons.controls(owner))
        self.assertLess(health.total, phased_health)

    def test_actual_graviton_and_halt_respect_shadow_pose_across_skipped_ticks(self):
        if not orisa_halt.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Halt supplement")

        def settle(run, owner, fight, record, until, skipped):
            samples = []
            update = fight.gravitons.update

            def observe(tick):
                immune = reaper.intangible(owner)
                before = tuple(owner.mover.position), fight.vitals[owner.body].total
                update(tick)
                after = tuple(owner.mover.position), fight.vitals[owner.body].total
                if immune:
                    self.assertEqual(after, before)
                    self.assertNotIn(owner.body, fight.halts.pulls)
                    if samples and samples[-1][1]:
                        self.assertEqual(after, samples[-1][3])
                if record["moved"] and samples and not samples[-1][2]:
                    self.assertLess(math.dist(before[0], record["target"]), 0.002)
                samples.append((tick, immune, record["moved"], after))

            with patch.object(fight.gravitons, "update", side_effect=observe):
                while run.frame < until:
                    run.frame = until if skipped else run.frame + 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            return samples

        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, zarya, fight = self.graviton_scene(extra=("Orisa",))
                orisa = run.players[2]
                self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, zarya.team, orisa.team = 0, 1, 1
                orisa.mover.position[:] = (1, 1, -3)
                orisa.mover.body = None
                field, = fight.gravitons.fields
                record = self.start_shadow(run, owner)
                self.assertLess(math.dist(record["target"], field.position), field.radius)
                for _ in range(22):
                    run.step({})
                self.detonate_halt(run, orisa, fight)
                self.assertIn(owner.body, fight.halts.pulls)
                self.assertTrue(fight.gravitons.controls(owner))
                self.assertFalse(reaper.intangible(owner))
                input_frame = owner.commands.last_frame

                samples = settle(run, owner, fight, record, field.until, skipped)

                self.assertEqual(owner.commands.last_frame, input_frame)
                self.assertTrue(record["moved"])
                self.assertFalse(fight.gravitons.fields)
                self.assertNotIn(owner.body, fight.halts.pulls)
                immune_ticks = [tick for tick, immune, _, _ in samples if immune]
                self.assertTrue(immune_ticks)
                self.assertTrue(any(moved for _, _, moved, _ in samples))
                hits = [hit["+0x84"] for hit in zarya.client.messages(combat.HIT)]
                self.assertLess(hits[0], immune_ticks[0])
                self.assertGreater(hits[-1], immune_ticks[-1])
                self.assertTrue(set(immune_ticks).isdisjoint(hits))
                outcomes.append((samples, hits, tuple(owner.mover.position)))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_graviton_filters_cover_range_and_allies(self):
        for case in ("cover", "range", "ally"):
            with self.subTest(case=case):
                run, owner, zarya, fight = self.graviton_scene()
                if case == "cover":
                    run.match.collision_world = World([
                        (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                        (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                        (1, 0, -2, 1, 8, -2, 1, 8, 12),
                        (1, 0, -2, 1, 8, 12, 1, 0, 12),
                    ], name="Graviton acquisition cover")
                elif case == "range":
                    owner.mover.position[0] = 12
                    owner.mover.body = None
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = zarya.team
                before = tuple(owner.mover.position)
                for _ in range(40):
                    run.step({})
                self.assertEqual(tuple(owner.mover.position), before)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertFalse(fight.gravitons.controls(owner))

    def test_actual_graviton_pulls_projected_reaper_and_spends_shield(self):
        run, owner, zarya, fight = self.graviton_scene(extra=("Ally",))
        ally = run.players[2]
        self.assertTrue(run.match.switch_hero(ally, heroes()[zarya_barriers.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        zarya.team, owner.team, ally.team = 0, 1, 1
        ally.mover.position[:] = (7, 1, 4)
        ally.mover.body = None
        run.step({ally: (*aim(fight._eye(ally), fight._eye(owner)), 16)})
        shield = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(shield)
        self.assertEqual(shield.health, 200)
        health = fight.vitals[owner.body]
        before = health.total
        position = tuple(owner.mover.position)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(shield.health, 200 - 30 * graviton.DAMAGE_RATE * combat.TICK)
        self.assertEqual(health.total, before)
        self.assertGreater(math.dist(owner.mover.position, position), 0.5)
        self.assertTrue(fight.gravitons.controls(owner))

    def test_actual_graviton_grasp_absorbs_and_deflect_transfers_field(self):
        for defense in ("Grasp", "Deflect"):
            with self.subTest(defense=defense):
                run, owner, zarya, fight = self.charge_scene(
                    hero="Zarya", names=("Reaper", "Zarya", defense), wall=None,
                )
                defender = run.players[2]
                guid = sigma_grasp.HERO if defense == "Grasp" else 0x02E0000000000029
                self.assertTrue(run.match.switch_hero(defender, heroes()[guid]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = zarya.team = 0
                defender.team = 1
                owner.mover.position[:] = (2, 1, 1)
                defender.mover.position[:] = (0, 1, 4)
                owner.mover.body = defender.mover.body = None
                button = 8 if defense == "Grasp" else 16
                for index in range(15):
                    run.step({defender: (-32768, 3500, button if index == 0 else 0)})
                zarya.body_script.set_ult_charge(2100)
                run.step({zarya: (0, 0, 32), defender: (-32768, 3500, 0)})
                self.assertEqual(fight.rockets[0].graviton_body, zarya.body)
                for _ in range(40):
                    run.step({defender: (-32768, 3500, 0)})
                self.assertFalse(fight.rockets)
                if defense == "Grasp":
                    self.assertFalse(fight.gravitons.fields)
                    self.assertEqual(fight.vitals[owner.body].total, 250)
                else:
                    field, = fight.gravitons.fields
                    self.assertIs(field.owner, defender)
                    self.assertEqual(field.body, defender.body)
                    self.assertLess(fight.vitals[owner.body].total, 250)
                    self.assertTrue(fight.gravitons.controls(owner))

    def test_actual_graviton_matrix_absorbs_before_field(self):
        if not dva_matrix.eligible(0x04000000000008CE):
            self.skipTest("requires the pinned Graviton projectile tag")
        run, owner, zarya, fight = self.charge_scene(
            hero="Zarya", names=("Reaper", "Zarya", "Dva"), wall=None,
        )
        defender = run.players[2]
        self.assertTrue(run.match.switch_hero(defender, replace(find_hero("dva"), body=pools.DVA_MECH)))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        zarya.team, owner.team, defender.team = 0, 1, 1
        defender.mover.position[:] = (0, 1, 4)
        defender.mover.body = None
        for _ in range(75):
            run.step({})
        for _ in range(12):
            run.step({defender: (-32768, 0, 2)})
        zarya.body_script.set_ult_charge(2100)
        run.step({zarya: (0, 0, 32), defender: (-32768, 0, 2)})
        self.assertEqual(fight.rockets[0].graviton_body, zarya.body)
        for _ in range(40):
            run.step({defender: (-32768, 0, 2)})
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.gravitons.fields)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def halt_scene(self, *, extra=()):
        if not orisa_halt.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Halt supplement")
        run, owner, orisa, fight = self.charge_scene(
            hero="Orisa", names=("Reaper", "Orisa", *extra), wall=None,
        )
        owner.mover.position[:] = (2, 1, 6)
        owner.mover.body = None
        run.step({})
        return run, owner, orisa, fight

    def detonate_halt(self, run, orisa, fight):
        for frame in range(26):
            run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
        orb, = fight.halts.orbs.values()
        self.assertIsNotNone(orb.until)
        return orb

    def test_actual_halt_moves_reaper_without_damage_or_stun(self):
        run, owner, orisa, fight = self.halt_scene()
        before = tuple(owner.mover.position)
        orb = self.detonate_halt(run, orisa, fight)
        pull = fight.halts.pulls[owner.body]
        self.assertEqual(pull.script.graph.index, 0xCCE)
        self.assertEqual(tuple(owner.mover.position), before)
        centre = orb.position
        for _ in range(38):
            run.step({})
        self.assertLess(math.dist(owner.mover.position, centre), math.dist(before, centre) - 0.5)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertFalse(fight.halts.orbs)
        for _ in range(100):
            run.step({})
        self.assertFalse(fight.halts.pulls)

    def test_actual_halt_idle_pose_matches_input_and_skipped_ticks(self):
        positions = {}
        for mode in ("posed", "idle", "skipped"):
            with self.subTest(mode=mode):
                run, owner, orisa, fight = self.halt_scene()
                before = tuple(owner.mover.position)
                orb = self.detonate_halt(run, orisa, fight)
                script = owner.body_script
                input_frame = owner.commands.last_frame
                if mode == "posed":
                    for _ in range(40):
                        run.step({})
                else:
                    for _ in range(1 if mode == "skipped" else 41):
                        run.frame += 41 if mode == "skipped" else 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(owner.commands.last_frame, input_frame)
                    self.assertEqual(script.frame, input_frame + 40)
                    settled = tuple(owner.mover.position)
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(tuple(owner.mover.position), settled)
                    owner.take_commands([Command(input_frame + 10, forward=127, buttons=1)])
                    self.assertEqual(tuple(owner.mover.position), settled)
                self.assertLess(math.dist(owner.mover.position, orb.position),
                                math.dist(before, orb.position) - 0.5)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                positions[mode] = (tuple(owner.mover.position), owner.mover.velocity)
                owner.take_commands([Command(script.frame + 1, forward=127)])
                self.assertEqual(owner.mover.last_command.frame, script.frame)
                positions[mode] += (tuple(owner.mover.position),)
        self.assertEqual(positions["idle"], positions["skipped"])
        self.assertEqual(positions["posed"], positions["idle"])

    def test_actual_halt_idle_gap_finishes_pull_and_slow_with_or_without_wall(self):
        for covered in (False, True):
            positions = []
            for skipped in (False, True):
                with self.subTest(covered=covered, skipped=skipped):
                    run, owner, orisa, fight = self.halt_scene()
                    orb = self.detonate_halt(run, orisa, fight)
                    pull = fight.halts.pulls[owner.body]
                    before = math.dist(owner.mover.position, orb.position)
                    if covered:
                        run.match.collision_world = World([
                            (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                            (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                            (1.3, 0, 4, 1.3, 8, 4, 1.3, 8, 12),
                            (1.3, 0, 4, 1.3, 8, 12, 1.3, 0, 12),
                            (1.3, 0, 4, 1.3, 8, 12, 1.3, 8, 4),
                            (1.3, 0, 4, 1.3, 0, 12, 1.3, 8, 12),
                        ], name="Halt wall introduced after acquisition")
                    for _ in range(1 if skipped else 145):
                        run.frame += 145 if skipped else 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertNotIn(owner.body, fight.halts.pulls)
                    self.assertTrue(pull.script.stopped)
                    self.assertFalse(owner.body_script.component.entity_var(23186).value())
                    self.assertEqual(fight.vitals[owner.body].total, 250)
                    if covered:
                        self.assertGreater(owner.mover.position[0], 1.3)
                    else:
                        self.assertLess(math.dist(owner.mover.position, orb.position), before - 0.5)
                    positions.append((tuple(owner.mover.position), owner.mover.velocity))
            self.assertEqual(positions[0], positions[1])

    def test_actual_halt_flight_gap_keeps_wall_range_and_wraith_impact_order(self):
        for scenario in ("wall", "range", "wraith"):
            results = []
            for skipped in (False, True):
                with self.subTest(scenario=scenario, skipped=skipped):
                    run, owner, orisa, fight = self.halt_scene()
                    if scenario == "range":
                        owner.mover.position[:] = (2, 1, 98)
                        run.match.collision_world = flat_world(1)
                    else:
                        owner.mover.position[:] = (2, 1, 4)
                        run.match.collision_world = World([
                            (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                            (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                            (-8, 0, 5, 8, 8, 5, 8, 0, 5),
                            (-8, 0, 5, -8, 8, 5, 8, 8, 5),
                        ], name="Halt retained flight wall")
                    owner.mover.body = None
                    run.step({orisa: (0, 0, 2), owner: (0, 0, 8 if scenario == "wraith" else 0)})
                    for _ in range(20):
                        if fight.halts.orbs:
                            break
                        run.step({})
                    orb, = fight.halts.orbs.values()
                    self.assertIsNone(orb.until)
                    self.assertEqual(reaper.intangible(owner), scenario == "wraith")
                    launch_tick = orb.tick
                    before = tuple(owner.mover.position)
                    ticks = 440 if scenario == "range" else 250
                    last_input = owner.commands.last_frame
                    for _ in range(1 if skipped else ticks):
                        run.frame += ticks if skipped else 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(owner.commands.last_frame, last_input)
                    self.assertFalse(fight.halts.orbs)
                    self.assertFalse(fight.halts.pulls)
                    self.assertFalse(reaper.intangible(owner))
                    self.assertEqual(orb.tick - launch_tick, 250 if scenario == "range" else 13)
                    self.assertEqual(orb.until - orb.tick, 32)
                    if scenario == "wraith":
                        self.assertEqual(tuple(owner.mover.position), before)
                    else:
                        self.assertGreater(math.dist(owner.mover.position, before), 0.5)
                    results.append((orb.position, orb.tick, tuple(owner.mover.position)))
            self.assertEqual(results[0], results[1])

    def test_actual_halt_excludes_wraith_and_wraith_cleanses_pending_pull(self):
        for already_intangible in (False, True):
            with self.subTest(already_intangible=already_intangible):
                run, owner, orisa, fight = self.halt_scene()
                if already_intangible:
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(reaper.intangible(owner))
                before = tuple(owner.mover.position)
                self.detonate_halt(run, orisa, fight)
                if not already_intangible:
                    self.assertIn(owner.body, fight.halts.pulls)
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(reaper.intangible(owner))
                for _ in range(35):
                    run.step({})
                self.assertNotIn(owner.body, fight.halts.pulls)
                self.assertEqual(tuple(owner.mover.position), before)

    def test_actual_halt_moves_reaper_without_canceling_blossom(self):
        run, owner, orisa, fight = self.halt_scene()
        orb = self.detonate_halt(run, orisa, fight)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        before = math.dist(owner.mover.position, orb.position)
        for _ in range(40):
            run.step({})
        self.assertLess(math.dist(owner.mover.position, orb.position), before - 0.5)
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_halt_moves_projected_reaper_during_enemy_blossom(self):
        run, owner, orisa, fight = self.halt_scene(extra=("Zarya", "Enemy Reaper"))
        zarya, enemy = run.players[2:]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = zarya.team = 0
        orisa.team = enemy.team = 1
        zarya.mover.position[:] = (7, 1, 6)
        enemy.mover.position[:] = (2, 1, 10)
        zarya.mover.body = enemy.mover.body = None
        for frame in range(25):
            run.step({orisa: (0, 0, 2 if frame == 0 else 0)})
        heading = aim(fight._eye(zarya), (2, 2, 6))
        run.step({zarya: (*heading, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        before = tuple(owner.mover.position)
        run.step({orisa: (0, 0, 2)})
        orb, = fight.halts.orbs.values()
        self.assertIsNotNone(orb.until)
        self.assertIn(owner.body, fight.halts.pulls)
        self.assertEqual(bubble.health, 200)
        enemy.body_script.set_ult_charge(2100)
        run.step({enemy: (0, 0, 32)})
        self.assertEqual(bubble.health, 166)
        for _ in range(100):
            if bubble.health == 0:
                break
            run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertLess(math.dist(owner.mover.position, orb.position), math.dist(before, orb.position) - 0.5)
        self.assertFalse(fight.is_stunned(owner))
        self.assertIsNotNone(reaper.blossom_state(enemy.body_script))
        for _ in range(20):
            run.step({})
            if fight.vitals[owner.body].total < 250:
                break
        self.assertEqual(fight.vitals[owner.body].total, 216)
        self.assertEqual(enemy.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_halt_acquires_zarya_inside_or_outside_particle_barrier(self):
        for inside in (False, True):
            with self.subTest(inside=inside):
                run, owner, orisa, fight = self.halt_scene()
                self.assertTrue(run.match.switch_hero(owner, heroes()[zarya_barriers.HERO]))
                owner.mover.position[:] = (1 if inside else 2.8, 1, 7.6)
                owner.mover.body = None
                for frame in range(25):
                    run.step({orisa: (0, 0, 2 if frame == 0 else 0)})
                run.step({owner: (0, 0, 8)})
                bubble = fight.zarya_bubbles.for_target(owner)
                self.assertIsNotNone(bubble)
                target = next(target for target in fight._targets() if target.owner is owner)
                health = fight.vitals_of(target)
                initial = health.total
                before = tuple(owner.mover.position)
                run.step({orisa: (0, 0, 2)})
                orb, = fight.halts.orbs.values()
                self.assertIsNotNone(orb.until)
                self.assertEqual(math.dist(orb.position, bubble.centre()) < zarya_barriers.RADIUS, inside)
                self.assertIn(owner.body, fight.halts.pulls)
                for _ in range(38):
                    run.step({})
                if not inside:
                    self.assertLess(math.dist(owner.mover.position, orb.position),
                                    math.dist(before, orb.position) - 0.5)
                self.assertEqual(bubble.health, 200)
                self.assertEqual(health.total, initial)

    def test_actual_halt_moves_transcending_zen_but_cryo_rejects_acquisition(self):
        run, owner, orisa, fight = self.halt_scene(extra=("Zen", "Mei"))
        zen, mei = run.players[2:]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = orisa.team = 0
        zen.team = mei.team = 1
        for actor, hero, position in (
            (zen, defenses.ZENYATTA, (2, 1, 6)),
            (mei, defenses.MEI, (-2, 1, 6)),
        ):
            self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
            actor.mover.position[:] = position
            actor.mover.body = None
        owner.mover.position[:] = (5, 1, 6)
        owner.mover.body = None
        for _ in range(20):
            run.step({})
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({zen: (0, 0, 32), mei: (0, 0, 8)})
        self.assertTrue(defenses.invulnerable(zen))
        self.assertTrue(defenses.invulnerable(mei))
        self.assertTrue(mei.body_script.component.entity_var(6601).value())
        positions = tuple(zen.mover.position), tuple(mei.mover.position)
        orb = self.detonate_halt(run, orisa, fight)
        self.assertIn(zen.body, fight.halts.pulls)
        self.assertNotIn(mei.body, fight.halts.pulls)
        for _ in range(40):
            run.step({})
        self.assertLess(math.dist(zen.mover.position, orb.position),
                        math.dist(positions[0], orb.position) - 0.5)
        self.assertEqual(tuple(mei.mover.position), positions[1])
        self.assertTrue(defenses.invulnerable(zen))
        health = fight.vitals[zen.body]
        before = health.total
        reaper_tests.ReaperTests().fire(run, owner, zen)
        self.assertEqual(health.total, before)

    def test_actual_cryo_freeze_retires_halt_post_slow(self):
        run, mei, orisa, fight = self.halt_scene()
        self.assertTrue(run.match.switch_hero(mei, heroes()[defenses.MEI]))
        mei.mover.position[:] = (2, 1, 6)
        mei.mover.body = None
        self.detonate_halt(run, orisa, fight)
        pull = fight.halts.pulls[mei.body]
        slow = mei.body_script.component.entity_var(23186)
        for _ in range(75):
            if slow.value():
                break
            run.step({})
        self.assertAlmostEqual(slow.value(), 0.65)
        run.step({mei: (0, 0, 8)})
        self.assertTrue(defenses.invulnerable(mei))
        run.step({})
        self.assertNotIn(mei.body, fight.halts.pulls)
        self.assertTrue(pull.script.stopped)
        self.assertFalse(slow.value())

    def test_actual_halt_post_slow_survives_source_switch_and_wraith_clears_it(self):
        for cleanse in (False, True):
            with self.subTest(cleanse=cleanse):
                run, owner, orisa, fight = self.halt_scene()
                self.detonate_halt(run, orisa, fight)
                pull = fight.halts.pulls[owner.body]
                script = pull.script
                for _ in range(100):
                    wait = script.states.get(script.graph.node(5).state)
                    if wait is not None and wait.active:
                        break
                    run.step({})
                self.assertIsNotNone(wait)
                self.assertTrue(wait.active)
                slow = owner.body_script.component.entity_var(23186)
                self.assertAlmostEqual(slow.value(), 0.65)
                self.assertTrue(run.match.switch_hero(orisa, find_hero("Reinhardt")))
                for _ in range(24):
                    self.step(run, orisa, owner, forward=127)
                self.assertIs(fight.halts.pulls[owner.body], pull)
                self.assertTrue(wait.active)
                self.assertAlmostEqual(slow.value(), 0.65)
                vx, _, vz = owner.mover.velocity
                self.assertAlmostEqual(math.hypot(vx, vz), 1.925, delta=0.005)
                if cleanse:
                    run.step({owner: (0, 0, 8)})
                    run.step({})
                    self.assertTrue(reaper.intangible(owner))
                else:
                    for _ in range(55):
                        self.step(run, orisa, owner, forward=127)
                    self.assertEqual(wait.ended_at - wait.start, 650)
                    vx, _, vz = owner.mover.velocity
                    self.assertAlmostEqual(math.hypot(vx, vz), 5.5, delta=0.005)
                self.assertNotIn(owner.body, fight.halts.pulls)
                self.assertTrue(script.stopped)
                self.assertFalse(slow.value())

    def test_actual_halt_resolves_shadow_phases_without_canceling_teleport(self):
        for phase in ("windup", "immune", "landed"):
            with self.subTest(phase=phase):
                run, owner, orisa, fight = self.halt_scene()
                origin = tuple(owner.mover.position)
                if phase == "windup":
                    for frame in range(25):
                        run.step({orisa: (0, 0, 2 if frame == 0 else 0)})
                destination = self.start_shadow(run, owner)["target"]
                if phase == "immune":
                    # Arm while the confirmed departure is approaching its immunity.
                    for _ in range(45):
                        run.step({})
                    orb = self.detonate_halt(run, orisa, fight)
                elif phase == "landed":
                    for _ in range(125):
                        if math.dist(owner.mover.position, destination) < 0.002:
                            break
                        run.step({})
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                    orisa.mover.position[0] = destination[0] - 2
                    orisa.mover.body = None
                    orb = self.detonate_halt(run, orisa, fight)
                else:
                    run.step({orisa: (0, 0, 2)})
                    orb, = fight.halts.orbs.values()
                    self.assertIsNotNone(orb.until)
                self.assertEqual(reaper.intangible(owner), phase == "immune")
                before = math.dist(owner.mover.position, orb.position)
                self.assertLess(before, 5)
                self.assertEqual(owner.body in fight.halts.pulls, phase != "immune")
                self.assertFalse(fight.is_stunned(owner))
                if phase != "immune":
                    for _ in range(36):
                        run.step({})
                    self.assertLess(math.dist(owner.mover.position, orb.position), before - 0.5)
                if phase != "landed":
                    self.assertGreater(math.dist(origin, destination), 2)
                    for _ in range(125):
                        if math.dist(owner.mover.position, destination) < 0.002:
                            break
                        run.step({})
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                    self.assertNotIn(owner.body, fight.halts.pulls)
                self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_halt_retires_old_orb_and_recipient_on_body_replacement(self):
        for replace_source in (False, True):
            with self.subTest(replace_source=replace_source):
                run, owner, orisa, fight = self.halt_scene()
                if replace_source:
                    run.step({orisa: (0, 0, 2)})
                    for _ in range(25):
                        if fight.halts.orbs:
                            break
                        run.step({})
                    orb, = fight.halts.orbs.values()
                    self.assertIsNone(orb.until)
                    self.assertTrue(run.match.switch_hero(orisa, find_hero("Reinhardt")))
                    self.assertFalse(fight.halts.orbs)
                else:
                    self.detonate_halt(run, orisa, fight)
                    retired = owner.body
                    pull = fight.halts.pulls[retired]
                    self.assertTrue(run.match.switch_hero(owner, find_hero("Reinhardt")))
                    self.assertNotEqual(owner.body, retired)
                    self.assertTrue(pull.script.stopped)
                for _ in range(45):
                    run.step({})
                self.assertFalse(fight.halts.pulls)
                self.assertFalse(fight.halts.orbs)
                self.assertFalse(any(instance.graph.index == 0xCCE
                                     for instance in owner.body_script.component.instances.values()))

    def test_actual_halt_filters_cover_range_and_allies(self):
        for case in ("cover", "range", "ally"):
            with self.subTest(case=case):
                run, owner, orisa, fight = self.halt_scene()
                if case == "cover":
                    run.match.collision_world = World([
                        (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                        (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                        (1, 0, 4, 1, 8, 4, 1, 8, 12),
                        (1, 0, 4, 1, 8, 12, 1, 0, 12),
                    ], name="Halt acquisition cover")
                elif case == "range":
                    owner.mover.position[2] = 18
                    owner.mover.body = None
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = orisa.team
                before = tuple(owner.mover.position)
                self.detonate_halt(run, orisa, fight)
                self.assertFalse(fight.halts.pulls)
                for _ in range(35):
                    run.step({})
                self.assertEqual(tuple(owner.mover.position), before)

    def test_actual_halt_wall_impact_detonates_and_keeps_far_side_occluded(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                run, owner, orisa, fight = self.halt_scene()
                owner.mover.position[2] = 6 if covered else 4
                owner.mover.body = None
                run.match.collision_world = World([
                    (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                    (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                    (-10, 0, 5, 10, 8, 5, 10, 0, 5),
                    (-10, 0, 5, -10, 8, 5, 10, 8, 5),
                ], name="Halt wall impact")
                run.step({orisa: (0, 0, 2)})
                for _ in range(45):
                    run.step({})
                    if any(orb.until is not None for orb in fight.halts.orbs.values()):
                        break
                orb, = fight.halts.orbs.values()
                self.assertIsNotNone(orb.until)
                self.assertAlmostEqual(orb.position[2], 5, places=3)
                self.assertEqual(owner.body in fight.halts.pulls, not covered)

    def test_actual_halt_prearming_and_cooldown_refuse_then_recast(self):
        run, _, orisa, fight = self.halt_scene()
        cast = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0xCCB)
        for frame in range(25):
            run.step({orisa: (0, 0, 2 if frame in (0, 15) else 0)})
        orb, = fight.halts.orbs.values()
        self.assertIsNone(orb.until)
        run.step({orisa: (0, 0, 2)})
        self.assertIsNotNone(orb.until)
        for _ in range(34):
            run.step({})
        self.assertFalse(fight.halts.orbs)
        run.step({orisa: (0, 0, 2)})
        for _ in range(25):
            run.step({})
        volley = cast.node_state(cast.graph.node(8))
        self.assertEqual(volley.counter, 1)
        self.assertFalse(fight.halts.orbs)
        for _ in range(390):
            run.step({})
        run.step({orisa: (0, 0, 2)})
        for _ in range(25):
            run.step({})
        self.assertEqual(volley.counter, 2)
        self.assertTrue(fight.halts.orbs)

    def test_actual_lethal_reaper_shell_stops_halt_before_launch(self):
        run, owner, orisa, fight = self.halt_scene()
        owner.mover.position[:] = (0, 1, 1.5)
        owner.mover.body = None
        run.step({})
        target = next(target for target in fight._targets() if target.owner is orisa)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 100)
        run.step({orisa: (0, 0, 2)})
        self.assertFalse(fight.halts.orbs)
        reaper_tests.ReaperTests().fire(run, owner, orisa)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, owner.body)
        for _ in range(50):
            run.step({})
        self.assertFalse(fight.halts.orbs)
        self.assertFalse(fight.halts.pulls)

    def test_actual_halt_is_absorbed_by_front_matrix_or_grasp(self):
        for defense, front in (("Matrix", True), ("Matrix", False), ("Grasp", True), ("Grasp", False)):
            with self.subTest(defense=defense, front=front):
                if defense == "Matrix" and not dva_matrix.eligible(0x040000000000172C):
                    self.skipTest("requires the pinned Halt projectile tag")
                run, owner, orisa, fight = self.halt_scene(extra=(defense,))
                defender = run.players[2]
                hero = (replace(find_hero("dva"), body=pools.DVA_MECH) if defense == "Matrix"
                        else heroes()[sigma_grasp.HERO])
                self.assertTrue(run.match.switch_hero(defender, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                orisa.team, owner.team, defender.team = 0, 1, 1
                defender.mover.position[:] = (0, 1, 6 if front else 10.5)
                defender.mover.body = None
                heading = -32768 if front else 0
                button, graph, index = ((2, dva_matrix.GRAPH, dva_matrix.STATE) if defense == "Matrix"
                                        else (8, sigma_grasp.GRASP, 1))
                for _ in range(75):
                    run.step({})
                for frame in range(12):
                    run.step({defender: (heading, 0, button if defense == "Matrix" or frame == 0 else 0)})
                active = next(i for i in defender.body_script.component.instances.values()
                              if i.graph.index == graph)
                self.assertTrue(active.states[index].active)
                before = tuple(owner.mover.position)
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0),
                              defender: (heading, 0, 2 if defense == "Matrix" else 0)})
                self.assertEqual(owner.body in fight.halts.pulls, not front)
                if front:
                    self.assertFalse(fight.halts.orbs)
                    for _ in range(36):
                        run.step({})
                    self.assertEqual(tuple(owner.mover.position), before)
                else:
                    orb, = fight.halts.orbs.values()
                    self.assertIsNotNone(orb.until)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                if defense == "Grasp":
                    self.assertEqual(active.vars[21312].value(), 0)
                    self.assertNotIn(defender.body, fight.grasp_shields)

    def halt_deflect_scene(self, *, wait_for_reflection=True):
        run, owner, orisa, fight = self.halt_scene(extra=("Genji", "Ally"))
        genji, ally = run.players[2:]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = orisa.team = 0
        genji.team = ally.team = 1
        for actor, position in ((owner, (2, 1, 0)), (genji, (0, 1, 6)), (ally, (-2, 1, 0))):
            actor.mover.position[:] = position
            actor.mover.body = None
        for frame in range(12):
            run.step({genji: (-32768, 2200, 16 if frame == 0 else 0)})
        run.step({orisa: (0, 0, 2), genji: (-32768, 2200, 0)})
        for _ in range(24):
            run.step({genji: (-32768, 2200, 0)})
            if not wait_for_reflection and fight.halts.orbs:
                break
        orb, = fight.halts.orbs.values()
        holder = genji if wait_for_reflection else orisa
        self.assertIs(orb.owner, holder)
        self.assertEqual(orb.owner_body, holder.body)
        self.assertIsNone(orb.until)
        return run, owner, orisa, genji, ally, fight, orb

    def test_actual_halt_reflection_transfers_acquisition_and_rejects_original_press(self):
        run, owner, orisa, genji, ally, fight, orb = self.halt_deflect_scene()
        run.step({orisa: (0, 0, 2), genji: (-32768, 2200, 0)})
        self.assertIsNone(orb.until)
        self.assertTrue(run.match.switch_hero(orisa, find_hero("Reinhardt")))
        self.assertIs(fight.halts.orbs[orb.entity], orb)
        for _ in range(55):
            run.step({})
            if orb.until is not None:
                break
        self.assertIsNotNone(orb.until)
        self.assertIn(owner.body, fight.halts.pulls)
        self.assertNotIn(ally.body, fight.halts.pulls)
        before = math.dist(owner.mover.position, orb.position)
        for _ in range(38):
            run.step({})
        self.assertLess(math.dist(owner.mover.position, orb.position), before - 0.5)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_halt_reflection_retires_on_reflector_body_replacement(self):
        run, owner, _, genji, _, fight, orb = self.halt_deflect_scene()
        before = tuple(owner.mover.position)
        self.assertTrue(run.match.switch_hero(genji, find_hero("Reinhardt")))
        self.assertNotIn(orb.entity, fight.halts.orbs)
        for _ in range(65):
            run.step({})
        self.assertIsNone(orb.until)
        self.assertFalse(fight.halts.pulls)
        self.assertEqual(tuple(owner.mover.position), before)

    def test_actual_halt_flight_gap_keeps_deflect_before_ground_contact(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, _, genji, ally, fight, orb = self.halt_deflect_scene(wait_for_reflection=False)
                before = tuple(owner.mover.position)
                ally_before = tuple(ally.mover.position)
                for _ in range(1 if skipped else 250):
                    run.frame += 250 if skipped else 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertIs(orb.owner, genji)
                self.assertIsNotNone(orb.until)
                self.assertFalse(fight.halts.orbs)
                self.assertFalse(fight.halts.pulls)
                self.assertGreater(math.dist(owner.mover.position, before), 0.5)
                self.assertEqual(tuple(ally.mover.position), ally_before)
                results.append((orb.position, orb.tick, tuple(owner.mover.position)))
        self.assertEqual(results[0], results[1])

    def test_actual_halt_deflect_then_matrix_resolve_in_one_tick(self):
        if not dva_matrix.eligible(0x040000000000172C):
            self.skipTest("requires the pinned Halt projectile tag")
        run, owner, orisa, fight = self.halt_scene(extra=("Genji", "D.Va"))
        genji, mech = run.players[2:]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(mech, replace(find_hero("dva"), body=pools.DVA_MECH)))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = orisa.team = mech.team = 0
        genji.team = 1
        for actor, position in ((genji, (0, 1, 6.13)), (mech, (0, 1, 2))):
            actor.mover.position[:] = position
            actor.mover.body = None
        for _ in range(75):
            run.step({})
        for frame in range(12):
            run.step({genji: (-32768, 0, 16 if frame == 0 else 0), mech: (0, 0, 2)})
        matrix = next(i for i in mech.body_script.component.instances.values()
                      if i.graph.index == dva_matrix.GRAPH)
        self.assertTrue(matrix.states[dva_matrix.STATE].active)
        events = []
        refresh = fight.halts.refresh_source

        def observe(orb):
            events.append((fight.tick, orb.owner, orb.entity in fight.halts.orbs))
            refresh(orb)

        with patch.object(fight.halts, "refresh_source", side_effect=observe):
            for frame in range(45):
                run.step({orisa: (0, 0, 2 if frame == 0 else 0),
                          genji: (-32768, 0, 0), mech: (0, 0, 2)})
                if len(events) == 2:
                    break
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], (events[1][0], genji, True))
        self.assertEqual(events[1][1:], (genji, False))
        self.assertFalse(fight.halts.orbs)
        self.assertFalse(fight.halts.pulls)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_halt_two_deflects_in_one_tick_spend_only_travelled_range(self):
        run, _, orisa, fight = self.halt_scene(extra=("First Genji", "Second Genji"))
        first, second = run.players[2:]
        for actor, z in ((first, 6.13), (second, 4.22)):
            self.assertTrue(run.match.switch_hero(actor, heroes()[0x02E0000000000029]))
            actor.mover.position[:] = (0, 1, z)
            actor.mover.body = None
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        orisa.team = second.team = 0
        first.team = 1
        for frame in range(12):
            button = 16 if frame == 0 else 0
            run.step({first: (-32768, 0, button), second: (0, 0, button)})
        events = []
        refresh = fight.halts.refresh_source

        def observe(orb):
            events.append((fight.tick, orb.owner, orb.position, orb.remaining))
            refresh(orb)

        with patch.object(fight.halts, "refresh_source", side_effect=observe):
            for frame in range(45):
                run.step({orisa: (0, 0, 2 if frame == 0 else 0),
                          first: (-32768, 0, 0), second: (0, 0, 0)})
                if len(events) >= 2:
                    break
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][:2], (events[1][0], first))
        self.assertIs(events[1][1], second)
        orb, = fight.halts.orbs.values()
        self.assertIs(orb.owner, second)
        self.assertIsNone(orb.until)
        travelled_after = math.dist(orb.position, events[1][2])
        self.assertGreater(travelled_after, 0.001)
        self.assertAlmostEqual(events[1][3] - orb.remaining, travelled_after)

    def test_actual_halt_wall_at_step_endpoint_wins_over_active_deflect(self):
        run, owner, orisa, fight = self.halt_scene(extra=("Genji",))
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        for actor, position in ((owner, (2, 1, 1)), (genji, (0, 1, 6))):
            actor.mover.position[:] = position
            actor.mover.body = None
        run.match.collision_world = World([
            (-30, 1, -30, -30, 1, 80, 30, 1, 80),
            (-30, 1, -30, 30, 1, 80, 30, 1, -30),
            (-8, 0, 2, 8, 8, 2, 8, 0, 2),
            (-8, 0, 2, -8, 8, 2, 8, 8, 2),
        ], name="Halt wall before Deflect")
        for frame in range(12):
            run.step({genji: (-32768, 0, 16 if frame == 0 else 0)})
        deflect = next(i for i in genji.body_script.component.instances.values()
                       if i.graph.index == orisa_halt.DEFLECT_GRAPH)
        run.step({orisa: (0, 0, 2), genji: (-32768, 0, 0)})
        for _ in range(26):
            run.step({genji: (-32768, 0, 0)})
            if any(orb.until is not None for orb in fight.halts.orbs.values()):
                break
        orb, = fight.halts.orbs.values()
        self.assertTrue(deflect.states[10].active)
        self.assertIsNotNone(orb.until)
        self.assertAlmostEqual(orb.position[2], 2, places=3)
        self.assertIs(orb.owner, orisa)
        self.assertIn(owner.body, fight.halts.pulls)
        self.assertNotIn(genji.body, fight.halts.pulls)

    def charge_scene(self, *, names=("Reaper", "Reinhardt"), hero="Reinhardt", wall=11):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(caster, find_hero(hero)))
        fight = run.match.combat
        for actor, position in ((caster, (0, 1, 0)), (owner, (0, 1, 6))):
            actor.mover.position[:] = position
            actor.mover.body = None
        geometry = [
            (-30, 1, -30, -30, 1, 80, 30, 1, 80),
            (-30, 1, -30, 30, 1, 80, 30, 1, -30),
        ]
        if wall is not None:
            geometry.extend((
                (-15, -5, wall, 15, 10, wall, 15, -5, wall),
                (-15, -5, wall, -15, 10, wall, 15, 10, wall),
            ))
        run.match.collision_world = World(geometry, name="Horizontal charge lane")
        for _ in range(12):
            run.step({})
        return run, owner, caster, fight

    def supercharger_scene(self, *, extra=()):
        if not orisa_supercharger.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Supercharger supplement")
        run, owner, orisa, fight = self.charge_scene(
            hero="Orisa", names=("Reaper", "Orisa", "Enemy", *extra), wall=None,
        )
        enemy = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = orisa.team = 0
        enemy.team = 1
        owner.mover.position[:] = (2, 1, 0)
        enemy.mover.position[:] = (2, 1, 1.5)
        owner.mover.body = enemy.mover.body = None
        script = orisa.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        run.step({orisa: (0, 0, 32)})
        step = cost * script.frame_ms / (1000 * script.ult_seconds)
        self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(), step, places=5)
        for _ in range(90):
            run.step({})
            if fight.superchargers.active:
                break
        device, = fight.superchargers.active.values()
        self.assertEqual(device.health, 200)
        self.assertEqual(fight.superchargers.scale(owner), 1.5)
        self.assertEqual(fight.superchargers.scale(enemy), 1)
        status = fight.superchargers.buffs[device.entity, owner.body]
        self.assertEqual(status.graph.index, 0xD01)
        self.assertTrue(status.state(3).active)
        return run, owner, enemy, fight, device

    def test_actual_supercharger_boosts_reaper_shell_charge_and_reaping_until_expiry(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, enemy, fight, device = self.supercharger_scene()
        health = fight.vitals[owner.body]
        health.damage(100)
        victim = fight.vitals[enemy.body]
        charge = owner.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        fixture.fire(run, owner, enemy)
        self.assertEqual(victim.total, 20)
        self.assertAlmostEqual(health.total, 213, places=4)
        self.assertAlmostEqual(charge.value() - before, 180, places=4)
        run.frame = device.until
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(fight.superchargers.active)
        self.assertFalse(fight.superchargers.buffs)
        self.assertEqual(fight.superchargers.scale(owner), 1)
        victim.heal(180)
        fixture.fire(run, owner, enemy)
        self.assertEqual(victim.total, 80)

    def test_actual_supercharger_boosts_every_blossom_pulse_without_damage_charge(self):
        run, owner, enemy, fight, device = self.supercharger_scene()
        fight.vitals[enemy.body] = victim = combat.Vitals(enemy.body, 1000)
        health = fight.vitals[owner.body]
        health.damage(200)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(190):
            run.step({})
        self.assertEqual(victim.total, 235)
        self.assertEqual(health.total, 250)
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertIn(device.entity, fight.superchargers.active)

    def test_actual_enemy_reaper_destroys_supercharger_without_reaping_or_charge(self):
        run, owner, enemy, fight, device = self.supercharger_scene()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        x, y, z = device.position
        enemy.mover.position[:] = (x - 1.5, y, z)
        enemy.mover.body = None
        run.step({})
        attacker = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        attacker.damage(100)
        charge = enemy.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        heading = aim(fight._eye(enemy), (x, y + 0.7, z))
        run.step({enemy: (*heading, 1)})
        self.assertLess(device.health, 200)
        self.assertEqual(charge.value(), before)
        self.assertEqual(attacker.total, 150)
        for _ in range(4):
            if device.entity not in fight.superchargers.active:
                break
            for _ in range(35):
                run.step({})
            run.step({enemy: (*heading, 1)})
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.entity, fight.superchargers.active)
        self.assertEqual(fight.superchargers.scale(owner), 1)
        self.assertEqual(attacker.total, 150)
        self.assertFalse(device.component.instances)

    def test_actual_helix_hits_supercharger_mesh_or_splashes_visible_surface(self):
        for case in ("direct", "splash", "cover", "ally", "grasp"):
            with self.subTest(case=case):
                extra = ("Sigma",) if case == "grasp" else ()
                run, owner, soldier, fight, device = self.supercharger_scene(extra=extra)
                x, y, z = device.position
                for ally in (owner, device.owner):
                    ally.mover.position[:] = (x + 12, y, z)
                    ally.mover.body = None
                soldier.mover.position[:] = (x - 4, y, z)
                soldier.mover.body = None
                defender_input = {}
                if case == "grasp":
                    sigma = run.players[3]
                    self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
                    sigma.team = owner.team
                    sigma.mover.position[:] = (x - 1.8, y, z)
                    sigma.mover.body = None
                    facing = aim(fight._eye(sigma), fight._eye(soldier))
                    run.step({sigma: (*facing, 8)})
                    defender_input = {sigma: (*facing, 0)}
                    for _ in range(12):
                        run.step(defender_input)
                    grasp = next(i for i in sigma.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertTrue(grasp.states[1].active)
                if case == "ally":
                    soldier.team = owner.team
                if case == "cover":
                    wall = x - 0.85
                    run.match.collision_world = World([
                        (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                        (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                        (wall, 0, z - 5, wall, 8, z - 5, wall, 8, z + 5),
                        (wall, 0, z - 5, wall, 8, z + 5, wall, 0, z + 5),
                    ], name="Supercharger Helix cover")
                run.step(defender_input)
                soldier.body_script.ult_seconds = 0
                charge = soldier.body_script.component.entity_var(ULT_CHARGE)
                before = charge.value()
                point = (x - 1.25, y, z) if case == "splash" else (x, y + 0.7, z)
                heading = aim(fight._eye(soldier), point)
                run.step({soldier: (*heading, 2), **defender_input})
                rocket = None
                for _ in range(60):
                    rocket = next((r for r in fight.rockets if r.definition == 0x040000000000072C), rocket)
                    if rocket is not None and rocket not in fight.rockets:
                        break
                    run.step({soldier: (*heading, 0), **defender_input})
                self.assertIsNotNone(rocket)
                self.assertNotIn(rocket, fight.rockets)
                expected = (60, 120) if case == "ally" else (40, 80)
                self.assertEqual((rocket.direct.amount, rocket.blast.amount), expected)
                self.assertEqual(charge.value(), before)
                self.assertEqual(fight.superchargers.scale(owner), 1.5)
                if case == "splash":
                    self.assertEqual(device.health, 120)
                elif case == "direct":
                    self.assertEqual(device.health, 80)
                    self.assertTrue(run.match.switch_hero(soldier, heroes()[reaper.HERO]))
                    soldier.mover.position[:] = (x - 1.5, y, z)
                    soldier.mover.body = None
                    run.step({})
                    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is soldier))
                    health.damage(100)
                    charge = soldier.body_script.component.entity_var(ULT_CHARGE)
                    before = charge.value()
                    heading = aim(fight._eye(soldier), point)
                    run.step({soldier: (*heading, 1)})
                    self.assertEqual(device.health, 0)
                    self.assertEqual(fight.superchargers.scale(owner), 1)
                    self.assertEqual(health.total, 150)
                    self.assertEqual(charge.value(), before)
                else:
                    self.assertEqual(device.health, 200)
                if case == "grasp":
                    self.assertEqual(grasp.vars[21312].value(), 120)

    def test_actual_reflected_helix_can_hit_original_shooters_supercharger(self):
        run, owner, soldier, fight, device = self.supercharger_scene(extra=("Genji",))
        genji = run.players[3]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        soldier.team, genji.team = owner.team, 1
        x, y, z = device.position
        for actor, position in (
            (owner, (x + 12, y, z)),
            (device.owner, (x + 12, y, z + 2)),
            (soldier, (x - 2, y, z + 1.4)),
            (genji, (x - 6, y, z)),
        ):
            actor.mover.position[:] = position
            actor.mover.body = None
        run.step({})
        self.assertEqual(fight.superchargers.scale(soldier), 1.5)
        self.assertEqual(fight.superchargers.scale(genji), 1)
        forward = aim(fight._eye(genji), (x, y + 0.65, z - 0.25))
        for frame in range(12):
            run.step({genji: (*forward, 16 if frame == 0 else 0)})
        incoming = aim(fight._eye(soldier), (x - 6, y + 1.45, z))
        run.step({soldier: (*incoming, 2), genji: (*forward, 0)})
        original, = fight.rockets
        self.assertEqual((original.direct.amount, original.blast.amount), (60, 120))
        soldier.mover.position[2] = z + 40
        soldier.mover.body = None
        for _ in range(20):
            if any(rocket.reflected for rocket in fight.rockets):
                break
            run.step({genji: (*forward, 0)})
        returned, = fight.rockets
        self.assertTrue(returned.reflected)
        self.assertIs(returned.shooter, genji)
        self.assertEqual(fight.superchargers.scale(soldier), 1)
        self.assertEqual((returned.direct.amount, returned.blast.amount), (40, 80))
        self.assertEqual(device.health, 200)
        for _ in range(20):
            run.step({})
            if returned not in fight.rockets:
                break
        self.assertNotIn(returned, fight.rockets)
        self.assertEqual(device.health, 80)
        self.assertEqual(fight.superchargers.scale(owner), 1.5)

    def test_actual_helix_reflected_by_boosted_genji_hits_reaper(self):
        run, owner, soldier, fight, device = self.supercharger_scene(extra=("Genji",))
        genji = run.players[3]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        owner.team, genji.team = soldier.team, device.team
        x, y, z = device.position
        for actor, position in (
            (owner, (x + 8, y, z)),
            (soldier, (x + 10, y, z + 1.4)),
            (genji, (x + 14, y, z)),
        ):
            actor.mover.position[:] = position
            actor.mover.body = None
        run.step({})
        self.assertEqual(fight.superchargers.scale(soldier), 1)
        self.assertEqual(fight.superchargers.scale(genji), 1.5)
        forward = aim(fight._eye(genji), (x + 8, y + 1.2, z - 0.25))
        for frame in range(12):
            run.step({genji: (*forward, 16 if frame == 0 else 0)})
        incoming = aim(fight._eye(soldier), (x + 14, y + 1.45, z))
        run.step({soldier: (*incoming, 2), genji: (*forward, 0)})
        original, = fight.rockets
        self.assertEqual((original.direct.amount, original.blast.amount), (40, 80))
        for _ in range(20):
            if any(rocket.reflected for rocket in fight.rockets):
                break
            run.step({genji: (*forward, 0)})
        returned, = fight.rockets
        self.assertTrue(returned.reflected)
        self.assertIs(returned.shooter, genji)
        self.assertEqual((returned.direct.amount, returned.blast.amount), (60, 120))
        for _ in range(20):
            if returned not in fight.rockets:
                break
            run.step({})
        self.assertNotIn(returned, fight.rockets)
        self.assertEqual(fight.vitals[owner.body].total, 70)
        self.assertEqual(device.health, 200)

    def test_actual_reflected_reaper_shell_uses_genjis_supercharger_bonus(self):
        for boosted_reflector in (False, True):
            with self.subTest(boosted_reflector=boosted_reflector):
                run, owner, genji, fight, device = self.supercharger_scene()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                if boosted_reflector:
                    owner.team, genji.team = 1, 0
                x, y, z = device.position
                owner.mover.position[:] = (x + 8, y, z)
                genji.mover.position[:] = (x + 8, y, z + 1.5)
                owner.mover.body = genji.mover.body = None
                run.step({})
                self.assertEqual(fight.superchargers.scale(owner), 1 if boosted_reflector else 1.5)
                self.assertEqual(fight.superchargers.scale(genji), 1.5 if boosted_reflector else 1)
                forward = (-32768, 2200)
                for frame in range(12):
                    run.step({genji: (*forward, 16 if frame == 0 else 0)})
                heading = aim(fight._eye(owner), (x + 8, y + 1, z + 1.5))
                charge = owner.body_script.component.entity_var(ULT_CHARGE)
                before = charge.value()
                run.step({owner: (*heading, 1), genji: (*forward, 0)})
                self.assertEqual(fight.vitals[owner.body].total, 70 if boosted_reflector else 130)
                target = next(target for target in fight._targets() if target.owner is genji)
                self.assertEqual(fight.vitals_of(target).total, 200)
                self.assertEqual(charge.value(), before)

    def test_actual_supercharger_reacquires_reaper_after_range_and_cover_exit(self):
        run, owner, _, fight, device = self.supercharger_scene()
        x, y, z = device.position
        initial = tuple(owner.mover.position)
        owner.mover.position[:] = (x + 40, y, z)
        owner.mover.body = None
        run.step({})
        self.assertEqual(fight.superchargers.scale(owner), 1)
        owner.mover.position[:] = initial
        owner.mover.body = None
        run.step({})
        self.assertEqual(fight.superchargers.scale(owner), 1.5)
        open_world = run.match.collision_world
        wall = (initial[0] + x) / 2
        run.match.collision_world = World([
            (-30, 1, -30, -30, 1, 80, 30, 1, 80),
            (-30, 1, -30, 30, 1, 80, 30, 1, -30),
            (wall, 0, -5, wall, 8, -5, wall, 8, 5),
            (wall, 0, -5, wall, 8, 5, wall, 0, 5),
        ], name="Supercharger sight blocker")
        run.step({})
        self.assertEqual(fight.superchargers.scale(owner), 1)
        run.match.collision_world = open_world
        run.step({})
        self.assertEqual(fight.superchargers.scale(owner), 1.5)

    def test_actual_reaper_melee_uses_supercharger_mesh_range_cover_and_team(self):
        for case in ("enemy", "ally", "range", "cover"):
            with self.subTest(case=case):
                run, owner, enemy, fight, device = self.supercharger_scene()
                attacker = owner if case == "ally" else enemy
                if attacker is enemy:
                    self.assertTrue(run.match.switch_hero(attacker, heroes()[reaper.HERO]))
                x, y, z = device.position
                attacker.mover.position[:] = (x - (4 if case == "range" else 1.5), y, z)
                attacker.mover.body = None
                for actor in run.players:
                    if actor is not attacker:
                        actor.mover.position[:] = (x + 12, y, z)
                        actor.mover.body = None
                if case == "cover":
                    wall = x - 0.85
                    run.match.collision_world = World([
                        (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                        (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                        (wall, 0, z - 5, wall, 8, z - 5, wall, 8, z + 5),
                        (wall, 0, z - 5, wall, 8, z + 5, wall, 0, z + 5),
                    ], name="Supercharger melee cover")
                run.step({})
                health = fight.vitals[attacker.body]
                health.damage(100)
                script = attacker.body_script
                charge = script.component.entity_var(ULT_CHARGE)
                before = charge.value()
                heading = aim(fight._eye(attacker), (x, y + 0.7, z))
                run.step({attacker: (*heading, 0x800)})
                for _ in range(30):
                    run.step({attacker: (*heading, 0)})
                self.assertEqual(device.health, 170 if case == "enemy" else 200)
                self.assertEqual(health.total, 150)
                self.assertEqual(charge.value(), before)

    def test_actual_enemy_blossom_destroys_supercharger_on_sixth_pulse(self):
        run, owner, enemy, fight, device = self.supercharger_scene()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        x, y, z = device.position
        enemy.mover.position[:] = (x - 1.5, y, z)
        for ally in (owner, device.owner):
            ally.mover.position[:] = (x + 12, y, z)
        for actor in (enemy, owner, device.owner):
            actor.mover.body = None
        run.step({})
        self.assertEqual(fight.superchargers.scale(owner), 1.5)
        health = fight.vitals[enemy.body]
        health.damage(100)
        enemy.body_script.set_ult_charge(2100)
        run.step({enemy: (0, 0, 32)})
        pools = [200, device.health] if device.health < 200 else [200]
        for _ in range(190):
            run.step({})
            if pools[-1] != device.health:
                pools.append(device.health)
            if not device.health:
                self.assertEqual(fight.superchargers.scale(owner), 1)
        self.assertEqual(pools, [200, 166, 132, 98, 64, 30, 0])
        self.assertFalse(device.component.instances)
        self.assertEqual(health.total, 150)
        self.assertEqual(enemy.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_blossom_reacquires_supercharger_after_cover_without_banked_pulses(self):
        run, owner, enemy, _, device = self.supercharger_scene()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        x, y, z = device.position
        enemy.mover.position[:] = (x - 1.5, y, z)
        for ally in (owner, device.owner):
            ally.mover.position[:] = (x + 12, y, z)
        for actor in (enemy, owner, device.owner):
            actor.mover.body = None
        open_world = run.match.collision_world
        wall = x - 0.85
        run.match.collision_world = World([
            (-30, 1, -30, -30, 1, 80, 30, 1, 80),
            (-30, 1, -30, 30, 1, 80, 30, 1, -30),
            (wall, 0, z - 5, wall, 8, z - 5, wall, 8, z + 5),
            (wall, 0, z - 5, wall, 8, z + 5, wall, 0, z + 5),
        ], name="Supercharger Blossom cover")
        run.step({})
        enemy.body_script.set_ult_charge(2100)
        run.step({enemy: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertIsNotNone(reaper.blossom_state(enemy.body_script))
        self.assertEqual(device.health, 200)
        run.match.collision_world = open_world
        run.step({})
        self.assertEqual(device.health, 166)
        for _ in range(5):
            run.step({})
        self.assertEqual(device.health, 166)

    def test_actual_reaper_shell_interrupts_supercharger_windup(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, orisa, fight = self.charge_scene(hero="Orisa", wall=None)
        owner.mover.position[:] = (0, 1, 1.5)
        owner.mover.body = None
        script = orisa.body_script
        script.ult_seconds = 0  # Isolate cast cost from the generic passive clock.
        cast = next(instance for instance in script.component.instances.values()
                    if instance.graph.index == orisa_supercharger.GRAPH)
        script.set_ult_charge(0)
        run.step({orisa: (0, 0, 32)})
        for _ in range(40):
            run.step({})
        volley = cast.node_state(cast.graph.node(29))
        self.assertEqual(volley.counter, 0)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({orisa: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(10):
            run.step({})
        self.assertEqual(volley.counter, 0)
        victim = fight.vitals_of(next(target for target in fight._targets() if target.owner is orisa))
        victim.damage(victim.total - 100)
        fixture.fire(run, owner, orisa)
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, owner.body)
        for _ in range(90):
            run.step({})
        self.assertEqual(volley.counter, 0)
        self.assertFalse(fight.superchargers.flights)
        self.assertFalse(fight.superchargers.active)
        self.assertFalse(fight.superchargers.buffs)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_blossom_loses_supercharger_boost_on_expiry_mid_channel(self):
        run, owner, enemy, fight, device = self.supercharger_scene()
        fight.vitals[enemy.body] = victim = combat.Vitals(enemy.body, 1000)
        run.frame = device.until - 30
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight.superchargers.scale(owner), 1.5)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(190):
            run.step({})
        self.assertFalse(fight.superchargers.active)
        self.assertEqual(fight.superchargers.scale(owner), 1)
        self.assertEqual(victim.total, 1000 - 3 * 51 - 12 * 34)

    def supercharger_flash_scene(self):
        if not orisa_supercharger.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Supercharger supplement")
        run, owner, orisa, fight = self.charge_scene(
            hero="Orisa", names=("Reaper", "Orisa", "McCree"), wall=None,
        )
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = orisa.team = 0
        caster.team = 1
        caster.mover.position[:] = (0, 1, -3)
        caster.mover.body = None
        orisa.body_script.ult_seconds = 0
        fight.vitals[orisa.body] = combat.Vitals(orisa.body, 450)
        return run, owner, orisa, caster, fight

    def test_actual_flashbang_interrupts_supercharger_unless_fortified(self):
        for fortified in (False, True):
            with self.subTest(fortified=fortified):
                run, owner, orisa, caster, fight = self.supercharger_flash_scene()
                script = orisa.body_script
                pool = fight.vitals[orisa.body]
                run.step({orisa: (0, 0, 8)} if fortified else {})
                charge = script.component.entity_var(ULT_CHARGE)
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost)
                cast = next(instance for instance in script.component.instances.values()
                            if instance.graph.index == orisa_supercharger.GRAPH)
                self.step(run, caster, orisa, cast=16, target_buttons=32)
                self.assertEqual(charge.value(), 0)
                pending = cast.node_state(cast.graph.node(30))
                self.assertTrue(pending.active)
                for _ in range(40):
                    if pool.total < 450:
                        break
                    self.step(run, caster, orisa, target_buttons=32)
                self.assertAlmostEqual(pool.total, 435 if fortified else 425, places=5)
                self.assertEqual(fight.is_stunned(orisa), not fortified)
                self.assertEqual(pending.active, fortified)
                for _ in range(90):
                    self.step(run, caster, orisa, target_buttons=32)
                self.assertEqual(bool(fight.superchargers.active), fortified)
                self.assertEqual(fight.superchargers.scale(owner), 1.5 if fortified else 1)
                self.assertEqual(charge.value(), 0)
                if not fortified:
                    # A fresh empty press is refused. A later charged press can deploy.
                    self.step(run, caster, orisa)
                    self.step(run, caster, orisa, target_buttons=32)
                    for _ in range(35):
                        self.step(run, caster, orisa)
                    self.assertFalse(fight.superchargers.active)
                    self.assertEqual(charge.value(), 0)
                    script.set_ult_charge(cost)
                    self.step(run, caster, orisa, target_buttons=32)
                    for _ in range(90):
                        self.step(run, caster, orisa)
                        if fight.superchargers.active:
                            break
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(fight.superchargers.scale(owner), 1.5)

    def test_actual_flashbang_after_supercharger_throw_preserves_flight_and_landing(self):
        run, owner, orisa, caster, fight = self.supercharger_flash_scene()
        script = orisa.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        x, y, z = orisa.mover.position
        heading = aim(fight._eye(orisa), (x, y + 10, z + 5))
        run.step({orisa: (*heading, 32)})
        for _ in range(60):
            if orisa.body in fight.superchargers.flights:
                break
            run.step({orisa: (*heading, 0)})
        flight = fight.superchargers.flights[orisa.body]
        self.throw(run, caster, orisa)
        self.assertIs(fight.superchargers.flights[orisa.body], flight)
        self.assertFalse(fight.superchargers.active)
        self.assertEqual(fight.superchargers.scale(owner), 1)
        for _ in range(120):
            run.step({})
            if fight.superchargers.active:
                break
        self.assertFalse(fight.superchargers.flights)
        device, = fight.superchargers.active.values()
        self.assertIs(device.owner, orisa)
        self.assertEqual(fight.superchargers.scale(owner), 1.5)

    def test_actual_flashbang_after_supercharger_landing_preserves_ally_boost(self):
        for nearby in (False, True):
            with self.subTest(nearby=nearby):
                run, owner, caster, fight, device = self.supercharger_scene()
                orisa = device.owner
                self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
                if not nearby:
                    x, y, z = device.position
                    orisa.mover.position[:] = (x + 6, y, z)
                    orisa.mover.body = None
                x, y, z = orisa.mover.position
                caster.mover.position[:] = (x, y, z - 3)
                caster.mover.body = None
                run.step({})
                deadline = device.until
                self.throw(run, caster, orisa)
                self.assertTrue(fight.is_stunned(orisa))
                self.assertIs(fight.superchargers.active[device.entity], device)
                self.assertEqual(device.until, deadline)
                self.assertEqual(device.health, 175 if nearby else 200)
                self.assertEqual(fight.superchargers.scale(owner), 1.5)

    def launch_field(self, launch_x, *, extra=()):
        run, owner, bap, fight = self.charge_scene(
            hero="Baptiste", names=("Reaper", "Baptiste", *extra), wall=None,
        )
        bap.mover.position[:] = (launch_x, 1, 1.5)
        bap.mover.body = None
        heading = aim(fight._eye(bap), (launch_x, 1, 1.0))
        run.step({bap: (*heading, 16)})
        return run, owner, bap, fight, heading

    def field_scene(self, *, launch_x=2, extra=()):
        run, owner, bap, fight, heading = self.launch_field(launch_x, extra=extra)
        for _ in range(90):
            run.step({bap: (*heading, 0)})
            if bap.body in fight.baptiste_fields.devices:
                break
        device = fight.baptiste_fields.devices[bap.body]
        self.assertEqual(device.health, 150)
        self.assertEqual(device.floor_fraction, 0.1)
        instance, = device.component.instances.values()
        self.assertEqual(instance.graph.index, 0x1762)
        self.assertEqual(instance.vars[1165].value(), 10)
        bap.mover.position[:] = (0, 1, 1.5)
        bap.mover.body = None
        for _ in range(3):
            run.step({})
        return run, owner, bap, fight, device

    def test_actual_shell_stops_at_immortality_floor_then_kills_after_expiry(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, bap, fight, device = self.field_scene()
        enemy = fight.vitals[bap.body]
        enemy.damage(enemy.total - 40)
        health = fight.vitals[owner.body]
        health.damage(100)
        charge = owner.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        fixture.fire(run, owner, bap)
        self.assertAlmostEqual(enemy.total, 20)
        self.assertFalse(fight.is_dead(bap))
        self.assertEqual(device.health, 150)
        self.assertAlmostEqual(health.total, 157, places=4)
        self.assertAlmostEqual(charge.value() - before, 20, places=4)
        run.frame += 400
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertNotIn(bap.body, fight.baptiste_fields.devices)
        self.assertFalse(device.component.instances)
        fixture.fire(run, owner, bap)
        self.assertTrue(fight.is_dead(bap))
        self.assertEqual(enemy.killer, owner.body)
        self.assertAlmostEqual(health.total, 164, places=4)

    def test_actual_reaper_shells_destroy_field_without_reaping_from_the_device(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, bap, fight, device = self.field_scene()
        enemy = fight.vitals[bap.body]
        enemy.damage(enemy.total - 40)
        health = fight.vitals[owner.body]
        health.damage(100)
        heading = aim(fight._eye(owner), device.centre())
        charge = owner.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        run.step({owner: (*heading, 1)})
        self.assertLess(device.health, 150)
        self.assertGreater(device.health, 0)
        self.assertEqual(charge.value(), before)
        self.assertEqual(health.total, 150)
        for _ in range(2):
            for _ in range(35):
                run.step({})
            run.step({owner: (*heading, 1)})
            if not device.health:
                break
        self.assertEqual(device.health, 0)
        self.assertEqual(health.total, 150)
        self.assertEqual(enemy.total, 40)
        for _ in range(35):
            run.step({})
        self.assertNotIn(bap.body, fight.baptiste_fields.devices)
        fixture.fire(run, owner, bap)
        self.assertTrue(fight.is_dead(bap))
        self.assertEqual(enemy.killer, owner.body)
        self.assertAlmostEqual(health.total, 164, places=4)

    def test_actual_reaper_melee_damages_immortality_device_without_reaping(self):
        run, owner, bap, fight, device = self.field_scene()
        x, y, z = device.position
        owner.mover.position[:] = (x, y, z - 1.25)
        owner.mover.body = None
        health = fight.vitals[owner.body]
        health.damage(100)
        heading = aim(fight._eye(owner), device.centre())
        run.step({owner: (*heading, 0x800)})
        for _ in range(30):
            run.step({owner: (*heading, 0)})
        self.assertEqual(device.health, 120)
        self.assertEqual(fight.vitals[bap.body].total, 200)
        self.assertEqual(health.total, 150)

    def test_actual_immortality_field_restores_only_visible_in_range_ally_reaper_to_floor(self):
        for case in ("ally", "enemy", "range", "cover"):
            with self.subTest(case=case):
                run, owner, bap, fight, device = self.field_scene()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                bap.team, owner.team = 0, int(case == "enemy")
                if case == "range":
                    owner.mover.position[2] = 20
                    owner.mover.body = None
                elif case == "cover":
                    run.match.collision_world = World([
                        (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                        (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                        (1, 0, -5, 1, 8, -5, 1, 8, 5),
                        (1, 0, -5, 1, 8, 5, 1, 0, 5),
                    ], name="Immortality Field cover")
                health = fight.vitals[owner.body]
                health.damage(240)
                self.assertEqual(health.total, 10)
                run.step({})
                self.assertEqual(health.total, 25 if case == "ally" else 10)
                self.assertEqual(device.health, 150)
                self.assertFalse(fight.is_dead(owner))

    def test_actual_blossom_breaks_immortality_field_then_kills_protected_baptiste(self):
        run, owner, bap, fight, device = self.field_scene()
        enemy = fight.vitals[bap.body]
        enemy.damage(enemy.total - 40)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        observed_floor = False
        for _ in range(105):
            run.step({})
            if device.health > 0 and enemy.total == 20:
                observed_floor = True
                self.assertFalse(fight.is_dead(bap))
            if enemy.dead:
                break
        self.assertTrue(observed_floor)
        self.assertEqual(device.health, 0)
        self.assertTrue(enemy.dead)
        self.assertEqual(enemy.killer, owner.body)

    def test_actual_blossom_at_field_landing_obeys_authored_target_scope(self):
        _, _, _, _, reference = self.field_scene(launch_x=8)
        instance, = reference.component.instances.values()
        landing = instance.states[7].start // reference.component.frame_ms
        for edge in ("before_landing", "on_landing", "disabled_tracking"):
            with self.subTest(edge=edge):
                run, owner, bap, fight, heading = self.launch_field(8)
                self.assertIn(bap.body, fight.baptiste_fields.flights)
                bap.mover.position[:] = (0, 1, 1.5)
                bap.mover.body = None
                cast = landing - 1 if edge == "before_landing" else landing
                while run.frame < cast - 1:
                    run.step({bap: (*heading, 0)})
                target = next(t for t in fight._targets() if t.owner is bap)
                enemy = fight.vitals_of(target)
                enemy.damage(enemy.total - 25)
                own = fight.vitals[owner.body]
                own.damage(100)
                if edge == "disabled_tracking":
                    run.step({bap: (*heading, 0)})
                    device = fight.baptiste_fields.devices[bap.body]
                    device.component.set(8912, True)
                    device.component.run_now()
                    instance, = device.component.instances.values()
                    self.assertTrue(instance.states[7].active)
                    self.assertFalse(instance.states[4].active)
                    cast += 1
                owner.body_script.set_ult_charge(2100)

                run.step({owner: (0, 0, 32), bap: (*heading, 0)})

                self.assertEqual(run.frame, cast)
                if edge == "on_landing":
                    device = fight.baptiste_fields.devices[bap.body]
                    instance, = device.component.instances.values()
                    self.assertTrue(instance.states[4].active)
                    self.assertTrue(instance.states[13].active)
                    self.assertEqual(enemy.total, 20)
                    self.assertAlmostEqual(own.total, 151.75, places=4)
                    self.assertEqual(device.health, 150)
                else:
                    self.assertTrue(enemy.dead)
                    self.assertEqual(enemy.death_tick, cast)
                    self.assertEqual(enemy.killer, owner.body)
                    self.assertAlmostEqual(own.total, 158.75, places=4)
                    if edge == "before_landing":
                        self.assertNotIn(bap.body, fight.baptiste_fields.devices)
                    else:
                        self.assertIs(fight.baptiste_fields.devices[bap.body], device)
                        self.assertEqual(device.health, 150)
                self.assertEqual(owner.body_script.component.vars[ULT_CHARGE].value(), 0)

    def test_actual_blossom_hits_on_field_protection_expiry_before_device_removal(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, bap, fight, device = self.field_scene(launch_x=8)
                component = device.component
                instance, = component.instances.values()
                wait = instance.states[7]
                self.assertTrue(wait.active)
                timeout = wait.evaluate(wait.node.field("m_timeout"), watch=False)
                self.assertEqual(timeout, 5)
                expiry = int((wait.start + timeout * 1000) // component.frame_ms)
                pulse_ticks = math.ceil(reaper.BLOSSOM_INTERVAL / combat.TICK)
                run.frame = expiry - pulse_ticks - 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                target = next(t for t in fight._targets() if t.owner is bap)
                self.assertIs(fight.baptiste_fields.protecting_device(target), device)
                enemy = fight.vitals_of(target)
                enemy.damage(enemy.total - 40)
                own = fight.vitals[owner.body]
                own.damage(100)
                owner.body_script.set_ult_charge(2100)

                run.step({owner: (0, 0, 32)})

                channel = fight.reaper_blossoms[owner.body]
                self.assertEqual(math.ceil(channel["targets"][bap.body] / combat.TICK - 1e-8), expiry)
                self.assertEqual(enemy.total, 20)
                self.assertAlmostEqual(own.total, 157, places=4)
                self.assertEqual(device.health, 150)
                if skipped:
                    run.frame = expiry
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    while run.frame < expiry - 1:
                        run.step({})
                    self.assertEqual(enemy.total, 20)
                    self.assertTrue(wait.active)
                    run.step({})

                self.assertFalse(wait.active)
                self.assertIs(fight.baptiste_fields.devices[bap.body], device)
                self.assertEqual(device.health, 150)
                self.assertTrue(enemy.dead)
                self.assertEqual(enemy.death_tick, expiry)
                self.assertEqual(enemy.killer, owner.body)
                self.assertAlmostEqual(own.total, 164, places=4)
                self.assertEqual(owner.body_script.component.vars[ULT_CHARGE].value(), 0)
                for _ in range(40):
                    run.step({})
                self.assertNotIn(bap.body, fight.baptiste_fields.devices)

    def blossom_mercy_field_trace(self, starting_health, *, skipped):
        run, owner, bap, fight, device = self.field_scene(launch_x=8, extra=("Mercy",))
        mercy = run.players[2]
        self.assertTrue(run.match.switch_hero(mercy, heroes()[mercy_beam.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = mercy.team = 0
        bap.team = 1
        x, y, z = owner.mover.position
        mercy.mover.position[:] = (x - 3, y, z)
        mercy.mover.body = None
        instance, = device.component.instances.values()
        wait = instance.states[7]
        timeout = wait.evaluate(wait.node.field("m_timeout"), watch=False)
        expiry = int((wait.start + timeout * 1000) // device.component.frame_ms)
        pulse_ticks = math.ceil(reaper.BLOSSOM_INTERVAL / combat.TICK)
        run.frame = expiry - pulse_ticks - 16
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        beam = {mercy: (*aim(fight._eye(mercy), (x, y + 1, z)), 2)}
        for _ in range(15):
            run.step(beam)
        self.assertEqual(mercy_beam.factor(fight, owner), 1.3)
        target = next(target for target in fight._targets() if target.owner is bap)
        self.assertIs(fight.baptiste_fields.protecting_device(target), device)
        enemy = fight.vitals_of(target)
        enemy.damage(enemy.total - starting_health)
        own = fight.vitals[owner.body]
        own.damage(100)
        script = owner.body_script
        script.ult_seconds = mercy.body_script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        mercy.body_script.component.set(12918, 0)
        mercy.body_script.set_ult_charge(0)
        boost_charge = mercy.body_script.component.entity_var(ULT_CHARGE)
        run.step(beam | {owner: (0, 0, 32)})
        first = run.frame
        spent = starting_health - 20
        boost = max(0, spent - 34)
        self.assertEqual(enemy.total, 20)
        self.assertAlmostEqual(own.total, 150 + spent * reaper.REAPING_RATIO)
        self.assertAlmostEqual(boost_charge.value(), boost, places=4)
        self.assertEqual(device.health, 150)

        frame = mercy.commands.last_frame
        while run.frame < expiry:
            run.frame = expiry if skipped else run.frame + 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(mercy.commands.last_frame, frame)
        self.assertFalse(wait.active)
        self.assertEqual(mercy_beam.factor(fight, owner), 1)
        self.assertIs(fight.baptiste_fields.devices[bap.body], device)
        self.assertEqual(device.health, 150)
        self.assertEqual((enemy.death_tick, enemy.killer), (expiry, owner.body))
        self.assertEqual(len(bap.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertAlmostEqual(own.total, 150 + starting_health * reaper.REAPING_RATIO)
        self.assertAlmostEqual(boost_charge.value(), boost, places=4)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        hits = [(hit["+0x84"], hit["+0x78"]["+0x4"]) for hit in owner.client.messages(combat.HIT)]
        self.assertEqual(hits, [(first, spent), (expiry, 20)])
        return hits, own.total, boost_charge.value()

    def test_actual_blossom_credits_only_beam_damage_above_immortality_floor(self):
        for starting_health in (50, 60):
            with self.subTest(starting_health=starting_health):
                stepped = self.blossom_mercy_field_trace(starting_health, skipped=False)
                self.assertEqual(stepped, self.blossom_mercy_field_trace(starting_health, skipped=True))

    def test_actual_helix_resolves_field_protection_expiry_across_world_gap(self):
        def scene():
            run, owner, bap, fight, device = self.field_scene(launch_x=8, extra=("Soldier",))
            shooter = run.players[2]
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = bap.team = 0
            shooter.team = 1
            x, y, z = owner.mover.position
            shooter.mover.position[:] = (x, y, z + 8)
            shooter.mover.body = None
            run.step({})
            target = next(t for t in fight._targets() if t.owner is owner)
            self.assertIs(fight.baptiste_fields.protecting_device(target), device)
            fight.vitals_of(target).damage(210)
            instance, = device.component.instances.values()
            wait = instance.states[7]
            timeout = wait.evaluate(wait.node.field("m_timeout"), watch=False)
            self.assertEqual(timeout, 5)
            expiry = int((wait.start + timeout * 1000) // device.component.frame_ms)
            heading = aim(fight._eye(shooter), (x, y + 1, z))
            return run, target, shooter, device, expiry, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, target, shooter, _, _, heading = scene()
        fight = run.match.combat
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, 25)
        travel = run.frame - fired
        self.assertGreater(travel, 1)

        for protected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    run, target, shooter, device, expiry, heading = scene()
                    fight = run.match.combat
                    contact = expiry - int(protected)
                    advance(run, contact - travel - 1)
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertIs(fight.baptiste_fields.protecting_device(target), device)
                    impacts = []

                    def observe(missile, *, world=fight, recipient=target, hits=impacts,
                                landed=fight._landed):
                        fields = world.baptiste_fields
                        hits.append((world.tick, fields.protecting_device(recipient) is not None,
                                     bool(fields.devices)))
                        landed(missile)

                    end = expiry + 40
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertEqual(impacts, [(contact, protected, True)])
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.baptiste_fields.devices)
                    self.assertEqual(device.health, 150)
                    own = fight.vitals_of(target)
                    self.assertEqual(own.total, 25 if protected else 0)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 15 if protected else 40)
                    if protected:
                        self.assertFalse(own.dead)
                    else:
                        self.assertEqual((own.death_tick, own.killer), (contact, shooter.body))
                    outcomes.append((impacts, own.death_tick))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_blossom_keeps_floor_across_overlapping_field_expiries(self):
        cases = ((False, True), (True, True), (False, False), (True, False))
        for skipped, in_both in cases:
            with self.subTest(skipped=skipped, in_both=in_both):
                run, owner, first, fight = self.charge_scene(
                    names=("Reaper", "First Baptiste", "Second Baptiste", "Target"),
                    hero="Baptiste", wall=None,
                )
                second, victim = run.players[2:]
                self.assertTrue(run.match.switch_hero(second, find_hero("Baptiste")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = 0
                first.team = second.team = victim.team = 1
                victim_x = 0 if in_both else 3
                for actor, position in ((first, (8, 1, 1.5)), (second, (-8, 1, 1.5)),
                                        (victim, (victim_x, 1, 1.5))):
                    actor.mover.position[:] = position
                    actor.mover.body = None
                for _ in range(12):
                    run.step({})
                first_aim = aim(fight._eye(first), (8, 1, 1))
                second_aim = aim(fight._eye(second), (-8, 1, 1))
                run.step({first: (*first_aim, 16)})
                pulse_gap = math.ceil(2 * reaper.BLOSSOM_INTERVAL / combat.TICK)
                for _ in range(pulse_gap - 1):
                    run.step({})
                run.step({second: (*second_aim, 16)})
                for _ in range(90):
                    if second.body in fight.baptiste_fields.devices:
                        break
                    run.step({})
                devices = [fight.baptiste_fields.devices[bap.body] for bap in (first, second)]
                waits, expiries = [], []
                for device in devices:
                    component = device.component
                    instance, = component.instances.values()
                    wait = instance.states[7]
                    self.assertTrue(wait.active)
                    timeout = wait.evaluate(wait.node.field("m_timeout"), watch=False)
                    self.assertEqual(timeout, 5)
                    waits.append(wait)
                    expiries.append(int((wait.start + timeout * 1000) // component.frame_ms))
                self.assertEqual(expiries[1] - expiries[0], pulse_gap)
                pulse_ticks = math.ceil(reaper.BLOSSOM_INTERVAL / combat.TICK)
                run.frame = expiries[0] - pulse_ticks - 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                target = next(t for t in fight._targets() if t.owner is victim)
                self.assertIs(fight.baptiste_fields.protecting_device(target), devices[0])
                enemy = fight.vitals_of(target)
                enemy.damage(enemy.total - 40)
                own = fight.vitals[owner.body]
                own.damage(100)
                owner.body_script.set_ult_charge(2100)

                run.step({owner: (0, 0, 32)})

                channel = fight.reaper_blossoms[owner.body]
                self.assertEqual(math.ceil(channel["targets"][victim.body] / combat.TICK - 1e-8), expiries[0])
                self.assertEqual(enemy.total, 20)
                self.assertAlmostEqual(own.total, 157, places=4)
                for index, tick in enumerate(expiries):
                    if skipped:
                        run.frame = tick
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    else:
                        while run.frame < tick:
                            run.step({})
                    self.assertFalse(waits[index].active)
                    self.assertEqual(tuple(fight.baptiste_fields.devices.values()), tuple(devices))
                    self.assertEqual([device.health for device in devices], [150, 150])
                    if index == 0:
                        self.assertTrue(waits[1].active)
                        fallback = devices[1] if in_both else None
                        self.assertIs(fight.baptiste_fields.protecting_device(target), fallback)
                        self.assertEqual(enemy.total, 20 if in_both else 0)
                        self.assertAlmostEqual(own.total, 157 if in_both else 164, places=4)
                self.assertTrue(enemy.dead)
                self.assertEqual(enemy.death_tick, expiries[1 if in_both else 0])
                self.assertEqual(enemy.killer, owner.body)
                self.assertAlmostEqual(own.total, 164, places=4)
                self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 40, places=4)
                self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
                self.assertEqual(owner.body_script.component.vars[ULT_CHARGE].value(), 0)

    def start_sound_barrier(self, run, lucio, fight):
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({lucio: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(80):
            run.step({})
            if lucio.body in fight.sound_barriers.leases:
                break
        self.assertIn(lucio.body, fight.sound_barriers.leases)
        return fight.sound_barriers.leases[lucio.body]

    def test_actual_sound_barrier_spends_pool_on_shell_then_expires(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
        owner.mover.position[2] = 1.5
        owner.mover.body = None
        health = fight.vitals[owner.body]
        health.damage(100)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is lucio))
        cast = self.start_sound_barrier(run, lucio, fight)
        shield = enemy.temporary_shields[lucio_barrier.POOL]
        self.assertEqual(shield.now, 750)
        self.assertEqual(cast.duration, 6)
        if LUCIO_STATUS_SHA256:
            self.assertIsNotNone(cast.effect)
            self.assertEqual(cast.effect.graph.index, 0x073B)
        charge = owner.body_script.component.entity_var(ULT_CHARGE)
        before_charge = charge.value()
        fixture.fire(run, owner, lucio)
        self.assertEqual(shield.now, 628)
        self.assertEqual(sum(enemy.now.values()), 200)
        self.assertAlmostEqual(health.total, 150 + 120 * reaper.REAPING_RATIO, places=4)
        self.assertEqual(charge.value(), before_charge)
        run.frame += 375
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(shield.now, 0)
        self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        if cast.effect is not None:
            self.assertTrue(cast.effect.stopped)
        before_charge = charge.value()
        fixture.fire(run, owner, lucio)
        self.assertEqual(sum(enemy.now.values()), 80)
        self.assertGreaterEqual(charge.value() - before_charge, 120)

    def test_actual_helix_resolves_sound_barrier_decay_across_world_gap(self):
        def scene():
            run, owner, lucio, fight = self.charge_scene(
                hero="Lucio", names=("Reaper", "Lucio", "Soldier"), wall=None,
            )
            shooter = run.players[2]
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = lucio.team = 0
            shooter.team = 1
            x, y, z = owner.mover.position
            lucio.mover.position[:] = (x + 8, y, z)
            shooter.mover.position[:] = (x, y, z + 8)
            lucio.mover.body = shooter.mover.body = None
            own = fight.vitals[owner.body]
            own.damage(131)
            self.start_sound_barrier(run, lucio, fight)
            lease = fight.sound_barriers.leases[owner.body]
            self.assertEqual((lease.capacity, lease.duration), (750, 6))
            if LUCIO_STATUS_SHA256:
                self.assertIsNotNone(lease.effect)
                self.assertEqual(lease.effect.graph.index, 0x073B)
            pool = own.temporary_shields[lucio_barrier.POOL]
            self.assertEqual(pool.now, 750)
            expiry = lease.last_tick + round(lease.duration / combat.TICK)
            heading = aim(fight._eye(shooter), (x, y + 1, z))
            return run, owner, shooter, fight, own, pool, expiry, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, _, shooter, fight, own, _, _, heading = scene()
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(sum(own.now.values()), 119)
        travel = run.frame - fired
        self.assertGreater(travel, 1)

        for protected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    run, owner, shooter, fight, own, pool, expiry, heading = scene()
                    contact = expiry - int(protected)
                    advance(run, contact - travel - 1)
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertIn(owner.body, fight.sound_barriers.leases)
                    impacts = []

                    def observe(missile, *, world=fight, shield=pool, hits=impacts, landed=fight._landed):
                        hits.append((world.tick, shield.now))
                        landed(missile)

                    end = expiry + 30
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertEqual(impacts, [(contact, 2 if protected else 0)])
                    self.assertFalse(fight.rockets)
                    self.assertNotIn(owner.body, fight.sound_barriers.leases)
                    self.assertEqual(own.total, 1 if protected else 0)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 120 if protected else 119)
                    if protected:
                        self.assertFalse(own.dead)
                    else:
                        self.assertEqual((own.death_tick, own.killer), (contact, shooter.body))
                    outcomes.append((impacts, own.death_tick))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_sound_barrier_break_credits_only_shell_damage_past_the_pool(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
        owner.mover.position[2] = 1.5
        owner.mover.body = None
        health = fight.vitals[owner.body]
        health.damage(100)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is lucio))
        self.start_sound_barrier(run, lucio, fight)
        enemy.decay_temporary_shields(lucio_barrier.POOL, 728)
        charge = owner.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        fixture.fire(run, owner, lucio)
        self.assertEqual(enemy.temporary_shields[lucio_barrier.POOL].now, 0)
        self.assertEqual(sum(enemy.now.values()), 100)
        self.assertAlmostEqual(health.total, 150 + 120 * reaper.REAPING_RATIO, places=4)
        self.assertAlmostEqual(charge.value() - before, 100, places=4)

    def test_actual_sound_barrier_ally_lease_survives_source_body_replacement(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, lucio, fight = self.charge_scene(
            hero="Lucio", names=("Reaper", "Lucio", "Enemy"), wall=None,
        )
        enemy = run.players[2]
        self.assertTrue(run.match.switch_hero(enemy, heroes()[reaper.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        lucio.team = owner.team = 0
        enemy.team = 1
        enemy.mover.position[:] = (0, 1, 7.5)
        enemy.mover.body = None
        self.start_sound_barrier(run, lucio, fight)
        shield = fight.vitals[owner.body].temporary_shields[lucio_barrier.POOL]
        lease = fight.sound_barriers.leases[owner.body]
        self.assertEqual(shield.now, 750)
        self.assertNotIn(enemy.body, fight.sound_barriers.leases)
        old_body = lucio.body
        self.assertTrue(run.match.switch_hero(lucio, find_hero("Soldier: 76")))
        self.assertNotIn(old_body, fight.sound_barriers.leases)
        self.assertIs(fight.sound_barriers.leases[owner.body], lease)
        fixture.fire(run, enemy, owner)
        self.assertEqual(shield.now, 628)
        self.assertEqual(sum(fight.vitals[owner.body].now.values()), 250)
        retired_body = owner.body
        self.assertTrue(run.match.switch_hero(owner, find_hero("Soldier: 76")))
        self.assertNotIn(retired_body, fight.sound_barriers.leases)
        if lease.effect is not None:
            self.assertTrue(lease.effect.stopped)

    def test_actual_sound_barrier_rejects_unfunded_cast_and_filters_covered_or_distant_reaper(self):
        for case in ("charge", "cover", "range"):
            with self.subTest(case=case):
                run, owner, lucio, fight = self.charge_scene(
                    hero="Lucio", wall=3 if case == "cover" else None,
                )
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                lucio.team = owner.team = 0
                if case == "range":
                    owner.mover.position[2] = 38
                    owner.mover.body = None
                if case == "charge":
                    lucio.body_script.set_ult_charge(0)
                    run.step({lucio: (0, 0, 32)})
                    for _ in range(55):
                        run.step({})
                    self.assertNotIn(lucio.body, fight.sound_barriers.leases)
                else:
                    self.start_sound_barrier(run, lucio, fight)
                self.assertNotIn(owner.body, fight.sound_barriers.leases)
                self.assertNotIn(lucio_barrier.POOL, fight.vitals[owner.body].temporary_shields)
                self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_lethal_reaper_shell_stops_sound_barrier_before_grant(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
        owner.mover.position[2] = 1.5
        owner.mover.body = None
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is lucio))
        enemy.damage(enemy.total - 20)
        health = fight.vitals[owner.body]
        health.damage(100)
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({lucio: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        fixture.fire(run, owner, lucio)
        self.assertTrue(fight.is_dead(lucio))
        self.assertEqual(enemy.killer, owner.body)
        for _ in range(85):
            run.step({})
        self.assertFalse(fight.sound_barriers.leases)
        self.assertAlmostEqual(health.total, 150 + 20 * reaper.REAPING_RATIO, places=4)

    def test_actual_blossom_spends_sound_barrier_then_hits_base_health(self):
        run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
        owner.mover.position[2] = 2.5
        owner.mover.body = None
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is lucio))
        self.start_sound_barrier(run, lucio, fight)
        shield = enemy.temporary_shields[lucio_barrier.POOL]
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(35):
            run.step({})
        self.assertLess(shield.now, 650)
        self.assertEqual(sum(enemy.now.values()), 200)
        for _ in range(180):
            run.step({})
        self.assertEqual(shield.now, 0)
        self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        self.assertGreater(enemy.total, 0)
        self.assertLess(enemy.total, 200)
        self.assertIsNone(reaper.blossom_state(owner.body_script))

    def blossom_sound_barrier_expiry_trace(self, *, protected, skipped):
        run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
        owner.mover.position[2] = 2.5
        owner.mover.body = None
        health = fight.vitals[owner.body]
        health.damage(health.total - 100)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is lucio))
        enemy.damage(enemy.total - 33)
        lease = self.start_sound_barrier(run, lucio, fight)
        self.assertEqual((lease.capacity, lease.duration), (750, 6))
        self.assertIsNotNone(lease.effect)
        self.assertEqual(lease.effect.graph.index, 0x073B)
        shield = enemy.temporary_shields[lucio_barrier.POOL]
        base = reaper.BLOSSOM_DAMAGE
        decay = lease.capacity * combat.TICK / lease.duration
        interval = math.ceil(reaper.BLOSSOM_INTERVAL / combat.TICK)
        second = lease.last_tick + round((lease.capacity - base) / decay) - int(protected)
        first = second - interval
        run.frame = first - 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(2100)
        samples = []
        damage = fight._damage

        def observe(source, target, amount, *args, **kwargs):
            before = shield.now
            result = damage(source, target, amount, *args, **kwargs)
            if source is owner and target.owner is lucio:
                samples.append((fight.tick, before, shield.now, sum(enemy.now.values()), health.total))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            run.step({owner: (0, 0, 32)})
            self.assertIsNotNone(reaper.blossom_state(owner.body_script))
            end = run.frame + 205
            for tick in (end,) if skipped else range(run.frame + 1, end + 1):
                run.match.update(tick * combat.TICK, tick, frames=False)
        initial = base + decay * (interval + int(protected))
        expected = [(first, initial, initial - base, 33)]
        if protected:
            third = first + math.ceil(2 * reaper.BLOSSOM_INTERVAL / combat.TICK)
            expected.extend(((second, 2, 0, 1), (third, 0, 0, 0)))
        else:
            expected.append((second, 0, 0, 0))
        self.assertEqual([sample[:4] for sample in samples], expected)
        effective = 0
        previous_health = 33
        for _, shield_before, shield_after, base_health, healed_total in samples:
            effective += shield_before - shield_after + previous_health - base_health
            self.assertAlmostEqual(healed_total, 100 + effective * reaper.REAPING_RATIO, places=4)
            previous_health = base_health
        self.assertEqual(effective, 69 if protected else 67)
        self.assertTrue(enemy.dead)
        self.assertEqual((enemy.death_tick, enemy.killer), (expected[-1][0], owner.body))
        self.assertEqual(len(lucio.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return samples, enemy.death_tick

    def test_actual_blossom_second_pulse_resolves_sound_barrier_depletion(self):
        if not LUCIO_STATUS_SHA256:
            self.skipTest("requires the pinned 073B Sound Barrier supplement")
        for protected in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    outcome = self.blossom_sound_barrier_expiry_trace(protected=protected, skipped=skipped)
                    outcomes.append(outcome)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_soundwave_pushes_reaper_preserves_blossom_and_respects_wraith(self):
        for wraith in (False, True):
            with self.subTest(wraith=wraith):
                run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
                owner.mover.position[2] = 2.5
                owner.mover.body = None
                start = tuple(owner.mover.position)
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is lucio))
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 8 if wraith else 32), lucio: (0, 0, 2)})
                self.assertEqual(fight.vitals[owner.body].total, 250 if wraith else 225)
                self.assertFalse(fight.is_stunned(owner))
                if wraith:
                    self.assertTrue(reaper.intangible(owner))
                    self.assertNotIn(owner.body, fight.npc_impulses)
                else:
                    velocity = fight.npc_impulses[owner.body][1]
                    self.assertAlmostEqual(math.hypot(*velocity), 17, places=5)
                    self.assertGreater(velocity[1], 0)
                    self.assertGreater(velocity[2], 0)
                    self.assertIsNotNone(reaper.blossom_state(owner.body_script))
                for _ in range(12):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 250 if wraith else 225)
                self.assertEqual(len(lucio.client.messages(combat.HIT)), int(not wraith))
                if wraith:
                    self.assertEqual(tuple(owner.mover.position), start)
                else:
                    self.assertGreater(owner.mover.position[1], start[1])
                    self.assertGreater(owner.mover.position[2], start[2] + 1)
                    self.assertLess(enemy.total, 200)

    def test_actual_soundwave_respects_cone_cover_range_team_and_projection(self):
        for case in ("rear", "cover", "range", "ally", "projection", "projection_break"):
            with self.subTest(case=case):
                run, owner, lucio, fight = self.charge_scene(
                    hero="Lucio", names=("Reaper", "Lucio", "Zarya"),
                    wall=1.25 if case == "cover" else None,
                )
                ally = run.players[2]
                owner.mover.position[2] = 10 if case == "range" else 2.5
                owner.mover.body = None
                ally.mover.position[:] = (8, 1, 2.5)
                ally.mover.body = None
                if case in ("ally", "projection", "projection_break"):
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    lucio.team, owner.team, ally.team = 1, int(case == "ally"), 0
                if case.startswith("projection"):
                    self.assertTrue(run.match.switch_hero(ally, find_hero("Zarya")))
                    ally.mover.position[:] = (5, 1, 2.5)
                    ally.mover.body = None
                for _ in range(3):
                    run.step({})
                bubble = None
                if case.startswith("projection"):
                    projection = aim(fight._eye(ally), fight._eye(owner))
                    run.step({ally: (*projection, 16)})
                    bubble = fight.zarya_bubbles.for_target(owner)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                    if case == "projection_break":
                        bubble.health = 20
                origin = tuple(owner.mover.position)
                run.step({lucio: (-32768 if case == "rear" else 0, 0, 2)})
                for _ in range(15):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(tuple(owner.mover.position), origin)
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertFalse(fight.is_stunned(owner))
                if bubble is not None:
                    breaking = case == "projection_break"
                    self.assertEqual(bubble.health, 0 if breaking else 175)
                    self.assertEqual(fight.zarya_bubbles.for_target(owner) is None, breaking)
                else:
                    self.assertFalse(lucio.client.messages(combat.HIT))

    def test_actual_soundwave_resolves_shadow_immunity_without_canceling_teleport(self):
        for phase in ("windup", "immune", "landed"):
            with self.subTest(phase=phase):
                run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
                owner.mover.position[2] = 2.5
                owner.mover.body = None
                for _ in range(3):
                    run.step({})
                origin = tuple(owner.mover.position)
                destination = self.start_shadow(run, owner)["target"]
                self.assertGreater(math.dist(origin, destination), 2)
                if phase != "windup":
                    for _ in range(110):
                        reached = (reaper.intangible(owner) if phase == "immune"
                                   else math.dist(owner.mover.position, destination) < 0.002)
                        if reached:
                            break
                        run.step({})
                    self.assertTrue(reached)
                if phase == "landed":
                    lucio.mover.position[0] = destination[0]
                    lucio.mover.body = None
                    run.step({})
                protected = phase == "immune"
                self.assertEqual(reaper.intangible(owner), protected)
                run.step({lucio: (0, 0, 2)})
                health = fight.vitals[owner.body]
                self.assertEqual(health.total, 250 if protected else 225)
                self.assertFalse(fight.is_stunned(owner))
                self.assertEqual(owner.body in fight.npc_impulses, not protected)
                if phase == "windup":
                    self.assertTrue(self.shadow(owner).state(32).active)
                    for _ in range(12):
                        run.step({})
                    self.assertGreater(owner.mover.position[2], origin[2] + 1)
                for _ in range(135):
                    run.step({})
                self.assertGreater(self.shadow(owner).vars[61].value(), 0)
                self.assertEqual(health.total, 250 if protected else 225)
                if phase == "landed":
                    self.assertGreater(owner.mover.position[2], destination[2] + 1)
                else:
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)

    def test_actual_soundwave_push_during_flashbang_keeps_the_stun_deadline(self):
        run, owner, lucio, fight = self.charge_scene(
            hero="Lucio", names=("Reaper", "Lucio", "McCree"), wall=None,
        )
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        lucio.team = caster.team = 1
        owner.team = 0
        owner.mover.position[2] = 2.5
        caster.mover.position[:] = (2.5, 1, 2.5)
        owner.mover.body = caster.mover.body = None
        for _ in range(3):
            run.step({})
        flashbang_tests.FlashbangTests().cast(run, caster, owner)
        deadline = fight.stuns[owner.body]
        self.assertFalse(fight.is_stunned(lucio))
        self.assertEqual(fight.vitals[owner.body].total, 225)
        position = tuple(owner.mover.position)
        heading = aim(fight._eye(owner), fight._eye(lucio))
        run.step({lucio: (0, 0, 2), owner: (*heading, 1)})
        self.assertEqual(fight.vitals[owner.body].total, 200)
        self.assertIn(owner.body, fight.npc_impulses)
        self.assertEqual(fight.stuns[owner.body], deadline)
        for _ in range(12):
            run.step({owner: (*heading, 1)})
        self.assertGreater(owner.mover.position[2], position[2] + 1)
        self.assertTrue(fight.is_stunned(owner))
        self.assertFalse(owner.client.messages(combat.HIT))
        while fight.tick < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(fight.vitals[owner.body].total, 200)

    def test_actual_soundwave_refuses_cooldown_and_reuses_fresh_input(self):
        run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
        owner.mover.position[2] = 2.5
        owner.mover.body = None
        instance = next(i for i in lucio.body_script.component.instances.values() if i.graph.index == 0x0776)
        ability = instance.state(20)
        health = fight.vitals[owner.body]
        run.step({lucio: (0, 0, 2)})
        self.assertEqual(health.total, 225)
        for _ in range(60):
            run.step({})
            if owner.body not in fight.npc_impulses:
                break
        self.assertNotIn(owner.body, fight.npc_impulses)
        owner.mover.position[:] = (0, 1, 2.5)
        owner.mover.body = None
        run.step({})
        self.assertGreater(ability.remaining(), 0.5)
        run.step({lucio: (0, 0, 2)})
        self.assertEqual(health.total, 225)
        for _ in range(320):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(health.total, 225)
        run.step({lucio: (0, 0, 2)})
        self.assertEqual(health.total, 200)
        self.assertEqual(len(lucio.client.messages(combat.HIT)), 2)

    def test_actual_reaper_shell_resolves_soundwave_source_death_boundary(self):
        fixture = reaper_tests.ReaperTests()
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, lucio, fight = self.charge_scene(hero="Lucio", wall=None)
                owner.mover.position[2] = 2.5
                owner.mover.body = None
                health = fight.vitals[owner.body]
                health.damage(100)
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is lucio))
                enemy.damage(enemy.total - 15)
                if released:
                    run.step({lucio: (0, 0, 2)})
                    self.assertEqual(health.total, 125)
                    self.assertIn(owner.body, fight.npc_impulses)
                fixture.fire(run, owner, lucio)
                self.assertTrue(fight.is_dead(lucio))
                self.assertEqual(enemy.killer, owner.body)
                if not released:
                    run.step({lucio: (0, 0, 2)})
                for _ in range(15):
                    run.step({})
                self.assertEqual(len(lucio.client.messages(combat.HIT)), int(released))
                self.assertFalse(fight.is_stunned(owner))
                expected = (125 if released else 150) + 15 * reaper.REAPING_RATIO
                self.assertAlmostEqual(health.total, expected, places=4)
                self.assertEqual(owner.mover.position[2] > 2.5, released)

    def test_actual_meteor_strike_inner_outer_and_wraith_with_real_charge(self):
        for case, distance, damage in (("inner", 1.5, 300), ("outer", 5, 107.5), ("wraith", 1.5, 0)):
            with self.subTest(case=case):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                owner.mover.position[2] = distance
                owner.mover.body = None
                origin = tuple(owner.mover.position)
                fight.vitals[owner.body] = health = combat.Vitals(owner.body, 500)
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
                script = doom.body_script
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(0)
                run.step({doom: (0, 0, 32)})
                self.assertNotIn(doom.body, fight.meteors)
                self.assertFalse(meteor_strike.protected(doom))
                run.step({})
                script.set_ult_charge(cost)
                run.step({doom: (0, 0, 32)})
                cast = fight.meteors[doom.body]
                self.assertTrue(meteor_strike.protected(doom))
                for _ in range(80):
                    if meteor_strike.aiming(meteor_strike.phase_of(doom)):
                        break
                    run.step({})
                self.assertTrue(meteor_strike.aiming(meteor_strike.phase_of(doom)))
                self.assertEqual(health.total, 500)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
                run.step({doom: (0, 0, 1), owner: (0, 0, 8 if case == "wraith" else 0)})
                self.assertTrue(cast.committed)
                landing = cast.target
                for _ in range(100):
                    run.step({})
                    if cast.impacted:
                        break
                self.assertTrue(cast.impacted)
                self.assertFalse(meteor_strike.protected(doom))
                self.assertEqual(tuple(doom.mover.position), landing)
                self.assertEqual(cast.hit, (owner.body,) if damage else ())
                self.assertAlmostEqual(health.total, 500 - damage)
                self.assertFalse(fight.is_stunned(owner))
                if damage:
                    self.assertGreater(fight.npc_impulses[owner.body][1][1], 0)
                    self.assertEqual(enemy.temporary_shields[0xDDD].now, 75)
                else:
                    self.assertTrue(reaper.intangible(owner))
                    self.assertEqual(tuple(owner.mover.position), origin)
                    self.assertNotIn(owner.body, fight.npc_impulses)
                    self.assertNotIn(doom.body, fight.doomfist_shields)
                charge = script.component.entity_var(ULT_CHARGE)
                lowest = charge.value()
                for _ in range(50):
                    run.step({})
                    lowest = min(lowest, charge.value())
                self.assertTrue(cast.finished)
                passive_step = cost * script.frame_ms / (1000 * script.ult_seconds)
                self.assertAlmostEqual(lowest, passive_step, places=5)
                self.assertLess(charge.value(), cost)
                self.assertAlmostEqual(health.total, 500 - damage)

    def test_actual_reaper_shell_passes_protected_meteor_then_kills_after_impact(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        owner.mover.position[2] = 5
        owner.mover.body = None
        health = fight.vitals[owner.body]
        health.damage(100)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
        enemy.damage(enemy.total - 20)
        cast = self.start_meteor(run, doom, fight)
        fixture.fire(run, owner, doom)
        self.assertTrue(meteor_strike.protected(doom))
        self.assertEqual(enemy.total, 20)
        self.assertEqual(health.total, 150)
        self.assertFalse(owner.client.messages(combat.HIT))
        run.step({doom: (0, 0, 1)})
        self.assertTrue(cast.committed)
        self.assertTrue(meteor_strike.protected(doom))
        self.wait_meteor_impact(run, cast)
        self.assertFalse(meteor_strike.protected(doom))
        self.assertEqual(enemy.total, 95)
        self.assertAlmostEqual(health.total, 42.5)
        for _ in range(100):
            if owner.body not in fight.npc_impulses:
                break
            run.step({})
        self.assertNotIn(owner.body, fight.npc_impulses)
        x, y, z = doom.mover.position
        owner.mover.position[:] = (x, y, z + 1.5)
        owner.mover.body = None
        remaining = enemy.total
        fixture.fire(run, owner, doom)
        self.assertTrue(fight.is_dead(doom))
        self.assertEqual(enemy.killer, owner.body)
        self.assertAlmostEqual(health.total, 42.5 + remaining * reaper.REAPING_RATIO, places=4)
        self.assertNotIn(doom.body, fight.meteors)

    def test_actual_meteor_strike_respects_cover_range_team_and_projected_barrier(self):
        for case in ("cover", "range", "ally", "projection"):
            with self.subTest(case=case):
                run, owner, doom, fight = self.charge_scene(
                    hero="Doomfist", names=("Reaper", "Doomfist", "Zarya"),
                    wall=2.5 if case == "cover" else None,
                )
                ally = run.players[2]
                owner.mover.position[2] = 8.0625 if case == "range" else 5
                owner.mover.body = None
                ally.mover.position[:] = (12, 1, 5)
                ally.mover.body = None
                if case in ("ally", "projection"):
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    doom.team, owner.team, ally.team = 1, int(case == "ally"), 0
                bubble = None
                if case == "projection":
                    self.assertTrue(run.match.switch_hero(ally, find_hero("Zarya")))
                    ally.mover.position[:] = (8, 1, 5)
                    ally.mover.body = None
                cast = self.start_meteor(run, doom, fight)
                run.step({doom: (0, 0, 1)})
                self.assertTrue(cast.committed)
                if case == "projection":
                    projection = aim(fight._eye(ally), fight._eye(owner))
                    run.step({ally: (*projection, 16)})
                    bubble = fight.zarya_bubbles.for_target(owner)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                self.wait_meteor_impact(run, cast)
                self.assertEqual(cast.hit, ())
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertNotIn(doom.body, fight.doomfist_shields)
                if bubble is not None:
                    self.assertAlmostEqual(bubble.health, 92.5)

    def test_actual_meteor_strike_moves_selection_then_timeout_uses_anchor(self):
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        cast = self.start_meteor(run, doom, fight)
        initial = cast.target
        for _ in range(25):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame)])
            doom.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(cast.target[2], initial[2] + 1)
        self.assertFalse(cast.committed)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(380):
            run.step({})
            if cast.committed:
                break
        self.assertTrue(cast.committed)
        landing = cast.target
        self.wait_meteor_impact(run, cast)
        self.assertEqual(tuple(doom.mover.position), landing)
        self.assertEqual(cast.hit, (owner.body,))
        self.assertLess(fight.vitals[owner.body].total, 250)

    def test_actual_meteor_strike_settles_without_owner_input_and_across_world_gaps(self):
        outcomes = {}
        for case in ("confirm", "wraith", "timeout"):
            for gap in (False, True):
                with self.subTest(case=case, gap=gap):
                    run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                    owner.mover.position[2] = 5
                    owner.mover.body = None
                    cast = self.start_meteor(run, doom, fight)
                    if case != "timeout":
                        run.step({doom: (0, 0, 1), owner: (0, 0, 8 if case == "wraith" else 0)})
                    self.assertEqual(cast.committed, case != "timeout")
                    self.assertFalse(cast.impacted)
                    self.assertEqual(reaper.intangible(owner), case == "wraith")
                    start = run.frame
                    elapsed = 700 if case == "timeout" else 240
                    old_input = doom.commands.last_frame
                    for offset in ((elapsed,) if gap else range(1, elapsed + 1)):
                        tick = start + offset
                        run.match.update(tick * combat.TICK, tick, frames=False)
                    self.assertEqual(doom.commands.last_frame, old_input)
                    self.assertGreater(doom.body_script.frame, start)
                    self.assertTrue(cast.committed)
                    self.assertTrue(cast.impacted)
                    self.assertTrue(cast.finished)
                    self.assertFalse(meteor_strike.protected(doom))
                    self.assertEqual(tuple(doom.mover.position), cast.target)
                    self.assertFalse(reaper.intangible(owner))
                    self.assertEqual(cast.hit, () if case == "wraith" else (owner.body,))
                    health = fight.vitals[owner.body].total
                    self.assertEqual(health, 250 if case == "wraith" else 142.5)
                    self.assertEqual(len(doom.client.messages(combat.HIT)), int(case != "wraith"))
                    settled_frame = doom.body_script.frame
                    doom.take_commands([Command(start + 1, buttons=1)])
                    self.assertEqual(doom.body_script.frame, settled_frame)
                    run.frame = start + elapsed
                    run.step({})
                    self.assertEqual(fight.vitals[owner.body].total, health)
                    self.assertEqual(len(doom.client.messages(combat.HIT)), int(case != "wraith"))
                    outcomes[case, gap] = (health, cast.hit, tuple(owner.mover.position), cast.target)
            self.assertEqual(outcomes[case, False], outcomes[case, True])

    def test_actual_meteor_strike_world_impact_reads_wraith_expiry_in_either_player_order(self):
        for caster_first in (False, True):
            with self.subTest(caster_first=caster_first):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                owner.mover.position[2] = 5
                owner.mover.body = None
                cast = self.start_meteor(run, doom, fight)
                run.step({owner: (0, 0, 8)})
                for _ in range(125):
                    run.step({})
                run.step({doom: (0, 0, 1)})
                self.assertTrue(cast.committed)
                self.assertTrue(reaper.intangible(owner))
                run.match.players.sort(key=lambda player: (player is not doom) == caster_first)
                for offset in range(1, 85):
                    was_immune = reaper.intangible(owner)
                    tick = run.frame + offset
                    run.match.update(tick * combat.TICK, tick, frames=False)
                    if cast.impacted:
                        break
                self.assertTrue(cast.impacted)
                self.assertTrue(was_immune)
                self.assertFalse(reaper.intangible(owner))
                self.assertEqual(cast.hit, (owner.body,))
                self.assertEqual(fight.vitals[owner.body].total, 142.5)

    def meteor_blossom_outcome(self, *, case, skipped):
        run, owner, doom, fight = self.charge_scene(
            hero="Doomfist", names=("Reaper", "Doomfist", "Zarya"), wall=None,
        )
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = zarya.team = 0
        doom.team = 1
        owner.mover.position[2] = 5
        zarya.mover.position[:] = (8, 1, 5)
        owner.mover.body = zarya.mover.body = None
        health = fight.vitals[owner.body]
        if case != "survives":
            health.damage(150)
        cast = self.start_meteor(run, doom, fight)
        if case == "projected":
            guard = aim(fight._eye(zarya), fight._eye(owner))
            run.step({zarya: (*guard, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertEqual(bubble is not None, case == "projected")
        script = owner.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({doom: (0, 0, 1), owner: (0, 0, 32)})
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)
        self.assertIsNotNone(reaper.blossom_state(script))
        self.assertEqual(charge.value(), 0)
        start = run.frame
        frames = owner.commands.last_frame, doom.commands.last_frame

        def advance_to(tick):
            while run.frame < tick:
                run.frame = tick if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)

        advance_to(start + 105)
        self.assertTrue(cast.impacted)
        self.assertEqual(cast.hit, () if bubble is not None else (owner.body,))
        expected_damage = {"survives": 107.5, "lethal": 100, "projected": 0}[case]
        self.assertAlmostEqual(doom.stats.value(stats.HERO_DAMAGE), expected_damage)
        self.assertFalse(meteor_strike.protected(doom))
        self.assertFalse(fight.is_stunned(owner))
        lethal = case == "lethal"
        self.assertEqual(fight.is_dead(owner), lethal)
        self.assertEqual(reaper.blossom_state(script) is None, lethal)
        damage = owner.stats.value(stats.HERO_DAMAGE)
        hit_frames = [event["+0x84"] for event in owner.client.messages(combat.HIT)]
        if lethal:
            self.assertEqual(health.killer, doom.body)
            self.assertEqual(damage, 0)
            self.assertEqual(hit_frames, [])
            self.assertNotIn(owner.body, fight.npc_impulses)
        else:
            self.assertGreater(damage, 0)
            self.assertTrue(hit_frames)
        if bubble is not None:
            self.assertAlmostEqual(bubble.health, 92.5)
            self.assertNotIn(owner.body, fight.npc_impulses)
            self.assertNotIn(doom.body, fight.doomfist_shields)
        after_impact = health.total, damage, hit_frames
        advance_to(start + 225)
        self.assertIsNone(reaper.blossom_state(script))
        self.assertEqual(charge.value(), 0)
        self.assertEqual((owner.commands.last_frame, doom.commands.last_frame), frames)
        if lethal:
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
        else:
            self.assertGreater(owner.stats.value(stats.HERO_DAMAGE), damage)
        return after_impact, health.total, owner.stats.value(stats.HERO_DAMAGE), tuple(owner.mover.position)

    def test_actual_meteor_strike_ends_blossom_only_on_unprotected_lethal_impact(self):
        for case in ("survives", "lethal", "projected"):
            with self.subTest(case=case):
                stepped = self.meteor_blossom_outcome(case=case, skipped=False)
                skipped = self.meteor_blossom_outcome(case=case, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_actual_replacing_meteor_body_retires_idle_descent(self):
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        owner.mover.position[2] = 5
        owner.mover.body = None
        cast = self.start_meteor(run, doom, fight)
        run.step({doom: (0, 0, 1)})
        self.assertTrue(cast.committed)
        old_body = doom.body
        self.assertTrue(run.match.switch_hero(doom, find_hero("Soldier: 76")))
        self.assertNotEqual(doom.body, old_body)
        self.assertNotIn(old_body, fight.meteors)
        tick = run.frame + 240
        run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(cast.impacted)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertNotIn(owner.body, fight.npc_impulses)
        self.assertFalse(fight.meteors)

    def start_meteor(self, run, doom, fight):
        script = doom.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({doom: (0, 0, 32)})
        cast = fight.meteors[doom.body]
        for _ in range(80):
            if meteor_strike.aiming(meteor_strike.phase_of(doom)):
                break
            run.step({})
        self.assertTrue(meteor_strike.aiming(meteor_strike.phase_of(doom)))
        return cast

    def wait_meteor_impact(self, run, cast, *, limit=100):
        for _ in range(limit):
            if cast.impacted:
                break
            run.step({})
        self.assertTrue(cast.impacted)

    def test_actual_seismic_slam_ground_and_aerial_waves_resolve_reaper_contact(self):
        outcomes = {}
        cases = ((False, False, False), (True, False, False), (False, True, False),
                 (True, True, False), (False, False, True))
        for aerial, wraith, gap in cases:
            with self.subTest(aerial=aerial, wraith=wraith, gap=gap):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                owner.mover.position[2] = 10
                owner.mover.body = None
                if aerial:
                    doom.mover.position[1] += 7
                    doom.mover.body = None
                for _ in range(5):
                    run.step({})
                start = tuple(doom.mover.position)
                destination = (0, 1, 7) if aerial else (0, 2.2, 10)
                heading = aim(fight._eye(doom), destination)
                run.step({doom: (*heading, 16), owner: (0, 0, 8 if wraith else 0)})
                cast = fight.slams[doom.body]
                self.assertEqual(cast.destination is not None, aerial)
                self.assertEqual(reaper.intangible(owner), wraith)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                for _ in range(95):
                    run.step({doom: (*heading, 0)})
                    if cast.wave_tick is not None:
                        break
                self.assertIsNotNone(cast.wave_tick)
                self.assertGreater(cast.origin[2], start[2] + 1)
                self.assertLess(abs(cast.origin[1] - 1), 0.05)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                tick = run.frame
                for offset in ((24,) if gap else range(1, 25)):
                    run.match.update((tick + offset) * combat.TICK, tick + offset, frames=False)
                amount = cast.state.instance.find_var(0, 476).value()
                self.assertGreater(amount, 25)
                self.assertLessEqual(amount, 125)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 250 if wraith else 250 - amount)
                self.assertEqual(cast.hit, set() if wraith else {owner.body})
                self.assertTrue(cast.finished)
                self.assertFalse(fight.is_stunned(owner))
                if wraith:
                    self.assertEqual(tuple(owner.mover.position), (0, 1, 10))
                    self.assertNotIn(doom.body, fight.doomfist_shields)
                else:
                    self.assertGreater(owner.mover.position[1], 1)
                    self.assertLess(owner.mover.position[2], 10)
                    pool = fight.vitals[doom.body].temporary_shields[0xDDD]
                    self.assertEqual(pool.now, 30)
                outcomes[aerial, wraith, gap] = amount, tuple(owner.mover.position)
                run.frame = tick + 24
                for _ in range(40):
                    run.step({doom: (*heading, 0)})
                self.assertGreater(cast.state.instance.state(0).remaining(), 0)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 250 if wraith else 250 - amount)
        self.assertEqual(outcomes[False, False, False], outcomes[False, False, True])

    def start_shadow_slam_fixture(self):
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        owner.mover.position[2] = 10
        owner.mover.body = None
        for _ in range(5):
            run.step({})
        return run, owner, doom, fight

    def launch_seismic_slam_fixture(self, run, doom, *, aerial):
        if aerial:
            doom.mover.position[1] += 7
            doom.mover.body = None
            for _ in range(5):
                run.step({})
        fight = run.match.combat
        heading = aim(fight._eye(doom), (0, 1, 7) if aerial else (0, 2.2, 10))
        run.step({doom: (*heading, 16)})
        cast = fight.slams[doom.body]
        self.assertEqual(cast.destination is not None, aerial)
        if aerial:
            self.assertAlmostEqual(cast.destination[2], 7, delta=0.05)
            self.assertIsNone(cast.origin)
        return cast

    def shadow_slam_entry_trace(self, impact, immunity_delay, *, aerial, protected, skipped):
        run, owner, doom, fight = self.start_shadow_slam_fixture()
        record = self.start_shadow(run, owner)
        immunity_tick = run.frame + immunity_delay
        expected = immunity_tick - int(not protected)
        fire_at = expected - impact
        prepare_at = fire_at - (6 if aerial else 1)
        self.assertGreaterEqual(prepare_at, run.frame)
        while run.frame < prepare_at:
            run.step({})
        cast = self.launch_seismic_slam_fixture(run, doom, aerial=aerial)
        self.assertEqual(run.frame, fire_at)
        self.assertFalse(reaper.intangible(owner))
        self.assertGreater(math.dist(record["target"], owner.mover.position), 2)
        frames = owner.commands.last_frame, doom.commands.last_frame
        end = expected + 180
        observations = []
        advance = seismic_slam.advance

        def observe(world, wave):
            before = owner.body in wave.hit
            advance(world, wave)
            hit = not before and owner.body in wave.hit
            if abs(world.tick - expected) <= 1 or hit:
                observations.append((world.tick, reaper.intangible(owner), hit))
            if hit:
                self.assertIn(owner.body, world.npc_impulses)

        with patch.object(seismic_slam, "advance", side_effect=observe):
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(observations, [
            (expected - 1, False, False),
            (expected, protected, not protected),
            (expected + 1, True, False),
        ])
        self.assertLess(cast.wave_tick, expected)
        self.assertTrue(cast.finished)
        if aerial:
            self.assertLess(math.dist(cast.origin, cast.destination), 0.05)
        self.assertEqual(cast.hit, set() if protected else {owner.body})
        self.assertEqual((owner.commands.last_frame, doom.commands.last_frame), frames)
        health = fight.vitals[owner.body].total
        amount = cast.state.instance.find_var(0, 476).value()
        self.assertGreater(amount, 25)
        self.assertAlmostEqual(health, 250 if protected else 250 - amount)
        self.assertAlmostEqual(doom.stats.value(stats.HERO_DAMAGE), 0 if protected else amount)
        self.assertEqual([event["+0x84"] for event in doom.client.messages(combat.HIT)],
                         [] if protected else [expected])
        self.assertEqual(doom.body in fight.doomfist_shields, not protected)
        self.assertFalse(fight.is_stunned(owner))
        self.assertTrue(record["moved"])
        self.assertLess(math.dist(owner.mover.position, record["target"]), 0.002)
        self.assertGreater(self.shadow(owner).vars[61].value(), 0)
        self.assertNotIn(owner.body, fight.npc_impulses)
        return observations, cast.wave_tick, cast.origin, health, tuple(owner.mover.position)

    def test_actual_seismic_slam_wave_resolves_shadow_immunity_entry(self):
        self.check_seismic_slam_wave_shadow_entry(aerial=False)

    def test_actual_aerial_seismic_slam_wave_resolves_shadow_immunity_entry(self):
        self.check_seismic_slam_wave_shadow_entry(aerial=True)

    def check_seismic_slam_wave_shadow_entry(self, *, aerial):
        run, owner, doom, _ = self.start_shadow_slam_fixture()
        cast = self.launch_seismic_slam_fixture(run, doom, aerial=aerial)
        fired = run.frame
        for _ in range(110):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if owner.body in cast.hit:
                break
        self.assertIn(owner.body, cast.hit)
        impact = run.frame - fired

        run, owner, _, _ = self.start_shadow_slam_fixture()
        self.start_shadow(run, owner)
        confirmed = run.frame
        for _ in range(110):
            if reaper.intangible(owner):
                break
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(reaper.intangible(owner))
        immunity_delay = run.frame - confirmed
        for protected in (False, True):
            with self.subTest(protected=protected):
                stepped = self.shadow_slam_entry_trace(
                    impact, immunity_delay, aerial=aerial, protected=protected, skipped=False,
                )
                skipped = self.shadow_slam_entry_trace(
                    impact, immunity_delay, aerial=aerial, protected=protected, skipped=True,
                )
                self.assertEqual(stepped, skipped)

    def seismic_slam_blossom_trace(self, *, aerial, case, skipped):
        run, owner, doom, fight = self.charge_scene(
            hero="Doomfist", names=("Reaper", "Doomfist", "Zarya", "Soldier"), wall=None,
        )
        zarya, soldier = run.players[2:4]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = zarya.team = 0
        doom.team = soldier.team = 1
        owner.mover.position[:] = (0, 1, 10)
        zarya.mover.position[:] = (8, 1, 10)
        soldier.mover.position[:] = (3, 1, 10)
        owner.mover.body = zarya.mover.body = soldier.mover.body = None
        for _ in range(5):
            run.step({})
        cast = self.launch_seismic_slam_fixture(run, doom, aerial=aerial)
        for _ in range(95):
            if cast.wave_tick is not None:
                break
            run.step({})
        self.assertIsNotNone(cast.wave_tick)
        self.assertNotIn(owner.body, cast.hit)

        health = fight.vitals[owner.body]
        if case != "survives":
            health.damage(249)
        script = owner.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        commands = {owner: (0, 0, 32)}
        if case == "projected":
            guard = aim(fight._eye(zarya), fight._eye(owner))
            commands[zarya] = (*guard, 16)
        run.step(commands)
        self.assertIsNotNone(reaper.blossom_state(script))
        self.assertEqual(charge.value(), 0)
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertEqual(bubble is not None, case == "projected")
        self.assertNotIn(owner.body, cast.hit)
        if bubble is not None:
            self.assertEqual(bubble.health, 200)
        frames = owner.commands.last_frame, doom.commands.last_frame
        contacts = []
        advance = seismic_slam.advance

        def observe(world, wave):
            pending = owner.body not in wave.hit
            before = health.total
            channeling = reaper.blossom_state(script) is not None
            advance(world, wave)
            if pending and owner.body in wave.hit:
                contacts.append((
                    world.tick, before, health.total, channeling, owner.body in fight.npc_impulses,
                ))

        end = run.frame + 225
        with patch.object(seismic_slam, "advance", side_effect=observe):
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        (tick, before, after, channeling, pushed), = contacts
        self.assertTrue(channeling)
        self.assertGreater(tick, cast.wave_tick)
        self.assertTrue(cast.finished)
        self.assertEqual(cast.hit, {owner.body})
        amount = cast.state.instance.find_var(0, 476).value()
        self.assertGreater(amount, 25)
        self.assertEqual(pushed, case == "survives")
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual((owner.commands.last_frame, doom.commands.last_frame), frames)
        self.assertIsNone(reaper.blossom_state(script))
        self.assertEqual(charge.value(), 0)
        later_hits = [event["+0x84"] for event in owner.client.messages(combat.HIT)
                      if event["+0x84"] > tick]
        if case == "lethal":
            self.assertEqual(after, 0)
            self.assertTrue(fight.is_dead(owner))
            self.assertEqual(health.killer, doom.body)
            self.assertEqual(later_hits, [])
        else:
            self.assertFalse(fight.is_dead(owner))
            self.assertTrue(later_hits)
        if bubble is None:
            self.assertAlmostEqual(after, max(0, before - amount))
        else:
            self.assertEqual(after, before)
            self.assertAlmostEqual(bubble.health, 200 - amount)
            self.assertNotIn(doom.body, fight.doomfist_shields)
        return contacts, cast.wave_tick, health.total, later_hits, tuple(owner.mover.position)

    def test_actual_seismic_slam_wave_ends_blossom_only_on_lethal_body_contact(self):
        for aerial in (False, True):
            for case in ("survives", "lethal", "projected"):
                with self.subTest(aerial=aerial, case=case):
                    stepped = self.seismic_slam_blossom_trace(aerial=aerial, case=case, skipped=False)
                    skipped = self.seismic_slam_blossom_trace(aerial=aerial, case=case, skipped=True)
                    self.assertEqual(stepped, skipped)

    def idle_slam_wraith_outcome(self, *, aerial, wraith, gap):
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        owner.mover.position[2] = 10
        owner.mover.body = None
        if aerial:
            doom.mover.position[1] += 7
            doom.mover.body = None
        for _ in range(5):
            run.step({})
        origin = tuple(doom.mover.position)
        heading = aim(fight._eye(doom), (0, 1, 7) if aerial else (0, 2.2, 10))
        run.step({doom: (*heading, 16), owner: (0, 0, 8 if wraith else 0)})
        cast = fight.slams[doom.body]
        self.assertEqual(cast.destination is not None, aerial)
        self.assertIsNone(cast.origin)
        self.assertIsNone(cast.wave_tick)
        self.assertEqual(reaper.intangible(owner), wraith)
        start = run.frame
        old_input = doom.commands.last_frame
        wave_finished = []
        advance = seismic_slam.advance

        def observe(fight, cast):
            advance(fight, cast)
            if cast.finished:
                wave_finished.append((fight.tick, reaper.intangible(owner)))

        with patch.object(seismic_slam, "advance", side_effect=observe):
            for offset in ((230,) if gap else range(1, 231)):
                tick = start + offset
                run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(doom.commands.last_frame, old_input)
        self.assertIsNotNone(cast.wave_tick)
        self.assertTrue(cast.finished)
        self.assertEqual([protected for _, protected in wave_finished], [wraith])
        self.assertFalse(reaper.intangible(owner))
        self.assertGreater(cast.origin[2], origin[2] + 1)
        self.assertLess(abs(cast.origin[1] - 1), 0.05)
        self.assertEqual(cast.hit, set() if wraith else {owner.body})
        self.assertFalse(fight.is_stunned(owner))
        amount = cast.state.instance.find_var(0, 476).value()
        self.assertGreater(amount, 25)
        self.assertLessEqual(amount, 125)
        health = fight.vitals[owner.body].total
        self.assertAlmostEqual(health, 250 if wraith else 250 - amount)
        hits = doom.client.messages(combat.HIT)
        self.assertEqual(len(hits), 0 if wraith else 1)
        ability = cast.state.instance.state(0)
        self.assertFalse(ability.flags & ability.RUNNING)
        cooldown = ability.remaining()
        self.assertGreater(cooldown, 0)
        poses = tuple(owner.mover.position), tuple(doom.mover.position)
        tick = start + 500
        run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(doom.commands.last_frame, old_input)
        self.assertEqual(doom.client.messages(combat.HIT), hits)
        self.assertEqual((tuple(owner.mover.position), tuple(doom.mover.position)), poses)
        return cast.wave_tick, cast.origin, wave_finished, health, hits, cooldown, poses

    def test_actual_seismic_slam_lands_without_owner_input_and_resolves_wraith(self):
        for aerial in (False, True):
            for wraith in (False, True):
                outcomes = []
                for gap in (False, True):
                    with self.subTest(aerial=aerial, wraith=wraith, gap=gap):
                        outcomes.append(self.idle_slam_wraith_outcome(aerial=aerial, wraith=wraith, gap=gap))
                if len(outcomes) == 2:
                    self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_ground_slam_respects_cover_faction_range_and_projection(self):
        for case in ("cover", "ally", "range", "projection"):
            with self.subTest(case=case):
                run, owner, doom, fight = self.charge_scene(
                    hero="Doomfist", names=("Reaper", "Doomfist", "Zarya"),
                    wall=3 if case == "cover" else None,
                )
                ally = run.players[2]
                owner.mover.position[2] = 25 if case == "range" else 10
                owner.mover.body = None
                ally.mover.position[:] = (12, 1, 10)
                ally.mover.body = None
                origin = tuple(owner.mover.position)
                if case in ("ally", "projection"):
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    doom.team, owner.team, ally.team = 1, int(case == "ally"), 0
                if case == "projection":
                    self.assertTrue(run.match.switch_hero(ally, find_hero("Zarya")))
                    ally.mover.position[:] = (4, 1, 10)
                    ally.mover.body = None
                heading = aim(fight._eye(doom), (0, 2.2, origin[2]))
                run.step({doom: (*heading, 16)})
                cast = fight.slams[doom.body]
                bubble = None
                for _ in range(95):
                    run.step({doom: (*heading, 0)})
                    if cast.wave_tick is not None:
                        break
                self.assertIsNotNone(cast.wave_tick)
                if case == "projection":
                    projection = aim(fight._eye(ally), fight._eye(owner))
                    run.step({ally: (*projection, 16)})
                    bubble = fight.zarya_bubbles.for_target(owner)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                for _ in range(35):
                    run.step({})
                self.assertTrue(cast.finished)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(tuple(owner.mover.position), origin)
                self.assertFalse(fight.is_stunned(owner))
                self.assertNotIn(doom.body, fight.doomfist_shields)
                if bubble is None:
                    self.assertNotIn(owner.body, cast.hit)
                else:
                    self.assertIn(owner.body, cast.hit)
                    amount = cast.state.instance.find_var(0, 476).value()
                    self.assertAlmostEqual(bubble.health, 200 - amount)

    def test_actual_lethal_reaper_shell_retires_seismic_slam_before_landing(self):
        fixture = reaper_tests.ReaperTests()
        for aerial in (False, True):
            with self.subTest(aerial=aerial):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                if aerial:
                    doom.mover.position[1] += 7
                    doom.mover.body = None
                for _ in range(5):
                    run.step({})
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
                enemy.damage(enemy.total - 5)
                health = fight.vitals[owner.body]
                health.damage(100)
                heading = aim(fight._eye(doom), (0, 1, 3) if aerial else (0, 2.2, 6))
                run.step({doom: (*heading, 16)})
                cast = fight.slams[doom.body]
                self.assertEqual(cast.destination is not None, aerial)
                self.assertIsNone(cast.wave_tick)
                fixture.fire(run, owner, doom)
                self.assertTrue(fight.is_dead(doom))
                self.assertEqual(enemy.killer, owner.body)
                for _ in range(75):
                    run.step({})
                self.assertNotIn(doom.body, fight.slams)
                self.assertIsNone(cast.wave_tick)
                self.assertFalse(fight.is_stunned(owner))
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertAlmostEqual(health.total, 150 + 5 * reaper.REAPING_RATIO, places=4)

    def test_actual_uppercut_lifts_reaper_without_stun_and_wraith_avoids_contact(self):
        for case in ("normal", "wraith", "blossom"):
            with self.subTest(case=case):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                owner.mover.position[2] = 1.5
                owner.mover.body = None
                start = tuple(owner.mover.position)
                health = fight.vitals[owner.body]
                doom_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
                owner.body_script.set_ult_charge(2100)
                if case == "wraith":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(reaper.intangible(owner))
                run.step({doom: (0, 0, 8), owner: (0, 0, 32 if case == "blossom" else 0)})
                self.assertEqual(health.total, 250 if case == "wraith" else 200)
                self.assertFalse(fight.is_stunned(owner))
                self.assertTrue(doom.mover.launching)
                self.assertGreater(doom.mover.velocity[1], 0)
                if case == "wraith":
                    self.assertNotIn(owner.body, fight.npc_impulses)
                    self.assertNotIn(doom.body, fight.doomfist_shields)
                else:
                    velocity = fight.npc_impulses[owner.body][1]
                    self.assertAlmostEqual(velocity[2], 7)
                    self.assertAlmostEqual(velocity[1], 14)
                    self.assertEqual(doom_pool.temporary_shields[0xDDD].now, 30)
                for _ in range(12):
                    run.step({})
                self.assertGreater(doom.mover.position[1], start[1])
                self.assertEqual(health.total, 250 if case == "wraith" else 200)
                self.assertFalse(fight.is_stunned(owner))
                if case == "wraith":
                    self.assertEqual(tuple(owner.mover.position), start)
                else:
                    self.assertGreater(owner.mover.position[1], start[1])
                    self.assertGreater(owner.mover.position[2], start[2])
                if case == "blossom":
                    self.assertIsNotNone(reaper.blossom_state(owner.body_script))
                    self.assertLess(doom_pool.total, 280)

    def test_actual_uppercut_resolves_shadow_step_phases(self):
        for phase in ("windup", "immune", "landed"):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(phase=phase, skipped=skipped):
                    run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                    owner.mover.position[2] = 1.5
                    owner.mover.body = None
                    origin = tuple(owner.mover.position)
                    destination = self.start_shadow(run, owner)["target"]
                    self.assertGreater(math.dist(origin, destination), 2)
                    if phase != "windup":
                        for _ in range(110):
                            ready = (reaper.intangible(owner) if phase == "immune"
                                     else math.dist(owner.mover.position, destination) < 0.002)
                            if ready:
                                break
                            run.step({})
                        self.assertTrue(ready)
                    if phase == "landed":
                        doom.mover.position[:] = (destination[0], destination[1], destination[2] - 1.5)
                        doom.mover.body = None
                        run.step({})
                    immune = phase == "immune"
                    self.assertEqual(reaper.intangible(owner), immune)
                    run.step({doom: (0, 0, 8)})
                    self.assertTrue(doom.mover.launching)
                    health = fight.vitals[owner.body]
                    self.assertEqual(health.total, 250 if immune else 200)
                    self.assertFalse(fight.is_stunned(owner))
                    self.assertEqual(owner.body in fight.npc_impulses, not immune)
                    self.assertEqual(doom.stats.value(stats.HERO_DAMAGE), 0 if immune else 50)
                    self.assertEqual(doom.body in fight.doomfist_shields, not immune)
                    if not immune:
                        _, velocity = fight.npc_impulses[owner.body]
                        self.assertEqual(velocity, (0, 14, 7))
                    if phase != "landed":
                        self.assertTrue(self.shadow(owner).state(32).active)
                    frame = owner.commands.last_frame
                    until = run.frame + 180
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(owner.commands.last_frame, frame)
                    self.assertFalse(self.shadow(owner).state(32).active)
                    self.assertGreater(self.shadow(owner).vars[61].value(), 0)
                    self.assertFalse(owner.mover.airborne)
                    self.assertNotIn(owner.body, fight.npc_impulses)
                    self.assertNotIn(owner.body, fight.impulse_landings)
                    if phase == "landed":
                        self.assertGreater(owner.mover.position[2], destination[2])
                    else:
                        self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                    outcomes.append((tuple(owner.mover.position), health.total, owner.mover.velocity))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_uppercut_respects_geometry_faction_and_projected_barrier(self):
        for case in ("rear", "range", "wall", "ally", "projection"):
            with self.subTest(case=case):
                run, owner, doom, fight = self.charge_scene(
                    hero="Doomfist", wall=0.75 if case == "wall" else None,
                    names=("Reaper", "Doomfist", "Zarya") if case == "projection" else ("Reaper", "Doomfist"),
                )
                owner.mover.position[2] = 6 if case == "range" else 1.5
                owner.mover.body = None
                origin = tuple(owner.mover.position)
                bubble = None
                if case in ("ally", "projection"):
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    doom.team, owner.team = 1, int(case == "ally")
                if case == "projection":
                    ally = run.players[2]
                    ally.team = owner.team
                    self.assertTrue(run.match.switch_hero(ally, find_hero("Zarya")))
                    ally.mover.position[:] = (4, 1, 1.5)
                    ally.mover.body = None
                    projection = aim(fight._eye(ally), fight._eye(owner))
                    run.step({ally: (*projection, 16)})
                    bubble = fight.zarya_bubbles.for_target(owner)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                run.step({doom: (-32768 if case == "rear" else 0, 0, 8)})
                self.assertTrue(doom.mover.launching)
                for _ in range(24):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(tuple(owner.mover.position), origin)
                self.assertFalse(fight.is_stunned(owner))
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertNotIn(doom.body, fight.doomfist_shields)
                if bubble is not None:
                    self.assertEqual(bubble.health, 150)

    def test_actual_lethal_reaper_shell_resolves_uppercut_contact_boundary(self):
        fixture = reaper_tests.ReaperTests()
        for landed in (False, True):
            with self.subTest(landed=landed):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                owner.mover.position[2] = 1.5
                owner.mover.body = None
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
                enemy.damage(enemy.total - 20)
                own = fight.vitals[owner.body]
                own.damage(100)
                if landed:
                    run.step({doom: (0, 0, 8)})
                    self.assertEqual(own.total, 100)
                    self.assertIn(owner.body, fight.npc_impulses)
                    self.assertEqual(enemy.total, 50)
                fixture.fire(run, owner, doom)
                self.assertTrue(fight.is_dead(doom))
                self.assertEqual(enemy.killer, owner.body)
                self.assertNotIn(doom.body, fight.doomfist_shields)
                if not landed:
                    run.step({doom: (0, 0, 8)})
                for _ in range(24):
                    run.step({})
                self.assertFalse(fight.is_stunned(owner))
                if landed:
                    self.assertGreater(owner.mover.position[1], 1)
                else:
                    self.assertAlmostEqual(owner.mover.position[1], 1)
                    self.assertNotIn(owner.body, fight.npc_impulses)
                expected = (100 + 50 * reaper.REAPING_RATIO if landed
                            else 150 + 20 * reaper.REAPING_RATIO)
                self.assertAlmostEqual(own.total, expected, places=4)

    def test_actual_rocket_punch_damage_wall_and_wraith(self):
        cases = (
            ("tap", 1, None, [450]),
            ("full", 90, None, [400]),
            ("wall", 90, 8, [400, 250]),
            ("wraith", 90, 8, []),
        )
        for case, held, wall, expected in cases:
            with self.subTest(case=case):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=wall)
                fight.vitals[owner.body] = combat.Vitals(owner.body, 500)
                target_pool = fight.vitals[owner.body]
                doom_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
                instance = next(i for i in doom.body_script.component.instances.values()
                                if i.graph.index == 0xD06)
                ability = instance.state(0)
                origin = tuple(owner.mover.position)
                launch = tuple(doom.mover.position)
                for _ in range(held):
                    run.step({doom: (0, 0, 2)})
                self.assertEqual(target_pool.total, 500)
                self.assertEqual(tuple(doom.mover.position), launch)
                self.assertEqual(ability.remaining(), 0)
                run.step({owner: (0, 0, 8)} if case == "wraith" else {})
                if case == "wraith":
                    self.assertTrue(reaper.intangible(owner))
                previous = 500
                totals = []
                for _ in range(90):
                    run.step({})
                    if target_pool.total != previous:
                        if not totals:
                            self.assertTrue(fight.is_stunned(owner))
                            self.assertIn(owner.body, fight.npc_impulses)
                        previous = target_pool.total
                        totals.append(previous)
                self.assertEqual(totals, expected)
                self.assertGreater(doom.mover.position[2], launch[2])
                self.assertGreater(ability.remaining(), 0)
                self.assertFalse(fight.is_stunned(owner))
                self.assertFalse(fight.punch_walls)
                if expected:
                    self.assertGreater(owner.mover.position[2], origin[2])
                    self.assertGreater(doom_pool.total, 250)
                else:
                    self.assertGreater(doom.mover.position[2], origin[2])
                    self.assertEqual(tuple(owner.mover.position), origin)
                    self.assertEqual(doom_pool.total, 250)
                    self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_actual_rocket_punch_interrupts_blossom_and_reaper_hits_temporary_shield(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
        own = fight.vitals[owner.body]
        owner.body_script.set_ult_charge(2100)
        for _ in range(90):
            run.step({doom: (0, 0, 2)})
        run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        for _ in range(40):
            run.step({})
            if fight.is_stunned(owner):
                break
        self.assertTrue(fight.is_stunned(owner))
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(own.total, 150)
        shield = enemy.temporary_shields[0xDDD]
        self.assertEqual(shield.now, 30)
        self.assertIn(doom.body, fight.doomfist_shields)
        before = enemy.total
        for _ in range(60):
            run.step({})
            if not fight.is_stunned(owner):
                break
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(enemy.total, before)
        x, y, z = doom.mover.position
        owner.mover.position[:] = (x, y, z + 1.5)
        owner.mover.body = None
        fixture.fire(run, owner, doom)
        self.assertEqual(shield.now, 0)
        self.assertEqual(enemy.total, before - 120)
        self.assertAlmostEqual(own.total, 150 + 120 * reaper.REAPING_RATIO, places=4)

    def test_actual_lethal_reaper_shell_prevents_rocket_punch_release(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is doom))
        enemy.damage(enemy.total - 20)
        own = fight.vitals[owner.body]
        own.damage(100)
        start = tuple(doom.mover.position)
        for _ in range(35):
            run.step({doom: (0, 0, 2)})
        ability = next(i.state(0) for i in doom.body_script.component.instances.values()
                       if i.graph.index == 0xD06)
        self.assertTrue(ability.active)
        fixture.fire(run, owner, doom)
        self.assertTrue(fight.is_dead(doom))
        self.assertEqual(enemy.killer, owner.body)
        for _ in range(85):
            run.step({})
        self.assertEqual(tuple(doom.mover.position), start)
        self.assertFalse(fight.punch_dashes)
        self.assertFalse(fight.punch_walls)
        self.assertFalse(fight.doomfist_shields)
        self.assertFalse(fight.is_stunned(owner))
        self.assertAlmostEqual(own.total, 150 + 20 * reaper.REAPING_RATIO, places=4)

    def test_actual_rocket_punch_resolves_shadow_immunity_boundary(self):
        for phase in ("windup", "immune", "landed"):
            with self.subTest(phase=phase):
                run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
                origin = tuple(owner.mover.position)
                destination = self.start_shadow(run, owner)["target"]
                self.assertGreater(math.dist(origin, destination), 2)
                if phase == "immune":
                    for _ in range(110):
                        if reaper.intangible(owner):
                            break
                        run.step({doom: (0, 0, 2)})
                    self.assertTrue(reaper.intangible(owner))
                elif phase == "landed":
                    for _ in range(110):
                        if math.dist(owner.mover.position, destination) < 0.002:
                            break
                        run.step({})
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                    self.assertFalse(reaper.intangible(owner))
                    doom.mover.position[0] = destination[0]
                    doom.mover.body = None
                run.step({doom: (0, 0, 2)})
                crossed_while_immune = False
                for _ in range(85):
                    run.step({})
                    crossed_while_immune |= (
                        doom.mover.position[2] >= origin[2] and reaper.intangible(owner)
                        and math.dist(owner.mover.position, origin) < 0.002
                    )
                    if fight.is_stunned(owner):
                        self.assertFalse(self.shadow(owner).state(32).active)
                        break
                protected = phase == "immune"
                self.assertEqual(crossed_while_immune, protected)
                self.assertEqual(fight.is_stunned(owner), not protected)
                self.assertEqual(fight.vitals[owner.body].total, 250 if protected else 200)
                for _ in range(100):
                    run.step({})
                self.assertGreater(self.shadow(owner).vars[61].value(), 0)
                if protected:
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                else:
                    expected_x = origin[0] if phase == "windup" else destination[0]
                    self.assertAlmostEqual(owner.mover.position[0], expected_x, places=3)
                    self.assertGreater(owner.mover.position[2], origin[2])

    def idle_punch_shadow_outcome(self, *, immune, reverse, gap, legacy_anchor=False):
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=7)
        fight.vitals[owner.body] = combat.Vitals(owner.body, 500)
        origin = tuple(owner.mover.position)
        destination = self.start_shadow(run, owner)["target"]
        if immune:
            for _ in range(110):
                if reaper.intangible(owner):
                    break
                run.step({doom: (0, 0, 2)})
            self.assertTrue(reaper.intangible(owner))
        run.step({doom: (0, 0, 2)})
        for _ in range(30):
            run.step({})
            dash = fight.punch_dashes.get(doom.body)
            if dash is not None:
                break
        self.assertIsNotNone(dash)
        self.assertFalse(dash.finished)
        self.assertEqual(fight.vitals[owner.body].total, 500)
        self.assertLess(doom.mover.position[2], origin[2])
        if reverse:
            run.match.players.reverse()
        start = run.frame
        old_input = doom.commands.last_frame
        if legacy_anchor:
            doom.body_script.world_frame_anchor = None
        crossings, interrupts = [], []
        shadow = self.shadow(owner)
        advance = rocket_punch.advance

        def observe(fight, player, command, punch, before, outcome):
            if before[2] < origin[2] <= player.mover.position[2]:
                crossings.append((fight.tick, reaper.intangible(owner)))
            windup = shadow.state(32).active
            health = fight.vitals[owner.body].total
            advance(fight, player, command, punch, before, outcome)
            if fight.vitals[owner.body].total < health:
                interrupts.append((fight.tick, windup, shadow.state(32).active, fight.is_stunned(owner)))

        with patch.object(rocket_punch, "advance", side_effect=observe):
            for offset in ((150,) if gap else range(1, 151)):
                tick = start + offset
                run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(doom.commands.last_frame, old_input)
        self.assertTrue(dash.finished)
        self.assertFalse(fight.punch_walls)
        self.assertFalse(fight.is_stunned(owner))
        self.assertFalse(reaper.intangible(owner))
        health = fight.vitals[owner.body].total
        if immune:
            self.assertEqual([protected for _, protected in crossings], [True])
            self.assertFalse(interrupts)
            self.assertEqual(health, 500)
            self.assertLess(math.dist(owner.mover.position, destination), 0.002)
        else:
            self.assertEqual([impact[1:] for impact in interrupts], [(True, False, True)])
            self.assertAlmostEqual(health, 500 - dash.damage - dash.wall_damage)
            self.assertAlmostEqual(owner.mover.position[0], origin[0], places=3)
            self.assertGreater(owner.mover.position[2], origin[2])
        hits = doom.client.messages(combat.HIT)
        self.assertEqual(len(hits), 0 if immune else 2)
        self.assertGreater(shadow.vars[61].value(), 0)
        ability = dash.state.instance.state(0)
        cooldown = ability.remaining()
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertGreater(cooldown, 0)
        positions = tuple(owner.mover.position), tuple(doom.mover.position)
        tick = start + 400
        run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(doom.commands.last_frame, old_input)
        self.assertEqual(positions, (tuple(owner.mover.position), tuple(doom.mover.position)))
        self.assertEqual(doom.client.messages(combat.HIT), hits)
        return health, positions, crossings, interrupts, hits, cooldown

    def test_actual_rocket_punch_crosses_shadow_step_without_owner_input(self):
        for immune in (False, True):
            outcomes = []
            for reverse in (False, True):
                for gap in (False, True):
                    with self.subTest(immune=immune, reverse=reverse, gap=gap):
                        outcome = self.idle_punch_shadow_outcome(immune=immune, reverse=reverse, gap=gap)
                        outcomes.append(outcome)
            self.assertTrue(all(outcome == outcomes[0] for outcome in outcomes))
            if not immune and outcomes:
                restored = self.idle_punch_shadow_outcome(
                    immune=False, reverse=False, gap=True, legacy_anchor=True,
                )
                self.assertEqual(restored, outcomes[0])

    def test_actual_projection_rejects_rocket_punch_control_even_on_break(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, owner, doom, fight = self.charge_scene(
                    hero="Doomfist", names=("Reaper", "Doomfist", "Zarya"), wall=8,
                )
                ally = run.players[2]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = ally.team = 0
                doom.team = 1
                self.assertTrue(run.match.switch_hero(ally, find_hero("Zarya")))
                ally.mover.position[:] = (4, 1, 6)
                ally.mover.body = None
                origin = tuple(owner.mover.position)
                projection = aim(fight._eye(ally), fight._eye(owner))
                for _ in range(90):
                    run.step({doom: (0, 0, 2)})
                run.step({ally: (*projection, 16)})
                bubble = fight.zarya_bubbles.for_target(owner)
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.health, 200)
                if breaking:
                    bubble.health = 50
                for _ in range(45):
                    run.step({})
                    self.assertFalse(fight.is_stunned(owner))
                self.assertEqual(bubble.health, 0 if breaking else 100)
                self.assertEqual(fight.zarya_bubbles.for_target(owner) is None, breaking)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(tuple(owner.mover.position), origin)
                self.assertNotIn(doom.body, fight.doomfist_shields)
                self.assertFalse(fight.punch_walls)

    def test_actual_rocket_punch_misses_reaper_behind_cover_outside_range_or_allied(self):
        for case in ("cover", "range", "ally"):
            with self.subTest(case=case):
                run, owner, doom, fight = self.charge_scene(
                    hero="Doomfist", wall=3 if case == "cover" else None,
                )
                if case == "range":
                    owner.mover.position[2] = 25
                    owner.mover.body = None
                elif case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    doom.team = owner.team
                origin = tuple(owner.mover.position)
                for _ in range(90):
                    run.step({doom: (0, 0, 2)})
                for _ in range(55):
                    run.step({})
                    self.assertFalse(fight.is_stunned(owner))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(tuple(owner.mover.position), origin)
                self.assertFalse(fight.punch_walls)
                self.assertNotIn(doom.body, fight.doomfist_shields)
                self.assertGreater(doom.mover.position[2], 0)
                if case == "ally":
                    self.assertGreater(doom.mover.position[2], origin[2])
                else:
                    self.assertLess(doom.mover.position[2], 3 if case == "cover" else origin[2])

    def test_actual_rocket_punch_cooldown_refuses_then_accepts_fresh_tap(self):
        run, owner, doom, fight = self.charge_scene(hero="Doomfist", wall=None)
        ability = next(i.state(0) for i in doom.body_script.component.instances.values()
                       if i.graph.index == 0xD06)
        run.step({doom: (0, 0, 2)})
        for _ in range(80):
            run.step({})
            if ability.remaining() > 0:
                break
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(fight.vitals[owner.body].total, 200)
        for _ in range(45):
            run.step({})
        self.assertFalse(fight.is_stunned(owner))
        self.assertFalse(fight.punch_walls)
        for actor, position in ((doom, (0, 1, 0)), (owner, (0, 1, 6))):
            actor.mover.position[:] = position
            actor.mover.body = None
        self.assertGreater(ability.remaining(), 0.8)
        run.step({doom: (0, 0, 2)})
        for _ in range(40):
            run.step({})
        self.assertEqual(doom.mover.position[2], 0)
        self.assertEqual(fight.vitals[owner.body].total, 200)
        for _ in range(300):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(doom.mover.position[2], 0)
        run.step({doom: (0, 0, 2)})
        for _ in range(45):
            run.step({})
        self.assertGreater(doom.mover.position[2], 0)
        self.assertEqual(fight.vitals[owner.body].total, 150)
        self.assertGreater(ability.remaining(), 0)

    def test_actual_reinhardt_charge_pins_blossom_and_passes_through_wraith(self):
        for wraith in (False, True):
            with self.subTest(wraith=wraith):
                run, owner, rein, fight = self.charge_scene()
                fight.vitals[owner.body] = combat.Vitals(owner.body, 500)
                owner.body_script.set_ult_charge(2100)
                run.step({rein: (0, 0, 8), owner: (0, 0, 8 if wraith else 32)})
                self.assertEqual(reaper.intangible(owner), wraith)
                self.assertEqual(reaper.blossom_state(owner.body_script) is not None, not wraith)
                observed_drive = False
                captured_at = None
                crossed_while_wraith = False
                for _ in range(100):
                    run.step({})
                    observed_drive |= rein.body in fight.charges
                    crossed_while_wraith |= rein.mover.position[2] >= 6 and reaper.intangible(owner)
                    if reinhardt_charge.pinned(fight, owner.body) and captured_at is None:
                        self.assertTrue(fight.is_stunned(owner))
                        self.assertIsNone(reaper.blossom_state(owner.body_script))
                        self.assertEqual(fight.vitals[owner.body].total, 500)
                        captured_at = owner.mover.position[2]
                    if observed_drive and rein.body not in fight.charges:
                        break
                self.assertTrue(observed_drive)
                self.assertGreater(rein.mover.position[2], 6)
                self.assertFalse(fight.charges)
                self.assertFalse(fight.is_stunned(owner))
                self.assertEqual(fight.vitals[owner.body].total, 500 if wraith else 200)
                if wraith:
                    self.assertTrue(crossed_while_wraith)
                    self.assertIsNone(captured_at)
                    self.assertAlmostEqual(owner.mover.position[2], 6)
                else:
                    self.assertIsNotNone(captured_at)
                    self.assertGreater(owner.mover.position[2], captured_at)
                for _ in range(24):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 500 if wraith else 200)

    def test_actual_reinhardt_charge_retires_pin_when_either_body_is_replaced(self):
        for replace_source in (False, True):
            with self.subTest(replace_source=replace_source):
                run, owner, rein, fight = self.charge_scene()
                run.step({rein: (0, 0, 8)})
                for _ in range(100):
                    run.step({})
                    if reinhardt_charge.pinned(fight, owner.body):
                        break
                previous_body = owner.body
                self.assertTrue(reinhardt_charge.pinned(fight, previous_body))
                actor = rein if replace_source else owner
                self.assertTrue(run.match.switch_hero(actor, find_hero("Soldier: 76")))
                self.assertFalse(reinhardt_charge.pinned(fight, previous_body))
                self.assertFalse(fight.is_stunned(owner))
                for _ in range(45):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 250 if replace_source else 200)
                self.assertFalse(fight.charges)

    def test_actual_reinhardt_charge_resolves_shadow_immunity_boundary(self):
        for phase in ("windup", "immune", "landed"):
            with self.subTest(phase=phase):
                run, owner, rein, fight = self.charge_scene()
                fight.vitals[owner.body] = combat.Vitals(owner.body, 500)
                origin = tuple(owner.mover.position)
                record = self.start_shadow(run, owner)
                destination = record["target"]
                self.assertGreater(math.dist(origin, destination), 2)
                shadow = self.shadow(owner)
                if phase == "immune":
                    # Let the run cross the departure point before the teleport.
                    for _ in range(20):
                        run.step({})
                elif phase == "landed":
                    for _ in range(110):
                        run.step({})
                        if math.dist(owner.mover.position, destination) < 0.002:
                            break
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                    self.assertFalse(reaper.intangible(owner))
                    rein.mover.position[0] = destination[0]
                    rein.mover.body = None
                run.step({rein: (0, 0, 8)})
                drive = captured = crossed_while_immune = False
                for _ in range(110):
                    run.step({})
                    charge = fight.charges.get(rein.body)
                    if charge is not None:
                        drive = True
                        if phase == "immune":
                            self.assertNotIn(owner.body, charge.hit)
                            crossed_while_immune |= (
                                rein.mover.position[2] >= origin[2] and reaper.intangible(owner)
                            )
                        if reinhardt_charge.pinned(fight, owner.body) and not captured:
                            captured = True
                            self.assertTrue(fight.is_stunned(owner))
                            self.assertFalse(reaper.intangible(owner))
                            self.assertFalse(shadow.state(32).active)
                            self.assertEqual(fight.vitals[owner.body].total, 500)
                    elif drive:
                        break
                self.assertTrue(drive)
                self.assertFalse(fight.charges)
                self.assertFalse(fight.is_stunned(owner))
                self.assertEqual(captured, phase != "immune")
                self.assertEqual(fight.vitals[owner.body].total, 500 if phase == "immune" else 200)
                for _ in range(65):
                    run.step({})
                self.assertGreater(shadow.vars[61].value(), 0)
                if phase == "immune":
                    self.assertTrue(crossed_while_immune)
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                else:
                    expected_x = origin[0] if phase == "windup" else destination[0]
                    self.assertAlmostEqual(owner.mover.position[0], expected_x, places=3)
                    self.assertGreater(owner.mover.position[2], origin[2])

    def shadow_confirm_charge_scene(self):
        run, owner, rein, fight = self.charge_scene()
        fight.vitals[owner.body] = combat.Vitals(owner.body, 500)
        self.start_shadow(run, owner, confirm=False)
        run.step({rein: (0, 0, 8), owner: (16384, 5000, 0)})
        return run, owner, rein, fight

    def test_actual_charge_on_shadow_confirm_tick_retires_pending_cast(self):
        run, owner, _, fight = self.shadow_confirm_charge_scene()
        start = run.frame
        for _ in range(100):
            run.step({owner: (16384, 5000, 0)})
            if reinhardt_charge.pinned(fight, owner.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, owner.body))
        travel = run.frame - start
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                outcomes.append(self.same_tick_shadow_charge_trace(travel, skipped=skipped))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])

    def same_tick_shadow_charge_trace(self, travel, *, skipped):
        run, owner, rein, fight = self.shadow_confirm_charge_scene()
        origin = tuple(owner.mover.position)
        for _ in range(travel - 1):
            run.step({owner: (16384, 5000, 0)})
        self.assertFalse(reinhardt_charge.pinned(fight, owner.body))
        run.step({owner: (16384, 5000, 1)})
        self.assertTrue(reinhardt_charge.pinned(fight, owner.body))
        self.assertEqual(fight.vitals[owner.body].total, 500)
        record = fight.reaper_shadows[owner.body]
        cast = self.shadow(owner).state(32)
        self.assertEqual(record["cast"], (cast.counter, cast.start))
        self.assertFalse(cast.active)
        self.assertEqual(cast.ended_at, cast.start)
        start = run.frame
        end = start + 165
        old_input = owner.commands.last_frame
        for tick in (end,) if skipped else range(start + 1, end + 1):
            run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(fight._shadow_pending())
        self.assertFalse(fight.is_stunned(owner))
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(owner.commands.last_frame, old_input)
        self.assertEqual(fight.vitals[owner.body].total, 200)
        self.assertAlmostEqual(owner.mover.position[0], origin[0], places=3)
        self.assertGreater(owner.mover.position[2], origin[2])
        cooldown = self.shadow(owner).vars[61].value()
        self.assertGreater(cooldown, 0)
        endpoint = end + 80
        self.assertEqual(list(fight._retained_cast_ticks({endpoint})), [endpoint])
        hits = rein.client.messages(combat.HIT)
        return cooldown, tuple(owner.mover.position), tuple(rein.mover.position), hits

    def idle_charge_shadow_outcome(self, *, immune, reverse, gap):
        run, owner, rein, fight = self.charge_scene()
        fight.vitals[owner.body] = combat.Vitals(owner.body, 500)
        origin = tuple(owner.mover.position)
        destination = self.start_shadow(run, owner)["target"]
        if immune:
            for _ in range(20):
                run.step({})
        run.step({rein: (0, 0, 8)})
        for _ in range(75):
            run.step({})
            charge = fight.charges.get(rein.body)
            if charge is not None:
                break
        self.assertIsNotNone(charge)
        self.assertNotIn(owner.body, charge.hit)
        if reverse:
            run.match.players.reverse()
        start = run.frame
        old_input = rein.commands.last_frame
        crossings = []
        advance = reinhardt_charge.advance

        def observe(fight, player, command, before, forward, seconds):
            if before[2] < origin[2] <= player.mover.position[2]:
                crossings.append((fight.tick, reaper.intangible(owner)))
            advance(fight, player, command, before, forward, seconds)

        with patch.object(reinhardt_charge, "advance", side_effect=observe):
            for offset in ((150,) if gap else range(1, 151)):
                tick = start + offset
                run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(rein.commands.last_frame, old_input)
        self.assertFalse(fight.charges)
        self.assertFalse(fight.is_stunned(owner))
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual([protected for _, protected in crossings], [immune])
        self.assertEqual(owner.body in charge.hit, not immune)
        health = fight.vitals[owner.body].total
        self.assertEqual(health, 500 if immune else 200)
        if immune:
            self.assertLess(math.dist(owner.mover.position, destination), 0.002)
        else:
            self.assertAlmostEqual(owner.mover.position[0], origin[0], places=3)
            self.assertGreater(owner.mover.position[2], origin[2])
        self.assertGreater(self.shadow(owner).vars[61].value(), 0)
        return health, tuple(owner.mover.position), tuple(rein.mover.position), tuple(crossings)

    def test_actual_reinhardt_charge_crosses_shadow_step_without_owner_input(self):
        for immune in (False, True):
            outcomes = []
            for reverse in (False, True):
                for gap in (False, True):
                    with self.subTest(immune=immune, reverse=reverse, gap=gap):
                        outcome = self.idle_charge_shadow_outcome(immune=immune, reverse=reverse, gap=gap)
                        outcomes.append(outcome)
            self.assertLessEqual(len(set(outcomes)), 1)

    def test_actual_lethal_reaper_shell_stops_reinhardt_charge_before_and_after_pin(self):
        fixture = reaper_tests.ReaperTests()
        for live in (False, True):
            with self.subTest(live=live):
                run, owner, rein, fight = self.charge_scene(names=("Pinned", "Reinhardt", "Shooter"))
                shooter = run.players[2]
                self.assertTrue(run.match.switch_hero(shooter, heroes()[reaper.HERO]))
                shooter.mover.position[:] = (10, 1, 0)
                shooter.mover.body = None
                instance = next(i for i in rein.body_script.component.instances.values()
                                if i.graph.index == 0x7D)
                run.step({rein: (0, 0, 8)})
                self.assertTrue(instance.state(0).active)
                if live:
                    for _ in range(100):
                        run.step({})
                        if reinhardt_charge.pinned(fight, owner.body):
                            break
                self.assertEqual(reinhardt_charge.pinned(fight, owner.body), live)
                self.assertEqual(fight.is_stunned(owner), live)
                self.assertEqual(rein.body in fight.charges, live)
                x, y, z = rein.mover.position
                shooter.mover.position[:] = (x + 1.3, y, z - 0.8)
                shooter.mover.body = None
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is rein))
                health.damage(health.total - 20)
                shooter_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
                shooter_health.damage(100)
                healed = shooter_health.total + health.total * reaper.REAPING_RATIO
                fixture.fire(run, shooter, rein)
                self.assertTrue(fight.is_dead(rein))
                self.assertEqual(health.killer, shooter.body)
                self.assertAlmostEqual(shooter_health.total, healed, places=4)
                self.assertFalse(fight.is_stunned(owner))
                self.assertFalse(fight.charges)
                released_position = tuple(owner.mover.position)
                for _ in range(75):
                    run.step({})
                    self.assertFalse(fight.charges)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertAlmostEqual(owner.mover.position[2], released_position[2], places=3)

    def test_actual_accretion_bypasses_front_deflect_and_grasp_then_reaper_hits(self):
        for name, graph, state, button in (("Genji", 0x03A3, 10, 16), ("Sigma", 0x1D02, 1, 8)):
            with self.subTest(defender=name):
                fixture = reaper_tests.ReaperTests()
                run, owner, caster, _ = fixture.scenario(names=("Reaper", "Caster", "Defender"))
                defender = run.players[2]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = caster.team = 0
                defender.team = 1
                self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
                self.assertTrue(run.match.switch_hero(defender, find_hero(name)))
                fight = run.match.combat
                x, y, z = caster.mover.position
                owner.mover.position[:] = (x - 1, y, z + 4)
                defender.mover.position[:] = (x, y, z + 6)
                owner.mover.body = defender.mover.body = None
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                before = enemy.total
                caster_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is caster))
                aim_rock = aim(fight._eye(caster), fight._eye(defender))
                aim_defense = aim(fight._eye(defender), fight._eye(caster))
                run.step({caster: (*aim_rock, 16), defender: (*aim_defense, button)})
                defense = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == graph)
                self.assertTrue(defense.state(state).active)
                for _ in range(100):
                    run.step({caster: (*aim_rock, 0), defender: (*aim_defense, 0)})
                    if fight.rockets:
                        break
                rock, = fight.rockets
                self.assertIsNotNone(rock.accretion_source)
                self.assertTrue(defense.state(state).active)
                contact = fight._projectile_defense_contact(
                    caster, rock.position, rock.direction, 6, moving=True, definition=rock.definition,
                )
                self.assertIsNotNone(contact)
                self.assertIs(contact[1], defender)
                for _ in range(45):
                    run.step({defender: (*aim_defense, 0)})
                    if rock not in fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertFalse(rock.reflected)
                self.assertEqual(caster_pool.total, 400)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(enemy.total, before - 70)
                self.assertTrue(fight.is_stunned(defender))
                self.assertFalse(defense.state(state).active)
                if name == "Sigma":
                    self.assertEqual(defense.vars[21312].value(), 0)
                    self.assertNotIn(defender.body, fight.grasp_shields)
                fixture.fire(run, owner, defender)
                self.assertLess(enemy.total, before - 70)

    def test_actual_accretion_resolves_shadow_immunity_boundary(self):
        for phase in ("windup", "immune", "landed"):
            with self.subTest(phase=phase):
                run, owner, caster, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
                fight = run.match.combat
                x, y, z = caster.mover.position
                owner.mover.position[:] = (x, y, z + 6)
                owner.mover.body = None
                origin = tuple(owner.mover.position)
                destination = self.start_shadow(run, owner)["target"]
                self.assertGreater(math.dist(origin, destination), 2)
                if phase == "immune":
                    for _ in range(20):
                        run.step({})
                elif phase == "landed":
                    for _ in range(110):
                        run.step({})
                        if math.dist(owner.mover.position, destination) < 0.002:
                            break
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                    self.assertFalse(reaper.intangible(owner))
                    caster.mover.position[0] = destination[0]
                    caster.mover.body = None
                heading = aim(fight._eye(caster), fight._eye(owner))
                run.step({caster: (*heading, 16)})
                rock = None
                crossed_while_immune = False
                for _ in range(100):
                    run.step({caster: (*heading, 0)})
                    if rock is None and fight.rockets:
                        rock, = fight.rockets
                    elif rock is not None and rock not in fight.rockets:
                        break
                    if rock is not None and rock in fight.rockets:
                        crossed_while_immune |= (
                            rock.position[2] >= origin[2] and reaper.intangible(owner)
                            and math.dist(owner.mover.position, origin) < 0.002
                        )
                self.assertIsNotNone(rock)
                self.assertIsNotNone(rock.accretion_source)
                self.assertFalse(fight.rockets)
                protected = phase == "immune"
                self.assertEqual(crossed_while_immune, protected)
                self.assertEqual(fight.is_stunned(owner), not protected)
                self.assertEqual(fight.vitals[owner.body].total, 250 if protected else 180)
                if protected:
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                    self.assertNotIn(owner.body, fight.npc_impulses)
                else:
                    self.assertFalse(self.shadow(owner).state(32).active)
                    self.assertIn(owner.body, fight.npc_impulses)
                for _ in range(65):
                    run.step({})
                self.assertGreater(self.shadow(owner).vars[61].value(), 0)
                if protected:
                    self.assertLess(math.dist(owner.mover.position, destination), 0.002)
                else:
                    expected_x = origin[0] if phase == "windup" else destination[0]
                    self.assertAlmostEqual(owner.mover.position[0], expected_x, places=3)
                    self.assertGreater(owner.mover.position[2], origin[2])

    def test_actual_accretion_interrupts_blossom_and_wraith_rejects_contact(self):
        for wraith in (False, True):
            with self.subTest(wraith=wraith):
                run, owner, caster, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
                fight = run.match.combat
                x, y, z = caster.mover.position
                owner.mover.position[:] = (x, y, z + 6)
                owner.mover.body = None
                origin = tuple(owner.mover.position)
                heading = aim(fight._eye(caster), fight._eye(owner))
                owner.body_script.set_ult_charge(2100)
                run.step({caster: (*heading, 16), owner: (0, 0, 8 if wraith else 32)})
                self.assertEqual(reaper.intangible(owner), wraith)
                self.assertEqual(reaper.blossom_state(owner.body_script) is not None, not wraith)
                rocket = None
                for _ in range(100):
                    run.step({caster: (*heading, 0)})
                    if rocket is None:
                        rocket = next((r for r in fight.rockets if r.accretion_source is not None), None)
                    elif rocket not in fight.rockets:
                        break
                self.assertIsNotNone(rocket)
                self.assertNotIn(rocket, fight.rockets)
                self.assertGreater(rocket.flown, 0)
                self.assertEqual(fight.is_stunned(owner), not wraith)
                self.assertEqual(fight.vitals[owner.body].total, 250 if wraith else 180)
                if wraith:
                    self.assertTrue(reaper.intangible(owner))
                    self.assertEqual(tuple(owner.mover.position), origin)
                    self.assertNotIn(owner.body, fight.npc_impulses)
                else:
                    self.assertIsNone(reaper.blossom_state(owner.body_script))
                    self.assertEqual(fight.stuns[owner.body] - fight.tick, 50)
                    self.assertGreater(fight.npc_impulses[owner.body][1][2], 4.5)
                    for _ in range(50):
                        run.step({})
                    self.assertFalse(fight.is_stunned(owner))
                    self.assertGreater(owner.mover.position[2], origin[2])
                    self.assertEqual(fight.vitals[owner.body].total, 180)

    def accretion_wraith_boundary_scene(self, *, names=("Reaper", "Sigma")):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
        x, y, z = caster.mover.position
        owner.mover.position[:] = (x, y, z + 6)
        owner.mover.body = None
        for _ in range(5):
            run.step({})
        return run, owner, caster, run.match.combat

    def launch_boundary_accretion(self, run, owner, caster, fight):
        heading = aim(fight._eye(caster), fight._eye(owner))
        run.step({caster: (*heading, 16)})
        fired = run.frame
        for _ in range(100):
            if fight.rockets:
                break
            run.step({caster: (*heading, 0)})
        rock, = fight.rockets
        self.assertIsNotNone(rock.accretion_source)
        return fired, rock

    def accretion_wraith_boundary_trace(self, travel, duration, *, offset, skipped):
        run, owner, caster, fight = self.accretion_wraith_boundary_scene()
        run.step({owner: (0, 0, 8)})
        expiry = run.frame + duration
        fire_at = expiry + offset - travel
        while run.frame < fire_at - 1:
            run.step({})
        fired, rock = self.launch_boundary_accretion(run, owner, caster, fight)
        self.assertEqual(fired, fire_at)
        self.assertTrue(reaper.intangible(owner))
        target = next(target for target in fight._targets() if target.owner is owner)
        a, b = target.segment()
        samples, stuns = [], []
        fly = fight._fly_step

        def observe(shot, duration, flight_tick):
            if shot is rock and fight.tick == expiry:
                inside = combat.segment_distance(rock.position, a, b) <= target.radius
                samples.append((fight.tick, reaper.intangible(owner), inside))
                if offset == -2:
                    self.assertGreater(rock.position[2], target.feet[2])
            fly(shot, duration, flight_tick)
            if shot is rock and owner.body in fight.stuns:
                stuns.append((fight.tick, fight.stuns[owner.body]))

        end = expiry + 55
        frame = owner.commands.last_frame
        with patch.object(fight, "_fly_step", side_effect=observe):
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(samples, [(expiry, False, offset == -1)])
        hit = offset >= -1
        self.assertEqual(stuns, [(expiry, expiry + 50)] if hit else [])
        self.assertEqual(fight.vitals[owner.body].total, 180 if hit else 250)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 70 if hit else 0)
        hits = [(event["+0x84"], event["+0x78"]["+0x4"]) for event in caster.client.messages(combat.HIT)]
        self.assertEqual(hits, [(expiry, 30), (expiry, 40)] if hit else [])
        self.assertEqual(owner.commands.last_frame, frame)
        self.assertFalse(reaper.intangible(owner))
        self.assertFalse(fight.is_stunned(owner))
        return samples, stuns, hits, tuple(owner.mover.position)

    def test_actual_accretion_resolves_capsule_reentry_at_wraith_expiry(self):
        run, owner, caster, fight = self.accretion_wraith_boundary_scene()
        fired, rock = self.launch_boundary_accretion(run, owner, caster, fight)
        for _ in range(30):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if rock not in fight.rockets:
                break
        self.assertEqual(fight.vitals[owner.body].total, 180)
        travel = run.frame - fired

        run, owner, _, _ = self.accretion_wraith_boundary_scene()
        run.step({owner: (0, 0, 8)})
        start = run.frame
        for _ in range(220):
            if not reaper.intangible(owner):
                break
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(reaper.intangible(owner))
        duration = run.frame - start
        for offset in (-2, -1, 0):
            with self.subTest(offset=offset):
                stepped = self.accretion_wraith_boundary_trace(travel, duration, offset=offset, skipped=False)
                skipped = self.accretion_wraith_boundary_trace(travel, duration, offset=offset, skipped=True)
                self.assertEqual(stepped, skipped)

    def accretion_splash_reaper_trace(self, *, protected, skipped):
        run, owner, caster, fight = self.accretion_wraith_boundary_scene(
            names=("Reaper", "Sigma", "Flanking Reaper"),
        )
        flank = run.players[2]
        self.assertTrue(run.match.switch_hero(flank, heroes()[reaper.HERO]))
        x, y, z = owner.mover.position
        flank.mover.position[:] = (x + 1.5, y, z)
        flank.mover.body = None
        for _ in range(5):
            run.step({})
        origin = tuple(flank.mover.position)
        if protected:
            run.step({flank: (0, 0, 8)})
        _, rock = self.launch_boundary_accretion(run, owner, caster, fight)
        self.assertEqual(rock.direct.amount, 30)
        self.assertEqual(rock.blast.amount, 40)
        self.assertEqual(reaper.intangible(flank), protected)
        frame = flank.commands.last_frame
        end = run.frame + 25
        while run.frame < end:
            run.frame = end if skipped else run.frame + 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(fight.rockets)
        self.assertEqual(flank.commands.last_frame, frame)
        self.assertEqual(fight.vitals[owner.body].total, 180)
        self.assertTrue(fight.is_stunned(owner))
        self.assertFalse(fight.is_stunned(flank))
        hits = [
            (hit["+0x84"], hit["+0x78"]["+0x0"]["+0x0"], hit["+0x78"]["+0x4"])
            for hit in caster.client.messages(combat.HIT)
        ]
        impact = hits[0][0]
        self.assertEqual(hits[:2], [(impact, owner.body, 30), (impact, owner.body, 40)])
        flank_health = fight.vitals[flank.body].total
        if protected:
            self.assertEqual(hits[2:], [])
            self.assertEqual(flank_health, 250)
            self.assertEqual(tuple(flank.mover.position), origin)
            self.assertNotIn(flank.body, fight.npc_impulses)
        else:
            self.assertEqual(hits[2:], [(impact, flank.body, 40)])
            self.assertEqual(flank_health, 210)
            self.assertGreater(flank.mover.position[0], origin[0])
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 70 if protected else 110)
        return hits, tuple(owner.mover.position), tuple(flank.mover.position), flank_health

    def test_actual_accretion_separates_direct_and_splash_reaper_contact(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                stepped = self.accretion_splash_reaper_trace(protected=protected, skipped=False)
                skipped = self.accretion_splash_reaper_trace(protected=protected, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_actual_projected_barrier_occludes_accretion_splash_and_body_force(self):
        for starting_shield in (None, 200, 20):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(starting_shield=starting_shield, skipped=skipped):
                    run, owner, caster, fight = self.accretion_wraith_boundary_scene(
                        names=("Reaper", "Sigma", "Flanking Reaper", "Zarya"),
                    )
                    flank, zarya = run.players[2:]
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = flank.team = zarya.team = 0
                    caster.team = 1
                    self.assertTrue(run.match.switch_hero(flank, heroes()[reaper.HERO]))
                    self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
                    x, y, z = owner.mover.position
                    flank.mover.position[:] = (x + 1.5, y, z)
                    zarya.mover.position[:] = (x + 5, y, z + 3)
                    flank.mover.body = zarya.mover.body = None
                    _, rock = self.launch_boundary_accretion(run, owner, caster, fight)
                    self.assertEqual(rock.blast.amount, 40)
                    bubble = None
                    if starting_shield is not None:
                        run.step({zarya: (*aim(fight._eye(zarya), fight._eye(flank)), 16)})
                        self.assertIn(rock, fight.rockets)
                        bubble = fight.zarya_bubbles.for_target(flank)
                        self.assertIsNotNone(bubble)
                        self.assertEqual(bubble.health, 200)
                        bubble.health = starting_shield
                    origin = tuple(flank.mover.position)
                    end = run.frame + 25
                    while run.frame < end:
                        run.frame = end if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertNotIn(rock, fight.rockets)
                    self.assertEqual(fight.vitals[owner.body].total, 180)
                    self.assertTrue(fight.is_stunned(owner))
                    flank_health = fight.vitals[flank.body].total
                    self.assertFalse(fight.is_stunned(flank))
                    if bubble is None:
                        self.assertEqual(flank_health, 210)
                        self.assertGreater(flank.mover.position[0], origin[0])
                        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 110)
                    else:
                        self.assertEqual(bubble.health, starting_shield)
                        self.assertIs(fight.zarya_bubbles.for_target(flank), bubble)
                        self.assertEqual(flank_health, 250)
                        self.assertNotIn(flank.body, fight.npc_impulses)
                        self.assertEqual(tuple(flank.mover.position), origin)
                        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 70)
                    hits = [event["+0x84"] for event in caster.client.messages(combat.HIT)]
                    self.assertEqual(len(set(hits)), 1)
                    outcomes.append((
                        hits, tuple(owner.mover.position), tuple(flank.mover.position), flank_health,
                    ))
            if len(outcomes) == 2:
                self.assertEqual(*outcomes)

    def test_actual_accretion_respects_static_cover_and_retained_flight_ticks(self):
        outcomes = {}
        for case in ("clear", "gap", "wall"):
            with self.subTest(case=case):
                run, owner, caster, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
                fight = run.match.combat
                x, y, z = caster.mover.position
                owner.mover.position[:] = (x, y, z + 6)
                owner.mover.body = None
                if case == "wall":
                    wall = z + 3
                    run.match.collision_world = World([
                        (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
                        (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
                        (x - 5, y - 1, wall, x + 5, y + 5, wall, x + 5, y - 1, wall),
                        (x - 5, y - 1, wall, x - 5, y + 5, wall, x + 5, y + 5, wall),
                    ], name="Accretion cover")
                heading = aim(fight._eye(caster), fight._eye(owner))
                run.step({caster: (*heading, 16)})
                for _ in range(100):
                    run.step({caster: (*heading, 0)})
                    if fight.rockets:
                        break
                rocket, = fight.rockets
                self.assertIsNotNone(rocket.accretion_source)
                self.assertGreater(rocket.gravity, 0)
                self.assertGreater(rocket.direction[1], 0)
                start_tick = run.frame
                for offset in ((24,) if case == "gap" else range(1, 25)):
                    tick = start_tick + offset
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertFalse(fight.rockets)
                self.assertGreater(rocket.flown, 0)
                self.assertEqual(fight.vitals[owner.body].total, 250 if case == "wall" else 180)
                self.assertEqual(fight.is_stunned(owner), case != "wall")
                outcomes[case] = (fight.stuns.get(owner.body), rocket.flown, tuple(owner.mover.position))
        self.assertEqual(outcomes["clear"], outcomes["gap"])
        self.assertLess(outcomes["wall"][1], outcomes["clear"][1])

    def test_actual_projected_barrier_blocks_accretion_damage_control_and_impulse(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, owner, caster, _ = reaper_tests.ReaperTests().scenario(
                    names=("Reaper", "Sigma", "Zarya"),
                )
                ally = run.players[2]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                caster.team = 1
                owner.team = ally.team = 0
                self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
                self.assertTrue(run.match.switch_hero(ally, find_hero("Zarya")))
                x, y, z = caster.mover.position
                owner.mover.position[:] = (x, y, z + 6)
                ally.mover.position[:] = (x + 4, y, z + 6)
                owner.mover.body = ally.mover.body = None
                origin = tuple(owner.mover.position)
                heading = aim(run.match.combat._eye(caster), run.match.combat._eye(owner))
                projection = aim(run.match.combat._eye(ally), run.match.combat._eye(owner))
                fight = run.match.combat
                run.step({caster: (*heading, 16)})
                for _ in range(100):
                    run.step({caster: (*heading, 0)})
                    if fight.rockets:
                        break
                rock, = fight.rockets
                self.assertIsNotNone(rock.accretion_source)
                run.step({ally: (*projection, 16)})
                bubble = fight.zarya_bubbles.for_target(owner)
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.health, 200)
                if breaking:
                    bubble.health = 20
                for _ in range(24):
                    run.step({})
                self.assertNotIn(rock, fight.rockets)
                self.assertEqual(bubble.health, 0 if breaking else 130)
                self.assertEqual(fight.zarya_bubbles.for_target(owner) is None, breaking)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertFalse(fight.is_stunned(owner))
                self.assertNotIn(owner.body, fight.npc_impulses)
                self.assertEqual(tuple(owner.mover.position), origin)

    def test_actual_lethal_reaper_shell_resolves_accretion_launch_boundary(self):
        fixture = reaper_tests.ReaperTests()
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, caster, _ = fixture.scenario()
                self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
                fight = run.match.combat
                x, y, z = caster.mover.position
                owner.mover.position[:] = (x, y, z + 6)
                owner.mover.body = None
                own = fight.vitals[owner.body]
                own.damage(100)
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is caster))
                enemy.damage(enemy.total - 20)
                heading = aim(fight._eye(caster), fight._eye(owner))
                run.step({caster: (*heading, 16)})
                if released:
                    for _ in range(100):
                        if fight.rockets:
                            break
                        run.step({caster: (*heading, 0)})
                self.assertEqual(len(fight.rockets), int(released))
                if released:
                    self.assertIsNotNone(fight.rockets[0].accretion_source)
                fixture.fire(run, owner, caster)
                self.assertTrue(fight.is_dead(caster))
                self.assertEqual(enemy.killer, owner.body)
                healed = 150 + 20 * reaper.REAPING_RATIO
                self.assertAlmostEqual(own.total, healed, places=4)
                for _ in range(45):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.is_stunned(owner), released)
                self.assertAlmostEqual(own.total, healed - (70 if released else 0), places=4)

    def test_actual_earthshatter_interrupts_blossom_but_wraith_rejects_wave(self):
        for wraith in (False, True):
            with self.subTest(wraith=wraith):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(rein, heroes()[0x02E0000000000007]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x, y, z + 6)
                rein.mover.body = None
                heading = aim(fight._eye(rein), (x, y + 1, z))
                script = rein.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                owner.body_script.set_ult_charge(2100)
                run.step({rein: (*heading, 32), owner: (0, 0, 8 if wraith else 32)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                for _ in range(50):
                    run.step({rein: (*heading, 0)})
                    if wave.active:
                        break
                self.assertTrue(wave.active)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                for _ in range(45):
                    run.step({})
                    if not wave.active:
                        break
                self.assertFalse(wave.active)
                self.assertEqual(fight.is_stunned(owner), not wraith)
                self.assertEqual(fight.vitals[owner.body].total, 250 if wraith else 200)
                if not wraith:
                    self.assertIsNone(reaper.blossom_state(owner.body_script))
                    self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                    enemy = next(t for t in fight._targets() if t.owner is rein)
                    rein_health = fight.vitals_of(enemy).total
                    for _ in range(175):
                        run.step({})
                    self.assertEqual(fight.vitals_of(enemy).total, rein_health)
                    self.assertFalse(fight.is_stunned(owner))

    def test_actual_earthshatter_close_impact_and_wave_respect_contact_boundaries(self):
        for case, distance in (("close", 3), ("wall", 6), ("ally", 6), ("behind", 6), ("range", 22)):
            with self.subTest(case=case):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(rein, heroes()[0x02E0000000000007]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x, y, z + distance)
                rein.mover.body = None
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    rein.team = owner.team
                heading = aim(fight._eye(rein), (x, y + 1, z if case != "behind" else z + 12))
                wall = z + 3
                collision = World([
                    (x - 5, y - 1, wall, x + 5, y - 1, wall, x + 5, y + 5, wall),
                    (x - 5, y - 1, wall, x + 5, y + 5, wall, x - 5, y + 5, wall),
                ], name="Earthshatter sight blocker")
                script = rein.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                if case == "close":
                    fight.vitals[owner.body] = combat.Vitals(owner.body, 500)
                expected = 250
                with patch.object(fight, "_collision", return_value=collision if case == "wall"
                                  else fight._collision()):
                    run.step({rein: (*heading, 32)})
                    for _ in range(50):
                        run.step({rein: (*heading, 0)})
                        if wave.active:
                            break
                    self.assertTrue(wave.active)
                    if case == "close":
                        # A surviving target distinguishes the near hit from a second wave hit.
                        self.assertEqual(fight.vitals[owner.body].total, expected)
                        self.assertTrue(fight.is_stunned(owner))
                    for _ in range(45):
                        run.step({})
                    self.assertFalse(wave.active)
                    self.assertEqual(fight.vitals[owner.body].total, expected)
                    if case != "close":
                        self.assertFalse(fight.is_stunned(owner))

    def test_actual_lethal_reaper_shell_resolves_earthshatter_release_boundary(self):
        for released in (False, True):
            with self.subTest(released=released):
                fixture = reaper_tests.ReaperTests()
                run, owner, rein, _ = fixture.scenario()
                self.assertTrue(run.match.switch_hero(rein, heroes()[0x02E0000000000007]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x, y, z + 6)
                rein.mover.body = None
                heading = aim(fight._eye(rein), (x, y + 1, z))
                victim = next(t for t in fight._targets() if t.owner is rein)
                health = fight.vitals_of(victim)
                health.damage(health.total - 20)
                reaping = health.total * reaper.REAPING_RATIO
                own = fight.vitals[owner.body]
                own.damage(100)
                script = rein.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                run.step({rein: (*heading, 32)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                if released:
                    for _ in range(50):
                        if wave.active:
                            break
                        run.step({rein: (*heading, 0)})
                    self.assertTrue(wave.active)
                self.assertEqual(wave.counter, int(released))
                self.assertEqual(own.total, 150)
                fixture.fire(run, owner, rein)
                self.assertTrue(fight.is_dead(rein))
                self.assertEqual(health.killer, owner.body)
                self.assertAlmostEqual(own.total, 150 + reaping, places=4)
                for _ in range(50):
                    run.step({})
                self.assertEqual(wave.counter, int(released))
                self.assertFalse(wave.active)
                self.assertEqual(fight.is_stunned(owner), released)
                self.assertAlmostEqual(own.total, (100 if released else 150) + reaping, places=4)

    def test_actual_earthshatter_resolves_shadow_immunity_boundary(self):
        for immune in (False, True):
            with self.subTest(immune=immune):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(rein, heroes()[0x02E0000000000007]))
                fight = run.match.combat
                x, y, z = origin = tuple(owner.mover.position)
                rein.mover.position[:] = (x, y, z + 6)
                rein.mover.body = None
                heading = aim(fight._eye(rein), (x, y + 1, z))
                record = self.start_shadow(run, owner)
                if immune:
                    for _ in range(34):
                        run.step({})
                script = rein.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                run.step({rein: (*heading, 32)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                for _ in range(65):
                    run.step({rein: (*heading, 0)})
                    if wave.active and (fight.tick - wave.launch_tick) * combat.TICK * wave.speed >= 6:
                        break
                self.assertTrue(wave.active)
                self.assertEqual(tuple(owner.mover.position), origin)
                self.assertEqual(reaper.intangible(owner), immune)
                self.assertEqual(fight.is_stunned(owner), not immune)
                self.assertEqual(fight.vitals[owner.body].total, 250 if immune else 200)
                for _ in range(75):
                    run.step({})
                self.assertFalse(wave.active)
                expected = record["target"] if immune else origin
                self.assertGreater(math.dist(origin, record["target"]), 1)
                self.assertLess(math.dist(owner.mover.position, expected), 0.002)
                # The released wave still covers the landing site after immunity ends.
                self.assertEqual(fight.vitals[owner.body].total, 200)
                self.assertTrue(fight.is_stunned(owner))
                self.assertGreater(self.shadow(owner).vars[61].value(), 0)

    def test_actual_sleep_dart_resolves_earthshatter_release_boundary(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario(
                    team_mode=True, names=("Reaper", "Reinhardt", "Ana"),
                )
                ana = run.players[2]
                self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
                self.assertTrue(run.match.switch_hero(ana, find_hero("Ana")))
                owner.team = ana.team = 0
                rein.team = 1
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x, y, z + 18)
                ana.mover.position[:] = (x + 2, y, z + 18)
                rein.mover.body = ana.mover.body = None
                for _ in range(20):
                    run.step({})
                heading = aim(fight._eye(rein), (x, y + 1, z))
                dart_aim = aim(fight._eye(ana), (x, y + 1, z + 18))
                script = rein.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                run.step({rein: (*heading, 32), ana: (*dart_aim, 0 if released else 8)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                if released:
                    for _ in range(50):
                        if wave.active:
                            break
                        run.step({rein: (*heading, 0)})
                    self.assertTrue(wave.active)
                    run.step({ana: (*dart_aim, 8)})
                for _ in range(35):
                    if rein.body in fight.ana_sleeps:
                        break
                    run.step({rein: (*heading, 0), ana: (*dart_aim, 0)})
                self.assertIn(rein.body, fight.ana_sleeps)
                self.assertFalse(fight.is_dead(rein))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(wave.counter, int(released))
                for _ in range(60):
                    run.step({})
                self.assertEqual(wave.counter, int(released))
                self.assertFalse(wave.active)
                self.assertEqual(fight.vitals[owner.body].total, 200 if released else 250)
                self.assertEqual(fight.is_stunned(owner), released)

    def test_actual_earthshatter_stops_when_source_body_is_replaced(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x, y, z + 6)
                rein.mover.body = None
                heading = aim(fight._eye(rein), (x, y + 1, z))
                script = rein.body_script
                old_body = rein.body
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                run.step({rein: (*heading, 32)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                if released:
                    for _ in range(50):
                        if wave.active:
                            break
                        run.step({rein: (*heading, 0)})
                    self.assertTrue(wave.active)
                self.assertEqual(wave.counter, int(released))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertTrue(run.match.switch_hero(rein, find_hero("Soldier: 76")))
                self.assertNotEqual(rein.body, old_body)
                self.assertTrue(script.retired)
                for _ in range(85):
                    run.step({})
                self.assertEqual(wave.counter, int(released))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertFalse(fight.is_stunned(owner))

    def test_actual_earthshatter_contact_and_stun_clock_match_across_world_gap(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x, y, z + 6)
                rein.mover.body = None
                heading = aim(fight._eye(rein), (x, y + 1, z))
                script = rein.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                run.step({rein: (*heading, 32)})
                for _ in range(50):
                    if wave.active:
                        break
                    run.step({rein: (*heading, 0)})
                self.assertTrue(wave.active)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                if skipped:
                    run.frame += 45
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(45):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(wave.active)
                self.assertEqual(fight.vitals[owner.body].total, 200)
                self.assertTrue(fight.is_stunned(owner))
                outcomes.append(fight.stuns[owner.body] - run.frame)
        self.assertEqual(*outcomes)

    def test_actual_earthshatter_is_occluded_by_allied_dome_and_projection(self):
        for defense in ("Winston", "Zarya"):
            with self.subTest(defense=defense):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario(
                    team_mode=True, names=("Reaper", "Reinhardt", defense),
                )
                ally = run.players[2]
                self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
                self.assertTrue(run.match.switch_hero(ally, find_hero(defense)))
                owner.team = ally.team = 0
                rein.team = 1
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x, y, z + 8)
                ally.mover.position[:] = (x + (2 if defense == "Winston" else 4), y, z)
                rein.mover.body = ally.mover.body = None
                for _ in range(20):
                    run.step({})
                guard = aim(fight._eye(ally), (x, y + 1, z))
                run.step({ally: (*guard, 16)})
                if defense == "Winston":
                    for _ in range(30):
                        if fight.winston_bubbles.actors:
                            break
                        run.step({})
                    bubble, = fight.winston_bubbles.actors.values()
                    expected = 700
                else:
                    bubble = fight.zarya_bubbles.for_target(owner)
                    expected = 200
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.health, expected)
                heading = aim(fight._eye(rein), (x, y + 1, z))
                script = rein.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ultimate, = script.ultimates
                wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
                run.step({rein: (*heading, 32)})
                for _ in range(85):
                    run.step({rein: (*heading, 0)})
                    self.assertEqual(fight.vitals[owner.body].total, 250)
                    self.assertFalse(fight.is_stunned(owner))
                self.assertEqual(wave.counter, 1)
                self.assertFalse(wave.active)
                self.assertEqual(bubble.health, expected)

    def scene(self, hero=reaper.HERO):
        fixture = flashbang_tests.FlashbangTests()
        run, caster, target = fixture.arena(distance=3)
        origin = tuple(target.mover.position)
        run.match.switch_hero(target, heroes()[hero])
        target.mover.position[:] = origin
        target.mover.body = None
        floor, wall = origin[1], origin[2] + 0.5
        # A real backstop forces an impact near even an intangible target.
        run.match.collision_world = World([
            (-50, floor, -50, -50, floor, 50, 50, floor, 50),
            (-50, floor, -50, 50, floor, 50, 50, floor, -50),
            (-5, floor, wall, 5, floor, wall, 5, floor + 5, wall),
            (-5, floor, wall, 5, floor + 5, wall, -5, floor + 5, wall),
            (-5, floor, wall, 5, floor + 5, wall, 5, floor, wall),
            (-5, floor, wall, -5, floor + 5, wall, 5, floor + 5, wall),
        ], name="Flashbang immunity backstop")
        for player in run.players:
            player.mover.use(world=run.match.collision_world)
        for _ in range(20):
            self.step(run, caster, target)
        target.body_script.ult_seconds = 0
        target.body_script.set_ult_charge(2100)
        fixture.health(run, target)
        return run, caster, target

    def step(self, run, caster, target, **kwargs):
        flashbang_tests.FlashbangTests().step(run, caster, target, **kwargs)

    def health(self, run, target):
        return flashbang_tests.FlashbangTests().health(run, target)

    def throw(self, run, caster, target):
        flashbang_tests.FlashbangTests().cast(run, caster, target)

    def shadow(self, target):
        return next(i for i in target.body_script.component.instances.values()
                    if i.graph.index == reaper.SHADOW_GRAPH)

    def start_shadow(self, run, target, *, confirm=True, angles=(16384, 5000)):
        run.step({target: (*angles, 16)})
        run.step({target: (*angles, 0)})
        self.assertTrue(self.shadow(target).state(0).active)
        record = run.match.combat.reaper_shadows[target.body]
        self.assertIsNotNone(record["target"])
        if confirm:
            run.step({target: (*angles, 1)})
            self.assertTrue(self.shadow(target).state(32).active)
        return record

    def assert_immune_impact(self, run, caster, target):
        fight = run.match.combat
        before = self.health(run, target)
        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            self.step(run, caster, target, cast=16)
            for _ in range(20):
                self.step(run, caster, target)
                self.assertFalse(fight.is_stunned(target))
            impacts = [call.args[0] for call in landed.call_args_list
                       if call.args[0].blast is not None
                       and call.args[0].blast.effect == combat.FLASHBANG_EFFECT]
            self.assertTrue(impacts)
            self.assertGreater(impacts[0].left, 0)  # collision, not lifetime expiry/miss
        self.assertEqual(self.health(run, target), before)
        self.assertNotIn(target.body, fight.stuns)

    def test_actual_flashbang_blocks_reaper_movement_attacks_and_skills_then_recovers(self):
        run, caster, target = self.scene()
        self.throw(run, caster, target)
        fight = run.match.combat
        self.assertEqual(self.health(run, target), 225)
        end = fight.stuns[target.body]
        self.assertEqual(end - fight.tick, 50)
        position = tuple(target.mover.position)
        enemy_health = self.health(run, caster)
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=1 | 8 | 16 | 32 | 0x800, forward=127)
            self.assertEqual(tuple(target.mover.position), position)
            self.assertEqual(self.health(run, caster), enemy_health)
            self.assertFalse(reaper.intangible(target))
            self.assertIsNone(reaper.blossom_state(target.body_script))
            self.assertFalse(target.body_script.component.held)
        for _ in range(15):
            self.step(run, caster, target, target_buttons=1, forward=127)
        self.assertNotEqual(tuple(target.mover.position), position)
        self.assertLess(self.health(run, caster), enemy_health)
        self.assertNotIn(target.body, fight.stuns)

    def test_short_skill_press_during_stun_cannot_cast_later_without_fresh_input(self):
        run, caster, target = self.scene()
        self.throw(run, caster, target)
        end = run.match.combat.stuns[target.body]
        self.step(run, caster, target, target_buttons=8)
        while run.frame < end + 20:
            self.step(run, caster, target)
            self.assertFalse(reaper.intangible(target))
        self.step(run, caster, target, target_buttons=8)
        self.assertTrue(reaper.intangible(target))

    def test_wraith_rejects_real_nearby_flashbang_but_recovery_is_vulnerable(self):
        for wait in (0, 190, 220):
            with self.subTest(wait=wait):
                run, caster, target = self.scene()
                self.step(run, caster, target, target_buttons=8)
                for _ in range(wait):
                    self.step(run, caster, target)
                if wait == 0:
                    self.assertTrue(reaper.intangible(target))
                    self.assert_immune_impact(run, caster, target)
                else:
                    self.assertFalse(reaper.intangible(target))
                    self.throw(run, caster, target)
                    self.assertEqual(self.health(run, target), 225)
                    wraith = next(i for i in target.body_script.component.instances.values()
                                  if i.graph.index == reaper.WRAITH_GRAPH)
                    self.assertGreater(wraith.vars[61].value(), 7.7)

    def test_flashbang_ends_blossom_without_refund_or_late_world_clock_damage(self):
        run, caster, target = self.scene()
        self.step(run, caster, target, target_buttons=32)
        self.assertIsNotNone(reaper.blossom_state(target.body_script))
        fight = run.match.combat
        record = fight.reaper_blossoms[target.body]
        self.throw(run, caster, target)
        self.assertIsNone(reaper.blossom_state(target.body_script))
        self.assertEqual(target.body_script.component.vars[342].value(), 0)
        remaining = self.health(run, caster)
        for _ in range(220):
            self.step(run, caster, target)
            self.assertEqual(self.health(run, caster), remaining)
        self.assertTrue(record["ended"])
        self.assertFalse(record["targets"])
        self.assertEqual(target.body_script.component.vars[342].value(), 0)

    def test_flashbang_aborts_shadow_selector_without_cooldown_and_can_reopen(self):
        run, caster, target = self.scene()
        self.start_shadow(run, target, confirm=False)
        self.throw(run, caster, target)
        shadow = self.shadow(target)
        self.assertFalse(shadow.state(0).active)
        self.assertFalse(shadow.state(6).active)
        self.assertEqual(shadow.vars[61].value(), 0)
        end = run.match.combat.stuns[target.body]
        while run.frame < end + 2:
            self.step(run, caster, target)
        self.start_shadow(run, target, confirm=False)

    def test_flashbang_before_shadow_immunity_aborts_cast_and_preserves_cooldown(self):
        run, caster, target = self.scene()
        record = self.start_shadow(run, target)
        start = tuple(target.mover.position)
        self.throw(run, caster, target)
        shadow = self.shadow(target)
        self.assertFalse(shadow.state(19).active)
        self.assertFalse(shadow.state(32).active)
        self.assertGreater(shadow.vars[61].value(), 9.9)
        for _ in range(160):
            self.step(run, caster, target)
        self.assertEqual(tuple(target.mover.position), start)
        self.assertGreater(math.dist(target.mover.position, record["target"]), 2)
        self.assertGreater(shadow.vars[61].value(), 7)

    def test_shadow_immunity_protects_but_landed_reaper_can_be_stunned_without_undo(self):
        for age in (67, 105):
            with self.subTest(age=age):
                run, caster, target = self.scene()
                record = self.start_shadow(run, target)
                for _ in range(age):
                    self.step(run, caster, target)
                if age == 67:
                    self.assertTrue(reaper.intangible(target))
                    self.assert_immune_impact(run, caster, target)
                    for _ in range(50):
                        self.step(run, caster, target)
                    self.assertLess(math.dist(target.mover.position, record["target"]), 0.002)
                else:
                    self.assertFalse(reaper.intangible(target))
                    self.assertLess(math.dist(target.mover.position, record["target"]), 0.002)
                    self.throw(run, caster, target)
                    self.assertEqual(self.health(run, target), 225)
                    self.assertLess(math.dist(target.mover.position, record["target"]), 0.002)
                    self.assertGreater(self.shadow(target).vars[61].value(), 8)

    def test_zen_and_mei_protected_bodies_reject_hit_control_as_well_as_damage(self):
        for hero, button in ((defenses.ZENYATTA, 32), (defenses.MEI, 8)):
            with self.subTest(hero=hero):
                run, caster, target = self.scene(hero)
                if hero == defenses.ZENYATTA:
                    target.body_script.set_ult_charge(target.body_script.component.vars[338].value())
                self.step(run, caster, target, target_buttons=button)
                self.assertTrue(defenses.invulnerable(target))
                self.assert_immune_impact(run, caster, target)
                self.assertTrue(defenses.invulnerable(target))

    def test_world_time_expires_stun_without_owner_input_and_cannot_resume_blossom(self):
        run, caster, target = self.scene()
        self.step(run, caster, target, target_buttons=32)
        self.throw(run, caster, target)
        fight = run.match.combat
        remaining = self.health(run, caster)
        for _ in range(220):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(self.health(run, caster), remaining)
        self.assertNotIn(target.body, fight.stuns)
        self.assertIsNone(reaper.blossom_state(target.body_script))
        self.assertFalse(fight.reaper_blossoms[target.body]["targets"])
        for _ in range(15):
            self.step(run, caster, target, target_buttons=1)
        self.assertLess(self.health(run, caster), remaining)
        self.assertEqual(self.health(run, caster), 0)
        self.assertEqual(target.body_script.component.vars[342].value(), remaining)

    def test_actual_lethal_primary_and_hero_replacement_retire_body_control(self):
        run, caster, target = self.scene()
        for _ in range(70):
            self.step(run, caster, target, cast=1)
        self.assertEqual(self.health(run, target), 40)  # three authored70-damage body shots
        self.throw(run, caster, target)
        fight = run.match.combat
        self.assertEqual(self.health(run, target), 15)
        end = fight.stuns[target.body]
        while not fight.is_dead(target) and run.frame < end - 1:
            self.step(run, caster, target, cast=1)
        self.assertTrue(fight.is_dead(target))
        self.assertLess(run.frame, end)
        self.assertNotIn(target.body, fight.stuns)
        self.assertTrue(target.body_script.component.owner_dead)

        run, caster, target = self.scene()
        self.start_shadow(run, target)
        self.throw(run, caster, target)
        old = target.body
        script = target.body_script
        run.match.switch_hero(target, target.hero)
        self.assertTrue(script.retired)
        self.assertNotEqual(target.body, old)
        self.assertNotIn(old, run.match.combat.stuns)
        self.assertNotIn(old, run.match.combat.reaper_shadows)
        self.assertFalse(run.match.combat.is_stunned(target))


if __name__ == "__main__":
    unittest.main()
