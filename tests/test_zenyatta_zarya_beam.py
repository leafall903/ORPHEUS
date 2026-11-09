"""Actual 104319 Zarya beam against Zenyatta and projected protection."""
import unittest

import test_zenyatta_bubbles as bubbles
from test_game_combat import Run, aim

from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.script import expr
from ow174.game.zenyatta import HERO


class ZaryaBeamProbe(unittest.TestCase):
    def test_actual_fire_opens_damage_scope_and_requires_live_enemy_target(self):
        cases = (("body", 8, 75), ("charge", 8, 84.12), ("wall", 8, 0),
                 ("range", 17, 0), ("friendly", 8, 0), ("miss", 8, 0), ("ultimate", 8, 0))
        for case, distance, rate in cases:
            with self.subTest(case=case):
                fixtures = bubbles.ZenyattaBubbleTests()
                run, zen, zarya = fixtures.arena(distance)
                fight = run.match.combat
                if case == "charge":
                    run.step({zarya: (0, 0, 8)})
                    run.step({zen: (0, 600, FIRE)})
                    for _ in range(25):
                        run.step({})
                    self.assertAlmostEqual(fixtures.energy(zarya), 9.6, places=5)
                if case == "ultimate":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                if case == "friendly":
                    zen.team = zarya.team
                x, y, z = zen.mover.position
                if case == "wall":
                    plane = z + 4
                    run.match.collision_world = World([
                        (x-4, y-20, plane, x+4, y-20, plane, x-4, y+20, plane),
                        (x+4, y-20, plane, x+4, y+20, plane, x-4, y+20, plane),
                    ])
                angles = aim(fight._eye(zarya), (x + (4 if case == "miss" else 0), y+1, z))
                instances = zarya.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0x241)
                initial = fixtures.health(run, zen)
                for _ in range(30):
                    run.step({zarya: (*angles, FIRE)})
                state = instance.node_state(instance.graph.node(28))
                self.assertTrue(state.active)
                self.assertLess(instance.vars[53].value(), 100)
                dealt = initial - fixtures.health(run, zen)
                slot = instance.find_var(0, 536, create=False)
                selected = slot.value() if slot else None
                expected = expr.Entity(zen.body) if case in ("body", "charge", "ultimate") else None
                self.assertEqual(selected, expected)
                run.step({zarya: (*angles, 0)})
                self.assertFalse(state.active)
                self.assertEqual(fixtures.health(run, zen), initial-dealt)
                self.assertAlmostEqual(dealt, rate * 0.479, places=4)


    def test_actual_beam_damages_projected_bubble_before_zen(self):
        fixtures = bubbles.ZenyattaBubbleTests()
        run, zen, attacker = fixtures.arena(projected=True)
        defender = run.players[2]
        run.match.switch_hero(defender, heroes()[ZARYA])
        defender.team = zen.team
        x, y, z = zen.mover.position
        defender.mover.position = [x+4, y, z]
        defender.mover.body = None
        attacker.mover.position = [x, y, z+8]
        attacker.mover.body = None
        for _ in range(20):
            run.step({})
        fixtures.projection(run, defender, zen)
        fight = run.match.combat
        bubble = fight.barriers.bubbles[defender.body, PROJECTED]
        before = fixtures.health(run, zen)
        angles = aim(fight._eye(attacker), (x, y+1, z))
        for _ in range(30):
            run.step({attacker: (*angles, FIRE)})
        self.assertEqual(fixtures.health(run, zen), before)
        self.assertAlmostEqual(bubble.hp, 200 - 75*0.479, places=4)
        self.assertAlmostEqual(fixtures.energy(defender), 75*0.479*0.2, places=4)


    def test_beam_damage_cannot_backfill_across_unposed_command_gap(self):
        fixtures = bubbles.ZenyattaBubbleTests()
        run, zen, attacker = fixtures.arena()
        x, y, z = zen.mover.position
        angles = aim(run.match.combat._eye(attacker), (x, y+1, z))
        run.step({attacker: (*angles, FIRE)})
        before = fixtures.health(run, zen)
        run.frame += 70
        run.step({attacker: (*angles, FIRE)})
        self.assertAlmostEqual(before - fixtures.health(run, zen), 75*0.015, places=4)


    def test_actual_projected_barrier_break_routes_later_beam_ticks_to_zen(self):
        fixtures = bubbles.ZenyattaBubbleTests()
        run = Run(PRACTICE_RANGE, ("Zen", "Defender", "Attacker", "Orb shooter"))
        zen, defender, attacker, orb_shooter = run.players
        run.match.bots.clear()
        x, y, z = zen.mover.position
        actors = (
            (zen, HERO, 0, (x, y, z)), (defender, ZARYA, 0, (x+4, y, z)),
            (attacker, ZARYA, 1, (x, y, z+8)), (orb_shooter, HERO, 1, (x+1, y, z+7)),
        )
        for player, hero, team, position in actors:
            run.match.switch_hero(player, heroes()[hero])
            player.team = team
            player.mover.position = list(position)
            player.mover.body = None
        for _ in range(20):
            run.step({})
        fixtures.projection(run, defender, zen)
        fight = run.match.combat
        bubble = fight.barriers.bubbles[defender.body, PROJECTED]
        beam_aim = aim(fight._eye(attacker), (x, y+1, z))
        orb_aim = aim(fight._eye(orb_shooter), (x, y+1, z))
        initial = fixtures.health(run, zen)
        broken = None
        for frame in range(118):
            run.step({attacker: (*beam_aim, FIRE), orb_shooter: (*orb_aim, FIRE if frame in (0, 34) else 0)})
            if bubble.hp == 0:
                broken = frame
                break
            self.assertEqual(fixtures.health(run, zen), initial)
        self.assertIsNotNone(broken)
        self.assertAlmostEqual(fixtures.energy(defender), 40, delta=.0001)
        target = next(target for target in fight._targets() if target.owner is zen)
        self.assertIsNone(fight.barriers.bubble(target))
        activation = defender.body_script.component.instances[bubble.instance]
        self.assertGreater(activation.vars[61].value(), 7)
        before = fixtures.health(run, zen)
        instances = attacker.body_script.component.instances.values()
        weapon = next(instance for instance in instances if instance.graph.index == 0x0241)
        damage_state = weapon.node_state(weapon.graph.node(28))
        initial_ms = damage_state.last_applied
        for _ in range(8):
            run.step({attacker: (*beam_aim, FIRE)})
        elapsed_ms = damage_state.last_applied - initial_ms
        self.assertEqual(elapsed_ms, 128)
        # Stack176 publishes the body one VM update after TrackRay81 changes.
        self.assertAlmostEqual(before-fixtures.health(run, zen), 9.525, places=4)
        self.assertNotIn((defender.body, PROJECTED), fight.barriers.bubbles)

        for _ in range(170):
            if fight.is_dead(zen):
                break
            run.step({attacker: (*beam_aim, FIRE)})
        self.assertTrue(fight.is_dead(zen))
        self.assertEqual(fixtures.health(run, zen), 0)
        for _ in range(5):
            run.step({attacker: (*beam_aim, FIRE)})
        instances = attacker.body_script.component.instances.values()
        weapon = next(instance for instance in instances if instance.graph.index == 0x0241)
        self.assertIsNone(weapon.find_var(0, 536, create=False).value())


if __name__ == "__main__":
    unittest.main(verbosity=2)
