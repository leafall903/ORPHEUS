"""Player input coverage for damage beams and Winston's Tesla Cannon."""

from dataclasses import replace

from test_game_mover import FLOOR, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import barrier_geometry, combat, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr


class GenjiWinstonTeslaTests(GenjiFixture):
    def test_tesla_hits_multiple_enemies_through_deflect_and_stops_on_release(self):
        run, genji = self.spawn(4, ("Genji", "Winston", "Side", "Far", "Rear", "Ally"))
        winston, side, far, rear, ally = run.players[1:]
        run.match.switch_hero(winston, heroes()[0x02E0000000000009])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        winston.team = ally.team = 0
        genji.team = side.team = far.team = rear.team = 1
        winston.mover.position[:] = [0, 1, 4]
        side.mover.position[:] = [1.5, 1, 0]
        far.mover.position[:] = [0, 1, -6]
        rear.mover.position[:] = [0, 1, 7]
        ally.mover.position[:] = [-1, 1, 0]
        for _ in range(30):
            run.step({})

        weapon = next(i for i in winston.body_script.component.instances.values() if i.graph.index == 0x00FE)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        for frame in range(25):
            run.step({genji: (0, 0, 16 if frame == 0 else 0), winston: (-32768, 0, 1 if frame >= 5 else 0)})
        self.assertTrue(deflect.state(10).active)
        self.assertTrue(weapon.state(10).active)
        self.assertTrue(weapon.state(13).active)
        self.assertEqual(weapon.vars[468].value(), (expr.Entity(genji.body), expr.Entity(side.body)))
        self.assertAlmostEqual(self.health(run, genji), 180.8)
        self.assertAlmostEqual(self.health(run, side), 180.8)
        for untouched in (far, rear, ally):
            self.assertEqual(self.health(run, untouched), 200)
        self.assertEqual(self.health(run, winston), 500)
        self.assertFalse(genji.client.messages(combat.HIT))
        ammo = weapon.vars[53].value()
        self.assertGreater(ammo, 93)
        self.assertLess(ammo, 94)

        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
        for _ in range(5):
            run.step({winston: (-32768, 0, 1)})
        self.assertTrue(weapon.state(10).active)
        self.assertFalse(weapon.vars[468].value())
        self.assertAlmostEqual(self.health(run, genji), 180.8)
        self.assertAlmostEqual(self.health(run, side), 180.8)
        self.assertLess(weapon.vars[53].value(), ammo)

        run.match.collision_world = World(FLOOR)
        run.step({winston: (-32768, 0, 1)})
        self.assertAlmostEqual(self.health(run, genji), 179.84)
        self.assertAlmostEqual(self.health(run, side), 179.84)
        ammo = weapon.vars[53].value()
        for _ in range(8):
            run.step({})
        self.assertFalse(weapon.state(10).active)
        self.assertFalse(weapon.state(13).active)
        self.assertFalse(weapon.vars[468].value())
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertAlmostEqual(self.health(run, genji), 179.84)
        self.assertAlmostEqual(self.health(run, side), 179.84)

    def test_projected_bubble_absorbs_tesla_only_for_its_recipient_until_break(self):
        run, genji = self.spawn(4, ("Genji", "Winston", "Protector", "Side", "Shuriken"))
        winston, protector, side, shuriken = run.players[1:]
        for player, hero in ((winston, 0x02E0000000000009), (protector, 0x02E0000000000068), (shuriken, GENJI)):
            run.match.switch_hero(player, heroes()[hero])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team = protector.team = side.team = 0
        winston.team = shuriken.team = 1
        winston.mover.position[:] = [0, 1, 4]
        protector.mover.position[:] = [0, 1, -7]
        side.mover.position[:] = [1.5, 1, 0]
        shuriken.mover.position[:] = [0, 1, 1.5]
        for _ in range(30):
            run.step({})
        run.step({protector: (0, 0, 16)})
        bubble, = run.match.combat.barriers.values()
        self.assertIs(bubble.recipient, genji)
        for frame in range(55):
            run.step({shuriken: (-32768, 500, 2 if frame < 49 else 0)})
        self.assertEqual(bubble.vitals.total, 26)

        for frame in range(28):
            self.assertFalse(bubble.retired)
            run.step({genji: (0, 0, 16 if frame == 0 else 0), winston: (-32768, 0, 1)})
        self.assertTrue(bubble.retired)
        self.assertFalse(bubble.component.instances)
        self.assertAlmostEqual(protector.body_script.component.entity_var(87).value(), 40, places=4)
        self.assertEqual(self.health(run, genji), 200)
        self.assertAlmostEqual(self.health(run, side), 173.12)
        hits = winston.client.messages(combat.HIT)
        self.assertEqual([notice["+0x78"]["+0x0"]["+0x0"] for notice in hits], [side.body] * 28)

        run.step({winston: (-32768, 0, 1)})
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(deflect.state(10).active)
        self.assertAlmostEqual(self.health(run, genji), 199.04)
        self.assertAlmostEqual(self.health(run, side), 172.16)


class GenjiMoiraBeamTests(GenjiFixture):
    def scene(self, distance=4, *, sigma=False):
        run, defender = self.spawn(distance)
        moira = run.players[1]
        run.match.switch_hero(moira, heroes()[0x02E00000000001A2])
        moira.mover.position[:] = [0, 1, distance]
        if sigma:
            run.match.switch_hero(defender, heroes()[0x02E000000000023B])
            defender.mover.position[:] = [0, 1, 0]
        for _ in range(30):
            run.step({})
        graph = next(i for i in moira.body_script.component.instances.values() if i.graph.index == 0x0F76)
        return run, defender, moira, graph

    def test_damage_grasp_bypasses_deflect_and_kinetic_grasp_then_releases(self):
        for sigma in (False, True):
            with self.subTest(sigma=sigma):
                run, defender, moira, beam = self.scene(sigma=sigma)
                before = self.health(run, defender)
                for frame in range(26):
                    run.step({defender: (0, 0, (8 if sigma else 16) if frame == 0 else 0),
                              moira: (-32768, 500, 2 if frame >= 5 else 0)})
                graph_index, state_index = (0x1D02, 1) if sigma else (0x03A3, 10)
                defense = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == graph_index)
                self.assertTrue(defense.state(state_index).active)
                self.assertTrue(beam.state(106).active)
                self.assertTrue(beam.state(22).active)
                self.assertEqual(expr.to_entity(beam.vars[375].value()), defender.body)
                self.assertAlmostEqual(self.health(run, defender), before - 16, places=4)
                self.assertFalse(defender.client.messages(combat.HIT))
                if sigma:
                    self.assertEqual(defense.vars[21312].value(), 0)
                self.assertEqual(self.health(run, moira), 200)
                self.assertEqual(len(moira.client.messages(combat.HIT)), 20)

                for _ in range(10):
                    run.step({})
                self.assertFalse(beam.state(106).active)
                self.assertFalse(beam.state(22).active)
                self.assertFalse(expr.to_entity(beam.vars[375].value()))
                self.assertAlmostEqual(self.health(run, defender), before - 16, places=4)
                for _ in range(5):
                    run.step({moira: (-32768, 500, 2)})
                self.assertTrue(beam.state(22).active)
                self.assertAlmostEqual(self.health(run, defender), before - 19.2, places=4)

    def test_damage_grasp_respects_wall_range_aim_and_team(self):
        for case in ("wall", "range", "aim", "team"):
            with self.subTest(case=case):
                run, genji, moira, beam = self.scene(21 if case == "range" else 4)
                if case == "wall":
                    run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
                if case == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    moira.team = genji.team
                for _ in range(20):
                    run.step({moira: (16384 if case == "aim" else -32768, 500, 2)})
                self.assertTrue(beam.state(106).active)
                self.assertFalse(beam.state(22).active)
                self.assertFalse(expr.to_entity(beam.vars[375].value()))
                self.assertEqual(self.health(run, genji), 200)
                self.assertFalse(moira.client.messages(combat.HIT))

    def test_damage_grasp_heals_actual_genji_wounds_only_on_damage_samples(self):
        run, genji, moira, beam = self.scene()
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, moira), 113)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)

        for frame in range(26):
            run.step({genji: (0, 0, 16 if frame == 0 else 0),
                      moira: (-32768, 500, 2 if frame >= 5 else 0)})
        self.assertTrue(beam.state(22).active)
        self.assertTrue(beam.state(45).active)
        healed = 24 * 0.016 * 20
        self.assertAlmostEqual(self.health(run, moira), 113 + healed, places=4)
        self.assertAlmostEqual(moira.stats.values[stats.HEALING], healed, places=4)
        self.assertAlmostEqual(self.health(run, genji), 184, places=4)

        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
        for _ in range(12):
            run.step({moira: (-32768, 500, 2)})
        self.assertTrue(beam.state(106).active)
        self.assertFalse(beam.state(45).active)
        self.assertAlmostEqual(self.health(run, moira), 113 + healed, places=4)
        self.assertAlmostEqual(self.health(run, genji), 184, places=4)
        run.match.collision_world = World(FLOOR)
        for _ in range(5):
            run.step({moira: (-32768, 500, 2)})
        self.assertTrue(beam.state(45).active)
        self.assertAlmostEqual(self.health(run, moira), 113 + healed + 24 * 0.016 * 4, places=4)
        self.assertAlmostEqual(self.health(run, genji), 180.8, places=4)

        after = self.health(run, moira)
        for _ in range(10):
            run.step({})
        self.assertFalse(beam.state(45).active)
        self.assertEqual(self.health(run, moira), after)

    def test_damage_grasp_does_not_heal_when_transcendence_rejects_damage(self):
        run, genji, moira, beam = self.scene()
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, moira), 113)
        run.match.switch_hero(genji, heroes()[0x02E0000000000020])
        genji.mover.position[:] = [0, 1, 0]
        component = genji.body_script.component
        genji.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({genji: (0, 0, 32)})
        zen = next(i for i in component.instances.values() if i.graph.index == 0x017B)
        self.assertTrue(zen.state(31).active)

        for _ in range(21):
            run.step({moira: (-32768, 500, 2)})
        self.assertTrue(beam.state(22).active)
        self.assertTrue(beam.state(45).active)
        self.assertEqual(expr.to_entity(beam.vars[375].value()), genji.body)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, moira), 113)
        self.assertFalse(moira.client.messages(combat.HIT))
        self.assertEqual(moira.stats.values.get(stats.HEALING, 0), 0)

    def test_lethal_damage_grasp_retires_deflect_and_releases_dead_target(self):
        run, genji, moira, beam = self.scene()
        fight = run.match.combat
        old_body, old_script = genji.body, genji.body_script
        genji.spawn = ((0, 1, 0), 0)
        fire = {moira: (-32768, 500, 2)}
        for _ in range(245):
            run.step(fire)
            if self.health(run, genji) <= 5:
                break
        self.assertGreater(self.health(run, genji), 3)
        self.assertLessEqual(self.health(run, genji), 5)
        run.step({**fire, genji: (0, 0, 16)})
        deflect = next(i for i in old_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(deflect.state(10).active)
        with self.assertNoLogs("ow174.game", level="ERROR"):
            for _ in range(8):
                run.step(fire)
            self.assertTrue(fight.is_dead(genji))
        self.assertEqual(fight.vitals[old_body].killer, moira.body)
        self.assertFalse(deflect.state(10).active)
        self.assertTrue(beam.state(106).active)
        self.assertFalse(beam.state(22).active)
        self.assertFalse(expr.to_entity(beam.vars[375].value()))
        self.assertFalse(genji.client.messages(combat.HIT))

        for _ in range(650):
            run.step({})
            if genji.body != old_body:
                break
        self.assertNotEqual(genji.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(self.health(run, genji), 200)
        run.step({genji: (0, 0, 16)})
        fresh = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(fresh.state(10).active)


class GenjiZaryaBeamTests(GenjiFixture):
    def test_symmetra_sustained_beam_uses_graph_tiers_and_decays_after_release(self):
        run, defender, symmetra, beam = self.scene(defender=0x02E000000000023B, symmetra=True)
        damage = []
        for _ in range(180):
            before = self.health(run, defender)
            run.step({symmetra: (-32768, 500, 1)})
            damage.append(round(before - self.health(run, defender), 4))
        self.assertEqual((damage[20], damage[90], damage[179]), (0.96, 1.92, 2.88))

        before = self.health(run, defender)
        for frames, rate in ((120, 180), (130, 120), (130, 60)):
            for _ in range(frames):
                run.step({})
            self.assertEqual(beam.vars[499].value(), rate)
            self.assertEqual(self.health(run, defender), before)
        run.step({symmetra: (-32768, 500, 1)})
        self.assertAlmostEqual(before - self.health(run, defender), 0.96, places=4)

    def test_symmetra_beam_bypasses_deflect_and_grasp_and_releases(self):
        for sigma in (False, True):
            with self.subTest(sigma=sigma):
                run, defender, symmetra, beam = self.scene(
                    defender=0x02E000000000023B if sigma else None, symmetra=True)
                before = self.health(run, defender)
                for frame in range(25):
                    run.step({defender: (0, 0, (8 if sigma else 16) if frame == 0 else 0),
                              symmetra: (-32768, 500, 1 if frame >= 5 else 0)})
                defense_graph, state = (0x1D02, 1) if sigma else (0x03A3, 10)
                defense = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == defense_graph)
                self.assertTrue(defense.state(state).active)
                self.assertTrue(beam.states[86].active)
                self.assertEqual(expr.to_entity(beam.vars[16627].value()), defender.body)
                self.assertAlmostEqual(self.health(run, defender), before - 19.2, places=4)
                self.assertFalse(defender.client.messages(combat.HIT))
                if sigma:
                    self.assertEqual(defense.vars[21312].value(), 0)
                for _ in range(10):
                    run.step({})
                self.assertFalse(beam.states[86].active)
                self.assertFalse(expr.to_entity(beam.vars[16627].value()))
                self.assertAlmostEqual(self.health(run, defender), before - 19.2, places=4)

    def scene(self, distance=4, defender=None, *, symmetra=False):
        run, genji = self.spawn(distance)
        attacker = run.players[1]
        if defender is not None:
            run.match.switch_hero(genji, heroes()[defender])
            genji.mover.position[:] = [0, 1, 0]
        hero, graph_index = (0x02E0000000000016, 0x157C) if symmetra else (0x02E0000000000068, 0x0241)
        run.match.switch_hero(attacker, heroes()[hero])
        attacker.mover.position[:] = [0, 1, distance]
        for _ in range(30):
            run.step({})
        graph = next(i for i in attacker.body_script.component.instances.values() if i.graph.index == graph_index)
        return run, genji, attacker, graph

    def test_primary_beam_damages_through_frontal_deflect_and_stops_on_release(self):
        for deflect in (False, True):
            with self.subTest(deflect=deflect):
                run, genji, zarya, graph = self.scene()
                for frame in range(25):
                    run.step({genji: (0, 0, 16 if deflect and frame == 0 else 0),
                              zarya: (-32768, 500, 1 if frame >= 5 else 0)})
                state = next(
                    i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3
                )
                self.assertEqual(bool(state.states.get(10) and state.states[10].active), deflect)
                self.assertTrue(graph.states[10].active)
                self.assertEqual(expr.to_entity(graph.vars[536].value()), genji.body)
                self.assertAlmostEqual(self.health(run, genji), 176)
                self.assertEqual(self.health(run, zarya), 400)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertLess(graph.vars[53].value(), 100)
                for _ in range(10):
                    run.step({})
                self.assertFalse(graph.states[10].active)
                self.assertFalse(expr.to_entity(graph.vars[16627].value()))
                self.assertAlmostEqual(self.health(run, genji), 176)

    def test_primary_beam_respects_wall_range_aim_and_team(self):
        for symmetra, distant, active_state in ((False, 17, 10), (True, 14, 100)):
            for case in ("wall", "range", "aim", "team"):
                with self.subTest(symmetra=symmetra, case=case):
                    run, genji, attacker, graph = self.scene(distant if case == "range" else 4,
                                                              symmetra=symmetra)
                    if case == "wall":
                        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
                    if case == "team":
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        attacker.team = genji.team
                    for _ in range(20):
                        run.step({attacker: (16384 if case == "aim" else -32768, 500, 1)})
                    self.assertTrue(graph.states[active_state].active)
                    self.assertEqual(self.health(run, genji), 200)
                    self.assertFalse(expr.to_entity(graph.vars[16627].value()))

    def test_lethal_primary_beam_retires_deflect_and_clears_target(self):
        for symmetra in (False, True):
            with self.subTest(symmetra=symmetra):
                run, genji, attacker, beam = self.scene(symmetra=symmetra)
                genji.spawn = ((0, 1, 0), 0)
                old_body = genji.body
                old_script = genji.body_script
                fight = run.match.combat
                fire = {attacker: (-32768, 500, 1)}
                for _ in range(180):
                    run.step(fire)
                    if self.health(run, genji) <= 12:
                        break
                self.assertGreater(self.health(run, genji), 3)
                self.assertLessEqual(self.health(run, genji), 12)

                run.step({**fire, genji: (0, 0, 16)})
                deflect = next(i for i in old_script.component.instances.values() if i.graph.index == 0x03A3)
                self.assertTrue(deflect.state(10).active)
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    for _ in range(12):
                        run.step(fire)
                        if fight.is_dead(genji):
                            break
                    run.step(fire)
                self.assertTrue(fight.is_dead(genji))
                self.assertEqual(fight.vitals[old_body].killer, attacker.body)
                self.assertFalse(deflect.state(10).active)

                self.assertTrue(beam.state(100 if symmetra else 10).active)
                self.assertFalse(expr.to_entity(beam.vars[16627].value()))

                for _ in range(650):
                    run.step({})
                    if genji.body != old_body:
                        break
                self.assertNotEqual(genji.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertEqual(self.health(run, genji), 200)
                run.step({genji: (0, 0, 16)})
                fresh = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
                self.assertTrue(fresh.state(10).active)

    def test_beam_bypasses_active_sigma_grasp_without_creating_shields(self):
        run, sigma, zarya, graph = self.scene(defender=0x02E000000000023B)
        for frame in range(25):
            run.step({sigma: (0, 0, 8 if frame == 0 else 0),
                      zarya: (-32768, 500, 1 if frame >= 5 else 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                            for s in grasp.states.values()))
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertAlmostEqual(self.health(run, sigma), 376)
        self.assertEqual(expr.to_entity(graph.vars[536].value()), sigma.body)
        self.assertFalse(any(i.graph.index == 0x1D03 for i in sigma.body_script.component.instances.values()))

    def test_beam_hits_reinhardt_barrier_before_its_owner(self):
        if not barrier_geometry.load()[0]:
            self.skipTest("external 104319 barrier mesh unavailable")
        for symmetra, damage in ((False, 24), (True, 19.2)):
            with self.subTest(symmetra=symmetra):
                run, rein, attacker, graph = self.scene(defender=0x02E0000000000007, symmetra=symmetra)
                for _ in range(40):
                    run.step({rein: (0, 0, 2)})
                barrier, = run.match.combat.barriers.values()
                for _ in range(20):
                    run.step({rein: (0, 0, 2), attacker: (-32768, 500, 1)})
                self.assertAlmostEqual(barrier.vitals.total, 1600 - damage, places=4)
                self.assertEqual(self.health(run, rein), 500)
                self.assertEqual(expr.to_entity(graph.vars[536].value()), barrier.entity)
                self.assertFalse(attacker.client.messages(combat.HIT))

                for _ in range(5):
                    run.step({attacker: (-32768, 500, 1)})
                self.assertTrue(barrier.retired)
                self.assertEqual(expr.to_entity(graph.vars[536].value()), rein.body)
                self.assertLess(self.health(run, rein), 500)

    def test_enemy_beam_hits_projected_bubble_before_genji(self):
        for symmetra in (False, True):
            with self.subTest(symmetra=symmetra):
                self.check_projected_bubble(symmetra)

    def projected_bubble_scene(self, symmetra, *, breaker=False):
        names = ("Genji", "Attacker", "Protector")
        run, genji = self.spawn(4, names + ("Shuriken",) if breaker else names)
        attacker, protector = run.players[1:3]
        zarya = heroes()[0x02E0000000000068]
        run.match.switch_hero(attacker, heroes()[0x02E0000000000016] if symmetra else zarya)
        run.match.switch_hero(protector, zarya)
        attacker.mover.position[:] = [0, 1, 4]
        protector.mover.position[:] = [0, 1, -4]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team = protector.team = 0
        attacker.team = 1
        if breaker:
            shuriken = run.players[3]
            run.match.switch_hero(shuriken, heroes()[GENJI])
            shuriken.team = attacker.team
            shuriken.mover.position[:] = [0, 1, 1.5]
        for _ in range(30):
            run.step({})
        run.step({protector: (0, 0, 16)})
        bubble, = run.match.combat.barriers.values()
        self.assertIs(bubble.recipient, genji)
        graph_index = 0x157C if symmetra else 0x0241
        graph = next(i for i in attacker.body_script.component.instances.values() if i.graph.index == graph_index)
        return run, genji, attacker, protector, graph, bubble

    def test_enemy_beam_breaks_projected_bubble_then_hits_during_deflect(self):
        for symmetra, tick_damage, break_frame in ((False, 1.2, 21), (True, 0.96, 27)):
            with self.subTest(symmetra=symmetra):
                run, genji, attacker, protector, graph, bubble = self.projected_bubble_scene(symmetra, breaker=True)
                shuriken = run.players[3]
                for frame in range(55):
                    run.step({shuriken: (-32768, 500, 2 if frame < 49 else 0)})
                self.assertEqual(bubble.vitals.total, 26)

                for frame in range(break_frame + 1):
                    self.assertFalse(bubble.retired)
                    run.step({genji: (0, 0, 16 if frame == 0 else 0), attacker: (-32768, 500, 1)})
                self.assertTrue(bubble.retired)
                self.assertEqual(bubble.vitals.total, 0)
                self.assertFalse(bubble.component.instances)
                self.assertEqual(self.health(run, genji), 200)
                self.assertAlmostEqual(protector.body_script.component.entity_var(87).value(), 40, places=4)
                self.assertFalse(attacker.client.messages(combat.HIT))

                run.step({attacker: (-32768, 500, 1)})
                deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
                self.assertTrue(deflect.state(10).active)
                self.assertEqual(expr.to_entity(graph.vars[536].value()), genji.body)
                self.assertAlmostEqual(self.health(run, genji), 200 - tick_damage, places=4)

    def check_projected_bubble(self, symmetra):
        run, genji, attacker, protector, graph, bubble = self.projected_bubble_scene(symmetra)
        damage = 19.2 if symmetra else 24

        for _ in range(20):
            run.step({attacker: (-32768, 500, 1)})
        self.assertAlmostEqual(bubble.vitals.total, 200 - damage, places=4)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(expr.to_entity(graph.vars[536].value()), bubble.entity)
        self.assertAlmostEqual(protector.body_script.component.entity_var(87).value(), damage * 0.2, places=4)
        self.assertFalse(attacker.client.messages(combat.HIT))

        for _ in range(150):
            run.step({})
            if bubble.retired:
                break
        self.assertTrue(bubble.retired)
        self.assertEqual(self.health(run, genji), 200)
        run.step({genji: (0, 0, 16)})
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        for _ in range(20):
            run.step({attacker: (-32768, 500, 1)})
        self.assertTrue(deflect.state(10).active)
        self.assertEqual(expr.to_entity(graph.vars[536].value()), genji.body)
        self.assertAlmostEqual(self.health(run, genji), 200 - damage, places=4)
