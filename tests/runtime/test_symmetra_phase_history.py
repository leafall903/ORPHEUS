"""Actual original phase actors feed the existing Sentry history integral."""
import math
import unittest
from unittest.mock import patch
from test_game_combat import aim
from test_symmetra_combat import arena,health
from test_symmetra_turrets import wall
from ow174.game import combat,cryo,immunity,stats
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import runtime

class PhaseHistoryTests(unittest.TestCase):

    def scene(self, hero, position=(0, 1, 2)):
        run, owner, defender = arena()
        self.assertTrue(run.match.switch_hero(defender, heroes()[hero]))
        defender.mover.position[:] = position
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                x, y, z = defender.mover.position
                angles = aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (*angles, attack), defender: (0, 0, defense)})
        step(count=15)
        return (run, owner, defender, step)

    def sentry_damage_across_defense_expiry(self, hero, duration, gap):
        run, owner, defender, step = self.scene(hero, (2, 1, 1))
        fight = run.match.combat
        protected = cryo.active if hero == cryo.HERO else immunity.phased_out
        with patch.object(fight, '_collision', return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            step(count=40)
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.slow)
            step(defense=8)
            self.assertTrue(protected(defender))
            final_tick = run.frame + math.ceil(duration / combat.TICK) + 5
            damage_before = owner.stats.value(stats.HERO_DAMAGE)
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertIsNone(sentry.target)
            self.assertIsNone(sentry.slow)
            while run.frame < final_tick - gap:
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
            if gap > 1:
                self.assertTrue(protected(defender))
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage_before)
            run.frame += gap
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertFalse(protected(defender))
            self.assertEqual(sentry.target, defender.body)
            self.assertIsNotNone(sentry.slow)
            damage = owner.stats.value(stats.HERO_DAMAGE)
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
            return damage - damage_before

    def test_actual_phase_gap_keeps_same_damage_as_contiguous_world_ticks(self):
        for hero,duration,gaps in ((0x02E0000000000002,3,(16,64,80)),
                                   (0x02E00000000001A2,.75,(16,40))):
            with self.subTest(hero=hero):
                contiguous=self.sentry_damage_across_defense_expiry(hero,duration,1)
                self.assertGreater(contiguous,0)
                for gap in gaps:
                    with self.subTest(gap=gap):
                        self.assertAlmostEqual(self.sentry_damage_across_defense_expiry(hero,duration,gap),
                                               contiguous,places=4)

    def test_actual_unequal_body_epoch_uses_existing_phase_clock_pair(self):
        for hero,duration in ((0x02E0000000000002,3),(0x02E00000000001A2,.75)):
            with self.subTest(hero=hero):
                run,owner,defender,step=self.scene(hero)
                fight=run.match.combat
                script=defender.body_script
                defender.take_commands([Command(script.frame+1000,buttons=8)])
                self.assertTrue(immunity.phased_out(defender))
                self.assertGreater(script.frame,fight.tick+500)
                before=(script.frame,script.component.frame,script.component.now,len(script.component.instances))
                with patch.object(runtime.Component,'run_now',side_effect=AssertionError('deadline ran VM')), \
                        patch.object(runtime.Component,'run_frame',side_effect=AssertionError('deadline ran VM')):
                    deadline=immunity.phase_world_deadline(fight,defender)
                self.assertEqual(before,(script.frame,script.component.frame,script.component.now,len(script.component.instances)))
                self.assertEqual(deadline-fight.tick,math.ceil(duration/combat.TICK))
                run.match.update((deadline-1)*combat.TICK,deadline-1,frames=False)
                self.assertTrue(immunity.phased_out(defender))
                run.match.update(deadline*combat.TICK,deadline,frames=False)
                self.assertFalse(immunity.phased_out(defender))
