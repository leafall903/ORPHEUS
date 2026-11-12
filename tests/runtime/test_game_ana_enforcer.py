"""Actual13EB launch/flight/contact through manual input, with authored43 movers."""
import unittest
from unittest.mock import patch

import test_game_ana_npcs as fixture
import test_game_ana_projected_barrier as projected
from test_game_ana_barrier_energy import energy
from test_game_combat import aim

from ow174.game import npc
from ow174.game.collision import World
from ow174.game.content import find_hero


@unittest.skipUnless(fixture.FIXTURE, 'explicit five-NPC runtime required')
class AnaEnforcerRingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = fixture.AnaRealNPCTests()
        cls.helper.setUpClass()
        cls.definition = next(d for d in cls.helper.catalog.values() if d.name == 'Enforcer')

    @classmethod
    def tearDownClass(cls):
        cls.helper.tearDownClass()

    def fight(self, *, distance=1.5):
        return self.helper.fight(self.definition, distance=distance)

    def start_fire(self, run, victim, bot):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        fight.vitals_of(target)  # observe/allocate default pools before any flight contact
        origin = (bot.position[0], bot.position[1] + 1.55, bot.position[2])
        bot.yaw, bot.pitch = aim(origin, (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        bot.firing = True
        component = bot.body_script.component
        component.edges([(component.world.ai_weapon_button(0), True)])

    def fire(self, run, victim, bot):
        self.start_fire(run, victim, bot)
        fight = run.match.combat
        for _ in range(160):
            run.step({})
            emitted = [r for r in fight.rockets if r.shooter is bot]
            if emitted:
                return emitted
        self.fail('actual primary never emitted a projectile')

    @staticmethod
    def settle(run, frames=20):
        for _ in range(frames):
            run.step({})

    def test_one_actual_report_spends_one_round_and_launches25_distinct_flights(self):
        run, ana, bot, _entry = self.fight()
        fight = run.match.combat
        with patch.object(fight, '_shoot', wraps=fight._shoot) as reports:
            shots = self.fire(run, ana, bot)
        self.assertEqual(reports.call_count, 1)
        self.assertEqual(len(shots), 25)
        self.assertEqual(len({r.direction for r in shots}), 25)
        self.assertTrue(all(r.speed == 80 and r.direct.amount == 2 for r in shots))
        self.assertTrue(all(r.direction[1] < -.2 for r in shots))  # manual down-pitch reaches the chest
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(weapon.vars[53].value(), 3)
        self.assertEqual(fight.vitals[ana.body].total, 200)

    def test_point_blank25_separate_contacts_deal50_after_flight(self):
        run, ana, bot, _entry = self.fight()
        fight = run.match.combat
        self.fire(run, ana, bot)
        with patch.object(fight, '_direct_hit', wraps=fight._direct_hit) as contacts:
            self.settle(run)
        self.assertEqual(contacts.call_count, 25)
        self.assertEqual(fight.vitals[ana.body].total, 150)

    def align_target_to_released_ray(self, run, victim, shots):
        # Position-only fixture: ensure a range contact without forcing hits or
        # replacing the generated scatter directions. This is not an accuracy test.
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        candidates = []
        for ray in shots:
            travel = (victim.mover.position[2] - ray.position[2]) / ray.direction[2]
            height = ray.position[1] + ray.direction[1] * travel
            if target.feet[1] + .2 < height < target.feet[1] + target.height - .2:
                candidates.append((ray, travel))
        self.assertTrue(candidates, 'no released trajectory crosses the body-height fixture')
        ray, travel = candidates[0]
        victim.mover.position[0] = ray.position[0] + ray.direction[0] * travel
        victim.mover.body = None

    def test_actual_projectile_falloff_uses_resource8_to15m_and_point2_floor(self):
        for distance in (6, 12, 16):
            with self.subTest(distance=distance):
                run, ana, bot, _entry = self.fight(distance=distance)
                fight = run.match.combat
                shots = self.fire(run, ana, bot)
                self.align_target_to_released_ray(run, ana, shots)
                with patch.object(fight, '_direct_hit', wraps=fight._direct_hit) as contacts:
                    self.settle(run, 30)
                amounts = [call.args[3] for call in contacts.call_args_list]
                self.assertTrue(amounts)
                for amount in amounts:
                    if distance == 6:
                        self.assertEqual(amount, 2)
                    elif distance == 12:
                        self.assertGreater(amount, .4)
                        self.assertLess(amount, 2)
                    else:
                        self.assertAlmostEqual(amount, .4)
                self.assertAlmostEqual(fight.vitals[ana.body].total, 200 - sum(amounts))

    def test_actual100m_projectile_limit_hits99m_but_not101m(self):
        for distance in (99, 101):
            with self.subTest(distance=distance):
                run, ana, bot, _entry = self.fight(distance=distance)
                shots = self.fire(run, ana, bot)
                self.align_target_to_released_ray(run, ana, shots)
                component = bot.body_script.component
                component.edges([(component.world.ai_weapon_button(0), False)])
                self.settle(run, 100)
                self.assertEqual(run.match.combat.vitals[ana.body].total < 200, distance == 99)

    def test_firing_time_offense_and_live_incoming_nano_apply_per_projectile(self):
        for offensive, defensive in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(offensive=offensive, defensive=defensive):
                run, ana, bot, entry = self.fight()
                fight = run.match.combat
                if offensive:
                    fight.vitals_of(entry).nano_until = fight.now + 8
                self.fire(run, ana, bot)
                fight.vitals_of(entry).nano_until = 0  # released damage keeps firing-time scaling
                if defensive:
                    fight.vitals[ana.body].nano_until = fight.now + 8
                self.settle(run)
                expected = 50 * (1.5 if offensive else 1) * (.5 if defensive else 1)
                self.assertEqual(fight.vitals[ana.body].total, 200 - expected)

    def test_partial_wall_blocks_individual_pellets_instead_of_the_entire_volley(self):
        run, ana, bot, _entry = self.fight(distance=2.5)
        x, y, z = ana.mover.position
        wall_z = z + 1.25
        run.match.collision_world = World([
            (x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
            (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30),
            (x, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
            (x, y, wall_z, x + 3, y + 4, wall_z, x, y + 4, wall_z)])
        fight = run.match.combat
        self.assertEqual(len(self.fire(run, ana, bot)), 25)
        with patch.object(fight, '_direct_hit', wraps=fight._direct_hit) as contacts:
            self.settle(run)
        self.assertGreater(contacts.call_count, 0)
        self.assertLess(contacts.call_count, 25)
        self.assertEqual(fight.vitals[ana.body].total, 200 - 2 * contacts.call_count)

    def test_sleep_stops_new_attacks_but_preserves_already_released_pellets(self):
        self.check_dart_after_launch(lethal=False)

    def test_lethal_dart_preserves_already_released_pellets_from_retained_corpse(self):
        self.check_dart_after_launch(lethal=True)

    def check_dart_after_launch(self, *, lethal):
        run, ana, bot, entry = self.fight(distance=6)
        fight = run.match.combat
        npc_pool = fight.vitals_of(entry)
        if lethal:
            npc_pool.damage(npc_pool.total - 1)
        direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        self.start_fire(run, ana, bot)
        # At six metres this input phase places the dart contact between
        # the first volley report and the arrival of its pellets.
        self.settle(run, frames=40)
        run.step({ana: (*direction, 8)})
        for _ in range(80):
            run.step({ana: (*direction, 0)})
            pellets = [shot for shot in fight.rockets if shot.shooter is bot]
            if pellets:
                break
        self.assertEqual(len(pellets), 25)
        self.assertFalse(npc_pool.dead or fight.is_asleep(bot))
        for _ in range(10):
            run.step({})
            if npc_pool.dead or fight.is_asleep(bot):
                break
        self.assertEqual(npc_pool.dead, lethal)
        self.assertEqual(fight.is_asleep(bot), not lethal)
        if lethal:
            self.assertEqual(npc_pool.killer, ana.body)
            self.assertIn(bot, run.match.bots)
        self.assertTrue(any(shot in fight.rockets for shot in pellets))
        before = fight.vitals[ana.body].total
        with patch.object(fight, '_shoot', wraps=fight._shoot) as reports:
            self.settle(run, frames=110)
        self.assertEqual(reports.call_count, 0)
        self.assertLess(fight.vitals[ana.body].total, before)
        self.assertTrue(all(shot.source_entity == bot.entity for shot in pellets))
        self.assertFalse(fight.rockets)

    def test_manual_removal_retires_all25_flights(self):
        run, ana, bot, _entry = self.fight()
        fight = run.match.combat
        self.fire(run, ana, bot)
        self.assertIn(bot.entity, npc.remove_spawned(run.match))
        self.assertFalse(fight.rockets)
        self.settle(run)
        self.assertEqual(fight.vitals[ana.body].total, 200)

    def test_retained_source_corpse_does_not_rewrite_released_projectile_identity(self):
        run, ana, bot, entry = self.fight()
        fight = run.match.combat
        shots = self.fire(run, ana, bot)
        source = bot.entity
        # Authoritative death-boundary fixture; not a firing-input kill claim.
        fight._damage(ana, entry, 10000, False, None, run.frame)
        self.assertTrue(bot.dead)
        self.assertIn(bot, run.match.bots)
        self.assertTrue(all(r.source_entity == source for r in shots))
        self.settle(run)
        self.assertEqual(fight.vitals[ana.body].total, 150)
        self.assertTrue(all(r.source_entity == source for r in shots))

    def test_replacement_body_at_another_pose_does_not_inherit_old_hits(self):
        run, ana, bot, _entry = self.fight()
        fight = run.match.combat
        self.fire(run, ana, bot)
        old = ana.body
        self.assertTrue(run.match.switch_hero(ana, ana.hero))
        ana.mover.position[0] += 10
        ana.mover.body = None
        self.settle(run)
        self.assertNotEqual(ana.body, old)
        self.assertEqual(fight.vitals[ana.body].total, 200)

    def test_actual_projected_barrier_absorbs25_contacts_and_credits_effective_loss(self):
        for capacity, health, remaining, gain in ((200, 200, 150, 10), (48, 198, 0, 9.6), (49, 200, 0, 9.8)):
            with self.subTest(capacity=capacity):
                helper = projected.AnaProjectedBarrierTests()
                run, ana, caster, _target = helper.fight(ally_ana=True)
                x, y, z = ana.mover.position
                bot = npc.spawn(run.match, self.definition, (x, y, z + 1.5), team=1 - ana.team, yaw=-32768)
                self.settle(run, 25)
                barrier = helper.cast(run, caster, ana)
                barrier.health = capacity  # explicit initial-pool fixture; no native capacity claim
                self.assertEqual(energy(caster), 0)
                self.fire(run, ana, bot)
                self.settle(run)
                self.assertEqual(run.match.combat.vitals[ana.body].total, health)
                self.assertEqual(barrier.health, remaining)
                self.assertAlmostEqual(energy(caster), gain, delta=.00001)

    def test_actual_genji_deflect_returns_released_pellets_to_the_source(self):
        run, victim, bot, entry = self.fight()
        self.assertTrue(run.match.switch_hero(victim, find_hero('Genji')))
        fight = run.match.combat
        direction = aim(fight._eye(victim), (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))
        run.step({victim: (*direction, 16)})
        self.fire(run, victim, bot)
        # Run.step's default input would turn the reflector away; keep its live facing.
        for _ in range(30):
            run.step({victim: (*direction, 0)})
        self.assertEqual(fight.vitals[victim.body].total, 200)
        self.assertLess(fight.vitals_of(entry).total, 350)
