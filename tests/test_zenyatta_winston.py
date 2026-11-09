"""Actual Winston 00FA barrier admission against Zenyatta attacks and support."""
import unittest

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.barriers import WINSTON
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.zenyatta import HERO as ZEN


class WinstonDomeZenTests(unittest.TestCase):
    def arena(self, distance=8):
        run = Run(names=("Zen", "Winston", "Ally"))
        zen, winston, ally = run.players
        run.match.bots.clear()
        x, y, z = zen.mover.position
        for player, hero, team, offset in (
            (zen, ZEN, 0, (0, 0)), (winston, WINSTON, 1, (0, distance)), (ally, None, 0, (2, distance))
        ):
            if hero is not None:
                self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position = [x+offset[0], y, z+offset[1]]
            player.mover.body = None
        for _ in range(90):
            run.step({})
        run.step({winston: (-32768, 0, 0x10)})
        fight = run.match.combat
        for _ in range(45):
            dome = fight.barriers.domes.get(winston.body)
            if dome is not None and dome.hp > 0:
                break
            run.step({})
        self.assertIsNotNone(dome)
        self.assertEqual(dome.hp, 700)
        self.assertEqual(tuple(i.graph.index for i in dome.component.instances.values()), (0xFC,))
        return run, zen, winston, ally, dome

    def press(self, run, player, target, buttons):
        fight = run.match.combat
        x, y, z = target.mover.position
        run.step({player: (*aim(fight._eye(player), (x, y+1.1, z)), buttons)})

    def test_actual_dome_intercepts_primary_and_charged_orbs_until_child_expiry(self):
        run, zen, winston, _, dome = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is winston)
        hp = fight.vitals_of(target)
        before = hp.total
        child, = dome.component.instances.values()
        timer = child.states[11]
        self.press(run, zen, winston, FIRE)
        for _ in range(35):
            run.step({})
        self.assertEqual(dome.hp, 652)
        self.assertEqual(hp.total, before)
        for _ in range(130):
            self.press(run, zen, winston, SECONDARY)
        self.press(run, zen, winston, 0)
        for _ in range(60):
            run.step({})
        self.assertEqual(dome.hp, 412)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertEqual(hp.total, before)
        for _ in range(650):
            run.step({})
            if winston.body not in fight.barriers.domes:
                break
        self.assertNotIn(winston.body, fight.barriers.domes)
        self.assertAlmostEqual(timer.ended_at-timer.start, 9000, delta=16)
        self.press(run, zen, winston, FIRE)
        for _ in range(30):
            run.step({})
        self.assertLess(hp.total, before)

    def test_actual_dome_blocks_discord_and_q_healing_but_allows_harmony(self):
        run, zen, winston, ally, dome = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        health = fight.vitals_of(target)
        health.damage(100)
        self.press(run, zen, winston, 0x08)
        run.step({})
        owner = fight.zenyatta.owners[zen.body]
        self.assertIsNone(owner.discord)
        self.assertNotIn("discord", owner.ready)
        self.press(run, zen, ally, 0x10)
        self.assertEqual(owner.harmony.target, ally.body)
        start = health.total
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.press(run, zen, ally, 0x20)
        for _ in range(10):
            run.step({})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertAlmostEqual(health.total-start, 11*.48)
        self.assertEqual(dome.hp, 700)

    def test_breaking_dome_takes_no_overflow_and_next_orb_hits_body(self):
        run, zen, winston, _, dome = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is winston)
        health = fight.vitals_of(target)
        before = health.total
        dome.hp = 30  # pre-worn barrier fixture
        self.press(run, zen, winston, FIRE)
        for _ in range(40):
            run.step({})
        self.assertEqual(dome.hp, 0)
        self.assertNotIn(winston.body, fight.barriers.domes)
        self.assertEqual(health.total, before)
        self.press(run, zen, winston, FIRE)
        for _ in range(20):
            run.step({})
        self.assertLess(health.total, before)

    def test_actual_orb_from_inside_dome_hits_body_before_boundary(self):
        run, zen, winston, _, dome = self.arena(distance=3)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is winston)
        health = fight.vitals_of(target)
        before = health.total
        self.press(run, zen, winston, FIRE)
        for _ in range(20):
            run.step({})
        self.assertEqual(dome.hp, 700)
        self.assertLess(health.total, before)

    def test_actual_melee_reaches_enemy_across_near_dome_boundary(self):
        run, zen, _, opponent, dome = self.arena()
        fight = run.match.combat
        x, y, z = dome.centre
        zen.mover.position = [x, y-.1, z-5.6]
        opponent.mover.position = [x, y-.1, z-4]
        zen.mover.body = opponent.mover.body = None
        opponent.team = 1
        run.step({})
        target = next(t for t in fight._targets() if t.owner is opponent)
        health = fight.vitals_of(target)
        before = health.total
        self.press(run, zen, opponent, 0x800)
        for _ in range(35):
            run.step({})
        self.assertEqual(health.total, before-30)
        self.assertEqual(dome.hp, 700)

    def test_actual_sleep_dart_is_consumed_by_zen_allied_dome(self):
        run, zen, winston, ana, dome = self.arena()
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        zen.team, ana.team = winston.team, 0
        x, y, z = dome.centre
        zen.mover.position = [x+1.5, y-.1, z]
        ana.mover.position = [x+1.5, y-.1, z-8]
        zen.mover.body = ana.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is zen)
        health = fight.vitals_of(target)
        self.press(run, ana, zen, 8)
        for _ in range(55):
            self.press(run, ana, zen, 0)
        self.assertEqual(dome.hp, 695)
        self.assertEqual(health.total, 200)
        self.assertNotIn(zen.body, fight.sleeps)
