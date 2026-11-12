"""Reflected flight/poison domain state; not encrypted combat replay evidence."""

import unittest

import test_server_snapshot as snapshots

from ow174.game import venom
from ow174.game.server_snapshot import snapshot_digest


class WidowmakerDeflectSnapshotTests(unittest.TestCase):
    def test_reflected_flight_owner_lifetime_and_poison_credit_are_stored(self):
        case = snapshots.ServerSnapshotTests()
        session, _, (source, reflector) = case.scene(two=True)
        mines = source.match.combat.venom
        flight = venom.Flight(source, source.body, (0, 1, 2), (0, -1, 20), 4.5, None, source.team)
        mines.flights.append(flight)
        original = snapshot_digest(case.capture(session))
        flight.owner, flight.body, flight.team = reflector, reflector.body, reflector.team
        flight.velocity = (0, 0, -20)
        transferred = snapshot_digest(case.capture(session))
        self.assertNotEqual(transferred, original)
        flight.remaining -= 0.016
        advanced = snapshot_digest(case.capture(session))
        self.assertNotEqual(advanced, transferred)
        mines.poison.append(venom.Poison(reflector, reflector.body, source.body, 5, 15))
        poisoned = snapshot_digest(case.capture(session))
        self.assertNotEqual(poisoned, advanced)
