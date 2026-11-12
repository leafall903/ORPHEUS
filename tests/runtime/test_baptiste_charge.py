"""Executed damage charge from actual0B8B; host eligibility remains scoped."""

import math
import unittest
from unittest.mock import patch

import test_baptiste_combat as fixture

from ow174.game import combat
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, BodyScript


class BaptisteDamageChargeTests(unittest.TestCase):
    def arena(self):
        parts = fixture.BaptisteCombatTests().setup_fight()
        _, source, ally, _, _, _, _ = parts
        ally.mover.position[0] = 20
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(0)
        return parts

    @staticmethod
    def charge(source):
        return source.body_script.component.vars[ULT_CHARGE].value()

    def fire(self, parts):
        run, source, _, _, fight, _, weapon = parts
        fixture.BaptisteCombatTests().fire(run, source, fight, weapon, 6)

    def test_actual_primary_effective_damage_uses_authored_charge_expression(self):
        parts = self.arena()
        _, source, _, enemy, fight, targets, _ = parts
        pool = fight.vitals_of(targets[enemy])
        before = pool.total
        self.fire(parts)
        self.assertEqual(before - pool.total, 24)
        self.assertEqual(self.charge(source), 24)

    def test_armour_and_overkill_only_award_actual_pool_loss(self):
        for health, armour, expected in ((200, 50, 19), (10, 0, 10)):
            with self.subTest(health=health, armour=armour):
                parts = self.arena()
                _, source, _, enemy, fight, _, _ = parts
                fight.vitals[enemy.body] = combat.Vitals(enemy.body, health, armour=armour)
                self.fire(parts)
                self.assertEqual(self.charge(source), expected)

    def test_actual_three_shot_input_and_duplicate_frame_do_not_duplicate_charge(self):
        parts = self.arena()
        run, source, _, enemy, fight, targets, _ = parts
        pool = fight.vitals_of(targets[enemy])
        before = pool.total
        for _ in range(35):
            run.step({source: (0, 0, 1)})
            if before - pool.total >= 72:
                break
        self.assertEqual(before - pool.total, 72)
        self.assertEqual(self.charge(source), 72)
        source.body_script.command(source.body_script.frame, 1)
        fight.command(source, Command(source.body_script.frame, buttons=1))
        self.assertEqual(self.charge(source), 72)
        self.assertEqual(before - pool.total, 72)

    def test_authored_multipliers_and_cost_cap_are_consumed(self):
        parts = self.arena()
        _, source, _, _, _, _, _ = parts
        component = source.body_script.component
        component.set(9532, 2.0)
        component.set(13022, 0.25)
        self.fire(parts)
        self.assertEqual(self.charge(source), 12)
        source.body_script.set_ult_charge(component.vars[ULT_COST].value() - 3)
        self.fire(parts)
        self.assertEqual(self.charge(source), component.vars[ULT_COST].value())

    def test_miss_friend_and_dead_source_do_not_award_points(self):
        for case in ("miss", "friend", "dead_source"):
            with self.subTest(case=case):
                parts = self.arena()
                _, source, _, enemy, fight, targets, _ = parts
                if case == "miss":
                    enemy.mover.position[0] = 30
                elif case == "friend":
                    enemy.team = source.team
                else:
                    fight._damage(enemy, targets[source], 200, False, None, 0)
                    self.assertTrue(fight.is_dead(source))
                self.fire(parts)
                self.assertEqual(self.charge(source), 0)

    def test_authored_charge_flags_veto_damage_and_healing_then_allow_recovery(self):
        for identifier, blocked, enabled in ((2426, False, True), (842, True, False)):
            for healing in (False, True):
                with self.subTest(identifier=identifier, healing=healing):
                    parts = self.arena()
                    run, source, ally, enemy, fight, targets, weapon = parts
                    component = source.body_script.component
                    component.set(identifier, blocked)
                    if healing:
                        ally.mover.position[:] = (0.0, 1.0, 4.0)
                        fight._damage(enemy, targets[ally], 100, False, None, 0)
                    target = targets[ally if healing else enemy]
                    before = fight.vitals_of(target).total
                    fixture.BaptisteCombatTests().fire(run, source, fight, weapon, 26 if healing else 6)
                    self.assertEqual(abs(fight.vitals_of(target).total - before), 70 if healing else 24)
                    self.assertEqual(self.charge(source), 0)
                    component.set(identifier, enabled)
                    fixture.BaptisteCombatTests().fire(run, source, fight, weapon, 26 if healing else 6)
                    self.assertEqual(self.charge(source), 30 if healing else 24)


class BaptisteHealingChargeTests(unittest.TestCase):
    arena = BaptisteDamageChargeTests.arena
    charge = staticmethod(BaptisteDamageChargeTests.charge)

    def heal(self, parts):
        run, source, ally, _, fight, _, weapon = parts
        ally.mover.position[:] = (0.0, 1.0, 4.0)
        fixture.BaptisteCombatTests().fire(run, source, fight, weapon, 26)

    def test_enemy_damage_then_actual_grenade_only_awards_effective_eligible_healing(self):
        parts = self.arena()
        _, source, ally, enemy, fight, targets, _ = parts
        pool = fight.vitals_of(targets[ally])
        fight._damage(enemy, targets[ally], 100, False, None, 0)
        self.assertEqual(pool.ult_healing_deficit, 100)
        self.heal(parts)
        self.assertEqual(pool.total, 170)
        self.assertEqual(pool.ult_healing_deficit, 30)
        self.assertEqual(self.charge(source), 70)
        self.heal(parts)
        self.assertEqual(pool.total, 200)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertEqual(self.charge(source), 100)
        self.heal(parts)
        self.assertEqual(self.charge(source), 100)

    def test_raw_self_and_friendly_damage_do_not_seed_healing_charge(self):
        for kind in ("raw", "self", "friendly"):
            with self.subTest(kind=kind):
                parts = self.arena()
                _, source, ally, _, fight, targets, _ = parts
                pool = fight.vitals_of(targets[ally])
                if kind == "raw":
                    pool.damage(100)
                else:
                    fight._damage(ally if kind == "self" else source, targets[ally],
                                  100, False, None, 0)
                self.assertEqual(pool.ult_healing_deficit, 0)
                self.heal(parts)
                self.assertEqual(pool.total, 170)
                self.assertEqual(self.charge(source), 0)

    def test_other_healing_consumes_deficit_before_baptiste_can_claim_it(self):
        parts = self.arena()
        _, source, ally, enemy, fight, targets, _ = parts
        ally.mover.position[:] = (0.0, 1.0, 4.0)
        fight._damage(enemy, targets[ally], 40, False, None, 0)
        fight.heal_area(ally, tuple(ally.mover.position), 0.1, 25)
        self.assertEqual(fight.vitals_of(targets[ally]).ult_healing_deficit, 15)
        self.heal(parts)
        self.assertEqual(self.charge(source), 15)

    def test_actual_burst_ally_healing_awards_charge_and_self_healing_is_excluded(self):
        parts = self.arena()
        run, source, ally, enemy, fight, targets, _ = parts
        ally.mover.position[:] = (0.0, 1.0, 4.0)
        fight._damage(enemy, targets[ally], 100, False, None, 0)
        fight._damage(enemy, targets[source], 180, False, None, 0)
        for frame in range(370):
            run.step({source: (0, 0, 8 if frame < 20 else 0)})
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 175)
        self.assertAlmostEqual(fight.vitals_of(targets[source]).total, 170)
        self.assertAlmostEqual(self.charge(source), 75, delta=0.001)

    def test_healing_uses_authored_modifiers_and_cost_cap(self):
        parts = self.arena()
        _, source, ally, enemy, fight, targets, _ = parts
        fight._damage(enemy, targets[ally], 100, False, None, 0)
        component = source.body_script.component
        component.set(9532, 2.0)
        component.set(13022, 0.25)
        self.heal(parts)
        self.assertEqual(self.charge(source), 35)
        source.body_script.set_ult_charge(component.vars[ULT_COST].value() - 3)
        self.heal(parts)
        self.assertEqual(self.charge(source), component.vars[ULT_COST].value())
        self.assertEqual(fight.vitals_of(targets[ally]).ult_healing_deficit, 0)

    def test_old_burst_keeps_healing_after_death_but_cannot_charge_replacement_body(self):
        parts = self.arena()
        run, source, ally, enemy, fight, targets, _ = parts
        ally.mover.position[:] = (0.0, 1.0, 4.0)
        fight._damage(enemy, targets[ally], 100, False, None, 0)
        for _ in range(20):
            run.step({source: (0, 0, 8)})
        self.assertGreater(self.charge(source), 0)
        old_body = source.body
        fight._damage(enemy, targets[source], 1000, False, None, run.frame)
        # Isolate healing from the legacy passive surrogate, including the first
        # update in which respawn creates and advances a replacement BodyScript.
        with patch.object(BodyScript, "_charge_ult", return_value=None):
            for _ in range(350):
                run.step({})
        self.assertNotEqual(source.body, old_body)
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 175)
        self.assertEqual(self.charge(source), 0)

    def test_actual_upward_grenade_cannot_charge_a_respawned_body(self):
        for dies in (False, True):
            with self.subTest(dies=dies):
                parts = self.arena()
                run, source, ally, enemy, fight, targets, _ = parts
                run.match.collision_world = flat_world(0.0)
                for actor, z in ((source, 0), (ally, 1.5), (enemy, 8)):
                    actor.mover.position[:] = (0.0, 0.0, z)
                fight._damage(enemy, targets[ally], 100, False, None, run.frame)
                old_body = source.body
                # Explicit offline transport boundary: the actual graph emits
                # an unleased ballistic flight; retail lease retirement is separate.
                with patch.object(fight.projectiles, "fired", return_value=None), \
                        patch.object(BodyScript, "_charge_ult", return_value=None):
                    for _ in range(30):
                        run.step({source: (0, -16384, 2)})
                        if fight.rockets:
                            break
                    self.assertEqual(len(fight.rockets), 1)
                    self.assertEqual(fight.rockets[0].source_entity, old_body)
                    if dies:
                        fight._damage(enemy, targets[source], 1000, False, None, run.frame)
                    for _ in range(420):
                        run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.vitals_of(targets[ally]).total, 150)
                self.assertEqual(source.body != old_body, dies)
                self.assertEqual(self.charge(source), 0 if dies else 50)
                if dies:
                    source.body_script.ult_seconds = 0
                    origin = fight._eye(source)
                    dx = ally.mover.position[0] - origin[0]
                    dz = ally.mover.position[2] - origin[2]
                    distance = math.hypot(dx, dz)
                    height = ally.mover.position[1] + 0.8 - origin[1]
                    # Aim from the actual respawn location using the independent
                    # low-angle ballistic solution for authored speed60/gravity20.
                    v2, gravity = 60.0 ** 2, 20.0
                    slope = (v2 - math.sqrt(v2 ** 2 - gravity * (gravity * distance ** 2
                                                               + 2 * height * v2))) / (gravity * distance)
                    yaw = round(math.atan2(dx, dz) * 65536 / math.tau)
                    pitch = round(-math.atan(slope) * 65536 / math.tau)
                    with patch.object(fight.projectiles, "fired", return_value=None):
                        for frame in range(130):
                            run.step({source: (yaw, pitch, 2 if frame < 30 else 0)})
                    self.assertEqual(fight.vitals_of(targets[ally]).total, 200)
                    self.assertEqual(self.charge(source), 50)


class BaptistePassiveChargeTests(unittest.TestCase):
    @staticmethod
    def body(**options):
        return BodyScript(fixture.BAPTISTE, 0xA0000101, 1000, **options)

    @staticmethod
    def advance(body, frames):
        for _ in range(frames):
            body.command(body.frame + 1, 0)

    @staticmethod
    def points(body):
        return body.component.vars[ULT_CHARGE].value()

    def test_default_uses_authored_interval_and_points_without_duplicate_frame_gain(self):
        body = self.body()
        self.assertIsNone(body.ult_seconds)
        self.advance(body, 62)
        self.assertEqual(self.points(body), 0)
        self.assertEqual(body.passive_charge_ms, 992)
        self.advance(body, 1)
        self.assertEqual(self.points(body), 5)
        self.assertEqual(body.passive_charge_ms, 8)
        self.advance(body, 62)
        self.assertEqual(self.points(body), 10)
        self.assertEqual(body.passive_charge_ms, 0)
        body.command(body.frame, 0)
        body.command(body.frame - 1, 0)
        self.assertEqual(self.points(body), 10)
        self.assertEqual(body.passive_charge_ms, 0)

    def test_gap_and_continuous_commands_match_within_existing_gap_budget(self):
        ordinary, skipped = self.body(), self.body()
        self.advance(ordinary, 125)
        skipped.command(skipped.frame + 125, 0)
        self.assertEqual(self.points(ordinary), self.points(skipped))
        self.assertEqual(ordinary.passive_charge_ms, skipped.passive_charge_ms)

    def test_authored_modifiers_and_cap_are_used_without_banking_full_charge_time(self):
        body = self.body()
        body.component.set(9532, 2.0)
        body.component.set(12918, 0.25)
        self.advance(body, 625)
        self.assertEqual(self.points(body), 25)
        cost = body.component.vars[ULT_COST].value()
        body.set_ult_charge(cost - 1)
        self.advance(body, 125)
        self.assertEqual(self.points(body), cost)
        self.assertEqual(body.passive_charge_ms, 0)
        body.set_ult_charge(0)
        self.advance(body, 1)
        self.assertEqual(self.points(body), 0)
        self.assertEqual(body.passive_charge_ms, 16)

    def test_disabled_passive_and_numeric_sandbox_override_remain_explicit(self):
        disabled = self.body(ult_seconds=0)
        self.advance(disabled, 125)
        self.assertEqual(self.points(disabled), 0)
        override = self.body(ult_seconds=60)
        self.advance(override, 1)
        cost = override.component.vars[ULT_COST].value()
        self.assertAlmostEqual(self.points(override), cost * 16 / 60000, places=5)
        other = BodyScript(SOLDIER, 0xA0000102, 1000)
        self.assertEqual(other.ult_seconds, 60)

    def test_dead_retired_and_authored_block_flag_reset_host_phase(self):
        for gate in ("dead", "retired", "842"):
            with self.subTest(gate=gate):
                body = self.body()
                self.advance(body, 40)
                self.assertEqual(body.passive_charge_ms, 640)
                if gate == "dead":
                    body.component.owner_dead = True
                elif gate == "retired":
                    body.retired = True
                else:
                    body.component.set(842, True)
                body._charge_ult()
                self.assertEqual(body.passive_charge_ms, 0)
                self.assertEqual(self.points(body), 0)

    def test_actual_authored_wait_does_not_receive_a_second_host_increment(self):
        body = self.body()
        instance = next(i for i in body.component.instances.values() if i.graph.index == 0x0B8B)
        # Explicitly mount the real Wait. Native ownership/2426 qualification is
        # outside this collision-free timer test, and is not forced true.
        state = instance.state(instance.graph.node(3).state)
        state.begin()
        self.advance(body, 63)
        self.assertEqual(self.points(body), 5)
        self.assertTrue(state.active)
        self.assertEqual(body.passive_charge_ms, 0)

    def test_explicit_false_2426_blocks_passive_and_recovery_starts_a_new_interval(self):
        body = self.body()
        self.advance(body, 40)
        self.assertEqual(body.passive_charge_ms, 640)
        body.component.set(2426, False)
        self.advance(body, 125)
        self.assertEqual(self.points(body), 0)
        self.assertEqual(body.passive_charge_ms, 0)
        body.component.set(2426, True)
        self.advance(body, 62)
        self.assertEqual(self.points(body), 0)
        self.assertEqual(body.passive_charge_ms, 992)
        self.advance(body, 1)
        self.assertEqual(self.points(body), 5)


if __name__ == "__main__":
    unittest.main()
