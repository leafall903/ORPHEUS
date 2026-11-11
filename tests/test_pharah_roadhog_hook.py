"""Authored Roadhog Chain Hook input against Pharah in the match loop."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_defenses as defense

from ow174.game.collision import World
from ow174.game.content import heroes


class PharahHookTests(unittest.TestCase):
    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, hog, other = fixture.encounter("Roadhog")
        other.mover.position[0] = 30
        other.mover.body = None
        return fixture, run, pharah, hog

    def pitch(self, run, hog, pharah):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is pharah)
        origin = fight._eye(hog)
        y = origin[1] - (target.feet[1] + target.height / 2)
        z = abs(origin[2] - target.feet[2])
        return round(math.atan2(y, z) * 65536 / math.tau)

    def cast(self, run, hog, pharah):
        run.step({hog: (-32768, self.pitch(run, hog, pharah), 8)})

    def catch(self, run, pharah, hog, inputs=0):
        hooks = run.match.combat.hooks
        for _ in range(55):
            if hooks.controlled(pharah.body):
                return
            run.step({hog: (-32768, self.pitch(run, hog, pharah), 0), pharah: (-32768, 0, inputs)})
        self.fail("Chain Hook never caught Pharah")

    def test_hook_stops_barrage_pulls_and_releases_pharah(self):
        fixture, run, pharah, hog = self.arena()
        fight = run.match.combat
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (-32768, 0, 32)})
        self.assertTrue(script.ultimate_running())
        self.cast(run, hog, pharah)
        self.catch(run, pharah, hog)
        self.assertEqual(fixture.health(run, pharah).total, 170)
        self.assertFalse(script.ultimate_running())
        hog_health = fixture.health(run, hog)
        before = hog_health.total
        start = math.dist(hog.mover.position, pharah.mover.position)
        for _ in range(10):
            run.step({pharah: (0, 0, 1 | 8)})
        self.assertEqual(hog_health.total, before)
        self.assertLess(math.dist(hog.mover.position, pharah.mover.position), start)
        for _ in range(70):
            run.step({})
        self.assertFalse(fight.hooks.controlled(pharah.body))
        self.assertAlmostEqual(math.dist(hog.mover.position, pharah.mover.position), 3, places=2)
        fixture.fire(run, pharah)
        self.assertLess(hog_health.total, before)

    def test_wall_range_and_team_prevent_hook_damage_and_control(self):
        for reason in ("wall", "range", "team"):
            with self.subTest(reason=reason):
                fixture, run, pharah, hog = self.arena()
                fight = run.match.combat
                y = hog.mover.position[1]
                wall = World([(-5, y, 4, 5, y + 5, 4, 5, y, 4),
                              (-5, y, 4, -5, y + 5, 4, 5, y + 5, 4)])
                if reason == "range":
                    pharah.mover.position[2] = -15
                    pharah.mover.body = None
                elif reason == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    pharah.team = hog.team
                before = tuple(pharah.mover.position)
                with patch.object(fight, "_collision", return_value=wall if reason == "wall"
                                  else fight._collision()):
                    self.cast(run, hog, pharah)
                    launched = False
                    for _ in range(80):
                        run.step({hog: (-32768, self.pitch(run, hog, pharah), 0)})
                        launched |= bool(fight.hooks.flights)
                        self.assertFalse(fight.hooks.controlled(pharah.body))
                self.assertTrue(launched)
                self.assertFalse(fight.hooks.flights)
                self.assertEqual(fixture.health(run, pharah).total, 200)
                self.assertEqual(tuple(pharah.mover.position), before)

    def test_pharah_rocket_kills_hog_and_releases_live_hook(self):
        fixture, run, pharah, hog = self.arena()
        fight = run.match.combat
        hog_health = fixture.health(run, hog)
        hog_health.damage(hog_health.total - 1)
        self.cast(run, hog, pharah)
        for _ in range(55):
            flight = fight.hooks.flights.get(hog.body)
            if flight is not None and flight.travelled > 5:
                break
            run.step({hog: (-32768, self.pitch(run, hog, pharah), 0)})
        else:
            self.fail("Chain Hook never reached the rocket race")
        run.step({hog: (-32768, self.pitch(run, hog, pharah), 0), pharah: (0, 0, 1)})
        self.assertTrue(any(rocket.shooter is pharah for rocket in fight.rockets))
        self.catch(run, pharah, hog)
        self.assertTrue(fight.hooks.controlled(pharah.body))
        for _ in range(15):
            run.step({})
        self.assertTrue(hog_health.dead)
        self.assertFalse(fight.hooks.controlled(pharah.body))

    def test_hook_interrupts_hover_and_locks_jet_until_release(self):
        fixture, run, pharah, hog = self.arena()
        fight = run.match.combat
        hover = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x25)
        jet = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x27)
        for _ in range(4):
            run.step({pharah: (0, 0, 2)})
        self.assertTrue(any(s.active and s.node.cls == "STU_A9C4FF83" for s in hover.states.values()))
        self.cast(run, hog, pharah)
        self.catch(run, pharah, hog, inputs=2)
        self.assertEqual(fixture.health(run, pharah).total, 170)
        self.assertFalse(any(s.active and s.node.cls == "STU_A9C4FF83" for s in hover.states.values()))
        for _ in range(8):
            run.step({pharah: (0, 0, 2 | 8)})
            self.assertTrue(fight.hooks.controlled(pharah.body))
            self.assertEqual(jet.state(0).counter, 0)
        for _ in range(80):
            run.step({})
        self.assertFalse(fight.hooks.controlled(pharah.body))
        run.step({pharah: (0, 0, 8)})
        self.assertEqual(pharah.mover.velocity[1], 20)

    def test_actual_winston_barrier_intercepts_the_chain(self):
        fixture, run, pharah, hog = self.arena()
        winston = run.players[2]
        self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = winston.team = 0
        hog.team = 1
        winston.mover.position = [1, pharah.mover.position[1], 0]
        winston.mover.body = None
        fight = run.match.combat
        run.step({winston: (0, 0, 16)})
        for _ in range(5):
            run.step({})
        bubble = fight.winston_bubbles[winston.body]
        self.assertEqual(bubble.health, 700)
        self.cast(run, hog, pharah)
        for _ in range(55):
            run.step({hog: (-32768, self.pitch(run, hog, pharah), 0)})
            self.assertFalse(fight.hooks.controlled(pharah.body))
        self.assertEqual(bubble.health, 670)
        self.assertEqual(fixture.health(run, pharah).total, 200)

    def test_hook_cooldown_blocks_repress_until_the_authored_eight_seconds(self):
        fixture, run, pharah, hog = self.arena()
        self.cast(run, hog, pharah)
        self.catch(run, pharah, hog)
        fight = run.match.combat
        vitals = fixture.health(run, pharah)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C8)
        ability = next(s for s in instance.states.values() if s.node.pos == 11)
        self.assertEqual(instance.find_var(0, 61).base, 8.0)
        for _ in range(90):
            run.step({})
        self.assertGreater(ability.remaining(), 0)
        self.cast(run, hog, pharah)
        for _ in range(55):
            run.step({hog: (-32768, self.pitch(run, hog, pharah), 0)})
            self.assertFalse(fight.hooks.flights)
        self.assertEqual(vitals.total, 170)
        for _ in range(500):
            run.step({})
            if ability.remaining() <= 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.cast(run, hog, pharah)
        self.catch(run, pharah, hog)
        self.assertEqual(vitals.total, 140)

    def test_new_wall_breaks_a_live_link_without_second_damage(self):
        fixture, run, pharah, hog = self.arena()
        fight = run.match.combat
        self.cast(run, hog, pharah)
        self.catch(run, pharah, hog)
        start = tuple(pharah.mover.position)
        y = hog.mover.position[1]
        wall = World([(-5, y, 4, 5, y + 5, 4, 5, y, 4),
                      (-5, y, 4, -5, y + 5, 4, 5, y + 5, 4)])
        with patch.object(fight, "_collision", return_value=wall):
            run.step({})
        self.assertFalse(fight.hooks.controlled(pharah.body))
        self.assertEqual(tuple(pharah.mover.position), start)
        self.assertEqual(fixture.health(run, pharah).total, 170)
