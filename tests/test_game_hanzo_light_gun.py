"""Light Gun combat after Hanzo destroys the D.Va player's original mech."""

import unittest

import test_game_hanzo_owned_mech as mech_tests
import test_game_hanzo_owned_missiles as missile_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import arrow_deflection, combat, dva_owned, matrix, sigma_grasp, zarya_barrier
from ow174.game.collision import World
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.expr import f32


class HanzoLightGunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.HanzoOwnedMechTests.setUpClass()

    def arena(self, distance=4):
        fixture = mech_tests.HanzoOwnedMechTests()
        run, pilot, mech, hanzo, _, _ = fixture.arena(8)
        fight = run.match.combat
        pilot_body = pilot.body
        fight.vitals[mech.entity] = combat.Vitals(mech.entity, 1)
        angles = fixture.draw(run, pilot, mech, hanzo)
        run.step({hanzo: (*angles, 0)})
        for _ in range(110):
            run.step({})
        self.assertTrue(mech.dead)
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(pilot.body, pilot_body)
        x, y, z = pilot.mover.position
        hanzo.mover.position[:] = (x, y, z + distance)
        hanzo.mover.body = None
        for _ in range(5):
            run.step({})
        pilot.body_script.ult_seconds = 0
        weapon = next(i for i in pilot.body_script.component.instances.values()
                      if i.graph.index == dva_owned.LIGHT_GUN)
        self.assertEqual(weapon.vars[53].value(), 20)
        return run, pilot, mech, hanzo, weapon.vars[53]

    def fire(self, run, pilot, hanzo, height, *, offset=0, settle=16, defense=None):
        fight = run.match.combat
        x, y, z = hanzo.mover.position
        angles = aim(fight._eye(pilot), (x + offset, y + height, z))
        defense = {} if defense is None else defense
        run.step({pilot: (*angles, 1), **defense})
        rocket, = fight.rockets
        self.assertEqual(rocket.source_entity, pilot.body)
        self.assertEqual((rocket.direct.amount, rocket.speed, rocket.range_left), (14, 50, 100))
        for _ in range(settle):
            run.step({pilot: (*angles, 0), **defense})
        return rocket

    def test_torso_and_upper_contact_credit_actual_damage(self):
        for height, amount, flags in ((0.8, 14, 0), (1.34, 28, combat.CRITICAL)):
            with self.subTest(height=height):
                run, pilot, _, hanzo, magazine = self.arena()
                charge = pilot.body_script.component.entity_var(ULT_CHARGE)
                before = charge.value()
                self.fire(run, pilot, hanzo, height)
                self.assertEqual(run.match.combat.vitals[hanzo.body].total, 200 - amount)
                self.assertEqual(charge.value(), f32(before + amount))
                self.assertEqual(magazine.value(), 19)
                self.assertEqual([hit["+0x78"] for hit in pilot.client.messages(combat.HIT)],
                                 [{"+0x0": {"+0x0": hanzo.body}, "+0x4": amount, "+0x8": flags}])

    def test_miss_wall_and_ally_do_not_credit_damage(self):
        for case in ("miss", "wall", "ally"):
            with self.subTest(case=case):
                run, pilot, _, hanzo, magazine = self.arena()
                fight = run.match.combat
                charge = pilot.body_script.component.entity_var(ULT_CHARGE)
                before = charge.value()
                fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, 200)
                if case == "wall":
                    x, y, z = pilot.mover.position
                    run.match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x - 3, z + 2, x + 3, z + 2, y, y + 4),
                        name="Light Gun cover",
                    )
                elif case == "ally":
                    hanzo.team = pilot.team
                self.fire(run, pilot, hanzo, 0.8, offset=4 if case == "miss" else 0)
                self.assertEqual(fight.vitals[hanzo.body].total, 200)
                self.assertFalse(pilot.client.messages(combat.HIT))
                self.assertEqual(charge.value(), before)
                self.assertEqual(magazine.value(), 19)

    def defender_scene(self, hero, button, *, lateral=0):
        run, pilot, _, hanzo, _ = self.arena(12)
        fight = run.match.combat
        hx, hy, hz = hanzo.mover.position
        defender = missile_tests.HanzoOwnedMissileTests().add_defender(
            run, hanzo, hero, x=hx + lateral, z=hz - 6, warmup=0,
        )
        defender.client = mech_tests.NpcFixtureClient(defender)
        for _ in range(80):
            run.step({})
        x, y, z = pilot.mover.position
        target = (hx, hy + 1, hz) if lateral else (x, y + 0.8, z)
        defense = {defender: (*aim(fight._eye(defender), target), button)}
        for _ in range(12):
            run.step(defense)
        return run, pilot, hanzo, defender, defense

    def test_genji_deflect_returns_round_with_new_credit(self):
        run, pilot, hanzo, genji, defense = self.defender_scene(arrow_deflection.GENJI, 16)
        fight = run.match.combat
        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        rocket = self.fire(run, pilot, hanzo, 0.8, settle=35, defense=defense)
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.source_entity, genji.body)
        self.assertEqual(fight.vitals[pilot.body].total, 136)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertEqual(fight.vitals[genji.body].total, 200)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in genji.client.messages(combat.HIT)], [14])
        self.assertFalse(pilot.client.messages(combat.HIT))
        self.assertEqual(charge.value(), before)

    def test_matrix_and_grasp_consume_the_round(self):
        for hero, button in ((matrix.DVA, 2), (sigma_grasp.SIGMA, 8)):
            with self.subTest(hero=hero):
                run, pilot, hanzo, defender, defense = self.defender_scene(hero, button)
                fight = run.match.combat
                charge = pilot.body_script.component.entity_var(ULT_CHARGE)
                before = charge.value()
                rocket = self.fire(run, pilot, hanzo, 0.8, settle=30, defense=defense)
                self.assertTrue(rocket.matrix_absorbable)
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(fight.vitals[hanzo.body].total, 200)
                self.assertFalse(pilot.client.messages(combat.HIT))
                self.assertEqual(charge.value(), before)
                if hero == matrix.DVA:
                    self.assertEqual(fight.vitals[defender.controlled_mech.entity].total, 600)
                else:
                    for _ in range(140):
                        run.step(defense)
                    vitals = fight.vitals[defender.body]
                    self.assertAlmostEqual(vitals.total - vitals.bonus_shields, 400)
                    self.assertAlmostEqual(vitals.bonus_shields, 8.4, places=5)

    def test_projected_barrier_takes_the_round(self):
        run, pilot, hanzo, zarya, _ = self.defender_scene(zarya_barrier.HERO, 16, lateral=4)
        fight = run.match.combat
        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, hanzo.body))
        self.fire(run, pilot, hanzo, 0.8, settle=25)
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (186, hanzo.body))
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertFalse(pilot.client.messages(combat.HIT))
        self.assertEqual(charge.value(), before)

        fight.zarya_barriers.damage(hanzo, 178, pilot.body)
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1], 8)
        self.fire(run, pilot, hanzo, 0.8, settle=25)
        self.assertNotIn(zarya.body, fight.zarya_barriers.projected)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertFalse(pilot.client.messages(combat.HIT))
        self.assertEqual(charge.value(), before)

        self.fire(run, pilot, hanzo, 0.8, settle=25)
        self.assertEqual(fight.vitals[hanzo.body].total, 186)
        self.assertEqual(charge.value(), f32(before + 14))

    def test_in_flight_round_trades_after_pilot_death_without_posthumous_charge(self):
        run, pilot, _, hanzo, magazine = self.arena(12)
        fight = run.match.combat
        pilot_vitals = fight.vitals[pilot.body] = combat.Vitals(pilot.body, 125)
        hanzo_vitals = fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, 14)
        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        px, py, pz = pilot.mover.position
        hx, hy, hz = hanzo.mover.position
        bow = aim(fight._eye(hanzo), (px, py + 0.8, pz))
        gun = aim(fight._eye(pilot), (hx, hy + 0.8, hz))
        for _ in range(70):
            run.step({hanzo: (*bow, 1)})
        run.step({hanzo: (*bow, 0), pilot: (*gun, 1)})
        round_, = (rocket for rocket in fight.rockets if rocket.shooter is pilot)
        for _ in range(12):
            run.step({pilot: (*gun, 0)})
            if pilot_vitals.dead:
                break
        self.assertTrue(pilot_vitals.dead)
        self.assertEqual(pilot_vitals.killer, hanzo.body)
        self.assertIn(round_, fight.rockets)
        self.assertFalse(hanzo_vitals.dead)

        for _ in range(20):
            run.step({pilot: (*gun, 0)})
        self.assertTrue(hanzo_vitals.dead)
        self.assertEqual(hanzo_vitals.killer, pilot.body)
        self.assertEqual(round_.source_entity, pilot.body)
        self.assertEqual(charge.value(), before)
        self.assertEqual(magazine.value(), 19)
        self.assertFalse(fight.rockets)

    def test_reach_boundary_hits_at_99_metres_and_expires_before_101(self):
        for distance, damage in ((99, 14), (101, 0)):
            with self.subTest(distance=distance):
                run, pilot, _, hanzo, _ = self.arena(distance)
                fight = run.match.combat
                vitals = fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, 200)
                charge = pilot.body_script.component.entity_var(ULT_CHARGE)
                before = charge.value()
                rocket = self.fire(run, pilot, hanzo, 0.8, settle=132)
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(vitals.total, 200 - damage)
                self.assertEqual(charge.value(), f32(before + damage))

    def test_held_primary_consumes_magazine_and_reloads_after_release(self):
        run, pilot, _, hanzo, magazine = self.arena()
        fight = run.match.combat
        vitals = fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, 5000)
        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        x, y, z = hanzo.mover.position
        angles = aim(fight._eye(pilot), (x, y + 0.8, z))
        for _ in range(230):
            run.step({pilot: (*angles, 1)})
            if magazine.value() == 0:
                break
        self.assertEqual(magazine.value(), 0)
        self.assertLess(len(pilot.client.messages(combat.HIT)), 20)

        for _ in range(150):
            run.step({pilot: (*angles, 0)})
            if magazine.value() == 20:
                break
        self.assertEqual(magazine.value(), 20)
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 20)
        self.assertEqual(vitals.total, 5000 - 20 * 14)
        run.step({pilot: (*angles, 1)})
        self.assertFalse(fight.rockets)
        for _ in range(100):
            run.step({pilot: (*angles, 0)})
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 20)
        self.assertEqual(magazine.value(), 20)
        before = charge.value()
        self.fire(run, pilot, hanzo, 0.8)
        self.assertEqual(magazine.value(), 19)
        self.assertEqual(vitals.total, 5000 - 21 * 14)
        self.assertEqual(charge.value(), f32(before + 14))

    def test_lethal_hit_credits_effective_loss_and_unlocks_call_mech(self):
        run, pilot, old, hanzo, _ = self.arena()
        fight = run.match.combat
        vitals = fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, 6)
        script = pilot.body_script
        component = script.component
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 5)
        self.fire(run, pilot, hanzo, 0.8)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, pilot.body)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(pilot.client.messages(combat.HIT)[-1]["+0x78"]["+0x4"], 6)

        run.step({pilot: (0, 0, 32)})
        for _ in range(100):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        self.assertIsNotNone(pilot.controlled_mech)
        self.assertIsNot(pilot.controlled_mech, old)
        self.assertIs(pilot.body_script, script)
        self.assertEqual(charge.value(), 0)

    def test_encrypted_bow_demech_and_light_gun_replay(self):
        from dataclasses import replace

        import test_server_journal as journal_tests

        from ow174.game.link import SYN
        from ow174.game.server_journal import _ReplaySession

        base = journal_tests.MAP
        arena = replace(base, free_for_all=False, spawns=(
            base.spawns[0], replace(base.spawns[1], position=(0, 0, 9)),
        ))
        session, sent = journal_tests.ServerJournalTests().make()
        definitions = tuple(sorted(mech_tests.HanzoOwnedMechTests.admitted))
        session.apply("configure_startup_projectile_absorption", {"definitions": definitions})
        picks = matrix.DVA, mech_tests.HANZO
        handoffs = session.create_match(arena, [
            (1, "Pilot", picks[0], 0, False), (2, "Hanzo", picks[1], 1, False),
        ])
        peers = [journal_tests.Peer(session, handoff, sent, ("127.0.0.1", 47650 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        journal_tests.acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        match = session.server.matches[0]
        pilot, hanzo = match.players
        for peer, player, hero in zip(peers, match.players, picks, strict=True):
            peer.message(21616, journal_tests.pick(player.entity, hero), now=now)
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        pilot_body = pilot.body
        self.assertNotEqual(mech.entity, pilot_body)
        pilot_peer, hanzo_peer = peers
        pilot_peer.commands(mech.body_script.frame + 1, 80, 0, now=now)

        fight = match.combat
        x, y, z = mech.position
        yaw, pitch = aim(fight._eye(hanzo), (x, y + 1.1, z))
        for _ in range(6):
            hanzo_peer.commands(hanzo.body_script.frame + 1, 70, 1, yaw=yaw, pitch=pitch, now=now)
            hanzo_peer.commands(hanzo.body_script.frame + 1, 1, 0, yaw=yaw, pitch=pitch, now=now)
            for _ in range(12):
                now += combat.TICK
                session.tick(now)
            if mech.dead:
                break
        self.assertTrue(mech.dead)
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(pilot.body, pilot_body)
        for _ in range(80):
            now += combat.TICK
            session.tick(now)
        pilot_peer.commands(pilot.body_script.frame + 1, 100, 0, now=now)

        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        x, y, z = hanzo.mover.position
        yaw, pitch = aim(fight._eye(pilot), (x, y + 0.8, z))
        pilot_peer.commands(pilot.body_script.frame + 1, 1, 1, yaw=yaw, pitch=pitch, now=now)
        round_, = fight.rockets
        self.assertEqual((round_.source_entity, round_.direct.amount), (pilot_body, 14))
        pilot_peer.commands(pilot.body_script.frame + 1, 1, 0, yaw=yaw, pitch=pitch, now=now)
        for _ in range(20):
            now += combat.TICK
            session.tick(now)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[hanzo.body].total, 186)
        self.assertGreaterEqual(charge.value(), before + 14)

        document = session.finish()
        self.assertEqual(document["state_profile"], "wire-only")
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertEqual(replay.run()["status"], "matched_controlled_roots_and_wire")
        copied = replay.server.matches[0]
        copied_pilot, copied_hanzo = copied.players
        self.assertEqual(copied_pilot.body, pilot_body)
        self.assertIsNone(copied_pilot.controlled_mech)
        self.assertEqual(copied.combat.vitals[copied_hanzo.body].total, 186)
        self.assertEqual(copied_pilot.body_script.component.entity_var(ULT_CHARGE).value(), charge.value())
        self.assertFalse(copied.combat.rockets)
