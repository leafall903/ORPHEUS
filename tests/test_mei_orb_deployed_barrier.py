import os
import unittest
from dataclasses import replace

import test_mei_orisa_barrier as orisa_fixtures
import test_mei_photon_barrier as photon_fixtures
import test_mei_reinhardt_barrier as rein_fixtures
import test_mei_shields as shield_fixtures
import test_mei_sigma as sigma_fixtures
import test_mei_wall as wall_fixtures
import test_mei_winston_barrier as winston_fixtures
from test_game_combat import aim, in_world

from ow174.game import combat, zarya_shields
from ow174.game.content import SOLDIER, heroes


def scene(*, friendly=False):
    test = photon_fixtures.MeiPhotonBarrierTests()
    _, run, mei, owner, fight = test.scene()
    actor = test.cast(run, owner, fight)
    x, y, z = mei.mover.position
    distance, _ = fight.photon_barriers.trace(mei, fight._eye(mei), (0.0, 0.0, 1.0), 8.0)
    run.match.game_map = replace(run.match.game_map, free_for_all=False)
    mei.team = 1 - owner.team if friendly else owner.team
    sym = run.match.add_player(
        3, "Enemy Symmetra", SOLDIER, owner.team if friendly else 1 - owner.team, False
    )
    in_world(run.match, sym)
    run.players.append(sym)
    assert run.match.switch_hero(sym, heroes()[0x02E0000000000016])
    fight.walls.place_player(mei, (x, y, z + distance + 1.0))
    fight.walls.place_player(sym, (x, y, z + distance - 4.0))
    for _ in range(15):
        run.step({})
    direction = aim(fight._eye(sym), (x, y + 1.2, z + distance + 1.0))
    contact = fight.photon_barriers.trace(sym, fight._eye(sym), combat.aim(*direction), 6.0)
    assert (contact is None) == friendly
    return run, mei, sym, fight, actor, direction


def fire(run, sym, direction):
    for _ in range(90):
        run.step({sym: (*direction, 2)})
    for _ in range(40):
        run.step({sym: (*direction, 0)})


def add_front(run, fight, mei, x_offset):
    player = run.match.add_player(4, "Approach side", SOLDIER, mei.team, False)
    in_world(run.match, player)
    run.players.append(player)
    assert run.match.switch_hero(player, heroes()[SOLDIER])
    x, y, z = mei.mover.position
    fight.walls.place_player(player, (x + x_offset, y, z - 1.65))
    for _ in range(10):
        run.step({})
    return player


def vitals(fight, player):
    return fight.vitals_of(next(t for t in fight._targets() if t.owner is player))


@unittest.skipUnless(os.environ.get("ORPHEUS_SYMMETRA_COLLISION"), "requires verified Photon mesh")
class MeiOrbDeployedBarrierTests(unittest.TestCase):
    def test_enemy_photon_intercepts_direct_orb_and_friendly_photon_passes(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, mei, sym, fight, actor, direction = scene(friendly=friendly)
                fire(run, sym, direction)
                self.assertEqual(vitals(fight, mei).total, 130.0 if friendly else 250.0)
                self.assertEqual(actor.health, 4000.0 if friendly else 3940.0)
                self.assertFalse(fight.rockets)

    def test_breaking_orb_has_approach_side_splash_and_no_rear_spill(self):
        run, mei, sym, fight, actor, direction = scene()
        front = add_front(run, fight, mei, 0.95)
        actor.health = 5.0
        fire(run, sym, direction)
        self.assertEqual(actor.health, 0.0)
        self.assertFalse(fight.photon_barriers.actors)
        self.assertEqual(vitals(fight, mei).total, 250.0)
        self.assertLess(vitals(fight, front).total, 200.0)
        x, y, z = front.mover.position
        fight.walls.place_player(front, (x + 20.0, y, z))
        fire(run, sym, direction)
        self.assertEqual(vitals(fight, mei).total, 130.0)

    def test_photon_blocks_splash_from_body_impact_on_approach_side(self):
        run, mei, sym, fight, actor, direction = scene()
        front = add_front(run, fight, mei, 0.0)
        fire(run, sym, direction)
        self.assertEqual(vitals(fight, front).total, 80.0)
        self.assertEqual(vitals(fight, mei).total, 250.0)
        self.assertEqual(actor.health, 4000.0)

    @unittest.skipUnless(
        all(
            os.environ.get(key)
            for key in (
                "ORPHEUS_ORISA_COLLISION",
                "ORPHEUS_REINHARDT_COLLISION",
                "ORPHEUS_SIGMA_BARRIER_MODEL",
            )
        ),
        "requires verified Orisa, Reinhardt and Sigma descriptors",
    )
    def test_orb_hits_each_other_deployed_world_barrier_before_mei(self):
        for name in ("orisa", "winston", "reinhardt", "sigma"):
            with self.subTest(name=name):
                held = {}
                if name == "orisa":
                    _, run, mei, defender, _, actor = orisa_fixtures.MeiOrisaBarrierTests().scene()

                elif name == "winston":
                    _, run, mei, defender, _, actor, _ = winston_fixtures.MeiWinstonBarrierTests().scene()

                elif name == "reinhardt":
                    run, mei, defender, _, actor = rein_fixtures.MeiReinhardtBarrierTests().scene()
                    held = {defender: (-32768, 0, 2)}

                else:
                    _, run, mei, defender, _, _, actor = sigma_fixtures.MeiSigmaBarrierTests().arena()

                fight = run.match.combat
                barriers = getattr(fight, name + "_barriers")
                eye = fight._eye(mei)
                initial = barriers.trace(mei, eye, (0.0, 0.0, 1.0), 10.0)
                self.assertIsNotNone(initial)
                x, y, z = mei.mover.position
                plane = z + initial[0]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                mei.team = defender.team
                attacker = run.match.add_player(3, "Orb attacker", SOLDIER, 1 - defender.team, False)
                in_world(run.match, attacker)
                run.players.append(attacker)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000016]))
                fight.walls.place_player(mei, (x, y, plane + 0.8))
                fight.walls.place_player(attacker, (x, y, plane - 3.5))
                for _ in range(10):
                    run.step(held)
                direction = aim(fight._eye(attacker), (x, y + 1.2, plane + 0.8))
                self.assertIsNotNone(
                    barriers.trace(attacker, fight._eye(attacker), combat.aim(*direction), 6.0)
                )

                def health(name=name, actor=actor):
                    if name == "sigma":
                        return actor.vitals.total
                    return actor.current if name == "reinhardt" else actor.health

                before = health()
                for frame in range(130):
                    run.step({**held, attacker: (*direction, 2 if frame < 90 else 0)})
                self.assertEqual((vitals(fight, mei).total, health()), (250.0, before - 60.0))
                self.assertFalse(fight.rockets)

    def test_photon_preserves_rear_ice_wall_and_projected_shield(self):
        for name in ("wall", "zarya"):
            run, mei, attacker, fight, actor, _ = scene()
            x, y, z = mei.mover.position
            plane = z - 1.0
            if name == "wall":
                pieces, _ = wall_fixtures.WallTests().place(run, mei, distance=1.2)
                fight.walls.place_player(mei, (x, y, plane + 3.0))
            else:
                zarya = run.match.add_player(4, "Zarya", SOLDIER, mei.team, False)
                in_world(run.match, zarya)
                run.players.append(zarya)
                self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_shields.HERO]))
                fight.walls.place_player(mei, (x, y, plane + 1.8))
                fight.walls.place_player(zarya, (x + 3.0, y, plane + 2.8))
                for _ in range(12):
                    run.step({})
                pieces = [shield_fixtures.ShieldTests().cast(run, zarya, mei)]
            direction = aim(fight._eye(attacker), (x, y + 1.2, plane + 2.0))
            before = [p.health for p in pieces]
            fire(run, attacker, direction)
            after = [p.health for p in pieces]
            self.assertEqual(after, before)
            self.assertEqual((actor.health, vitals(fight, mei).total), (3940.0, 250.0))


if __name__ == "__main__":
    unittest.main(argv=[__file__], defaultTest="MeiOrbDeployedBarrierTests", verbosity=2)
