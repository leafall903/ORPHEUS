import os
import unittest
from dataclasses import replace

import test_mei_orisa_barrier as orisa
import test_mei_photon_barrier as photon
import test_mei_reinhardt_barrier as reinhardt
import test_mei_sigma as sigma
import test_mei_winston_barrier as winston
from test_game_combat import aim, in_world

from ow174.game import combat, mei
from ow174.game.content import SOLDIER, heroes


def deployed(name):
    held = {}
    if name == "photon":
        case = photon.MeiPhotonBarrierTests()
        _, run, target, defender, fight = case.scene()
        actor = case.cast(run, defender, fight)
    elif name == "orisa":
        _, run, target, defender, _, actor = orisa.MeiOrisaBarrierTests().scene()
    elif name == "winston":
        _, run, target, defender, _, actor, _ = winston.MeiWinstonBarrierTests().scene()
    elif name == "reinhardt":
        run, target, defender, _, actor = reinhardt.MeiReinhardtBarrierTests().scene()
        held = {defender: (-32768, 0, 2)}
    else:
        _, run, target, defender, _, _, actor = sigma.MeiSigmaBarrierTests().arena()
    return run, target, defender, actor, held


def durability(actor, name):
    if name == "reinhardt":
        return actor.current
    if name == "sigma":
        return actor.vitals.total
    return actor.health


@unittest.skipUnless(
    all(os.environ.get(key) for key in (
        "ORPHEUS_SYMMETRA_COLLISION",
        "ORPHEUS_ORISA_COLLISION",
        "ORPHEUS_REINHARDT_COLLISION",
        "ORPHEUS_SIGMA_BARRIER_MODEL",
    )) and photon.photon_barrier.body_definition(),
    "requires verified deployed barrier descriptors and Photon body",
)
class MeiMeleeBarrierTests(unittest.TestCase):
    def test_actual_melee_checks_each_deployed_shield_and_rear_body_independently(self):
        """Exercise reconstructed melee targeting with authentic graph admissions."""
        for name in ("photon", "orisa", "winston", "reinhardt", "sigma"):
            for friendly in (False, True):
                with self.subTest(name=name, friendly=friendly):
                    run, target, defender, actor, held = deployed(name)
                    fight = run.match.combat
                    barriers = getattr(fight, name + "_barriers")
                    origin = fight._eye(target)
                    initial = barriers.trace(target, origin, (0.0, 0.0, 1.0), 10.0)
                    self.assertIsNotNone(initial)
                    x, y, z = target.mover.position
                    plane = z + initial[0]
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    attacker_team = defender.team if friendly else 1 - defender.team
                    target.team = 1 - attacker_team
                    attacker = run.match.add_player(3, "Mei melee", SOLDIER, attacker_team, False)
                    in_world(run.match, attacker)
                    run.players.append(attacker)
                    self.assertTrue(run.match.switch_hero(attacker, heroes()[mei.HERO]))
                    fight.walls.place_player(target, (x, y, plane + 0.8))
                    fight.walls.place_player(attacker, (x, y, plane - 1.2))
                    for _ in range(12):
                        run.step(held)

                    eye = fight._eye(attacker)
                    direction = aim(eye, (x, y + 1.2, plane + 0.8))
                    contact = barriers.trace(attacker, eye, combat.aim(*direction), 3.0)
                    self.assertEqual(contact is None, friendly)
                    before = durability(actor, name)
                    target_body = next(body for body in fight._targets() if body.owner is target)
                    vitals = fight.vitals_of(target_body)
                    target_before = vitals.total

                    strike = held | {attacker: (*direction, 0x800)}
                    for _ in range(6):
                        run.step(strike)
                    self.assertEqual(vitals.total, target_before - 30.0)
                    self.assertEqual(durability(actor, name), before - (0.0 if friendly else 30.0))

