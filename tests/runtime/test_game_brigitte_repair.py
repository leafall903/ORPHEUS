"""Repair Pack's actual input, charge graph and reconstructed healing consumer."""

import unittest

import test_game_brigitte as brigitte_tests

from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.content import heroes
from ow174.game.script import expr, graph, message_receive, runtime, scene


class RepairTests(unittest.TestCase):
    def test_actual_recipient_repeat_message_extends_one_graph_and_rebinds_healer(self):
        owner, first, second = 0xA0001000, 0xA0001100, 0xA0001200
        world = scene.SceneWorld()
        world.bind(owner)
        component = runtime.Component(owner, world=world)
        world.bind_script_component(owner, component)
        instance = component.play_loaded_graph(
            graph.graph(0xEE1), source=first, instigator=first, policy=2,
            params=((0x0A4000000000002A, 110),),
        )
        component.run_now()
        component.run_frame(31)
        before = instance.vars[22729].value()
        self.assertGreater(before, 1)
        self.assertLess(before, 2)
        self.assertEqual(message_receive.deliver(component, message_receive.Packet(
            0x0240000000000F6F, second, second, ((0x0A4000000000041A, 1),),
        )), 1)
        component.run_now()
        self.assertEqual(list(component.instances.values()), [instance])
        self.assertAlmostEqual(instance.vars[22729].value(), before + 2, places=5)
        self.assertEqual(instance.vars[40].value(), expr.Entity(second))

    def test_actual_two_casters_extend_one_recipient_stream_instead_of_double_rate(self):
        run, first, enemy, ally, target, _instance = self.fixture()
        second = enemy
        run.match.switch_hero(second, heroes()[first.hero.guid])
        second.team = first.team
        x, y, z = first.mover.position
        second.mover.position, second.mover.body = [x + 1, y, z], None
        run.match.switch_hero(ally, next(h for h in heroes().values() if h.name == "Roadhog"))
        ally.mover.position, ally.mover.body = [x, y, z + 10], None
        target = next(t for t in run.match.combat._targets() if t.owner is ally)
        fight = run.match.combat
        fight._damage(first, target, 400, False, None, run.frame)
        self.step(run, first, 45, cast=True)
        original = fight.brigitte.repairs[first.body, ally.body]
        start = original.until - 2
        self.step(run, second, 45, cast=True)
        self.assertNotIn((first.body, ally.body), fight.brigitte.repairs)
        current = fight.brigitte.repairs[second.body, ally.body]
        self.assertIs(current.healer, second)
        self.assertAlmostEqual(current.until, start + 4, places=5)
        self.assertEqual(sum(h.target == ally.body for h in fight.brigitte.repairs.values()), 1)
        before = fight.vitals_of(target).total
        self.step(run, first, 30)
        self.assertAlmostEqual(fight.vitals_of(target).total - before, 55 * 30 * 0.016, places=4)
        self.step(run, first, 300)
        self.assertAlmostEqual(fight.vitals_of(target).total, 420, places=4)

    def fixture(self):
        run, player = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Enemy", "Ally"))
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        enemy, ally = run.players[1:]
        enemy.team, ally.team = 1 - player.team, player.team
        x, y, z = player.mover.position
        enemy.mover.position, ally.mover.position = [x + 10, y, z], [x, y, z + 10]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xEDD)
        return run, player, enemy, ally, target, instance

    def step(self, run, player, frames, *, cast=False):
        for frame in range(frames):
            run.step({player: (0, 0, 16 if cast and frame == 0 else 0)})

    def test_actual_cast_consumes_charge_then_heals110_over_two_seconds(self):
        run, player, enemy, _ally, target, instance = self.fixture()
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        vitals = fight.vitals_of(target)
        self.step(run, player, 16, cast=True)
        self.assertEqual(vitals.total, 50)
        self.assertEqual(instance.vars[11].value(), 3)
        self.step(run, player, 10)
        self.assertEqual(instance.vars[11].value(), 2)
        self.assertGreater(instance.vars[61].value(), 5)
        self.assertEqual(vitals.total, 50)  # cast completed, pack still travelling
        self.step(run, player, 40)
        self.assertGreater(vitals.total, 50)
        self.assertLess(vitals.total, 160)
        self.step(run, player, 140)
        self.assertAlmostEqual(vitals.total, 160, places=4)
        self.assertFalse(fight.brigitte.repairs)
        self.assertFalse(fight.brigitte.repair_deliveries)
        self.assertEqual(fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy)).total, 200)

    def test_repair_arrival_and_expiry_integrate_across_scheduler_gaps(self):
        from ow174.game import stats

        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, source, enemy, ally, target, instance = self.fixture()
                source.body_script.ult_seconds = 0
                fight = run.match.combat
                fight._damage(enemy, target, 150, False, None, run.frame)
                self.step(run, source, 20, cast=True)
                [delivery] = fight.brigitte.repair_deliveries
                self.assertEqual(instance.vars[11].value(), 2)
                self.assertEqual(fight.vitals_of(target).total, 50)
                for frames in (70, 150):
                    if skipped:
                        run.frame += frames
                        run.match.update(run.frame * .016, run.frame)
                    else:
                        self.step(run, source, frames)
                    expected = 55 * min(2, max(0, fight.now - delivery.at))
                    self.assertAlmostEqual(fight.vitals_of(target).total, 50 + expected, places=5)
                    self.assertAlmostEqual(source.stats.value(stats.HEALING), expected, places=5)
                    # The charge graph stores f32 and rounds each tick credit.
                    self.assertAlmostEqual(source.body_script.component.entity_var(342).value(),
                                           expected, delta=0.001)
                    self.assertFalse(fight.brigitte.repair_deliveries)
                    self.assertEqual((source.body, ally.body) in fight.brigitte.repairs, expected < 110)
                self.assertAlmostEqual(fight.vitals_of(target).total, 160, places=5)

    def test_actual_flashbang_after_launch_keeps_the_in_flight_pack(self):
        run, source, enemy, _ally, target, instance = self.fixture()
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        x, y, z = source.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.step(run, source, 20, cast=True)
        self.assertEqual(instance.vars[11].value(), 2)
        self.assertEqual(len(fight.brigitte.repair_deliveries), 1)
        run.step({enemy: (-32768, 0, 16)})
        for _ in range(40):
            if fight.is_stunned(source):
                break
            run.step({enemy: (-32768, 0, 0)})
        self.assertTrue(fight.is_stunned(source))
        self.step(run, source, 160)
        self.assertAlmostEqual(fight.vitals_of(target).total, 160, places=4)
        self.assertFalse(fight.brigitte.repairs)
        self.assertFalse(fight.brigitte.repair_deliveries)

    def test_enemy_out_of_range_behind_and_ffa_reject_without_charge_loss(self):
        for kind in ("enemy", "far", "behind", "ffa"):
            with self.subTest(kind=kind):
                run, player, enemy, ally, _target, instance = self.fixture()
                x, y, z = player.mover.position
                if kind == "enemy":
                    ally.team = enemy.team
                elif kind == "far":
                    ally.mover.position = [x, y, z + 32]
                elif kind == "behind":
                    ally.mover.position = [x, y, z - 10]
                else:
                    run.match.game_map = run.match.game_map.__class__(**{
                        **run.match.game_map.__dict__, "free_for_all": True,
                    })
                self.step(run, player, 60, cast=True)
                self.assertEqual(instance.vars[11].value(), 3)
                self.assertFalse(run.match.combat.brigitte.repairs)
                self.assertFalse(run.match.combat.brigitte.repair_deliveries)

    def test_three_charges_exhaust_then_recharge_allows_one_new_cast(self):
        run, player, _enemy, _ally, _target, instance = self.fixture()
        for expected in (2, 1, 0):
            self.step(run, player, 50, cast=True)
            self.assertEqual(instance.vars[11].value(), expected)
        original = instance.states[19].counter
        self.step(run, player, 50, cast=True)
        self.assertEqual(instance.vars[11].value(), 0)
        self.assertEqual(instance.states[19].counter, original)
        self.step(run, player, 220)
        self.assertEqual(instance.vars[11].value(), 1)
        self.step(run, player, 50, cast=True)
        self.assertEqual(instance.vars[11].value(), 0)
        self.assertEqual(instance.states[19].counter, original + 1)

    def test_repeated_packs_extend_healing_duration_without_stacking_rate(self):
        run, player, enemy, ally, target, instance = self.fixture()
        tank = next(hero for hero in heroes().values() if hero.name == "Reinhardt")
        run.match.switch_hero(ally, tank)
        x, y, z = player.mover.position
        ally.mover.position = [x, y, z + 10]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 500, False, None, run.frame)
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(3):
            self.step(run, player, 45, cast=True)
        self.assertEqual(instance.vars[11].value(), 0)
        self.assertEqual(len(fight.brigitte.repairs), 1)
        self.step(run, player, 340)
        self.assertAlmostEqual(vitals.total, before + 330, places=4)
        self.assertFalse(fight.brigitte.repairs)

    def test_target_dying_in_flight_cannot_be_revived(self):
        run, player, enemy, _ally, target, _instance = self.fixture()
        fight = run.match.combat
        self.step(run, player, 20, cast=True)
        self.assertEqual(len(fight.brigitte.repair_deliveries), 1)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        self.step(run, player, 80)
        self.assertEqual(fight.vitals_of(target).total, 0)
        self.assertTrue(fight.vitals_of(target).dead)
        self.assertFalse(fight.brigitte.repairs)

    def test_los_rejection_preserves_charges(self):
        run, player, _enemy, _ally, _target, instance = self.fixture()
        x, y, z = player.mover.position
        wall = World([
            (x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
            (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50),
            (x - 5, y, z + 5, x + 5, y + 5, z + 5, x + 5, y, z + 5),
            (x - 5, y, z + 5, x - 5, y + 5, z + 5, x + 5, y + 5, z + 5),
        ])
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = wall
        self.step(run, player, 60, cast=True)
        self.assertEqual(instance.vars[11].value(), 3)
        self.assertFalse(run.match.combat.brigitte.repairs)

    def test_use_lockout_rejects_early_press_without_banking_it(self):
        run, player, _enemy, _ally, _target, instance = self.fixture()
        self.step(run, player, 20, cast=True)
        self.assertEqual(instance.vars[11].value(), 2)
        self.step(run, player, 20, cast=True)
        self.assertEqual(instance.vars[11].value(), 2)
        self.step(run, player, 30, cast=True)
        self.assertEqual(instance.vars[11].value(), 1)

    def test_target_lock_survives_aim_change_during_cast(self):
        run, player, enemy, ally, target, instance = self.fixture()
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.step(run, player, 1, cast=True)
        x, y, z = player.mover.position
        ally.mover.position = [x, y, z - 10]
        ally.mover.body = None
        self.step(run, player, 200)
        self.assertAlmostEqual(fight.vitals_of(target).total, 160, places=4)
        self.assertEqual(instance.vars[11].value(), 2)

    def test_source_death_during_cast_spends_charge_without_posthumous_healing(self):
        run, player, enemy, _ally, target, instance = self.fixture()
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.step(run, player, 1, cast=True)
        source = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, source, 1000, False, None, run.frame)
        self.step(run, player, 80)
        self.assertEqual(fight.vitals_of(target).total, 50)
        # Authored Wait20 onEnd starts Chase28, whose onBegin decrements
        # charge11. Abort/death follows that onEnd path too; do not refund it.
        self.assertEqual(instance.vars[11].value(), 2)
        self.assertFalse(fight.brigitte.repair_casts)
        self.assertFalse(fight.brigitte.repair_deliveries)
        self.assertFalse(fight.brigitte.repairs)

    def test_source_death_in_flight_preserves_delivery_until_body_retires(self):
        for retire in (False, True):
            with self.subTest(retire=retire):
                run, source, enemy, ally, target, instance = self.fixture()
                source.body_script.ult_seconds = 0
                fight = run.match.combat
                fight._damage(enemy, target, 150, False, None, run.frame)
                self.step(run, source, 20, cast=True)
                self.assertEqual(instance.vars[11].value(), 2)
                self.assertEqual(fight.vitals_of(target).total, 50)
                self.assertFalse(fight.brigitte.repairs)
                [delivery] = fight.brigitte.repair_deliveries
                old_body = source.body
                source_target = next(t for t in fight._targets() if t.owner is source)
                fight._damage(enemy, source_target, 1000, False, None, run.frame)
                self.assertTrue(fight.is_dead(source))
                source.body_script.set_ult_charge(0)
                self.assertIn(delivery, fight.brigitte.repair_deliveries)
                if retire:
                    self.assertTrue(run.match.switch_hero(source, source.hero))
                    self.assertNotEqual(source.body, old_body)
                    source.body_script.ult_seconds = 0
                    self.assertFalse(fight.brigitte.repair_deliveries)
                self.step(run, source, 45)
                self.assertEqual((old_body, ally.body) in fight.brigitte.repairs, not retire)
                self.step(run, source, 155)
                self.assertAlmostEqual(fight.vitals_of(target).total, 50 if retire else 160, places=4)
                self.assertEqual(source.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(fight.brigitte.repairs)
                self.assertFalse(fight.brigitte.repair_deliveries)

    def test_source_death_after_delivery_keeps_issued_heal_but_stops_charge_credit(self):
        run, source, enemy, ally, target, _instance = self.fixture()
        source.body_script.ult_seconds = 0
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.step(run, source, 45, cast=True)
        self.assertIn((source.body, ally.body), fight.brigitte.repairs)
        source_target = next(t for t in fight._targets() if t.owner is source)
        fight._damage(enemy, source_target, 1000, False, None, run.frame)
        source.body_script.set_ult_charge(0)
        self.step(run, source, 160)
        self.assertAlmostEqual(fight.vitals_of(target).total, 160, places=4)
        self.assertEqual(source.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.brigitte.repairs)

    def test_delivered_pack_survives_same_hero_body_replacement_without_new_body_credit(self):
        run, source, enemy, ally, target, _instance = self.fixture()
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.step(run, source, 45, cast=True)
        old_body = source.body
        self.assertTrue(run.match.switch_hero(source, source.hero))
        self.assertNotEqual(source.body, old_body)
        source.body_script.ult_seconds = 0
        self.step(run, source, 180)
        self.assertAlmostEqual(fight.vitals_of(target).total, 160, places=4)
        self.assertEqual(source.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.brigitte.repairs)
        self.assertNotEqual(ally.body, old_body)

    def test_changed_hero_retirement_ends_healing_on_first_roster_observation(self):
        run, source, enemy, _ally, target, _instance = self.fixture()
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.step(run, source, 45, cast=True)
        soldier = next(h for h in heroes().values() if h.name == "Soldier: 76")
        self.assertTrue(run.match.switch_hero(source, soldier))
        self.step(run, source, 1)
        after = fight.vitals_of(target).total
        self.step(run, source, 180)
        self.assertEqual(fight.vitals_of(target).total, after)
        self.assertFalse(fight.brigitte.repairs)

    def test_extended_heal_is_capped_by_authored_three_second_source_loss_wait(self):
        run, source, enemy, ally, _target, _instance = self.fixture()
        fight = run.match.combat
        run.match.switch_hero(ally, next(h for h in heroes().values() if h.name == "Roadhog"))
        x, y, z = source.mover.position
        ally.mover.position, ally.mover.body = [x, y, z + 10], None
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 400, False, None, run.frame)
        for _ in range(3):
            self.step(run, source, 45, cast=True)
        before = fight.vitals_of(target).total
        old_body = source.body
        run.match.switch_hero(source, source.hero)
        self.step(run, source, 1)
        effect = fight.brigitte.repairs[old_body, ally.body]
        self.assertAlmostEqual(effect.cleanup_at - fight.now, 3)
        self.step(run, source, 300)
        self.assertAlmostEqual(fight.vitals_of(target).total - before, 55 * (3 + 0.016), places=4)
        self.assertFalse(fight.brigitte.repairs)

    def test_refresh_after_source_loss_keeps_original_controller_and_cleanup_deadline(self):
        run, first, second, ally, _target, _instance = self.fixture()
        fight = run.match.combat
        run.match.switch_hero(second, heroes()[first.hero.guid])
        second.team = first.team
        x, y, z = first.mover.position
        second.mover.position, second.mover.body = [x + 1, y, z], None
        run.match.switch_hero(ally, next(h for h in heroes().values() if h.name == "Roadhog"))
        ally.mover.position, ally.mover.body = [x, y, z + 10], None
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(first, target, 400, False, None, run.frame)
        for _ in range(3):
            self.step(run, first, 45, cast=True)
        old_body = first.body
        run.match.switch_hero(first, first.hero)
        self.step(run, first, 1)
        deadline = fight.brigitte.repairs[old_body, ally.body].cleanup_at
        self.step(run, second, 45, cast=True)
        effect = fight.brigitte.repairs[second.body, ally.body]
        self.assertIs(effect.healer, second)
        self.assertIs(effect.controller, first)
        self.assertEqual(effect.cleanup_at, deadline)
        self.assertTrue(effect.source_present)
        # A changed first controller must not accelerate the already-running
        # Wait while the refreshed source body is still present.
        run.match.switch_hero(first, next(h for h in heroes().values() if h.name == "Soldier: 76"))
        before, when = fight.vitals_of(target).total, fight.now
        self.step(run, second, 300)
        self.assertAlmostEqual(fight.vitals_of(target).total - before, 55 * (deadline - when), places=4)
        self.assertFalse(fight.brigitte.repairs)

    def test_actual_flashbang_during_cast_cannot_launch_a_deferred_pack_after_expiry(self):
        run, source, enemy, _ally, target, instance = self.fixture()
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        x, y, z = source.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        run.step({source: (0, 0, 16), enemy: (-32768, 0, 16)})
        self.assertIn(source.body, fight.brigitte.repair_casts)
        for _ in range(40):
            if fight.is_stunned(source):
                break
            run.step({enemy: (-32768, 0, 0)})
        self.assertTrue(fight.is_stunned(source))
        for _ in range(150):
            run.step({})
        self.assertFalse(fight.is_stunned(source))
        self.assertAlmostEqual(fight.vitals_of(target).total, 50, places=4)
        self.assertEqual(instance.vars[11].value(), 2)  # authored abort path consumes the charge
        self.assertFalse(fight.brigitte.repair_casts)
        self.assertFalse(fight.brigitte.repair_deliveries)
        self.assertFalse(fight.brigitte.repairs)
        self.step(run, source, 200, cast=True)
        self.assertAlmostEqual(fight.vitals_of(target).total, 160, places=4)
        self.assertEqual(instance.vars[11].value(), 1)

    def accretion_scene(self):
        from test_game_combat import aim

        from ow174.game.content import find_hero

        run, source, enemy, ally, target, instance = self.fixture()
        self.assertTrue(run.match.switch_hero(enemy, find_hero("sigma")))
        x, y, z = source.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z - 6], None
        fight = run.match.combat
        ally_hp = fight.vitals_of(target)
        fight._damage(enemy, target, 150, False, None, run.frame)
        direction = aim(fight._eye(enemy), (x, y + 1.0, z))
        return run, source, enemy, ally, ally_hp, instance, direction

    def test_actual_accretion_after_launch_keeps_the_in_flight_pack(self):
        run, source, enemy, ally, ally_hp, instance, direction = self.accretion_scene()
        x, y, z = source.mover.position
        ally.mover.position, ally.mover.body = [x, y, z + 20], None
        fight = run.match.combat
        run.step({enemy: (*direction, 16)})
        for _ in range(30):
            run.step({enemy: (*direction, 0)})
        run.step({source: (0, 0, 16), enemy: (*direction, 0)})
        for _ in range(35):
            if fight.is_stunned(source):
                break
            run.step({enemy: (*direction, 0)})
        self.assertTrue(fight.is_stunned(source))
        self.assertEqual(ally_hp.total, 50)
        self.assertEqual(instance.vars[11].value(), 2)
        self.assertEqual(len(fight.brigitte.repair_deliveries), 1)
        self.step(run, source, 200)
        self.assertAlmostEqual(ally_hp.total, 160, places=4)
        self.assertFalse(fight.brigitte.repair_deliveries)
        self.assertFalse(fight.brigitte.repairs)

    def test_actual_accretion_during_cast_discards_unlaunched_pack(self):
        run, source, enemy, _ally, ally_hp, instance, direction = self.accretion_scene()
        fight = run.match.combat
        z = source.mover.position[2]
        run.step({enemy: (*direction, 16)})
        for _ in range(90):
            if any(rocket.accretion_source and rocket.position[2] >= z - 2.5 for rocket in fight.rockets):
                break
            run.step({enemy: (*direction, 0)})
        else:
            self.fail("actual Accretion did not approach Brigitte")
        run.step({source: (0, 0, 16), enemy: (*direction, 0)})
        self.assertIn(source.body, fight.brigitte.repair_casts)
        for _ in range(8):
            if fight.is_stunned(source):
                break
            run.step({enemy: (*direction, 0)})
        self.assertTrue(fight.is_stunned(source))
        self.step(run, source, 150)
        self.assertEqual(ally_hp.total, 50)
        self.assertEqual(instance.vars[11].value(), 2)
        self.assertFalse(fight.brigitte.repair_casts)
        self.assertFalse(fight.brigitte.repair_deliveries)
        self.step(run, source, 200, cast=True)
        self.assertAlmostEqual(ally_hp.total, 160, places=4)

    def test_actual_bash_after_launch_preserves_delivery_but_rejects_new_cast(self):
        run, source, enemy, ally, target, instance = self.fixture()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[source.hero.guid]))
        x, y, z = source.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 2.5], None
        ally.mover.position, ally.mover.body = [x, y, z + 20], None
        for _ in range(20):
            run.step({enemy: (-32768, 0, 2)})
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        hp = fight.vitals_of(target)
        for frame in range(20):
            run.step({source: (0, 0, 16 if frame == 0 else 0), enemy: (-32768, 0, 2)})
        self.assertEqual(instance.vars[11].value(), 2)
        delivery, = fight.brigitte.repair_deliveries

        run.step({enemy: (-32768, 0, 3)})
        for _ in range(10):
            if fight.is_stunned(source):
                break
            run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(fight.is_stunned(source))
        self.assertEqual(fight.brigitte.repair_deliveries, [delivery])
        self.assertEqual(hp.total, 50)

        self.step(run, source, 18)
        self.assertTrue(fight.is_stunned(source))
        self.step(run, source, 1, cast=True)
        self.assertFalse(fight.brigitte.repair_casts)
        key = (source.body, ally.body)
        for _ in range(30):
            if key in fight.brigitte.repairs:
                break
            run.step({})
        self.assertTrue(fight.is_stunned(source))
        self.assertIn(key, fight.brigitte.repairs)
        self.assertFalse(fight.brigitte.repair_deliveries)

        self.step(run, source, 160)
        self.assertFalse(fight.is_stunned(source))
        self.assertAlmostEqual(hp.total, 160, places=4)
        self.assertEqual(instance.vars[11].value(), 2)
        self.assertFalse(fight.brigitte.repair_casts)
        self.assertFalse(fight.brigitte.repair_deliveries)
        self.assertFalse(fight.brigitte.repairs)
        self.step(run, source, 26, cast=True)
        self.assertEqual(instance.vars[11].value(), 1)
        self.step(run, source, 174)
        self.assertAlmostEqual(hp.total, 200, places=4)

    def test_actual_bash_during_cast_discards_unlaunched_pack(self):
        run, source, enemy, _ally, target, instance = self.fixture()
        run.match.switch_hero(enemy, heroes()[source.hero.guid])
        x, y, z = source.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 2.5], None
        for _ in range(20):
            run.step({enemy: (-32768, 0, 2)})
        fight = run.match.combat
        fight._damage(enemy, target, 150, False, None, run.frame)
        run.step({source: (0, 0, 16), enemy: (-32768, 0, 3)})
        for _ in range(10):
            if fight.is_stunned(source):
                break
            run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(fight.is_stunned(source))
        self.step(run, source, 200)
        self.assertEqual(fight.vitals_of(target).total, 50)
        self.assertEqual(instance.vars[11].value(), 2)
        self.assertFalse(fight.brigitte.repair_casts)
        self.assertFalse(fight.brigitte.repair_deliveries)


if __name__ == "__main__":
    unittest.main()
