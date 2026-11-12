"""Shadow Step command/geometry/cast lifecycle for the declared reconstruction."""

import math
import unittest

import test_game_reaper as reaper_tests

from ow174.game import reaper
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.script import runtime


class ShadowStepTests(unittest.TestCase):
    def scene(self):
        run, owner, victim, _ = reaper_tests.ReaperTests().scenario()
        victim.spawned = False
        instance = next(i for i in owner.body_script.component.instances.values()
                        if i.graph.index == reaper.SHADOW_GRAPH)
        return run, owner, victim, instance

    def select(self, run, owner, *, pitch=5000):
        run.step({owner: (0, pitch, 16)})
        run.step({owner: (0, pitch, 0)})

    def confirm(self, run, owner, *, pitch=5000):
        run.step({owner: (0, pitch, 1)})

    def test_shadow_during_wraith_refuses_without_event_loop_or_held_retry(self):
        run, owner, _, instance = self.scene()
        run.step({owner: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        script = owner.body_script
        run.step({owner: (0, 5000, 16)})
        self.assertIs(owner.body_script, script)
        self.assertTrue(reaper.intangible(owner))
        self.assertFalse(instance.state(0).active)
        self.assertTrue(instance.state(1).active)
        counter = instance.state(6).counter
        for _ in range(220):
            run.step({owner: (0, 5000, 16)})
        self.assertIs(owner.body_script, script)
        self.assertFalse(reaper.intangible(owner))
        self.assertFalse(instance.state(0).active)
        self.assertEqual(instance.state(6).counter, counter)
        run.step({})
        self.select(run, owner)
        self.assertTrue(instance.state(0).active)

    def test_visible_floor_selection_locked_cast_teleport_recovery_and_cooldown(self):
        run, owner, _, instance = self.scene()
        start = tuple(owner.mover.position)
        self.select(run, owner)
        self.assertTrue(instance.state(0).active)
        self.assertTrue(instance.vars[983].value())
        record = run.match.combat.reaper_shadows[owner.body]
        target = record["target"]
        self.assertLess(math.dist(start, target), 35)
        self.confirm(run, owner)
        self.assertFalse(instance.state(0).active)
        self.assertTrue(instance.state(9).active)
        for _ in range(62):
            run.step({})
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertFalse(reaper.intangible(owner))
        for _ in range(3):
            run.step({})
        self.assertTrue(reaper.intangible(owner))
        for _ in range(31):
            run.step({owner: (16000, -4000, 0)})  # later aim cannot retarget the locked cast
        self.assertTrue(record["moved"])
        self.assertLess(math.dist(owner.mover.position, target), 0.002)
        self.assertFalse(reaper.intangible(owner))
        for _ in range(40):
            run.step({})
        self.assertGreater(instance.vars[61].value(), 9.7)
        self.assertLess(instance.vars[61].value(), 10)
        for _ in range(5):
            run.step({})
        self.assertLess(math.dist(owner.mover.position, target), 0.002)  # physics pose agrees

    def test_shadow_cooldown_retires_on_short_mode_respawn(self):
        from ow174.game import combat

        fixture = reaper_tests.ReaperTests()
        run, owner, enemy, _ = fixture.scenario(team_mode=True)
        owner.team, enemy.team = 0, 1
        enemy.spawned = False
        fight = run.match.combat
        old = owner.body_script
        old_body = owner.body
        self.select(run, owner)
        self.confirm(run, owner)
        record = fight.reaper_shadows[old_body]
        for _ in range(140):
            run.step({})
        self.assertTrue(record["moved"])
        delay = combat.respawn_seconds(run.match)
        shadow = next(i for i in old.component.instances.values() if i.graph.index == reaper.SHADOW_GRAPH)
        self.assertGreater(shadow.vars[61].value(), delay)
        x, y, z = owner.mover.position
        enemy.mover.position[:] = (x + 1.5, y, z)
        enemy.mover.body = None
        enemy.spawned = True
        run.step({})
        fight.vitals[old_body].damage(249)
        fixture.fire(run, enemy, owner)
        self.assertTrue(fight.is_dead(owner))
        for _ in range(round(delay / combat.TICK) + 2):
            if owner.body != old_body:
                break
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertTrue(old.retired)
        self.assertNotIn(old_body, fight.reaper_shadows)
        enemy.spawned = False
        self.select(run, owner)
        fresh = fight.reaper_shadows[owner.body]
        self.assertIsNotNone(fresh["target"])
        self.confirm(run, owner)
        for _ in range(100):
            run.step({})
        self.assertTrue(fresh["moved"])
        self.assertLess(math.dist(owner.mover.position, fresh["target"]), 0.002)

    def test_selection_cancel_spends_no_cooldown_and_can_reopen(self):
        run, owner, _, instance = self.scene()
        start = tuple(owner.mover.position)
        self.select(run, owner)
        run.step({owner: (0, 5000, 2)})
        run.step({})
        self.assertFalse(instance.state(0).active)
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertEqual(tuple(owner.mover.position), start)
        self.select(run, owner)
        self.assertTrue(instance.state(0).active)

    def test_horizontal_or_beyond_range_aim_cannot_confirm_or_consume_cooldown(self):
        for pitch in (0, 100):
            with self.subTest(pitch=pitch):
                run, owner, _, instance = self.scene()
                self.select(run, owner, pitch=pitch)
                self.assertFalse(instance.vars[983].value())
                self.confirm(run, owner, pitch=pitch)
                for _ in range(140):
                    run.step({owner: (0, pitch, 0)})
                self.assertTrue(instance.state(0).active)
                self.assertEqual(instance.vars[61].value(), 0)
                self.assertIsNone(run.match.combat.reaper_shadows[owner.body]["cast"])

    def test_live_actor_occupancy_rejects_landing(self):
        run, owner, victim, instance = self.scene()
        self.select(run, owner)
        target = run.match.combat.reaper_shadows[owner.body]["target"]
        victim.mover.position[:] = target
        victim.mover.body = None
        victim.spawned = True
        self.confirm(run, owner)
        self.assertFalse(instance.vars[983].value())
        self.assertIsNone(run.match.combat.reaper_shadows[owner.body]["cast"])

    def test_real_low_ceiling_rejects_standing_capsule_clearance(self):
        run, owner, _, instance = self.scene()
        self.select(run, owner)
        x, y, z = run.match.combat.reaper_shadows[owner.body]["target"]
        lo, hi, roof = -5000, 5000, y + 1.2
        run.match.collision_world = World([
            (lo, y, lo, lo, y, hi, hi, y, hi), (lo, y, lo, hi, y, hi, hi, y, lo),
            (x-0.7, roof, z-0.7, x-0.7, roof, z+0.7, x+0.7, roof, z+0.7),
            (x-0.7, roof, z-0.7, x+0.7, roof, z+0.7, x+0.7, roof, z-0.7),
        ])
        self.assertFalse(run.match.combat._shadow_landing(owner, (x, y, z)))
        self.confirm(run, owner)
        self.assertFalse(instance.vars[983].value())
        self.assertIsNone(run.match.combat.reaper_shadows[owner.body]["cast"])

    def test_real_vertical_occluder_cannot_be_selected_as_walkable_landing(self):
        run, owner, _, instance = self.scene()
        x, y, z = owner.mover.position
        lo, hi, wall = -5000, 5000, z + 1
        run.match.collision_world = World([
            (lo, y, lo, lo, y, hi, hi, y, hi), (lo, y, lo, hi, y, hi, hi, y, lo),
            (x-5, y, wall, x+5, y, wall, x+5, y+5, wall),
            (x-5, y, wall, x+5, y+5, wall, x-5, y+5, wall),
        ])
        self.select(run, owner)
        self.assertFalse(instance.vars[983].value())
        self.confirm(run, owner)
        self.assertIsNone(run.match.combat.reaper_shadows[owner.body]["cast"])

    def test_destination_is_revalidated_when_an_actor_arrives_during_cast(self):
        run, owner, victim, instance = self.scene()
        self.select(run, owner)
        record = run.match.combat.reaper_shadows[owner.body]
        self.confirm(run, owner)
        start = tuple(owner.mover.position)
        victim.mover.position[:] = record["target"]
        victim.mover.body = None
        victim.spawned = True
        for _ in range(140):
            run.step({})
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertTrue(record["moved"])
        self.assertGreater(instance.vars[61].value(), 9.5)

    def test_cooldown_attempt_does_not_bypass_or_permanently_disable_input(self):
        run, owner, _, instance = self.scene()
        self.select(run, owner)
        self.confirm(run, owner)
        for _ in range(140):
            run.step({})
        counter = instance.state(0).counter
        self.select(run, owner)
        self.assertEqual(instance.state(0).counter, counter)
        self.assertFalse(instance.state(0).active)
        for _ in range(640):
            run.step({})
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertFalse(instance.state(0).active)
        self.select(run, owner)
        self.assertTrue(instance.state(0).active)
        self.assertEqual(instance.state(0).counter, counter + 1)

    def test_death_and_early_scope_abort_cannot_teleport(self):
        for reason in ("death", "abort", "switch"):
            with self.subTest(reason=reason):
                run, owner, victim, instance = self.scene()
                self.select(run, owner)
                self.confirm(run, owner)
                start = tuple(owner.mover.position)
                if reason == "death":
                    target = next(t for t in run.match.combat._targets() if t.owner is owner)
                    run.match.combat._damage(victim, target, 1000, False, None, run.frame)
                elif reason == "abort":
                    instance.state(19).request(runtime.ABORT)
                    owner.body_script.component.run_now()
                else:
                    run.match.switch_hero(owner, victim.hero)
                for _ in range(140):
                    run.step({})
                self.assertEqual(tuple(owner.mover.position), start)

    def test_damage_is_blocked_only_during_authored_shadow_immunity_scope(self):
        run, owner, victim, _ = self.scene()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        self.select(run, owner)
        self.confirm(run, owner)
        fight._damage(victim, target, 10, False, None, run.frame)
        self.assertEqual(fight.vitals[owner.body].total, 240)
        for _ in range(65):
            run.step({})
        self.assertTrue(reaper.intangible(owner))
        fight._damage(victim, target, 100, False, None, run.frame)
        self.assertEqual(fight.vitals[owner.body].total, 240)
        for _ in range(35):
            run.step({})
        fight._damage(victim, target, 10, False, None, run.frame)
        self.assertEqual(fight.vitals[owner.body].total, 230)


    def test_cast_blocks_all_throttle_axes_on_next_frame_and_restores_after_end(self):
        run, owner, _, _instance = self.scene()
        self.select(run, owner)
        self.confirm(run, owner)
        start = tuple(owner.mover.position)
        locks = [m for m in owner.mover.mods.values() if m.index in (17, 18, 19)]
        self.assertEqual({m.index for m in locks}, {17, 18, 19})
        for mod in locks:
            self.assertFalse(mod.acts(run.frame))
            self.assertTrue(mod.acts(run.frame + 1))
        def command(right, forward):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame, frames=False)
            owner.take_commands([Command(run.frame, yaw=0, pitch=5000, right=right,
                                          forward=forward, buttons=0)])
        for right, forward in [(127, 0), (-127, 0), (0, 127), (0, -127)]:
            for _ in range(8):
                command(right, forward)
                self.assertEqual(owner.mover.throttles, (right, forward))
                self.assertEqual(tuple(owner.mover.position), start)
                self.assertAlmostEqual(owner.mover.velocity[0], 0)
                self.assertAlmostEqual(owner.mover.velocity[2], 0)
        for _ in range(120):
            run.step({})
        destination = tuple(owner.mover.position)
        self.assertNotEqual(destination, start)
        self.assertFalse(any(m.index in (17, 18, 19) and m.acts(run.frame + 1)
                             for m in owner.mover.mods.values()))
        for _ in range(30):
            command(0, 127)
        self.assertGreater(owner.mover.position[2], destination[2] + 1)
        self.assertAlmostEqual(owner.mover.velocity[2], 5.5, delta=0.01)



if __name__ == "__main__":
    unittest.main()
