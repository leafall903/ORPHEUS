"""Actual Sombra Q against Zen, with reconstructed shield and input admission."""

import unittest
from dataclasses import replace

from test_game_combat import Run, aim
from test_game_mover import box

from ow174.game import combat, sombra_emp
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL
from ow174.game.zenyatta import DISCORD, HARMONY, HERO


class ZenyattaEmpTests(unittest.TestCase):
    def arena(self, distance=6):
        run = Run(names=("Zen", "Sombra", "Zarya"))
        zen, sombra, zarya = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.bots.clear()
        x, y, z = zen.mover.position
        for player, hero, team, position in (
            (zen, HERO, 0, (x, y, z)),
            (sombra, sombra_emp.SOMBRA, 1, (x, y, z+distance)),
            (zarya, ZARYA, 0, (x+6, y, z)),
        ):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position = list(position)
            player.mover.body = None
        run.step({})
        return run, zen, sombra, zarya

    def hp(self, run, player):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is player))

    @staticmethod
    def charge(player):
        return player.body_script.component.entity_var(342).value()

    @staticmethod
    def fill(player):
        cost = player.body_script.component.entity_var(338).value()
        player.body_script.set_ult_charge(cost)
        return cost

    @staticmethod
    def instances(player, index):
        return [i for i in player.body_script.component.instances.values() if i.graph.index == index]

    def test_actual_emp_strips_base_shields_and_locks_skills_until_its_child_expires(self):
        run, zen, sombra, zarya = self.arena()
        fight = run.match.combat
        vitals = self.hp(run, zen)
        owner = fight.zenyatta.owner(zen)
        old_clock = owner.last_damage
        cost = self.fill(zen)
        self.fill(sombra)
        run.step({sombra: (0, 0, 0x20)})
        for _ in range(65):
            run.step({})
            if self.instances(zen, sombra_emp.CHILD):
                break
        self.assertEqual(vitals.now[SHIELDS_POOL], 150)
        run.step({zen: (0, 0, 0x20)})
        self.assertFalse(fight.zenyatta.ultimate(zen))
        self.assertEqual(self.charge(zen), cost)
        self.assertEqual((vitals.now[HEALTH_POOL], vitals.now[SHIELDS_POOL]), (50, 0))
        self.assertEqual(self.charge(sombra), 0)
        self.assertGreater(owner.last_damage, old_clock)
        damaged_at = run.frame
        child, = self.instances(zen, sombra_emp.CHILD)
        chase = child.node_state(child.graph.node(4))
        self.assertEqual(child.vars[567].value(), (5,))
        self.assertTrue(chase.active)
        self.assertEqual(len(self.instances(zen, sombra_emp.EFFECT)), 1)

        direction = aim(fight._eye(zen), fight._eye(zarya))
        run.step({zen: (*direction, HARMONY)})
        self.assertIsNone(owner.harmony)
        run.step({zen: (0, 0, FIRE | DISCORD | 0x20)})
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.zenyatta.ultimate(zen))
        self.assertIsNone(owner.discord)
        self.assertEqual(self.charge(zen), cost)
        notice, = zen.client.messages(combat.HIT)
        self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], sombra.body)

        while run.frame < damaged_at+187:
            run.step({})
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
        run.step({})
        self.assertAlmostEqual(vitals.now[SHIELDS_POOL], .24, places=5)
        while chase.active and run.frame < damaged_at+320:
            run.step({})
        self.assertFalse(chase.active)
        self.assertIn(run.frame-damaged_at, (312, 313))
        self.assertFalse(self.instances(zen, sombra_emp.EFFECT))
        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(vitals.total, 200)
        self.assertEqual(self.charge(zen), 0)

    def test_actual_emp_rejects_protected_allied_distant_and_occluded_zen(self):
        for case in ("projection", "transcendence", "team", "range", "wall", "uncharged"):
            with self.subTest(case=case):
                run, zen, sombra, zarya = self.arena(distance=17 if case == "range" else 6)
                fight = run.match.combat
                bubble = None
                if case == "team":
                    sombra.team = zen.team
                if case == "wall":
                    x, y, z = zen.mover.position
                    run.match.collision_world = World(box(x-3, x+3, y-1, y+4, z+2, z+3))
                if case == "projection":
                    direction = aim(fight._eye(zarya), fight._eye(zen))
                    run.step({zarya: (*direction, HARMONY)})
                    bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                if case == "transcendence":
                    self.fill(zen)
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                old_clock = fight.zenyatta.owner(zen).last_damage
                if case != "uncharged":
                    self.fill(sombra)
                run.step({sombra: (0, 0, 0x20)})
                for _ in range(55):
                    run.step({})
                self.assertEqual(self.hp(run, zen).total, 200)
                self.assertEqual(fight.zenyatta.owner(zen).last_damage, old_clock)
                self.assertFalse(self.instances(zen, sombra_emp.CHILD))
                self.assertFalse(self.instances(zen, sombra_emp.EFFECT))
                if bubble is not None:
                    self.assertEqual(bubble.hp, 200)
                if case == "transcendence":
                    self.assertTrue(fight.zenyatta.ultimate(zen))

    def test_recast_replaces_emp_timer_without_resetting_clock_at_zero_shield(self):
        run, zen, sombra, _ = self.arena()
        fight = run.match.combat
        vitals = self.hp(run, zen)
        owner = fight.zenyatta.owner(zen)
        self.fill(sombra)
        run.step({sombra: (0, 0, 0x20)})
        for _ in range(80):
            run.step({})
        first, = self.instances(zen, sombra_emp.CHILD)
        first_effect, = self.instances(zen, sombra_emp.EFFECT)
        clock = owner.last_damage
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)

        self.fill(sombra)
        run.step({sombra: (0, 0, 0x20)})
        for _ in range(50):
            run.step({})
            if first.stopped and self.instances(zen, sombra_emp.EFFECT):
                break
        second, = self.instances(zen, sombra_emp.CHILD)
        effect, = self.instances(zen, sombra_emp.EFFECT)
        self.assertIsNot(second, first)
        self.assertIsNot(effect, first_effect)
        self.assertTrue(first.stopped)
        self.assertTrue(first_effect.stopped)
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
        self.assertEqual(owner.last_damage, clock)
        chase = second.node_state(second.graph.node(4))
        self.assertTrue(chase.active)

        old_body = zen.body
        self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
        self.assertNotEqual(zen.body, old_body)
        self.assertFalse(self.instances(zen, sombra_emp.CHILD))
        self.assertFalse(self.instances(zen, sombra_emp.EFFECT))
        self.fill(zen)
        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))

    def test_emp_rejected_presses_require_real_release_after_a_gap_across_expiry(self):
        run, zen, sombra, zarya = self.arena()
        fight = run.match.combat
        owner = fight.zenyatta.owner(zen)
        cost = self.fill(zen)
        self.fill(sombra)
        run.step({sombra: (0, 0, 0x20)})
        for _ in range(65):
            run.step({})
            if self.hp(run, zen).now[SHIELDS_POOL] == 0:
                break
        self.assertEqual(self.hp(run, zen).now[SHIELDS_POOL], 0)
        child, = self.instances(zen, sombra_emp.CHILD)
        chase = child.node_state(child.graph.node(4))
        hold = {zen: (0, 0, DISCORD | 0x20)}
        for _ in range(280):
            run.step(hold)
        self.assertTrue(chase.active)
        self.assertEqual(zen.body_script.emp_suppressed_buttons, DISCORD | 0x20)

        # This gap crosses the lock deadline and includes synthesized releases.
        run.frame += 54
        run.step(hold)
        self.assertFalse(chase.active)
        self.assertFalse(fight.zenyatta.ultimate(zen))
        self.assertIsNone(owner.discord)
        self.assertEqual(self.charge(zen), cost)

        ally_direction = aim(fight._eye(zen), fight._eye(zarya))
        run.step({zen: (*ally_direction, HARMONY | DISCORD)})
        self.assertEqual(owner.harmony.target, zarya.body)
        self.assertEqual(zen.body_script.emp_suppressed_buttons, DISCORD)
        run.step(hold)
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(self.charge(zen), 0)
        self.assertIsNone(owner.discord)
