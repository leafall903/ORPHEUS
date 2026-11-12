"""Real Shift/E cooldown edges, preserving original early-press age."""

from test_genji_combat import GenjiFixture


class GenjiInputBufferTests(GenjiFixture):
    def begin(self, skill):
        run, player = self.spawn(100)
        button, graph, active = (8, 0x01DB, 1) if skill == "strike" else (16, 0x03A3, 10)
        run.step({player: (0, 0, button)})
        run.step({})
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == graph)
        self.assertEqual(instance.state(active).counter, 1)
        for _ in range(180):
            if instance.state(0).var.value() > 0:
                break
            run.step({})
        self.assertGreater(instance.state(0).var.value(), 1)
        return run, player, button, instance.state(0), instance.state(active)

    def test_short_press_seconds_too_early_expires_before_ready(self):
        for skill in ("strike", "deflect"):
            with self.subTest(skill=skill):
                run, player, button, ability, active = self.begin(skill)
                run.step({player: (0, 0, button)})
                for _ in range(650):
                    run.step({})
                self.assertEqual(ability.var.value(), 0)
                self.assertEqual(active.counter, 1)

    def test_short_press_within_authored_buffer_fires_after_release(self):
        for skill in ("strike", "deflect"):
            with self.subTest(skill=skill):
                run, player, button, ability, active = self.begin(skill)
                for _ in range(650):
                    if ability.var.value() <= .080:
                        break
                    run.step({})
                self.assertGreater(ability.var.value(), 0)
                run.step({player: (0, 0, button)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(active.counter, 2)

    def test_held_input_during_cooldown_starts_when_ready(self):
        for skill in ("strike", "deflect"):
            with self.subTest(skill=skill):
                run, player, button, _ability, active = self.begin(skill)
                for _ in range(650):
                    run.step({player: (0, 0, button)})
                    if active.counter == 2:
                        break
                self.assertEqual(active.counter, 2)

    def test_fresh_press_after_ready_starts_new_activation(self):
        for skill in ("strike", "deflect"):
            with self.subTest(skill=skill):
                run, player, button, ability, active = self.begin(skill)
                for _ in range(650):
                    if ability.var.value() == 0:
                        break
                    run.step({})
                for _ in range(20):
                    run.step({})
                self.assertEqual(active.counter, 1)
                run.step({player: (0, 0, button)})
                self.assertEqual(active.counter, 2)
