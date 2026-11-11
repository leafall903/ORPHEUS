"""Actual Zarya E target/phase versus Ana; explicit body-protection reconstruction."""
import unittest

from test_game_ana import TEAM_MAP
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero


class AnaProjectedBarrierTests(unittest.TestCase):
    def fight(self, *, ally_ana=False, ffa=False, distance=6):
        run = Run(KANEZAKA_DM if ffa else TEAM_MAP, ('Ana', 'Zarya', 'Target'))
        ana, caster, target = run.players
        for actor, hero in ((ana, 'Ana'), (caster, 'Zarya'), (target, 'Soldier')):
            self.assertTrue(run.match.switch_hero(actor, find_hero(hero)))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        ana.team, caster.team, target.team = (1 if ally_ana else 0), 1, 1
        x, y, z = ana.mover.position
        caster.mover.position = [x + 3, y, z + 1]
        target.mover.position = [x, y, z + distance]
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        for _ in range(25):
            run.step({})
        return run, ana, caster, target

    @staticmethod
    def entry(run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def direction(self, run, actor, target):
        entry = self.entry(run, target)
        return aim(run.match.combat._eye(actor),
                   (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))

    def cast(self, run, caster, target, *, valid=True):
        direction = self.direction(run, caster, target)
        run.step({caster: (*direction, 16)})
        fight = run.match.combat
        if not valid:
            self.assertNotIn(target.body, fight.body_barriers)
            return None
        barrier = fight.body_barriers[target.body]
        self.assertEqual(barrier.source, caster.body)
        self.assertEqual(barrier.target, target.body)
        self.assertEqual(barrier.health, 200)
        pet, effect = next((i.states[10], i.states[18])
                           for i in caster.body_script.component.instances.values() if i.graph.index == 0x25C)
        self.assertTrue(pet.active and effect.active)
        self.assertNotIn(caster.body, fight.body_barriers)
        return barrier

    def attack(self, run, ana, target, button, *, scoped=False, frames=70):
        direction = self.direction(run, ana, target)
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2)})
        run.step({ana: (*direction, button | (2 if scoped else 0))})
        for _ in range(frames):
            run.step({ana: (*direction, 0)})

    def test_actual_E_then_both_rifle_modes_hit_ally_barrier_not_health(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, ana, caster, target = self.fight()
                barrier = self.cast(run, caster, target)
                self.attack(run, ana, target, 1, scoped=scoped)
                fight = run.match.combat
                self.assertEqual(fight.vitals_of(self.entry(run, target)).total, 200)
                self.assertEqual(barrier.health, 130)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(ana.body_script.component.vars[342].value(), 0)
                self.assertFalse(any(hit.target == target.body for hit in fight.timed_hits))

    def test_actual_sleep_grenade_and_melee_do_not_apply_body_status(self):
        for button, shield in ((8, 195), (16, 150), (0x800, 170)):
            with self.subTest(button=button):
                run, ana, caster, target = self.fight(distance=1.5 if button == 0x800 else 6)
                barrier = self.cast(run, caster, target)
                self.attack(run, ana, target, button)
                fight = run.match.combat
                vitals = fight.vitals_of(self.entry(run, target))
                self.assertEqual(vitals.total, 200)
                self.assertEqual(barrier.health, shield)
                self.assertFalse(fight.is_asleep(target))
                self.assertLessEqual(vitals.healing_block_until, fight.now)

    def test_friendly_rifle_and_grenade_heal_without_consuming_projected_pool(self):
        for button, expected in ((1, 170), (16, 200)):
            with self.subTest(button=button):
                run, ana, caster, target = self.fight(ally_ana=True)
                fight = run.match.combat
                fight.vitals_of(self.entry(run, target)).damage(100)
                barrier = self.cast(run, caster, target)
                self.attack(run, ana, target, button)
                self.assertEqual(fight.vitals_of(self.entry(run, target)).total, expected)
                self.assertEqual(barrier.health, 200)

    def test_enemy_dead_ffa_and_authored_target_variable_predicates_reject(self):
        for reason in ('enemy', 'dead', 'ffa', 6600, 11264):
            with self.subTest(reason=reason):
                run, ana, caster, target = self.fight(ffa=reason == 'ffa')
                if reason == 'enemy':
                    target.team = ana.team
                elif reason == 'dead':
                    run.match.combat._damage(ana, self.entry(run, target), 10000, False, None, run.frame)
                elif type(reason) is int:
                    target.body_script.component.set(reason, True)
                self.cast(run, caster, target, valid=False)

    def test30m_range_and_static_LOS_gate_actual_E(self):
        for distance, wall, valid in ((29, False, True), (31, False, False), (6, True, False)):
            with self.subTest(distance=distance, wall=wall):
                run, _ana, caster, target = self.fight()
                x, y, z = caster.mover.position
                target.mover.position = [x, y, z + distance]
                if wall:
                    wall_z = z + 3
                    floor = [(x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
                             (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50)]
                    triangles = [(x - 3, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
                                 (x - 3, y, wall_z, x + 3, y + 4, wall_z, x - 3, y + 4, wall_z)]
                    run.match.collision_world = World([*floor, *triangles])
                self.cast(run, caster, target, valid=valid)

    def test_caster_death_or_replacement_does_not_rebind_released_barrier(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, ana, caster, target = self.fight()
                barrier = self.cast(run, caster, target)
                source = caster.body
                if death:
                    run.match.combat._damage(ana, self.entry(run, caster), 10000, False, None, run.frame)
                else:
                    position = list(caster.mover.position)
                    self.assertTrue(run.match.switch_hero(caster, find_hero('Soldier')))
                    caster.mover.position = position
                self.attack(run, ana, target, 1)
                self.assertEqual(barrier.source, source)
                self.assertEqual(barrier.health, 130)
                self.assertEqual(run.match.combat.vitals_of(self.entry(run, target)).total, 200)

    def test_target_replacement_retires_old_pool_without_reapplying_old_cast(self):
        run, ana, caster, target = self.fight()
        self.cast(run, caster, target)
        old, position = target.body, list(target.mover.position)
        self.assertTrue(run.match.switch_hero(target, target.hero))
        target.mover.position = position
        self.attack(run, ana, target, 1)
        fight = run.match.combat
        self.assertNotIn(old, fight.body_barriers)
        self.assertNotIn(target.body, fight.body_barriers)
        self.assertEqual(fight.vitals_of(self.entry(run, target)).total, 130)

    def test_aim_miss_has_no_self_fallback_and_allows_later_valid_press(self):
        run, _ana, caster, target = self.fight()
        run.step({caster: (-32768, 0, 16)})
        for _ in range(10):
            run.step({caster: (-32768, 0, 0)})
        self.assertEqual(run.match.combat.body_barriers, {})
        self.cast(run, caster, target)

    def test_depleted_cast_cannot_refill_and_target_death_retires_pool(self):
        run, ana, caster, target = self.fight()
        barrier = self.cast(run, caster, target)
        fight, entry = run.match.combat, self.entry(run, target)
        fight._damage(ana, entry, 1000, False, None, run.frame)
        self.assertEqual(barrier.health, 0)
        self.assertEqual(fight.vitals_of(entry).total, 200)
        for _ in range(5):
            run.step({})
        self.assertIs(fight.body_barriers[target.body], barrier)
        fight._damage(ana, entry, 1000, False, None, run.frame)
        self.assertTrue(fight.vitals_of(entry).dead)
        self.assertNotIn(target.body, fight.body_barriers)
        for _ in range(5):
            run.step({})
        self.assertNotIn(target.body, fight.body_barriers)

    def test_pending_damage_antiheal_and_sleep_are_cleansed_on_actual_release(self):
        for sleep in (False, True):
            with self.subTest(sleep=sleep):
                run, ana, caster, target = self.fight()
                fight = run.match.combat
                if sleep:
                    self.attack(run, ana, target, 8, frames=45)
                    self.assertTrue(fight.is_asleep(target))
                    self.assertGreater(fight.vitals_of(self.entry(run, target)).sleep_wake_floor, fight.now)
                else:
                    self.attack(run, ana, target, 16, frames=45)
                    self.attack(run, ana, target, 1, frames=10)
                    self.assertTrue(any(hit.target == target.body for hit in fight.timed_hits))
                    self.assertGreater(fight.vitals_of(self.entry(run, target)).healing_block_until,
                                       fight.now)
                self.cast(run, caster, target)
                after = fight.vitals_of(self.entry(run, target)).total
                self.assertFalse(fight.is_asleep(target))
                self.assertEqual(fight.vitals_of(self.entry(run, target)).sleep_wake_floor, 0)
                self.assertEqual(fight.vitals_of(self.entry(run, target)).healing_block_until, 0)
                self.assertFalse(any(hit.target == target.body and not hit.healing
                                     for hit in fight.timed_hits))
                for _ in range(30):
                    run.step({})
                self.assertEqual(fight.vitals_of(self.entry(run, target)).total, after)

    def test_caster_sleep_after_release_does_not_cancel_target_protection(self):
        run, ana, caster, target = self.fight()
        barrier = self.cast(run, caster, target)
        self.attack(run, ana, caster, 8, frames=70)
        self.assertTrue(run.match.combat.is_asleep(caster))
        self.attack(run, ana, target, 1)
        self.assertEqual(barrier.health, 130)
        self.assertEqual(run.match.combat.vitals_of(self.entry(run, target)).total, 200)

    def test_expiry_native_cooldown_and_later_fresh_activation(self):
        run, _ana, caster, target = self.fight()
        barrier = self.cast(run, caster, target)
        for _ in range(130):
            run.step({})
        self.assertGreaterEqual(run.match.combat.now, barrier.until)
        direction = self.direction(run, caster, target)
        run.step({caster: (*direction, 16)})
        self.assertIs(run.match.combat.body_barriers[target.body], barrier)
        for _ in range(600):
            run.step({})
        fresh = self.cast(run, caster, target)
        self.assertIsNot(fresh, barrier)
        self.assertNotEqual(fresh.cast, barrier.cast)

    def test_actual_projected_target_and_source_are_in_real_server_snapshot(self):
        import test_server_snapshot as snapshots

        helper = snapshots.ServerSnapshotTests()
        session, _peers, players = helper.scene(two=True)
        caster, target = players
        self.assertTrue(session.apply('switch_hero', {'account': caster.account_lo,
                                                    'hero': find_hero('Zarya').guid}))
        target.team = caster.team
        x, y, z = caster.mover.position
        target.mover.position = [x, y, z + 6]
        caster.mover.yaw, caster.mover.pitch = self.direction_from_server(caster, target)
        script = caster.body_script
        script.command(script.frame + 1, 16)
        caster.match.combat.command(caster, Command(script.frame))
        barrier = caster.match.combat.body_barriers[target.body]
        before = snapshots.snapshot_digest(helper.capture(session))
        # Immutable source/body identity is causal even before health is touched.
        barrier.source += 1
        self.assertNotEqual(snapshots.snapshot_digest(helper.capture(session)), before)
        barrier.source -= 1
        self.assertEqual(snapshots.snapshot_digest(helper.capture(session)), before)

    @staticmethod
    def direction_from_server(caster, target):
        fight = caster.match.combat
        entry = next(t for t in fight._targets() if t.owner is target)
        return aim(fight._eye(caster), (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))


if __name__ == '__main__':
    unittest.main()
