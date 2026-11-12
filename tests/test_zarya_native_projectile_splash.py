"""Original splash continuation after a real projectile hits a native particle Pet."""
import unittest
from contextlib import nullcontext
from itertools import product
from unittest.mock import patch
import test_zarya_native_cleanse as fixtures
from test_game_combat import aim, in_world
from ow174.game import combat
from ow174.game.content import SOLDIER, heroes

PHARAH = 0x02E0000000000008

class ZaryaNativeSplashTests(unittest.TestCase):
    def test_actual_pharah_native_contact_splashes_exposed_neighbour_once(self):
        for geometry, projected, capacity in product(("sphere", "native_capsule"), (False, True), (200, 20)):
            with self.subTest(geometry=geometry, projected=projected, capacity=capacity):
                run, fight, caster, recipient = fixtures.NativePetApplicationTests().encounter(projected)
                shooter = run.players[0]
                self.assertTrue(run.match.switch_hero(shooter, heroes()[PHARAH]))
                shooter.mover.position[:] = [0, 1, 0]
                shooter.mover.body = None
                if projected:
                    neighbour = run.match.add_player(8, "Exposed", SOLDIER, 3, False)
                    in_world(run.match, neighbour)
                    self.assertTrue(run.match.switch_hero(neighbour, heroes()[SOLDIER]))
                    run.players.append(neighbour)
                else:
                    neighbour = run.players[2]
                neighbour.team = recipient.team
                neighbour.mover.position[:] = [1.5, 1, 5]
                neighbour.mover.body = None
                for _ in range(30):
                    run.step({})
                direction = aim(fight._eye(caster), fight._eye(recipient))
                run.step({caster: (*direction, 16 if projected else 8)})
                pet = fight.barriers.authored_for(caster.body)
                self.assertIsNotNone(pet)
                pet.vitals.damage(200 - capacity)
                body = next(t for t in fight._targets() if t.owner is recipient)
                exposed = next(t for t in fight._targets() if t.owner is neighbour)
                before, nearby = fight.vitals_of(body).total, fight.vitals_of(exposed).total
                pose = tuple(recipient.mover.position)
                direction = aim(fight._eye(shooter), fight._eye(recipient))
                # Isolate the actual authored capsule as well as the default
                # sphere alias; both still damage the same real Pet/Vitals.
                geometry_query = (patch.object(fight.barriers, "intercept", return_value=None)
                                  if geometry == "native_capsule" else nullcontext())
                with geometry_query, patch.object(fight, "_explode", wraps=fight._explode) as explode:
                    run.step({shooter: (*direction, 1)})
                    rocket, = fight.rockets
                    self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
                    for _ in range(60):
                        run.step({})
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertLess(fight.vitals_of(exposed).total, nearby)
                    self.assertEqual(explode.call_count, 1)
                self.assertEqual(pet.vitals.total, max(0, capacity - 120))
                self.assertEqual(fight.vitals_of(body).total, before)
                self.assertEqual(tuple(recipient.mover.position), pose)
                self.assertEqual(recipient.mover.velocity, (0, 0, 0))
