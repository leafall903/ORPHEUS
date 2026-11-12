"""Actual 104319 Zarya beam against Zenyatta and projected protection."""
import unittest
from dataclasses import replace

import test_zenyatta_bubbles as bubbles
from test_game_combat import Run, aim

from ow174.game import combat, dva, stats
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.flashbang import HERO as MCCREE
from ow174.game.script import expr
from ow174.game.zenyatta import HERO


class ZaryaBeamProbe(unittest.TestCase):
    def test_actual_flashbang_interrupts_beam_until_observed_command_at_expiry(self):
        for omitted in (0, 3):
            with self.subTest(omitted=omitted):
                run = Run(names=("Zarya", "Zen", "McCree"))
                zarya, zen, caster = run.players
                run.match.bots.clear()
                x, y, z = zarya.mover.position
                for player, guid, team, dx, dz in (
                    (zarya, ZARYA, 0, 0, 0), (zen, HERO, 1, 0, 6), (caster, MCCREE, 1, 2, 2),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[guid]))
                    player.team = team
                    player.mover.position = [x+dx, y, z+dz]
                    player.mover.body = None
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                weapon = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x0241)
                firing = {zarya: (0, 0, FIRE)}
                for _ in range(20):
                    run.step(firing)
                state = weapon.node_state(weapon.graph.node(28))
                self.assertTrue(state.active)
                self.assertEqual(weapon.vars[536].value(), expr.Entity(zen.body))
                activation = state.counter
                before = zarya.stats.value(stats.HERO_DAMAGE)
                run.frame += 3
                run.step(firing)
                self.assertEqual(state.counter, activation)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE)-before, 75*.064, places=4)

                cast_aim = aim(fight._eye(caster), (x, y+1, z))
                saw_flash = False
                for frame in range(50):
                    run.step({**firing, caster: (*cast_aim, 16 if frame == 0 else 0)})
                    saw_flash |= any(r.blast and r.blast.effect == combat.FLASHBANG_EFFECT for r in fight.rockets)
                    if fight.is_stunned(zarya):
                        break
                self.assertTrue(saw_flash)
                end = fight.stuns[zarya.body]
                self.assertEqual(end-run.frame, 50)
                self.assertFalse(state.active)
                ammo, damage = weapon.vars[53].value(), zarya.stats.value(stats.HERO_DAMAGE)
                while run.frame < end-omitted-1:
                    run.step(firing)
                    self.assertTrue(fight.is_stunned(zarya))
                    self.assertFalse(state.active)
                    self.assertEqual(weapon.vars[53].value(), ammo)
                    self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), damage)
                run.frame += omitted
                run.step(firing)
                self.assertEqual(run.frame, end)
                self.assertFalse(fight.is_stunned(zarya))
                self.assertTrue(state.active)
                self.assertEqual(state.counter, activation+1)
                self.assertEqual(weapon.vars[536].value(), expr.Entity(zen.body))
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE)-damage, 75*.015, places=4)

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


    def test_actual_beam_damages_active_projectile_guards_then_zen_until_q(self):
        cases = (
            (heroes()[0x02E0000000000029], 0x03A3, 0x10),
            (heroes()[0x02E000000000023B], 0x1D02, 0x08),
            (replace(heroes()[dva.HERO], body=dva.MECH), 0x02CC, SECONDARY),
        )
        fixtures = bubbles.ZenyattaBubbleTests()
        for hero, graph_index, button in cases:
            with self.subTest(graph=hex(graph_index)):
                run = Run(PRACTICE_RANGE, ("Zarya", "Zen", "Guard"))
                zarya, zen, guard = run.players
                run.match.bots.clear()
                x, y, z = zarya.mover.position
                for player, definition, team, offset in (
                    (zarya, heroes()[ZARYA], 1, 0), (zen, heroes()[HERO], 0, 8), (guard, hero, 0, 4),
                ):
                    self.assertTrue(run.match.switch_hero(player, definition))
                    player.team = team
                    player.mover.position = [x, y, z+offset]
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                fight = run.match.combat
                instance = next(i for i in guard.body_script.component.instances.values() if i.graph.index == graph_index)
                direction = aim(fight._eye(zarya), (x, y+1, z+8))
                guard_input = (-32768, 0, button)
                run.step({guard: guard_input})
                defense = next(state for state in instance.states.values()
                               if state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles")
                guard_before = fixtures.health(run, guard)
                for _ in range(30):
                    run.step({guard: guard_input, zarya: (*direction, FIRE)})
                    self.assertTrue(defense.active)
                self.assertLess(fixtures.health(run, guard), guard_before)
                self.assertEqual(fixtures.health(run, zen), 200)
                self.assertEqual(fixtures.health(run, zarya), 400)
                self.assertFalse(fight.rockets)
                self.assertFalse(guard.client.messages(combat.HIT))
                self.assertEqual({hit["+0x78"]["+0x0"]["+0x0"] for hit in zarya.client.messages(combat.HIT)},
                                 {guard.body})
                if graph_index == 0x1D02:
                    self.assertEqual(instance.find_var(0, 21312).value(), 0)

                guard.mover.position[0] += 10
                guard.mover.body = None
                for _ in range(12):
                    run.step({guard: guard_input, zarya: (*direction, FIRE)})
                self.assertLess(fixtures.health(run, zen), 200)
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                hits = len(zarya.client.messages(combat.HIT))
                for _ in range(20):
                    run.step({zarya: (*direction, FIRE)})
                weapon = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x0241)
                self.assertTrue(weapon.node_state(weapon.graph.node(28)).active)
                self.assertEqual(weapon.find_var(0, 536, create=False).value(), expr.Entity(zen.body))
                self.assertEqual(fixtures.health(run, zen), 200)
                self.assertEqual(len(zarya.client.messages(combat.HIT)), hits)

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
