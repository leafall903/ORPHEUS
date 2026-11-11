"""Actual Sombra EMP against Mercy's skills and body pools."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_game_mercy_descent as descent_tests
import test_game_mercy_deflection as defense_tests
import test_game_mercy_dynamic_barrier as barrier_tests
import test_game_mercy_guardian as guardian_tests
import test_game_mercy_hack as hack_tests
import test_game_mercy_healing_status as status_tests
import test_game_mercy_npc as npc_tests
import test_game_mercy_resurrect as resurrect_tests
from test_game_combat import aim
from test_game_mercy_blaster_critical import equip
from test_game_mover import FLOOR, wall

from ow174.game import combat, npc, npc_control, sigma_barrier, sombra_hack, winston_barriers
from ow174.game.collision import World
from ow174.game.commands import FIRE, JUMP, SECONDARY
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph, healing_status
from ow174.game.script.projectile_defense import SHIELD_TAG


class MercyEmpTests(unittest.TestCase):
    def encounter(self, distance=4):
        helper = hack_tests.MercyHackTests()
        result = helper.encounter(distance)
        self.addCleanup(helper.doCleanups)
        script = result[1].enemy.body_script
        script.ult_seconds = 0
        return result

    @staticmethod
    def cast(f, *, enough=True, yaw=0, frames=45, mercy_buttons=0, caster=None):
        caster = caster or f.enemy
        script = caster.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost if enough else cost - 1)
        for frame in range(frames):
            f.run.step({caster: (yaw, 0, 32 if frame == 0 else 0), f.mercy: (0, 0, mercy_buttons)})
        return script.component.entity_var(342).value()

    def test_emp_uses_actual_charge_and_delay_and_silences_until_expiry(self):
        guardian, f, health = self.encounter()
        self.assertEqual(self.cast(f, enough=False), 1399)
        self.assertEqual(health.hack_until, 0)
        self.assertEqual(self.cast(f, yaw=16384, frames=20), 0)
        self.assertEqual(health.hack_until, 0)
        for _ in range(25):
            f.run.step({})
        self.assertGreater(health.hack_until, f.fight.now + 4)
        self.assertEqual(health.total, 200)
        self.assertTrue(any(i.graph.index == sombra_hack.EFFECT and i.source_entity() == f.enemy.body
                            for i in f.mercy.body_script.component.instances.values()))
        guardian.step(4, guardian_tests.SHIFT)
        self.assertFalse(guardian.instance.state(0).active)
        while f.fight.now < health.hack_until:
            f.run.step({})
        guardian.step(4, guardian_tests.SHIFT)
        self.assertTrue(guardian.instance.state(0).active)

    def test_emp_whiff_spends_charge_and_rejects_wall_faction_and_range(self):
        for blocked in ("wall", "ally", "range"):
            with self.subTest(blocked=blocked):
                guardian, f, health = self.encounter(25 if blocked == "range" else 4)
                if blocked == "ally":
                    f.enemy.team = f.mercy.team
                if blocked == "wall":
                    x, _, z = f.mercy.mover.position
                    f.match.collision_world = World(FLOOR + wall(x + 2, z - 4, x + 2, z + 4))
                self.assertEqual(self.cast(f), 0)
                self.assertEqual(health.hack_until, 0)
                self.assertEqual(health.total, 200)
                guardian.step(4, guardian_tests.SHIFT)
                self.assertTrue(guardian.instance.state(0).active)

    def test_actual_emp_preserves_active_descent_and_allows_restart_while_hacked(self):
        descent = descent_tests.AngelicDescentTests()
        descent.setUp()
        self.addCleanup(descent.doCleanups)
        f = descent.fixture
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[sombra_hack.HERO]))
        f.enemy.body_script.ult_seconds = 0
        descent.place_at_elevated_firing_platform(40)
        descent.step(30, JUMP)
        state = descent.descent()
        self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)
        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        health = f.fight.vitals_of(target)

        height = f.mercy.mover.position[1]
        self.assertEqual(self.cast(f, mercy_buttons=JUMP), 0)
        self.assertGreater(health.hack_until, f.fight.now + 4)
        self.assertEqual(health.total, 200)
        self.assertTrue(state.active)
        self.assertAlmostEqual(height - f.mercy.mover.position[1], 2 * 45 * combat.TICK, delta=0.05)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

        descent.step(12)
        self.assertFalse(state.active)
        self.assertLess(f.mercy.mover.velocity[1], -5)
        descent.step(30, JUMP)
        self.assertGreater(health.hack_until, f.fight.now)
        self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

    def test_actual_emp_spares_revived_mercy_until_immunity_expires(self):
        res = resurrect_tests.ResurrectTests()
        res.setUp(ally_hero=sombra_hack.MERCY)
        self.addCleanup(res.doCleanups)
        self.assertTrue(res.match.switch_hero(res.enemy, heroes()[sombra_hack.HERO]))
        res.enemy.mover.position[:] = (4, 1, 3)
        res.enemy.mover.body = None
        res.enemy.body_script.ult_seconds = 0
        body = res.ally.body
        res.cast()
        self.assertEqual(res.ally.body, body)
        revived = res.fight.vitals_of(res.target)
        caster = res.fight.vitals_of(next(t for t in res.fight._targets() if t.owner is res.mercy))
        script = res.ally.body_script.component

        self.assertEqual(self.cast(res), 0)
        self.assertGreater(caster.hack_until, res.fight.now + 4)
        self.assertGreater(revived.invulnerable_until, res.fight.now)
        self.assertEqual(revived.hack_until, 0)
        self.assertEqual(revived.total, 200)
        self.assertFalse(any(i.graph.index == sombra_hack.EFFECT for i in script.instances.values()))

        for _ in range(150):
            if revived.invulnerable_until <= res.fight.now:
                break
            res.run.step({})
        self.assertLessEqual(revived.invulnerable_until, res.fight.now)
        self.assertEqual(res.ally.body, body)
        self.assertEqual(self.cast(res), 0)
        self.assertGreater(revived.hack_until, res.fight.now + 4)
        self.assertEqual(revived.total, 200)
        self.assertTrue(any(i.graph.index == sombra_hack.EFFECT and i.source_entity() == res.enemy.body
                            for i in script.instances.values()))

    def test_actual_emp_removes_zarya_shield_pool_without_damaging_mercy(self):
        _, f, health = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[0x02E0000000000068]))
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x, y, z + 3)
        f.ally.mover.body = None
        target = next(t for t in f.fight._targets() if t.owner is f.ally)
        zarya = f.fight.vitals_of(target)
        self.assertEqual(zarya.total, 400)
        self.assertEqual(self.cast(f), 0)
        self.assertEqual(zarya.total, 200)
        self.assertEqual(health.total, 200)
        self.assertGreater(health.hack_until, f.fight.now)

    def test_actual_emp_removes_sigma_grasp_shields_and_base_shields(self):
        defense = defense_tests.MercyDeflectionTests()
        self.addCleanup(defense.doCleanups)
        f, sigma = defense.grasp_shields_from_blaster()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[sombra_hack.HERO]))
        f.ally.body_script.ult_seconds = 0
        body_health = sigma.now[combat.HEALTH_POOL]
        self.assertEqual(self.cast(f, caster=f.ally), 0)
        self.assertEqual(sigma.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(sigma.temporary_shields[SHIELD_TAG].now, 0)
        self.assertEqual(sigma.total, body_health)
        self.assertTrue(any(i.graph.index == sombra_hack.EFFECT and i.source_entity() == f.ally.body
                            for i in f.enemy.body_script.component.instances.values()))
        self.assertNotIn(f.enemy.body, f.fight.grasp_shields)

    def test_actual_emp_breaks_projected_barrier_and_spares_mercy_from_hack(self):
        guardian, f, health = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[0x02E0000000000068]))
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x, y, z + 8)
        f.ally.mover.body = None
        yaw, pitch = aim(f.fight._eye(f.ally), f.fight._eye(f.mercy))
        f.run.step({f.ally: (yaw, pitch, 16)})
        self.assertTrue(f.fight.zarya_barriers.protects(f.mercy))
        with patch.object(sombra_hack, "submit_emp_health", wraps=sombra_hack.submit_emp_health) as hit:
            self.assertEqual(self.cast(f), 0)
        mercy_nodes = {
            call.args[1].node.pos for call in hit.call_args_list
            if call.args[1].instance.graph.index == sombra_hack.EMP_HIT
            and call.args[1].instance.component.entity == f.mercy.body
        }
        self.assertEqual(mercy_nodes, {3})
        self.assertEqual(f.fight.zarya_barriers.projected[f.ally.body].remaining, 0)
        self.assertAlmostEqual(f.ally.body_script.component.entity_var(87).value(), 40)
        self.assertFalse(f.fight.zarya_barriers.protects(f.mercy))
        self.assertEqual(health.total, 200)
        self.assertEqual(health.hack_until, 0)
        guardian.step(4, guardian_tests.SHIFT)
        self.assertTrue(guardian.instance.state(0).active)

    def raised_barrier(self, *, blocked=None):
        assets = barrier_tests.MercyDynamicBarrierTests()
        assets.setUp()
        self.addCleanup(assets.doCleanups)
        _, f, health = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[barrier_tests.REINHARDT]))
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x + 2, y, z)
        f.ally.mover.body = None
        if blocked == "friendly":
            f.ally.team = f.enemy.team
        if blocked == "wall":
            f.match.collision_world = World(FLOOR + wall(x + 3.7, z - 5, x + 3.7, z + 5))
        yaw, pitch = aim(f.fight._eye(f.ally), f.fight._eye(f.enemy))
        target = next(t for t in f.fight._targets() if t.owner is f.ally)
        tank = f.fight.vitals_of(target)
        original_health = tank.total
        hold = (yaw, pitch, SECONDARY)
        for _ in range(45):
            f.run.step({f.ally: hold})
            if f.fight.barriers[f.ally.body].active:
                break
        self.assertTrue(f.fight.barriers[f.ally.body].active)
        f.enemy.body_script.set_ult_charge(1400)
        for frame in range(45):
            f.run.step({f.enemy: (0, 0, 32 if frame == 0 else 0), f.ally: hold})
        self.assertEqual(tank.total, original_health)
        self.assertEqual(health.total, 200)
        return f, health, f.fight.barriers[f.ally.body]

    def test_emp_breaks_raised_enemy_shield_and_hacks_mercy_behind_it(self):
        f, health, barrier = self.raised_barrier()
        self.assertEqual(barrier.current, 0)
        self.assertFalse(barrier.active)
        self.assertGreater(barrier.broken_until, f.fight.now)
        self.assertGreater(health.hack_until, f.fight.now)

    def test_emp_leaves_friendly_and_occluded_raised_shields_intact(self):
        for blocked in ("friendly", "wall"):
            with self.subTest(blocked=blocked):
                _, health, barrier = self.raised_barrier(blocked=blocked)
                self.assertTrue(barrier.active)
                self.assertEqual(barrier.current, barrier.maximum)
                if blocked == "wall":
                    self.assertEqual(health.hack_until, 0)

    def sphere(self):
        _, f, health = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[winston_barriers.HERO]))
        f.enemy.team = f.mercy.team
        f.ally.team = 1 - f.mercy.team
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x, y, z + 8)
        f.ally.mover.body = None
        equip(f.run, f.mercy)
        f.run.step({f.ally: (0, 0, 16)})
        for _ in range(12):
            f.run.step({})
            if f.ally.body in f.fight.winston_barriers.actors:
                break
        bubble = f.fight.winston_barriers.actors[f.ally.body]
        self.assertEqual(bubble.health, 700)
        return f, health, bubble

    def shoot_ally(self, f):
        x, y, z = f.ally.mover.position
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 1, z))
        f.run.step({f.mercy: (yaw, pitch, FIRE)})
        for _ in range(27):
            f.run.step({})

    def test_deployed_sphere_stops_mercy_bullet_and_emp_breaks_without_body_in_range(self):
        f, mercy, bubble = self.sphere()
        target = next(t for t in f.fight._targets() if t.owner is f.ally)
        tank = f.fight.vitals_of(target)
        before = tank.total
        self.shoot_ally(f)
        self.assertEqual(tank.total, before)
        self.assertEqual(bubble.health, 680)
        original = tuple(f.ally.mover.position)
        f.ally.mover.position[2] += 28
        f.ally.mover.body = None
        self.assertEqual(self.cast(f), 0)
        self.assertEqual(bubble.health, 0)
        self.assertNotIn(f.ally.body, f.fight.winston_barriers.actors)
        self.assertEqual(mercy.hack_until, 0)
        self.assertEqual(tank.total, before)
        f.ally.mover.position[:] = original
        f.ally.mover.body = None
        self.shoot_ally(f)
        self.assertEqual(tank.total, before - 15)

    def test_friendly_emp_leaves_sphere_and_it_expires_on_world_time(self):
        f, _, bubble = self.sphere()
        f.enemy.team = f.ally.team
        self.assertEqual(self.cast(f), 0)
        self.assertEqual(bubble.health, 700)
        self.assertIn(f.ally.body, f.fight.winston_barriers.actors)
        while f.fight.now < bubble.expires:
            f.run.step({})
        self.assertNotIn(f.ally.body, f.fight.winston_barriers.actors)

    def test_winston_sphere_protects_mercy_from_actual_enforcer_pellets(self):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f, mercy, bubble = self.sphere()
        f.ally.team = f.mercy.team
        x, y, z = bubble.centre
        f.mercy.mover.position[:] = (x, y - 0.1, z - 3.5)
        f.ally.mover.position[:] = (x + 7, y - 0.1, z)
        f.enemy.mover.position[:] = (x - 7, y - 0.1, z)
        for player in f.run.players:
            player.mover.body = None
            player.client.bot_stream = Mock(return_value=None)
        definition = next(d for d in fixture.catalog.values() if d.name == "Enforcer")
        bot = npc.spawn(f.match, definition, (x, y - 0.1, z - 7), team=1 - f.mercy.team)
        fixture.tick(f.run, f.mercy, bot, 2, buttons=0)
        npc_control.start(f.match, f.mercy)
        for _ in range(120):
            fixture.tick(f.run, f.mercy, bot, 1, buttons=0)
            if bubble.health < 700:
                break
        fixture.tick(f.run, f.mercy, bot, 8, buttons=0)
        self.assertEqual(bubble.health, 650)
        self.assertEqual(mercy.total, 200)

    def test_winston_sphere_protects_mercy_from_actual_soldier_rifle(self):
        f, mercy, bubble = self.sphere()
        f.ally.team = f.mercy.team
        f.enemy.team = 1 - f.mercy.team
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[SOLDIER]))
        x, y, z = bubble.centre
        f.mercy.mover.position[:] = (x, y - 0.1, z - 3.5)
        f.enemy.mover.position[:] = (x, y - 0.1, z - 7)
        f.mercy.mover.body = f.enemy.mover.body = None
        yaw, pitch = aim(f.fight._eye(f.enemy), (x, y + 0.9, z - 3.5))
        for _ in range(60):
            f.run.step({f.enemy: (yaw, pitch, FIRE)})
            if bubble.health < 700:
                break
        self.assertEqual(bubble.health, 680)
        self.assertEqual(mercy.total, 200)

    def orisa_deployment(self, *, protect_mercy=False):
        geometry = Path(__file__).resolve().parents[3] / "013E/orisa_geometry_174.json"
        self.assertTrue(geometry.is_file())
        self.enterContext(patch.dict("os.environ", {"ORPHEUS_ORISA_GEOMETRY": str(geometry)}))
        _, f, _ = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[0x02E000000000013E]))
        f.enemy.team = f.mercy.team
        f.ally.team = f.mercy.team if protect_mercy else 1 - f.mercy.team
        if protect_mercy:
            f.enemy.team = 1 - f.mercy.team
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x, y, z + 10)
        f.ally.mover.body = None
        equip(f.run, f.mercy)
        f.run.step({f.ally: (-32768, 3900, 16)})
        host = f.fight.orisa_barriers
        saw_flight = False
        for _ in range(160):
            f.run.step({f.ally: (-32768, 3900, 0)})
            saw_flight |= bool(host.flights)
            if host.active:
                break
        self.assertTrue(saw_flight)
        barrier, = host.active.values()
        self.assertEqual(barrier.script.graph.index, 0x0BD8)
        self.assertAlmostEqual(barrier.position[1], y)
        self.assertEqual(barrier.health, 600)
        return f, host, barrier

    def test_orisa_world_deployment_intercepts_mercy_then_emp_breaks_it(self):
        f, host, barrier = self.orisa_deployment()
        target = next(t for t in f.fight._targets() if t.owner is f.ally)
        vitals = f.fight.vitals_of(target)
        before = vitals.total
        self.shoot_ally(f)
        self.assertEqual(barrier.health, 580)
        self.assertEqual(vitals.total, before)
        self.assertEqual(self.cast(f), 0)
        self.assertEqual(barrier.health, 0)
        self.assertFalse(host.active)
        self.shoot_ally(f)
        self.assertLess(vitals.total, before)

    def test_orisa_deployment_rejects_early_recast_and_expires_on_world_clock(self):
        f, host, barrier = self.orisa_deployment()
        f.run.step({f.ally: (-32768, 3900, 16)})
        for _ in range(10):
            f.run.step({f.ally: (-32768, 3900, 0)})
        self.assertEqual(list(host.active.values()), [barrier])
        self.assertFalse(host.flights)
        self.assertFalse(host.pending)
        frame = barrier.until + 1
        f.match.update(frame * combat.TICK, frame, frames=False)
        self.assertFalse(host.active)

    def sigma_deployment(self, *, protect_mercy=False):
        model = Path(__file__).resolve().parents[3] / "023B/barrier-resources/00000000C015.00C"
        self.assertTrue(model.is_file())
        self.enterContext(patch.dict("os.environ", {"ORPHEUS_SIGMA_BARRIER_MODEL": str(model)}))
        _, f, _ = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[sigma_barrier.HERO]))
        f.enemy.team = f.mercy.team
        f.ally.team = f.mercy.team if protect_mercy else 1 - f.mercy.team
        if protect_mercy:
            f.enemy.team = 1 - f.mercy.team
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x, y, z + 10)
        f.ally.mover.body = None
        equip(f.run, f.mercy)
        for frame in range(48):
            f.run.step({f.ally: (-32768, 0, SECONDARY if frame < 20 else 0)})
        barrier, = f.fight.sigma_barriers.values()
        self.assertTrue(barrier.child_request_seen)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.phase, 3)
        self.assertEqual(barrier.vitals.total, 700)
        return f, barrier

    def test_sigma_experimental_barrier_intercepts_mercy_and_emp_breaks_it(self):
        f, barrier = self.sigma_deployment()
        target = next(t for t in f.fight._targets() if t.owner is f.ally)
        vitals = f.fight.vitals_of(target)
        self.shoot_ally(f)
        self.assertEqual(barrier.vitals.total, 680)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(self.cast(f), 0)
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.vitals.total, 0)
        before = vitals.total
        self.shoot_ally(f)
        self.assertEqual(vitals.total, before - 20)

    def test_sigma_recall_rejects_early_redeploy_and_recharges(self):
        f, barrier = self.sigma_deployment()
        self.shoot_ally(f)
        self.assertEqual(barrier.vitals.total, 680)
        f.run.step({f.ally: (-32768, 0, SECONDARY)})
        f.run.step({})
        self.assertFalse(barrier.active)
        ability = barrier.instance.states[111]
        self.assertGreater(ability.remaining(), 1.9)
        f.run.step({f.ally: (-32768, 0, SECONDARY)})
        f.run.step({})
        self.assertFalse(barrier.active)
        for _ in range(110):
            f.run.step({})
        self.assertEqual(barrier.vitals.total, 680)
        for _ in range(60):
            f.run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(barrier.vitals.total, 700)
        for frame in range(30):
            f.run.step({f.ally: (-32768, 0, SECONDARY if frame < 20 else 0)})
        self.assertTrue(barrier.active)
        self.shoot_ally(f)
        self.assertEqual(barrier.vitals.total, 680)

    def test_sigma_barrier_protects_mercy_from_actual_soldier_rifle(self):
        f, barrier = self.sigma_deployment(protect_mercy=True)
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[SOLDIER]))
        x, y, z = f.ally.mover.position
        f.mercy.mover.position[:] = (x, y, z)
        f.ally.mover.position[:] = (x + 6, y, z)
        f.enemy.mover.position[:] = (x, y, z - 10)
        for player in f.run.players:
            player.mover.body = None
        health = f.fight.vitals[f.mercy.body]
        yaw, pitch = aim(f.fight._eye(f.enemy), (x, y + 1, z))
        for _ in range(60):
            f.run.step({f.enemy: (yaw, pitch, FIRE)})
            if barrier.vitals.total < 700:
                break
        self.assertEqual(barrier.vitals.total, 680)
        self.assertEqual(health.total, 200)

    def test_orisa_barrier_protects_mercy_from_actual_enforcer_pellets(self):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f, _, barrier = self.orisa_deployment(protect_mercy=True)
        x, y, z = f.ally.mover.position
        f.mercy.mover.position[:] = (x, y, z)
        f.ally.mover.position[:] = (x + 6, y, z)
        f.enemy.mover.position[:] = (x - 6, y, z)
        for player in f.run.players:
            player.mover.body = None
            player.client.bot_stream = Mock(return_value=None)
        definition = next(d for d in fixture.catalog.values() if d.name == "Enforcer")
        bot = npc.spawn(f.match, definition, (x, y, barrier.position[2] - 2), team=1 - f.mercy.team)
        fixture.tick(f.run, f.mercy, bot, 2, buttons=0)
        npc_control.start(f.match, f.mercy)
        for _ in range(120):
            fixture.tick(f.run, f.mercy, bot, 1, buttons=0)
            if barrier.health < 600:
                break
        fixture.tick(f.run, f.mercy, bot, 8, buttons=0)
        self.assertEqual(barrier.health, 550)
        self.assertEqual(f.fight.vitals[f.mercy.body].total, 200)

    def test_helix_detonates_on_winston_sphere_without_splash_leak_on_break(self):
        for durability, remaining in ((700, 580), (20, 0)):
            with self.subTest(durability=durability):
                f, mercy, bubble = self.sphere()
                f.ally.team = f.mercy.team
                f.enemy.team = 1 - f.mercy.team
                self.assertTrue(f.match.switch_hero(f.enemy, heroes()[SOLDIER]))
                x, y, z = bubble.centre
                f.mercy.mover.position[:] = (x, y - 0.1, z - 3.5)
                f.enemy.mover.position[:] = (x, y - 0.1, z - 7)
                f.mercy.mover.body = f.enemy.mover.body = None
                bubble.health = durability
                yaw, pitch = aim(f.fight._eye(f.enemy), (x, y + 0.9, z - 3.5))
                f.run.step({f.enemy: (yaw, pitch, SECONDARY)})
                for _ in range(60):
                    f.run.step({})
                self.assertEqual(bubble.health, remaining)
                self.assertEqual(mercy.total, 200)
                self.assertFalse(f.fight.rockets)
                self.assertEqual(f.ally.body in f.fight.winston_barriers.actors, bool(remaining))
                if not remaining:
                    for _ in range(520):
                        f.run.step({})
                    f.run.step({f.enemy: (yaw, pitch, SECONDARY)})
                    for _ in range(35):
                        f.run.step({})
                    self.assertEqual(mercy.total, 80)

    def test_helix_detonates_on_orisa_mesh_without_harming_mercy(self):
        f, host, barrier = self.orisa_deployment(protect_mercy=True)
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[SOLDIER]))
        x, y, z = barrier.position
        f.enemy.mover.position[:] = (x, y, z - 4)
        f.ally.mover.position[:] = (x + 7, y, z)
        f.enemy.mover.body = f.ally.mover.body = None
        origin = f.fight._eye(f.enemy)
        depth = host.geometry.cast(barrier.position, barrier.yaw, origin, (0, 0, 1), 12)
        self.assertIsNotNone(depth)
        f.mercy.mover.position[:] = (x, y, origin[2] + depth + 1.4)
        f.mercy.mover.body = None
        health = f.fight.vitals[f.mercy.body]
        f.run.step({f.enemy: (0, 0, SECONDARY)})
        for _ in range(45):
            f.run.step({})
        self.assertEqual(barrier.health, 480)
        self.assertEqual(health.total, 200)

    def test_mercy_melee_damages_orisa_mesh_and_body_independently(self):
        f, host, shield = self.orisa_deployment()
        x, y, z = shield.position
        f.mercy.mover.position[:] = (x, y, z - 4)
        f.mercy.mover.body = None
        origin = f.fight._eye(f.mercy)
        depth = host.geometry.cast(shield.position, shield.yaw, origin, (0, 0, 1), 12)
        self.assertIsNotNone(depth)
        front = origin[2] + depth
        f.mercy.mover.position[:] = (x, y, front - 1.25)
        f.ally.mover.position[:] = (x, y, front + 0.65)
        f.mercy.mover.body = f.ally.mover.body = None
        target = next(t for t in f.fight._targets() if t.owner is f.ally)
        vitals = f.fight.vitals_of(target)
        before = vitals.total
        f.run.step({f.mercy: (0, 0, 0x800)})
        for _ in range(30):
            f.run.step({})
        self.assertEqual(shield.health, 570)
        self.assertEqual(vitals.total, before - 25)

    def test_actual_trooper_melee_hits_winston_sphere_and_mercy(self):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f, mercy, sphere = self.sphere()
        f.ally.team = f.mercy.team
        x, y, z = sphere.centre
        f.mercy.mover.position[:] = (x, y - 0.1, z - 4.25)
        f.ally.mover.position[:] = (x + 6, y - 0.1, z)
        f.enemy.mover.position[:] = (x - 6, y - 0.1, z)
        for player in f.run.players:
            player.mover.body = None
            player.client.bot_stream = Mock(return_value=None)
        definition = next(d for d in fixture.catalog.values() if d.name == "Trooper")
        bot = npc.spawn(f.match, definition, (x, y - 0.1, z - 6.25), team=1 - f.mercy.team)
        fixture.tick(f.run, f.mercy, bot, 12, buttons=0)
        npc_control.start(f.match, f.mercy, melee=True)
        fixture.tick(f.run, f.mercy, bot, 62, buttons=0)
        self.assertEqual(sphere.health, 670)
        self.assertEqual(mercy.total, 170)

    def test_helix_terrain_splash_damages_nearby_visible_sphere_only(self):
        for occluded in (False, True):
            with self.subTest(occluded=occluded):
                f, mercy, sphere = self.sphere()
                f.ally.team = f.mercy.team
                f.enemy.team = 1 - f.mercy.team
                self.assertTrue(f.match.switch_hero(f.enemy, heroes()[SOLDIER]))
                x, y, z = sphere.centre
                f.mercy.mover.position[:] = (x, y - 0.1, z - 3.5)
                f.enemy.mover.position[:] = (x, y - 0.1, z - 8)
                f.mercy.mover.body = f.enemy.mover.body = None
                if occluded:
                    f.match.collision_world = World(FLOOR + wall(x - 8, z - 5.4, x + 8, z - 5.4))
                yaw, pitch = aim(f.fight._eye(f.enemy), (x, y - 0.1, z - 5.15))
                f.run.step({f.enemy: (yaw, pitch, SECONDARY)})
                for _ in range(55):
                    f.run.step({})
                self.assertEqual(sphere.health, 700 if occluded else 620)
                self.assertEqual(mercy.total, 200)
                self.assertFalse(f.fight.rockets)

    def mount_grenade_status(self):
        self.assertTrue(status_tests.STATUS_BUNDLE.is_file())
        self.addCleanup(npc_tests.clear_graphs)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", status_tests.STATUS_BUNDLE))
        npc_tests.clear_graphs()
        self.assertTrue(healing_status.Statuses.available())

    def throw_grenade_at_mercy(self, f):
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[status_tests.ANA]))
        x, y, z = f.mercy.mover.position
        yaw, pitch = aim(f.fight._eye(f.enemy), (x, y + 1, z))
        f.run.step({f.enemy: (yaw, pitch, 16)})
        self.assertTrue(any(rocket.shooter is f.enemy for rocket in f.fight.rockets))
        for _ in range(45):
            f.run.step({})

    def test_ana_grenade_detonates_on_winston_sphere_and_spares_mercy(self):
        for durability, remaining in ((700, 650), (20, 0)):
            with self.subTest(durability=durability):
                self.mount_grenade_status()
                f, mercy, sphere = self.sphere()
                f.ally.team = f.mercy.team
                f.enemy.team = 1 - f.mercy.team
                x, y, z = sphere.centre
                f.mercy.mover.position[:] = (x, y - 0.1, z - 3.5)
                f.enemy.mover.position[:] = (x, y - 0.1, z - 7)
                f.mercy.mover.body = f.enemy.mover.body = None
                sphere.health = durability
                self.throw_grenade_at_mercy(f)
                self.assertEqual(sphere.health, remaining)
                self.assertEqual(mercy.total, 200)
                self.assertEqual(f.fight.healing_status.scale(f.mercy.body), 1)
                applied = f.fight.healing_status.applied.values()
                self.assertFalse(any(item.target == f.mercy.body for item in applied))

    def test_ana_grenade_spends_projected_barrier_without_applying_antiheal(self):
        self.mount_grenade_status()
        _, f, mercy = self.encounter()
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[0x02E0000000000068]))
        x, y, z = f.mercy.mover.position
        f.ally.mover.position[:] = (x, y, z + 5)
        f.ally.mover.body = None
        yaw, pitch = aim(f.fight._eye(f.ally), f.fight._eye(f.mercy))
        f.run.step({f.ally: (yaw, pitch, 16)})
        self.assertTrue(f.fight.zarya_barriers.protects(f.mercy))
        self.throw_grenade_at_mercy(f)
        self.assertEqual(f.fight.zarya_barriers.projected[f.ally.body].remaining, 150)
        self.assertEqual(mercy.total, 200)
        self.assertEqual(f.fight.healing_status.scale(f.mercy.body), 1)

    def test_friendly_grenade_heals_mercy_through_friendly_sphere(self):
        self.mount_grenade_status()
        f, mercy, sphere = self.sphere()
        f.ally.team = f.enemy.team = f.mercy.team
        x, y, z = sphere.centre
        f.mercy.mover.position[:] = (x, y - 0.1, z - 3.5)
        f.enemy.mover.position[:] = (x, y - 0.1, z - 7)
        f.mercy.mover.body = f.enemy.mover.body = None
        mercy.damage(120)
        f.fight._sync_body_health(f.mercy, mercy)
        self.throw_grenade_at_mercy(f)
        self.assertEqual(sphere.health, 700)
        self.assertEqual(mercy.total, 180)
        self.assertAlmostEqual(f.fight.healing_status.scale(f.mercy.body), 1.5)
