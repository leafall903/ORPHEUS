"""Original Hand Cannon terrain cover includes the active authored Cryo capsule."""

import unittest
from test_game_combat import Run, PRACTICE_RANGE, aim
from test_doomfist_combat import DoomfistFixture
from ow174.game import combat, defenses
from ow174.game.commands import FIRE
from ow174.game.content import heroes


class HandCannonCryoTests(unittest.TestCase):
    def test_actual_emitted_pellet_stops_on_allied_cryo_before_enemy(self):
        run = Run(PRACTICE_RANGE, ('Doom', 'Mei', 'Behind'))
        doom, mei, behind = run.players
        doom.team, mei.team, behind.team = 0, 0, 1
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(doom, heroes()[0x02E000000000012F])
        match.switch_hero(mei, heroes()[defenses.MEI])
        x, y, z = doom.mover.position
        DoomfistFixture.place_player(mei, (x + 4, y, z + 3))
        DoomfistFixture.place_player(behind, (x, y, z + 8))
        run.step({})
        run.step({mei: (0, 0, 8)})
        self.assertIsNotNone(defenses.cryo_capsule(mei))
        fight = match.combat
        look = aim(fight._eye(doom), (x, y + 1, z + 8))
        run.step({doom: (*look, FIRE)})
        self.assertEqual(len(fight.rockets), 11)
        shot = fight.rockets[0]
        origin, direction = shot.position, shot.direction
        distance = 3
        centre = combat._along(origin, direction, distance)
        DoomfistFixture.place_player(mei, (centre[0], y, centre[2]))
        point = combat._along(origin, direction, 7)
        DoomfistFixture.place_player(behind, (point[0], y, point[2]))
        self.assertNotIn(mei.body, {target.entity for target in fight._shot_targets(doom, shot.direct)})
        ca, cb, radius = defenses.cryo_capsule(mei)
        self.assertIsNotNone(combat.ray_capsule(origin, direction, ca, cb, radius))
        pool = fight.vitals_of(fight.player_target(behind))
        before = pool.total
        fight._sweep_rocket(shot, 10 / shot.speed, set())
        self.assertEqual(pool.total, before)
        self.assertLess(shot.position[2], point[2])
