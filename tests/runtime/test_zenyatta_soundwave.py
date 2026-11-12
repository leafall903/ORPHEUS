import unittest
from dataclasses import replace

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import SECONDARY, Command
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes
from ow174.game.lucio import HERO as LUCIO
from ow174.game.zenyatta import HERO


class SoundwaveProbe(unittest.TestCase):
    def arena(
        self, *, side=0, distance=3, friendly=False, free_for_all=False, ally_hero=None, ally_offset=(0, 0)
    ):
        names = ("Lucio", "Zen") if ally_hero is None else ("Lucio", "Zen", "Ally")
        game_map = (
            replace(PRACTICE_RANGE, free_for_all=True, team_sizes=(8,)) if free_for_all else PRACTICE_RANGE
        )
        run = Run(game_map, names)
        lucio, zen = run.players[:2]
        run.match.bots.clear()
        run.match.switch_hero(lucio, heroes()[LUCIO])
        run.match.switch_hero(zen, heroes()[HERO])
        x, y, z = lucio.mover.position
        zen.mover.position = [x + side, y, z + distance]
        zen.team = lucio.team if friendly else 1
        zen.mover.body = None
        if ally_hero is not None:
            ally = run.players[2]
            run.match.switch_hero(ally, ally_hero)
            ally.team = zen.team
            ally.mover.position = [x + ally_offset[0], y, z + ally_offset[1]]
            ally.mover.body = None
        for _ in range(20):
            run.step({})
        return run, lucio, zen

    def test_projected_barrier_and_cooldown_gate_soundwave(self):
        run, lucio, zen = self.arena(ally_hero=heroes()[ZARYA], ally_offset=(4, 3))
        zarya = run.players[2]
        fight = run.match.combat
        x, y, z = zen.mover.position
        projection = aim(fight._eye(zarya), (x, y + 1.5, z))
        wave = aim(fight._eye(lucio), (x, y + 1, z))
        run.step({zarya: (*projection, 16)})
        shield = fight.barriers.bubbles[zarya.body, PROJECTED]
        weapon = next(i for i in lucio.body_script.component.instances.values() if i.graph.index == 0x0776)
        run.step({lucio: (*wave, SECONDARY)})
        state = weapon.node_state(weapon.graph.node(30))
        first = state.counter
        self.assertGreater(first, 0)
        vitals = fight.vitals[zen.body]
        for _ in range(20):
            run.step({})
        self.assertEqual((vitals.total, shield.hp, zen.mover.position[2]), (200, 200, z))
        run.step({lucio: (*wave, SECONDARY)})
        run.step({})
        self.assertEqual(state.counter, first)
        for _ in range(300):
            run.step({})
        self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
        run.step({lucio: (*wave, SECONDARY)})
        for _ in range(20):
            run.step({})
        self.assertEqual(state.counter, first + 1)
        self.assertEqual(vitals.total, 175)
        self.assertGreater(zen.mover.position[2], z + 0.5)

    def test_free_for_all_soundwave_hits_same_team_number(self):
        run, lucio, zen = self.arena(friendly=True, free_for_all=True)
        self.assertEqual(lucio.team, zen.team)
        fight = run.match.combat
        x, y, z = zen.mover.position
        wave = aim(fight._eye(lucio), (x, y + 1, z))
        run.step({lucio: (*wave, SECONDARY)})
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[zen.body].total, 175)
        self.assertGreater(zen.mover.position[2], z + 0.5)

    def test_soundwave_resolves_damage_immunity_separately_for_two_targets(self):
        run, lucio, zen = self.arena(ally_hero=heroes()[HERO], ally_offset=(1, 3))
        other = run.players[2]
        fight = run.match.combat
        x, y, z = zen.mover.position
        other.body_script.set_ult_charge(other.body_script.component.entity_var(338).value())
        run.step({other: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(other))
        wave = aim(fight._eye(lucio), (x, y + 1, z))
        run.step({lucio: (*wave, SECONDARY)})
        self.assertEqual(fight.vitals[zen.body].total, 175)
        self.assertEqual(fight.vitals[other.body].total, 200)
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertGreater(zen.mover.position[2], z + 0.5)
        self.assertGreater(other.mover.position[2], z + 0.5)
        self.assertTrue(fight.zenyatta.ultimate(other))

    def test_soundwave_displaces_active_transcendence_out_of_ally_range(self):
        run, lucio, zen = self.arena(ally_hero=heroes()[SOLDIER], ally_offset=(0, -6.7))
        ally = run.players[2]
        fight = run.match.combat
        ally_vitals = fight.vitals[ally.body]
        ally_vitals.damage(150)
        before = ally_vitals.total
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        run.step({})
        self.assertAlmostEqual(ally_vitals.total, before + 4.8, places=4)
        x, y, z = zen.mover.position
        wave = aim(fight._eye(lucio), (x, y + 1, z))
        run.step({lucio: (*wave, SECONDARY)})
        for _ in range(12):
            run.step({})
        self.assertGreater(zen.mover.position[2] - ally.mover.position[2], 10)
        still = ally_vitals.total
        for _ in range(10):
            run.step({})
            self.assertTrue(fight.zenyatta.ultimate(zen))
            self.assertEqual(fight.vitals[zen.body].total, 200)
            self.assertEqual(ally_vitals.total, still)

        # Walk the ally back into range while the same ultimate remains active.
        charge = zen.body_script.component.entity_var(342)
        charge_before = charge.value()
        for _ in range(75):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
            ally.take_commands([Command(run.frame, forward=127)])
            self.assertTrue(fight.zenyatta.ultimate(zen))
            if ally_vitals.total > still:
                break
        else:
            self.fail("Walking back into range did not resume healing")
        self.assertLess(zen.mover.position[2] - ally.mover.position[2], 10)
        self.assertAlmostEqual(ally_vitals.total, still + 4.8, places=4)
        self.assertEqual(charge.value(), charge_before)

    def test_charged_volley_can_release_after_soundwave(self):
        run, lucio, zen = self.arena()
        fight = run.match.combat
        x, y, z = lucio.mover.position
        angles = aim(fight._eye(zen), (x, y + 1, z))
        for _ in range(130):
            run.step({zen: (*angles, SECONDARY)})
        wave = aim(fight._eye(lucio), (x, y + 1, z + 3))
        run.step({lucio: (*wave, SECONDARY), zen: (*angles, SECONDARY)})
        for _ in range(8):
            run.step({zen: (*angles, SECONDARY)})
        self.assertEqual(fight.vitals[zen.body].total, 175)
        self.assertGreater(zen.mover.position[2], z + 3.25)
        self.assertFalse(fight.is_stunned(zen))
        angles = aim(fight._eye(zen), (x, y + 1, z))
        for _ in range(45):
            run.step({zen: (*angles, 0)})
        self.assertTrue(fight.is_dead(lucio))
        self.assertEqual(fight.vitals[lucio.body].total, 0)

    def test_soundwave_changes_melee_reach_only_before_its_impact(self):
        for mode in ("before", "after", "projection"):
            with self.subTest(mode=mode):
                projection = mode == "projection"
                run, lucio, zen = self.arena(
                    distance=2.85, ally_hero=heroes()[ZARYA] if projection else None, ally_offset=(4, 2.85),
                )
                fight = run.match.combat
                x, y, z = zen.mover.position
                lx, ly, lz = lucio.mover.position
                wave = aim(fight._eye(lucio), (x, y+1, z))
                melee = aim(fight._eye(zen), (lx, ly+1, lz))
                if projection:
                    zarya = run.players[2]
                    angle = aim(fight._eye(zarya), fight._eye(zen))
                    run.step({zarya: (*angle, 16)})
                    bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                    self.assertEqual((bubble.target, bubble.hp), (zen.body, 200))
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0043)
                commands = {zen: (*melee, 0x800)}
                if mode != "after":
                    commands[lucio] = (*wave, SECONDARY)
                charge = zen.body_script.component.entity_var(342)
                charge_before, frame_before = charge.value(), run.frame
                run.step(commands)
                self.assertEqual(weapon.states[4].counter, 1)
                health = fight.vitals[lucio.body]
                self.assertEqual(health.total, 200 if mode == "before" else 170)
                hits = zen.client.messages(combat.HIT)
                self.assertEqual(len(hits), 0 if mode == "before" else 1)
                if hits:
                    self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], lucio.body)
                if mode == "before":
                    self.assertGreater(zen.mover.position[2], z)
                if mode == "after":
                    self.assertEqual(zen.mover.position[2], z)
                    run.step({lucio: (*wave, SECONDARY)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(health.total, 200 if mode == "before" else 170)
                self.assertEqual(fight.vitals[zen.body].total, 200 if projection else 175)
                self.assertFalse(fight.is_stunned(zen))
                expected_charge = (0 if mode == "before" else 30)+(run.frame-frame_before)*.08
                self.assertAlmostEqual(charge.value()-charge_before, expected_charge, places=3)
                if projection:
                    self.assertEqual((zen.mover.position[2], bubble.hp), (z, 200))
                else:
                    self.assertGreater(zen.mover.position[2], z+0.5)

    def test_damage_and_displacement_from_real_input(self):
        for case in ("normal", "ultimate", "wall", "range", "ally", "side", "half impulse", "zero impulse"):
            with self.subTest(case=case):
                ultimate = case == "ultimate"
                distance = 8 if case == "range" else 3
                run, lucio, zen = self.arena(
                    side=4 if case == "side" else 0, distance=distance, friendly=case == "ally"
                )
                x, y, z = lucio.mover.position
                fight = run.match.combat
                if ultimate:
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                vitals = fight.vitals[zen.body]
                before = vitals.total
                if case == "wall":
                    run.match.collision_world = World(
                        [
                            (x - 4, y - 4, z + 1, x + 4, y - 4, z + 1, x + 4, y + 5, z + 1),
                            (x - 4, y - 4, z + 1, x + 4, y + 5, z + 1, x - 4, y + 5, z + 1),
                        ]
                    )
                angles = aim(fight._eye(lucio), (x, y + 1, z + 3))
                if case in ("half impulse", "zero impulse"):
                    lucio.body_script.component.set(0x2977, 0.5 if case == "half impulse" else 0.0)
                run.step({lucio: (*angles, SECONDARY)})
                for _ in range(20):
                    run.step({})
                displaced = zen.mover.position[2] - z - distance
                damaged = case in ("normal", "half impulse", "zero impulse")
                self.assertEqual(before - vitals.total, 25 if damaged else 0)
                if case in ("normal", "ultimate"):
                    self.assertGreater(displaced, 0.5)
                elif case == "half impulse":
                    self.assertGreater(displaced, 0)
                    self.assertLess(displaced, 0.5)
                else:
                    self.assertAlmostEqual(displaced, 0, places=4)
                self.assertEqual(fight.zenyatta.ultimate(zen), ultimate)
