"""Flight must compute its authored launch direction from the actual strafe input."""
import unittest

import test_echo_movement as movement_tests


class EchoFlightDirectionTests(unittest.TestCase):
    def test_strafe_takeoff_keeps_the_authored_direction_and_twenty_eight_impulse(self):
        fixture = movement_tests.EchoMovementTests()
        run, player = fixture.session()
        fixture.step(run, player, 8, right=127)
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == 0x1BB6)
        direction = instance.vars[27915].value()
        self.assertAlmostEqual(direction.x, -1)
        self.assertAlmostEqual(direction.y, 0)
        self.assertAlmostEqual(direction.z, 0)
        self.assertTrue(any(mod.value == (-28.0, 0.0, 0.0) for mod in player.mover.mods.values()))


if __name__ == '__main__':
    unittest.main()
