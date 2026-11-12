"""Actual Ana attacks against a command-deployed 0BD8 barrier."""

import os
import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import orisa_barrier, stats
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import heroes
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get('ORPHEUS_ORISA_GEOMETRY'), 'requires verified Orisa ray mesh')
class AnaOrisaBarrierTests(unittest.TestCase):
    @staticmethod
    def place(player, position):
        player.spawn = position, player.spawn[1]
        player.mover = player.new_mover()

    def deploy(self, *, ally=False, distance=5):
        run, ana, orisa, _ = fixture.AnaRifleTests().setup_fight(enemy=not ally)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[orisa_barrier.HERO]))
        x, y, z = ana.mover.position
        self.place(orisa, (x + 15, y, z))
        for _ in range(30):
            run.step({})
        manager = run.match.combat.orisa_barriers
        run.step({orisa: (0, 0, 16)})
        for _ in range(120):
            run.step({})
            if manager.active:
                break
        [shield] = manager.active.values()
        self.assertEqual(shield.health, 600)
        self.assertEqual(shield.script.graph.index, 0xBD8)
        self.assertEqual(shield.script.vars[40].value(), expr.Entity(orisa.body))
        self.assertEqual(shield.component.entity_var(567).value(), 20)
        x, y, z = shield.position
        self.place(orisa, (x, y, z - 2))
        self.place(ana, (x, y, z + distance))
        run.step({})
        return run, ana, orisa, shield

    @staticmethod
    def vitals(run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def fire(self, arena, button, *, scoped=False):
        run, ana, orisa, _ = arena
        x, y, z = orisa.mover.position
        direction = aim(run.match.combat._eye(ana), (x, y + 1, z))
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2)})
        run.step({ana: (*direction, button | (2 if scoped else 0))})
        for _ in range(66):
            run.step({ana: (*direction, 2 if scoped else 0)})

    def test_all_ana_projectiles_damage_mesh_without_body_status(self):
        for button, scoped, amount in ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50)):
            with self.subTest(button=button, scoped=scoped):
                arena = self.deploy()
                run, ana, orisa, shield = arena
                vitals = self.vitals(run, orisa)
                before = vitals.total
                self.fire(arena, button, scoped=scoped)
                self.assertEqual(shield.health, 600 - amount)
                self.assertEqual(shield.component.entity_var(253).value(), 600 - amount)
                self.assertEqual(vitals.total, before)
                self.assertFalse(run.match.combat.is_asleep(orisa))
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_breaking_grenade_preserves_occlusion_and_near_side_healing(self):
        arena = self.deploy(distance=3)
        run, ana, orisa, shield = arena
        own = self.vitals(run, ana)
        own.damage(150)
        target = self.vitals(run, orisa)
        before = target.total
        shield.health = 40  # isolate the final contact with the real mesh
        self.fire(arena, 16)
        self.assertEqual(shield.health, 0)
        self.assertNotIn(shield.entity, run.match.combat.orisa_barriers.active)
        self.assertEqual(target.total, before)
        self.assertEqual(target.healing_block_until, 0)
        self.assertEqual(own.total, 150)

    def test_same_team_barrier_allows_rifle_heal_and_nano(self):
        arena = self.deploy(ally=True)
        run, ana, orisa, shield = arena
        vitals = self.vitals(run, orisa)
        vitals.damage(200)
        before = vitals.total
        self.fire(arena, 1)
        self.assertEqual(vitals.total, before + 70)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.fire(arena, 32)
        self.assertEqual(vitals.total, sum(vitals.most.values()))
        self.assertGreater(vitals.nano_until, run.match.combat.now)
        self.assertEqual(shield.health, 600)

    def test_friendly_barrier_passes_grenade_and_boosted_scoped_healing(self):
        arena = self.deploy(ally=True)
        run, ana, target, barrier = arena
        vitals = self.vitals(run, target)
        vitals.damage(250, bypass_armour=True)
        before = vitals.total
        for button, scoped, expected in ((16, False, 100), (1, True, 205)):
            self.fire(arena, button, scoped=scoped)
            self.assertEqual(vitals.total, before + expected)
            self.assertGreater(vitals.healing_boost_until, run.match.combat.now)
            self.assertEqual(vitals.healing_block_until, 0)
        self.assertEqual(ana.stats.value(stats.HEALING), 205)
        self.assertEqual(barrier.health, 600)

    def test_original_clock_expiry_stops_collision_before_tail_retires(self):
        run, _, orisa, shield = arena = self.deploy()
        manager = run.match.combat.orisa_barriers
        before = snapshot_digest(export_component_vm(shield.component))
        for _ in range(32):
            run.step({})
        self.assertNotEqual(snapshot_digest(export_component_vm(shield.component)), before)
        for _ in range(1300):
            run.step({})
            if shield.entity not in manager.active:
                break
        self.assertFalse(shield.blocking())
        self.assertIn(shield.entity, manager.retiring)
        vitals = self.vitals(run, orisa)
        before_hp = vitals.total
        self.fire(arena, 1, scoped=True)
        self.assertEqual(vitals.total, before_hp - 70)
        self.assertNotIn(shield.entity, manager.retiring)

    def test_deployed_barrier_preserves_source_identity_after_owner_replacement(self):
        arena = self.deploy()
        run, _, orisa, shield = arena
        source = orisa.body
        position = tuple(orisa.mover.position)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[fixture.ANA]))
        self.assertNotEqual(orisa.body, source)
        self.place(orisa, position)
        run.step({})
        self.fire(arena, 1)
        self.assertEqual(shield.health, 530)
        self.assertEqual(shield.source_body, source)
        self.assertEqual(shield.script.vars[40].value(), expr.Entity(source))
        self.assertEqual(self.vitals(run, orisa).total, 200)

    def test_orisa_deployable_geometry_vm_and_health_export_as_causal_state(self):
        import test_server_snapshot as stored

        from ow174.game import combat

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        fight = player.match.combat
        manager = fight.orisa_barriers
        # Storage fixture for the deployment handoff. Actual E and landing are tested separately.
        rocket = combat.Rocket(player, player.team, (0, 0, 4), (0, 0, 1), 25, 1, None, None,
                               source_entity=player.body, deploy_direction=(0, 0, 1))
        manager.pending.append((fight.tick, rocket))
        pending = stored.snapshot_digest(case.capture(session))
        manager.update(fight.tick)
        [barrier] = manager.active.values()
        self.assertEqual(barrier.health, 600)
        live = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(live, pending)
        manager.damage(barrier, 50)
        self.assertNotEqual(stored.snapshot_digest(case.capture(session)), live)
