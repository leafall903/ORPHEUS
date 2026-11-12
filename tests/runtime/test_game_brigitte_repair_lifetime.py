"""Actual0EE1 cleanup requests under explicit lookup/controller test services.

These tests observe authored control and timer behavior. Health, native owner
lifetime and operation7 dispatch are separate acceptance boundaries.
"""

import unittest
from unittest.mock import patch

from ow174.game.script import entity_queries, expr, graph, message_receive, nodes, runtime, scene

OWNER, FIRST, SECOND = 0xA0001000, 0xA0001100, 0xA0001200
FIRST_CONTROLLER, SECOND_CONTROLLER = 0xA0002100, 0xA0002200
HERO, OTHER = expr.Asset(0x02E0000000000195), expr.Asset(0x02E000000000006E)


class ControllerWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get("$") == "STU_F7262B36":
            source = expr.to_entity(instance.evaluate(cfg.get("m_801B298F")))
            return expr.Entity({FIRST: FIRST_CONTROLLER, SECOND: SECOND_CONTROLLER}.get(source, 0))
        return super().native(cfg, instance)


class RepairLifetimeResourceTests(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def request(instance, node, caller):
            self.requests.append((instance.component.now, node.pos))
            return True  # Request collector, not a synchronous instance destroy.

        patcher = patch.dict(runtime.HANDLERS, {"STU_22926252": request})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.world = ControllerWorld()
        for entity in (OWNER, FIRST, SECOND, FIRST_CONTROLLER, SECOND_CONTROLLER):
            self.world.bind(entity)
        self.component = runtime.Component(OWNER, world=self.world)
        self.world.bind_script_component(OWNER, self.component)
        self.controllers = {}
        for entity in (FIRST_CONTROLLER, SECOND_CONTROLLER):
            controller = runtime.Component(entity, world=self.world)
            controller.set(13161, HERO)
            self.world.bind_script_component(entity, controller)
            self.controllers[entity] = controller
        self.live = {FIRST, SECOND}
        context = entity_queries.Context(
            OWNER, True, True, False,
            lookup=lambda entity: entity_queries.Target(entity) if entity in self.live else None,
            watch_environment=None,
        )
        self.world.set_entity_query_context(OWNER, context)
        self.effect = self.component.play_loaded_graph(
            graph.graph(0xEE1), source=FIRST, instigator=FIRST, policy=2,
            params=((0x0A4000000000002A, 110),),
        )
        self.component.run_now()
        # Use actual repeat messages to make the healing duration longer than
        # the cleanup wait; keep the resource's source-loss path independently visible.
        self.refresh(FIRST)
        self.refresh(FIRST)
        self.assertEqual(self.effect.vars[21709].value(), expr.Entity(FIRST_CONTROLLER))
        self.assertEqual(self.effect.vars[940].value(), HERO)
        self.component.run_frame(31)

    def refresh(self, source):
        self.assertEqual(message_receive.deliver(self.component, message_receive.Packet(
            0x0240000000000F6F, source, source, ((0x0A4000000000041A, 1),),
        )), 1)
        self.component.run_now()

    def cleanup_requests(self):
        return [time for time, node in self.requests if node == 47]

    def lose_first(self):
        self.live.remove(FIRST)
        self.component.run_frame(32)

    def verify_deadline(self, wait):
        self.assertIsInstance(wait, nodes.Wait)
        self.assertEqual(wait.ms, 3000)
        deadline = wait.start + wait.ms
        self.component.run_frame(deadline // 16 - 1)
        self.assertEqual(self.cleanup_requests(), [])
        self.component.run_frame(deadline // 16)
        self.assertEqual(self.cleanup_requests(), [deadline])

    def test_same_picked_hero_loss_requests_cleanup_after_three_seconds(self):
        self.lose_first()
        wait = self.effect.states[13]
        self.assertTrue(wait.active)
        self.verify_deadline(wait)

    def test_changed_picked_hero_loss_requests_cleanup_without_wait(self):
        self.controllers[FIRST_CONTROLLER].set(13161, OTHER)
        self.lose_first()
        self.assertEqual(self.cleanup_requests(), [self.component.now])
        wait = self.effect.states.get(13)
        self.assertTrue(wait is None or not wait.active)

    def test_refresh_after_loss_keeps_initial_controller_and_pending_cleanup(self):
        self.lose_first()
        wait = self.effect.states[13]
        original_start = wait.start
        self.component.run_frame(64)
        self.refresh(SECOND)
        self.assertEqual(self.effect.vars[40].value(), expr.Entity(SECOND))
        self.assertEqual(self.effect.vars[21709].value(), expr.Entity(FIRST_CONTROLLER))
        self.assertTrue(wait.active)
        self.assertEqual(wait.start, original_start)
        self.verify_deadline(wait)

    def test_refresh_before_loss_watches_new_body_without_starting_cleanup(self):
        self.refresh(SECOND)
        self.lose_first()
        self.assertEqual(self.effect.vars[40].value(), expr.Entity(SECOND))
        self.assertEqual(self.effect.vars[21709].value(), expr.Entity(FIRST_CONTROLLER))
        wait = self.effect.states.get(13)
        self.assertTrue(wait is None or not wait.active)
        self.assertEqual(self.cleanup_requests(), [])


if __name__ == "__main__":
    unittest.main()
