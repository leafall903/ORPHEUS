"""Actual emitted spray packets, with explicitly reconstructed sweep geometry."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures
from test_game_combat import Run, aim

from ow174.game import mei_wall
from ow174.game.collision import World
from ow174.game.content import heroes


class MoiraSprayGeometryTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight

    def one_packet(self, run, source):
        for _ in range(20):
            run.step({source: (0, 0, 1)})
            packets = run.match.combat.rockets
            if packets:
                self.assertEqual(len(packets), 1)
                self.assertIsNotNone(packets[0].spray)
                return packets[0]
        self.fail('authored volley emitted no spray packet')

    def test_actual_mei_wall_blocks_spray_and_intercepts_grasp_until_cancel(self):
        for attack, friendly_wall in (("spray", False), ("grasp", False), ("grasp", True)):
            with self.subTest(attack=attack, friendly_wall=friendly_wall):
                run = Run(names=("Moira", "Mei", "Recipient"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                source, mei, recipient = run.players
                self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                self.assertTrue(match.switch_hero(mei, heroes()[mei_wall.MEI]))
                mei.team = source.team if friendly_wall else 1 - source.team
                recipient.team = source.team if attack == "spray" else 1 - source.team
                x, y, z = source.mover.position
                mei.mover.position = [x, y, z + 9]
                recipient.mover.position = [x, y, z + 6]
                for player in run.players:
                    player.mover.body = None
                for _ in range(40):
                    run.step({})
                fight = match.combat
                direction = aim(fight._eye(mei), (x, y, z + 3))
                for button in (16, 0, 1, 0):
                    run.step({mei: (*direction, button)})
                self.assertTrue(fight.walls.pieces)
                targets = {target.owner: target for target in fight._targets()}
                own = fight.vitals_of(targets[source])
                health = fight.vitals_of(targets[recipient])
                own.damage(100)
                health.damage(100)
                health_before = health.total
                component = source.body_script.component
                component.set(12918, 0)
                source.body_script.set_ult_charge(0)
                weapon = next(item for item in component.instances.values() if item.graph.index == 0xF76)
                weapon.write((0, 87), 20.0)
                total_wall_health = sum(piece.health for piece in fight.walls.pieces.values())
                buttons = 1 if attack == "spray" else 2
                for _ in range(20):
                    run.step({source: (0, 0, buttons)})
                self.assertEqual(health.total, health_before)
                self.assertEqual(own.total, 100)
                self.assertEqual(component.entity_var(342).value(), 0)
                self.assertLess(weapon.vars[87].value(), 21)
                wall_damage = total_wall_health - sum(piece.health for piece in fight.walls.pieces.values())
                self.assertAlmostEqual(wall_damage, 16 if attack == "grasp" and not friendly_wall else 0)
                self.assertNotIn(recipient.body, fight.moira_hots)
                run.step({mei: (*direction, 16)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(20):
                    run.step({source: (0, 0, buttons)})
                self.assertGreater(component.entity_var(342).value(), 0)
                if attack == "spray":
                    self.assertGreater(health.total, health_before)
                else:
                    self.assertLess(health.total, health_before)
                    self.assertGreater(own.total, 100)

    def test_one_packet_heals_two_allies_through_an_enemy_without_repeated_contact(self):
        run = Run(names=('Moira', 'NearAlly', 'Enemy', 'FarAlly'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source = run.players[0]
        match.switch_hero(source, heroes()[fixtures.MOIRA])
        x, y, z = source.mover.position
        for index, player in enumerate(run.players[1:], 1):
            player.team = 1 - source.team if index == 2 else source.team
            player.mover.position = [x, y, z + 2 * index]
        for _ in range(20):
            run.step({})
        targets = {t.owner: t for t in match.combat._targets()}
        health = [match.combat.vitals_of(targets[p]) for p in run.players[1:]]
        for vitals in health:
            vitals.damage(100)
        packet = self.one_packet(run, source)
        for _ in range(10):
            run.step({})
        self.assertEqual([v.total for v in health], [103.5, 100, 103.5])
        self.assertEqual(packet.spray.hits, {run.players[1].body, run.players[3].body})

    def test_declared_width_hits_off_axis_capsule_and_rejects_outside_width(self):
        for offset in (1.0, 1.5):
            with self.subTest(offset=offset):
                run, source, _target, health = self.setup_fight(offset=offset)
                self.one_packet(run, source)
                for _ in range(10):
                    run.step({})
                self.assertEqual(health.total, 103.5 if offset == 1 else 100)

    def test_packet_stops_at_wall_and_cannot_heal_with_width_through_cover(self):
        run, source, _target, health = self.setup_fight()
        x, y, z = source.mover.position
        z += 1.5
        wall = World([
            (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
            (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
        ])
        with patch.object(run.match.combat, '_collision', return_value=wall):
            packet = self.one_packet(run, source)
            for _ in range(15):
                run.step({})
        self.assertEqual(health.total, 100)
        self.assertNotIn(packet, run.match.combat.rockets)
        self.assertAlmostEqual(packet.position[2], z, places=4)

    def test_packet_expires_at_authored_range_and_rejects_distant_recipient(self):
        run, source, _target, health = self.setup_fight(distance=17)
        packet = self.one_packet(run, source)
        for _ in range(30):
            run.step({})
        self.assertEqual(health.total, 100)
        self.assertNotIn(packet, run.match.combat.rockets)
        self.assertAlmostEqual(packet.flown * packet.speed, 15, places=4)


    def test_actual_ice_wall_blocks_melee_and_both_beam_searches(self):
        for attack in ("melee", "beam"):
            for friendly_wall in (False, True):
                with self.subTest(attack=attack, friendly_wall=friendly_wall):
                    run = Run(names=("Moira", "Mei", "Ally", "Enemy"))
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    source, mei, ally, enemy = run.players
                    self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                    self.assertTrue(match.switch_hero(mei, heroes()[mei_wall.MEI]))
                    ally.team, enemy.team = source.team, 1 - source.team
                    mei.team = source.team if friendly_wall else enemy.team
                    x, y, z = source.mover.position
                    enemy_distance, wall_distance = (2.2, 1.1) if attack == "melee" else (7, 3)
                    for player, distance in ((mei, 9), (ally, 6), (enemy, enemy_distance)):
                        player.mover.position = [x, y, z + distance]
                    for player in run.players:
                        player.mover.body = None
                    for _ in range(40):
                        run.step({})
                    fight = match.combat
                    direction = aim(fight._eye(mei), (x, y, z + wall_distance))
                    for button in (16, 0, 1, 0):
                        run.step({mei: (*direction, button)})
                    self.assertEqual(len(fight.walls.pieces), 5)
                    pillar = min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0] - x))
                    targets = {target.owner: target for target in fight._targets()}
                    self.assertAlmostEqual(enemy.mover.position[1], y, places=2)
                    own, healing, damage = (fight.vitals_of(targets[p]) for p in (source, ally, enemy))
                    own.damage(100)
                    healing.damage(100)
                    before = healing.total, damage.total
                    if attack == "melee":
                        if not friendly_wall:
                            # Leave exactly one real quick-melee hit before destruction.
                            fight.walls.damage(pillar, source, 370)
                        run.step({source: (0, 0, 0x800)})
                        for _ in range(90):
                            run.step({})
                        self.assertEqual(pillar.health, 400 if friendly_wall else 0)
                        self.assertEqual(pillar.entity in fight.walls.pieces, friendly_wall)
                    else:
                        fixtures.MoiraOpenAcceptanceTests.start_ult(self, run, source)
                        for _ in range(20):
                            run.step({})
                        self.assertGreater(own.total, 100)
                    self.assertEqual((healing.total, damage.total), before)
                    run.step({mei: (*direction, 16)})
                    self.assertFalse(fight.walls.pieces)
                    before = healing.total, damage.total
                    if attack == "melee":
                        run.step({source: (0, 0, 0x800)})
                        for _ in range(90):
                            run.step({})
                        self.assertEqual(damage.total, before[1] - 30)
                    else:
                        for _ in range(20):
                            run.step({})
                        self.assertAlmostEqual(healing.total - before[0], 44.8, places=4)
                        self.assertAlmostEqual(damage.total - before[1], -22.4, places=4)
