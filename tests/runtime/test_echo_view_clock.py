"""Different body epochs must preserve viewed target identity through actual Echo input."""
import unittest
from types import SimpleNamespace

from test_game_combat import Run, aim

from ow174.game import combat, echo_clock
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, heroes

ECHO = 0x02E0000000000206


class EchoViewClockTests(unittest.TestCase):
    def test_fractional_world_history_cursor_is_not_rounded(self):
        script = SimpleNamespace(echo_birth=(1000, 9000), frame_ms=15)
        self.assertEqual(echo_clock.view_world_tick(script, Command(9041, extra=8), 15000), 1037.9375)

    def exercise(self, action, owner_birth):
        run = Run(PRACTICE_RANGE, ('Echo', 'ViewedA', 'CurrentB'))
        player, a, b = run.players
        count = len(run.match.bots)
        self.assertEqual(count, 15)
        player.commands.last_frame = owner_birth
        run.match.switch_hero(player, heroes()[ECHO])
        self.assertEqual(player.body_script.echo_birth, (1000, owner_birth))
        fight = run.match.combat
        if action == 'duplicate':
            player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        x, y, z = player.mover.position
        near = (x - .8, y, z + (2 if action == 'melee' else 8))
        far = (x + 5, y, z + (2 if action == 'melee' else 8))
        yaw, pitch = aim(fight._eye(player), (near[0], near[1] + 1.1, near[2]))
        for offset in range(1, 42):
            a.mover.position[:] = near if offset <= 33 else far
            b.mover.position[:] = far if offset <= 33 else near
            tick = 1000 + offset
            run.match.update(tick * .016, tick)
            if offset < 41:
                player.take_commands([Command(owner_birth + offset, yaw=(yaw + 32768) & 65535,
                                              buttons=16 if action == 'beam' and offset >= 20 else 0)])
        targets = {t.owner: t for t in fight._targets()}
        before = (fight.vitals_of(targets[a]).total, fight.vitals_of(targets[b]).total)
        frame = owner_birth + 41
        buttons = {'beam': 16, 'melee': 0x800, 'duplicate': 32}[action]
        player.take_commands([Command(frame, yaw=yaw, pitch=pitch,
                                      buttons=buttons, extra=128)])
        self.assertEqual(len(run.match.bots), count)
        if action == 'duplicate':
            self.assertEqual(fight.echo_duplicates.states[player.entity].target_body, a.body)
        else:
            after = (fight.vitals_of(targets[a]).total, fight.vitals_of(targets[b]).total)
            self.assertLess(after[0], before[0])
            self.assertEqual(after[1], before[1])
            hit = player.client.messages(combat.HIT)[-1]
            self.assertEqual(hit['+0x78']['+0x0']['+0x0'], a.body)
            self.assertEqual(hit['+0x84'], frame)

    def test_beam_uses_viewed_world_identity_and_owner_hit_frame(self):
        for epoch in (1000, 9000):
            with self.subTest(owner_birth=epoch):
                self.exercise('beam', epoch)

    def test_melee_uses_viewed_world_identity_and_owner_hit_frame(self):
        for epoch in (1000, 9000):
            with self.subTest(owner_birth=epoch):
                self.exercise('melee', epoch)

    def test_duplicate_chooses_viewed_body_identity(self):
        for epoch in (1000, 9000):
            with self.subTest(owner_birth=epoch):
                self.exercise('duplicate', epoch)


if __name__ == '__main__':
    unittest.main()
