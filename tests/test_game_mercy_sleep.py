"""Actual Ana dart contacts against Mercy's authored skills and input."""

import unittest

import test_game_mercy_chains as chains
import test_game_mercy_control as controls
import test_game_mercy_descent as descent_tests
import test_game_mercy_deflection as defense_tests
import test_game_mercy_emp as emp_tests
import test_game_mercy_guardian as guardian_tests
import test_game_mercy_resurrect as resurrect_tests
import test_game_mercy_staff as staff_tests
import test_game_mercy_valkyrie as valkyrie_tests
from test_game_combat import aim

from ow174.game.combat import TICK, respawn_seconds
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import heroes
from ow174.game.script import nodes

ANA = 0x02E000000000013B


class MercySleepTests(unittest.TestCase):
    fixture = controls.MercyControlTests.fixture

    def caster(self, fixture):
        owner = fixture.fixture if hasattr(fixture, "fixture") else fixture
        owner.match.switch_hero(owner.enemy, heroes()[ANA])
        if owner is not fixture:
            owner.enemy.mover.position[:] = (0, 1, 5)
            owner.enemy.mover.body = None
        return owner

    def cast(self, owner, victim=None, *, miss=False, victim_buttons=0):
        victim = victim or owner.mercy
        x, y, z = victim.mover.position
        yaw, pitch = aim(owner.fight._eye(owner.enemy), (x, y + 1, z))
        if miss:
            yaw = (yaw + 65536) % 65536 - 32768
        target_command = (0, 0, victim_buttons)
        owner.run.step({owner.enemy: (yaw, pitch, 8), victim: target_command})
        for _ in range(60):
            owner.run.step({owner.enemy: (yaw, pitch, 0), victim: target_command})
            if owner.fight.is_asleep(victim):
                break
        target = next(t for t in owner.fight._targets() if t.owner is victim)
        return owner.fight.vitals_of(target)

    def test_sleep_dart_releases_descent_until_natural_wake_and_new_press(self):
        descent = self.fixture(descent_tests.AngelicDescentTests)
        owner = self.caster(descent)
        descent.place_at_elevated_firing_platform(180)
        descent.step(30, JUMP)
        state = descent.descent()
        self.assertTrue(state.active)
        self.assertAlmostEqual(owner.mercy.mover.velocity[1], -2, places=2)

        health = self.cast(owner, victim_buttons=JUMP)
        self.assertEqual(health.total, 195)
        self.assertTrue(owner.fight.is_asleep(owner.mercy))
        self.assertFalse(state.active)
        for _ in range(8):
            descent.step(1, JUMP)
            self.assertFalse(state.active)
            self.assertFalse(any(mod.index == 13 for mod in owner.mercy.mover.mods.values()))
        self.assertLess(owner.mercy.mover.velocity[1], -3)
        while (owner.run.frame + 1) * TICK < health.sleep_until:
            descent.step(1, JUMP)
            self.assertFalse(state.active)
        descent.step(1)
        self.assertFalse(owner.fight.is_asleep(owner.mercy))
        self.assertGreater(owner.mercy.mover.position[1], 1)
        descent.step(30, JUMP)
        self.assertTrue(state.active)
        self.assertAlmostEqual(owner.mercy.mover.velocity[1], -2, places=2)

    def test_actual_dart_suppresses_inputs_until_expiry_and_passive_heal_still_runs(self):
        owner = self.caster(self.fixture(staff_tests.MercyStaffTests))
        health = self.cast(owner)
        component = owner.mercy.body_script.component
        slot = component.entity_var(31)
        self.assertEqual(slot.value(), 1)
        self.assertEqual(health.total, 195)
        self.assertTrue(owner.fight.is_asleep(owner.mercy))
        self.assertAlmostEqual(health.sleep_until - owner.fight.now, 5)
        before = tuple(owner.mercy.mover.position)
        for _ in range(90):
            run = owner.run
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            owner.mercy.take_commands([Command(run.frame, forward=127, buttons=FIRE | 8 | 16 | 32, action=2)])
            self.assertEqual(slot.value(), 1)
        self.assertTrue(owner.fight.is_asleep(owner.mercy))
        self.assertEqual(tuple(owner.mercy.mover.position), before)
        self.assertEqual(owner.mercy.body_script.buttons, 0)
        self.assertEqual(health.total, 200)
        for _ in range(230):
            owner.run.step({})
        self.assertFalse(owner.fight.is_asleep(owner.mercy))
        self.assertFalse(owner.fight.is_stunned(owner.mercy))
        self.assertEqual(slot.value(), 1)
        before = owner.vitals.total
        owner.step(20)
        self.assertAlmostEqual(owner.vitals.total - before, 17.6)

        run.frame += 1
        run.match.update(run.frame * TICK, run.frame)
        owner.mercy.take_commands([Command(run.frame, action=2)])
        self.assertEqual(slot.value(), 2)
        owner.step(35, buttons=0)
        weapon = next(instance for instance in component.instances.values() if instance.graph.index == 0x6D)
        magazine = weapon.find_var(0, 53)
        self.assertEqual(magazine.value(), 20)
        owner.step(1, target=owner.enemy)
        self.assertEqual(magazine.value(), 19)

    def test_later_actual_rifle_damage_wakes_mercy(self):
        owner = self.caster(self.fixture(staff_tests.MercyStaffTests))
        health = self.cast(owner)
        self.assertTrue(owner.fight.is_asleep(owner.mercy))
        rifle = owner.ally
        rifle.team = owner.enemy.team
        x, y, z = owner.mercy.mover.position
        rifle.mover.position[:] = (x + 4, y, z)
        rifle.mover.body = None
        yaw, pitch = aim(owner.fight._eye(rifle), (x, y + 0.8, z))
        for _ in range(25):
            owner.run.step({rifle: (yaw, pitch, FIRE)})
            if health.total < 195:
                break
        self.assertLess(health.total, 195)
        self.assertFalse(owner.fight.is_asleep(owner.mercy))

    def test_dart_hit_breaks_held_staff_and_miss_preserves_it(self):
        for miss in (False, True):
            with self.subTest(miss=miss):
                owner = self.caster(self.fixture(staff_tests.MercyStaffTests))
                owner.step(20)
                mercy, ana = owner.mercy, owner.enemy
                x, y, z = owner.ally.mover.position
                beam_yaw, beam_pitch = aim(owner.fight._eye(mercy), (x, y + 1, z))
                x, y, z = mercy.mover.position
                dart_yaw, dart_pitch = aim(owner.fight._eye(ana), (x, y + 1, z))
                if miss:
                    dart_yaw = (dart_yaw + 65536) % 65536 - 32768
                before = owner.vitals.total
                launched = False
                for frame in range(60):
                    owner.run.step({
                        mercy: (beam_yaw, beam_pitch, FIRE),
                        ana: (dart_yaw, dart_pitch, 8 if frame == 0 else 0),
                    })
                    launched |= any(rocket.shooter is ana for rocket in owner.fight.rockets)
                    if owner.fight.is_asleep(mercy):
                        break
                self.assertTrue(launched)
                self.assertEqual(owner.fight.is_asleep(mercy), not miss)
                self.assertGreater(owner.vitals.total, before)
                before = owner.vitals.total
                owner.step(20)
                self.assertAlmostEqual(owner.vitals.total - before, 17.6 if miss else 0)
                if not miss:
                    owner.step(315, buttons=0)
                    self.assertFalse(owner.fight.is_asleep(mercy))
                    owner.step(20)
                    self.assertAlmostEqual(owner.vitals.total - before, 17.6)

    def test_lethal_hit_during_sleep_retires_control_and_respawn_accepts_movement(self):
        f = self.caster(self.fixture(staff_tests.MercyStaffTests))
        mercy, shooter, ana = f.mercy, f.ally, f.enemy
        self.assertTrue(f.match.switch_hero(shooter, heroes()[controls.MCCREE]))
        shooter.team = ana.team
        shooter.mover.position[:] = (4, 1, 0)
        ana.mover.position[:] = (0, 1, 6)
        for player in f.run.players:
            player.mover.body = None
        for _ in range(35):
            f.run.step({})
        target = next(t for t in f.fight._targets() if t.owner is mercy)
        health = f.fight.vitals_of(target)
        x, y, z = mercy.mover.position
        yaw, pitch = aim(f.fight._eye(shooter), (x, y + 0.8, z))
        for _ in range(90):
            f.run.step({shooter: (yaw, pitch, FIRE)})
            if health.total <= 60:
                break
        self.assertEqual(health.total, 60)
        self.cast(f)
        self.assertEqual(health.total, 55)
        self.assertTrue(f.fight.is_asleep(mercy))
        original_deadline = health.sleep_until
        old_body, old_script = mercy.body, mercy.body_script
        for _ in range(60):
            f.run.step({shooter: (yaw, pitch, FIRE)})
            if f.fight.is_dead(mercy):
                break
        self.assertTrue(f.fight.is_dead(mercy))
        self.assertEqual(health.killer, shooter.body)
        self.assertEqual(health.sleep_until, 0)
        self.assertTrue(old_script.component.owner_dead)

        for _ in range(int(respawn_seconds(f.match) / TICK) + 10):
            f.run.step({})
            if mercy.body != old_body:
                break
        self.assertNotEqual(mercy.body, old_body)
        self.assertLess(f.fight.now, original_deadline)
        self.assertTrue(old_script.retired)
        self.assertNotIn(old_body, f.fight.vitals)
        self.assertFalse(f.fight.is_stunned(mercy))
        self.assertFalse(f.fight.is_dead(mercy))
        position = tuple(mercy.mover.position)
        for _ in range(12):
            f.run.frame += 1
            f.match.update(f.run.frame * TICK, f.run.frame)
            mercy.take_commands([Command(f.run.frame, forward=127)])
        self.assertNotEqual(tuple(mercy.mover.position), position)

    def test_dart_blocks_valkyrie_flight_input_without_stopping_ultimate(self):
        valkyrie = self.fixture(valkyrie_tests.ValkyrieTests)
        owner = self.caster(valkyrie)
        owner.enemy.mover.position[:] = (4, 1, 5)
        valkyrie.start()
        valkyrie.step(35)
        health = self.cast(owner)
        self.assertAlmostEqual(health.total, 195.32)  # Valkyrie regeneration runs on the hit frame.
        self.assertTrue(owner.fight.is_asleep(owner.mercy))
        self.assertTrue(valkyrie.running())
        before = valkyrie.instance.find_var(0, 215).value()
        position = tuple(owner.mercy.mover.position)
        valkyrie.walk(20, forward=127, buttons=JUMP)
        self.assertEqual(tuple(owner.mercy.mover.position), position)
        self.assertLess(valkyrie.instance.find_var(0, 215).value(), before - 0.3)
        self.assertEqual(valkyrie.charge(), 0)
        valkyrie.step(315)
        self.assertFalse(owner.fight.is_asleep(owner.mercy))
        self.assertTrue(valkyrie.running())
        valkyrie.walk(20, buttons=JUMP)
        self.assertGreater(owner.mercy.mover.position[1], position[1] + 2)

    def test_actual_resurrection_restores_same_body_before_old_sleep_deadline(self):
        f = self.fixture(chains.MercyChainTests)
        mercy, ally, shooter, ana = f.mercy, f.anchor, f.chain, f.enemy
        self.assertTrue(f.match.switch_hero(shooter, heroes()[controls.MCCREE]))
        self.assertTrue(f.match.switch_hero(ana, heroes()[ANA]))
        shooter.team = ana.team
        ally.mover.position[:] = (0, 1, 3)
        shooter.mover.position[:] = (4, 1, 3)
        ana.mover.position[:] = (0, 1, 8)
        for player in f.run.players:
            player.mover.body = None
        for _ in range(35):
            f.run.step({})

        yaw, pitch = aim(f.fight._eye(shooter), (0, 1.8, 3))
        f.run.step({shooter: (yaw, pitch, FIRE)})
        self.assertEqual(f.health(ally), 30)
        health = self.cast(f, ally)
        self.assertEqual(health.total, 25)
        self.assertTrue(f.fight.is_asleep(ally))
        original_deadline = health.sleep_until
        body = ally.body
        for _ in range(60):
            f.run.step({shooter: (yaw, pitch, FIRE)})
            if f.fight.is_dead(ally):
                break
        self.assertTrue(f.fight.is_dead(ally))
        self.assertEqual(health.killer, shooter.body)
        self.assertEqual(health.sleep_until, 0)

        yaw, pitch = aim(f.fight._eye(mercy), (0, 2, 3))
        f.run.step({mercy: (yaw, pitch, resurrect_tests.RESURRECT)})
        for _ in range(120):
            f.run.step({})
            if not f.fight.is_dead(ally):
                break
        self.assertFalse(f.fight.is_dead(ally))
        self.assertEqual(ally.body, body)
        self.assertEqual(health.total, 200)
        self.assertFalse(f.fight.is_asleep(ally))
        self.assertTrue(f.fight.control_locked(ally))
        self.assertFalse(any(who is ally for _, who in f.fight.respawns))

        position = tuple(ally.mover.position)
        for _ in range(105):
            f.run.frame += 1
            f.match.update(f.run.frame * TICK, f.run.frame)
            ally.take_commands([Command(f.run.frame, forward=127)])
            if not f.fight.control_locked(ally) and tuple(ally.mover.position) != position:
                break
            self.assertEqual(tuple(ally.mover.position), position)
        self.assertFalse(f.fight.control_locked(ally))
        self.assertNotEqual(tuple(ally.mover.position), position)
        self.assertLess(f.fight.now, original_deadline)

    def test_actual_dart_interrupts_guardian_and_resurrection(self):
        guardian = self.fixture(guardian_tests.GuardianAngelTests)
        owner = self.caster(guardian)
        guardian.step(4, guardian_tests.SHIFT)
        self.assertTrue(guardian.instance.state(0).active)
        self.cast(owner)
        self.assertTrue(owner.fight.is_asleep(owner.mercy))
        self.assertFalse(guardian.instance.state(0).active)
        self.assertGreater(guardian.cooldown(), 0)

        resurrection = self.fixture(resurrect_tests.ResurrectTests)
        owner = self.caster(resurrection)
        resurrection.step(1, resurrect_tests.RESURRECT)
        self.assertTrue(any(isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING
                            for state in resurrection.instance.states.values()))
        self.cast(owner)
        self.assertTrue(owner.fight.is_asleep(owner.mercy))
        resurrection.step(120)
        self.assertTrue(owner.fight.is_dead(owner.ally))

    def test_projected_barrier_break_does_not_wake_mercy_until_next_hit(self):
        f = self.fixture(chains.MercyChainTests)
        fight, run, mercy = f.fight, f.run, f.mercy
        zarya, shooter, ana = f.anchor, f.chain, f.enemy
        for player, hero, position, team in (
            (zarya, 0x02E0000000000068, (0, 1, 6), 0),
            (shooter, controls.MCCREE, (4, 1, 0), 1),
            (ana, ANA, (0, 1, -6), 1),
        ):
            self.assertTrue(f.match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position[:] = position
            player.mover.body = None
        mercy.mover.body = None
        for _ in range(35):
            run.step({})

        health = self.cast(f)
        self.assertTrue(fight.is_asleep(mercy))
        yaw, pitch = aim(fight._eye(zarya), fight._eye(mercy))
        run.step({zarya: (yaw, pitch, 16)})
        self.assertTrue(fight.zarya_barriers.protects(mercy))
        x, y, z = mercy.mover.position
        yaw, pitch = aim(fight._eye(shooter), (x, y + 0.8, z))
        shield_hits = []
        for _ in range(115):
            before = health.total
            run.step({shooter: (yaw, pitch, FIRE)})
            barrier = fight.zarya_barriers.projected.get(zarya.body)
            if barrier is not None and (not shield_hits or shield_hits[-1] != barrier.remaining):
                shield_hits.append(barrier.remaining)
                self.assertGreaterEqual(health.total, before)  # Passive self-healing can run during sleep.
                self.assertTrue(fight.is_asleep(mercy))
            if not fight.is_asleep(mercy):
                break
        self.assertEqual(shield_hits, [130, 60, 0])
        self.assertAlmostEqual(health.total, before - 70)
        self.assertFalse(fight.is_asleep(mercy))

    def test_miss_and_friendly_contact_do_not_sleep_mercy(self):
        for miss in (False, True):
            with self.subTest(miss=miss):
                owner = self.caster(self.fixture(staff_tests.MercyStaffTests))
                if not miss:
                    owner.enemy.team = owner.mercy.team
                health = self.cast(owner, miss=miss)
                self.assertEqual(health.total, 200)
                self.assertFalse(owner.fight.is_asleep(owner.mercy))


    def test_recently_revived_ally_rejects_dart_until_immunity_ends(self):
        resurrection = self.fixture(resurrect_tests.ResurrectTests)
        owner = self.caster(resurrection)
        resurrection.cast()
        health = self.cast(owner, owner.ally)
        self.assertGreater(health.invulnerable_until, owner.fight.now)
        self.assertEqual(health.total, 200)
        self.assertFalse(owner.fight.is_asleep(owner.ally))
        for _ in range(800):
            owner.run.step({})
        self.cast(owner, owner.ally)
        self.assertEqual(health.total, 195)
        self.assertTrue(owner.fight.is_asleep(owner.ally))

    def test_sphere_intercepts_sleep_dart_even_on_break(self):
        helper = emp_tests.MercyEmpTests()
        self.addCleanup(helper.doCleanups)
        for durability, remaining in ((700, 695), (5, 0)):
            with self.subTest(durability=durability):
                f, health, sphere = helper.sphere()
                f.ally.team = f.mercy.team
                f.enemy.team = 1 - f.mercy.team
                self.caster(f)
                x, y, z = sphere.centre
                f.mercy.mover.position[:] = (x, y - 0.1, z - 3.5)
                f.enemy.mover.position[:] = (x, y - 0.1, z - 7)
                f.mercy.mover.body = f.enemy.mover.body = None
                sphere.health = durability
                self.cast(f)
                self.assertEqual(sphere.health, remaining)
                self.assertEqual(health.total, 200)
                self.assertFalse(f.fight.is_asleep(f.mercy))
                self.assertFalse(f.fight.rockets)

    def test_actual_genji_deflect_returns_dart_into_mercy(self):
        helper = defense_tests.MercyDeflectionTests()
        self.addCleanup(helper.doCleanups)
        f, deflect = helper.fixture()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[ANA]))
        f.ally.mover.position[:] = (0, 1, 0)
        f.mercy.mover.position[:] = (0, 1, 3)
        f.enemy.mover.position[:] = (0, 1, 8)
        for player in f.run.players:
            player.mover.body = None
        f.run.step({f.enemy: (-32768, 0, 16)})
        for _ in range(3):
            f.run.step({f.enemy: (-32768, 0, 0)})
        self.assertTrue(deflect.node_state(deflect.graph.node(18)).active)
        yaw, pitch = aim(f.fight._eye(f.ally), (0, 2.3, 8))
        f.run.step({f.ally: (yaw, pitch, 8), f.enemy: (-32768, 0, 0)})
        for _ in range(60):
            f.run.step({f.ally: (yaw, pitch, 0), f.enemy: (-32768, 0, 0)})
            if f.fight.is_asleep(f.mercy):
                break
        self.assertEqual(helper.health(f, f.mercy), 195)
        self.assertTrue(f.fight.is_asleep(f.mercy))
        self.assertEqual(helper.health(f, f.enemy), 200)
        self.assertFalse(f.fight.is_asleep(f.enemy))

    def test_projected_barrier_absorbs_dart_without_sleeping_mercy(self):
        helper = emp_tests.MercyEmpTests()
        self.addCleanup(helper.doCleanups)
        _, f, health = helper.encounter()
        self.caster(f)
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[0x02E0000000000068]))
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x, y, z + 5)
        f.ally.mover.body = None
        yaw, pitch = aim(f.fight._eye(f.ally), f.fight._eye(f.mercy))
        f.run.step({f.ally: (yaw, pitch, 16)})
        self.assertTrue(f.fight.zarya_barriers.protects(f.mercy))
        self.cast(f)
        self.assertEqual(f.fight.zarya_barriers.projected[f.ally.body].remaining, 195)
        self.assertEqual(health.total, 200)
        self.assertFalse(f.fight.is_asleep(f.mercy))
