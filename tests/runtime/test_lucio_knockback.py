"""Unresisted Soundwave host reconstruction; native resistance/immunity stay open.

Real input and resource vector feed the capsule mover. Manual NPC route/force
arbitration here is an explicit server policy, not native Talon AI acceptance.
"""
import math
import unittest
from unittest.mock import patch

import test_lucio_combat as combat_fixtures
import test_baptiste_pve as pve
import test_game_ana_orisa_barrier as placement

from ow174.game import combat, knockback, npc, velocity
from ow174.game.collision import World
from ow174.game.commands import SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.mover import FELL, mover_data
from ow174.game.script import expr


class LucioKnockbackTests(unittest.TestCase):
    def scenario(self):
        return combat_fixtures.LucioCombatTests().scenario()

    def test_real_soundwave_moves_enemy_up_and_forward_once_and_excludes_ally(self):
        run, source, enemy, ally = self.scenario()
        before = tuple(enemy.mover.position)
        run.step({source: (0, 0, SECONDARY)})
        self.assertGreater(enemy.mover.position[1], before[1])
        self.assertGreater(enemy.mover.position[2], before[2])
        self.assertTrue(enemy.mover.flags_word & FELL)
        self.assertAlmostEqual(math.hypot(*enemy.mover.velocity), 17., delta=.002)
        self.assertAlmostEqual(enemy.mover.velocity[1] / enemy.mover.velocity[2], .5, delta=.001)
        self.assertEqual(tuple(ally.mover.velocity), (0., 0., 0.))
        self.assertEqual(run.match.combat.knockbacks.serial, 1)
        for _ in range(4):
            source.take_commands([Command(run.frame, buttons=SECONDARY)])
        self.assertEqual(run.match.combat.knockbacks.serial, 1)

    def test_clock_uses_the_victim_next_pose_even_when_victim_was_processed_first(self):
        run, source, enemy, _ = self.scenario()
        run.players[:] = [enemy, source, run.players[2]]
        run.step({source: (0, 0, SECONDARY)})
        self.assertEqual(tuple(enemy.mover.velocity), (0., 0., 0.))
        rows = enemy.mover.velocity_changes
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].frame, enemy.body_script.component.frame + 1)
        self.assertEqual(rows[0].key & knockback.HOST_PREFIX, knockback.HOST_PREFIX)
        run.step({})
        self.assertGreater(enemy.mover.velocity[2], 14.)
        self.assertEqual(enemy.mover.velocity_changes, [])

    def test_recovered_target_cannot_consume_knockback_before_impact(self):
        for future_source in (False, True):
            with self.subTest(future_source=future_source):
                run, source, enemy, _ = self.scenario()
                run.step({})
                impact = run.frame + 8
                fight = run.match.combat
                if not future_source:
                    run.match.update(impact * combat.TICK, impact)
                source.take_commands([Command(impact, buttons=SECONDARY)])
                if future_source:
                    self.assertFalse(enemy.mover.velocity_changes)
                    run.match.update((impact - 1) * combat.TICK, impact - 1)
                    self.assertFalse(enemy.mover.velocity_changes)
                    run.match.update(impact * combat.TICK, impact)
                request, = enemy.mover.velocity_changes
                self.assertEqual(request.frame, impact)
                endpoint = impact + 3
                run.match.update(endpoint * combat.TICK, endpoint)
                with patch.object(velocity, "consume", wraps=velocity.consume) as consume:
                    enemy.take_commands([Command(endpoint)])
                self.assertEqual([call.args[3] for call in consume.call_args_list], [impact])
                self.assertFalse(enemy.mover.velocity_changes)
                self.assertEqual(fight.knockbacks.serial, 1)

    def test_miss_and_real_wall_do_not_queue_any_impulse(self):
        for position, wall in (((0, 1, 12), False), ((0, 1, 4), True)):
            with self.subTest(position=position, wall=wall):
                run, source, enemy, _ = self.scenario()
                enemy.mover.position[:] = position
                if wall:
                    run.match.collision_world = World([
                        (-3, -2, 2, 3, -2, 2, 3, 6, 2),
                        (-3, -2, 2, 3, 6, 2, -3, 6, 2),
                    ])
                run.step({source: (0, 0, SECONDARY)})
                self.assertEqual(run.match.combat.knockbacks.serial, 0)
                self.assertFalse(enemy.mover.velocity_changes)

    def test_body_retirement_clears_pending_impulse_and_never_kicks_replacement(self):
        run, source, enemy, _ = self.scenario()
        run.players[:] = [enemy, source, run.players[2]]
        run.step({source: (0, 0, SECONDARY)})
        old_body, old_mover = enemy.body, enemy.mover
        self.assertTrue(old_mover.velocity_changes)
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        self.assertNotIn(old_body, run.match.combat.knockbacks.active)
        self.assertEqual(old_mover.velocity_changes, [])
        self.assertIsNot(old_mover, enemy.mover)
        run.step({})
        self.assertEqual(enemy.mover.velocity[0::2], (0., 0.))

    def test_unresisted_host_preserves_incoming_force_while_stun_blocks_input(self):
        run, source, enemy, _ = self.scenario()
        run.match.combat.stuns[enemy.body] = run.frame + 40
        run.step({source: (0, 0, SECONDARY), enemy: (0, 0, 0x40)})
        first = enemy.mover.position[2]
        run.step({enemy: (0, 0, 0x40)})
        self.assertGreater(enemy.mover.position[2], first)
        self.assertGreater(enemy.mover.velocity[2], 10.)
        self.assertEqual(enemy.mover.throttles, (0, 0))

    def test_dead_victim_receives_no_new_request_and_existing_requests_retire(self):
        run, source, enemy, _ = self.scenario()
        run.players[:] = [enemy, source, run.players[2]]
        run.step({source: (0, 0, SECONDARY)})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.assertTrue(enemy.mover.velocity_changes)
        fight._damage(source, target, 1000., False, None, run.frame)
        self.assertEqual(enemy.mover.velocity_changes, [])
        self.assertNotIn(enemy.body, fight.knockbacks.active)
        self.assertFalse(fight.knockbacks.apply(target, expr.Vec3(0., 6., 12.)))

    def test_body_sizes_do_not_change_unresisted_authored_vector(self):
        for hero in (0x02E0000000000079, 0x02E0000000000042, 0x02E0000000000007):
            with self.subTest(hero=hero):
                run, source, enemy, _ = self.scenario()
                run.match.switch_hero(enemy, heroes()[hero])
                enemy.mover.position[:] = (0, 1, 4)
                run.step({source: (0, 0, SECONDARY)})
                self.assertGreater(enemy.mover.velocity[1], 7.)
                self.assertGreater(enemy.mover.velocity[2], 15.)

    def test_zero_authored_ruleset_multiplier_disables_force_without_disabling_damage(self):
        run, source, enemy, _ = self.scenario()
        world = source.body_script.world
        default = world.ruleset
        with patch.object(world, "ruleset", side_effect=lambda key: 0. if key == 10615 else default(key)):
            run.step({source: (0, 0, SECONDARY)})
        helper = combat_fixtures.LucioCombatTests()
        self.assertEqual(helper.health(run, enemy), 175.)
        self.assertEqual(run.match.combat.knockbacks.serial, 0)
        self.assertEqual(enemy.mover.velocity, (0., 0., 0.))

    def test_actual_sound_barrier_pools_absorb_damage_without_absorbing_unresisted_force(self):
        run, source, enemy, _ = self.scenario()
        run.match.switch_hero(enemy, heroes()[0x02E0000000000079])
        enemy.mover.position[:] = (0, 1, 4)
        enemy.body_script.ult_seconds = 0
        enemy.body_script.set_ult_charge(2940.)
        run.step({enemy: (0, 0, 32)})
        for _ in range(42):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        before = vitals.total
        self.assertGreater(before, 900.)
        run.step({source: (0, 0, SECONDARY)})
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200.)
        self.assertAlmostEqual(before - vitals.total, 25. + 750. / 6. * combat.TICK, delta=.001)
        self.assertGreater(enemy.mover.velocity[2], 15.)


@unittest.skipUnless(pve.DATA, "requires actual five-type NPC resources/movers")
class LucioNpcKnockbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pve.BaptistePveTests.setUpClass()
        cls.definitions = pve.BaptistePveTests.catalog

    @classmethod
    def tearDownClass(cls):
        pve.BaptistePveTests.tearDownClass()

    def scenario(self, definition, team=1):
        run, source, bot, _, _ = pve.BaptistePveTests().make(definition, team=team, distance=4)
        self.assertTrue(run.match.switch_hero(source, heroes()[0x02E0000000000079]))
        placement.AnaOrisaBarrierTests.place(source, (0, 1, 0))
        return None, run, source, bot

    def test_all_five_unresisted_manual_bodies_use_real_capsule_mover_then_settle(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                _, run, source, bot = self.scenario(definition)
                start = tuple(bot.position)
                run.step({source: (0, 0, SECONDARY)})
                mover = bot.impulse_mover
                self.assertIsNotNone(mover)
                self.assertEqual(mover.data, mover_data(definition.body))
                self.assertEqual(bot.position, start)  # NPC uses next world tick
                self.assertEqual(len(mover.velocity_changes), 1)
                run.step({})
                self.assertGreater(bot.position[1], start[1])
                self.assertGreater(bot.position[2], start[2])
                self.assertTrue(bot.movement(run.frame).flags & FELL)
                self.assertEqual(len(mover.velocity_changes), 0)
                self.assertIs(run.match.combat.knockbacks.active[bot.entity], mover)
                for _ in range(240):
                    run.step({})
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(bot.velocity, (0., 0., 0.))
                self.assertNotIn(bot.entity, run.match.combat.knockbacks.active)

    def test_allied_and_explicitly_removed_bodies_have_no_force_leaks(self):
        definition = next(iter(self.definitions.values()))
        _, run, source, bot = self.scenario(definition, team=0)
        run.step({source: (0, 0, SECONDARY)})
        self.assertIsNone(bot.impulse_mover)
        _, run, source, bot = self.scenario(definition)
        run.step({source: (0, 0, SECONDARY)})
        mover = bot.impulse_mover
        self.assertTrue(mover.velocity_changes)
        npc.remove_spawned(run.match)
        self.assertIsNone(bot.impulse_mover)
        self.assertNotIn(bot.entity, run.match.combat.knockbacks.active)
        self.assertEqual(mover.velocity_changes, [])

    def test_future_wave_impulse_retires_on_npc_death_or_removal(self):
        definition = next(iter(self.definitions.values()))
        for death in (False, True):
            with self.subTest(death=death):
                _, run, source, bot = self.scenario(definition)
                run.step({})
                impact = run.frame + 8
                fight = run.match.combat
                source.take_commands([Command(impact, buttons=SECONDARY)])
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(fight.soundwaves[0].frame, impact)
                if death:
                    target = next(t for t in fight._targets() if t.owner is bot)
                    fight._damage(source, target, 1000, False, None, run.frame)
                    self.assertTrue(bot.dead)
                else:
                    self.assertIn(bot.entity, npc.remove_spawned(run.match))
                run.match.update((impact + 2) * combat.TICK, impact + 2)
                self.assertFalse(fight.soundwaves)
                self.assertFalse(hasattr(fight.knockbacks, "pending_npc"))
                self.assertIsNone(bot.impulse_mover)

    def test_lethal_wave_on_every_manual_body_keeps_corpse_without_pending_force(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                _, run, source, bot = self.scenario(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                vitals = fight.vitals_of(target)
                vitals.damage(vitals.total - 20.)
                run.step({source: (0, 0, SECONDARY)})
                self.assertTrue(bot.dead)
                self.assertIn(bot, run.match.bots)
                self.assertIsNone(bot.impulse_mover)
                self.assertNotIn(bot.entity, fight.knockbacks.active)
                self.assertFalse(fight.knockbacks.apply(target, expr.Vec3(0., 6., 12.)))

    def test_world_gap_recovers_the_one_frame_request_without_repeating_it(self):
        definition = next(iter(self.definitions.values()))
        _, run, source, bot = self.scenario(definition)
        run.step({source: (0, 0, SECONDARY)})
        mover = bot.impulse_mover
        bot.step(run.frame + 5, combat.TICK, run.match.collision_world)
        self.assertGreater(bot.position[2], 4.7)
        self.assertEqual(mover.velocity_changes, [])
        self.assertLess(bot.velocity[1], 7.6)
        self.assertEqual(run.match.combat.knockbacks.serial, 1)

    def test_real_capsule_wall_stops_forced_motion_before_the_body_crosses_it(self):
        definition = next(iter(self.definitions.values()))
        _, run, source, bot = self.scenario(definition)
        run.match.collision_world = World([
            (-5, -5, 5.5, 5, 20, 5.5, 5, -5, 5.5),
            (-5, -5, 5.5, -5, 20, 5.5, 5, 20, 5.5),
        ])
        run.step({source: (0, 0, SECONDARY)})
        self.assertIsNotNone(bot.impulse_mover)
        for _ in range(20):
            run.step({})
        self.assertLessEqual(bot.position[2], 5.5 - mover_data(bot.body).stand_radius + .02)
        self.assertAlmostEqual(bot.velocity[2], 0., delta=.001)

    def test_reconstructed_route_resumes_after_forced_arc_and_ground_friction(self):
        definition = next(iter(self.definitions.values()))
        _, run, source, bot = self.scenario(definition)
        bot.route = ((0, 1, 4), (0, 1, 100))
        bot.index, bot.target, bot.start, bot.keys = 1, bot.route[1], bot.route[0], True
        route = bot.route
        run.step({source: (0, 0, SECONDARY)})
        self.assertIsNotNone(bot.impulse_mover)
        for _ in range(200):
            run.step({})
        self.assertIsNone(bot.impulse_mover)
        self.assertEqual(bot.route, route)
        self.assertEqual(bot.target, route[1])
        self.assertGreater(bot.velocity[2], 0.)

    def test_stun_pauses_route_input_but_does_not_freeze_the_forced_arc(self):
        definition = next(iter(self.definitions.values()))
        _, run, source, bot = self.scenario(definition)
        run.match.combat.stuns[bot.entity] = run.frame + 80
        run.step({source: (0, 0, SECONDARY)})
        run.step({})
        self.assertGreater(bot.position[2], 4.)
        self.assertGreater(bot.position[1], 1.)
        self.assertEqual(bot.held, (0, 0))


if __name__ == '__main__':
    unittest.main()
