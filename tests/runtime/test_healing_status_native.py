"""Original status VMs on the existing sole received-healing host."""
import unittest
from ow174.game import combat
from ow174.game.content import heroes
from ow174.game.script import graph
from test_game_combat import Run

ANA = 0x02E000000000013B

class NativeHealingStatusTests(unittest.TestCase):
    def scene(self):
        run = Run(names=("Ana", "Target"))
        ana, target = run.players[:2]
        self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
        fight = run.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        return run, fight, ana, target, body

    def test_original_application_runs_child_filter_and_expires_on_same_host(self):
        run, fight, ana, target, body = self.scene()
        self.assertTrue(fight.healing.available())
        before = fight.vitals_of(body).total
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        self.assertIs(item.component.world.match.combat.healing, fight.healing)
        self.assertIsNone(item.component.fault)
        self.assertTrue(any(i.graph.index == 0x0C72 for i in item.component.instances.values()))
        self.assertLess(fight.vitals_of(body).total, before)
        self.assertEqual(fight.healing.factor(body), 0)
        self.assertEqual(fight.healing.scale(body.entity), 0)
        for _ in range(251):
            run.step({})
        self.assertEqual(fight.healing.factor(body), 1)
        self.assertEqual(fight.healing.scale(body.entity), 1)

    def test_retire_clears_actual_status_components(self):
        run, fight, ana, target, body = self.scene()
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        fight.healing.retire(body.entity)
        self.assertFalse(item.component.instances)
        self.assertFalse(fight.healing.applied)
        self.assertNotIn(body.entity, fight.healing.filter_history)

    def test_component_storage_tracks_original_status_timer_and_rejects_unknown(self):
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest, BodyVMSnapshotError
        run, fight, ana, target, body = self.scene()
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        before = snapshot_digest(export_component_vm(item.component))
        timer = next(event for instance in item.component.instances.values()
                     for event in instance.queue if event.kind == 1)
        original = timer.time
        timer.time += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(item.component)), before)
        timer.time = original
        self.assertEqual(snapshot_digest(export_component_vm(item.component)), before)
        item.component.world.unregistered = True
        with self.assertRaises(BodyVMSnapshotError):
            export_component_vm(item.component)
        del item.component.world.unregistered
        self.assertEqual(snapshot_digest(export_component_vm(item.component)), before)

    def test_native_filter_sparse_expiry_preserves_same_shield_ledger(self):
        values = []
        for sparse in (False, True):
            run, fight, ana, target, body = self.scene()
            self.assertTrue(run.match.switch_hero(target, heroes()[0x02E0000000000068]))
            run.step({})
            body = next(t for t in fight._targets() if t.owner is target)
            fight.healing.impact(ana, body)
            self.assertEqual(fight.healing.factor(body), 0)
            through = run.frame + 313
            if sparse:
                run.frame = through
                run.match.update(through * combat.TICK, through)
            else:
                for _ in range(313):
                    run.step({})
            self.assertEqual(fight.healing.factor(body), 1)
            values.append(fight.vitals_of(body).total)
        self.assertAlmostEqual(values[0], values[1], places=4)
        self.assertGreater(values[0], 360)
        self.assertLess(values[0], 380)

    def test_actual_server_captures_status_vm_and_filter_history_causally(self):
        import test_server_snapshot as storage
        from ow174.game.server_snapshot import snapshot_digest, ServerSnapshotError
        helper = storage.ServerSnapshotTests()
        session, peers, players = helper.scene(two=True)
        ana, recipient = players[:2]
        self.assertTrue(session.apply("switch_hero", {"account": ana.account_lo, "hero": ANA}))
        fight = ana.match.combat
        body = next(t for t in fight._targets() if t.owner is recipient)
        fight.healing.impact(ana, body)
        item, = fight.healing.applied.values()
        before = snapshot_digest(helper.capture(session))
        launch_team = item.component.world.source_team
        item.component.world.source_team = ana.team
        self.assertNotEqual(snapshot_digest(helper.capture(session)), before)
        item.component.world.source_team = launch_team
        self.assertEqual(snapshot_digest(helper.capture(session)), before)
        original = item.last_frame
        item.last_frame += 1
        self.assertNotEqual(snapshot_digest(helper.capture(session)), before)
        item.last_frame = original
        self.assertEqual(snapshot_digest(helper.capture(session)), before)
        item.component.world.unregistered = True
        with self.assertRaises(ServerSnapshotError):
            helper.capture(session)
        del item.component.world.unregistered
        self.assertEqual(snapshot_digest(helper.capture(session)), before)

    def test_actual_grenade_replaces_carrier_with_one_native_damage_and_cleanse(self):
        from test_zarya_native_cleanse import NativePetApplicationTests
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, caster, victim = NativePetApplicationTests().encounter(projected)
                body = next(t for t in fight._targets() if t.owner is victim)
                self.assertEqual(fight.vitals_of(body).total, (200 if projected else 400) - 60)
                items = [item for item in fight.healing.applied.values() if item.target == victim.body]
                self.assertTrue(items)
                self.assertTrue(any(instance.graph.index == 0x0C72 for item in items
                                    for instance in item.component.instances.values()))
                self.assertNotIn(victim.body, fight.healing.anti)
                self.assertEqual(fight.healing.factor(body), 0)
                self.assertEqual(fight._heal_pools(victim, fight.vitals_of(body), 20), 0)
                fight.cleanse_ana_effects(victim.body)
                self.assertEqual(fight.healing.factor(body), 1)
                self.assertFalse(any(instance.graph.index == 0x0C72 for item in items
                                     for instance in item.component.instances.values()))

    def test_actual_friendly_grenade_heals_once_and_runs_original_amp_child(self):
        from test_zarya_native_cleanse import ZaryaNativeCleanseTests
        fixture = ZaryaNativeCleanseTests()
        run, fight, ana, caster, victim = fixture.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
        ana.mover.position[:] = [0, 1, 0]
        ana.mover.body = None
        ana.team = victim.team
        body = next(t for t in fight._targets() if t.owner is victim)
        fight.vitals_of(body).damage(150)
        fight._sync_body_health(victim, fight.vitals_of(body))
        fixture.throw_biotic_grenade(run, ana, victim)
        self.assertEqual(fight.vitals_of(body).total, 350)
        self.assertTrue(any(instance.graph.index == 0x0B99 for item in fight.healing.applied.values()
                            if item.target == victim.body for instance in item.component.instances.values()))
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.healing.factor(body), 1.5)

    def test_actual_npc_death_immediately_retires_status_components(self):
        from test_game_mercy_healing_status import MercyHealingStatusTests
        from ow174.game import npc
        import test_game_mercy_chains as chains
        fixture = MercyHealingStatusTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        catalog = npc.load_catalog(chains.npc_tests.NPC_DATA / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                bot = npc.spawn(fixture.match, definition, (0, 1, 8), team=0)
                target = next(t for t in fixture.fight._targets() if t.owner is bot)
                fixture.ana.team = 0
                fixture.fight.healing.impact(fixture.ana, target)
                item = fixture.fight.healing.applied[(fixture.ana.body, bot.entity)]
                fixture.ana.team = 1
                fixture.fight._damage(fixture.ana, target, 10000, False, None, fixture.run.frame)
                self.assertFalse(item.component.instances)
                self.assertNotIn((fixture.ana.body, bot.entity), fixture.fight.healing.applied)
                npc.remove_spawned(fixture.match)

    def test_actual_matrix_strength_scales_native_instant_event_but_not_filter(self):
        import test_baptiste_healing_status as original
        from test_baptiste_matrix import deploy_matrix
        from test_game_combat import aim
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                fixture = original.BaptisteHealingStatusTests()
                fixture.arena(friendly=friendly)
                self.addCleanup(fixture.doCleanups)
                fixture.bap.team = fixture.ana.team
                matrix = deploy_matrix(fixture.run, fixture.bap)
                matrix.position = (0.0, 2.0, 6.0)
                matrix.rotation = (0.0, 0.0, 0.0, 1.0)
                if friendly:
                    fixture.injure(180)
                target = fixture.targets[fixture.ally]
                angles = aim(fixture.fight._eye(fixture.ana),
                             (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
                for frame in range(60):
                    fixture.step({fixture.ana: (*angles, 16 if frame == 0 else 0)})
                    if any(item.target == target.entity for item in fixture.fight.healing.applied.values()):
                        break
                else:
                    self.fail("actual matrix-crossed grenade did not reach the native status host")
                self.assertEqual(fixture.health(), 200 if friendly else 80)
                for _ in range(5):
                    fixture.step()
                self.assertEqual(fixture.fight.healing.factor(target), 1.5 if friendly else 0)

    def test_in_flight_grenade_keeps_existing_launch_team_after_source_changes_team(self):
        from test_zarya_native_cleanse import ZaryaNativeCleanseTests
        from test_game_combat import aim
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                fixture = ZaryaNativeCleanseTests()
                run, fight, ana, caster, victim = fixture.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
                ana.mover.position[:] = [0, 1, 0]
                ana.mover.body = None
                ana.team = victim.team if friendly else 1 - victim.team
                target = next(t for t in fight._targets() if t.owner is victim)
                if friendly:
                    fight.vitals_of(target).damage(150)
                    fight._sync_body_health(victim, fight.vitals_of(target))
                before = fight.vitals_of(target).total
                angles = aim(fight._eye(ana), (victim.mover.position[0], victim.mover.position[1] + 1,
                                               victim.mover.position[2]))
                for frame in range(60):
                    run.step({ana: (*angles, 16 if frame == 0 else 0)})
                    rocket = next((r for r in fight.rockets if r.definition == 0x0400000000001426), None)
                    if rocket is not None:
                        break
                else:
                    self.fail("actual Ana grenade did not launch")
                launch_team = rocket.team
                ana.team = 1 - ana.team
                self.assertNotEqual(launch_team, ana.team)
                for _ in range(60):
                    run.step({})
                    if rocket not in fight.rockets:
                        break
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(fight.vitals_of(target).total, before + (100 if friendly else -60))
                expected_graph = 0x0B99 if friendly else 0x0C72
                self.assertTrue(any(instance.graph.index == expected_graph for item in fight.healing.applied.values()
                                    if item.target == target.entity for instance in item.component.instances.values()))
