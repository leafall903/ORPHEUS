"""Actual Sound Barrier input and Ana's contacts with its temporary pool."""

import unittest

import test_game_ana as fixture
import test_game_ana_orisa_barrier as placement
from test_game_combat import aim

from ow174.game import sound_barrier, stats
from ow174.game.content import heroes
from ow174.game.mover import IN_AIR
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import HEALTH_POOL


class AnaSoundBarrierTests(unittest.TestCase):
    def arena(self, *, enemy=False, distance=3):
        run, ana, lucio, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy, distance=distance)
        self.assertTrue(run.match.switch_hero(lucio, heroes()[sound_barrier.HERO]))
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(lucio, (x, y, z + distance))
        for _ in range(30):
            run.step({})
        return run, ana, lucio

    @staticmethod
    def vitals(run, player):
        fight = run.match.combat
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is player))

    @staticmethod
    def direction(run, ana, lucio):
        x, y, z = lucio.mover.position
        return aim(run.match.combat._eye(ana), (x, y + 1, z))

    def cast(self, run, lucio):
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({lucio: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertIsNotNone(sound_barrier.landing_state(lucio))

    def land(self, run, lucio):
        vitals = self.vitals(run, lucio)
        for _ in range(90):
            if vitals.sound_shield is not None:
                break
            run.step({})
        self.assertIsNotNone(vitals.sound_shield)
        self.assertEqual(vitals.sound_shield.amount, 750)
        return vitals

    def test_grounded_cast_grants_same_team_pool_and_world_clock_expires_it(self):
        run, ana, lucio = self.arena()
        self.cast(run, lucio)
        lucio_frame = lucio.body_script.frame
        start = run.frame
        run.match.update((start + 42) * .016, start + 42, frames=False)
        self.assertEqual(lucio.body_script.frame, lucio_frame)
        caster = self.vitals(run, lucio)
        ally = self.vitals(run, ana)
        self.assertEqual(caster.sound_shield.amount, 750)
        self.assertEqual(ally.sound_shield.amount, 750)
        run.match.update((start + 104) * .016, start + 104, frames=False)
        self.assertAlmostEqual(ally.sound_shield.amount, 626)
        run.match.update((start + 417) * .016, start + 417, frames=False)
        self.assertEqual(caster.total, 200)
        self.assertEqual(ally.total, 200)

    def test_long_world_gap_does_not_create_a_fresh_late_shield(self):
        run, ana, lucio = self.arena()
        self.cast(run, lucio)
        start = run.frame
        run.match.update((start + 180) * .016, start + 180, frames=False)
        vitals = self.vitals(run, ana)
        self.assertAlmostEqual(vitals.sound_shield.amount, 474)
        self.assertFalse(run.match.combat.sound_barriers.waves)
        run.match.update((start + 418) * .016, start + 418, frames=False)
        self.assertEqual(vitals.total, 200)

    def test_airborne_cast_waits_for_actual_landing(self):
        run, ana, lucio = self.arena()
        x, y, z = lucio.mover.position
        placement.AnaOrisaBarrierTests.place(lucio, (x, y + 12, z))
        self.cast(run, lucio)
        for _ in range(45):
            run.step({})
        self.assertTrue(lucio.mover.flags_word & IN_AIR)
        self.assertIsNone(self.vitals(run, ana).sound_shield)
        self.land(run, lucio)
        self.assertFalse(lucio.mover.flags_word & IN_AIR)
        self.assertEqual(self.vitals(run, ana).sound_shield.amount, 750)

    def test_range_and_cover_exclude_allies_and_wave_can_reach_late_arrival(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, ana, lucio = self.arena(distance=6 if cover else 32)
                x, y, z = ana.mover.position
                if cover:
                    fixture.set_static_wall(run, ana, 3)
                self.cast(run, lucio)
                self.land(run, lucio)
                ally = self.vitals(run, ana)
                self.assertIsNone(ally.sound_shield)
                lx, ly, lz = lucio.mover.position
                placement.AnaOrisaBarrierTests.place(ana, (lx, ly, lz + 2))
                run.step({})
                self.assertEqual(ally.sound_shield.amount, 750)

    def test_actual_ana_contacts_drain_pool_and_sleep_and_antiheal_still_apply(self):
        for button, held, expected in ((1, 0, 70), (1, 2, 70), (8, 0, 5), (16, 0, 50), (0x800, 0, 30)):
            with self.subTest(button=button, scoped=bool(held)):
                run, ana, lucio = self.arena(enemy=True, distance=1.5 if button == 0x800 else 3)
                direction = self.direction(run, ana, lucio)
                for _ in range(35 if held else 0):
                    run.step({ana: (*direction, held)})
                self.cast(run, lucio)
                vitals = self.land(run, lucio)
                self.assertIsNone(self.vitals(run, ana).sound_shield)
                if held:
                    for _ in range(35):
                        run.step({ana: (*direction, held)})
                before = vitals.sound_shield.amount
                at = run.match.combat.now
                run.step({ana: (*direction, held | button)})
                for _ in range(65):
                    run.step({ana: (*direction, held)})
                fight = run.match.combat
                self.assertAlmostEqual(before - vitals.sound_shield.amount - (fight.now - at) * 125, expected)
                self.assertEqual(vitals.now[HEALTH_POOL], 200)
                self.assertEqual(fight.is_asleep(lucio), button == 8)
                self.assertEqual(vitals.healing_block_until > fight.now, button == 16)
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), expected)

    def test_actual_dart_interrupts_queued_landing_after_charge_is_spent(self):
        run, ana, lucio = self.arena(enemy=True)
        direction = self.direction(run, ana, lucio)
        self.cast(run, lucio)
        run.step({ana: (*direction, 8)})
        for _ in range(95):
            run.step({})
        self.assertTrue(run.match.combat.is_asleep(lucio))
        self.assertIsNone(self.vitals(run, lucio).sound_shield)
        self.assertNotIn(lucio.body, run.match.combat.sound_barriers.landings)

    def test_friendly_rifle_heals_ordinary_health_without_refilling_sound_barrier(self):
        run, ana, lucio = self.arena()
        self.cast(run, lucio)
        vitals = self.land(run, lucio)
        vitals.now[HEALTH_POOL] = 90
        direction = self.direction(run, ana, lucio)
        run.step({ana: (*direction, 1)})
        for _ in range(65):
            run.step({})
        self.assertEqual(vitals.now[HEALTH_POOL], 160)
        self.assertAlmostEqual(vitals.sound_shield.amount, 618)
        self.assertEqual(ana.stats.value(stats.HEALING), 70)

    def test_caster_replacement_retires_wave_and_leaves_granted_ally_shield(self):
        run, ana, lucio = self.arena()
        self.cast(run, lucio)
        self.land(run, lucio)
        old = lucio.body
        self.assertTrue(run.match.switch_hero(lucio, heroes()[fixture.ANA]))
        run.step({})
        manager = run.match.combat.sound_barriers
        self.assertNotIn(old, manager.waves)
        self.assertNotIn(old, manager.shielded)
        self.assertIsNone(self.vitals(run, lucio).sound_shield)
        self.assertAlmostEqual(self.vitals(run, ana).sound_shield.amount, 748)

    def test_pending_landing_and_active_pool_enter_the_server_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        lucio, = players
        peer, = peers
        peer.message(21616, stored.pick(lucio.entity, sound_barrier.HERO), now=101.0)
        session.tick(101.0)
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        peer.commands(script.frame + 1, 4, 32, now=101.0)
        fight = lucio.match.combat
        landing = fight.sound_barriers.landings[lucio.body]
        pending = stored.snapshot_digest(case.capture(session))
        landing.ready += .016
        self.assertNotEqual(pending, stored.snapshot_digest(case.capture(session)))
        session.tick(102.0)
        shield = fight.vitals[lucio.body].sound_shield
        self.assertIsNotNone(shield)
        active = stored.snapshot_digest(case.capture(session))
        shield.amount -= 5
        self.assertNotEqual(active, stored.snapshot_digest(case.capture(session)))
