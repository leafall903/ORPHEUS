import os
import unittest
from dataclasses import replace

import test_mei_orb_deployed_barrier as fixtures
from test_game_combat import aim, in_world

from ow174.game import combat
from ow174.game.content import SOLDIER, heroes

ROADHOG = 0x02E0000000000040


class MeiRoadhogBarriersTests(unittest.TestCase):
    def fire(self, run, hog, angle, button, held=None, stop=None):
        if button == 32:
            script = hog.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        for frame in range(120):
            run.step({**(held or {}), hog: (*angle, button if frame == 0 else 0)})
            if stop is not None and stop():
                break

    @unittest.skipUnless(os.environ.get("ORPHEUS_SYMMETRA_COLLISION"), "requires verified Photon mesh")
    def test_actual_photon_intercepts_enemy_volley_and_passes_friendly_volley(self):
        for friendly in (False, True):
            for button in (1, 2, 32):
                with self.subTest(friendly=friendly, button=button):
                    run, mei, hog, fight, barrier, _ = fixtures.scene(friendly=friendly)
                    position = tuple(hog.mover.position)
                    self.assertTrue(run.match.switch_hero(hog, heroes()[ROADHOG]))
                    fight.walls.place_player(hog, position)
                    for _ in range(24):
                        run.step({})
                    start = tuple(mei.mover.position)
                    direction = aim(fight._eye(hog), fight._eye(mei))
                    self.fire(run, hog, direction, button)
                    health = fixtures.vitals(fight, mei).total
                    if friendly:
                        self.assertLess(health, 250.0)
                        self.assertEqual(barrier.health, 4000.0)
                    else:
                        self.assertEqual(health, 250.0)
                        self.assertEqual(tuple(mei.mover.position), start)
                        self.assertLess(barrier.health, 4000.0)

    def defender(self, name):
        held = {}
        if name == "orisa":
            _, run, mei, defender, _, actor = fixtures.orisa_fixtures.MeiOrisaBarrierTests().scene()
        elif name == "winston":
            _, run, mei, defender, _, actor, _ = fixtures.winston_fixtures.MeiWinstonBarrierTests().scene()
        elif name == "reinhardt":
            run, mei, defender, _, actor = fixtures.rein_fixtures.MeiReinhardtBarrierTests().scene()
            held = {defender: (-32768, 0, 2)}
        else:
            _, run, mei, defender, _, _, actor = fixtures.sigma_fixtures.MeiSigmaBarrierTests().arena()
        return run, mei, defender, actor, held

    @unittest.skipUnless(
        all(os.environ.get(name) for name in (
            "ORPHEUS_ORISA_COLLISION", "ORPHEUS_REINHARDT_COLLISION", "ORPHEUS_SIGMA_BARRIER_MODEL",
        )),
        "requires verified deployed barrier descriptors",
    )
    def test_actual_other_deployed_barriers_take_damage_before_mei(self):
        for name in ("orisa", "winston", "reinhardt", "sigma"):
            for button in (1, 2, 32):
                with self.subTest(barrier=name, button=button):
                    self.check_other_barrier(name, button)

    def check_other_barrier(self, name, button):
        run, mei, defender, actor, held = self.defender(name)
        fight = run.match.combat
        manager = getattr(fight, name + "_barriers")
        contact = manager.trace(mei, fight._eye(mei), (0., 0., 1.), 10.)
        self.assertIsNotNone(contact)
        x, y, z = mei.mover.position
        plane = z + contact[0]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = defender.team
        hog = run.match.add_player(3, "Roadhog", SOLDIER, 1 - defender.team, False)
        in_world(run.match, hog)
        run.players.append(hog)
        self.assertTrue(run.match.switch_hero(hog, heroes()[ROADHOG]))
        fight.walls.place_player(mei, (x, y, plane + 0.8))
        fight.walls.place_player(hog, (x, y, plane - 3.5))
        for _ in range(10):
            run.step(held)
        direction = aim(fight._eye(hog), (x, y + 1.2, plane + 0.8))
        self.assertIsNotNone(manager.trace(hog, fight._eye(hog), combat.aim(*direction), 6.))

        def health():
            if name == "sigma":
                return actor.vitals.total
            return actor.current if name == "reinhardt" else actor.health

        start = tuple(mei.mover.position)
        before = health()
        self.fire(run, hog, direction, button, held, stop=lambda: health() < before)
        self.assertLess(health(), before)
        self.assertEqual(fixtures.vitals(fight, mei).total, 250.0)
        self.assertEqual(tuple(mei.mover.position), start)
