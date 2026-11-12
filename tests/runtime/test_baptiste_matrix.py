"""Actual1902 input/lifecycle and reconstructed authenticated-mesh crossings."""

import math
import unittest
from unittest.mock import patch

import test_baptiste_combat as fixture
from test_game_combat import aim

from ow174.game import stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


def deploy_matrix(run, player):
    """Use actual Q, delayed rotate and confirm; keep the window facing the shot."""
    component = player.body_script.component
    component.set(ULT_CHARGE, component.vars[ULT_COST].value())
    run.step({player: (0, 8192, 0x20)})
    for _ in range(7):
        run.step({player: (0, 8192, 0)})
    run.step({player: (0, 8192, 0x20)})
    run.step({player: (0, 8192, 0)})
    run.step({player: (0, 8192, 1)})
    for _ in range(30):
        run.step({})
    actor, = run.match.combat.baptiste.matrices.values()
    return actor


class BaptisteMatrixTests(unittest.TestCase):
    def test_window_survives_owner_death_and_respawn_until_its_own_deadline(self):
        run, player, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
        actor = deploy_matrix(run, player)
        old_body = player.body
        fight._damage(enemy, targets[player], 10000, False, None, run.frame)
        self.assertTrue(fight.is_dead(player))
        for _ in range(200):
            run.step({})
        self.assertNotEqual(player.body, old_body)
        self.assertFalse(fight.is_dead(player))
        self.assertIn(actor.entity, fight.deployables)
        player.mover.position[0] = 20
        run.match.switch_hero(ally, heroes()[fixture.BAPTISTE])
        ally.mover.position[:] = (0.0, 1.0, 0.0)
        enemy.mover.position[:] = (0.0, 1.0, 8.0)
        target = next(t for t in fight._targets() if t.owner is enemy)
        before = fight.vitals_of(target).total
        for _ in range(30):
            run.step({ally: (0, 0, 1)})
            if fight.vitals_of(target).total < before:
                break
        self.assertEqual(before - fight.vitals_of(target).total, 48)
        fight.baptiste.update(actor.expires_tick)
        self.assertNotIn(actor.entity, fight.deployables)

    def test_skipped_flight_ticks_use_window_lifetime_at_crossing(self):
        for timing, expected in (("expires_after_crossing", 140),
                                 ("expires_before_crossing", 70), ("born_after_crossing", 70)):
            results = []
            for gap in (1, 40):
                with self.subTest(timing=timing, gap=gap):
                    args = fixture.BaptisteCombatTests().setup_fight()
                    run, player, ally, _, fight, targets, weapon = args
                    actor = deploy_matrix(run, player)
                    ally.mover.position[2] = 20
                    fight.vitals_of(targets[ally]).damage(180)
                    if timing == "expires_after_crossing":
                        actor.expires_tick = fight.tick + 5
                    elif timing == "expires_before_crossing":
                        actor.expires_tick = fight.tick + 1
                    else:
                        actor.created_tick = fight.tick + 10
                    with patch.object(fight.projectiles, "fired", return_value=None):
                        fight._shoot(player, Command(run.frame), weapon.state(26), 0,
                                     run.frame * 16, activation=(1, 1))
                    rocket, = fight.rockets
                    # Prime the projectile's time boundary, then compare ordinary
                    # and skipped scheduler updates over exactly the same flight.
                    fight.tick += 1
                    fight._fly(rocket)
                    for _ in range(40):
                        if rocket not in fight.rockets:
                            break
                        fight.tick += gap
                        fight._fly(rocket)
                    result = fight.vitals_of(targets[ally]).total - 20
                    results.append(result)
                    self.assertAlmostEqual(result, expected)
            self.assertEqual(results[0], results[1])

    def test_allied_baptiste_can_amplify_in_both_crossing_directions(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run, owner, ally, enemy, fight, _, _ = fixture.BaptisteCombatTests().setup_fight()
                deploy_matrix(run, owner)
                owner.mover.position[0] = 20
                run.match.switch_hero(ally, heroes()[fixture.BAPTISTE])
                ally.mover.position[:] = (0.0, 1.0, 8.0 if reverse else 0.0)
                enemy.mover.position[:] = (0.0, 1.0, 0.0 if reverse else 8.0)
                target = next(t for t in fight._targets() if t.owner is enemy)
                before = fight.vitals_of(target).total
                for _ in range(30):
                    run.step({ally: (-32768 if reverse else 0, 0, 1)})
                    if fight.vitals_of(target).total < before:
                        break
                self.assertEqual(before - fight.vitals_of(target).total, 48)

    def test_expired_window_does_not_amplify_a_new_shot(self):
        run, player, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
        ally.mover.position[0] = 20
        actor = deploy_matrix(run, player)
        actor.expires_tick = fight.tick
        run.step({})
        self.assertNotIn(actor.entity, fight.deployables)
        before = fight.vitals_of(targets[enemy]).total
        for _ in range(30):
            run.step({player: (0, 0, 1)})
            if fight.vitals_of(targets[enemy]).total < before:
                break
        self.assertEqual(before - fight.vitals_of(targets[enemy]).total, 24)

    def test_actual_primary_crossing_doubles_damage_and_melee_does_not_amplify(self):
        for melee in (False, True):
            with self.subTest(melee=melee):
                run, player, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
                ally.mover.position[0] = 20
                enemy.mover.position[2] = 2 if melee else 8
                deploy_matrix(run, player)
                before = fight.vitals_of(targets[enemy]).total
                for _ in range(30):
                    run.step({player: (0, 0, 0x800 if melee else 1)})
                    if fight.vitals_of(targets[enemy]).total < before:
                        break
                self.assertEqual(before - fight.vitals_of(targets[enemy]).total, 30 if melee else 48)

    def test_actual_healing_projectile_doubles_direct_and_area_healing(self):
        run, player, ally, _, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
        deploy_matrix(run, player)
        fight.vitals_of(targets[ally]).damage(180)
        with patch.object(fight.projectiles, "fired", return_value=None):
            for _ in range(30):
                run.step({player: (0, 0, 2)})
                if fight.vitals_of(targets[ally]).total > 20:
                    break
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 160)
        self.assertEqual(player.stats.values[stats.HEALING], 140)

    def test_enemy_shots_and_outside_mesh_do_not_amplify(self):
        for outside in (False, True):
            with self.subTest(outside=outside):
                run, player, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
                ally.mover.position[0] = 20
                actor = deploy_matrix(run, player)
                if outside:
                    actor.position = (6.0, actor.position[1], actor.position[2])
                    source, target, yaw = player, targets[enemy], 0
                else:
                    source, target, yaw = enemy, targets[player], -32768
                yaw, pitch = aim(fight._eye(source), (target.feet[0], target.feet[1] + 0.8, target.feet[2]))
                before = fight.vitals_of(target).total
                for _ in range(30):
                    run.step({source: (yaw, pitch, 1)})
                    if fight.vitals_of(target).total < before:
                        break
                self.assertEqual(before - fight.vitals_of(target).total, 24 if outside else 20)

    def test_projectile_retains_one_boost_after_window_is_removed(self):
        run, player, ally, _, fight, targets, weapon = fixture.BaptisteCombatTests().setup_fight()
        actor = deploy_matrix(run, player)
        ally.mover.position[2] = 20
        fight.vitals_of(targets[ally]).damage(180)
        with patch.object(fight.projectiles, "fired", return_value=None):
            fight._shoot(player, Command(run.frame, yaw=0, pitch=0), weapon.state(26), 0,
                         run.frame * 16, activation=(1, 1))
        rocket, = fight.rockets
        for _ in range(5):
            fight.tick += 1
            fight._fly(rocket)
        self.assertTrue(rocket.amplified)
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 100))
        fight.destroy_deployable(actor.entity)
        for _ in range(40):
            if rocket not in fight.rockets:
                break
            fight.tick += 1
            fight._fly(rocket)
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 160)

    def setup_matrix(self):
        args = fixture.BaptisteCombatTests().setup_fight()
        run, player, _, _, fight, _, _ = args
        component = player.body_script.component
        component.set(ULT_CHARGE, component.vars[ULT_COST].value())
        return run, player, fight, component

    def command(self, player, buttons=0, pitch=8192, yaw=0, frames=1):
        for _ in range(frames):
            player.take_commands([Command(player.body_script.frame + 1, buttons=buttons,
                                          pitch=pitch, yaw=yaw)])
        self.assertIsNotNone(player.body_script)

    def place(self, player):
        self.command(player, 0x20)
        self.command(player)
        self.command(player, 1)

    def test_confirm_consumes_authored_charge_and_spawns_actual_prefab(self):
        _, player, fight, component = self.setup_matrix()
        self.command(player, 0x20)
        self.assertEqual(component.vars[ULT_CHARGE].value(), 2310)
        self.assertFalse(fight.baptiste.matrices)
        self.command(player)
        self.command(player, 1)
        self.assertEqual(component.vars[ULT_CHARGE].value(), 0)
        actor, = fight.baptiste.matrices.values()
        self.assertEqual(actor.definition, 0x0400000000002B50)
        self.assertAlmostEqual(actor.position[1], 1)
        self.assertEqual(actor.expires_tick - actor.created_tick, 625)
        self.assertNotIn(actor.entity, fight.vitals)
        self.assertNotIn(actor.entity, [target.entity for target in fight._targets()])
        # Native2B50 has no health component; a late viewer can still encode it.
        fight.deployable_create(actor).build(0)

    def test_low_charge_cannot_preview_confirm_or_spawn(self):
        _, player, fight, component = self.setup_matrix()
        component.set(ULT_CHARGE, 2309)
        self.place(player)
        self.assertFalse(fight.baptiste.matrices)
        self.assertGreaterEqual(component.vars[ULT_CHARGE].value(), 2309)

    def test_no_floor_wall_and_beyond_range_reject_without_spending(self):
        for geometry, pitch in ((World([]), 8192),
                                (World([(-100, -100, 1, 100, -100, 1, 0, 100, 1)]), 0),
                                (World([(-100, 1, -100, 100, 1, -100, 0, 1, 100)]), 100)):
            with self.subTest(pitch=pitch):
                run, player, fight, component = self.setup_matrix()
                run.match.collision_world = geometry
                self.command(player, 0x20, pitch=pitch)
                self.command(player, pitch=pitch)
                self.command(player, 1, pitch=pitch)
                self.assertFalse(fight.baptiste.matrices)
                self.assertEqual(component.vars[ULT_CHARGE].value(), 2310)

    def test_confirmation_uses_current_aim_and_rotation_toggle(self):
        orientations = []
        for rotate in (False, True):
            _, player, fight, component = self.setup_matrix()
            self.command(player, 0x20, pitch=0)
            # Authored node22 waits0.1s before enabling Q rotation.
            self.command(player, pitch=0, frames=7)
            if rotate:
                self.command(player, 0x20, pitch=0)
                self.command(player, pitch=0)
            self.command(player, 1, pitch=8192)
            actor, = fight.baptiste.matrices.values()
            orientations.append(actor.rotation)
            self.assertEqual(component.vars[ULT_CHARGE].value(), 0)
        dot = sum(a * b for a, b in zip(*orientations, strict=True))
        self.assertAlmostEqual(abs(dot), math.sqrt(0.5))

    def test_cancel_preserves_charge_and_allows_reopen(self):
        _, player, fight, component = self.setup_matrix()
        self.command(player, 0x20)
        self.command(player)
        self.command(player, 2, frames=30)
        self.command(player, frames=30)
        self.assertFalse(fight.baptiste.matrices)
        self.assertEqual(component.vars[ULT_CHARGE].value(), 2310)
        self.place(player)
        self.assertEqual(len(fight.baptiste.matrices), 1)

    def test_repeat_presses_do_not_duplicate_and_expiry_removes(self):
        _, player, fight, component = self.setup_matrix()
        self.place(player)
        actor, = fight.baptiste.matrices.values()
        for _ in range(3):
            self.command(player)
            self.place(player)
        self.assertEqual(list(fight.baptiste.matrices), [actor.entity])
        self.assertEqual(component.vars[ULT_CHARGE].value(), 0)
        fight.baptiste.update(actor.expires_tick - 1)
        self.assertIn(actor.entity, fight.deployables)
        fight.baptiste.update(actor.expires_tick)
        self.assertNotIn(actor.entity, fight.deployables)
        self.assertFalse(fight.baptiste.matrices)

    def test_hero_switch_removes_matrix(self):
        run, player, fight, _ = self.setup_matrix()
        self.place(player)
        actor, = fight.baptiste.matrices.values()
        run.match.switch_hero(player, heroes()[SOLDIER])
        fight.baptiste.update(fight.tick)
        self.assertNotIn(actor.entity, fight.deployables)


if __name__ == "__main__":
    unittest.main()
