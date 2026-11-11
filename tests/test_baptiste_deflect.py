"""Actual Baptiste/Genji input with explicit003 admission and static pose hosts."""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_baptiste_combat as fixture
from test_baptiste_matrix import deploy_matrix
from test_game_combat import aim

from ow174.game import combat, deflection, mover, npc, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class _Fixture:
    @classmethod
    def setUpClass(cls):
        known = deflection.catalog()
        if (not known.admitted(0x0400000000002CFA) or not known.admitted(0x04000000000029AD)
                or known.admitted(0x04000000000029A8)):
            raise RuntimeError('requires explicit verified2CFA/29AD/29A8 projectile-tag profile')

    def arena(self, distance=8, rear=False):
        args = fixture.BaptisteCombatTests().setup_fight()
        self.run, self.bap, self.ally, self.genji, self.fight, self.targets, self.weapon = args
        self.run.match.switch_hero(self.genji, heroes()[deflection.GENJI])
        self.genji.mover.position[:] = (0, 1, distance)
        self.ally.mover.position[:] = (20, 1, 4)
        for actor in self.run.players:
            actor.mover.body = None  # static pose; native movement is a separate acceptance gate
            actor.body_script.ult_seconds = 0
        self.guard_aim = (0 if rear else -32768, 0)
        self.target = next(t for t in self.fight._targets() if t.owner is self.genji)
        self.deflect = next(i for i in self.genji.body_script.component.instances.values()
                            if i.graph.index == 0x03A3)

    def step(self, bap=0, genji=0, count=1, aim_bap=(0, 0)):
        for _ in range(count):
            with patch.object(self.fight.projectiles, 'fired', return_value=None):
                self.run.step({self.bap: (*aim_bap, bap), self.genji: (*self.guard_aim, genji)})

    def start(self):
        self.step(genji=16)
        self.step(count=4)
        self.assertTrue(self.deflect.state(10).active)

    def hp(self, owner):
        target = next(t for t in self.fight._targets() if t.owner is owner)
        return self.fight.vitals_of(target).total

    def charge(self):
        return self.bap.body_script.component.vars[342].value()

    def returning_heal_target(self, owner):
        owner.team = self.genji.team
        owner.mover.position[:] = (0, 1, 4)
        target = next(t for t in self.fight._targets() if t.owner is owner)
        self.fight.vitals_of(target).damage(180)  # explicit injury, no enemy-charge eligibility claim
        self.guard_aim = aim(self.fight._eye(self.genji),
                             (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        return target


class BaptisteDeflectTests(_Fixture, unittest.TestCase):
    def test_authored_box_can_return_capsule_miss_but_lateral_outside_keeps_original_flight(self):
        for lateral, expected in ((.65, 1), (1.0, 0)):
            with self.subTest(lateral=lateral):
                self.arena()
                self.bap.mover.position[0] = lateral
                self.start()
                a, b = self.target.segment()
                self.assertIsNone(combat.ray_capsule(self.fight._eye(self.bap), (0, 0, 1),
                                                    a, b, self.target.radius))
                self.step(bap=2)
                self.step(count=12)
                rocket, = self.fight.rockets
                self.assertEqual(rocket.deflections, expected)
                self.assertIs(rocket.shooter, self.genji if expected else self.bap)
                self.assertEqual(self.hp(self.genji), 200)

    def test_two_opposing_active_guards_do_not_turn_cycle_bound_into_damage(self):
        self.arena()
        self.run.match.switch_hero(self.ally, heroes()[deflection.GENJI])
        self.ally.mover.position[:] = (0, 1, 2)
        self.ally.mover.body = None
        self.ally.body_script.ult_seconds = 0
        self.run.step({self.ally: (0, 0, 16), self.genji: (*self.guard_aim, 16)})
        self.step(count=4)
        self.step(bap=1)
        self.step(count=25)
        self.assertTrue(self.deflect.state(10).active)
        self.assertEqual([self.hp(p) for p in self.run.players], [200, 200, 200])
        self.assertEqual(self.charge(), 0)
        self.assertTrue(all(not p.client.messages(combat.HIT) for p in self.run.players))

    def test_unknown_definition_metadata_does_not_guess_reflection(self):
        self.arena()
        self.start()
        self.fight.projectile_tags = deflection.ProjectileTags(json.dumps({
            'version': 1, 'build': '1.74.0.0.104319', 'entities': {},
        }).encode())
        self.step(bap=1)
        self.assertEqual(self.hp(self.genji), 176)
        self.assertEqual(self.hp(self.bap), 200)

    def test_actual_three_round_burst_returns_damage_credit_and_spends_original_ammo(self):
        self.arena()
        self.start()
        ammo = self.weapon.vars[53].value()
        self.step(bap=1)
        self.step(count=25)
        self.assertEqual(self.hp(self.genji), 200)
        self.assertEqual(self.hp(self.bap), 128)
        self.assertEqual(ammo - self.weapon.vars[53].value(), 3)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.genji.stats.values[stats.HERO_DAMAGE], 72)
        self.assertEqual(self.bap.client.messages(combat.HIT), [])
        self.assertEqual(len(self.genji.client.messages(combat.HIT)), 3)

    def test_returned_primary_kill_belongs_to_deflector(self):
        self.arena()
        self.start()
        self.fight.vitals_of(self.targets[self.bap]).damage(190)
        self.step(bap=1)
        vitals = self.fight.vitals[self.bap.body]
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.genji.body)
        self.assertEqual(self.genji.stats.values[stats.ELIMINATIONS], 1)
        self.assertEqual(self.charge(), 0)

    def test_actual_secondary_returns_as_healing_for_deflectors_team(self):
        self.arena()
        self.returning_heal_target(self.ally)
        self.start()
        self.step(bap=2)
        self.step(count=40)
        self.assertEqual(self.hp(self.ally), 90)
        self.assertEqual(self.genji.stats.values[stats.HEALING], 70)
        self.assertEqual(self.bap.stats.values.get(stats.HEALING, 0), 0)
        self.assertEqual(self.charge(), 0)
        self.assertFalse(self.fight.rockets)

    def test_secondary_residual_flight_and_defense_deadline_agree_across_scheduler_gaps(self):
        for late in (False, True):
            results = []
            for gap in (1, 30):
                with self.subTest(late=late, gap=gap):
                    self.arena()
                    self.returning_heal_target(self.ally)
                    self.start()
                    if late:
                        self.step(count=115)  # E remains live, but the incoming crossing is after2s.
                    self.step(bap=2)
                    self.step()  # admit the flight before comparing elapsed world intervals
                    base = self.run.frame
                    for delta in (range(1, 31) if gap == 1 else (30,)):
                        self.run.match.update((base + delta) * .016, base + delta)
                    results.append(self.hp(self.ally))
                    self.assertEqual(results[-1], 20 if late else 90)
                    self.assertEqual(self.genji.stats.values.get(stats.HEALING, 0), 0 if late else 70)
            self.assertEqual(*results)

    def test_reflected_grenade_survives_original_caster_body_retirement(self):
        self.arena(distance=20)
        self.returning_heal_target(self.ally)
        self.start()
        self.step(bap=2, aim_bap=(0, -600))
        for _ in range(45):
            self.step()
            if any(r.deflections for r in self.fight.rockets):
                break
        rocket, = (r for r in self.fight.rockets if r.deflections)
        self.assertIs(rocket.shooter, self.genji)
        self.assertEqual(rocket.source_entity, self.genji.body)
        self.run.match.switch_hero(self.bap, heroes()[fixture.BAPTISTE])
        self.bap.body_script.ult_seconds = 0
        self.bap.mover.position[:] = (20, 1, 0)
        self.bap.mover.body = None
        self.assertIn(rocket, self.fight.rockets)
        self.step(count=45)
        self.assertEqual(self.hp(self.ally), 90)
        self.assertEqual(self.charge(), 0)

    def test_confirmed_original_prediction_retires_without_explosion_release(self):
        self.arena()
        self.returning_heal_target(self.ally)
        self.start()
        # This path keeps real transport emission; only the ACK result is a fixture.
        self.run.step({self.bap: (0, 0, 2), self.genji: (*self.guard_aim, 0)})
        rocket, = self.fight.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        alias = 0xA9000123
        shot.created[alias] = SimpleNamespace(received=True)
        self.step(count=40)
        updates = [u for u in self.bap.client.entities if u.entity == alias]
        self.assertTrue(updates)
        self.assertTrue(all(u.op == combat.OP_DESTROY and u.ch2 is None for u in updates))
        self.assertNotIn(shot, self.fight.projectiles.shots)
        self.assertEqual(self.hp(self.ally), 90)

    def test_actual_front_melee_is_blocked_but_back_melee_and_primary_hit(self):
        for rear in (False, True):
            with self.subTest(rear=rear):
                self.arena(distance=1.5, rear=rear)
                self.start()
                self.step(bap=0x800)
                self.step(count=30)
                self.assertEqual(self.hp(self.genji), 170 if rear else 200)
                self.assertEqual(self.hp(self.bap), 200)

    def test_rear_deflect_does_not_return_primary(self):
        self.arena(rear=True)
        self.start()
        self.step(bap=1)
        self.assertEqual(self.hp(self.genji), 176)
        self.assertEqual(self.hp(self.bap), 200)
        self.assertEqual(self.charge(), 24)

    def test_wall_and_nearer_body_precede_deflect(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                self.arena()
                self.start()
                if wall:
                    self.run.match.collision_world = World([
                        (-10, -10, 4, 10, -10, 4, 10, 10, 4),
                        (-10, -10, 4, 10, 10, 4, -10, 10, 4),
                    ])
                else:
                    self.ally.team = 1
                    self.ally.mover.position[:] = (0, 1, 4)
                self.step(bap=1)
                self.assertEqual(self.hp(self.genji), 200)
                self.assertEqual(self.hp(self.bap), 200)
                self.assertEqual(self.hp(self.ally), 200 if wall else 176)

    def test_friendly_active_genji_does_not_return_friendly_primary(self):
        self.arena()
        self.genji.team = self.bap.team
        self.start()
        self.step(bap=1)
        self.step(count=25)
        self.assertEqual(self.hp(self.bap), 200)
        self.assertEqual(self.hp(self.genji), 200)

    def test_expiry_and_cooldown_repress_leave_genji_damageable(self):
        self.arena()
        self.start()
        self.step(count=140)
        self.assertFalse(self.deflect.state(10).active)
        self.step(genji=16)
        self.assertFalse(self.deflect.state(10).active)
        self.step(bap=1)
        self.assertEqual(self.hp(self.genji), 176)

    def test_world_deadline_rejects_stale_active_owner_vm(self):
        self.arena()
        self.start()
        self.run.frame += 200
        self.run.match.update(self.run.frame * .016, self.run.frame)
        self.assertTrue(self.deflect.state(10).active)  # no synthesized defender command
        self.bap.take_commands([Command(self.run.frame, buttons=1)])
        self.assertEqual(self.hp(self.genji), 176)

    def test_matrix_boost_latches_before_return_without_second_crossing_boost(self):
        self.arena()
        matrix = deploy_matrix(self.run, self.bap)
        matrix.position = (0, 2, 4)
        matrix.rotation = (0, 0, 0, 1)
        self.start()
        self.step(bap=1)
        self.assertEqual(self.hp(self.bap), 152)
        self.assertEqual(self.hp(self.genji), 200)
        self.assertEqual(self.charge(), 0)

    def test_matrix_boosted_grenade_returns_effective_healing_with_new_owner(self):
        self.arena()
        self.returning_heal_target(self.ally)
        matrix = deploy_matrix(self.run, self.bap)
        matrix.position = (0, 2, 3)
        matrix.rotation = (0, 0, 0, 1)
        self.start()
        self.step(bap=2)
        self.step(count=40)
        self.assertEqual(self.hp(self.ally), 160)
        self.assertEqual(self.genji.stats.values[stats.HEALING], 140)
        self.assertEqual(self.bap.stats.values.get(stats.HEALING, 0), 0)
        self.assertEqual(self.charge(), 0)

    def test_excluded_field_canister_is_not_reflected(self):
        self.arena()
        self.start()
        self.step(bap=16)
        self.step(count=80)
        ward, = self.fight.baptiste.fields.values()
        actor = ward.actor
        self.assertIs(actor.owner, self.bap)
        self.assertGreater(actor.position[2], 5)


class BaptisteNpcDeflectTests(_Fixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        data = os.environ.get('ORPHEUS_PVE_NPC_DATA')
        if not data:
            raise RuntimeError('requires explicit five-NPC catalog and mover inputs')
        cls.catalog = npc.load_catalog(Path(data) / 'pve_npcs_174.json')
        mover.mover_data(next(iter(cls.catalog.values())).body)
        if any(row.body not in mover.loaded_mover_table() for row in cls.catalog.values()):
            raise RuntimeError('requires all five explicit NPC mover rows')

    def test_returned_primary_and_secondary_hit_all_five_actual_npc_bodies(self):
        for definition in self.catalog.values():
            for healing in (False, True):
                with self.subTest(npc=definition.name, healing=healing):
                    self.arena()
                    bot = npc.spawn(self.run.match, definition, (0, 1, 4), team=1 if healing else 0)
                    target = next(t for t in self.fight._targets() if t.owner is bot)
                    if healing:
                        self.fight.vitals_of(target).damage(180)
                    before = self.hp(bot)
                    self.guard_aim = aim(self.fight._eye(self.genji), (0, 1 + target.height / 2, 4))
                    self.start()
                    self.step(bap=2 if healing else 1)
                    self.step(count=40 if healing else 1)
                    self.assertAlmostEqual(self.hp(bot) - before, 70 if healing else -24, places=4)
                    self.assertEqual(self.charge(), 0)


if __name__ == '__main__':
    unittest.main()
