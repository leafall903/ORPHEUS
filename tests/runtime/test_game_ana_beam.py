"""Actual Zarya primary versus Ana; explicit capsule/frame-rate reconstruction."""
import unittest

import test_game_ana_barrier_energy as energy_fixture
from test_game_combat import aim

from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero


class AnaBeamTests(unittest.TestCase):
    def fight(self, *, distance=6, friendly=False):
        run, ana, caster, _entry, _barrier = energy_fixture.AnaBarrierEnergyTests().fight(
            False, friendly=friendly, distance=distance)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is ana)
        direction = aim(fight._eye(caster),
                        (own.feet[0], own.feet[1] + own.height / 2, own.feet[2]))
        return run, ana, caster, own, direction

    @staticmethod
    def primary(run, caster, direction, frames=80, button=1):
        for _ in range(frames):
            run.step({caster: (*direction, button)})

    @staticmethod
    def instance(caster):
        return next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x241)

    def test_actual_primary_resolves_native_entity_output_and_resource_damage_rate(self):
        run, ana, caster, own, direction = self.fight()
        self.primary(run, caster, direction)
        fight, instance = run.match.combat, self.instance(caster)
        self.assertEqual(instance.vars[2815].value().id, ana.body)
        self.assertEqual(instance.vars[536].value().id, ana.body)
        self.assertTrue(instance.states[43].ray_hit)
        self.assertEqual(instance.vars[1648].value(), 15)
        self.assertAlmostEqual(fight.vitals_of(own).total, 200 - 75 * 80 * .016)
        self.assertAlmostEqual(instance.vars[53].value(), 74.41812896728516)

    def test_friendly_miss_range_and_static_wall_do_not_damage_ana(self):
        for reason in ('friendly', 'miss', 'range', 'wall'):
            with self.subTest(reason=reason):
                run, _ana, caster, own, direction = self.fight(
                    distance=16 if reason == 'range' else 6, friendly=reason == 'friendly')
                if reason == 'miss':
                    direction = (0, 0)
                if reason == 'wall':
                    x, y, z = caster.mover.position
                    wall_z = z - 3
                    floor = [(x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
                             (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30)]
                    wall = [(x - 3, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
                            (x - 3, y, wall_z, x + 3, y + 4, wall_z, x - 3, y + 4, wall_z)]
                    run.match.collision_world = World([*floor, *wall])
                self.primary(run, caster, direction)
                self.assertEqual(run.match.combat.vitals_of(own).total, 200)
                self.assertEqual(self.instance(caster).vars[536].value().id, 0)

    def test_inside_range_hits_and_leaving_ray_then_reentry_updates_target(self):
        run, ana, caster, own, direction = self.fight(distance=14)
        self.primary(run, caster, direction, frames=10)
        self.assertAlmostEqual(run.match.combat.vitals_of(own).total, 188)
        original = list(ana.mover.position)
        ana.mover.position[0] += 3
        ana.mover.body = None  # rebuild the fixture's physics capsule after teleport
        self.primary(run, caster, direction, frames=20)
        after_miss = run.match.combat.vitals_of(own).total
        self.assertAlmostEqual(after_miss, 188)
        self.assertEqual(self.instance(caster).vars[536].value().id, 0)
        ana.mover.position = original
        ana.mover.body = None
        self.primary(run, caster, direction, frames=10)
        self.assertAlmostEqual(run.match.combat.vitals_of(own).total, 176)

    def test_release_reentry_and_duplicate_consumer_do_not_add_extra_damage(self):
        run, _ana, caster, own, direction = self.fight()
        self.primary(run, caster, direction, frames=10)
        fight = run.match.combat
        self.assertAlmostEqual(fight.vitals_of(own).total, 188)
        fight.command(caster, Command(run.frame))
        self.assertAlmostEqual(fight.vitals_of(own).total, 188)
        self.primary(run, caster, direction, frames=20, button=0)
        self.assertAlmostEqual(fight.vitals_of(own).total, 188)
        self.primary(run, caster, direction, frames=10)
        self.assertAlmostEqual(fight.vitals_of(own).total, 176)

    def test_actual_ammo_exhaustion_reload_and_held_restart_gate_damage(self):
        run, _ana, caster, own, direction = self.fight(friendly=True)
        weapon = self.instance(caster)
        for _ in range(400):
            self.primary(run, caster, direction, frames=1)
            if weapon.vars[53].value() == 0:
                break
        self.assertEqual(weapon.vars[53].value(), 0)
        caster.team = 1 - own.team
        fight = run.match.combat
        self.primary(run, caster, direction, frames=20)
        self.assertEqual(fight.vitals_of(own).total, 200)
        self.assertFalse(weapon.states[10].active)
        for _ in range(150):
            self.primary(run, caster, direction, frames=1)
            if weapon.states[10].active:
                break
        self.assertTrue(weapon.states[10].active)
        self.assertGreater(weapon.vars[53].value(), 0)
        self.assertAlmostEqual(fight.vitals_of(own).total, 198.8)

    def test_primary_to_secondary_ends_beam_and_primary_can_resume(self):
        run, _ana, caster, own, direction = self.fight()
        self.primary(run, caster, direction, frames=10)
        self.assertAlmostEqual(run.match.combat.vitals_of(own).total, 188)
        # Secondary aims away so its independent projectile cannot obscure beam damage.
        self.primary(run, caster, (0, 0), frames=40, button=2)
        self.assertFalse(self.instance(caster).states[10].active)
        self.assertAlmostEqual(run.match.combat.vitals_of(own).total, 188)
        self.primary(run, caster, direction, frames=80)
        self.assertTrue(self.instance(caster).states[10].active)
        self.assertLess(run.match.combat.vitals_of(own).total, 188)

    def test_sleep_release_and_reentry_restore_live_ray_notifications(self):
        run, _ana, caster, own, direction = self.fight()
        self.primary(run, caster, direction, frames=10)
        fight = run.match.combat
        source = next(t for t in fight._targets() if t.owner is caster)
        fight.vitals_of(source).sleep_until = fight.now + .5
        fight._interrupt_body(caster)
        self.primary(run, caster, direction, frames=40, button=0)
        self.assertAlmostEqual(fight.vitals_of(own).total, 188)
        self.primary(run, caster, direction, frames=10)
        self.assertEqual(self.instance(caster).vars[536].value().id, own.entity)
        self.assertAlmostEqual(fight.vitals_of(own).total, 176)

    def test_actual_ana_shield_contact_increases_live_beam_rate(self):
        helper = energy_fixture.AnaBarrierEnergyTests()
        run, ana, caster, entry, barrier = helper.fight(False)
        helper.contact(run, ana, entry, barrier, 1)
        self.assertEqual(energy_fixture.energy(caster), 14)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is ana)
        direction = aim(fight._eye(caster),
                        (own.feet[0], own.feet[1] + own.height / 2, own.feet[2]))
        self.primary(run, caster, direction, frames=30)
        self.assertAlmostEqual(fight.vitals_of(own).total, 200 - 88.3 * 30 * .016, delta=.0001)

    def test_nano_outgoing_and_incoming_scalars_apply_to_actual_beam(self):
        for offensive, defensive, rate in ((True, False, 112.5), (False, True, 37.5), (True, True, 56.25)):
            with self.subTest(offensive=offensive, defensive=defensive):
                run, _ana, caster, own, direction = self.fight()
                fight = run.match.combat
                source = next(t for t in fight._targets() if t.owner is caster)
                if offensive:
                    fight.vitals_of(source).nano_until = fight.now + 8
                if defensive:
                    fight.vitals_of(own).nano_until = fight.now + 8
                self.primary(run, caster, direction, frames=30)
                self.assertAlmostEqual(fight.vitals_of(own).total, 200 - rate * 30 * .016)

    def test_caster_sleep_stun_death_or_replacement_stops_continuous_damage(self):
        for reason in ('sleep', 'stun', 'death', 'replace'):
            with self.subTest(reason=reason):
                run, ana, caster, own, direction = self.fight()
                self.primary(run, caster, direction, frames=10)
                fight = run.match.combat
                source = next(t for t in fight._targets() if t.owner is caster)
                if reason == 'sleep':
                    fight.vitals_of(source).sleep_until = fight.now + 5
                    fight._interrupt_body(caster)
                elif reason == 'stun':
                    fight.stuns[caster.body] = run.frame + 100
                    fight._interrupt_body(caster)
                elif reason == 'death':
                    fight._damage(ana, source, 10000, False, None, run.frame)
                    fight._damage(ana, source, 10000, False, None, run.frame)
                else:
                    self.assertTrue(run.match.switch_hero(caster, find_hero('Soldier')))
                self.primary(run, caster, direction, frames=20, button=0)
                self.assertAlmostEqual(fight.vitals_of(own).total, 188)

    def test_actual_beam_death_has_no_later_health_damage_or_old_body_rebinding(self):
        run, ana, caster, own, direction = self.fight()
        self.primary(run, caster, direction, frames=180)
        fight = run.match.combat
        self.assertTrue(fight.vitals_of(own).dead)
        old = ana.body
        self.assertTrue(run.match.switch_hero(ana, ana.hero))
        ana.mover.position[0] += 10
        current = next(t for t in fight._targets() if t.owner is ana)
        self.primary(run, caster, direction, frames=10)
        self.assertNotEqual(ana.body, old)
        self.assertEqual(fight.vitals_of(current).total, 200)
        self.assertEqual(self.instance(caster).vars[536].value().id, 0)
