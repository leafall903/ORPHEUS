"""Actual attacks against Mei's solid Cryo-Freeze combat proxy."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import aim, in_world
from test_symmetra_combat import arena, health
from test_symmetra_turrets import wall

from ow174.game import combat, cryo, stats
from ow174.game.content import SOLDIER, heroes


class SymmetraCryoTests(unittest.TestCase):
    def scene(self, position=(0, 1, 1.7), behind=(0, 1, 2.6)):
        run, owner, mei = arena()
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        mei.mover.position[:] = position
        rear = run.match.add_player(3, "Rear", SOLDIER, 0, False)
        in_world(run.match, rear)
        run.players.append(rear)
        self.assertTrue(run.match.switch_hero(rear, heroes()[SOLDIER]))
        rear.mover.position[:] = behind
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                x, y, z = mei.mover.position
                angles = aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (*angles, attack), mei: (0, 0, defense)})

        step(count=20)
        return run, owner, mei, rear, step

    def test_ice_stops_actual_beam_orb_and_melee_before_rear_body(self):
        for attack in (1, 2, 0x800):
            with self.subTest(attack=attack):
                run, owner, mei, rear, step = self.scene()
                if attack == 2:
                    step(2, count=90)
                step(2 if attack == 2 else 0, 8)
                self.assertTrue(cryo.active(mei))
                before = health(run, mei).total, health(run, rear).total
                step(attack if attack != 2 else 0, count=5 if attack == 1 else 1)
                if attack == 2:
                    self.assertTrue(run.match.combat.rockets)
                    step(count=20)
                    self.assertFalse(run.match.combat.rockets)
                self.assertEqual((health(run, mei).total, health(run, rear).total), before)
                self.assertFalse(owner.client.messages(combat.HIT))
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_primary_cancel_and_world_idle_expiry_restore_actual_beam_damage(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, _, mei, _, step = self.scene()
                step(defense=8)
                self.assertTrue(cryo.active(mei))
                if cancel:
                    step(count=30)
                    step(defense=1)
                else:
                    for _ in range(260):
                        if not cryo.active(mei):
                            break
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(cryo.active(mei))
                before = health(run, mei).total
                step(1)
                self.assertLess(health(run, mei).total, before)

    def test_cryo_self_heals_for_authored_total_without_owner_commands(self):
        run, _, mei, _, step = self.scene()
        for _ in range(250):
            if health(run, mei).total < 49:
                break
            step(1)
        before = health(run, mei).total
        self.assertGreater(before, 0)
        self.assertGreater(sum(health(run, mei).most.values()) - before, 200)
        step(defense=8)
        self.assertTrue(cryo.active(mei))
        for _ in range(260):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(cryo.active(mei))
        self.assertAlmostEqual(health(run, mei).total - before, 200)
        self.assertAlmostEqual(mei.stats.value(stats.HEALING), 200)

    def test_cancel_accounts_only_elapsed_healing_once(self):
        run, _, mei, _, step = self.scene()
        step(1, count=35)
        before = health(run, mei).total
        step(defense=8)
        step(count=20)
        active_sample = cryo.healing_sample(mei)
        self.assertTrue(cryo.active(mei))
        self.assertAlmostEqual(health(run, mei).total - before, active_sample[1])
        self.assertAlmostEqual(active_sample[1], 20 * combat.TICK * 50)
        step(defense=1)
        self.assertFalse(cryo.active(mei))
        ended_sample = cryo.healing_sample(mei)
        self.assertAlmostEqual(health(run, mei).total - before, ended_sample[1])
        step(1, count=8)
        self.assertLess(health(run, mei).total, before + ended_sample[1])
        self.assertAlmostEqual(mei.stats.value(stats.HEALING), ended_sample[1])

    def test_overheal_is_neither_credited_nor_banked(self):
        run, _, mei, _, step = self.scene()
        step(1, count=10)
        vitals = health(run, mei)
        maximum = sum(vitals.most.values())
        deficit = maximum - vitals.total
        self.assertGreater(deficit, 0)
        step(defense=8)
        step(count=100)
        self.assertTrue(cryo.active(mei))
        self.assertAlmostEqual(vitals.total, maximum)
        self.assertAlmostEqual(mei.stats.value(stats.HEALING), deficit)
        step(defense=1)
        step(1, count=8)
        self.assertLess(vitals.total, maximum)
        self.assertAlmostEqual(mei.stats.value(stats.HEALING), deficit)

    def test_allied_cryo_still_blocks_beam_until_cancel(self):
        run, owner, mei, rear, step = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = owner.team
        rear.team = 1 - owner.team
        step(defense=8)
        self.assertTrue(cryo.active(mei))
        before = health(run, rear).total
        step(1, count=30)
        self.assertEqual(health(run, rear).total, before)
        step(defense=1)
        self.assertFalse(cryo.active(mei))
        step(1, count=5)
        self.assertLess(health(run, rear).total, before)

    def test_flashbang_detonates_on_ice_without_stunning_behind_it(self):
        run, caster, mei, rear, _ = self.scene()
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.mover.position[:] = (0, 1, 0)
        fight = run.match.combat
        run.step({mei: (0, 0, 8)})
        self.assertTrue(cryo.active(mei))
        before = health(run, mei).total, health(run, rear).total
        angles = aim(fight._eye(caster), (0, 2, 1.7))
        run.step({caster: (*angles, 16)})
        self.assertTrue(fight.rockets)
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual((health(run, mei).total, health(run, rear).total), before)
        self.assertFalse(fight.is_stunned(mei))
        self.assertFalse(fight.is_stunned(rear))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_ice_occludes_sentry_to_rear_then_sentry_resumes(self):
        run, owner, mei, rear, step = self.scene(behind=(0, 1, .7))
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.attached_at)
            step(defense=8)
            self.assertTrue(cryo.active(mei))
            before = health(run, mei).total, health(run, rear).total
            step(count=60)
            self.assertIsNone(sentry.target)
            self.assertEqual((health(run, mei).total, health(run, rear).total), before)
            step(defense=1)
            step(count=5)
            self.assertEqual(sentry.target, mei.body)
            self.assertLess(health(run, mei).total, before[0])

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_expiring_cryo_only_limits_sentry_catch_up_on_occluded_ray(self):
        contiguous = self.sentry_damage_across_cryo_cover_expiry(1, blocks=True)
        self.assertAlmostEqual(contiguous, 5 * 40 * combat.TICK, places=4)
        for gap in (16, 64, 80):
            with self.subTest(gap=gap):
                self.assertAlmostEqual(self.sentry_damage_across_cryo_cover_expiry(gap, blocks=True),
                                       contiguous, places=4)
        self.assertAlmostEqual(self.sentry_damage_across_cryo_cover_expiry(64, blocks=False),
                               64 * 40 * combat.TICK, places=4)

    def sentry_damage_across_cryo_cover_expiry(self, gap, *, blocks):
        run, owner, mei, rear, step = self.scene(
            position=(0 if blocks else 3, 1, 1.7), behind=(0, 1, .7),
        )
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = owner.team
        rear.team = 1 - owner.team
        fight = run.match.combat
        step(defense=8)
        self.assertTrue(cryo.active(mei))
        final_tick = run.frame + round(4 / combat.TICK) + 5
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            while run.frame < final_tick - gap:
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
            [sentry] = fight.sentries.sentries
            damage_before = owner.stats.value(stats.HERO_DAMAGE)
            if gap > 1:
                self.assertTrue(cryo.active(mei))
                self.assertEqual(sentry.target, None if blocks else rear.body)
                if blocks:
                    self.assertEqual(damage_before, 0)
            run.frame += gap
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertFalse(cryo.active(mei))
            self.assertEqual(sentry.target, rear.body)
            self.assertIsNotNone(sentry.slow)
            self.assertEqual(health(run, mei).total, 250)
            damage = owner.stats.value(stats.HERO_DAMAGE)
            self.assertAlmostEqual(200 - health(run, rear).total, damage, places=4)
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
            return damage if blocks else damage - damage_before

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_active_sentry_slow_retires_on_cryo_entry_and_reapplies_after_cancel(self):
        run, owner, mei, _, step = self.scene(behind=(20, 1, 0))
        fight = run.match.combat
        component = mei.body_script.component
        slow = component.entity_var(23186)
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            step(count=50)
            [sentry] = fight.sentries.sentries
            self.assertEqual(sentry.target, mei.body)
            self.assertAlmostEqual(slow.value(), .2)
            _, old_aura = sentry.slow
            step(defense=8)
            self.assertTrue(cryo.active(mei))
            self.assertIsNone(sentry.target)
            self.assertIsNone(sentry.slow)
            self.assertNotIn(old_aura.id, component.instances)
            self.assertFalse(slow.value())
            step(count=20)
            self.assertFalse(slow.value())
            step(defense=1)
            self.assertFalse(cryo.active(mei))
            self.assertFalse(slow.value())
            before = health(run, mei).total
            step()
            self.assertEqual(sentry.target, mei.body)
            _, new_aura = sentry.slow
            self.assertIsNot(new_aura, old_aura)
            self.assertAlmostEqual(slow.value(), .2)
            self.assertLess(health(run, mei).total, before)
