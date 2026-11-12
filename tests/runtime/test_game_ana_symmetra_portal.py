"""Actual Symmetra placement and Ana combat against the teleporter pair."""

import unittest

import test_game_ana as fixture
import test_game_ana_baptiste_field as field_fixture
import test_game_ana_projected_barrier as barrier_fixture
import test_game_ana_symmetra_sentry as sentry_fixture
from test_game_combat import aim

from ow174.game.commands import Command, FIRE
from ow174.game.content import find_hero
from ow174.game.script import expr


class AnaSymmetraPortalTests(unittest.TestCase):
    def arena(self, *, enemy=True):
        return sentry_fixture.AnaSymmetraSentryTests().arena(enemy=enemy)

    def deploy(self, run, symmetra):
        fight = run.match.combat
        x, y, z = symmetra.mover.position
        direction = aim(fight._eye(symmetra), (x, y, z + 15))
        run.step({symmetra: (*direction, 16)})
        instance = next(item for item in symmetra.body_script.component.instances.values()
                        if item.graph.index == 0x1571)
        preview = instance.node_state(instance.graph.node(69))
        self.assertTrue(preview.active)
        run.step({symmetra: (*direction, 0)})
        run.step({symmetra: (*direction, FIRE)})
        self.assertFalse(preview.active)
        pair = fight.symmetra_portals.pairs[symmetra.body]
        self.assertIs(pair.instance, instance)
        self.assertTrue(instance.node_state(instance.graph.node(2)).active)
        for variable, endpoint in zip((1624, 1625), pair.endpoints, strict=True):
            self.assertEqual(instance.component.entity_var(variable).value(), expr.Entity(endpoint.body))
            self.assertEqual(endpoint.vitals.now, {0: 50, 1: 0, 2: 250})
        return pair

    @staticmethod
    def ready(run, pair):
        while run.match.combat.now < pair.ready:
            run.step({})

    def test_ana_transits_after_build_and_can_return_after_use_cooldown(self):
        run, ana, symmetra, _ = self.arena(enemy=False)
        pair = self.deploy(run, symmetra)
        fight = run.match.combat
        destination, entrance = pair.endpoints
        field_fixture.AnaBaptisteFieldTests.place(ana, entrance.position)
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], entrance.position[2], places=2)
        self.ready(run, pair)
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], destination.position[2], places=2)
        instance, until = fight.symmetra_portals.transits[ana.body]
        self.assertEqual(instance.graph.index, 0x1647)
        run.step({})
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], destination.position[2], places=2)
        while fight.now < until:
            run.step({})
        self.assertTrue(instance.stopped)
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], entrance.position[2], places=2)

    def test_enemy_ana_cannot_use_either_end(self):
        run, ana, symmetra, _ = self.arena()
        pair = self.deploy(run, symmetra)
        self.ready(run, pair)
        for endpoint in pair.endpoints:
            field_fixture.AnaBaptisteFieldTests.place(ana, endpoint.position)
            run.step({})
            run.step({ana: (0, 0, 4)})
            self.assertAlmostEqual(ana.mover.position[2], endpoint.position[2], places=2)

    def test_too_close_confirmation_preserves_preview_for_valid_placement(self):
        run, _, symmetra, _ = self.arena()
        fight = run.match.combat
        x, y, z = symmetra.mover.position
        close = aim(fight._eye(symmetra), (x, y, z + 1))
        for button in (16, 0, FIRE, 0):
            run.step({symmetra: (*close, button)})
        instance = next(item for item in symmetra.body_script.component.instances.values()
                        if item.graph.index == 0x1571)
        preview = instance.node_state(instance.graph.node(69))
        self.assertTrue(preview.active)
        self.assertFalse(fight.symmetra_portals.pairs)
        valid = aim(fight._eye(symmetra), (x, y, z + 15))
        run.step({symmetra: (*valid, FIRE)})
        self.assertFalse(preview.active)
        self.assertIn(symmetra.body, fight.symmetra_portals.pairs)

    def test_friendly_endpoints_do_not_take_ana_weapon_damage(self):
        run, ana, symmetra, _ = self.arena(enemy=False)
        pair = self.deploy(run, symmetra)
        destination, entrance = pair.endpoints
        x, y, z = destination.position
        field_fixture.AnaBaptisteFieldTests.place(ana, (x, y, z + 5))
        for button, scoped in ((FIRE, False), (FIRE, True), (8, False), (16, False)):
            field_fixture.AnaBaptisteFieldTests().fire(run, ana, destination.centre(), button, scoped=scoped)
        self.assertEqual(destination.vitals.total, 300)
        self.assertEqual(entrance.vitals.total, 300)

    def test_source_cover_and_blocked_destination_deny_transit(self):
        run, ana, symmetra, _ = self.arena(enemy=False)
        pair = self.deploy(run, symmetra)
        self.ready(run, pair)
        destination, entrance = pair.endpoints
        x, y, z = entrance.position
        source = x, y, z - 1
        place = field_fixture.AnaBaptisteFieldTests.place
        place(ana, source)
        world = run.match.collision_world
        fixture.set_static_wall(run, ana, .5)
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], source[2], places=2)
        run.step({})
        place(ana, destination.position)
        fixture.set_static_wall(run, ana, 0)
        place(ana, source)
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], source[2], places=2)
        run.match.collision_world = world
        run.step({})
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], destination.position[2], places=2)

    def test_actual_sleep_dart_prevents_owner_transit_until_wake(self):
        run, ana, symmetra, _ = self.arena()
        pair = self.deploy(run, symmetra)
        self.ready(run, pair)
        fight = run.match.combat
        destination, entrance = pair.endpoints
        x, y, z = entrance.position
        place = field_fixture.AnaBaptisteFieldTests.place
        place(symmetra, (x + 1, y, z))
        place(ana, (x + 1, y, z - 4))
        direction = aim(fight._eye(ana), (x + 1, y + 1, z))
        run.step({ana: (*direction, 8)})
        for _ in range(70):
            run.step({})
            if fight.is_asleep(symmetra):
                break
        self.assertTrue(fight.is_asleep(symmetra))
        run.step({symmetra: (0, 0, 4)})
        self.assertAlmostEqual(symmetra.mover.position[2], z, places=2)
        for _ in range(400):
            if not fight.is_asleep(symmetra):
                break
            run.step({})
        self.assertFalse(fight.is_asleep(symmetra))
        run.step({symmetra: (0, 0, 4)})
        self.assertAlmostEqual(symmetra.mover.position[2], destination.position[2], places=2)

    def test_scoped_rifle_rewinds_to_the_correct_side_of_transit(self):
        for delay, aim_at_exit, damage in ((1, False, 70), (0, False, 0), (0, True, 70)):
            with self.subTest(delay=delay, aim_at_exit=aim_at_exit):
                run, ana, symmetra, _ = self.arena()
                pair = self.deploy(run, symmetra)
                fight = run.match.combat
                self.ready(run, pair)
                destination, entrance = pair.endpoints
                x, y, z = entrance.position
                place = field_fixture.AnaBaptisteFieldTests.place
                place(symmetra, (x + .8, y, z))
                place(ana, (x + 5, y, (z + destination.position[2]) / 2))
                for _ in range(35):
                    run.step({ana: (0, 0, 2)})
                old = tuple(symmetra.mover.position)
                target = next(item for item in fight._targets() if item.owner is symmetra)
                vitals = fight.vitals_of(target)
                initial = vitals.total
                run.frame += 1
                run.match.update(run.frame * .016, run.frame)
                symmetra.take_commands([Command(run.frame, buttons=4)])
                self.assertAlmostEqual(symmetra.mover.position[2], destination.position[2], places=2)
                tx, ty, tz = symmetra.mover.position if aim_at_exit else old
                yaw, pitch = aim(fight._eye(ana), (tx, ty + 1.55, tz))
                ana.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=3, extra=delay)])
                for _ in range(65):
                    run.step({})
                self.assertEqual(vitals.total, initial - damage)
                self.assertEqual([endpoint.vitals.total for endpoint in pair.endpoints], [300, 300])

    def test_enemy_endpoints_take_actual_rifle_dart_grenade_and_melee(self):
        for buttons, scoped, damage in ((FIRE, False, 70), (FIRE, True, 70), (8, False, 5),
                                         (16, False, 50), (0x800, False, 30)):
            with self.subTest(buttons=buttons, scoped=scoped):
                run, ana, symmetra, _ = self.arena()
                pair = self.deploy(run, symmetra)
                destination, entrance = pair.endpoints
                x, y, z = destination.position
                field_fixture.AnaBaptisteFieldTests.place(ana, (x, y, z + (1.45 if buttons == 0x800 else 5)))
                field_fixture.AnaBaptisteFieldTests().fire(run, ana, destination.centre(), buttons, scoped=scoped)
                self.assertEqual(destination.vitals.total, 300 - damage)
                self.assertEqual(entrance.vitals.total, 300)

    def test_destroying_either_endpoint_retires_both(self):
        for index in (0, 1):
            with self.subTest(index=index):
                run, ana, symmetra, _ = self.arena()
                pair = self.deploy(run, symmetra)
                target = pair.endpoints[index]
                x, y, z = target.position
                place = field_fixture.AnaBaptisteFieldTests.place
                place(symmetra, (x + 3, y, z))
                place(ana, (x, y, z + 5))
                target.vitals.damage(240)
                field_fixture.AnaBaptisteFieldTests().fire(run, ana, target.centre(), FIRE, scoped=True)
                self.assertTrue(target.vitals.dead)
                self.assertNotIn(symmetra.body, run.match.combat.symmetra_portals.pairs)
                for variable in (1624, 1625):
                    self.assertEqual(pair.instance.component.entity_var(variable).value(), expr.Entity(0))

    def test_owner_replacement_retires_pair(self):
        run, _, symmetra, _ = self.arena()
        pair = self.deploy(run, symmetra)
        old = symmetra.body
        self.assertTrue(run.match.switch_hero(symmetra, find_hero("Soldier: 76")))
        self.assertNotIn(old, run.match.combat.symmetra_portals.pairs)
        self.assertEqual(pair.instance.component.entity_var(1624).value(), expr.Entity(0))

    def kill_owner(self, run, ana, symmetra, pair):
        fight = run.match.combat
        x, y, z = pair.endpoints[1].position
        place = field_fixture.AnaBaptisteFieldTests.place
        place(symmetra, (x + 3, y, z))
        place(ana, (x + 3, y, z + 5))
        target = next(item for item in fight._targets() if item.owner is symmetra)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 60)
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, (x + 3, y + 1, z), FIRE, scoped=True)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, ana.body)
        return next(when for when, owner in fight.respawns if owner is symmetra)

    def test_pair_survives_an_actual_ana_kill_and_same_hero_respawn(self):
        run, ana, symmetra, _ = self.arena()
        pair = self.deploy(run, symmetra)
        old_body, old_instance = symmetra.body, pair.instance
        fight = run.match.combat
        respawn = self.kill_owner(run, ana, symmetra, pair)
        self.assertIs(fight.symmetra_portals.pairs[old_body], pair)
        while fight.now < respawn:
            run.step({})
        self.assertNotEqual(symmetra.body, old_body)
        self.assertNotIn(old_body, fight.symmetra_portals.pairs)
        self.assertIs(fight.symmetra_portals.pairs[symmetra.body], pair)
        self.assertIsNot(pair.instance, old_instance)
        self.assertEqual(pair.instance.component.entity, symmetra.body)
        for variable, endpoint in zip((1624, 1625), pair.endpoints, strict=True):
            self.assertEqual(pair.instance.component.entity_var(variable).value(), expr.Entity(endpoint.body))
        destination, entrance = pair.endpoints
        field_fixture.AnaBaptisteFieldTests.place(symmetra, entrance.position)
        run.step({symmetra: (0, 0, 4)})
        self.assertAlmostEqual(symmetra.mover.position[2], destination.position[2], places=2)

    def test_pair_destruction_while_owner_dead_carries_cooldown_through_respawn(self):
        run, ana, symmetra, _ = self.arena()
        pair = self.deploy(run, symmetra)
        old_body = symmetra.body
        fight = run.match.combat
        respawn = self.kill_owner(run, ana, symmetra, pair)
        while fight.now < respawn - 2:
            run.step({})
        endpoint = pair.endpoints[0]
        x, y, z = endpoint.position
        field_fixture.AnaBaptisteFieldTests.place(ana, (x, y, z + 5))
        endpoint.vitals.damage(240)
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, endpoint.centre(), FIRE, scoped=True)
        self.assertTrue(endpoint.vitals.dead)
        self.assertTrue(fight.is_dead(symmetra))
        deadline = fight.symmetra_portals.cooldowns[old_body]
        while fight.now < respawn:
            run.step({})
        self.assertNotEqual(symmetra.body, old_body)
        self.assertNotIn(old_body, fight.symmetra_portals.cooldowns)
        self.assertEqual(fight.symmetra_portals.cooldowns[symmetra.body], deadline)
        self.assertFalse(fight.symmetra_portals.pairs)
        x, y, z = symmetra.mover.position
        direction = aim(fight._eye(symmetra), (x, y, z + 15))
        instance = next(item for item in symmetra.body_script.component.instances.values()
                        if item.graph.index == 0x1571)
        preview = instance.node_state(instance.graph.node(69))
        for button in (16, 0, FIRE, 0):
            run.step({symmetra: (*direction, button)})
        self.assertFalse(fight.symmetra_portals.pairs)
        self.assertFalse(preview.active)
        while fight.now < deadline:
            run.step({})
        for button in (16, 0, FIRE):
            run.step({symmetra: (*direction, button)})
        self.assertIn(symmetra.body, fight.symmetra_portals.pairs)

    def test_friendly_ana_can_transit_while_symmetra_is_dead(self):
        helper = barrier_fixture.AnaProjectedBarrierTests()
        run, ana, symmetra, soldier = helper.fight(ally_ana=True)
        self.assertTrue(run.match.switch_hero(symmetra, find_hero("Symmetra")))
        soldier.team = 0
        x, y, z = ana.mover.position
        place = field_fixture.AnaBaptisteFieldTests.place
        place(symmetra, (x + 4, y, z + 5))
        for _ in range(25):
            run.step({})
        pair = self.deploy(run, symmetra)
        self.ready(run, pair)
        fight = run.match.combat
        destination, entrance = pair.endpoints
        x, y, z = entrance.position
        place(symmetra, (x + 3, y, z))
        place(soldier, (x + 3, y, z + 5))
        target = fight.vitals_of(helper.entry(run, symmetra))
        target.damage(target.total - 5)
        direction = aim(fight._eye(soldier), (x + 3, y + 1, z))
        run.step({soldier: (*direction, FIRE)})
        for _ in range(30):
            if target.dead:
                break
            run.step({})
        self.assertTrue(target.dead)
        place(ana, entrance.position)
        run.step({ana: (0, 0, 4)})
        self.assertAlmostEqual(ana.mover.position[2], destination.position[2], places=2)
        self.assertTrue(fight.is_dead(symmetra))

    def test_ana_hits_restart_only_the_hit_endpoint_shield_recovery_delay(self):
        run, ana, symmetra, _ = self.arena()
        pair = self.deploy(run, symmetra)
        endpoint, other = pair.endpoints
        fight = run.match.combat
        x, y, z = endpoint.position
        field_fixture.AnaBaptisteFieldTests.place(ana, (x, y, z + 5))
        direction = aim(fight._eye(ana), endpoint.centre())

        def scoped_hit():
            for _ in range(35):
                run.step({ana: (*direction, 2)})
            before = endpoint.vitals.total
            run.step({ana: (*direction, 3)})
            self.assertAlmostEqual(endpoint.vitals.total, before - 70, delta=.5)
            return fight.now

        first = scoped_hit()
        while fight.now < first + 2.9:
            run.step({})
        self.assertEqual(endpoint.vitals.now[2], 180)
        run.frame += 70
        run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertAlmostEqual(endpoint.vitals.now[2], 180 + (fight.now - first - 3) * 30)
        second = scoped_hit()
        after_second = endpoint.vitals.now[2]
        while fight.now < second + 2.9:
            run.step({})
        self.assertEqual(endpoint.vitals.now[2], after_second)
        run.frame += 425
        run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertEqual(endpoint.vitals.now, {0: 50, 1: 0, 2: 250})
        self.assertEqual(other.vitals.total, 300)

    def test_owner_dismissal_starts_the_authored_redeployment_delay(self):
        run, _, symmetra, _ = self.arena()
        pair = self.deploy(run, symmetra)
        self.ready(run, pair)
        fight = run.match.combat
        run.step({symmetra: (0, 0, 16)})
        self.assertFalse(fight.symmetra_portals.pairs)
        deadline = fight.symmetra_portals.cooldowns[symmetra.body]
        self.assertAlmostEqual(deadline - fight.now, 10)
        x, y, z = symmetra.mover.position
        direction = aim(fight._eye(symmetra), (x, y, z + 15))
        for button in (0, 16, 0, FIRE, 0):
            run.step({symmetra: (*direction, button)})
        self.assertFalse(fight.symmetra_portals.pairs)
        while fight.now < deadline:
            run.step({})
        run.step({symmetra: (*direction, FIRE)})
        self.assertIn(symmetra.body, fight.symmetra_portals.pairs)

    def test_endpoint_shields_and_ready_time_enter_the_server_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        symmetra = players[1]
        peers[1].message(21616, stored.pick(symmetra.entity, find_hero("Symmetra").guid), now=100.8)
        for button in (16, 0, FIRE, 0):
            peers[1].commands(symmetra.body_script.frame + 1, 4, button)
        fight = symmetra.match.combat
        pair = fight.symmetra_portals.pairs[symmetra.body]
        before = stored.snapshot_digest(case.capture(session))
        pair.endpoints[0].vitals.damage(5)
        changed = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, changed)
        pair.ready += .016
        ready_changed = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(changed, ready_changed)
        pair.endpoints[0].recover_at = fight.now + 3
        self.assertNotEqual(ready_changed, stored.snapshot_digest(case.capture(session)))

    def test_actual_portal_pair_survives_stored_replay(self):
        import test_server_snapshot as stored

        session, sent = stored.StoredJournalTests().make()
        handoff, = session.create_match(stored.MAP, [(1, "Symmetra", stored.SOLDIER, 0, False)])
        peer = stored.Peer(session, handoff, sent, ("127.0.0.1", 47210))
        peer.send(flags=stored.SYN)
        peer.send()
        peer.message(21601, {})
        stored.acknowledge_ownership(session, (peer,))
        session.tick(100.8)
        symmetra, = session.server.matches[0].players
        peer.message(21616, stored.pick(symmetra.entity, find_hero("Symmetra").guid), now=100.8)
        for button in (16, 0, FIRE, 0):
            peer.commands(symmetra.body_script.frame + 1, 4, button)
        pair = symmetra.match.combat.symmetra_portals.pairs[symmetra.body]
        expected = tuple((endpoint.body, endpoint.position, endpoint.vitals.total) for endpoint in pair.endpoints)
        document = session.finish()
        replay = stored._ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied, = replay.server.matches[0].players
        pair = copied.match.combat.symmetra_portals.pairs[copied.body]
        self.assertEqual(tuple((endpoint.body, endpoint.position, endpoint.vitals.total)
                               for endpoint in pair.endpoints), expected)
