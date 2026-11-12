"""Actual Zarya Shift versus Ana; reconstructed body-bound barrier protection."""
import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import stats
from ow174.game.commands import Command
from ow174.game.content import find_hero


class AnaPersonalBarrierTests(unittest.TestCase):
    def fight(self, *, ally=False, distance=6, activate=True):
        run, ana, zarya, old = fixture.AnaRifleTests().setup_fight(enemy=not ally, distance=distance)
        self.assertTrue(run.match.switch_hero(zarya, find_hero('Zarya')))
        zarya.mover.position = list(old.feet)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        entry = next(t for t in fight._targets() if t.owner is zarya)
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        if activate:
            self.cast(run, zarya)
        return run, ana, zarya, entry

    def cast(self, run, zarya):
        run.step({zarya: (0, 0, 8)})
        state = next(i.states[2] for i in zarya.body_script.component.instances.values()
                     if i.graph.index == 0x0244)
        self.assertTrue(state.active)
        barrier = run.match.combat.body_barriers[zarya.body]
        self.assertEqual(barrier.health, 200)
        # The input frame has already advanced the authored2s ChaseVar by up
        # to one16ms command step when combat consumes the Pet activation.
        self.assertAlmostEqual(barrier.until - run.match.combat.now, 2, delta=0.016)
        return barrier

    def attack(self, run, ana, entry, button, *, scoped=False, frames=70):
        fight = run.match.combat
        direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2)})
        run.step({ana: (*direction, button | (2 if scoped else 0))})
        for _ in range(frames):
            run.step({})

    def test_both_actual_rifle_modes_hit_barrier_without_dose_or_health_charge(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, ana, zarya, entry = self.fight()
                self.attack(run, ana, entry, 1, scoped=scoped)
                fight = run.match.combat
                self.assertEqual(fight.vitals_of(entry).total, 400)
                self.assertEqual(fight.body_barriers[zarya.body].health, 130)
                self.assertFalse(any(hit.target == zarya.body for hit in fight.timed_hits))
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(ana.body_script.component.vars[342].value(), 0)

    def test_actual_sleep_is_consumed_without_sleeping_or_damage(self):
        run, ana, zarya, entry = self.fight()
        self.attack(run, ana, entry, 8)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(entry).total, 400)
        self.assertFalse(fight.is_asleep(zarya))
        self.assertEqual(fight.body_barriers[zarya.body].health, 195)

    def test_actual_grenade_contact_is_blocked_without_antiheal(self):
        run, ana, zarya, entry = self.fight()
        self.attack(run, ana, entry, 16)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(entry).total, 400)
        self.assertLessEqual(fight.vitals_of(entry).healing_block_until, fight.now)
        self.assertEqual(fight.body_barriers[zarya.body].health, 150)

    def test_actual_melee_is_consumed_by_barrier(self):
        run, ana, zarya, entry = self.fight(distance=1.5)
        self.attack(run, ana, entry, 0x800)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(entry).total, 400)
        self.assertEqual(fight.body_barriers[zarya.body].health, 170)

    def test_allied_rifle_and_grenade_heal_through_active_personal_barrier(self):
        for button, expected in ((1, 370), (16, 400)):
            with self.subTest(button=button):
                run, ana, zarya, entry = self.fight(ally=True)
                fight = run.match.combat
                fight.vitals_of(entry).damage(100)
                self.attack(run, ana, entry, button)
                self.assertEqual(fight.vitals_of(entry).total, expected)
                self.assertEqual(fight.body_barriers[zarya.body].health, 200)

    def test_breaking_contact_does_not_overflow_and_depleted_cast_cannot_refill(self):
        run, ana, zarya, entry = self.fight()
        fight = run.match.combat
        fight._damage(ana, entry, 190, False, None, run.frame)  # establish near-broken shield
        self.assertEqual(fight.body_barriers[zarya.body].health, 10)
        self.attack(run, ana, entry, 1)
        self.assertEqual(fight.body_barriers[zarya.body].health, 0)
        self.assertEqual(fight.vitals_of(entry).total, 400)
        self.attack(run, ana, entry, 1, frames=20)
        self.assertLess(fight.now, fight.body_barriers[zarya.body].until)
        self.assertLess(fight.vitals_of(entry).total, 400)
        for _ in range(30):
            run.step({})
        self.assertEqual(fight.vitals_of(entry).total, 330)

    def test_expiry_and_cooldown_reject_recast_then_allow_rifle_damage(self):
        run, ana, zarya, entry = self.fight()
        fight = run.match.combat
        old = fight.body_barriers[zarya.body]
        for _ in range(130):
            run.step({})
        run.step({zarya: (0, 0, 8)})
        self.assertIs(fight.body_barriers[zarya.body], old)
        self.attack(run, ana, entry, 1)
        self.assertEqual(fight.vitals_of(entry).total, 330)

    def test_actual_bubble_activation_cleanses_pending_ana_dose_and_antiheal(self):
        run, ana, zarya, entry = self.fight(activate=False)
        fight = run.match.combat
        self.attack(run, ana, entry, 16, frames=45)
        self.assertGreater(fight.vitals_of(entry).healing_block_until, fight.now)
        self.attack(run, ana, entry, 1, frames=10)
        self.assertTrue(any(hit.target == zarya.body for hit in fight.timed_hits))
        self.cast(run, zarya)
        after = fight.vitals_of(entry).total
        self.assertEqual(fight.vitals_of(entry).healing_block_until, 0)
        self.assertFalse(any(hit.target == zarya.body for hit in fight.timed_hits))
        for _ in range(30):
            run.step({})
        self.assertEqual(fight.vitals_of(entry).total, after)

    def test_nano_offense_scales_barrier_damage_but_target_reduction_does_not(self):
        run, ana, zarya, entry = self.fight()
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is ana)
        # Establish both already-active effects; native Nano release has its own
        # executed suite. This check isolates the new shield/health boundary.
        fight.vitals_of(own).nano_until = fight.now + 8
        fight.vitals_of(entry).nano_until = fight.now + 8
        self.attack(run, ana, entry, 1)
        self.assertEqual(fight.body_barriers[zarya.body].health, 95)
        self.assertEqual(fight.vitals_of(entry).total, 400)

    def test_replaced_body_does_not_inherit_old_cast_or_refill_it(self):
        run, ana, zarya, entry = self.fight()
        fight, old = run.match.combat, zarya.body
        self.assertTrue(run.match.switch_hero(zarya, zarya.hero))
        zarya.mover.position = list(entry.feet)
        entry = next(t for t in fight._targets() if t.owner is zarya)
        self.assertNotEqual(zarya.body, old)
        self.assertNotIn(old, fight.body_barriers)
        self.assertNotIn(zarya.body, fight.body_barriers)
        self.attack(run, ana, entry, 1)
        self.assertEqual(fight.vitals_of(entry).total, 330)

    def test_flashbang_is_blocked_and_sleeping_owner_cannot_activate(self):
        run, attacker, zarya, entry = self.fight(distance=3)
        fight = run.match.combat
        position = list(attacker.mover.position)
        self.assertTrue(run.match.switch_hero(attacker, find_hero('0x02E0000000000042')))
        attacker.mover.position = position
        direction = aim(fight._eye(attacker), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        run.step({attacker: (*direction, 16)})
        for _ in range(45):
            # Check the impact window, not the final frame after0.8s expiry.
            self.assertFalse(fight.is_stunned(zarya))
            if fight.body_barriers[zarya.body].health < 200:
                break
            run.step({})
        self.assertEqual(fight.vitals_of(entry).total, 400)
        self.assertEqual(fight.body_barriers[zarya.body].health, 175)

        run, ana, zarya, entry = self.fight(activate=False)
        self.attack(run, ana, entry, 8)
        self.assertTrue(run.match.combat.is_asleep(zarya))
        run.step({zarya: (0, 0, 8)})
        self.assertNotIn(zarya.body, run.match.combat.body_barriers)

    def test_actual_pet_and_absorbed_health_are_causal_server_snapshot_state(self):
        import test_server_snapshot as snapshot_fixture

        helper = snapshot_fixture.ServerSnapshotTests()
        session, _peers, players = helper.scene(two=True)
        attacker, zarya = players
        self.assertTrue(session.apply('switch_hero', {'account': zarya.account_lo,
                                                    'hero': find_hero('Zarya').guid}))
        fight = zarya.match.combat
        zarya.body_script.command(zarya.body_script.frame + 1, 8)
        fight.command(zarya, Command(zarya.body_script.frame))
        barrier = fight.body_barriers[zarya.body]
        before = snapshot_fixture.snapshot_digest(helper.capture(session))
        entry = next(t for t in fight._targets() if t.owner is zarya)
        fight._damage(attacker, entry, 70, False, None, zarya.match.tick)
        self.assertEqual(barrier.health, 130)
        after = snapshot_fixture.snapshot_digest(helper.capture(session))
        self.assertNotEqual(after, before)
        self.assertEqual(snapshot_fixture.snapshot_digest(helper.capture(session)), after)
        slot = zarya.body_script.component.entity_var(87, create=False)
        self.assertEqual(slot.value(), 14)
        stored, slot.base = slot.base, 15
        self.assertNotEqual(snapshot_fixture.snapshot_digest(helper.capture(session)), after)
        slot.base = stored
        self.assertEqual(snapshot_fixture.snapshot_digest(helper.capture(session)), after)


if __name__ == '__main__':
    unittest.main()
