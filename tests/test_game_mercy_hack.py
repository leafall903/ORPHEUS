"""Actual Sombra Hack against Mercy in the static server."""

import unittest
from types import SimpleNamespace

import test_game_mercy_boost as boost_tests
import test_game_mercy_chains as chains_tests
import test_game_mercy_descent as descent_tests

import test_game_mercy_guardian as guardian_tests
import test_game_mercy_resurrect as resurrect_tests
import test_game_mercy_staff as staff_tests
import test_game_mercy_valkyrie as valkyrie_tests
import test_game_mercy_zarya_barriers as zarya_tests
from test_game_combat import aim
from test_game_mercy_blaster_critical import equip
from test_game_mover import FLOOR, wall

from ow174.game import sombra_hack, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import nodes, support_effects
from ow174.game.script.driver import ULT_CHARGE


class MercyHackTests(unittest.TestCase):
    def encounter(self, distance=4):
        guardian = guardian_tests.GuardianAngelTests()
        guardian.setUp()
        self.addCleanup(guardian.doCleanups)
        f = guardian.fixture
        return guardian, f, self.place_sombra(f, distance)

    def place_sombra(self, f, distance=4):
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[sombra_hack.HERO]))
        x, y, z = f.mercy.mover.position
        f.enemy.mover.position[:] = (x + distance, y, z)
        f.enemy.mover.body = None
        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        return f.fight.vitals_of(target)

    @staticmethod
    def cast(f):
        x, y, z = f.mercy.mover.position
        yaw, pitch = aim(f.fight._eye(f.enemy), (x, y + 1, z))
        for _ in range(55):
            f.run.step({f.enemy: (yaw, pitch, SECONDARY)})

    def test_actual_hack_silences_guardian_then_recovers_and_allows_blaster(self):
        guardian, f, health = self.encounter()
        self.cast(f)
        self.assertGreater(health.hack_until, f.fight.now + 4)
        self.assertTrue(any(i.graph.index == sombra_hack.EFFECT
                            for i in f.mercy.body_script.component.instances.values()))
        guardian.step(4, guardian_tests.SHIFT)
        self.assertFalse(guardian.instance.state(0).active)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        enemy_hp = f.fight.vitals_of(target)
        before = enemy_hp.total
        equip(f.run, f.mercy)
        x, y, z = f.enemy.mover.position
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({f.mercy: (yaw, pitch, FIRE)})
        for _ in range(30):
            f.run.step({})
        self.assertGreater(health.hack_until, f.fight.now)
        self.assertEqual(enemy_hp.total, before - 20)
        while f.fight.now < health.hack_until:
            f.run.step({})
        guardian.step(4, guardian_tests.SHIFT)
        self.assertTrue(guardian.instance.state(0).active)

    def test_actual_hack_interrupts_active_guardian_angel(self):
        guardian, f, health = self.encounter()
        f.ally.mover.position[:] = (0, 1, 20)
        _, channel, cast = self.begin_hack(f)
        for _ in range(18):
            f.run.step(cast)
        self.assertTrue(channel.active)
        self.assertEqual(health.hack_until, 0)
        flight = guardian.instance.state(0)
        before = f.mercy.mover.position[2]
        for _ in range(30):
            x, y, z = f.mercy.mover.position
            hack_aim = aim(f.fight._eye(f.enemy), (x, y + 1, z))
            x, y, z = f.ally.mover.position
            guardian_aim = aim(f.fight._eye(f.mercy), (x, y + 1, z))
            was_flying = flight.active
            f.run.step({f.enemy: (*hack_aim, SECONDARY),
                        f.mercy: (*guardian_aim, guardian_tests.SHIFT)})
            if health.hack_until > f.fight.now:
                break
        self.assertGreater(health.hack_until, f.fight.now + 4)
        self.assertTrue(was_flying)
        self.assertFalse(flight.active)
        self.assertGreater(f.mercy.mover.position[2], before + 0.5)
        self.assertLess(f.mercy.mover.position[2], 16.3)
        self.assertGreater(guardian.cooldown(), 1.4)
        self.assertLessEqual(guardian.cooldown(), 1.5)
        stopped = f.mercy.mover.position[2]
        guardian.step(10, guardian_tests.SHIFT)
        self.assertEqual(f.mercy.mover.position[2], stopped)
        for _ in range(110):
            guardian.step(1)
            if guardian.cooldown() <= 0:
                break
        self.assertEqual(guardian.cooldown(), 0)
        self.assertGreater(health.hack_until, f.fight.now + 1)
        guardian.step(4, guardian_tests.SHIFT)
        self.assertFalse(flight.active)
        self.assertEqual(f.mercy.mover.position[2], stopped)
        self.assertEqual(guardian.cooldown(), 0)
        while f.fight.now < health.hack_until:
            guardian.step(1)
        self.assertEqual(guardian.cooldown(), 0)
        guardian.step(4, guardian_tests.SHIFT)
        self.assertTrue(flight.active)
        self.assertGreater(f.mercy.mover.position[2], stopped)

    def test_wall_faction_and_range_reject_actual_hack(self):
        for blocked in ("wall", "ally", "range"):
            with self.subTest(blocked=blocked):
                guardian, f, health = self.encounter(18 if blocked == "range" else 4)
                if blocked == "ally":
                    f.enemy.team = f.mercy.team
                if blocked == "wall":
                    x, _, z = f.mercy.mover.position
                    f.match.collision_world = World(FLOOR + wall(x + 2, z - 4, x + 2, z + 4))
                self.cast(f)
                self.assertEqual(health.hack_until, 0)
                guardian.step(4, guardian_tests.SHIFT)
                self.assertTrue(guardian.instance.state(0).active)


    def test_actual_hack_interrupts_resurrection_until_its_cooldown_expires(self):
        res = resurrect_tests.ResurrectTests()
        res.setUp()
        self.addCleanup(res.doCleanups)
        f = res.fixture
        health = self.place_sombra(f)
        revive_stat = 0x086000000000026A
        original_body = f.ally.body
        res.step(1, resurrect_tests.RESURRECT)
        ability = next(s for s in res.instance.states.values()
                       if isinstance(s, nodes.Ability) and s.flags & nodes.Ability.RUNNING)
        self.assertEqual(res.cooldown(), 0)
        self.cast(f)
        self.assertGreater(health.hack_until, f.fight.now)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(res.cooldown(), 29)
        self.assertLessEqual(res.cooldown(), 30)
        res.step(1, resurrect_tests.RESURRECT)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        res.step(120)
        self.assertTrue(f.fight.is_dead(f.ally))
        self.assertEqual(f.ally.body, original_body)
        self.assertEqual(f.mercy.stats.value(revive_stat), 0)

        for _ in range(1875):
            if res.cooldown() <= 0.5:
                break
            res.step(1)
        self.assertGreater(res.cooldown(), 0)
        self.assertLessEqual(res.cooldown(), 0.5)
        self.assertLessEqual(health.hack_until, f.fight.now)
        self.assertFalse(f.fight.is_dead(f.ally))
        self.assertNotEqual(f.ally.body, original_body)

        f.ally.mover.position[:] = (0, 1, 3)
        res.target = next(t for t in f.fight._targets() if t.owner is f.ally)
        f.fight._damage(f.enemy, res.target, 500, False, None, f.run.frame)
        body = f.ally.body
        res.step(1, resurrect_tests.RESURRECT)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertTrue(f.fight.is_dead(f.ally))
        for _ in range(40):
            res.step(1)
            if res.cooldown() <= 0:
                break
        self.assertEqual(res.cooldown(), 0)
        self.assertTrue(f.fight.is_dead(f.ally))
        self.assertEqual(f.ally.body, body)
        res.cast()
        self.assertEqual(f.ally.body, body)
        self.assertEqual(f.fight.vitals_of(res.target).total, 200)
        self.assertEqual(f.mercy.stats.value(revive_stat), 1)
        self.assertGreater(res.cooldown(), 29)

    def test_revived_mercy_rejects_hack_until_immunity_expires(self):
        res = resurrect_tests.ResurrectTests()
        res.setUp(ally_hero=sombra_hack.MERCY)
        self.addCleanup(res.doCleanups)
        self.assertTrue(res.match.switch_hero(res.enemy, heroes()[sombra_hack.HERO]))
        res.enemy.mover.position[:] = (4, 1, 3)
        res.enemy.mover.body = None
        body = res.ally.body
        res.cast()
        self.assertEqual(res.ally.body, body)
        health = res.fight.vitals_of(res.target)
        self.assertGreater(health.invulnerable_until, res.fight.now)
        victim = SimpleNamespace(run=res.run, fight=res.fight, mercy=res.ally, enemy=res.enemy)
        script = res.ally.body_script.component

        weapon, channel, cast = self.begin_hack(victim)
        for _ in range(55):
            res.run.step(cast)
            if not channel.active:
                break
        self.assertFalse(channel.active)
        self.assertGreater(health.invulnerable_until, res.fight.now)
        self.assertEqual(health.hack_until, 0)
        self.assertEqual(health.total, 200)
        self.assertFalse(any(i.graph.index == sombra_hack.EFFECT for i in script.instances.values()))

        cooldown = weapon.find_var(0, 61)
        for _ in range(650):
            res.run.step({})
            if cooldown.value() <= 0 and health.invulnerable_until <= res.fight.now:
                break
        self.assertLessEqual(cooldown.value(), 0)
        self.assertLessEqual(health.invulnerable_until, res.fight.now)
        self.assertEqual(res.ally.body, body)
        self.cast(victim)
        self.assertGreater(health.hack_until, res.fight.now + 4)
        self.assertTrue(any(i.graph.index == sombra_hack.EFFECT for i in script.instances.values()))
        self.assertEqual(health.total, 200)

    def test_actual_hack_blocks_new_valkyrie_without_spending_charge(self):
        valk = valkyrie_tests.ValkyrieTests()
        valk.setUp()
        self.addCleanup(valk.doCleanups)
        f = valk.fixture
        health = self.place_sombra(f)
        self.cast(f)
        self.assertGreater(health.hack_until, f.fight.now)
        valk.script.set_ult_charge(valk.cost)
        valk.step(1, valkyrie_tests.ULTIMATE)
        self.assertFalse(valk.running())
        self.assertEqual(valk.charge(), valk.cost)
        while f.fight.now < health.hack_until:
            f.run.step({})
        valk.step(1, valkyrie_tests.ULTIMATE)
        self.assertTrue(valk.running())
        self.assertEqual(valk.charge(), 0)

    def test_actual_hack_preserves_base_staff_healing(self):
        f = staff_tests.MercyStaffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        health = self.place_sombra(f)
        f.step(10)
        script = f.mercy.body_script.component
        staff = next(i for i in script.instances.values() if i.graph.index == 0x006B)
        x, y, z = f.ally.mover.position
        staff_command = (*aim(f.fight._eye(f.mercy), (x, y + 1, z)), FIRE)
        x, y, z = f.mercy.mover.position
        hack_command = (*aim(f.fight._eye(f.enemy), (x, y + 1, z)), SECONDARY)
        for _ in range(55):
            f.run.step({f.mercy: staff_command, f.enemy: hack_command})
            if health.hack_until > f.fight.now:
                break
        self.assertGreater(health.hack_until, f.fight.now + 4)
        self.assertIn(staff, script.instances.values())
        self.assertTrue(any(i.graph.index == sombra_hack.EFFECT for i in script.instances.values()))
        before = f.vitals.total
        credit = f.mercy.stats.value(stats.HEALING)
        f.step(20)
        self.assertAlmostEqual(f.vitals.total - before, 17.6, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(stats.HEALING) - credit, 17.6, places=4)
        f.step(10, buttons=0)
        self.assertAlmostEqual(f.vitals.total - before, 17.6, places=4)
        f.step(20)
        self.assertGreater(health.hack_until, f.fight.now)
        self.assertGreater(f.vitals.total - before, 17.6)

    @unittest.skipUnless(boost_tests.EFFECT_BUNDLE.is_file(), "explicit extracted effect pack required")
    def test_actual_hack_preserves_base_staff_projectile_boost_snapshot(self):
        for launch_boosted in (False, True):
            with self.subTest(launch_boosted=launch_boosted):
                f = boost_tests.MercyBoostTests()
                f.setUp()
                self.addCleanup(f.doCleanups)
                health = self.place_sombra(f, distance=12)
                script = f.mercy.body_script.component
                script.set(12918, 0.0)
                charge = script.entity_var(ULT_CHARGE)
                beam = SECONDARY if launch_boosted else 0
                f.step(10, beam=beam)
                x, y, z = f.ally.mover.position
                staff_aim = aim(f.fight._eye(f.mercy), (x, y + 1, z))
                x, y, z = f.mercy.mover.position
                hack_command = (*aim(f.fight._eye(f.enemy), (x, y + 1, z)), SECONDARY)
                for _ in range(55):
                    f.run.step({f.mercy: (*staff_aim, beam), f.enemy: hack_command})
                    if health.hack_until > f.fight.now:
                        break
                self.assertGreater(health.hack_until, f.fight.now + 4)
                self.assertAlmostEqual(f.fight.damage_scale(f.ally), 1.3 if launch_boosted else 1)
                before = f.enemy_health()
                charge_before = charge.value()
                f.step(1, beam=beam, attack=SECONDARY)
                projectile, = f.fight.rockets
                self.assertIs(projectile.shooter, f.ally)
                direct, blast = (52, 104) if launch_boosted else (40, 80)
                self.assertAlmostEqual(projectile.direct.amount, direct, places=4)
                self.assertAlmostEqual(projectile.blast.amount, blast, places=4)
                self.assertEqual(f.enemy_health(), before)
                beam = 0 if launch_boosted else SECONDARY
                f.step(5, beam=beam)
                self.assertIn(projectile, f.fight.rockets)
                self.assertAlmostEqual(f.fight.damage_scale(f.ally), 1 if launch_boosted else 1.3)
                f.step(30, beam=beam)
                self.assertGreater(health.hack_until, f.fight.now)
                self.assertFalse(f.fight.rockets)
                self.assertAlmostEqual(before - f.enemy_health(), direct + blast, places=4)
                self.assertAlmostEqual(f.ally.stats.value(stats.HERO_DAMAGE), direct + blast, places=4)
                self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
                bonus = 36 if launch_boosted else 0
                self.assertAlmostEqual(f.mercy.stats.value(boost_tests.AMPLIFIED), bonus, places=4)
                self.assertAlmostEqual(charge.value() - charge_before, bonus, places=4)
                self.assertEqual(f.fixture.vitals.total, 100)

    def test_actual_hack_preserves_passive_self_healing_cadence(self):
        for hacked in (False, True):
            with self.subTest(hacked=hacked):
                _, f, health = self.encounter()
                target = next(t for t in f.fight._targets() if t.owner is f.mercy)
                f.fight._damage(f.enemy, target, 50, False, None, f.run.frame)
                if hacked:
                    self.cast(f)
                else:
                    for _ in range(55):
                        f.run.step({})
                self.assertEqual(health.hack_until > f.fight.now, hacked)
                for _ in range(7):
                    f.run.step({})
                self.assertEqual(health.total, 150)
                for _ in range(63):
                    f.run.step({})
                self.assertEqual(health.hack_until > f.fight.now, hacked)
                self.assertAlmostEqual(health.total, 170.16, places=4)
                self.assertEqual(f.mercy.stats.value(stats.HEALING), 0)

    def test_actual_hack_preserves_angelic_descent_input(self):
        descent = descent_tests.AngelicDescentTests()
        descent.setUp()
        self.addCleanup(descent.doCleanups)
        f = descent.fixture
        health = self.place_sombra(f)
        descent.step(12, descent_tests.JUMP)
        state = descent.descent()
        self.assertTrue(state.active)
        for _ in range(55):
            x, y, z = f.mercy.mover.position
            hack_command = (*aim(f.fight._eye(f.enemy), (x, y + 1, z)), SECONDARY)
            f.run.step({f.enemy: hack_command, f.mercy: (0, 0, descent_tests.JUMP)})
            if health.hack_until > f.fight.now:
                break
        self.assertGreater(health.hack_until, f.fight.now + 4)
        self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)
        descent.step(12)
        self.assertFalse(state.active)
        self.assertLess(f.mercy.mover.velocity[1], -5)
        descent.step(30, descent_tests.JUMP)
        self.assertGreater(health.hack_until, f.fight.now)
        self.assertTrue(state.active)
        terminal = [m for m in f.mercy.mover.mods.values() if m.index == 13]
        self.assertEqual([(m.op, m.value) for m in terminal], [(2, 2.0)])
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, delta=0.01)

    def hack_during_valkyrie_staff(self, beam):
        f = chains_tests.MercyChainTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        health = self.place_sombra(f)
        f.start()
        component = f.mercy.body_script.component
        ultimate = next(i for i in component.instances.values() if i.graph.index == 0x10DA)
        timer = ultimate.state(2)
        activation = timer.counter, timer.start
        self.assertTrue(timer.active)
        f.step(10, beam=beam)
        effects = dict(f.fight.support_effects.attachments)
        expected = ({support_effects.HEAL, support_effects.CHAIN_HEAL} if beam == FIRE else
                    {support_effects.BOOST, support_effects.CHAIN_BOOST})
        self.assertEqual({effect.definition for effect in effects.values()}, expected)
        self.assertEqual({effect.target for effect in effects.values()}, {f.anchor.body, f.chain.body})

        for _ in range(55):
            x, y, z = f.anchor.mover.position
            staff_aim = aim(f.fight._eye(f.mercy), (x, y + 1, z))
            x, y, z = f.mercy.mover.position
            hack_aim = aim(f.fight._eye(f.enemy), (x, y + 1, z))
            f.run.step({f.mercy: (*staff_aim, beam), f.enemy: (*hack_aim, SECONDARY)})
            if health.hack_until > f.fight.now:
                break
        self.assertGreater(health.hack_until, f.fight.now + 4)
        self.assertTrue(any(i.graph.index == sombra_hack.EFFECT for i in component.instances.values()))
        self.assertTrue(timer.active)
        self.assertEqual((timer.counter, timer.start), activation)
        self.assertTrue(component.entity_var(842).value())
        self.assertEqual(health.total, 200)

        guardian = next(i for i in component.instances.values() if i.graph.index == 0x006E)
        f.step(4, beam=beam | guardian_tests.SHIFT)
        self.assertFalse(guardian.state(0).active)
        for key, effect in effects.items():
            self.assertIs(f.fight.support_effects.attachments[key], effect)
        if beam == FIRE:
            before = f.health(f.anchor), f.health(f.chain)
            f.step(20)
            for player, hp in zip((f.anchor, f.chain), before, strict=True):
                self.assertAlmostEqual(f.health(player) - hp, 19.2, places=4)
        else:
            before = f.health(f.enemy)
            f.step(1, beam=beam, attack=FIRE)
            f.step(10, beam=beam)
            self.assertAlmostEqual(before - f.health(f.enemy), 26, places=4)
            self.assertAlmostEqual(f.chain.stats.value(stats.HERO_DAMAGE), 26, places=4)
            self.assertAlmostEqual(f.mercy.stats.value(boost_tests.AMPLIFIED), 6, places=4)
            self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)

        while f.fight.now < health.hack_until:
            f.step(1, beam=beam)
        self.assertTrue(timer.active)
        self.assertEqual((timer.counter, timer.start), activation)
        f.step(4, beam=beam | guardian_tests.SHIFT)
        self.assertTrue(guardian.state(0).active)

    @unittest.skipUnless(boost_tests.EFFECT_BUNDLE.is_file(), "explicit extracted effect pack required")
    def test_actual_hack_preserves_active_valkyrie_and_chain_healing(self):
        self.hack_during_valkyrie_staff(FIRE)

    @unittest.skipUnless(boost_tests.EFFECT_BUNDLE.is_file(), "explicit extracted effect pack required")
    def test_actual_hack_preserves_active_valkyrie_and_chain_boost(self):
        self.hack_during_valkyrie_staff(SECONDARY)

    def begin_hack(self, f):
        script = f.enemy.body_script.component
        weapon = next(i for i in script.instances.values() if i.graph.index == sombra_hack.WEAPON)
        channel = weapon.node_state(weapon.graph.node(100))
        x, y, z = f.mercy.mover.position
        cast = {f.enemy: (*aim(f.fight._eye(f.enemy), (x, y + 1, z)), SECONDARY)}
        for _ in range(55):
            f.run.step(cast)
            if channel.active:
                break
        self.assertTrue(channel.active)
        self.assertTrue(script.entity_var(8358).value())
        return weapon, channel, cast

    def test_actual_blaster_interrupts_a_hack_channel_but_a_miss_does_not(self):
        for hit in (True, False):
            with self.subTest(hit=hit):
                _, f, health = self.encounter()
                equip(f.run, f.mercy)
                weapon, channel, cast = self.begin_hack(f)
                self.assertEqual(health.hack_until, 0)

                x, y, z = f.enemy.mover.position
                yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
                if not hit:
                    yaw = yaw % 65536 - 32768
                f.run.step({**cast, f.mercy: (yaw, pitch, FIRE)})
                self.assertTrue(f.fight.rockets)
                for _ in range(50):
                    f.run.step(cast)
                self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 20 if hit else 0)
                self.assertFalse(channel.active)
                if hit:
                    self.assertEqual(health.hack_until, 0)
                    tags = weapon.find_var(0, 510).value()
                    self.assertEqual([tag.guid & 0xFFFF for tag in tags], [0x3BF, 0x9A6, 0x1E1E, 0x2D67, 0x4D29])
                    cooldown = weapon.find_var(0, 61)
                    self.assertGreater(cooldown.value(), 0)
                    f.run.step({})
                    f.run.step(cast)
                    self.assertFalse(channel.active)
                    for _ in range(150):
                        f.run.step({})
                        if cooldown.value() <= 0:
                            break
                    self.assertLessEqual(cooldown.value(), 0)
                    for _ in range(55):
                        f.run.step(cast)
                    self.assertGreater(health.hack_until, f.fight.now)
                else:
                    self.assertGreater(health.hack_until, f.fight.now)

    def test_actual_melee_interrupts_an_active_hack_channel(self):
        _, f, health = self.encounter(1.5)
        for _ in range(35):
            f.run.step({})
        weapon, channel, cast = self.begin_hack(f)
        x, y, z = f.enemy.mover.position
        direction = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({**cast, f.mercy: (*direction, 0x800)})
        for _ in range(50):
            f.run.step(cast)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 30)
        self.assertFalse(channel.active)
        self.assertEqual(health.hack_until, 0)
        self.assertGreater(weapon.find_var(0, 61).value(), 0)
        tags = weapon.find_var(0, 510).value()
        self.assertIn(0x302, [tag.guid & 0xFFFF for tag in tags])

    def test_projected_barrier_absorbs_blaster_and_preserves_hack(self):
        fixture = zarya_tests.MercyZaryaBarrierTests()
        run, mercy, zarya, sombra, enemy_health, _ = fixture.encounter(
            projected=True, ally_hero=sombra_hack.HERO)
        fight = run.match.combat
        f = SimpleNamespace(run=run, mercy=mercy, enemy=sombra, fight=fight)
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        weapon, channel, cast = self.begin_hack(f)
        self.assertEqual(health.hack_until, 0)
        self.assertTrue(fight.zarya_barriers.protects(sombra))
        initial_tags = weapon.find_var(0, 510).value()
        before = enemy_health.total
        x, y, z = sombra.mover.position
        direction = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({**cast, f.mercy: (*direction, FIRE)})
        for _ in range(50):
            f.run.step(cast)
        shield = f.fight.zarya_barriers.projected[zarya.body]
        self.assertEqual(shield.remaining, 180)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 4)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(enemy_health.total, before)
        self.assertEqual(weapon.find_var(0, 510).value(), initial_tags)
        self.assertFalse(channel.active)
        self.assertGreater(health.hack_until, f.fight.now)
