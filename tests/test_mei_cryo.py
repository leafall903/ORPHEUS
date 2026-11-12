"""Executed Cryo reconstruction checks; native/client parity stays open."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_delivered_mei_combat as fixtures

from ow174.game import combat, mei
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import heroes


class CryoTests(unittest.TestCase):
    def duel(self):
        run, owner, enemy, _target = fixtures.MeiCombatTests().duel()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        return run, owner, enemy, target, fight.vitals_of(target)

    def step(self, run, owner, frames, button=0):
        for _ in range(frames):
            run.step({owner: (0, 0, button)})

    def cryo(self, owner):
        return mei.cryo_instance(owner)

    def test_full_duration_heals_exactly_200_self_only_and_ends(self):
        run, owner, enemy, _target, vitals = self.duel()
        vitals.damage(220.0)
        other_target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        other_vitals = run.match.combat.vitals_of(other_target)
        other_vitals.damage(100.0)
        self.step(run, owner, 1, 8)
        self.assertTrue(mei.protected(owner))
        self.assertGreater(vitals.total, 30.0)
        self.step(run, owner, 248)
        self.assertTrue(mei.protected(owner))
        self.step(run, owner, 1)
        self.assertFalse(mei.protected(owner))
        self.assertAlmostEqual(vitals.total, 230.0, places=6)
        self.assertEqual(other_vitals.total, 100.0)
        self.step(run, owner, 50)
        self.assertAlmostEqual(vitals.total, 230.0, places=6)
        self.assertGreater(self.cryo(owner).vars[61].value(), 0)

    def test_switching_hero_during_cryo_retires_healing_and_protection(self):
        run, owner, enemy, _target, vitals = self.duel()
        fight = run.match.combat
        vitals.damage(100.0)
        self.step(run, owner, 1, 8)
        self.assertTrue(mei.protected(owner))
        self.assertGreater(vitals.total, 150.0)
        retired_health = vitals.total
        old_body = owner.body
        self.assertTrue(run.match.switch_hero(owner, enemy.hero))
        self.assertNotEqual(owner.body, old_body)
        self.assertIsNone(self.cryo(owner))
        self.assertFalse(mei.protected(owner))

        x, y, z = enemy.mover.position
        fight.walls.place_player(owner, (x, y, z + 5.))
        target = next(t for t in fight._targets() if t.owner is owner)
        replacement = fight.vitals_of(target)
        full_health = replacement.total
        fixtures.MeiCombatTests().fire(run, enemy, target, FIRE, 0.9, frames=3)
        self.assertEqual(replacement.total, full_health - 20.)
        self.step(run, owner, 250)
        self.assertEqual(replacement.total, full_health - 20.)
        self.assertEqual(vitals.total, retired_health)

    def test_enemy_rifle_hits_cannot_damage_or_issue_hit_notice_during_cryo(self):
        run, owner, enemy, target, vitals = self.duel()
        vitals.damage(100.0)
        self.step(run, owner, 1, 8)
        yaw, pitch = fixtures.aim(run.match.combat._eye(enemy), (
            target.feet[0], target.feet[1] + 0.9, target.feet[2],
        ))
        before = vitals.total
        for _ in range(3):
            run.step({enemy: (yaw, pitch, FIRE)})
        self.assertGreater(vitals.total, before)
        self.assertEqual(enemy.client.messages(combat.HIT), [])

    def test_cryo_blocks_enemy_mei_attacks_and_helix_until_cancel(self):
        for attack, button, ammo_cost, expected in (
            ("spray", FIRE, None, None),
            ("icicle", 2, 10., 75.),
            ("melee", 0x800, 0., 30.),
            ("helix", 2, None, 120.),
        ):
            with self.subTest(attack=attack):
                run, owner, enemy, target, vitals = self.duel()
                fight = run.match.combat
                weapon = None
                helix = None
                if attack != "helix":
                    self.assertTrue(run.match.switch_hero(enemy, heroes()[mei.HERO]))
                    weapon = next(i for i in enemy.body_script.component.instances.values()
                                  if i.graph.index == 0x5E6)
                else:
                    helix = next(i for i in enemy.body_script.component.instances.values()
                                 if i.graph.index == 0x254)
                x, y, z = owner.mover.position
                fight.walls.place_player(enemy, (x, y, z + 2.))
                self.step(run, owner, 30)
                self.step(run, owner, 1, 8)
                self.assertTrue(mei.protected(owner))
                fixtures.MeiCombatTests().fire(run, enemy, target, button, 0.9)
                self.assertTrue(mei.protected(owner))
                self.assertEqual(vitals.total, 250.)
                self.assertIsNone(fight.frost(owner))
                self.assertEqual(enemy.client.messages(combat.HIT), [])
                if weapon is not None:
                    ammo = weapon.vars[53].value()
                    if ammo_cost is None:
                        self.assertLess(ammo, 120.)
                    else:
                        self.assertEqual(ammo, 120. - ammo_cost)
                if attack == "melee":
                    melee = next(i for i in enemy.body_script.component.instances.values()
                                 if i.graph.index == 0x43)
                    self.assertEqual(melee.states[4].counter, 1)
                if helix is not None:
                    self.assertGreater(helix.vars[61].value(), 0.)

                self.step(run, owner, 1, 2)
                self.assertFalse(mei.protected(owner))
                self.step(run, owner, 60)
                if helix is not None:
                    for _ in range(375):
                        if helix.vars[61].value() == 0.:
                            break
                        run.step({})
                    self.assertEqual(helix.vars[61].value(), 0.)
                fixtures.MeiCombatTests().fire(run, enemy, target, button, 0.9)
                hits = enemy.client.messages(combat.HIT)
                if expected is None:
                    self.assertGreater(len(hits), 0)
                    self.assertEqual(vitals.total, 250. - 2.75 * len(hits))
                    self.assertIsNotNone(fight.frost(owner))
                else:
                    self.assertEqual(vitals.total, 250. - expected)
                    self.assertTrue(hits)
                self.assertGreater(self.cryo(owner).vars[61].value(), 0.)

    def test_cryo_entered_in_flight_consumes_icicle_and_protects_rear_enemy(self):
        run = fixtures.Run(fixtures.KANEZAKA_DM, ("Shooter", "Cryo", "Rear"))
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter, defender, rear = run.players
        for actor in (shooter, defender):
            self.assertTrue(run.match.switch_hero(actor, heroes()[mei.HERO]))
        shooter.team = 0
        defender.team = rear.team = 1
        x, y, z = shooter.mover.position
        fight.walls.place_player(defender, (x, y, z + 24.0))
        fight.walls.place_player(rear, (x, y, z + 28.0))
        self.step(run, defender, 20)
        defender_target, rear_target = (
            next(t for t in fight._targets() if t.owner is actor) for actor in (defender, rear)
        )
        front_health = fight.vitals_of(defender_target)
        rear_health = fight.vitals_of(rear_target)
        rear_before = rear_health.total
        aim = fixtures.aim(fight._eye(shooter), (x, y + 0.9, z + 28.0))
        shooter.body_script.ult_seconds = 0
        shooter.body_script.set_ult_charge(0.0)
        charge = shooter.body_script.component.entity_var(342)
        def launch_icicle():
            for _ in range(45):
                run.step({shooter: (*aim, 2)})
                if fight.rockets:
                    break
            self.assertEqual(len(fight.rockets), 1)
            return fight.rockets[0]

        rocket = launch_icicle()
        self.assertFalse(mei.protected(defender))
        self.step(run, defender, 1, 8)
        self.assertTrue(mei.protected(defender))
        self.assertIn(rocket, fight.rockets)
        self.step(run, defender, 35)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(rocket.hit_entities, {defender.body})
        self.assertEqual(front_health.total, 250.0)
        self.assertEqual(rear_health.total, rear_before)
        self.assertEqual(shooter.client.messages(combat.HIT), [])
        self.assertEqual(charge.value(), 0.0)

        rocket = launch_icicle()
        self.assertTrue(mei.protected(defender))
        self.step(run, defender, 1, 2)
        self.assertFalse(mei.protected(defender))
        self.assertIn(rocket, fight.rockets)
        self.step(run, defender, 35)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(front_health.total, 175.0)
        self.assertEqual(rear_health.total, rear_before)
        self.assertEqual(charge.value(), 75.0)

    def test_cancel_stops_healing_and_starts_nonbypassable_cooldown(self):
        run, owner, _enemy, _target, vitals = self.duel()
        vitals.damage(200.0)
        self.step(run, owner, 1, 8)
        self.step(run, owner, 20)
        self.step(run, owner, 1, 2)
        self.assertFalse(mei.protected(owner))
        after = vitals.total
        self.assertLess(after, 100.0)
        self.assertGreater(self.cryo(owner).vars[61].value(), 11.9)
        for _ in range(50):
            self.step(run, owner, 1, 8)
            self.step(run, owner, 1)
        self.assertEqual(self.cryo(owner).states[6].counter, 1)
        self.assertEqual(vitals.total, after)
        self.step(run, owner, 660)
        self.step(run, owner, 1, 8)
        self.assertTrue(mei.protected(owner))
        self.assertEqual(self.cryo(owner).states[6].counter, 2)

    def test_cryo_ignores_early_cancel_then_spends_full_cooldown_on_fresh_cancel(self):
        run, owner, _, _, vitals = self.duel()
        vitals.damage(100.0)
        self.step(run, owner, 1, 8)
        self.step(run, owner, 1)
        self.step(run, owner, 1, 2)
        instance = self.cryo(owner)
        self.assertTrue(mei.protected(owner))
        self.assertEqual(instance.vars[61].value(), 0.0)
        self.step(run, owner, 15)
        self.assertTrue(mei.protected(owner))
        self.step(run, owner, 1, 2)
        self.assertFalse(mei.protected(owner))
        self.assertGreater(instance.vars[61].value(), 11.9)
        health = vitals.total
        self.step(run, owner, 1)
        self.step(run, owner, 1, 8)
        self.step(run, owner, 15)
        self.assertFalse(mei.protected(owner))
        self.assertEqual(instance.state(6).counter, 1)
        self.assertEqual(vitals.total, health)

    def test_actual_cryo_cooldown_press_expiry_hold_and_fresh_input(self):
        for kind in ('early', 'near', 'held', 'fresh'):
            with self.subTest(kind=kind):
                run, owner, _enemy, _target, _vitals = self.duel()
                self.step(run, owner, 1, 8)
                self.step(run, owner, 20)
                self.step(run, owner, 1, 2)
                instance = self.cryo(owner)
                self.assertGreater(instance.vars[61].value(), 11.9)
                # Unlike064A,05FE declares no early-press window. The static
                # runtime's null-window policy is0ms; a released near-end tap
                # must not be treated as a150ms buffered request.
                self.assertIsNone(instance.state(0).node.field('m_246438AD'))
                if kind == 'near':
                    while instance.vars[61].value() > .1:
                        self.step(run, owner, 1)
                if kind in ('early', 'near'):
                    self.step(run, owner, 1, 8)
                    self.step(run, owner, 1)
                    while instance.vars[61].value() > 0.:
                        self.step(run, owner, 1)
                    self.step(run, owner, 15)
                    self.assertFalse(mei.protected(owner))
                    self.assertEqual(instance.state(6).counter, 1)
                elif kind == 'held':
                    self.step(run, owner, 800, 8)
                    self.assertTrue(mei.protected(owner))
                    self.assertEqual(instance.state(6).counter, 2)
                else:
                    self.step(run, owner, 780)
                    self.step(run, owner, 1, 8)
                    self.assertTrue(mei.protected(owner))
                    self.assertEqual(instance.state(6).counter, 2)

    def test_dead_owner_cannot_start_or_heal(self):
        run, owner, enemy, target, vitals = self.duel()
        run.match.combat._damage(enemy, target, 1000.0, False, None, run.frame)
        self.assertTrue(vitals.dead)
        self.step(run, owner, 20, 8)
        self.assertFalse(mei.protected(owner))
        self.assertEqual(vitals.total, 0.0)

    def test_input_emptied_weapon_recovers_fresh_primary_and_secondary_after_cryo_exit(self):
        for end in ('duration', 'cancel'):
            for button in (1, 2):
                with self.subTest(end=end, button=button):
                    run, owner, enemy, _target, _vitals = self.duel()
                    weapon = next(i for i in owner.body_script.component.instances.values()
                                  if i.graph.index == 0x05E6)
                    for _ in range(600):
                        run.step({owner: (-32768, 0, 1)})
                        if weapon.vars[53].value() == 0:
                            break
                    self.assertEqual(weapon.vars[53].value(), 0)
                    run.step({owner: (-32768, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                    self.step(run, owner, 249 if end == 'duration' else 30)
                    if end == 'cancel':
                        run.step({owner: (-32768, 0, 2)})
                    # Allow the authored exit wait and release any cancel input.
                    self.step(run, owner, 30)
                    self.assertFalse(mei.protected(owner))
                    target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                    yaw, pitch = fixtures.aim(run.match.combat._eye(owner),
                                              (target.feet[0], target.feet[1] + .9, target.feet[2]))
                    for _ in range(150):
                        run.step({owner: (yaw, pitch, button)})
                    self.assertLess(run.match.combat.vitals_of(target).total, 200.)
                    self.assertGreaterEqual(weapon.vars[53].value(), 0.)
                    self.assertLessEqual(weapon.vars[53].value(), 120.)

    def test_secondary_waits_for_cryo_exit_priority_without_consuming_the_hold(self):
        for cancel in (False, True):
            for held in (False, True):
                with self.subTest(cancel=cancel, held=held):
                    run, owner, enemy, _, _ = self.duel()
                    target = next(t for t in run.match.combat._targets() if t.owner is enemy)
                    angle = fixtures.aim(run.match.combat._eye(owner),
                                         (target.feet[0], target.feet[1] + .9, target.feet[2]))
                    weapon = next(i for i in owner.body_script.component.instances.values()
                                  if i.graph.index == 0x05E6)
                    run.step({owner: (*angle, 8)})
                    self.step(run, owner, 20 if cancel else 249)
                    run.step({owner: (*angle, 2)})
                    self.assertFalse(mei.protected(owner))
                    self.assertEqual(weapon.vars[53].value(), 120.)
                    for _ in range(85):
                        run.step({owner: (*angle, 2 if held else 0)})
                        if owner.client.messages(combat.HIT):
                            break
                    health = run.match.combat.vitals_of(target)
                    if not held:
                        self.assertEqual(health.total, 200.)
                        self.assertEqual(weapon.vars[53].value(), 120.)
                        for _ in range(85):
                            run.step({owner: (*angle, 2)})
                            if owner.client.messages(combat.HIT):
                                break
                    self.assertEqual(health.total, 125.)
                    self.assertEqual(weapon.vars[53].value(), 110.)
                    self.assertGreater(self.cryo(owner).vars[61].value(), 8.)

    def walk(self, run, owner, frames, buttons=0):
        for _ in range(frames):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            for player in run.players:
                player.client.queue_entities(run.match.remote_movements(player))
                player.take_commands([Command(run.frame, forward=127 if player is owner else 0,
                                               buttons=buttons if player is owner else 0)])

    def test_cryo_rejects_walking_and_jump_then_restores_walking_after_cancel(self):
        run, owner, _enemy, _target, _vitals = self.duel()
        self.step(run, owner, 1, 8)
        before = owner.mover.position
        self.walk(run, owner, 30, JUMP)
        self.assertEqual(owner.mover.position, before)
        self.assertEqual(owner.mover.throttles, (0, 127))
        self.assertEqual(owner.mover.input_throttles, (0, 127))
        self.step(run, owner, 1, 2)
        self.walk(run, owner, 30)
        self.assertGreater(owner.mover.position[2], before[2] + 0.5)

    def test_cryo_gate_remains_a_serializable_plain_state(self):
        from ow174.game.body_vm_snapshot import export_body_vm
        run, owner, _enemy, _target, _vitals = self.duel()
        self.step(run, owner, 1, 8)
        document = export_body_vm(owner.body_script)
        self.assertIsInstance(document, dict)
        self.assertEqual(document, export_body_vm(owner.body_script))

    def test_passive_server_storage_observes_healing_and_penetration_history(self):
        import test_server_snapshot as snapshots

        from ow174.game.server_snapshot import snapshot_digest

        fixture = snapshots.ServerSnapshotTests()
        session, _peers, players = fixture.scene()
        owner = players[0]
        fight = owner.match.combat
        initial = snapshot_digest(fixture.capture(session))
        fight.cryo_samples[owner.body] = mei.CryoHealing((1, 1, 1000), fight.now, 42.)
        healing = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(initial, healing)
        rocket = combat.Rocket(owner, owner.team, fight._eye(owner), (0.0, 0.0, 1.0),
                               40.0, 1.0, combat.HitDef(2.75), None, target_limit=99)
        fight.rockets.append(rocket)
        flying = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(healing, flying)
        rocket.hit_entities.add(0xC0001000)
        pierced = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(flying, pierced)
        fight.frost_hits[owner.body] = (1.0, 16000, 17300, 0, 0)
        frozen = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(pierced, frozen)
        fight.frost_hits[owner.body] = (1.0, 16000, 17301, 0, 0)
        self.assertNotEqual(frozen, snapshot_digest(fixture.capture(session)))


if __name__ == "__main__":
    unittest.main()
