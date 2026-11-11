"""104319 Reinhardt charge-resource probes with disclosed engine adapters.

These execute the actual shared child graph, not Combat event production.
Lifecycle694 and component43 possession are supplied explicitly. The server's
diagnostic60-second fill is disabled. No production charge acceptance is claimed.
"""

import unittest

from ow174.game.script import entity_queries, runtime
from ow174.game.script.driver import BodyScript

HERO = 0x02E0000000000007
BODY, CONTROLLER = 0xA0000101, 0xA0000100
MESSAGE = 0x0240000000000000


class ReinhardtChargeResourceTests(unittest.TestCase):
    def body(self, *, possession=True):
        body = BodyScript(HERO, BODY, 0, ult_seconds=0)
        self.assertEqual(body.component.entity_var(338).value(), 1540)
        self.assertEqual(sum(i.graph.index == 0xB8B
                             for i in body.component.instances.values()), 1)
        if possession:
            targets = {BODY: entity_queries.Target(BODY, possessor=entity_queries.Possession(CONTROLLER))}
            context = entity_queries.Context(BODY, True, True, True, lookup=targets.get,
                                            watch_environment=None, watch_possessor=lambda *args: None)
            body.world.entity_query_context = lambda owner: context
        return body

    def send(self, body, index, params=None):
        self.assertGreater(runtime.send_message(body.component, MESSAGE | index, params or {}, BODY), 0)
        body.component.run_now()

    def enable(self, body):
        self.send(body, 0x694)
        self.assertIs(body.component.entity_var(2426).value(), True)

    @staticmethod
    def charge(body):
        return body.component.entity_var(342).value()

    def test_lifecycle_and_possession_are_required_before_passive_charge(self):
        body = self.body()
        body.command(125, 0)
        self.assertEqual(self.charge(body), 0)
        without_possession = self.body(possession=False)
        self.enable(without_possession)
        without_possession.command(125, 0)
        self.assertEqual(self.charge(without_possession), 0)

    def test_authored_one_second_wait_adds_five_points_at_frame_deadlines(self):
        body = self.body()
        self.enable(body)
        for frame, expected in ((62, 0), (63, 5), (124, 5), (125, 10), (187, 10), (188, 15)):
            body.command(frame, 0)
            self.assertEqual(self.charge(body), expected, frame)

    def test_passive_multipliers_suppression_and_effective_cost_cap(self):
        body = self.body()
        body.component.set(9532, 2.0)
        body.component.set(12918, 3.0)
        self.enable(body)
        body.command(63, 0)
        self.assertEqual(self.charge(body), 30)
        body.component.set(842, True)
        body.component.run_now()
        body.command(250, 0)
        self.assertEqual(self.charge(body), 30)
        body.component.set(842, False)
        body.set_ult_charge(1535)
        body.component.run_now()
        body.command(313, 0)
        self.assertEqual(self.charge(body), 1540)
        body.command(438, 0)
        self.assertEqual(self.charge(body), 1540)

    def test_explicit_charge_message_multiplier_clamp_and_running_gate(self):
        body = self.body()
        self.enable(body)
        body.component.set(9532, 2.0)
        for amount, expected in ((120, 240), (2000, 1540), (-2000, 0)):
            self.send(body, 0xEB, {0x2A: float(amount)})
            self.assertEqual(self.charge(body), expected)
        body.component.set(842, True)
        self.send(body, 0xEB, {0x2A: 120.0})
        self.assertEqual(self.charge(body), 0)


if __name__ == "__main__":
    unittest.main()
