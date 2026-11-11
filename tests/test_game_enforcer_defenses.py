"""Actual Enforcer burst, defense commands and Zarya ally/barrier chains.

Manual controller, ring trajectories and defense geometry remain reconstructed.
"""

import os
import unittest
from dataclasses import replace

import test_game_zarya_npcs as fixtures

from ow174.game import interception, npc
from ow174.game.content import heroes


@unittest.skipUnless(fixtures.NPC_DATA and os.environ.get("ORPHEUS_PROJECTILE_TAGS"),
                     "requires actual NPC and projectile metadata inputs")
class EnforcerDefenseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not interception.eligible(0x04000000000023F0):
            raise AssertionError("requires sourced23F0 projectile tags;unknown is not acceptance")
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def scene(self, names=("Defender",)):
        run = fixtures.Run(names=names)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in run.players:
            player.client = fixtures.NpcClient(player)
        return run

    def defender(self, run, player, hero, position=(0, 1, 0)):
        definition = heroes()[0x02E0000000000000 | hero]
        if hero == 0x7A:
            definition = replace(definition, body=0x0400000000000407)
        self.assertTrue(run.match.switch_hero(player, definition))
        player.mover.position[:] = position
        player.team = 0

    def attacker(self, run, target):
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Enforcer")
        bot = npc.spawn(run.match, definition, (0, 1, 5), team=1)
        bot.firing, bot.attack_target = True, target.entity
        return bot

    def health(self, run, player):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        return fight.vitals_of(target)

    def test_real_matrix_deflect_and_grasp_admit_all_25_pellets(self):
        for kind, hero, button, graph_id in (("matrix", 0x7A, 2, 0x02CC),
                                            ("deflect", 0x29, 16, 0x03A3),
                                            ("grasp", 0x23B, 8, 0x1D02)):
            for enabled in (False, True):
                with self.subTest(kind=kind, enabled=enabled):
                    run = self.scene()
                    player, = run.players
                    self.defender(run, player, hero)
                    bot = self.attacker(run, player)
                    vitals = self.health(run, player)
                    before = vitals.total
                    for frame in range(100):
                        held = button if enabled and ((kind == "matrix" and frame >= 45)
                                                      or frame == 45) else 0
                        run.step({player: (0, 0, held)})
                    self.assertEqual(vitals.total,
                                     before if enabled else before - (25 if hero == 0x7A else 50))
                    self.assertEqual(run.match.combat.vitals[bot.entity].total,
                                     300 if enabled and kind == "deflect" else 350)
                    instance = next(i for i in player.body_script.component.instances.values()
                                    if i.graph.index == graph_id)
                    if enabled:
                        self.assertEqual(instance.vars[6584].value(), 2)  # per-projectile output
                    self.assertFalse(run.match.combat.rockets)
                    if enabled and kind == "grasp":
                        self.assertEqual(instance.vars[21312].value(), 50)  # authored accumulator
                        bot.firing = False
                        for _ in range(150):
                            run.step({})
                        self.assertAlmostEqual(vitals.temporary_shields[interception.SHIELD_TAG].now,
                                               30, delta=0.00001)

    def test_ally_matrix_protects_zarya_without_body_cover(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                run = self.scene(("Zarya", "Mech"))
                zarya, mech = run.players
                self.defender(run, zarya, 0x68)
                self.defender(run, mech, 0x7A, (1, 1, 3))
                self.attacker(run, zarya)
                for frame in range(100):
                    run.step({mech: (0, 0, 2 if enabled and frame >= 45 else 0)})
                self.assertEqual(self.health(run, zarya).total, 400 if enabled else 350)
                self.assertEqual(self.health(run, mech).total, 600)

    def test_projected_barrier_blocks_genji_returned_burst_on_allied_enforcer(self):
        run = self.scene(("Zarya", "Genji"))
        caster, genji = run.players
        self.defender(run, caster, 0x68, (0, 1, 10))
        caster.team = 1
        self.defender(run, genji, 0x29)
        bot = self.attacker(run, genji)
        for frame in range(100):
            run.step({caster: (-32768, 0, 16 if frame == 60 else 0),
                      genji: (0, 0, 16 if frame == 45 else 0)})
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 350)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 10, delta=0.00001)
        energy = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x0242)
        self.assertEqual(energy.vars[392].value().id, genji.body)
