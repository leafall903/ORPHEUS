"""Actual protection commands against Genji's combat paths."""

from dataclasses import replace

from test_game_combat import aim
from test_game_mover import FLOOR, wall
from test_genji_combat import GenjiFixture

from ow174.game import barriers, combat
from ow174.game.collision import World
from ow174.game.content import heroes


class GenjiWraithTests(GenjiFixture):
    def scene(self, distance=4, third=False):
        names = ("Genji", "Reaper", "Behind") if third else ("Genji", "Reaper")
        run, genji = self.spawn(distance, names)
        reaper = run.players[1]
        run.match.switch_hero(reaper, heroes()[0x02E0000000000002])
        reaper.mover.position[:] = [0, 1, distance]
        if third:
            run.players[2].mover.position[:] = [0, 1, 8]
        for _ in range(30):
            run.step({})
        graph = next(i for i in reaper.body_script.component.instances.values() if i.graph.index == 0x20)
        run.step({reaper: (-32768, 0, 8)})
        self.assertTrue(graph.states[5].active)
        self.assertTrue(reaper.body_script.component.entity_var(530).value())
        return run, genji, reaper, graph

    def test_all_genji_attack_kinds_leave_phased_reaper_undamaged(self):
        for attack, distance in (("primary", 4), ("fan", 1.5), ("melee", 1.5), ("strike", 4), ("blade", 4)):
            with self.subTest(attack=attack):
                run, genji, reaper, graph = self.scene(distance)
                if attack == "blade":
                    component = genji.body_script.component
                    genji.body_script.set_ult_charge(component.entity_var(338).value())
                button = {"primary": 1, "fan": 2, "melee": 0x800, "strike": 8, "blade": 32}[attack]
                for frame in range(110 if attack == "blade" else 30):
                    use = button if frame == 0 else 1 if attack == "blade" and frame > 70 else 0
                    run.step({genji: (0, 0, use)})
                self.assertTrue(graph.states[5].active)
                self.assertEqual(self.health(run, reaper), 250)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_projectiles_continue_to_enemy_behind_wraith(self):
        run, genji, reaper, graph = self.scene(third=True)
        behind = run.players[2]
        yaw, pitch = aim(run.match.combat._eye(genji), (0, 2, 8))
        for frame in range(35):
            run.step({genji: (yaw, pitch, 1 if frame == 0 else 0)})
        self.assertTrue(graph.states[5].active)
        self.assertEqual(self.health(run, reaper), 250)
        self.assertEqual(self.health(run, behind), 113)

    def test_early_cancel_restores_damage_and_cooldown_rejects_reactivation(self):
        run, genji, reaper, graph = self.scene()
        for _ in range(30):
            run.step({})
        self.assertTrue(graph.states[5].active)
        run.step({reaper: (-32768, 0, 8)})
        self.assertFalse(graph.states[5].active)
        for _ in range(30):
            run.step({})
        self.assertGreater(graph.vars[61].value(), 0)
        run.step({reaper: (-32768, 0, 8)})
        self.assertFalse(graph.states[5].active)
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, reaper), 163)

    def test_natural_expiry_retires_protection_and_starts_cooldown(self):
        run, _, _, graph = self.scene()
        phase = graph.states[5]
        for _ in range(186):
            run.step({})
        self.assertTrue(phase.active)
        run.step({})
        self.assertFalse(phase.active)
        self.assertEqual(phase.ended_at - phase.start, 3000)
        for _ in range(25):
            run.step({})
        self.assertGreater(graph.vars[61].value(), 0)

    def test_reflected_rifle_shot_passes_wraith_and_retains_genji_attribution(self):
        run, genji, reaper, _ = self.scene(third=True)
        shooter = run.players[2]
        for frame in range(20):
            run.step({genji: (0, 0, 16 if frame == 0 else 0),
                      shooter: (-32768, 0, 1 if frame == 6 else 0)})
        self.assertEqual(self.health(run, reaper), 250)
        self.assertEqual(self.health(run, shooter), 160)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
        self.assertFalse(shooter.client.messages(combat.HIT))

    def test_flashbang_does_not_apply_damage_or_stun_during_wraith(self):
        run, caster, reaper, graph = self.scene(distance=3)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = [0, 1, 0]
        yaw, pitch = aim(run.match.combat._eye(caster), (0, 2, 3))
        for frame in range(35):
            run.step({caster: (yaw, pitch, 16 if frame == 3 else 0)})
            self.assertFalse(run.match.combat.is_stunned(reaper))
        self.assertTrue(graph.states[5].active)
        self.assertEqual(self.health(run, reaper), 250)
        self.assertFalse(caster.client.messages(combat.HIT))


class GenjiFadeTests(GenjiFixture):
    def scene(self, distance=4):
        run, genji = self.spawn(distance)
        moira = run.players[1]
        run.match.switch_hero(moira, heroes()[0x02E00000000001A2])
        moira.mover.position[:] = [0, 1, distance]
        for _ in range(30):
            run.step({})
        graph = next(i for i in moira.body_script.component.instances.values() if i.graph.index == 0x0F7C)
        return run, genji, moira, graph

    def fade(self, run, moira, graph):
        run.step({moira: (-32768, 0, 8)})
        self.assertTrue(graph.states[4].active)
        self.assertTrue(moira.body_script.component.entity_var(530).value())

    def test_all_genji_attacks_are_rejected_during_fade(self):
        for attack, distance in (("primary", 4), ("fan", 1.5), ("melee", 1.5), ("strike", 4), ("blade", 4)):
            with self.subTest(attack=attack):
                run, genji, moira, graph = self.scene(distance)
                if attack == "blade":
                    component = genji.body_script.component
                    genji.body_script.set_ult_charge(component.entity_var(338).value())
                    run.step({genji: (0, 0, 32)})
                    for _ in range(70):
                        run.step({})
                self.fade(run, moira, graph)
                button = {"primary": 1, "fan": 2, "melee": 0x800, "strike": 8, "blade": 1}[attack]
                for frame in range(30):
                    run.step({genji: (0, 0, button if frame == 0 else 0)})
                self.assertTrue(graph.states[4].active)
                self.assertEqual(self.health(run, moira), 200)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_exact_fade_window_then_damage_and_cooldown_reuse(self):
        run, genji, moira, graph = self.scene()
        self.fade(run, moira, graph)
        phase = graph.states[4]
        for _ in range(45):
            run.step({})
        self.assertTrue(phase.active)
        run.step({})
        self.assertFalse(phase.active)
        self.assertEqual(phase.ended_at - phase.start, 750)
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, moira), 113)
        self.assertGreater(graph.vars[61].value(), 0)
        run.step({moira: (-32768, 0, 8)})
        self.assertFalse(phase.active)
        for _ in range(390):
            run.step({})
            if graph.vars[61].value() == 0:
                break
        self.assertEqual(graph.vars[61].value(), 0)
        self.fade(run, moira, graph)

    def test_flashbang_misses_during_fade(self):
        run, caster, moira, graph = self.scene(distance=3)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = [0, 1, 0]
        self.fade(run, moira, graph)
        yaw, pitch = aim(run.match.combat._eye(caster), (0, 2, 3))
        for frame in range(35):
            run.step({caster: (yaw, pitch, 16 if frame == 3 else 0)})
            self.assertFalse(run.match.combat.is_stunned(moira))
        self.assertTrue(graph.states[4].active)
        self.assertEqual(self.health(run, moira), 200)
        self.assertFalse(caster.client.messages(combat.HIT))


class GenjiTranscendenceTests(GenjiFixture):
    def scene(self, distance=4, third=False):
        names = ("Genji", "Zenyatta", "Behind") if third else ("Genji", "Zenyatta")
        run, genji = self.spawn(distance, names)
        zen = run.players[1]
        run.match.switch_hero(zen, heroes()[0x02E0000000000020])
        zen.mover.position[:] = [0, 1, distance]
        if third:
            run.players[2].mover.position[:] = [0, 1, 8]
        for _ in range(30):
            run.step({})
        component = zen.body_script.component
        zen.body_script.set_ult_charge(component.entity_var(338).value())
        graph = next(i for i in component.instances.values() if i.graph.index == 0x017B)
        run.step({zen: (-32768, 0, 32)})
        self.assertTrue(graph.states[31].active)
        self.assertTrue(component.entity_var(10414).value())
        self.assertEqual(component.entity_var(342).value(), 0)
        return run, genji, zen, graph

    def test_genji_damage_is_rejected_while_transcendence_is_active(self):
        for attack, distance in (("primary", 4), ("fan", 1.5), ("melee", 1.5), ("strike", 4), ("blade", 4)):
            with self.subTest(attack=attack):
                run, genji, zen, graph = self.scene(distance)
                if attack == "blade":
                    component = genji.body_script.component
                    genji.body_script.set_ult_charge(component.entity_var(338).value())
                button = {"primary": 1, "fan": 2, "melee": 0x800, "strike": 8, "blade": 32}[attack]
                for frame in range(110 if attack == "blade" else 30):
                    use = button if frame == 0 else 1 if attack == "blade" and frame > 70 else 0
                    run.step({genji: (0, 0, use)})
                self.assertTrue(graph.states[31].active)
                self.assertEqual(self.health(run, zen), 200)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_transcendence_body_stops_shuriken_then_expires(self):
        run, genji, zen, graph = self.scene(third=True)
        behind = run.players[2]
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, behind), 200)
        self.assertFalse(genji.client.messages(combat.HIT))
        phase = graph.states[31]
        for _ in range(330):
            if not phase.active:
                break
            run.step({})
        self.assertFalse(phase.active)
        self.assertAlmostEqual(phase.ended_at - phase.start, 6000, delta=16)
        run.step({zen: (-32768, 0, 32)})
        self.assertFalse(phase.active)
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, zen), 113)
        self.assertEqual(self.health(run, behind), 200)

    def test_flashbang_does_not_damage_or_stun_transcendence(self):
        run, caster, zen, graph = self.scene(distance=3)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = [0, 1, 0]
        yaw, pitch = aim(run.match.combat._eye(caster), (0, 2, 3))
        for frame in range(35):
            run.step({caster: (yaw, pitch, 16 if frame == 3 else 0)})
            self.assertFalse(run.match.combat.is_stunned(zen))
        self.assertTrue(graph.states[31].active)
        self.assertEqual(self.health(run, zen), 200)
        self.assertFalse(caster.client.messages(combat.HIT))

    def test_transcendence_heals_only_a_visible_in_range_ally(self):
        for case in ("ally", "enemy", "range", "wall"):
            with self.subTest(case=case):
                run, genji = self.spawn(4, ("Genji", "Wounded", "Zenyatta"))
                wounded, zen = run.players[1:]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                genji.team = zen.team = 0
                wounded.team = 1
                run.match.switch_hero(zen, heroes()[0x02E0000000000020])
                zen.mover.position[:] = [14 if case == "range" else 2, 1, 4]
                if case == "wall":
                    run.match.collision_world = World(FLOOR + wall(1, 2, 1, 6))
                for _ in range(30):
                    run.step({})
                self.fire(run, genji, 1)
                self.assertEqual(self.health(run, wounded), 113)
                if case != "enemy":
                    zen.team = wounded.team
                component = zen.body_script.component
                zen.body_script.set_ult_charge(component.entity_var(338).value())
                run.step({zen: (0, 0, 32)})
                for _ in range(10):
                    run.step({})
                graph = next(i for i in component.instances.values() if i.graph.index == 0x017B)
                self.assertTrue(graph.states[31].active)
                if case == "ally":
                    self.assertTrue(graph.states[6].active)
                    before = self.health(run, wounded)
                    self.assertGreater(before, 160)
                    run.step({})
                    self.assertAlmostEqual(self.health(run, wounded) - before, 4.8)
                    for _ in range(12):
                        run.step({})
                    self.assertEqual(self.health(run, wounded), 200)
                else:
                    self.assertEqual(self.health(run, wounded), 113)
                    if case == "wall":
                        run.match.collision_world = World(FLOOR)
                        for _ in range(20):
                            run.step({})
                        self.assertEqual(self.health(run, wounded), 200)


class GenjiCryoFreezeTests(GenjiFixture):
    def scene(self, distance=4, third=False, *, activate=True, mei_x=0):
        names = ("Genji", "Mei", "Behind") if third else ("Genji", "Mei")
        run, genji = self.spawn(distance, names)
        mei = run.players[1]
        run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
        mei.mover.position[:] = [mei_x, 1, distance]
        if third:
            run.players[2].mover.position[:] = [0, 1, 8]
        for _ in range(30):
            run.step({})
        graph = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x05FE)
        if activate:
            self.freeze(run, mei, graph)
        return run, genji, mei, graph

    def freeze(self, run, mei, graph):
        run.step({mei: (-32768, 0, 8)})
        self.assertTrue(graph.states[35].active)
        self.assertTrue(mei.body_script.component.entity_var(10414).value())

    def test_direct_attacks_do_not_damage_mei_during_cryo_freeze(self):
        for attack, distance in (("primary", 4), ("fan", 1.5), ("melee", 1.5), ("strike", 4), ("blade", 4)):
            with self.subTest(attack=attack):
                run, genji, mei, graph = self.scene(distance)
                if attack == "blade":
                    component = genji.body_script.component
                    genji.body_script.set_ult_charge(component.entity_var(338).value())
                button = {"primary": 1, "fan": 2, "melee": 0x800, "strike": 8, "blade": 32}[attack]
                for frame in range(110 if attack == "blade" else 30):
                    use = button if frame == 0 else 1 if attack == "blade" and frame > 70 else 0
                    run.step({genji: (0, 0, use)})
                self.assertTrue(graph.states[35].active)
                self.assertEqual(self.health(run, mei), 250)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_retained_body_intercepts_shuriken_until_expiry(self):
        run, genji, mei, graph = self.scene(third=True)
        fight = run.match.combat
        ice = next(iter(fight.barriers.scripted.values()))
        child = next(iter(ice.component.instances.values()))
        self.assertEqual(child.graph.index, 0x0623)
        self.assertEqual(child.source_entity(), mei.body)
        self.assertEqual(graph.states[11].pet_entity, ice.entity)
        self.assertIs(barriers.ray_hit(fight, genji, fight._eye(genji), (0, 0, 1), 8)[1], ice)
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, mei), 250)
        self.assertEqual(self.health(run, run.players[2]), 200)
        state = graph.states[35]
        for _ in range(205):
            if not state.active:
                break
            run.step({})
        self.assertFalse(state.active)
        self.assertAlmostEqual(state.ended_at - state.start, 4000, delta=16)
        self.assertTrue(ice.retired)
        self.assertFalse(ice.component.instances)
        self.assertIsNone(barriers.ray_hit(fight, genji, fight._eye(genji), (0, 0, 1), 8))
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, mei), 163)

    def test_cryo_intercepts_projectile_radius_outside_centreline(self):
        for offset, behind_health in ((.55, 200), (.65, 113)):
            with self.subTest(offset=offset):
                run, genji, mei, graph = self.scene(third=True, mei_x=offset)
                fight = run.match.combat
                ice, = fight.barriers.scripted.values()
                self.assertIsNone(barriers.ray_hit(fight, genji, fight._eye(genji), (0, 0, 1), 8))
                self.fire(run, genji, 1)
                self.assertTrue(graph.states[35].active)
                self.assertEqual(self.health(run, mei), 250)
                self.assertEqual(self.health(run, run.players[2]), behind_health)
                self.assertEqual(genji.body_script.component.entity_var(342).value(),
                                 200 - behind_health)
                if offset == .55:
                    run.step({mei: (-32768, 0, 2)})
                    self.assertTrue(ice.retired)
                    self.fire(run, genji, 1)
                    self.assertEqual(self.health(run, mei), 163)
                    self.assertEqual(self.health(run, run.players[2]), 200)

    def test_cancel_starts_cooldown_and_rejects_immediate_reuse(self):
        run, genji, mei, graph = self.scene()
        for _ in range(20):
            run.step({})
        run.step({mei: (-32768, 0, 2)})
        self.assertFalse(graph.states[35].active)
        for _ in range(18):
            run.step({})
        self.assertGreater(graph.vars[61].value(), 0)
        run.step({mei: (-32768, 0, 8)})
        self.assertFalse(graph.states[35].active)
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, mei), 163)

    def test_cryo_restores_authored_budget_after_actual_shuriken_damage(self):
        # At this distance the centre fan pellet hits and the two flanks miss.
        run, genji, mei, graph = self.scene(distance=10, activate=False)
        for button in (1, 1, 2):
            self.fire(run, genji, button)
        self.assertEqual(self.health(run, mei), 47)
        self.freeze(run, mei, graph)
        for _ in range(260):
            run.step({})
        self.assertFalse(graph.states[35].active)
        self.assertAlmostEqual(self.health(run, mei), 247, places=4)

    def test_cancel_stops_healing_without_granting_the_remaining_budget(self):
        run, genji, mei, graph = self.scene(activate=False)
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, mei), 163)
        self.freeze(run, mei, graph)
        for _ in range(25):
            run.step({})
        run.step({mei: (-32768, 0, 2)})
        self.assertFalse(graph.states[35].active)
        healed = self.health(run, mei)
        self.assertGreater(healed, 180)
        self.assertLess(healed, 190)
        for _ in range(60):
            run.step({})
        self.assertEqual(self.health(run, mei), healed)
        self.fire(run, genji, 1)
        self.assertAlmostEqual(self.health(run, mei), healed - 87, places=4)
