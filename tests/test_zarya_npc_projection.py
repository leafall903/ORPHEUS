"""Native025C binds actual NPC recipient/component and the single defensive Pet pool."""
import unittest
import test_ashe_pve_npcs as fixtures
from test_game_combat import aim, in_world
from ow174.game import combat, symmetra_turrets
from ow174.game.content import SOLDIER, heroes

ZARYA = 0x02E0000000000068

class ZaryaNpcProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AshePveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.AshePveNpcTests.tearDownClass()

    def scene(self, definition):
        run, caster, recipient = fixtures.AshePveNpcTests().spawn(definition, 4, ally=True)
        point = tuple(caster.mover.position)
        self.assertTrue(run.match.switch_hero(caster, heroes()[ZARYA]))
        caster.mover.position[:] = point
        caster.mover.body = None
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is recipient)
        a, b = target.segment()
        direction = aim(fight._eye(caster), tuple((lo + hi) / 2 for lo, hi in zip(a, b)))
        run.step({caster: (*direction, 16)})
        pet = fight.barriers.authored_for(caster.body)
        self.assertIsNotNone(pet)
        self.assertIs(pet.recipient, recipient)
        self.assertEqual(pet.recipient_body, recipient.entity)
        self.assertIs(pet.recipient_component, recipient.body_script.component)
        self.assertEqual(pet.vitals.total, 200)
        self.assertIs(symmetra_turrets._single_replay_projection(fight), recipient)
        enemy = run.match.add_player(9, "Incoming", SOLDIER, 9, False)
        in_world(run.match, enemy)
        enemy.team = 1 - caster.team
        self.assertTrue(run.match.switch_hero(enemy, heroes()[SOLDIER]))
        return run, caster, recipient, pet, target, enemy

    def test_five_npcs_receive_one_real_pool_and_expire_on_original_clock(self):
        for definition in fixtures.AshePveNpcTests.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, recipient, pet, target, enemy = self.scene(definition)
                fight = run.match.combat
                body_vm = recipient.body_script.component
                body_pool = fight.vitals_of(target)
                before = body_pool.total
                self.assertFalse(fight._damage(enemy, target, 60, False, None, fight.tick))
                self.assertEqual(body_pool.total, before)
                self.assertEqual(pet.vitals.total, 140)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 12)
                self.assertIs(recipient.body_script.component, body_vm)
                self.assertIs(fight.vitals_of(target), body_pool)
                expiry = fight.barriers.expiries[caster.body, True]
                end = expiry.tick + 2
                run.match.update(end * combat.TICK, end, frames=False)
                self.assertNotIn(pet.entity, fight.barriers.scripted)
                self.assertIs(recipient.body_script.component, body_vm)
                self.assertEqual(body_pool.total, before)

    def test_actual_recipient_death_retires_projection_without_another_pool(self):
        definition = next(iter(fixtures.AshePveNpcTests.catalog.values()))
        run, caster, recipient, pet, target, enemy = self.scene(definition)
        fight = run.match.combat
        # Native/direct body death uses the same actual Vitals and existing
        # death dispatcher, independently of the finite interception pool.
        fight.vitals_of(target).damage(10000)
        fight._bot_died(enemy, recipient, False, None, fight.tick)
        self.assertTrue(recipient.dead)
        self.assertNotIn(pet.entity, fight.barriers.scripted)


    def test_team_change_and_actual_removal_retire_the_same_projected_actor(self):
        from ow174.game import npc

        definition = next(iter(fixtures.AshePveNpcTests.catalog.values()))
        for reason in ("team", "remove"):
            with self.subTest(reason=reason):
                run, caster, recipient, pet, target, enemy = self.scene(definition)
                fight = run.match.combat
                if reason == "team":
                    recipient.team = 1 - caster.team
                    run.step({})
                else:
                    npc.remove_body(run.match, recipient)
                self.assertNotIn(pet.entity, fight.barriers.scripted)
                self.assertIsNone(symmetra_turrets._single_replay_projection(fight))

    def test_actual_server_encloses_projection_recipient_and_native_pet_storage(self):
        import dataclasses
        import test_server_snapshot as storage
        from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest

        helper = storage.ServerSnapshotTests()
        session, peers, players = helper.scene()
        caster = players[0]
        match = caster.match
        self.assertTrue(session.apply("switch_hero", {"account": caster.account_lo, "hero": ZARYA}))
        point = tuple(caster.mover.position)
        definition = next(iter(fixtures.AshePveNpcTests.catalog.values()))
        entity = session.apply("spawn_manual_npc", {
            "match_id": match.id, "definition": dataclasses.asdict(definition),
            "position": (point[0], point[1], point[2] + 4), "team": caster.team, "yaw": 0,
        })
        fight = match.combat
        recipient = next(bot for bot in match.bots if bot.entity == entity)
        target = next(target for target in fight._targets() if target.owner is recipient)
        a, b = target.segment()
        direction = aim(fight._eye(caster), tuple((lo + hi) / 2 for lo, hi in zip(a, b)))
        peers[0].commands(caster.body_script.frame + 1, 1, 16, yaw=direction[0], pitch=direction[1])
        pet = fight.barriers.authored_for(caster.body)
        self.assertIsNotNone(pet)
        self.assertIs(pet.recipient_component, recipient.body_script.component)
        self.assertIs(symmetra_turrets._single_replay_projection(fight), recipient)
        before = snapshot_digest(helper.capture(session))
        pet.vitals.damage(1)
        self.assertNotEqual(snapshot_digest(helper.capture(session)), before)
        pet.vitals.heal(1)
        self.assertEqual(snapshot_digest(helper.capture(session)), before)
        original = pet.recipient_component
        pet.recipient_component = caster.body_script.component
        self.assertNotEqual(snapshot_digest(helper.capture(session)), before)
        pet.recipient_component = original
        self.assertEqual(snapshot_digest(helper.capture(session)), before)
        pet.unregistered = True
        with self.assertRaises(ServerSnapshotError):
            helper.capture(session)
        del pet.unregistered
        self.assertEqual(snapshot_digest(helper.capture(session)), before)
