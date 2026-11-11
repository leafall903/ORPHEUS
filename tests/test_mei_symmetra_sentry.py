import os
import unittest
from unittest.mock import patch

import test_mei_combat as combat_fixtures
import test_mei_wall as wall_fixtures
import test_server_snapshot as snapshots
from test_game_combat import aim
from test_game_server import pick

from ow174.game import mei, replay_manifest
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import runtime
from ow174.game.server_snapshot import snapshot_digest


def check_sentry_fight(ice):
    fixture = combat_fixtures.MeiCombatTests()
    run, owner, sym, _ = fixture.duel()
    assert run.match.switch_hero(sym, heroes()[0x02E0000000000016])
    fight = run.match.combat
    x, y, z = owner.mover.position
    fight.walls.place_player(sym, (x, y, z + 5.0))
    for _ in range(12):
        run.step({})
    if ice:
        pieces, wall_aim = wall_fixtures.WallTests().place(run, owner, distance=2.0)
        centre = pieces[2]
        point = (centre.feet[0], centre.feet[1] + 1.3, centre.feet[2])
    else:
        point = (x, y, z + 2.0)
    angle = aim(fight._eye(sym), point)
    for frame in range(45):
        run.step({sym: (*angle, 8 if frame == 0 else 0)})
        if fight.sentries.sentries:
            break
    [sentry] = fight.sentries.sentries
    assert sentry.attached_at is None and sentry.speed == 15.0
    for _ in range(100):
        run.step({})
        if not ice and sentry.target == owner.body:
            break
    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))
    if ice:
        assert sentry.support_pillar == centre.entity, (sentry.bot.position, centre.feet)
        assert sentry.target is None and health.total == 250.0
        wall_fixtures.WallTests().tap(run, owner, 16, wall_aim)
        assert sentry.bot not in run.match.bots and not fight.sentries.sentries
        return
    assert sentry.target == owner.body
    turret = next(t for t in fight._targets() if t.owner is sentry.bot)
    assert fight.vitals_of(turret).total == 30.0
    slow = owner.body_script.component.entity_var(23186)
    assert abs(slow.value() - 0.2) < 1e-6
    before = health.total
    for _ in range(10):
        run.step({})
    assert abs(before - health.total - 6.4) < 1e-5, (before, health.total)
    run.step({owner: (0, 0, 8)})
    for _ in range(35):
        run.step({})
    assert mei.protected(owner) and sentry.target is None and not slow.value()
    assert health.total == 250.0
    run.step({owner: (0, 0, 2)})
    for _ in range(5):
        run.step({})
    assert sentry.target == owner.body and abs(slow.value() - 0.2) < 1e-6
    with (
        patch.object(owner.client, "queue_bot_death", create=True),
        patch.object(sym.client, "queue_bot_death", create=True),
    ):
        fixture.fire(run, owner, turret, 2, 0.2)
    assert sentry.bot not in run.match.bots and not fight.sentries.sentries and not slow.value(), (
        sentry.bot.dead,
        sentry.bot in run.match.bots,
        sentry.target,
        slow.value(),
        len(fight.rockets),
    )


@unittest.skipUnless(
    os.environ.get("ORPHEUS_SYMMETRA_SUMMON_BUNDLE"),
    "requires the verified 104319 summon bundle",
)
class MeiSymmetraSentryTests(unittest.TestCase):
    def test_actual_sentry_damage_slow_cryo_and_icicle_retirement(self):
        check_sentry_fight(ice=False)

    def test_actual_sentry_attaches_to_ice_wall_and_retires_on_cancel(self):
        check_sentry_fight(ice=True)

    def test_attached_sentry_is_in_passive_capture_and_replay_pins_bundle(self):
        fixture = snapshots.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        (owner,) = players
        peers[0].message(21616, pick(owner.entity, 0x02E0000000000016), now=100.8)
        session.tick(100.960)
        for offset in range(85):
            session.tick(100.976 + offset * 0.016)
            owner.take_commands(
                [Command(owner.body_script.frame + 1, pitch=8192, buttons=8 if offset == 0 else 0)]
            )
        [sentry] = owner.match.combat.sentries.sentries
        assert sentry.attached_at is not None and sentry.bot.body == 0x0400000000002658
        configuration = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        expected = "0595250af9a5c12e7f1d881a21d8e3bd4f7c29cd5f69d40d8685985741fdfadb"
        self.assertEqual(configuration["ow174.game.symmetra_sentry.bundle_sha256"], expected)
        with patch.object(runtime.Component, "run_now", side_effect=AssertionError("capture ran the VM")):
            before = snapshot_digest(fixture.capture(session))
            sentry.last_tick -= 1
            assert snapshot_digest(fixture.capture(session)) != before

    def test_three_sentry_charges_reject_fourth_until_authored_recovery(self):
        run, mei_player, symmetra, _ = combat_fixtures.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(symmetra, heroes()[0x02E0000000000016]))
        fight = run.match.combat
        x, y, z = symmetra.mover.position
        fight.walls.place_player(mei_player, (x + 30.0, y, z))
        for _ in range(20):
            run.step({})
        weapon = next(i for i in symmetra.body_script.component.instances.values() if i.graph.index == 0x157E)

        def press():
            for _ in range(40):
                run.step({symmetra: (0, 8192, 8)})
            run.step({symmetra: (0, 8192, 0)})

        for _ in range(3):
            press()
        original = tuple(fight.sentries.sentries)
        self.assertEqual(len(original), 3)
        self.assertEqual(weapon.find_var(0, 11).value(), 0)
        press()
        self.assertEqual(tuple(fight.sentries.sentries), original)
        for _ in range(650):
            run.step({})
        press()
        self.assertEqual(len(fight.sentries.sentries), 3)
        self.assertNotIn(original[0], fight.sentries.sentries)
        self.assertNotIn(original[0].bot, run.match.bots)


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
