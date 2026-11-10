"""Real Genji Deflect commands against the declared Doomfist projectile host."""

import unittest

from test_doomfist_combat import DoomfistFixture
from test_doomfist_meteor import charge_by_neutral_commands
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box

from ow174.game import combat, defenses, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import nodes


class DeflectInteractionTests(DoomfistFixture, unittest.TestCase):
    def arena(self, distance=3, *, names=('Doomfist', 'Genji')):
        self.run = Run(KANEZAKA_DM, names)
        self.a, self.b = self.run.players[:2]
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.match.switch_hero(self.a, heroes()[0x02E000000000012F])
        self.match.switch_hero(self.b, heroes()[defenses.GENJI])
        self.fight = self.match.combat
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + distance))
        self.facing = -32768
        self.step()

    def step(self, button=0, *, defend=0, pitch=0):
        x, y, z = self.b.mover.position
        yaw, target_pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        self.run.step({self.a: (yaw, target_pitch, button),
                       self.b: (self.facing, pitch, defend)})

    def wait(self, frames):
        for _ in range(frames):
            self.step()

    def start(self):
        self.step(defend=16)
        self.assertIsNotNone(defenses.deflect(self.b))

    def own(self):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))

    def test_frontal_pellets_return_with_genji_damage_credit_and_no_extra_ammo_cost(self):
        self.arena()
        self.start()
        charge = self.charge_checkpoint()
        weapon = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x304)
        ammo = weapon.vars[53].value()
        self.step(1)
        self.wait(10)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(self.own().total, 184)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 0)
        self.assert_damage_charge(charge, 0)
        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 66)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertEqual(len(self.b.client.messages(combat.HIT)), 11)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(self.own().temporary_shields, 0)

    def test_back_attack_is_not_reflected(self):
        self.arena()
        self.facing = 0
        self.start()
        self.step(1)
        self.wait(10)
        self.assertEqual(self.vitals().total, 134)
        self.assertEqual(self.own().total, 250)

    def test_deflect_box_basis_remains_orthonormal_for_pitched_and_vertical_aim(self):
        self.arena()
        for yaw, pitch in ((0, 8192), (16384, -8192), (-32768, 16384)):
            self.facing = yaw
            self.step(pitch=pitch)
            basis = defenses._basis(self.b)
            for axis in basis:
                self.assertAlmostEqual(sum(v * v for v in axis), 1)
            for a, b in ((0, 1), (0, 2), (1, 2)):
                self.assertAlmostEqual(sum(x * y for x, y in zip(basis[a], basis[b], strict=True)), 0)

    def test_reflected_team_skips_deflector_ally_and_hits_original_enemy_team(self):
        self.arena(names=('Doomfist', 'Genji', 'Ally', 'Enemy'))
        ally, enemy = self.run.players[2:]
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team = enemy.team = 0
        self.b.team = ally.team = 1
        x, y, z = self.a.mover.position
        # A45-degree reflection leaves the original incoming line; the ally
        # is on that reflected line, ahead of the enemy, with actual capsules.
        self.facing = 24576
        self.place_player(ally, (x + 1.5, y, z + .44))
        self.place_player(enemy, (x + 3, y, z - 1.06))
        self.start()
        self.step(1)
        self.wait(20)
        targets = {t.owner: self.fight.vitals_of(t) for t in self.fight._targets()}
        self.assertEqual(targets[ally].total, 200)
        self.assertEqual(targets[enemy].total, 134)
        self.assertEqual(self.own().total, 250)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 66)

    def test_reflected_lethal_pellets_credit_genji_with_one_kill(self):
        self.arena(names=('Doomfist', 'Genji', 'Rifle'))
        rifle = self.run.players[2]
        x, y, z = self.a.mover.position
        self.place_player(rifle, (x + 10, y, z))
        for _ in range(100):
            yaw, pitch = aim(self.fight._eye(rifle), self.fight._eye(self.a))
            self.run.step({rifle: (yaw, pitch, 1), self.b: (self.facing, 0, 0)})
            if self.own().total <= 10:
                break
        self.assertEqual(self.own().total, 10)
        self.start()
        self.step(1)
        self.wait(10)
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(self.own().killer, self.b.body)
        self.assertEqual(self.b.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
        self.assertEqual(rifle.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(len(self.b.client.messages(combat.KILL_NOTICE)), 1)

    def test_reflected_pellets_consume_real_uppercut_shields_before_health(self):
        self.arena(1.5)
        self.step(8)
        owner = self.own()
        self.assertEqual(owner.temporary_shields, 30)
        deadline = owner.temporary_decay_at
        self.wait(80)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.start()
        self.assertGreater(owner.temporary_shields, 0)
        charge = self.charge_checkpoint()
        attacker_credit = self.a.stats.value(stats.HERO_DAMAGE)
        target_health = self.vitals().total
        initial_credit = self.b.stats.value(stats.HERO_DAMAGE)
        for frame in range(11):
            before_tick = self.fight.now
            shields = owner.temporary_shields
            health = owner.now[combat.HEALTH_POOL]
            credit = self.b.stats.value(stats.HERO_DAMAGE)
            self.step(int(frame == 0))
            reflected = self.b.stats.value(stats.HERO_DAMAGE) - credit
            decay = min(shields, 3 * max(0, self.fight.now - max(before_tick, deadline)))
            remaining = shields - decay
            self.assertAlmostEqual(owner.temporary_shields, max(0, remaining - reflected))
            self.assertAlmostEqual(health - owner.now[combat.HEALTH_POOL], max(0, reflected - remaining))
            self.assertEqual(owner.temporary_decay_at, deadline)
        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE) - initial_credit, 66)
        self.assertEqual(owner.temporary_shields, 0)
        self.assertEqual(self.vitals().total, target_health)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), attacker_credit)
        self.assert_damage_charge(charge, 0)

    def test_static_wall_is_hit_before_reflector_boxes(self):
        self.arena()
        x, y, z = self.a.mover.position
        world = World(box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                      + box(x - 2, x + 2, y, y + 3, z + 1.5, z + 1.7))
        self.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        self.start()
        self.step(1)
        self.wait(10)
        self.assertEqual(self.own().total, 250)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(self.fight.rockets)

    def test_reflection_at_exact_budget_endpoint_has_no_unpaid_separation(self):
        # Mechanical sweep invariant, separate from command acceptance: create
        # a real pellet, then supply a budget ending exactly at its first box.
        self.arena()
        self.start()
        self.step(1)
        rocket = self.fight.rockets[0]
        hit, owner, _ = defenses.reflection(self.fight, rocket, 10)
        self.assertIs(owner, self.b)
        point = tuple(a + b * hit for a, b in zip(rocket.position, rocket.direction, strict=True))
        duration = hit / rocket.speed
        self.fight._sweep_rocket(rocket, duration)
        for actual, expected in zip(rocket.position, point, strict=True):
            self.assertAlmostEqual(actual, expected, places=9)
        self.assertAlmostEqual(rocket.flown, duration)
        self.assertAlmostEqual(rocket.left + rocket.flown, 6.25)

    def test_multiple_actual_reflections_preserve_lifetime_without_duplicate_damage(self):
        self.arena(names=('Doomfist', 'Genji', 'Second Genji'))
        other = self.run.players[2]
        self.match.switch_hero(other, heroes()[defenses.GENJI])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z))
        self.place_player(other, (x, y, z + 4))
        self.place_player(self.a, (x + 1, y, z + 3))
        self.run.step({self.b: (0, 0, 16), other: (-32768, 0, 16)})
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        self.run.step({self.a: (yaw, pitch, 1),
                       self.b: (0, 0, 0), other: (-32768, 0, 0)})
        self.assertEqual(len(self.fight.rockets), 11)
        seen, remaining = set(), {}
        for _ in range(410):
            self.run.step({self.b: (0, 0, 0), other: (-32768, 0, 0)})
            self.assertLessEqual(len(self.fight.rockets), 11)
            for rocket in self.fight.rockets:
                seen.add(rocket.shooter)
                self.assertAlmostEqual(rocket.flown + rocket.left, 6.25)
                if id(rocket) in remaining:
                    self.assertLess(rocket.left, remaining[id(rocket)])
                remaining[id(rocket)] = rocket.left
        self.assertIn(self.b, seen)
        self.assertIn(other, seen)
        self.assertFalse(self.fight.rockets)
        total = sum(self.fight.vitals_of(t).total for t in self.fight._targets())
        self.assertLess(total, 650)
        self.assertGreaterEqual(total, 584)

    def test_frontal_quick_melee_is_blocked_but_rear_melee_hits(self):
        for facing in (-32768, 0):
            with self.subTest(facing=facing):
                self.arena(1.5)
                self.facing = facing
                self.start()
                self.step(0x800)
                self.wait(25)
                self.assertEqual(self.vitals().total, 200 if facing == -32768 else 170)
                self.assertEqual(self.own().total, 250)

    def test_punch_uppercut_and_ground_slam_bypass_projectile_deflect(self):
        for attack, distance, button, frames in (
            ('punch', 6, 2, 50), ('uppercut', 1.5, 8, 45), ('slam', 10, 16, 90),
        ):
            with self.subTest(attack=attack):
                self.arena(distance)
                self.start()
                self.step(button)
                self.wait(frames)
                self.assertLess(self.vitals().total, 200)
                self.assertGreater(self.own().temporary_shields, 0)
                self.assertTrue(self.a.client.messages(combat.HIT))

    def test_punch_contact_interrupts_deflect_before_its_natural_end(self):
        self.arena(6)
        self.start()
        self.step(2)
        for _ in range(40):
            self.step()
            if self.vitals().total < 200:
                break
        self.assertEqual(self.vitals().total, 150)
        self.assertIsNone(defenses.deflect(self.b))
        self.wait(60)
        self.assertGreater(defenses.genji(self.b).state(0).remaining(), 0)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.step(1)
        self.wait(10)
        self.assertEqual(self.vitals().total, 84)
        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_aerial_slam_bypasses_deflect_and_keeps_its_passive_gain(self):
        self.arena(10)
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y + 8, z))
        self.start()
        self.run.step({self.a: (0, 9000, 16), self.b: (self.facing, 0, 0)})
        cast = self.fight.slams.casts[self.a.body]
        self.assertEqual(cast.kind, 'aerial')
        self.wait(80)
        self.assertIsNotNone(cast.wave_at)
        self.assertIn(self.b.body, cast.hit)
        self.assertLess(self.vitals().total, 200)
        self.assertGreater(self.own().temporary_shields, 0)

    def test_authored_duration_cooldown_rejection_and_fresh_reentry(self):
        self.arena()
        self.start()
        instance = defenses.genji(self.b)
        phase, ability = instance.state(10), instance.state(0)
        activation, start = phase.counter, phase.start
        self.wait(160)
        self.assertIsNone(defenses.deflect(self.b))
        self.assertEqual(phase.ended_at - start, 2000)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.step(defend=16)
        self.wait(600)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(phase.counter, activation)
        self.start()
        self.assertEqual(phase.counter, activation + 1)

    def test_actual_meteor_damage_bypasses_deflect_and_grants_seventy_five(self):
        self.arena()
        charge_by_neutral_commands(self)
        self.step(0x20)
        self.wait(70)
        self.start()
        self.step(1)
        self.wait(100)
        self.assertLess(self.vitals().total, 200)
        self.assertEqual(self.own().temporary_shields, 75)
        self.assertTrue(self.fight.meteors.casts[self.a.body].impacted)


if __name__ == '__main__':
    unittest.main()
