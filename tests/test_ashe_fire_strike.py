"""Build-104319 Fire Strike against Ashe and her independent companion."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_ashe_bob as bob_fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, sombra_hack
from ow174.game.content import HEROES_PATH, find_hero, heroes
from ow174.game.script import graph

ASHE = 0x02E0000000000200
REINHARDT = 0x02E0000000000007
FIRE_STRIKE = 16
EXTENSION = Path(os.environ.get("ORPHEUS_GRAPH_EXTENSION", "."))


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class FireStrikeTests(unittest.TestCase):
    def arena(self):
        run = Run(KANEZAKA_DM, ("Ashe", "Reinhardt", "Second target"))
        ashe, rein, other = run.players
        run.match.bots = []
        self.assertTrue(run.match.switch_hero(ashe, heroes()[ASHE]))
        self.assertTrue(run.match.switch_hero(rein, heroes()[REINHARDT]))
        ashe.mover.position[:] = (0, 0, 0)
        rein.mover.position[:] = (0, 0, 7)
        other.mover.position[:] = (0, 0, 3)
        for _ in range(100):
            run.step({})
        return run, ashe, rein, other

    def fire(self, run, rein, point, frames, *, press=False):
        fight = run.match.combat
        for index in range(frames):
            angle = aim(fight._eye(rein), point)
            run.step({rein: (*angle, FIRE_STRIKE if press and index == 0 else 0)})

    def test_windup_and_single_hit_per_body(self):
        run, ashe, rein, other = self.arena()
        fight = run.match.combat
        point = (0, fight._eye(rein)[1], 0)
        self.fire(run, rein, point, 22, press=True)
        self.assertFalse(fight.rockets)
        self.fire(run, rein, point, 12)
        strike, = fight.rockets
        self.assertEqual((strike.speed, strike.direct.amount), (25.0, 100.0))
        self.assertAlmostEqual(strike.strike_radius, 0.85)
        self.assertAlmostEqual(strike.flown + strike.left, 4.0)
        self.fire(run, rein, point, 60)
        self.assertEqual(fight.vitals[ashe.body].total, 100.0)
        self.assertEqual(fight.vitals[other.body].total, 100.0)
        self.assertEqual(len(rein.client.messages(combat.HIT)), 2)
        self.fire(run, rein, point, 65, press=True)
        self.assertEqual(len(rein.client.messages(combat.HIT)), 2)
        self.fire(run, rein, point, 325)
        self.fire(run, rein, point, 65, press=True)
        self.assertEqual(len(rein.client.messages(combat.HIT)), 4)

    def test_terrain_stops_the_swept_strike(self):
        run, ashe, rein, other = self.arena()
        fight = run.match.combat
        run.match.collision_world = wall_scene(5)
        self.fire(run, rein, (0, fight._eye(rein)[1], 0), 75, press=True)
        self.assertEqual(fight.vitals[ashe.body].total, 200.0)
        self.assertEqual(fight.vitals[other.body].total, 200.0)
        self.assertFalse(fight.rockets)

    @unittest.skipUnless(EXTENSION.is_file(), "requires explicit external B.O.B. graph extension")
    def test_live_companion_takes_fire_strike_damage(self):
        run, ashe, rein, actor = bob_fixtures.BobTests().cast()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(rein, heroes()[REINHARDT]))
        rein.mover.position[:] = (0, 1, 10)
        fight.vitals[rein.body] = combat.Vitals(rein.body, 10000)
        for _ in range(55):
            run.step({})
        x, y, z = actor.bot.position
        self.fire(run, rein, (x, y + 1.3, z), 80, press=True)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1100.0)
        self.assertIn(actor, fight.companions.active.values())
        self.assertEqual(fight.vitals[ashe.body].total, 100.0)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires build 104319 Barrier Field mesh",
    )
    def test_strike_pierces_raised_field_and_both_bodies(self):
        run, ashe, rein, defender = self.arena()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(defender, heroes()[REINHARDT]))
        defender.mover.position[:] = (0, 1, 3)
        defender.mover.body = None
        for _ in range(35):
            run.step({defender: (0, 0, 2)})
        shield = fight.reinhardt_barriers.states[defender.body]
        self.assertTrue(shield.active)
        for index in range(70):
            attack = aim(fight._eye(rein), fight._eye(ashe))
            run.step({rein: (*attack, FIRE_STRIKE if index == 0 else 0), defender: (0, 0, 2)})
        self.assertEqual(shield.current, 1500.0)
        self.assertEqual(fight.vitals[defender.body].total, 405.0)
        self.assertEqual(fight.vitals[ashe.body].total, 100.0)

    def test_live_projectile_guards(self):
        for name, button, trigger in (("genji", 16, 22), ("sigma", 8, 24), ("dva", 2, 22)):
            with self.subTest(guard=name):
                if name != "genji" and not EXTENSION.is_file():
                    self.skipTest("requires external guard graph extension")
                run, ashe, rein, defender = self.arena()
                fight = run.match.combat
                hero = find_hero(name)
                if name == "dva":
                    mech = 0x0400000000000407
                    record = graph.entity_body(mech)
                    if record is None or graph.graph(0x02CC) is None:
                        self.skipTest("requires explicit 104319 D.Va mech data")
                    with patch.object(graph, "bodies", return_value={**graph.bodies(), hero.body: record}):
                        self.assertTrue(run.match.switch_hero(defender, replace(hero, body=mech)))
                else:
                    self.assertTrue(run.match.switch_hero(defender, hero))
                defender.mover.position[:] = (0, 1, 3)
                defender.mover.body = None
                for _ in range(70):
                    run.step({})
                if name == "dva":
                    # This fixture begins in the mech without a native call-mech animation.
                    call_mech = next(i for i in defender.body_script.component.instances.values()
                                     if i.graph.index == 0x0276)
                    call_mech.states[30].end(False)
                for index in range(75):
                    attack = aim(fight._eye(rein), fight._eye(ashe))
                    defence = aim(fight._eye(defender), fight._eye(rein))
                    active = index >= trigger if name == "dva" else index == trigger
                    run.step({rein: (*attack, FIRE_STRIKE if index == 0 else 0),
                              defender: (*defence, button if active else 0)})
                self.assertEqual(fight.vitals[ashe.body].total, 200.0)
                self.assertEqual(rein.client.messages(combat.HIT), [])
                if name == "genji":
                    self.assertEqual(fight.vitals[rein.body].total, 405.0)
                    self.assertEqual(len(defender.client.messages(combat.HIT)), 1)
                else:
                    index = {"sigma": 0x1D02, "dva": 0x02CC}[name]
                    guard = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == index)
                    slot = 21312 if name == "sigma" else 6584
                    self.assertEqual(guard.find_var(0, slot).value(), 100.0)
                    self.assertFalse(fight.rockets)

    def sombra_arena(self):
        run, ashe, rein, sombra = self.arena()
        self.assertTrue(run.match.switch_hero(sombra, find_hero("sombra")))
        sombra.mover.position[:] = (4, 1, 7)
        sombra.mover.body = None
        for _ in range(55):
            run.step({})
        return run, ashe, rein, sombra

    def test_hack_blocks_new_fire_strike_until_receiver_expires(self):
        run, ashe, rein, sombra = self.sombra_arena()
        fight = run.match.combat
        for _ in range(65):
            facing = aim(fight._eye(sombra), fight._eye(rein))
            run.step({sombra: (*facing, 2)})
            if sombra_hack.locked(rein):
                break
        self.assertTrue(sombra_hack.locked(rein))
        point = fight._eye(ashe)
        self.fire(run, rein, point, 75, press=True)
        self.assertEqual(fight.vitals[ashe.body].total, 200.0)
        self.assertFalse(fight.rockets)
        for _ in range(320):
            if not sombra_hack.locked(rein):
                break
            run.step({})
        self.assertFalse(sombra_hack.locked(rein))
        self.fire(run, rein, point, 75, press=True)
        self.assertEqual(fight.vitals[ashe.body].total, 100.0)

    def test_emp_interrupts_live_fire_strike_windup(self):
        run, ashe, rein, sombra = self.sombra_arena()
        fight = run.match.combat
        sombra.body_script.set_ult_charge(1400)
        attack = aim(fight._eye(rein), fight._eye(ashe))
        run.step({rein: (*attack, FIRE_STRIKE), sombra: (0, 0, 32)})
        cast = sombra_hack.instance_for(rein, 0x00E2).states[9]
        self.assertTrue(cast.flags & cast.RUNNING)
        for _ in range(75):
            run.step({rein: (*attack, 0)})
            self.assertFalse(fight.rockets)
        self.assertTrue(sombra_hack.locked(rein))
        self.assertFalse(cast.flags & cast.RUNNING)
        self.assertEqual(fight.vitals[ashe.body].total, 200.0)

    def test_emp_preserves_already_released_fire_strike(self):
        run, ashe, rein, sombra = self.sombra_arena()
        fight = run.match.combat
        rein.mover.position[:] = (0, 1, 20)
        rein.mover.body = None
        sombra.mover.position[:] = (4, 1, 20)
        sombra.mover.body = None
        self.fire(run, rein, fight._eye(ashe), 34, press=True)
        strike, = fight.rockets
        sombra.body_script.set_ult_charge(1400)
        run.step({sombra: (0, 0, 32)})
        for _ in range(21):
            run.step({})
        self.assertTrue(sombra_hack.locked(rein))
        self.assertFalse(sombra_hack.locked(ashe))
        self.assertIn(strike, fight.rockets)
        for _ in range(50):
            run.step({})
        self.assertEqual(fight.vitals[ashe.body].total, 100.0)

    def test_lethal_viper_hit_cancels_windup_but_preserves_released_strike(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, ashe, rein, other = self.arena()
                fight = run.match.combat
                other.mover.position[0] = 12
                other.mover.body = None
                target = next(target for target in fight._targets() if target.entity == ashe.body)
                ashe_health = fight.vitals_of(target)
                shot = aim(fight._eye(ashe), fight._eye(rein))
                attack = aim(fight._eye(rein), fight._eye(ashe))
                for _ in range(40):
                    run.step({ashe: (*shot, 2)})
                for _ in range(3):
                    run.step({ashe: (*shot, 3)})
                    for _ in range(50):
                        run.step({ashe: (*shot, 2)})
                self.assertGreater(fight.vitals[rein.body].total, 0.0)
                self.assertLessEqual(fight.vitals[rein.body].total, 75.0)
                for index in range(34 if released else 12):
                    run.step({ashe: (*shot, 2), rein: (*attack, FIRE_STRIKE if index == 0 else 0)})
                self.assertEqual(bool(fight.rockets), released)
                run.step({ashe: (*shot, 3), rein: (*attack, 0)})
                self.assertTrue(fight.vitals[rein.body].dead)
                self.assertEqual(len(ashe.client.messages(combat.KILL_NOTICE)), 1)
                for _ in range(65):
                    run.step({})
                    if not released:
                        self.assertFalse(fight.rockets)
                self.assertEqual(ashe_health.total, 100.0 if released else 200.0)
                self.assertEqual(len(rein.client.messages(combat.HIT)), int(released))

    def test_live_fire_strike_range_boundary(self):
        for distance, health in ((99, 100.0), (102, 200.0)):
            with self.subTest(distance=distance):
                run, ashe, rein, other = self.arena()
                fight = run.match.combat
                rein.mover.position[:] = (0, 1, distance)
                rein.mover.body = None
                other.mover.position[0] = 12
                other.mover.body = None
                self.fire(run, rein, fight._eye(ashe), 320, press=True)
                self.assertEqual(fight.vitals[ashe.body].total, health)
                self.assertFalse(fight.rockets)
