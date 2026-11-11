"""No owner input: authored scopes, timers and cooldowns advance via missing windows.

The declared server policy leaves the newest input window available. No claim
about native transport rollback or original-client presentation is made here.
"""

import math
import unittest

import test_game_reaper as reaper_tests
import test_reaper_defenses as defense_tests
import test_reaper_shadow as shadow_tests

from ow174.game import combat, defenses, reaper
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class ReaperWorldClockTests(unittest.TestCase):
    def world(self, run, ticks, *, skip=False):
        for _ in range(1 if skip else ticks):
            run.frame += ticks if skip else 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)

    def test_wraith_expires_recovers_and_cools_without_owner_input(self):
        run, owner, enemy, _ = reaper_tests.ReaperTests().scenario()
        fight = run.match.combat
        own_target = next(t for t in fight._targets() if t.owner is owner)
        run.step({owner: (0, 0, 8)})
        script = owner.body_script
        instance = next(i for i in script.component.instances.values()
                        if i.graph.index == reaper.WRAITH_GRAPH)
        counter = instance.state(1).counter
        last_input = owner.commands.last_frame
        self.world(run, 180)
        self.assertTrue(reaper.intangible(owner))
        fight._damage(enemy, own_target, 10, False, None, run.frame)
        self.assertEqual(fight.vitals_of(own_target).total, 250)
        self.world(run, 9)
        self.assertFalse(reaper.intangible(owner))
        self.assertFalse(instance.state(3).active)
        self.assertTrue(instance.state(7).active)
        fight._damage(enemy, own_target, 10, False, None, run.frame)
        self.assertEqual(fight.vitals_of(own_target).total, 240)
        self.world(run, 26)
        self.assertFalse(instance.state(7).active)
        self.assertGreater(instance.vars[61].value(), 7.9)
        self.assertEqual(owner.commands.last_frame, last_input)
        self.assertFalse(script.component.held)
        owner.take_commands([Command(script.frame + 1, buttons=8)])
        self.assertFalse(reaper.intangible(owner))  # actual cooldown refuses
        self.world(run, 515)
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertEqual(instance.state(1).counter, counter)  # no buffered late cast
        owner.take_commands([Command(script.frame + 1, buttons=8)])
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(instance.state(1).counter, counter + 1)

    def test_zen_and_mei_protection_expire_and_resource_rules_survive_idle_time(self):
        for hero, inside, after in ((defenses.ZENYATTA, 370, 12), (defenses.MEI, 244, 12)):
            with self.subTest(hero=hero):
                case = defense_tests.ReaperDefenseTests()
                run, attacker, defender, target = case.scene(hero)
                case.cast(run, defender)
                fight = run.match.combat
                start = fight.vitals_of(target).total
                self.world(run, inside)
                self.assertTrue(defenses.invulnerable(defender))
                fight._damage(attacker, target, 30, False, None, run.frame)
                self.assertEqual(fight.vitals_of(target).total, start)
                self.world(run, after)
                self.assertFalse(defenses.invulnerable(defender))
                fight._damage(attacker, target, 30, False, None, run.frame)
                self.assertEqual(fight.vitals_of(target).total, start - 30)
                script = defender.body_script
                button = 32 if hero == defenses.ZENYATTA else 8
                owner_frame = script.frame + 1
                defender.take_commands([Command(owner_frame, buttons=button)])
                self.assertFalse(defenses.invulnerable(defender))
                if hero == defenses.ZENYATTA:
                    self.assertLess(script.component.vars[342].value(), script.component.vars[338].value())
                    script.set_ult_charge(script.component.vars[338].value())
                else:
                    self.world(run, 760)
                defender.take_commands([Command(script.frame + 1, buttons=0)])
                defender.take_commands([Command(script.frame + 1, buttons=button)])
                self.assertTrue(defenses.invulnerable(defender))

    def test_skipped_world_windows_finish_blossom_damage_without_awarding_its_damage_charge(self):
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, enemy, target = reaper_tests.ReaperTests().scenario()
                fight = run.match.combat
                fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                self.world(run, 313, skip=skip)
                self.assertIsNone(reaper.blossom_state(owner.body_script))
                self.assertEqual(fight.vitals_of(target).total, 490)
                self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
                self.world(run, 125, skip=skip)
                self.assertEqual(fight.vitals_of(target).total, 490)

    def test_shadow_cast_lands_and_ends_immunity_without_owner_input(self):
        case = shadow_tests.ShadowStepTests()
        run, owner, _, instance = case.scene()
        start = tuple(owner.mover.position)
        case.select(run, owner)
        case.confirm(run, owner)
        record = run.match.combat.reaper_shadows[owner.body]
        self.world(run, 62)
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertFalse(reaper.intangible(owner))
        self.world(run, 5)
        self.assertTrue(reaper.intangible(owner))
        self.world(run, 31)
        self.assertFalse(reaper.intangible(owner))
        self.assertTrue(record["moved"])
        self.assertLess(math.dist(owner.mover.position, record["target"]), 0.002)
        self.world(run, 40)
        self.assertGreater(instance.vars[61].value(), 9.7)
        self.world(run, 650)
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertLess(math.dist(owner.mover.position, record["target"]), 0.002)

    def test_reanchor_handles_input_frame_offset_and_rejects_late_input_without_moving(self):
        run, owner, _, _ = reaper_tests.ReaperTests().scenario()
        script = owner.body_script
        owner.take_commands([Command(script.frame + 200, buttons=8)])
        self.assertTrue(reaper.intangible(owner))
        old_input = owner.commands.last_frame
        self.world(run, 220, skip=True)
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(owner.commands.last_frame, old_input)
        frame, now = script.frame, script.component.now
        anchor = script.world_frame_anchor
        position = tuple(owner.mover.position)
        owner.take_commands([Command(old_input + 1, forward=127, buttons=8)])
        self.assertEqual((script.frame, script.component.now), (frame, now))
        self.assertEqual(script.world_frame_anchor, anchor)
        self.assertEqual(tuple(owner.mover.position), position)

    def test_world_elapsed_scales_to_owner_quanta_and_replacement_gets_a_new_anchor(self):
        for quanta in (8000, 32000):
            with self.subTest(quanta=quanta):
                run, owner, _, _ = reaper_tests.ReaperTests().scenario()
                owner.quanta = quanta
                run.match.switch_hero(owner, heroes()[SOLDIER])
                run.match.switch_hero(owner, heroes()[reaper.HERO])
                script = owner.body_script
                owner.take_commands([Command(script.frame + 1, buttons=8)])
                self.world(run, 180)
                self.assertTrue(reaper.intangible(owner))
                self.world(run, 15)
                self.assertFalse(reaper.intangible(owner))
                run.match.switch_hero(owner, heroes()[SOLDIER])
                self.assertTrue(script.retired)
                frame = script.frame
                self.world(run, 125)
                self.assertEqual(script.frame, frame)
                run.match.switch_hero(owner, heroes()[reaper.HERO])
                self.assertEqual(owner.body_script.world_frame_anchor,
                                 (run.match.tick, owner.body_script.frame))

    def test_idle_fill_stops_at_wire_epoch_limit_and_does_not_change_unrelated_heroes(self):
        from ow174.game.projectiles import MAX_LEASE_FRAME

        run, owner, other, _ = reaper_tests.ReaperTests().scenario()
        unrelated = other.body_script
        old_frame = unrelated.frame
        owner.commands.reset(last_frame=MAX_LEASE_FRAME - 3)
        run.match.switch_hero(owner, heroes()[reaper.HERO])
        script = owner.body_script
        self.world(run, 100, skip=True)
        self.assertEqual(script.frame, MAX_LEASE_FRAME - 1)
        self.assertEqual(unrelated.frame, old_frame)
        self.world(run, 100, skip=True)
        self.assertEqual(script.frame, MAX_LEASE_FRAME - 1)
        self.assertEqual(owner.take_commands([Command(MAX_LEASE_FRAME + 1)]), MAX_LEASE_FRAME + 1)

    def test_missing_input_releases_primary_without_extra_damage_and_reload_finishes(self):
        case = reaper_tests.ReaperTests()
        run, owner, enemy, target = case.scenario()
        case.fire(run, owner, enemy)
        script = owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == reaper.SHOTGUN_GRAPH)
        self.assertEqual(weapon.vars[53].value(), 7)
        self.world(run, 150, skip=True)
        self.assertFalse(script.component.held)
        self.assertEqual(weapon.vars[53].value(), 7)
        self.assertEqual(run.match.combat.vitals_of(target).total, 80)
        owner.take_commands([Command(script.frame + 1, buttons=0x400)])
        self.world(run, 125, skip=True)
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(run.match.combat.vitals_of(target).total, 80)

    def test_blossom_acquisition_after_enemy_wraith_ends_matches_skipped_and_contiguous_world(self):
        results = []
        for skip, ticks in ((False, 200), (True, 200), (True, 1000)):
            with self.subTest(skip=skip, ticks=ticks):
                run, owner, enemy, _ = reaper_tests.ReaperTests().scenario()
                run.match.switch_hero(enemy, heroes()[reaper.HERO])
                x, y, z = owner.mover.position
                enemy.mover.position[:] = [x, y, z + 1.5]
                enemy.mover.body = None
                for _ in range(20):
                    run.step({})
                fight = run.match.combat
                fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
                owner.body_script.ult_seconds = 0
                run.step({enemy: (0, 0, 8)})
                for _ in range(99):
                    run.step({})
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                self.world(run, ticks, skip=skip)
                self.assertFalse(reaper.intangible(enemy))
                self.assertIsNone(reaper.blossom_state(owner.body_script))
                self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                results.append(fight.vitals[enemy.body].total)
        self.assertEqual(results, [728, 728, 728])  # eight pulses after immunity; earlier pulses reject


if __name__ == "__main__":
    unittest.main()
