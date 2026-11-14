"""A blocked ejection ends the real parked rows in their command-frame domain."""
import json
import unittest
from pathlib import Path

import test_dva_delivery_created_body as created
import test_game_combat as combat_tests
import test_game_mover as mover_tests

from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import find_hero
from ow174.game.mover import statescript_mods


class DvaReturnModifierClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    def check_clock(self, name):
        contract = json.loads((Path(__file__).resolve().parents[2] /
                               'fixtures/behavior/dva-return-modifier-clock-v1.json').read_text())
        owner_birth, world_birth = contract['input'][name]
        run = combat_tests.Run(combat_tests.KANEZAKA_DM, ('DVa', 'Enemy'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        pilot, enemy = run.players
        # Explicit prior-input epoch fixture, before the DVa VM is created.
        match.tick = world_birth
        pilot.commands.last_frame = owner_birth
        self.assertTrue(match.switch_hero(pilot, find_hero('dva')))
        mech, parked = pilot.controlled_mech, pilot.mover
        parked_position = tuple(parked.position)
        parked.track_mods(statescript_mods(pilot.body_script.component), owner_birth)
        locks = [mod for mod in parked.mods.values() if mod.index in (11, 23)]
        self.assertEqual(sorted(mod.index for mod in locks), [11, 23])

        def step(offset, *, forward=0, shoot=False):
            world = world_birth + offset
            match.update(world * .016, world)
            pilot.take_commands([Command(owner_birth + offset, forward=forward)])
            point = mech.position
            yaw, pitch = combat_tests.aim(match.combat._eye(enemy),
                                          (point[0], point[1] + 1., point[2]))
            enemy.take_commands([Command(world, yaw=yaw, pitch=pitch, buttons=FIRE if shoot else 0)])

        for offset in range(1, 66):
            step(offset, forward=127)
        x, y, z = mech.position
        self.assertGreater(z, parked_position[2] + 2.)
        enemy.mover.position[:] = (x, y, z - 3.)
        enemy.mover.body = None
        for offset in range(66, 566):
            step(offset, shoot=True)
            if mech.dead:
                break
        self.assertTrue(mech.dead, 'actual Soldier commands must destroy the normal600HP mech')
        self.assertIs(pilot.controlled_mech, mech)
        ceiling = mech.position[1] + .8
        x, _, z = mech.position
        roof = [(x - 1, ceiling, z - 1, x + 1, ceiling, z + 1, x + 1, ceiling, z - 1),
                (x - 1, ceiling, z - 1, x - 1, ceiling, z + 1, x + 1, ceiling, z + 1)]
        # Explicit collision input: the pilot capsule cannot occupy the mech's
        # ejection pose. Keep the existing safe parked pose through the handoff.
        match.collision_world = World(mover_tests.FLOOR + roof)
        match.deliver_body_messages()
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(pilot.mover, parked)
        self.assertEqual(tuple(parked.position), parked_position)
        returned_frame = pilot.body_script.frame
        self.assertNotEqual(returned_frame, match.tick)
        self.assertEqual([mod.end for mod in locks], [returned_frame + 1] * len(locks))
        self.assertTrue(all(mod.acts(returned_frame) for mod in locks))
        self.assertFalse(any(mod.acts(returned_frame + 1) for mod in locks))
        for more in range(1, 31):
            world = world_birth + offset + more
            match.update(world * .016, world)
            pilot.take_commands([Command(owner_birth + offset + more, forward=127)])
        self.assertGreater(parked.position[2], parked_position[2] + .5)

    def test_blocked_lethal_ejection_with_owner_clock_ahead(self):
        self.check_clock('owner_ahead')

    def test_blocked_lethal_ejection_with_owner_clock_behind(self):
        self.check_clock('owner_behind')


if __name__ == '__main__':
    unittest.main()
