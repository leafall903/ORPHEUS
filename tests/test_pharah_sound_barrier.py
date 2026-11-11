"""Actual Lúcio Sound Barrier input and Pharah's interaction with its temporary pool."""

import unittest
from dataclasses import replace

import test_pharah_control as control
import test_pharah_defenses as defense
from test_game_mover import FLOOR, wall

from ow174.game import sound_barrier
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.mover import IN_AIR


class PharahSoundBarrierTests(unittest.TestCase):
    def arena(self, *, friendly=False):
        fixture = defense.PharahDefenseTests()
        run, pharah, lucio, other = fixture.encounter("Zenyatta")
        self.assertTrue(run.match.switch_hero(lucio, heroes()[sound_barrier.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        lucio.team = other.team = 1
        pharah.team = int(friendly)
        y = pharah.mover.position[1]
        lucio.mover.position = [4, y, 8]
        other.mover.position = [0, y, 8]
        lucio.mover.body = other.mover.body = None
        for _ in range(5):
            run.step({})
        return fixture, run, pharah, lucio, other

    def cast(self, run, lucio):
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({lucio: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertIsNotNone(sound_barrier.landing_state(lucio))

    def land(self, run, vitals):
        for _ in range(140):
            if sound_barrier.SHIELD_TAG in vitals.temporary_shields:
                return vitals.temporary_shields[sound_barrier.SHIELD_TAG]
            run.step({})
        self.fail("Sound Barrier never granted a temporary pool")

    def test_enemy_sound_barrier_spends_temporary_shields_and_pharah_earns_damage_credit(self):
        fixture, run, pharah, lucio, target = self.arena()
        fight = run.match.combat
        vitals = fixture.health(run, target)
        base = dict(vitals.now)
        self.cast(run, lucio)
        pool = self.land(run, vitals)
        self.assertEqual(pool.now, 750)
        self.assertEqual(pool.most, 750)
        self.assertNotIn(sound_barrier.SHIELD_TAG, fixture.health(run, pharah).temporary_shields)
        start = fight.now
        fixture.fire(run, pharah)
        self.assertAlmostEqual(pool.now, 750 - (fight.now - start) * 125 - 120, places=5)
        self.assertEqual(vitals.now, base)
        charge = pharah.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 120)

        run.frame += 400
        run.match.update(run.frame * .016, run.frame)
        self.assertEqual(pool.now, 0)
        self.assertEqual(vitals.now, base)
        fixture.fire(run, pharah)
        self.assertEqual(vitals.total, sum(base.values()) - 120)
        self.assertEqual(charge.value(), 240)

    def test_airborne_lucio_grants_friendly_pharah_only_after_landing(self):
        fixture, run, pharah, lucio, other = self.arena(friendly=True)
        other.team = 0
        lucio.mover.position[1] += 12
        lucio.mover.body = None
        run.step({})
        vitals = fixture.health(run, pharah)
        self.cast(run, lucio)
        for _ in range(35):
            run.step({})
        self.assertTrue(lucio.mover.flags_word & IN_AIR)
        self.assertNotIn(sound_barrier.SHIELD_TAG, vitals.temporary_shields)
        pool = self.land(run, vitals)
        self.assertFalse(lucio.mover.flags_word & IN_AIR)
        self.assertEqual(pool.now, 750)
        self.assertNotIn(sound_barrier.SHIELD_TAG, fixture.health(run, other).temporary_shields)

    def test_concussion_displaces_through_shield_without_damage_or_charge(self):
        fixture, run, pharah, lucio, target = self.arena()
        vitals = fixture.health(run, target)
        base = vitals.total
        self.cast(run, lucio)
        pool = self.land(run, vitals)
        position = tuple(target.mover.position)
        start = run.match.combat.now
        run.step({pharah: (0, 0, 16)})
        for _ in range(35):
            run.step({})
        self.assertGreater(target.mover.position[2], position[2] + 1)
        self.assertAlmostEqual(pool.now, 750 - (run.match.combat.now - start) * 125, places=5)
        self.assertAlmostEqual(vitals.total - pool.now, base)
        charge = pharah.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 0)

    def test_quick_melee_spills_from_decayed_shield_into_base_health(self):
        fixture, run, pharah, lucio, target = self.arena()
        target.mover.position[2] = 1.8
        target.mover.body = None
        vitals = fixture.health(run, target)
        base = vitals.total
        self.cast(run, lucio)
        pool = self.land(run, vitals)
        charge = pharah.body_script.component.entity_var(342)
        for _ in range(375):
            if pool.now <= 12:
                break
            run.step({})
        self.assertGreater(pool.now, 2)
        self.assertLessEqual(pool.now, 12)
        remaining_at_hit = pool.now - 2
        run.step({pharah: (0, 0, 0x800)})
        self.assertEqual(pool.now, 0)
        self.assertAlmostEqual(vitals.total, base - (30 - remaining_at_hit), places=5)
        self.assertEqual(charge.value(), 30)

    def test_lethal_pharah_rocket_cancels_sound_barrier_before_release(self):
        fixture, run, pharah, lucio, target = self.arena()
        vitals = fixture.health(run, lucio)
        vitals.damage(vitals.total - 60)
        self.cast(run, lucio)
        fight = run.match.combat
        aim = fixture.aim_at_body(fight, pharah, lucio)
        run.step({pharah: (*aim, 1)})
        for _ in range(90):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, pharah.body)
        self.assertFalse(fight.sound_barriers.landings)
        self.assertFalse(fight.sound_barriers.waves)
        self.assertNotIn(sound_barrier.SHIELD_TAG, fixture.health(run, target).temporary_shields)

    def test_friendly_pharah_can_enter_the_live_wave_after_range_or_cover_exclusion(self):
        for blocked_by in ("range", "cover"):
            with self.subTest(blocked_by=blocked_by):
                fixture, run, pharah, lucio, _ = self.arena(friendly=True)
                fight = run.match.combat
                world = run.match.collision_world
                if blocked_by == "range":
                    pharah.mover.position[2] = -25
                    pharah.mover.body = None
                else:
                    run.match.collision_world = World(FLOOR + wall(-10, 4, 10, 4))
                self.cast(run, lucio)
                self.land(run, fixture.health(run, lucio))
                vitals = fixture.health(run, pharah)
                self.assertNotIn(sound_barrier.SHIELD_TAG, vitals.temporary_shields)
                self.assertIn(lucio.body, fight.sound_barriers.waves)

                if blocked_by == "range":
                    pharah.mover.position[2] = 0
                    pharah.mover.body = None
                else:
                    run.match.collision_world = world
                pool = self.land(run, vitals)
                self.assertEqual(pool.now, 750)
                self.assertIn(lucio.body, fight.sound_barriers.waves)
                for _ in range(5):
                    run.step({})
                self.assertAlmostEqual(pool.now, 740, places=5)

    def test_body_replacement_retires_wave_and_keeps_only_live_recipient_shields(self):
        fixture, run, pharah, lucio, late_ally = self.arena(friendly=True)
        fight = run.match.combat
        late_ally.mover.position[2] = -25
        late_ally.mover.body = None
        vitals = fixture.health(run, pharah)
        self.cast(run, lucio)
        pool = self.land(run, vitals)
        old_caster = lucio.body
        self.assertIn(old_caster, fight.sound_barriers.waves)
        self.assertTrue(run.match.switch_hero(lucio, lucio.hero))
        self.assertNotEqual(lucio.body, old_caster)
        self.assertNotIn(old_caster, fight.sound_barriers.waves)
        late_ally.mover.position[2] = 8
        late_ally.mover.body = None
        for _ in range(5):
            run.step({})
        self.assertAlmostEqual(pool.now, 740, places=5)
        self.assertNotIn(sound_barrier.SHIELD_TAG, fixture.health(run, late_ally).temporary_shields)

        old_recipient = pharah.body
        self.assertTrue(run.match.switch_hero(pharah, pharah.hero))
        self.assertNotEqual(pharah.body, old_recipient)
        self.assertNotIn(old_recipient, fight.sound_barriers.shielded)
        self.assertNotIn(sound_barrier.SHIELD_TAG, fixture.health(run, pharah).temporary_shields)

    def test_enemy_flashbang_debits_sound_barrier_and_still_blocks_pharahs_jet(self):
        fixture, run, pharah, lucio, enemy = self.arena(friendly=True)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000042]))
        enemy.team = 0
        enemy.mover.position = [0, pharah.mover.position[1], 3]
        enemy.mover.body = None
        vitals = fixture.health(run, pharah)
        base = dict(vitals.now)
        self.cast(run, lucio)
        pool = self.land(run, vitals)
        fight = run.match.combat
        start = fight.now
        commands = control.PharahControlTests()
        commands.step(run, pharah, enemy, cast=16)
        for _ in range(50):
            if fight.is_stunned(pharah):
                break
            commands.step(run, pharah, enemy)
        self.assertTrue(fight.is_stunned(pharah))
        self.assertEqual(vitals.now, base)
        self.assertAlmostEqual(pool.now, 750 - (fight.now - start) * 125 - 25, places=5)

        jet = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x27)
        end = fight.stuns[pharah.body]
        while run.frame < end - 1:
            commands.step(run, pharah, enemy, buttons=8)
        self.assertEqual(jet.vars[61].value(), 0)
        commands.step(run, pharah, enemy, buttons=8)
        self.assertFalse(fight.is_stunned(pharah))
        self.assertGreater(jet.vars[61].value(), 9.9)
        self.assertEqual(pharah.mover.velocity[1], 20)

    def test_same_barrage_depletes_sound_barrier_and_kills_exposed_target(self):
        fixture, run, pharah, lucio, target = self.arena()
        target.mover.position[2] = 4
        target.mover.body = None
        vitals = fixture.health(run, target)
        base = sum(vitals.now.values())
        self.cast(run, lucio)
        pool = self.land(run, vitals)
        fight = run.match.combat
        direction = fixture.aim_at_body(fight, pharah, target)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (*direction, 32)})
        self.assertTrue(script.ultimate_running())

        for _ in range(90):
            shield_before = pool.now
            run.step({pharah: (*direction, 0)})
            if pool.now == 0:
                break
            self.assertEqual(sum(vitals.now.values()), base)
        self.assertEqual(pool.now, 0)
        self.assertAlmostEqual(shield_before, 30, places=5)
        # Two points decay before the 40-damage hit. The remaining 28 absorb
        # part of the impact and the other 12 reach ordinary health.
        self.assertAlmostEqual(sum(vitals.now.values()), base - 12, places=5)
        self.assertFalse(vitals.dead)
        for _ in range(30):
            if vitals.dead:
                break
            run.step({pharah: (*direction, 0)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, pharah.body)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_friendly_sound_barrier_keeps_barrage_running_through_lethal_reflection(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                fixture, run, pharah, lucio, genji = self.arena(friendly=True)
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.team = 0
                genji.mover.position = [0, pharah.mover.position[1], 4]
                genji.mover.body = None
                genji.body_script.ult_seconds = 0
                genji.body_script.set_ult_charge(0)
                vitals = fixture.health(run, pharah)
                base = dict(vitals.now)
                if protected:
                    self.cast(run, lucio)
                    pool = self.land(run, vitals)
                fight = run.match.combat
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({genji: (-32768, 0, 16)})
                for _ in range(5):
                    run.step({genji: (-32768, 0, 0)})
                direction = fixture.aim_at_body(fight, pharah, genji)
                start = fight.now
                shield_before = pool.now if protected else 0
                credit = genji.body_script.component.entity_var(342)
                run.step({pharah: (*direction, 32), genji: (-32768, 0, 0)})
                for _ in range(90):
                    if vitals.dead or (protected and credit.value() >= sum(base.values())):
                        break
                    run.step({pharah: (*direction, 0), genji: (-32768, 0, 0)})
                self.assertEqual(vitals.dead, not protected)
                self.assertEqual(script.ultimate_running(), protected)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                if protected:
                    self.assertEqual(vitals.now, base)
                    lost = shield_before - pool.now - (fight.now - start) * 125
                    self.assertGreaterEqual(credit.value(), sum(base.values()))
                    self.assertAlmostEqual(credit.value(), lost, places=5)
                else:
                    self.assertEqual(vitals.killer, genji.body)
                    self.assertEqual(credit.value(), sum(base.values()))
