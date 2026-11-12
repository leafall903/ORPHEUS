"""Same-impact cover remains solid for the original Pharah force consumer."""
import unittest
import test_mei_wall as walls
import test_zarya_native_cleanse as zarya
from test_game_combat import aim
from ow174.game.content import heroes

PHARAH = 0x02E0000000000008


class ImpactCoverTests(unittest.TestCase):
    def test_actual_broken_wall_still_blocks_same_rocket_impulse(self):
        fixture = walls.WallTests()
        run, mei, shooter, _ = fixture.scene()
        fight = run.match.combat
        pieces, _ = fixture.place(run, mei, distance=2)
        center = pieces[2]
        center.health = 20
        self.assertTrue(run.match.switch_hero(shooter, heroes()[PHARAH]))
        shooter.mover.position[:] = [center.feet[0], center.feet[1], center.feet[2] + 5]
        shooter.mover.body = None
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(shooter), (center.feet[0], center.feet[1] + 1, center.feet[2]))
        before = tuple(mei.mover.position)
        run.step({shooter: (*angles, 1)})
        for _ in range(60):
            run.step({})
            if center.entity not in fight.walls.pieces:
                break
        self.assertNotIn(center.entity, fight.walls.pieces)
        self.assertEqual(tuple(mei.mover.position), before)
        self.assertEqual(mei.mover.velocity, (0, 0, 0))

    def test_actual_broken_particle_pet_blocks_same_rocket_impulse(self):
        run, fight, caster, victim = zarya.NativePetApplicationTests().encounter(True)
        run.step({caster: (*aim(fight._eye(caster), fight._eye(victim)), 16)})
        pet = fight.barriers.authored_for(caster.body)
        self.assertIsNotNone(pet)
        pet.vitals.now[0] = 20
        shooter = run.players[0]
        self.assertTrue(run.match.switch_hero(shooter, heroes()[PHARAH]))
        shooter.mover.position[:] = [0, 1, 0]
        shooter.mover.body = None
        for _ in range(30):
            run.step({})
        before = tuple(victim.mover.position)
        angles = aim(fight._eye(shooter), fight._eye(victim))
        run.step({shooter: (*angles, 1)})
        for _ in range(60):
            run.step({})
            if pet.entity not in fight.barriers.scripted:
                break
        self.assertNotIn(pet.entity, fight.barriers.scripted)
        self.assertEqual(tuple(victim.mover.position), before)
        self.assertEqual(victim.mover.velocity, (0, 0, 0))
