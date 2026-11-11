"""Actual Zarya barrier activations and Genji damage through independent Pets."""

from dataclasses import replace

from test_game_combat import aim
from test_game_mover import FLOOR, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import barriers, combat
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr


def draw_dragonblade(run, genji):
    component = genji.body_script.component
    genji.body_script.set_ult_charge(component.entity_var(338).value())
    run.step({genji: (0, 0, 32)})
    for _ in range(80):
        run.step({})


class GenjiZaryaBubbleTests(GenjiFixture):
    def scene(self, distance=4, *, draw_blade=False):
        run, genji = self.spawn(distance)
        zarya = run.players[1]
        run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
        zarya.mover.position[:] = [0, 1, distance]
        for _ in range(30):
            run.step({})
        if draw_blade:
            draw_dragonblade(run, genji)
        graph = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x0244)
        run.step({zarya: (-32768, 0, 8)})
        self.assertEqual(len(run.match.combat.barriers), 1)
        bubble = next(iter(run.match.combat.barriers.values()))
        child = next(i for i in bubble.component.instances.values() if i.graph.index == 0x0256)
        self.assertEqual(child.source_entity(), zarya.body)
        self.assertEqual(expr.to_entity(child.vars[2390].value()), zarya.body)
        self.assertEqual(bubble.vitals.total, 200)
        return run, genji, zarya, graph, bubble

    def test_primary_is_consumed_and_expiry_enforces_cooldown_before_reuse(self):
        run, genji, zarya, graph, bubble = self.scene()
        for frame in range(24):
            run.step({genji: (0, 0, 1 if frame == 0 else 0)})
        self.assertEqual(bubble.vitals.total, 113)
        self.assertEqual(self.health(run, zarya), 400)
        energy = zarya.body_script.component.entity_var(87)
        self.assertAlmostEqual(energy.value(), 17.4, places=4)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
        for _ in range(20):
            run.step({zarya: (-32768, 500, 1)})
        self.assertAlmostEqual(energy.value(), 17.4, places=4)
        self.assertAlmostEqual(self.health(run, genji), 200 - (75 + 95 * 0.174) * 0.32, places=4)
        for _ in range(90):
            run.step({})
        self.assertGreater(energy.value(), 0)
        self.assertLess(energy.value(), 17.4)
        before = energy.value()
        run.step({})
        self.assertAlmostEqual(before - energy.value(), 1.8 * 0.016, places=4)
        self.assertTrue(bubble.retired)
        self.assertGreater(graph.vars[61].value(), 9)
        run.step({zarya: (-32768, 0, 8)})
        self.assertFalse(run.match.combat.barriers)
        for _ in range(630):
            run.step({})
            if graph.vars[61].value() == 0:
                break
        self.assertEqual(graph.vars[61].value(), 0)
        run.step({zarya: (-32768, 0, 8)})
        renewed = next(iter(run.match.combat.barriers.values()))
        self.assertNotEqual(renewed.entity, bubble.entity)
        self.assertEqual(renewed.vitals.total, 200)

    def test_breaking_projectile_has_no_spill_and_next_projectile_hits_body(self):
        run, genji, zarya, graph, bubble = self.scene(distance=1.5)
        for _ in range(118):
            run.step({genji: (0, 0, 2)})
            if bubble.retired:
                break
        self.assertTrue(bubble.retired)
        self.assertEqual(bubble.vitals.total, 0)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 40, places=4)
        self.assertGreater(graph.vars[61].value(), 0)
        for _ in range(12):
            run.step({})
        self.assertEqual(self.health(run, zarya), 342)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 2)

    def test_swift_strike_is_consumed_without_body_damage(self):
        run, genji, zarya, _, bubble = self.scene()
        for frame in range(10):
            run.step({genji: (0, 0, 8 if frame == 0 else 0)})
        self.assertEqual(bubble.vitals.total, 150)
        self.assertEqual(self.health(run, zarya), 400)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_dragonblade_breaks_barrier_without_spill_then_hits_zarya(self):
        run, genji, zarya, _, bubble = self.scene(draw_blade=True)
        energy = zarya.body_script.component.entity_var(87)

        def swing():
            run.step({genji: (0, 0, 1)})
            for _ in range(58):
                run.step({})

        swing()
        self.assertEqual(bubble.vitals.total, 90)
        self.assertAlmostEqual(energy.value(), 22, places=4)
        self.assertEqual(self.health(run, zarya), 400)
        self.assertFalse(genji.client.messages(combat.HIT))
        swing()
        self.assertTrue(bubble.retired)
        self.assertEqual(bubble.vitals.total, 0)
        self.assertAlmostEqual(energy.value(), 40, places=4)
        self.assertEqual(self.health(run, zarya), 400)
        self.assertFalse(genji.client.messages(combat.HIT))
        swing()
        self.assertEqual(self.health(run, zarya), 290)
        hits = genji.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [110])

    def test_flashbang_damage_does_not_deliver_stun_through_self_barrier(self):
        run, caster, zarya, _, bubble = self.scene(distance=3)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = [0, 1, 0]
        yaw, pitch = aim(run.match.combat._eye(caster), (0, 2, 3))
        for frame in range(35):
            run.step({caster: (yaw, pitch, 16 if frame == 3 else 0)})
            self.assertFalse(run.match.combat.is_stunned(zarya))
        self.assertEqual(bubble.vitals.total, 175)
        self.assertEqual(self.health(run, zarya), 400)

    def test_body_replacement_retires_bubble_and_child_graph(self):
        run, _, zarya, _, bubble = self.scene()
        self.assertTrue(run.match.switch_hero(zarya, zarya.hero))
        self.assertTrue(bubble.retired)
        self.assertFalse(bubble.component.instances)
        self.assertFalse(run.match.combat.barriers)


class GenjiZaryaProjectedTests(GenjiFixture):
    def scene(self, *, ally_distance=4, ally_x=0, caster_distance=8, caster_x=0, collision=None,
              names=("Genji", "Ally", "Zarya")):
        run, genji = self.spawn(4, names)
        ally, zarya = run.players[1:3]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team, ally.team, zarya.team = 0, 1, 1
        run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
        ally.mover.position[:] = [ally_x, 1, ally_distance]
        zarya.mover.position[:] = [caster_x, 1, caster_distance]
        if collision is not None:
            run.match.collision_world = collision
        for _ in range(30):
            run.step({})
        graph = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x025C)
        return run, genji, ally, zarya, graph

    def cast(self, run, zarya, yaw=-32768):
        run.step({zarya: (yaw, 0, 16)})
        for _ in range(12):
            run.step({zarya: (yaw, 0, 0)})

    def test_projected_bubble_protects_ally_and_replacement_releases_source(self):
        run, genji, ally, zarya, graph = self.scene()
        self.cast(run, zarya)
        self.assertEqual(len(run.match.combat.barriers), 1)
        bubble = next(iter(run.match.combat.barriers.values()))
        child = next(i for i in bubble.component.instances.values() if i.graph.index == 0x0256)
        self.assertEqual(child.source_entity(), zarya.body)
        self.assertEqual(expr.to_entity(child.vars[2390].value()), ally.body)
        self.assertEqual(bubble.vitals.total, 200)
        for frame in range(24):
            run.step({genji: (0, 0, 1 if frame == 0 else 0)})
        self.assertEqual(bubble.vitals.total, 113)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 17.4, places=4)
        self.assertEqual(self.health(run, ally), 200)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertTrue(run.match.switch_hero(ally, ally.hero))
        self.assertTrue(bubble.retired)
        self.assertFalse(bubble.component.instances)
        self.assertFalse(run.match.combat.barriers)
        self.assertGreater(graph.vars[61].value(), 0)
        self.cast(run, zarya)
        self.assertFalse(run.match.combat.barriers)

    def test_projected_bubble_catches_projectile_graze_before_caster(self):
        for offset, absorbed in ((.55, 87), (.65, 0)):
            with self.subTest(offset=offset):
                run, genji, ally, zarya, _ = self.scene(ally_x=offset)
                fight = run.match.combat
                yaw, _ = aim(fight._eye(zarya), (offset, 2, 4))
                self.cast(run, zarya, yaw)
                bubble, = fight.barriers.values()
                self.assertIs(bubble.recipient, ally)
                self.assertIsNone(barriers.ray_hit(fight, genji, fight._eye(genji), (0, 0, 1), 8))

                for frame in range(35):
                    run.step({genji: (0, 0, 1 if frame == 0 else 0)})

                self.assertEqual(bubble.vitals.total, 200 - absorbed)
                self.assertEqual(self.health(run, ally), 200)
                self.assertEqual(self.health(run, zarya), 313 + absorbed)
                energy = zarya.body_script.component.entity_var(87).value()
                if absorbed:
                    self.assertAlmostEqual(energy, 17.4, places=4)
                else:
                    self.assertIsNone(energy)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 87 - absorbed)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [] if absorbed else [29] * 3)

    def test_dragonblade_breaks_projected_barrier_before_damaging_recipient(self):
        run, genji, ally, zarya, _ = self.scene()
        draw_dragonblade(run, genji)
        self.cast(run, zarya)
        bubble = next(iter(run.match.combat.barriers.values()))
        self.assertIs(bubble.recipient, ally)
        self.assertEqual(bubble.vitals.total, 200)
        caster_energy = zarya.body_script.component.entity_var(87)

        for shield_hp, energy in ((90, 22), (0, 40)):
            run.step({genji: (0, 0, 1)})
            for _ in range(58):
                run.step({})
            self.assertEqual(bubble.vitals.total, shield_hp)
            self.assertAlmostEqual(caster_energy.value(), energy, places=4)
            self.assertEqual(self.health(run, ally), 200)
            self.assertFalse(genji.client.messages(combat.HIT))
        self.assertTrue(bubble.retired)
        self.assertFalse(bubble.component.instances)

        run.step({genji: (0, 0, 1)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.health(run, ally), 90)
        self.assertEqual(self.health(run, zarya), 400)
        hits = genji.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [110])

    def test_quick_melee_breaks_projected_barrier_without_spill_then_damages_recipient(self):
        run, genji, ally, zarya, graph = self.scene(ally_distance=1.5)
        self.cast(run, zarya)
        bubble, = run.match.combat.barriers.values()
        self.assertIs(bubble.recipient, ally)
        energy = zarya.body_script.component.entity_var(87)
        charge = genji.body_script.component.entity_var(342)
        for frame in range(55):
            run.step({genji: (0, 0, 2 if frame < 49 else 0)})
        self.assertEqual(bubble.vitals.total, 26)
        self.assertAlmostEqual(energy.value(), 34.8, places=4)

        for frame in range(60):
            run.step({genji: (0, 0, 0x800 if frame == 0 else 0)})
            if bubble.retired:
                break
        self.assertTrue(bubble.retired)
        self.assertFalse(bubble.component.instances)
        self.assertEqual(bubble.vitals.total, 0)
        self.assertGreater(graph.vars[61].value(), 0)
        self.assertEqual(self.health(run, ally), 200)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertEqual(charge.value(), 0)
        self.assertAlmostEqual(energy.value(), 40, places=4)

        for _ in range(60):
            run.step({})
        self.fire(run, genji, 0x800)
        self.assertEqual(self.health(run, ally), 170)
        self.assertEqual(self.health(run, zarya), 400)
        self.assertEqual(charge.value(), 30)
        hit, = genji.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": ally.body}, "+0x4": 30, "+0x8": 0})

    def test_overlapping_projected_barriers_credit_only_the_absorbing_caster(self):
        run, genji, ally, first, _ = self.scene(names=("Genji", "Ally", "Zarya", "Second"))
        second = run.players[3]
        second.team = ally.team
        run.match.switch_hero(second, first.hero)
        second.mover.position[:] = [2, 1, 8]
        draw_dragonblade(run, genji)
        self.cast(run, first)
        first_barrier = next(iter(run.match.combat.barriers.values()))
        first_energy = first.body_script.component.entity_var(87)
        second_energy = second.body_script.component.entity_var(87)
        second_energy_before = second_energy.value()
        second_yaw, _ = aim(run.match.combat._eye(second), run.match.combat._eye(ally))
        run.step({genji: (0, 0, 1)})
        for _ in range(24):
            run.step({})
        self.cast(run, second, yaw=second_yaw)
        self.assertEqual(len(run.match.combat.barriers), 2)
        second_barrier = next(b for b in run.match.combat.barriers.values() if b.parent is second)
        self.assertIs(first_barrier.recipient, ally)
        self.assertIs(second_barrier.recipient, ally)
        self.assertEqual((first_barrier.vitals.total, second_barrier.vitals.total), (90, 200))
        for _ in range(22):
            run.step({})

        run.step({genji: (0, 0, 1)})
        for _ in range(58):
            run.step({})
        self.assertTrue(first_barrier.retired)
        self.assertEqual(first_barrier.vitals.total, 0)
        self.assertEqual(second_barrier.vitals.total, 200)
        self.assertAlmostEqual(first_energy.value(), 40, places=4)
        self.assertEqual(second_energy.value(), second_energy_before)

        run.step({genji: (0, 0, 1)})
        for _ in range(20):
            run.step({})
        self.assertEqual(second_barrier.vitals.total, 90)
        self.assertAlmostEqual(second_energy.value(), 22, places=4)
        self.assertLessEqual(first_energy.value(), 40)
        self.assertEqual(self.health(run, ally), 200)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_projected_barrier_absorbs_reflected_allied_shuriken(self):
        run, genji, ally, zarya, _ = self.scene(
            ally_distance=2, caster_distance=2, caster_x=4,
            names=("Genji", "Ally", "Zarya", "Shooter"),
        )
        shooter = run.players[3]
        shooter.team = ally.team
        run.match.switch_hero(shooter, heroes()[GENJI])
        shooter.mover.position[:] = [0, 1, 4]
        self.cast(run, zarya, yaw=-16384)
        bubble = next(iter(run.match.combat.barriers.values()))
        self.assertIs(bubble.recipient, ally)
        self.assertEqual(bubble.vitals.total, 200)

        run.step({genji: (0, 0, 16)})
        for frame in range(55):
            run.step({shooter: (-32768, 0, 1 if frame == 0 else 0)})
        self.assertEqual(bubble.vitals.total, 113)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 17.4, places=4)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, ally), 200)
        self.assertEqual(self.health(run, shooter), 200)
        for attacker in (genji, shooter):
            self.assertFalse(attacker.client.messages(combat.HIT))
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_cast_rejects_enemy_wall_out_of_range_and_off_aim(self):
        for case in ("enemy", "wall", "range", "aim", "free_for_all"):
            with self.subTest(case=case):
                run, _, ally, zarya, graph = self.scene(
                    caster_distance=36 if case == "range" else 8,
                    collision=World(FLOOR + wall(-5, 6, 5, 6)) if case == "wall" else None,
                )
                if case == "enemy":
                    ally.team = 0
                if case == "free_for_all":
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                self.cast(run, zarya, yaw=0 if case == "aim" else -32768)
                self.assertFalse(run.match.combat.barriers)
                self.assertEqual(graph.vars[61].value(), 0)

    def test_caster_replacement_retires_projected_bubble(self):
        run, _, _, zarya, _ = self.scene()
        self.cast(run, zarya)
        bubble = next(iter(run.match.combat.barriers.values()))
        self.assertTrue(run.match.switch_hero(zarya, zarya.hero))
        self.assertTrue(bubble.retired)
        self.assertFalse(bubble.component.instances)
        self.assertFalse(run.match.combat.barriers)

    def test_self_and_projected_breaks_share_the_caster_energy_cap(self):
        run, genji, ally, zarya, _ = self.scene(ally_distance=1.5, caster_distance=0, caster_x=1.5)
        component = zarya.body_script.component
        self_barrier = next(i for i in component.instances.values() if i.graph.index == 0x0244)
        energy = component.entity_var(87)

        def break_barrier(yaw):
            bubble = next(iter(run.match.combat.barriers.values()))
            for _ in range(118):
                run.step({genji: (yaw, 0, 2)})
                if bubble.retired:
                    break
            self.assertTrue(bubble.retired)
            self.assertEqual(bubble.vitals.total, 0)

        run.step({zarya: (0, 0, 8)})
        break_barrier(16384)
        self.assertAlmostEqual(energy.value(), 40, places=4)

        for _ in range(30):
            run.step({})
        self.cast(run, zarya, yaw=-8192)
        self.assertIs(next(iter(run.match.combat.barriers.values())).recipient, ally)
        break_barrier(0)
        self.assertAlmostEqual(energy.value(), 80, places=4)

        for _ in range(650):
            run.step({})
            if self_barrier.vars[61].value() == 0:
                break
        self.assertEqual(self_barrier.vars[61].value(), 0)
        self.assertGreater(energy.value(), 60)
        self.assertLess(energy.value(), 80)
        run.step({zarya: (0, 0, 8)})
        break_barrier(16384)
        self.assertAlmostEqual(energy.value(), 100, places=4)

    def test_expiry_rejects_recast_then_cooldown_allows_new_pet(self):
        run, _, ally, zarya, graph = self.scene()
        self.cast(run, zarya)
        old = next(iter(run.match.combat.barriers.values()))
        for _ in range(125):
            run.step({})
        self.assertTrue(old.retired)
        self.assertGreater(graph.vars[61].value(), 0)
        self.cast(run, zarya)
        self.assertFalse(run.match.combat.barriers)
        for _ in range(650):
            run.step({})
            if graph.vars[61].value() == 0:
                break
        self.assertEqual(graph.vars[61].value(), 0)
        self.cast(run, zarya)
        fresh = next(iter(run.match.combat.barriers.values()))
        self.assertNotEqual(fresh.entity, old.entity)
        self.assertIs(fresh.recipient, ally)
        self.assertEqual(fresh.vitals.total, 200)
