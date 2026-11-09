"""Ana's actual grenade modifies real Brigitte heals."""

import unittest

import test_game_brigitte as brigitte_tests

from ow174.game import stats
from ow174.game.commands import FIRE
from ow174.game.content import heroes


class HealingModifierTests(unittest.TestCase):
    def scene(self, friendly, recipient_hero='Roadhog', initial_health=200):
        run, brig = brigitte_tests.BrigitteTests().fixture(('Brigitte', 'Ana', 'Recipient', 'Enemy'))
        match = run.match
        match.game_map = match.game_map.__class__(**{**match.game_map.__dict__, 'free_for_all': False})
        ana, recipient, enemy = run.players[1:]
        for player, name in ((ana, 'Ana'), (recipient, recipient_hero)):
            self.assertTrue(match.switch_hero(player, next(h for h in heroes().values() if h.name == name)))
        ana.team = brig.team if friendly else 1 - brig.team
        recipient.team, enemy.team = brig.team, 1 - brig.team
        x, y, z = brig.mover.position
        for player, where in ((ana, (x + 3, y, z + 10)), (recipient, (x, y, z + 10)),
                              (enemy, (x, y, z + 3))):
            player.mover.position, player.mover.body = list(where), None
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is recipient)
        hp = fight.vitals_of(target)
        fight._damage(enemy, target, hp.total - initial_health, False, None, run.frame)
        self.assertEqual(hp.total, initial_health)
        for _ in range(6):
            run.step({})
        return run, brig, ana, recipient, hp

    def grenade(self, run, ana, hp, friendly, direction=(-16384, 0)):
        before = hp.total
        run.step({ana: (*direction, 16)})
        for _ in range(40):
            run.step({ana: (*direction, 0)})
            start, end = hp.healing_boost if friendly else hp.healing_block
            if end > 0:
                self.assertAlmostEqual(end - start, 3.95 if friendly else 4.0)
                self.assertEqual(hp.total, before + (100 if friendly else -60))
                return end
        self.fail('actual grenade did not apply a healing modifier')

    def advance(self, run, frames):
        for _ in range(frames):
            run.step({})

    def pack(self, run, brig):
        run.step({brig: (0, 0, 16)})
        self.advance(run, 165)

    def test_repair_pack_amplifies_or_blocks_then_restores_at_expiry(self):
        for friendly in (True, False):
            with self.subTest(friendly=friendly):
                run, brig, ana, _recipient, hp = self.scene(friendly)
                until = self.grenade(run, ana, hp, friendly)
                before = hp.total
                self.pack(run, brig)
                expected = 165 if friendly else 0
                self.assertAlmostEqual(hp.total - before, expected, places=5)
                self.assertAlmostEqual(brig.stats.value(stats.HEALING), expected, places=5)
                while run.match.combat.now <= until:
                    run.step({})
                before = hp.total
                self.pack(run, brig)
                self.assertAlmostEqual(hp.total - before, 110, places=5)

    def test_inspire_integrates_status_expiry_and_grenade_activation_delay(self):
        for friendly in (True, False):
            with self.subTest(friendly=friendly):
                run, brig, ana, recipient, hp = self.scene(friendly)
                self.grenade(run, ana, hp, friendly)
                before = hp.total
                for _ in range(20):
                    run.step({brig: (0, 0, FIRE)})
                fight = run.match.combat
                heal = fight.brigitte.inspire[brig.body, recipient.body]
                start = heal.until - 6.0
                status_start, status_end = hp.healing_boost if friendly else hp.healing_block
                self.assertGreater(status_end, start)
                self.assertLess(status_end, heal.until)
                overlap = status_end - max(start, status_start)
                self.advance(run, 400)
                expected = 90 + 15 * overlap * (0.5 if friendly else -1)
                self.assertAlmostEqual(hp.total - before, expected, places=5)

    def test_rally_armour_pulses_are_independent_of_actual_ana_healing_modifiers(self):
        for friendly in (True, False):
            with self.subTest(friendly=friendly):
                run, brig, ana, recipient, hp = self.scene(friendly)
                self.grenade(run, ana, hp, friendly)
                x, y, z = brig.mover.position
                recipient.mover.position, recipient.mover.body = [x, y, z + 4], None
                fight = run.match.combat
                modifier = 1.5 if friendly else 0
                start, _ = hp.healing_boost if friendly else hp.healing_block
                for _ in range(8):
                    if fight.now >= start:
                        break
                    run.step({})
                self.assertEqual(hp.healing_factor(fight.now), modifier)
                before = hp.total
                healing = brig.stats.value(stats.HEALING)
                brig.body_script.set_ult_charge(brig.body_script.component.entity_var(338).value())
                run.step({brig: (0, 0, 32)})
                self.assertEqual(hp.temporary_armour, 15)
                self.advance(run, 32)
                self.assertEqual(hp.healing_factor(fight.now), modifier)
                self.assertEqual(hp.temporary_armour, 30)
                self.assertEqual(hp.total - hp.temporary_armour, before)
                self.assertEqual(brig.stats.value(stats.HEALING), healing)

    def test_personal_barrier_cleanse_resumes_an_already_delivered_repair_pack(self):
        run, brig, ana, zarya, hp = self.scene(False, "Zarya", 250)
        expiry = self.grenade(run, ana, hp, False)
        fight = run.match.combat
        before = hp.total
        run.step({brig: (0, 0, 16)})
        self.advance(run, 60)
        key = (brig.body, zarya.body)
        heal = fight.brigitte.repairs[key]
        self.assertEqual(hp.total, before)
        self.assertEqual(hp.healing_factor(fight.now), 0)
        run.step({zarya: (-32768, 0, 8)})
        self.assertIsNotNone(fight._personal_barrier_pet(zarya))
        self.assertEqual(hp.healing_factor(fight.now), 1)
        cleanse_at = hp.healing_block[1]
        self.assertGreater(cleanse_at, heal.until - 2)
        self.assertLess(cleanse_at, heal.until)
        for _ in range(130):
            if key not in fight.brigitte.repairs:
                break
            run.step({})
        self.assertNotIn(key, fight.brigitte.repairs)
        self.assertLess(fight.now, expiry)
        self.assertAlmostEqual(hp.total - before, 55 * (heal.until - cleanse_at), places=5)

    def test_fade_and_cryo_cleanse_enemy_grenade_during_active_pack(self):
        for name, graph_id, phase in (("Moira", 0x0F7C, 3), ("Mei", 0x05FE, 6)):
            with self.subTest(hero=name):
                run, brig, ana, recipient, hp = self.scene(False, name, 85)
                old_expiry = self.grenade(run, ana, hp, False)
                fight = run.match.combat
                run.step({brig: (0, 0, 16)})
                self.advance(run, 60)
                key = (brig.body, recipient.body)
                heal = fight.brigitte.repairs[key]
                self.assertEqual(hp.healing_factor(fight.now), 0)
                self.assertEqual(brig.stats.value(stats.HEALING), 0)
                run.step({recipient: (-32768, 0, 8)})
                script = next(i for i in recipient.body_script.component.instances.values()
                              if i.graph.index == graph_id)
                self.assertTrue(script.states[phase].active)
                self.assertEqual(hp.healing_factor(fight.now), 1)
                cleanse_at = hp.healing_block[1]
                self.assertLess(heal.last, heal.until)
                self.assertGreater(cleanse_at, heal.until - 2)
                self.assertLess(heal.until, old_expiry)
                self.advance(run, 115)
                self.assertNotIn(key, fight.brigitte.repairs)
                self.assertLess(fight.now, old_expiry)
                self.assertAlmostEqual(
                    brig.stats.value(stats.HEALING), 55 * (heal.until - cleanse_at), places=5,
                )

    def test_wraith_cleanses_enemy_grenade_and_pack_heals_before_old_expiry(self):
        run, brig, ana, reaper, hp = self.scene(False, 'Reaper', 120)
        expiry = self.grenade(run, ana, hp, False)
        fight = run.match.combat
        self.assertEqual(hp.healing_factor(fight.now), 0)
        run.step({reaper: (-32768, 0, 8)})
        self.assertTrue(fight._invulnerable(reaper))
        self.assertEqual(hp.healing_factor(fight.now), 1)
        self.advance(run, 30)
        run.step({reaper: (-32768, 0, 8)})
        self.assertFalse(fight._invulnerable(reaper))
        before = hp.total
        self.pack(run, brig)
        self.assertLess(fight.now, expiry)
        self.assertAlmostEqual(hp.total - before, 110, places=5)
