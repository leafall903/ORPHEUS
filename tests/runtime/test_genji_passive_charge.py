"""Actual0B8B timing in the normal server profile and declared gate variants.

Normal profile binding is explicit server reconstruction. Original association,
server-only initialization and full mode policy remain OPEN.
"""

from unittest.mock import patch

from test_genji_combat import GenjiFixture

from ow174.game.script import expr, runtime


class GenjiAuthoredPassiveChargeTests(GenjiFixture):
    def declared(self, *, multiplier=1, mode_disabled=False):
        run, player = self.spawn(100)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        component = script.component
        original = component.world.native
        foreign = component.world.entity_var

        def native(cfg, instance):
            if cfg.get("$") == "STU_F7262B36":
                return expr.Entity(player.entity)  # explicitly declared result, association is not proved
            return original(cfg, instance)

        def variable(entity, identifier):
            if entity == runtime.GAME_MODE_ENTITY and identifier == 11002:
                return False
            return foreign(entity, identifier)

        self.enterContext(patch.object(component.world, "native", side_effect=native))
        self.enterContext(patch.object(component.world, "entity_var", side_effect=variable))
        for key, value in ((2426, True), (842, False), (11942, mode_disabled),
                           (9532, multiplier), (12918, 1)):
            component.set(key, value)
        component.run_now()
        instance = next(i for i in component.instances.values() if i.graph.index == 0xB8B)
        wait = instance.state(instance.graph.node(3).state)
        return run, player, component, instance, wait

    def charge(self, component):
        return component.entity_var(342).value()

    def deadline(self, instance, wait):
        return next(e.time for e in instance.queue if e.state is wait)

    def test_declared_gates_execute_actual_one_second_tick_and_cap(self):
        run, _player, component, instance, wait = self.declared()
        due = self.deadline(instance, wait)
        self.assertEqual(due - component.now, 1000)
        while component.now < due:
            self.assertEqual(self.charge(component), 0)
            run.step({})
        self.assertEqual(self.charge(component), 5)
        self.assertEqual(self.deadline(instance, wait), due + 1000)
        component.set(342, component.entity_var(338).value() - 1)
        component.run_now()
        while component.now < due + 1000:
            run.step({})
        self.assertEqual(self.charge(component), component.entity_var(338).value())
        self.assertFalse(wait.active)

    def test_declared_multipliers_and_gate_restart_use_resource_expression(self):
        run, _player, component, instance, wait = self.declared(multiplier=6)
        due = self.deadline(instance, wait)
        while component.now < due:
            run.step({})
        self.assertEqual(self.charge(component), 30)
        component.set(2426, False)
        component.run_now()
        self.assertFalse(wait.active)
        for _ in range(100):
            run.step({})
        self.assertEqual(self.charge(component), 30)
        component.set(2426, True)
        component.run_now()
        self.assertEqual(self.deadline(instance, wait) - component.now, 1000)

    def test_declared_mode_disabling_gate_prevents_actual_wait(self):
        run, _player, component, _instance, wait = self.declared(mode_disabled=True)
        self.assertFalse(wait.active)
        for _ in range(130):
            run.step({})
        self.assertEqual(self.charge(component), 0)

    def assert_blade_cycle(self, run, player, component, instance, wait):
        script = player.body_script
        script.set_ult_charge(component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        ability = next(i.state(3) for i in component.instances.values() if i.graph.index == 0x317)
        self.assertTrue(ability.flags & 1)
        self.assertEqual(self.charge(component), 0)
        for _ in range(600):
            if not ability.flags & 1:
                break
            self.assertFalse(wait.active)
            self.assertEqual(self.charge(component), 0)
            run.step({})
        self.assertFalse(ability.flags & 1)
        due = self.deadline(instance, wait)
        self.assertGreater(due, component.now)
        self.assertLessEqual(due - component.now, 1000)
        while component.now < due:
            self.assertEqual(self.charge(component), 0)
            run.step({})
        self.assertEqual(self.charge(component), 5)

    def test_actual_blade_suppresses_authored_passive_then_restarts_fresh_wait(self):
        self.assert_blade_cycle(*self.declared())

    def normal(self, distance=100):
        run, player = self.spawn(distance, passive_charge=True)
        component = player.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0xB8B)
        wait = instance.state(instance.graph.node(3).state)
        return run, player, component, instance, wait

    def test_normal_owned_genji_uses_resource_passive_without_declared_test_services(self):
        run, player = self.spawn(100, passive_charge=True)
        component = player.body_script.component
        self.assertEqual(player.body_script.ult_seconds, 0)
        instance = next(i for i in component.instances.values() if i.graph.index == 0xB8B)
        wait = instance.state(instance.graph.node(3).state)
        self.assertTrue(wait.active)
        due = self.deadline(instance, wait)
        self.assertEqual(self.charge(component), 0)
        while component.now < due:
            self.assertEqual(self.charge(component), 0)
            run.step({})
        self.assertEqual(self.charge(component), 5)

    def test_normal_blade_pauses_resource_charge_and_resumes_after_end(self):
        self.assert_blade_cycle(*self.normal())

    def test_normal_damage_credit_and_resource_tick_do_not_add_debug_fill(self):
        run, player, component, _instance, _wait = self.normal(4)
        self.fire(run, player, 1)
        self.assertEqual(self.charge(component), 87)
        for _ in range(40):
            run.step({})
        self.assertEqual(self.charge(component), 92)

    def test_normal_disabling_gate_cancels_wait_until_reenabled(self):
        run, _player, component, instance, wait = self.normal()
        component.set(11942, True)
        component.run_now()
        self.assertFalse(wait.active)
        for _ in range(130):
            run.step({})
        self.assertEqual(self.charge(component), 0)
        component.set(11942, False)
        component.run_now()
        self.assertEqual(self.deadline(instance, wait) - component.now, 1000)

    def test_normal_death_and_replacement_retire_old_passive_wait(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                run, player, component, _instance, wait = self.normal()
                script = player.body_script
                if replacement:
                    run.match.switch_hero(player, player.hero)
                    self.assertTrue(script.retired)
                    current = player.body_script.component
                    self.assertIsNot(current, component)
                    fresh = next(i for i in current.instances.values() if i.graph.index == 0xB8B)
                    self.assertTrue(fresh.state(fresh.graph.node(3).state).active)
                else:
                    target = next(t for t in run.match.combat._targets() if t.owner is player)
                    run.match.combat._damage(run.players[1], target, 1000, False, None, run.frame)
                    run.step({})
                    self.assertTrue(component.owner_dead)
                    self.assertFalse(wait.active)
                    self.assertFalse(component.entity_var(2426).value())

    def test_standalone_missing_context_stays_unbound_and_explicit_debug_override_remains(self):
        from ow174.game.script.driver import BodyScript

        for override in (None, 2.0):
            with self.subTest(override=override):
                script = BodyScript(0x02E0000000000029, 0xC000A001, 1, ult_seconds=override)
                script.command(20, 0)
                if override is None:
                    self.assertEqual(script.ult_seconds, 0)
                    self.assertEqual(self.charge(script.component), 0)
                else:
                    self.assertEqual(script.ult_seconds, override)
                    self.assertGreater(self.charge(script.component), 0)

    def test_normal_profile_never_adds_debug_fill_on_top_of_authored_tick(self):
        run, player, component, instance, wait = self.normal()
        player.body_script.ult_seconds = 2.0  # legacy mutable debug knob
        due = self.deadline(instance, wait)
        while component.now < due:
            self.assertEqual(self.charge(component), 0)
            run.step({})
        self.assertEqual(self.charge(component), 5)

    def test_normal_owner_spawn_lifecycle_removes_and_restores_related_entity_gate(self):
        _run, player, component, instance, wait = self.normal()
        player.spawned = False
        component.now += 1  # native service changes are observed at the next clock update
        component.run_now()
        self.assertFalse(wait.active)
        player.spawned = True
        component.now += 1
        component.run_now()
        self.assertTrue(wait.active)
        self.assertEqual(self.deadline(instance, wait) - component.now, 1000)

    def test_normal_profile_fills_from_zero_through_actual_vm_wait_events(self):
        _run, _player, component, instance, wait = self.normal()
        cost = component.entity_var(338).value()
        self.assertEqual(cost, 1932)
        ticks = 0
        while self.charge(component) < cost:
            self.assertLess(ticks, 400)
            component.now = self.deadline(instance, wait)
            component.run_now()
            ticks += 1
        self.assertEqual(ticks, 387)
        self.assertEqual(self.charge(component), cost)
        self.assertFalse(wait.active)
