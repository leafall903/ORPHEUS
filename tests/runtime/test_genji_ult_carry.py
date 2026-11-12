"""Actual Genji combat charge across body generations."""

from unittest.mock import patch

from test_game_combat import aim
from test_genji_combat import GenjiFixture

from ow174.game.content import SOLDIER, heroes


class GenjiUltCarryTests(GenjiFixture):
    @staticmethod
    def charge(player):
        return player.body_script.component.entity_var(342).value()

    def kill_with_rifle(self, run, genji):
        enemy = run.players[1]
        yaw, pitch = aim(run.match.combat._eye(enemy), (0, 2, 0))
        for _ in range(120):
            run.step({enemy: (yaw, pitch, 1)})
            if run.match.combat.is_dead(genji):
                return
        self.fail("actual enemy rifle did not kill Genji")

    def respawn(self, run, player):
        previous = player.body
        for _ in range(650):
            run.step({})
            if player.body != previous:
                self.assertFalse(run.match.combat.is_dead(player))
                return
        self.fail("normal respawn did not replace Genji")

    def test_rifle_death_and_respawn_transfer_unspent_damage_charge(self):
        run, genji = self.spawn(4)
        self.fire(run, genji, 1)
        self.assertEqual(self.charge(genji), 87)
        old_script = genji.body_script
        self.kill_with_rifle(run, genji)
        self.assertEqual(self.charge(genji), 87)
        self.respawn(run, genji)
        self.assertTrue(old_script.retired)
        self.assertAlmostEqual(self.charge(genji), 87, places=4)
        self.assertEqual(self.health(run, genji), 200)
        charge_graph = next(
            i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0B8B
        )
        self.assertAlmostEqual(charge_graph.vars[32280].value(), 87 / 1932)

    def test_death_during_dragonblade_cannot_refund_spent_charge(self):
        run, genji = self.spawn(4)
        script = genji.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({genji: (0, 0, 32)})
        self.assertEqual(self.charge(genji), 0)
        self.kill_with_rifle(run, genji)
        self.respawn(run, genji)
        self.assertEqual(self.charge(genji), 0)
        self.assertFalse(any(s.flags & 1 for s in genji.body_script.ultimates))

    def test_same_hero_replacement_transfers_but_hero_change_starts_fresh(self):
        run, genji = self.spawn(4)
        self.fire(run, genji, 1)
        self.assertEqual(self.charge(genji), 87)
        previous = genji.body_script
        with patch.object(run.match, "_next_body_id", return_value=None):
            self.assertFalse(run.match.switch_hero(genji, genji.hero))
        self.assertIs(genji.body_script, previous)
        self.assertEqual(self.charge(genji), 87)
        self.assertTrue(run.match.switch_hero(genji, genji.hero))
        self.assertAlmostEqual(self.charge(genji), 87, places=4)
        hero = genji.hero
        self.assertTrue(run.match.switch_hero(genji, heroes()[SOLDIER]))
        self.assertTrue(run.match.switch_hero(genji, hero))
        self.assertEqual(self.charge(genji), 0)
