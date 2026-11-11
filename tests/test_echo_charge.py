"""Actual health-event charge behavior; the1HP->1point policy is reconstructed.

Native base passive rate and ultimate-source tag rules remain open.
Disabling the existing passive clock isolates actual damage/healing gains.
"""
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_echo_combat as echo_tests
import test_echo_duplicate as duplicate_tests

from ow174.game import combat
from ow174.game.commands import CROUCH, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph


class EchoChargeTests(unittest.TestCase):
    def session(self):
        result = echo_tests.EchoPrimaryTests().session()
        result[1].body_script.ult_seconds = 0
        result[1].body_script.set_ult_charge(0)
        return result

    def shot(self, run, player, angles):
        run.step({player: (*angles, 1)})
        for _ in range(25):
            run.step({player: (*angles, 0)})

    def charge(self, player):
        return player.body_script.component.entity_var(342).value()

    def test_real_primary_health_damage_grants_exact_charge_and_misses_do_not(self):
        run, player, _, target, angles = self.session()
        self.shot(run, player, angles)
        self.assertEqual(run.match.combat.vitals_of(target).total, 149)
        self.assertEqual(self.charge(player), 51)
        self.shot(run, player, (-32768, 0))
        for _ in range(90):
            run.step({})
        self.assertEqual(self.charge(player), 51)

    def test_actual_melee_and_beam_charge_only_for_health_removed(self):
        run, player, _, target, angles = echo_tests.EchoPrimaryTests().session(distance=2)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        vitals = run.match.combat.vitals_of(target)
        run.step({player: (*angles, 0x800)})
        self.assertEqual(vitals.total, 170)
        self.assertEqual(self.charge(player), 30)
        for _ in range(70):
            run.step({})
        self.assertEqual(self.charge(player), 30)

        miss = (angles[0] % 65536 - 32768, angles[1])
        beam = echo_tests.EchoBeamTests().start_beam(run, player, miss).states[17]
        self.assertEqual(vitals.total, 170)
        self.assertEqual(self.charge(player), 30)
        run.step({player: (*angles, 0)})
        self.assertTrue(beam.active)
        self.assertAlmostEqual(vitals.total, 169.2)
        self.assertAlmostEqual(self.charge(player), 30.8, places=5)
        run.step({player: (*miss, 0)})
        self.assertTrue(beam.active)
        self.assertAlmostEqual(vitals.total, 169.2)
        self.assertAlmostEqual(self.charge(player), 30.8, places=5)

    def test_actual_armour_damage_and_overkill_use_lost_pools_not_requested_amount(self):
        run, player, enemy, _, angles = self.session()
        run.match.switch_hero(enemy, heroes()[0x02E0000000000007], preserve_pose=True)
        run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        total = fight.vitals_of(target).total
        self.shot(run, player, angles)
        self.assertEqual(self.charge(player), 36)
        fight._damage(player, target, 1e6, False, None, run.frame)
        self.assertEqual(self.charge(player), total)

    def test_allies_self_damage_and_full_health_heals_do_not_create_charge(self):
        run, player, ally, target, angles = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = player.team
        self.shot(run, player, angles)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(self.charge(player), 0)
        own = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat.heal_area(player, tuple(player.mover.position), 10, 50)
        self.assertEqual(self.charge(player), 0)
        run.match.combat._damage(player, own, 30, False, None, run.frame)
        self.assertEqual(self.charge(player), 0)

    def test_real_damage_caps_charge_and_unlocks_duplicate(self):
        run, player, enemy, target, angles = self.session()
        fight = run.match.combat
        body = player.body
        cost = player.body_script.component.entity_var(338).value()
        player.body_script.set_ult_charge(cost - 10)
        run.step({player: (*angles, 32)})
        self.assertEqual(player.body, body)
        self.assertEqual(self.charge(player), cost - 10)
        self.assertFalse(fight.echo_duplicates.active(player))

        self.shot(run, player, angles)
        self.assertEqual(fight.vitals_of(target).total, 149)
        self.assertEqual(self.charge(player), cost)
        run.step({player: (*angles, 32)})
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertNotEqual(player.body, body)
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertEqual(fight.echo_duplicates.states[player.entity].target_body, enemy.body)
        self.assertEqual(self.charge(player), 0)

    def test_copied_weapon_damage_charges_the_copied_meter(self):
        fixture = duplicate_tests.EchoDuplicateTests()
        run, player, _, target, angles = fixture.session()
        fixture.start(run, player, angles)
        player.body_script.ult_seconds = 0
        for _ in range(26):
            run.step({player: (*angles, 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 120)  # four9Hz rounds over26 frames
        self.assertEqual(self.charge(player), 520)

    def test_copied_passive_charge_uses_the_same_authored_bonus(self):
        fixture = duplicate_tests.EchoDuplicateTests()
        run, player, _, _, angles = fixture.session()
        fixture.start(run, player, angles)
        script = player.body_script
        cost = script.component.entity_var(338).value()
        run.step({})
        expected = cost * script.frame_ms / (1000 * script.ult_seconds) * 6.5
        self.assertAlmostEqual(self.charge(player), expected, places=5)

    def test_copied_damage_gain_reads_1544_bonus(self):
        fixture = duplicate_tests.EchoDuplicateTests()
        run, player, _, target, angles = fixture.session()
        expression = graph.graph(0x1544).node(4).field('m_value')['m_expression']
        with patch.dict(expression, {'m_D99EF254': [2.0]}):
            fixture.start(run, player, angles)
        player.body_script.ult_seconds = 0
        for _ in range(26):
            run.step({player: (*angles, 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 120)
        self.assertEqual(self.charge(player), 240)

    def test_sticky_contacts_and_fuses_charge_only_when_damage_resolves(self):
        run, player, _, target, angles = self.session()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        run.step({player: (*angles, 2)})
        self.assertEqual(vitals.total, 200)
        self.assertEqual(self.charge(player), 0)
        for _ in range(40):
            run.step({player: (*angles, 0)})
        self.assertEqual(len(fight.rockets), 6)
        self.assertTrue(all(rocket.attached_entity == target.entity for rocket in fight.rockets))
        self.assertEqual(vitals.total, 170)
        self.assertEqual(self.charge(player), 30)

        for _ in range(90):
            run.step({})
            self.assertAlmostEqual(self.charge(player), 200 - vitals.total)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(vitals.total, 20)
        self.assertEqual(self.charge(player), 180)

    def test_pre_copy_sticky_blast_damages_without_filling_the_copied_meter(self):
        fixture = duplicate_tests.EchoDuplicateTests()
        run, player, _, target, angles = self.session()
        fight = run.match.combat
        echo_tests.EchoStickyTests().launch(run, player, angles)
        self.assertEqual(fight.vitals_of(target).total, 170)
        self.assertEqual(self.charge(player), 30)
        old_body = player.body
        fixture.start(run, player, angles)
        self.assertNotEqual(player.body, old_body)
        player.body_script.ult_seconds = 0
        for _ in range(90):
            run.step({})
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertEqual(fight.vitals_of(target).total, 20)
        self.assertEqual(self.charge(player), 0)

    def test_running_copied_ultimate_does_not_generate_charge_from_real_bullets(self):
        fixture = duplicate_tests.EchoDuplicateTests()
        run, player, _, target, angles = fixture.session()
        fixture.start(run, player, angles)
        player.body_script.ult_seconds = 0
        fixture.charged(player)
        run.step({})
        run.step({player: (*angles, 32)})
        self.assertTrue(any(s.flags & 1 for s in player.body_script.ultimates))
        for _ in range(80):
            run.step({player: (*angles, 1)})
        self.assertLess(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(self.charge(player), 0)

    def test_configured_ruleset_scalar_controls_real_damage_gain(self):
        run, player, _, target, _ = self.session()
        fight = run.match.combat
        original = player.body_script.world.ruleset
        with patch.object(player.body_script.world, 'ruleset',
                          side_effect=lambda key: 2.0 if key == 0x2819 else original(key)):
            fight._damage(player, target, 10, False, None, run.frame)
        self.assertEqual(self.charge(player), 20)
        with patch.object(player.body_script.world, 'ruleset',
                          side_effect=lambda key: 0.0 if key == 0x2819 else original(key)):
            fight._damage(player, target, 10, False, None, run.frame)
        self.assertEqual(self.charge(player), 20)

    def test_echo_standing_and_crouched_origin_use_current_body_resource_heights(self):
        run, player, _, _, _ = self.session()
        fight = run.match.combat
        self.assertAlmostEqual(fight._eye(player)[1] - player.mover.position[1], 1.7)
        for _ in range(3):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, buttons=CROUCH)])
        self.assertAlmostEqual(fight._eye(player)[1] - player.mover.position[1], 1.1)
        target = next(t for t in fight._targets() if t.owner is player)
        self.assertAlmostEqual(target.eye, 1.1)
