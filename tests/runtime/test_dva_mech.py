"""D.Va enters play in her mech: the body definition, its health pools and its Fusion Cannons."""

import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat, pools
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, heroes, spawn_form
from ow174.game.script import graph

DVA = 0x02E000000000007A
PILOT, MECH = 0x040000000000040A, 0x0400000000000407
FUSION_CANNONS = 0x050A


def _pick(entity: int, hero: int) -> dict:
    """21616 as the hero select screen sends it: game message 1361.025 with the hero."""
    return {
        "+0x78": {"+0x0": entity},
        "+0x80": bytes.fromhex("00 00 00 00 01 c1 5d 04") + hero.to_bytes(8, "little") + bytes(8),
        "+0xA8": 0x0240000000001361,
        "+0xB0": 3,
    }


def setUpModule():
    spawn_form.cache_clear()  # a test that hid the mech's data may have left the pilot here


class SpawnFormTests(unittest.TestCase):
    def test_dva_enters_play_in_her_mech_and_the_others_in_their_own_body(self):
        self.assertEqual(heroes()[DVA].body, PILOT)
        mech = spawn_form(heroes()[DVA])
        self.assertEqual((mech.guid, mech.body), (DVA, MECH))
        self.assertIs(spawn_form(heroes()[DVA]), mech)  # a second pick of her is the same hero
        self.assertIs(spawn_form(heroes()[SOLDIER]), heroes()[SOLDIER])

    def test_a_pack_without_the_mech_keeps_the_pilot(self):
        spawn_form.cache_clear()
        self.addCleanup(spawn_form.cache_clear)
        with patch.object(graph, "entity_body", return_value=None):
            self.assertIs(spawn_form(heroes()[DVA]), heroes()[DVA])


class PoolTests(unittest.TestCase):
    def test_the_mech_has_its_own_health_and_armour(self):
        self.assertEqual(pools.body_pools(DVA), (150.0, 0.0, 0.0))
        self.assertEqual(pools.body_pools(DVA, PILOT), (150.0, 0.0, 0.0))
        self.assertEqual(pools.body_pools(DVA, MECH), (300.0, 300.0, 0.0))

    def test_a_hero_played_in_his_own_body_is_unchanged(self):
        soldier = heroes()[SOLDIER]
        self.assertEqual(pools.body_pools(SOLDIER, soldier.body), pools.body_pools(SOLDIER))


class MechInMatchTests(unittest.TestCase):
    def setUp(self):
        self.run_ = Run()
        self.match, self.player = self.run_.match, self.run_.players[0]
        self.assertTrue(self.match.switch_hero(self.player, spawn_form(heroes()[DVA])))

    def step(self, buttons: int = 0) -> None:
        run = self.run_
        run.frame += 1
        self.match.update(run.frame * combat.TICK, run.frame)
        self.player.take_commands([Command(run.frame, buttons=buttons)])

    def test_a_pick_of_dva_gives_the_mech_and_a_second_pick_keeps_the_body(self):
        soldier = heroes()[SOLDIER]
        self.assertTrue(self.match.switch_hero(self.player, soldier))
        self.match.game_message(self.player, _pick(self.player.entity, DVA), 0.0)
        self.assertEqual(self.player.hero.body, MECH)
        body = self.player.body
        self.match.game_message(self.player, _pick(self.player.entity, DVA), 0.0)
        self.assertEqual(self.player.body, body)

    def test_the_body_runs_the_mechs_graphs_with_its_pools(self):
        script = self.player.body_script
        self.assertEqual(script.world.body_definition, MECH)
        self.assertIn(FUSION_CANNONS, {item.graph.index for item in script.component.instances.values()})
        fight = self.match.combat
        target = next(target for target in fight._targets() if target.entity == self.player.body)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 600.0)
        self.assertEqual(sorted(vitals.most.values()), [0.0, 300.0, 300.0])

    def test_held_fire_shoots_the_fusion_cannons(self):
        for _ in range(30):
            self.step()
        with patch.object(combat.Combat, "_shoot", autospec=True) as shoot:
            for _ in range(60):
                self.step(FIRE)
        self.assertGreaterEqual(shoot.call_count, 3)
        self.assertIsNone(self.player.body_script.component.fault)


if __name__ == "__main__":
    unittest.main()
