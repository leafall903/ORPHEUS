"""Command-clock boundary handling through the encrypted game link."""

import unittest
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, aim
from test_game_repeated_lifecycles import REAPER, ProtocolHarness, wire_frame
from test_game_roadhog_hook import ROADHOG
from test_server_journal import pick

from ow174.game import ball_mines, bodytrace, combat, messages, movelog, world
from ow174.game.bits import BitReader, BitWriter
from ow174.game.collision import flat_world
from ow174.game.commands import FIRE, FRAME_HALF
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.link import ACK, FIN, KIND_FRAME
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.nodes import Ability
from ow174.game.server import LEAVE_GAME

LAST_VALID = 0xFF7FFFFF
FIRST_UNSAFE = LAST_VALID + 1


def input_window(first, count, *, buttons=0, forward=0, yaw=0, pitch=0):
    """One complete command followed by consecutive identical compressed commands."""
    body = BitWriter()
    body.bits(0, 24)  # latency and key flags
    body.bits(count, 8)
    body.bits(first, 32)
    body.bit(bool(forward))
    if forward:
        body.bits(0b100, 3)  # arbitrary throttles, forward follows
        body.signed(forward, 8)
    body.signed(yaw, 16)
    body.signed(pitch, 16)
    body.bits(buttons, 24)
    body.bits(0, 24)  # action and extra
    for _ in range(count - 1):
        body.bits(0, 2)  # next frame, same command
    payload = BitWriter()
    messages.write_channel(payload, [])
    payload.bit(1)
    payload.bits(body.count, 32)
    payload.append(body)
    return payload.getvalue()


def leave_messages(link):
    """Datagram sequence, reliable ordinal and decoded value for each leave attempt."""
    result = []
    for packet in link.got:
        if packet.kind != KIND_FRAME:
            continue
        reader = BitReader(packet.payload)
        reader.var_a()
        reliable, _ = messages.read_channel(reader)
        result.extend((packet.seq, ordinal, value) for ordinal, kind, value in reliable if kind == LEAVE_GAME)
    return result


class CommandHandoffTests(unittest.TestCase):
    def setUp(self):
        self.run = ProtocolHarness()
        self.addCleanup(self.run.close)
        for module in (bodytrace, movelog):
            edit = patch.object(module, "ENABLED", False)
            edit.start()
            self.addCleanup(edit.stop)

    def soldier(self, anchor=None):
        (handoff,) = self.run.server.create_match(PRACTICE_RANGE, [(1, "Clock", 0, 0, False)])
        link, player = self.run.connect(handoff)
        if anchor is not None:
            player.commands.reset(last_frame=anchor)
        self.run.message(link, 21616, pick(player.entity, SOLDIER))
        script = player.body_script
        self.assertIsNotNone(script)
        self.run.until(lambda: script.stream.base is not None and player.body in link.models)
        self.assertEqual(link.models[player.body].vars[(11, 53)], 30)
        return handoff, link, player

    @staticmethod
    def ammo(player):
        return player.body_script.component.instances[11].vars[53].value()

    @staticmethod
    def motion(player):
        return (
            player.commands.last_frame, player.mover.last_command, tuple(player.mover.position),
            tuple(sample.frame for sample in player.mover.history), player.body_script.frame,
        )

    def test_valid_prefix_fires_then_reliable_leave_and_fresh_handoff(self):
        handoff, link, player = self.soldier(LAST_VALID - 1)
        script, client = player.body_script, player.client
        combat = player.match.combat
        with patch.object(combat, "_shoot", wraps=combat._shoot) as shoot:
            self.run.send(link, payload=input_window(LAST_VALID, 3, buttons=FIRE, forward=127))
        shoot.assert_called_once()
        _, command, state, _, shot_time = shoot.call_args.args
        self.assertEqual((command.frame, shot_time // state.instance.component.frame_ms),
                         (LAST_VALID, LAST_VALID))
        self.assertEqual((player.commands.last_frame, player.mover.last_command.frame, script.frame),
                         (LAST_VALID, LAST_VALID, LAST_VALID))
        self.assertEqual(self.ammo(player), 29)
        self.assertEqual(client.leaving_since, self.run.now)
        self.assertTrue(self.run.server.going_home(player))

        body, before = player.body, self.motion(player)
        self.run.message(link, 21616, pick(player.entity, REAPER), ack=(0, 0))
        self.run.send(link, payload=input_window(FIRST_UNSAFE, 2), ack=(0, 0))
        self.assertEqual((player.body, player.hero.guid, self.motion(player)), (body, SOLDIER, before))
        self.assertIs(player.body_script, script)

        self.run.until(lambda: bool(leave_messages(link)), ack_except=(link,))
        (first,) = leave_messages(link)
        self.run.send(link, ack=(first[0] + 1, 0))
        self.run.until(lambda: len(leave_messages(link)) == 2, ack_except=(link,))
        _, resent = leave_messages(link)
        self.assertGreater(resent[0], first[0])
        self.assertEqual(resent[1:], first[1:])
        self.assertEqual(resent[2], {"+0x78": 0, "+0x79": False})

        self.run.send(link)
        self.run.send(link, FIN)
        self.assertEqual(link.got[-1].flags & (ACK | FIN), ACK | FIN)
        self.assertTrue(player.gone and script.retired and client.closed)
        self.assertEqual(self.run.left.count(player), 1)
        self.assertNotIn(link.conn, self.run.server.clients)

        fresh, new_link, new_player = self.soldier()
        self.assertNotEqual(fresh.conn, handoff.conn)
        self.assertNotEqual((fresh.key_in, fresh.key_out), (handoff.key_in, handoff.key_out))
        self.assertEqual(new_player.client.address, client.address)
        before = self.motion(new_player)
        self.run.send(link, payload=input_window(FIRST_UNSAFE, 1, buttons=FIRE))
        self.run.message(link, 21616, pick(player.entity, REAPER))
        self.run.send(link, FIN)
        self.assertEqual(self.motion(new_player), before)
        self.assertEqual(new_player.hero.guid, SOLDIER)
        self.assertIsNone(new_player.client.leaving_since)
        self.run.command(new_link, buttons=FIRE)
        self.assertEqual(self.ammo(new_player), 29)

    def test_roadhog_disconnect_releases_hook_and_rejoin_starts_fresh(self):
        server = self.run.server
        old, rival = server.create_match(KANEZAKA_DM, [(1, "Hook", 0, 0, False), (2, "Target", 0, 1, False)])
        link, hog = self.run.connect(old)
        rival_link, target = self.run.connect(rival)
        self.run.message(link, 21616, pick(hog.entity, ROADHOG))
        self.run.message(rival_link, 21616, pick(target.entity, SOLDIER))
        match = hog.match
        fight = match.combat
        script = hog.body_script
        old_body = hog.body
        self.run.until(lambda: script.stream.base is not None and target.body_script.stream.base is not None)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x01C6)
        x, y, z = hog.mover.position
        target.mover.position[:] = (x + 30, y, z)
        target.mover.body = None
        self.run.step(5)
        self.run.command(link, buttons=FIRE)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.run.step(60)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
        before = pool.total

        def latch(caster_link, caster):
            x, y, z = caster.mover.position
            target.mover.position[:] = (x, y, z + 10)
            target.mover.body = None
            self.run.command(caster_link, buttons=8)
            for _ in range(60):
                self.run.step()
                self.run.command(caster_link)
                if fight.hooks.controlled(target.body):
                    return
            self.fail("Roadhog Hook did not catch the connected target")

        latch(link, hog)
        self.assertEqual(pool.total, before - 30)
        target_frame = target.commands.last_frame
        caught_z = target.mover.position[2]
        self.run.step(5)
        self.assertEqual(target.commands.last_frame, target_frame)
        self.assertLess(target.mover.position[2], caught_z)
        self.assertTrue(fight.hooks.controlled(target.body))
        hook = next(i for i in script.component.instances.values() if i.graph.index == 0x01C8)
        cooldown = next(state for state in hook.states.values() if state.node.pos == 11)
        self.assertGreater(cooldown.remaining(), 0)
        self.run.send(link, FIN)
        self.assertTrue(hog.gone and script.retired)
        self.assertNotIn(old.conn, server.clients)
        self.assertNotIn(old_body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(target.body))
        self.assertEqual(target.mover.velocity, (0, 0, 0))
        self.assertFalse(any(key[0] == old_body for key in fight.hooks.seen))

        (fresh,) = server.join_match(match, [(hog.account_lo, hog.name, 0, 0, False)])
        new_link, new_hog = self.run.connect(fresh)
        self.run.message(new_link, 21616, pick(new_hog.entity, ROADHOG))
        new_script = new_hog.body_script
        self.run.until(lambda: new_script.stream.base is not None)
        self.assertIsNot(new_script, script)
        self.assertNotEqual(new_hog.body, old_body)
        self.assertNotEqual(new_link.conn, link.conn)
        fresh_weapon = next(i for i in new_script.component.instances.values() if i.graph.index == 0x01C6)
        before_motion = self.motion(new_hog)
        self.run.send(link, payload=input_window(new_script.frame + 1, 2, buttons=FIRE | 8 | 16 | 32))
        self.run.send(link, FIN)
        self.assertEqual(self.motion(new_hog), before_motion)
        self.assertEqual(fresh_weapon.vars[53].value(), 5)
        self.assertFalse(fight.hooks.controlled(target.body))
        self.assertEqual(pool.total, before - 30)

        latch(new_link, new_hog)
        self.assertEqual(pool.total, before - 60)
        self.assertIn(new_hog.body, fight.hooks.flights)
        self.assertNotIn(old_body, fight.hooks.flights)

    def test_late_hook_window_suppresses_old_input_and_keeps_fresh_suffix(self):
        for tournament in (False, True):
            for primed in (False, True):
                with self.subTest(tournament=tournament, primed=primed):
                    self.check_late_hook_window(tournament, primed)

    def test_late_halt_window_preserves_actions_and_only_moves_the_fresh_suffix(self):
        for tournament in (False, True):
            for primed in (False, True):
                with self.subTest(tournament=tournament, primed=primed):
                    self.check_late_halt_window(tournament, primed)

    def check_late_halt_window(self, tournament, primed):
        from ow174.game.commands import SECONDARY

        source, target = self.run.server.create_match(
            KANEZAKA_DM, [(1, "Orisa", 0, 0, False), (2, "Hog", 0, 1, tournament)],
        )
        caster, orisa = self.run.connect(source)
        victim, hog = self.run.connect(target)
        self.run.message(caster, 21616, pick(orisa.entity, 0x02E000000000013E))
        self.run.message(victim, 21616, pick(hog.entity, ROADHOG))
        fight = hog.match.combat
        halt = fight.halt
        if halt.control is None or halt.query is None:
            self.skipTest("prepared 104319 0CCC/0CCE Halt inputs required")
        self.run.until(lambda: all(p.body_script.stream.base is not None for p in (orisa, hog)))
        for player, pose in ((orisa, (0, 1, 0)), (hog, (1.2, 1, 5))):
            player.mover.position[:] = pose
            player.mover.body = None
        if primed:
            self.run.command(victim)
        else:
            self.assertIsNone(hog.commands.last_frame)
        anchor_frame, anchor_tick = hog.command_clock
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        self.run.command(caster, buttons=SECONDARY)
        for _ in range(25):
            self.run.step()
            self.run.command(caster)
        orb, = halt.orbs.values()
        self.run.command(caster, buttons=SECONDARY)
        self.assertIsNotNone(orb.requested)
        self.run.step()
        self.assertIn(hog.body, halt.pulls)
        self.run.until(lambda: hog.body not in halt.pulls)
        self.assertEqual(hog.command_clock, (anchor_frame, anchor_tick))
        pose = tuple(hog.mover.position)
        self.assertGreater(pose[2], 6)
        last_motion = halt.player_pose_ticks[hog.body]
        cutoff = anchor_frame + (last_motion - anchor_tick) * 16000 // hog.quanta
        self.assertEqual(halt.player_command_cutoffs[hog.body], cutoff)

        old = input_window(cutoff - 5, 3, buttons=FIRE, forward=127)
        self.run.send(victim, payload=old)
        self.assertEqual(hog.commands.last_frame, cutoff - 3)
        self.assertEqual(hog.command_clock, (anchor_frame, anchor_tick))
        self.assertEqual(tuple(hog.mover.position), pose)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.run.send(victim, payload=old)
        self.assertEqual(weapon.vars[53].value(), 4)

        mixed = input_window(cutoff - 2, 4, forward=127)
        self.run.send(victim, payload=mixed)
        self.assertEqual(hog.commands.last_frame, cutoff + 1)
        self.assertEqual(hog.command_clock, (cutoff + 1, hog.match.tick))
        history = list(hog.mover.history)[-4:]
        self.assertEqual([sample.frame for sample in history], list(range(cutoff - 2, cutoff + 2)))
        self.assertTrue(all(sample.position == pose for sample in history[:3]))
        self.assertGreater(hog.mover.position[2], pose[2])
        after = self.motion(hog)
        self.run.send(victim, payload=mixed)
        self.assertEqual(self.motion(hog), after)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertEqual(fight.vitals[hog.body].total, 600)

        old_body = hog.body
        self.run.message(victim, 21616, pick(hog.entity, SOLDIER))
        self.run.until(lambda: hog.body != old_body and hog.body_script.stream.base is not None)
        self.assertNotIn(old_body, halt.player_pose_ticks)
        self.assertNotIn(old_body, halt.player_command_cutoffs)

    def test_halt_disconnect_retires_old_body_and_rejoins_fresh(self):
        for phase in ("flight", "caster_pull", "victim_pull"):
            with self.subTest(phase=phase):
                self.check_halt_rejoin(phase)

    @contextmanager
    def lose_first_body_destroy(self, observer, body):
        applied = observer.apply
        dropped = []

        def drop(packet):
            if not dropped and packet.kind == KIND_FRAME:
                _, records = wire_frame(packet)
                if any(record.op == world.OP_DESTROY and record.entity == body for record, _ in records):
                    dropped.append(packet.seq)
                    return
            applied(packet)

        with patch.object(observer, "apply", side_effect=drop):
            yield
            self.run.until(lambda: body not in observer.models and body not in observer.fields)
        self.assertEqual(len(dropped), 1)
        delivered = {seq for seq, record in observer.records
                     if record.op == world.OP_DESTROY and record.entity == body}
        self.assertTrue(delivered)
        self.assertTrue(delivered.isdisjoint(dropped))

    def test_reflected_halt_disconnect_and_loss_follow_the_deflector(self):
        for retire_original in (False, True):
            for acquired in (False, True):
                with self.subTest(retire_original=retire_original, acquired=acquired):
                    self.check_reflected_halt_disconnect(retire_original, acquired)

    def check_reflected_halt_disconnect(self, retire_original, acquired):
        from test_game_mover import box

        from ow174.game.collision import World
        from ow174.game.commands import SECONDARY

        server = self.run.server
        source, reflector, target = server.create_match(
            replace(KANEZAKA_DM, free_for_all=False),
            [(1, "Orisa", 0, 0, False), (2, "Genji", 0, 1, False), (3, "Hog", 0, 0, False)],
        )
        caster, orisa = self.run.connect(source)
        deflector, genji = self.run.connect(reflector)
        victim, hog = self.run.connect(target)
        heroes = ((caster, orisa, 0x02E000000000013E), (deflector, genji, 0x02E0000000000029),
                  (victim, hog, ROADHOG))
        for peer, player, hero in heroes:
            self.run.message(peer, 21616, pick(player.entity, hero))
        match = hog.match
        halt = match.combat.halt
        if halt.control is None or halt.query is None:
            self.skipTest("prepared 104319 0CCC/0CCE Halt inputs required")
        self.run.until(lambda: all(player.body_script.stream.base is not None for _, player, _ in heroes))
        self.assertEqual(orisa.team, hog.team)
        self.assertNotEqual(genji.team, hog.team)
        match.collision_world = World(box(-40, 40, 0, 1, -40, 40) + box(-4, 4, 1, 7, -4, -3.8))
        for player, position in ((orisa, (0, 1, 0)), (genji, (0, 1, 5)), (hog, (1.5, 1, -2))):
            player.mover.position[:] = position
            player.mover.body = None

        def face_orisa(buttons=0):
            frame = genji.body_script.frame + 1
            self.run.send(deflector, payload=input_window(frame, 1, yaw=-32768, buttons=buttons))

        for _ in range(30):
            self.run.step()
            self.run.command(caster)
            face_orisa()
        face_orisa(16)
        self.run.command(caster, buttons=SECONDARY)
        for _ in range(55):
            self.run.step()
            self.run.command(caster)
            face_orisa()
            if ((not acquired or hog.body in halt.pulls)
                    and any(orb.shooter is genji for orb in halt.orbs.values())):
                break
        orb, = halt.orbs.values()
        self.assertIs(orb.shooter, genji)
        self.assertEqual(orb.acquired, acquired)
        leaving, old_link = (orisa, caster) if retire_original else (genji, deflector)
        old_body = leaving.body
        self.run.until(lambda: old_body in victim.models)
        before = tuple(hog.mover.position)
        command_frame = hog.body_script.frame
        with self.lose_first_body_destroy(victim, old_body):
            self.run.send(old_link, FIN)
            self.assertNotIn(old_link.conn, server.clients)
            self.assertEqual(orb.entity in halt.orbs, retire_original)
            self.assertEqual(hog.body in halt.pulls, acquired and retire_original)
        self.run.step(110)
        self.assertEqual(hog.body_script.frame, command_frame)
        if retire_original:
            self.assertLess(hog.mover.position[2], before[2] - .5)
        else:
            self.assertEqual(tuple(hog.mover.position), before)
        self.assertFalse(halt.orbs)
        self.assertFalse(halt.pulls)
        self.assertEqual(match.combat.vitals[hog.body].total, 600)

    def check_halt_rejoin(self, phase):
        from ow174.game.commands import SECONDARY
        from ow174.game.orisa_halt import HALT

        orisa_hero = 0x02E000000000013E
        server = self.run.server
        source, target = server.create_match(
            KANEZAKA_DM, [(1, "Orisa", 0, 0, False), (2, "Hog", 0, 1, False)],
        )
        caster_link, orisa = self.run.connect(source)
        victim_link, hog = self.run.connect(target)
        self.run.message(caster_link, 21616, pick(orisa.entity, orisa_hero))
        self.run.message(victim_link, 21616, pick(hog.entity, ROADHOG))
        match = orisa.match
        fight = match.combat
        halt = fight.halt
        if halt.control is None or halt.query is None:
            self.skipTest("prepared 104319 0CCC/0CCE Halt inputs required")

        def tick(caster_buttons=0):
            self.run.step()
            self.run.command(caster_link, buttons=caster_buttons)
            self.run.command(victim_link)

        def place():
            for actor, position in ((orisa, (0, 1, 0)), (hog, (1.2, 1, 5))):
                actor.mover.position[:] = position
                actor.mover.body = None
            for _ in range(30):
                tick()

        def launch():
            tick(SECONDARY)
            for _ in range(12):
                tick()
                if halt.orbs:
                    break
            orb, = halt.orbs.values()
            return orb

        def detonate(orb, *, lose_press=False):
            while fight.tick < orb.born + 18:
                tick()
            if lose_press:
                missed = orisa.body_script.frame + 1
                self.run.step()
                self.run.command(victim_link)
                self.run.step()
                self.run.command(caster_link, advance=2)
                self.run.command(victim_link)
                self.run.send(caster_link, payload=input_window(missed, 1, buttons=SECONDARY))
                self.assertIsNone(orb.requested)
                self.assertEqual(tuple(halt.orbs), (orb.entity,))
            tick(SECONDARY)
            self.assertIsNotNone(orb.requested)
            requested = orb.requested
            tick()
            self.assertIn(hog.body, halt.pulls)
            pull = halt.pulls[hog.body].script
            if lose_press:
                duplicate = input_window(orisa.body_script.frame - 1, 1, buttons=SECONDARY)
                self.run.send(caster_link, payload=duplicate)
                self.assertEqual(orb.requested, requested)
                self.assertIs(halt.pulls[hog.body].script, pull)
            self.run.until(lambda: victim_link.models[hog.body].graphs.get(pull.id) == 0xCCE)
            return pull.id

        self.run.until(lambda: all(p.body_script.stream.base is not None for p in (orisa, hog)))
        place()
        orb = launch()
        retire_source = phase != "victim_pull"
        leaving, old_link, observer = (
            (orisa, caster_link, victim_link) if retire_source else (hog, victim_link, caster_link)
        )
        if phase != "flight":
            pull_id = detonate(orb)
            for _ in range(26):
                tick()
            self.assertIn(hog.body, halt.pulls)
            self.assertGreater(hog.mover.position[2], 5.1)
        old_body = leaving.body
        old_script = leaving.body_script
        self.run.until(lambda: old_body in observer.models)
        with self.lose_first_body_destroy(observer, old_body):
            self.run.send(old_link, FIN)
            self.assertTrue(leaving.gone and old_script.retired)
            self.assertNotIn(old_link.conn, server.clients)
            self.assertFalse(halt.pulls)
            if phase == "caster_pull":
                self.assertEqual(hog.mover.velocity, (0, 0, 0))
            if retire_source:
                self.assertFalse(halt.orbs)
        if phase == "caster_pull":
            x, _, z = hog.mover.position
            for _ in range(5):
                self.run.step()
                self.run.command(victim_link)
            self.assertEqual((hog.mover.position[0], hog.mover.position[2]), (x, z))
            self.run.until(lambda: pull_id not in victim_link.models[hog.body].graphs)

        (handoff,) = server.join_match(match, [(leaving.account_lo, leaving.name, 0, 0, False)])
        new_link, fresh = self.run.connect(handoff)
        self.run.message(new_link, 21616, pick(fresh.entity, orisa_hero if retire_source else ROADHOG))
        self.run.until(lambda: fresh.body_script.stream.base is not None and fresh.body in observer.models)
        self.assertNotEqual(fresh.body, old_body)
        self.assertNotEqual(new_link.conn, old_link.conn)
        before = self.motion(fresh)
        stale = input_window(fresh.body_script.frame + 1, 2, buttons=SECONDARY, forward=127)
        self.run.send(old_link, payload=stale)
        self.run.send(old_link, FIN)
        self.assertEqual(self.motion(fresh), before)
        self.assertFalse(halt.pulls)

        if retire_source:
            caster_link, orisa = new_link, fresh
        else:
            victim_link, hog = new_link, fresh
            ability = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == HALT)
            for _ in range(420):
                tick()
                if not halt.orbs and ability.state(2).remaining() == 0:
                    break
            self.assertEqual(ability.state(2).remaining(), 0)
        self.assertFalse(halt.orbs)
        place()
        new_orb = launch()
        self.assertNotEqual(new_orb.entity, orb.entity)
        fresh_pull = detonate(new_orb, lose_press=phase == "flight")
        victim_frame = hog.body_script.frame
        for _ in range(75):
            if phase == "flight":
                self.run.step()
                self.run.command(caster_link)
            else:
                tick()
        if phase == "flight":
            self.assertEqual(hog.body_script.frame, victim_frame)
            self.run.command(victim_link)
            self.run.until(lambda: fresh_pull not in victim_link.models[hog.body].graphs)
        self.assertGreater(hog.mover.position[2], 6)
        self.assertEqual(fight.vitals[hog.body].total, 600)
        self.assertFalse(halt.orbs)
        self.assertFalse(halt.pulls)

    def check_late_hook_window(self, tournament, primed):
        server = self.run.server
        owner, victim = server.create_match(
            KANEZAKA_DM, [(1, "Hook", 0, 0, False), (2, "Target", 0, 1, tournament)],
        )
        link, hog = self.run.connect(owner)
        victim_link, target = self.run.connect(victim)
        for peer, player in ((link, hog), (victim_link, target)):
            self.run.message(peer, 21616, pick(player.entity, ROADHOG))
        anchor_frame, anchor_tick = target.body_script.frame, target.match.tick
        self.run.until(lambda: all(p.body_script.stream.base is not None for p in (hog, target)))
        fight = hog.match.combat
        x, y, z = hog.mover.position
        target.mover.position[:] = (x, y, z + 10)
        target.mover.body = None
        weapon = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x01C6)
        if primed:
            self.run.command(victim_link)
            anchor_frame, anchor_tick = target.commands.last_frame, target.match.tick
        else:
            self.assertIsNone(target.commands.last_frame)
        self.assertEqual(target.command_clock, (anchor_frame, anchor_tick))
        self.run.command(link, buttons=8)
        for _ in range(60):
            self.run.step()
            self.run.command(link)
            if fight.hooks.controlled(target.body):
                break
        self.assertTrue(fight.hooks.controlled(target.body))
        first = anchor_frame + (fight.tick - anchor_tick) * 16000 // target.quanta
        self.run.until(lambda: not fight.hooks.controlled(target.body))
        cutoff = anchor_frame + (fight.tick - anchor_tick) * 16000 // target.quanta
        pose = tuple(target.mover.position)
        pool = fight.vitals[target.body]
        self.assertEqual(pool.total, 570)
        old = input_window(first, 3, buttons=FIRE | 8 | 16, forward=127)
        self.run.send(victim_link, payload=old)
        self.assertEqual(target.commands.last_frame, first + 2)
        self.assertEqual(target.command_clock, (anchor_frame, anchor_tick))
        self.assertEqual(tuple(target.mover.position), pose)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertFalse(target.body_script.component.held)
        self.assertNotIn(target.body, fight.hooks.flights)
        self.assertEqual(fight.incoming_damage_scale(target), 1)
        self.run.send(victim_link, payload=old)
        self.assertEqual(target.commands.last_frame, first + 2)

        self.run.step()
        mixed = input_window(cutoff - 2, 4, buttons=FIRE, forward=127)
        self.run.send(victim_link, payload=mixed)
        self.assertEqual(target.commands.last_frame, cutoff + 1)
        self.assertEqual(target.command_clock, (cutoff + 1, target.match.tick))
        history = list(target.mover.history)[-4:]
        self.assertEqual([sample.frame for sample in history], list(range(cutoff - 2, cutoff + 2)))
        self.assertTrue(all(sample.position == pose for sample in history[:3]))
        self.assertGreater(target.mover.position[2], pose[2])
        self.assertEqual(weapon.vars[53].value(), 4)
        after = self.motion(target)
        self.run.send(victim_link, payload=mixed)
        self.assertEqual(self.motion(target), after)
        self.assertEqual(weapon.vars[53].value(), 4)

        old_body = target.body
        self.run.message(victim_link, 21616, pick(target.entity, SOLDIER))
        self.run.until(lambda: target.body != old_body and target.body_script.stream.base is not None)
        self.assertNotIn(old_body, fight.hooks.player_command_cutoffs)
        self.assertNotIn(target.body, fight.hooks.player_command_cutoffs)
        self.run.command(victim_link, buttons=FIRE)
        self.assertEqual(self.ammo(target), 29)

    def test_roadhog_inflight_scrap_retires_on_disconnect_and_rejoin_fires_fresh(self):
        for button, fragmented in ((1, False), (2, False), (2, True)):
            with self.subTest(button=button, fragmented=fragmented):
                self.check_roadhog_weapon_rejoin(button, fragmented=fragmented)

    def test_roadhog_buffered_melee_retires_on_disconnect_and_rejoin_hits_fresh(self):
        self.check_roadhog_weapon_rejoin(0x800, fragmented=False)

    def check_roadhog_weapon_rejoin(self, button, *, fragmented):
        server = self.run.server
        old, rival = server.create_match(
            KANEZAKA_DM, [(1, "Hog", 0, 0, False), (2, "Target", 0, 1, False)],
        )
        link, hog = self.run.connect(old)
        rival_link, target = self.run.connect(rival)
        self.run.message(link, 21616, pick(hog.entity, ROADHOG))
        self.run.message(rival_link, 21616, pick(target.entity, SOLDIER))
        match, body, script = hog.match, hog.body, hog.body_script
        fight = match.combat
        self.run.until(lambda: script.stream.base is not None and target.body_script.stream.base is not None)
        target_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
        health = target_pool.total

        melee = button == 0x800
        if melee:
            x, y, z = hog.mover.position
            match.collision_world = flat_world(y)
            target.mover.position[:] = (x, y, z + 1.5)
            target.mover.body = None
            for _ in range(12):
                self.run.step()
            self.run.command(rival_link)
        advance = max(1, match.tick + 1 - script.frame) if melee else 1
        self.run.command(link, buttons=button, advance=advance)
        if melee:
            health -= 30
            for _ in range(25):
                if target_pool.total == health:
                    break
                self.run.step()
                self.run.command(link, buttons=button)
            self.assertEqual(target_pool.total, health)
            self.run.command(link)
            self.run.command(link, buttons=button)
            queued = next(state for instance in script.component.instances.values()
                          if instance.graph.index == 0x0043 for state in instance.states.values()
                          if isinstance(state, Ability))
            self.assertTrue(queued.presses)
            self.assertEqual(target_pool.total, health)
        else:
            self.assertTrue(any(rocket.shooter is hog for rocket in fight.rockets))
        if fragmented:
            for _ in range(20):
                self.run.step()
                if any(rocket.shooter is hog and rocket.burst is None for rocket in fight.rockets):
                    break
            else:
                self.fail("Secondary slug did not emit in-flight fragments")
        self.run.send(link, FIN)
        self.assertTrue(hog.gone and script.retired)
        self.assertFalse(any(rocket.shooter is hog for rocket in fight.rockets))

        (fresh,) = server.join_match(match, [(hog.account_lo, hog.name, 0, 0, False)])
        new_link, new_hog = self.run.connect(fresh)
        self.run.message(new_link, 21616, pick(new_hog.entity, ROADHOG))
        new_script = new_hog.body_script
        self.run.until(lambda: new_script.stream.base is not None)
        self.assertNotEqual(new_hog.body, body)
        weapon = next(i for i in new_script.component.instances.values() if i.graph.index == 0x1C6)
        x, y, z = new_hog.mover.position
        target.mover.position[:] = (x, y, z + (1.5 if melee else 4))
        target.mover.body = None
        self.run.send(link, payload=input_window(new_script.frame + 1, 2, buttons=button))
        for _ in range(80 if melee else 25):
            self.run.step()
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertEqual(target_pool.total, health)
        advance = max(1, match.tick + 1 - new_script.frame) if melee else 1
        self.run.command(new_link, buttons=button, advance=advance)
        for _ in range(30):
            self.run.step()
            if melee:
                self.run.command(new_link)
        self.assertEqual(weapon.vars[53].value(), 5 if melee else 4)
        if melee:
            self.assertEqual(target_pool.total, health - 30)
        else:
            self.assertLess(target_pool.total, health)

    def test_roadhog_active_channels_retire_on_disconnect_and_rejoin_cleanly(self):
        for button in (16, 32):
            with self.subTest(button=button):
                self.check_roadhog_channel_rejoin(button)

    def check_roadhog_channel_rejoin(self, button):
        server = self.run.server
        old, rival = server.create_match(KANEZAKA_DM, [(1, "Hog", 0, 0, False), (2, "Target", 0, 1, False)])
        link, hog = self.run.connect(old)
        rival_link, target = self.run.connect(rival)
        self.run.message(link, 21616, pick(hog.entity, ROADHOG))
        self.run.message(rival_link, 21616, pick(target.entity, SOLDIER))
        match = hog.match
        fight = match.combat
        script = hog.body_script
        self.run.until(lambda: script.stream.base is not None and target.body_script.stream.base is not None)
        match.collision_world = flat_world(1)
        script.ult_seconds = 0
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        target_pool = combat.Vitals(target.body, 20000)
        fight.vitals[target.body] = target_pool

        def face_target(caster):
            caster.mover.position[:] = (0, 1, 0)
            caster.mover.body = None
            target.mover.position[:] = (0, 1, -4 if button == 16 else 4)
            target.mover.body = None

        def rifle_hit(victim, victim_pool, amount):
            before = victim_pool.total
            self.run.step()
            frame = max(target.body_script.frame, target.commands.last_frame or 0, match.tick) + 1
            x, y, z = victim.mover.position
            yaw, pitch = aim(fight._eye(target), (x, y + .9, z))
            rounds = self.ammo(target)
            self.run.send(rival_link, payload=input_window(frame, 1, buttons=FIRE, yaw=yaw, pitch=pitch))
            self.assertEqual(self.ammo(target), rounds - 1)
            self.assertAlmostEqual(victim_pool.total, before - amount, places=4)
            self.run.command(rival_link)

        def commands(caster_link, count):
            for _ in range(count):
                self.run.step()
                self.run.command(caster_link)

        face_target(hog)
        if button == 32:
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.command(link, buttons=button)
        commands(link, 60)
        ultimate, = script.ultimates
        if button == 16:
            self.assertGreater(pool.total, 200)
            self.assertLess(pool.total, 500)
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
            self.assertIn(hog.body, fight.breather_deadlines)
            rifle_hit(hog, pool, 10)
            self.assertAlmostEqual(pool.ult_healing_deficit, 10)
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        else:
            self.assertTrue(ultimate.flags & Ability.RUNNING)
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
            self.assertLess(target_pool.total, 20000)

        previous = pool.total, target_pool.total
        old_body = hog.body
        self.run.until(lambda: old_body in rival_link.models)
        with self.lose_first_body_destroy(rival_link, old_body):
            self.run.send(link, FIN)
            self.assertTrue(hog.gone and script.retired)
            self.assertNotIn(old_body, fight.vitals)
            self.assertNotIn(old_body, fight.breather_deadlines)
        (fresh,) = server.join_match(match, [(hog.account_lo, hog.name, 0, 0, False)])
        fresh_link, fresh_hog = self.run.connect(fresh)
        self.run.message(fresh_link, 21616, pick(fresh_hog.entity, ROADHOG))
        fresh_script = fresh_hog.body_script
        self.run.until(lambda: fresh_script.stream.base is not None)
        fresh_script.ult_seconds = 0
        self.assertNotEqual(fresh_hog.body, old_body)
        fresh_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is fresh_hog))
        self.assertEqual(fresh_pool.total, 600)
        self.assertEqual(fight.incoming_damage_scale(fresh_hog), 1)

        self.run.send(link, payload=input_window(fresh_script.frame + 1, 2, buttons=button))
        commands(fresh_link, 60)
        self.assertEqual((pool.total, target_pool.total), previous)
        if button == 16:
            self.assertEqual(fresh_pool.ult_healing_deficit, 0)
            self.assertEqual(fresh_script.component.entity_var(ULT_CHARGE).value(), 0)
            face_target(fresh_hog)
            fresh_pool.damage(400)
            fight._sync_body_health(fresh_hog, fresh_pool)
            rifle_hit(fresh_hog, fresh_pool, 20)
            self.assertAlmostEqual(fresh_pool.ult_healing_deficit, 20)
            press_frame = fresh_script.frame + 1
            press = input_window(press_frame, 1, buttons=16)
            self.run.send(fresh_link, payload=press)
            self.assertEqual(fight.incoming_damage_scale(fresh_hog), .5)
            deadline = fight.breather_deadlines[fresh_hog.body]
            self.run.send(fresh_link, payload=press)
            self.assertEqual(fresh_script.frame, press_frame)
            self.assertEqual(fight.breather_deadlines[fresh_hog.body], deadline)
            commands(fresh_link, 180)
            self.assertAlmostEqual(fresh_pool.total, 480)
            self.assertAlmostEqual(fresh_script.component.entity_var(ULT_CHARGE).value(), 20)
            self.assertAlmostEqual(fresh_pool.ult_healing_deficit, 0)
            self.assertEqual(fight.incoming_damage_scale(fresh_hog), 1)
        else:
            fresh_ultimate, = fresh_script.ultimates
            face_target(fresh_hog)
            self.run.command(fresh_link, buttons=32)
            commands(fresh_link, 60)
            self.assertFalse(fresh_ultimate.flags & Ability.RUNNING)
            self.assertEqual(target_pool.total, previous[1])
            fresh_script.set_ult_charge(fresh_script.component.entity_var(ULT_COST).value())
            self.run.command(fresh_link, buttons=32)
            commands(fresh_link, 60)
            self.assertTrue(fresh_ultimate.flags & Ability.RUNNING)
            self.assertLess(target_pool.total, previous[1])

    def test_quad_cannons_held_fire_retires_with_the_encrypted_body(self):
        from ow174.game import quad_cannons

        server = self.run.server
        handoff, rival = server.create_match(KANEZAKA_DM, [(1, "Ball", 0, 0, False), (2, "Hog", 0, 1, False)])
        link, ball = self.run.connect(handoff)
        hog_link, hog = self.run.connect(rival)
        self.run.message(link, 21616, pick(ball.entity, quad_cannons.HERO))
        self.run.message(hog_link, 21616, pick(hog.entity, ROADHOG))
        match = ball.match
        fight = match.combat
        script = ball.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        self.run.until(lambda: script.stream.base is not None and hog.body_script.stream.base is not None)
        x, y, z = ball.mover.position
        hog.mover.position[:] = (x, y, z + 4)
        hog.mover.body = None
        for _ in range(45):
            self.run.command(link)
            self.run.step()
        pool = fight.vitals_of(next(target for target in fight._targets() if target.owner is hog))
        weapon = next(i for i in script.component.instances.values() if i.graph.index == quad_cannons.GRAPH)
        for _ in range(24):
            self.run.command(link, buttons=FIRE)
            self.run.step()
        self.assertTrue(weapon.states[9].active)
        self.assertLess(weapon.vars[53].value(), 80)
        health = pool.total
        self.assertLess(health, 600)

        self.run.send(link, FIN)
        self.assertTrue(script.retired and ball.gone)
        self.assertNotIn(handoff.conn, server.clients)
        self.run.step(45)
        self.assertEqual(pool.total, health)

        (fresh,) = server.join_match(match, [(ball.account_lo, ball.name, 0, 0, False)])
        fresh_link, fresh_ball = self.run.connect(fresh)
        self.run.message(fresh_link, 21616, pick(fresh_ball.entity, quad_cannons.HERO))
        fresh_script = fresh_ball.body_script
        fresh_script.ult_seconds = 0
        fresh_script.set_ult_charge(0)
        self.run.until(lambda: fresh_script.stream.base is not None)
        self.assertNotEqual(fresh_ball.body, ball.body)
        self.assertNotEqual(fresh.conn, handoff.conn)
        fresh_weapon = next(i for i in fresh_script.component.instances.values()
                            if i.graph.index == quad_cannons.GRAPH)
        before = self.motion(fresh_ball)
        self.run.send(link, payload=input_window(fresh_script.frame + 1, 3, buttons=FIRE))
        self.run.send(link, FIN)
        self.assertEqual(self.motion(fresh_ball), before)
        self.assertEqual(fresh_weapon.vars[53].value(), 80)
        self.assertEqual(fresh_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(pool.total, health)

        x, y, z = fresh_ball.mover.position
        hog.mover.position[:] = (x, y, z + 4)
        hog.mover.body = None
        for _ in range(45):
            self.run.command(fresh_link)
            self.run.step()
        self.run.command(fresh_link, buttons=FIRE)
        self.assertEqual(fresh_weapon.vars[53].value(), 79)
        self.assertLess(pool.total, health)
        self.assertAlmostEqual(
            fresh_script.component.entity_var(ULT_CHARGE).value(), (health - pool.total) * .75,
        )

    def test_grappling_claw_disconnect_retires_flight_or_attachment_and_rejoins_fresh(self):
        for attached in (False, True):
            with self.subTest(attached=attached):
                self.check_grappling_claw_rejoin(attached)

    def check_grappling_claw_rejoin(self, attached):
        from test_game_mover import box

        from ow174.game import ball_grapple, stats
        from ow174.game.collision import World
        from ow174.game.commands import SECONDARY
        from ow174.game.mover import FELL

        server = self.run.server
        old, rival = server.create_match(KANEZAKA_DM, [(1, "Ball", 0, 0, False), (2, "Hog", 0, 1, False)])
        link, ball = self.run.connect(old)
        observer, hog = self.run.connect(rival)
        self.run.message(link, 21616, pick(ball.entity, ball_grapple.HERO))
        self.run.message(observer, 21616, pick(hog.entity, ROADHOG))
        match = ball.match
        fight = match.combat
        script = ball.body_script
        old_body = ball.body
        self.run.until(lambda: script.stream.base is not None and old_body in observer.models)
        x, y, z = ball.mover.position
        match.collision_world = World(
            box(x - 90, x + 90, y - 1, y, z - 90, z + 90)
            + box(x - 30, x + 30, y - 1, y + 45, z + 12, z + 12.25)
        )
        hog.mover.position[:] = (x + 25, y, z)
        hog.mover.body = None
        pool = fight.vitals_of(next(target for target in fight._targets() if target.owner is hog))

        def prepare(owner, owner_link):
            for _ in range(320):
                self.run.step()
                self.run.command(owner_link)
            owner.mover.position[:] = (x, y + 8, z)
            owner.mover.body = None
            owner.mover.flags_word |= FELL
            for _ in range(2):
                self.run.step()
                self.run.command(owner_link)
            self.run.command(owner_link, buttons=SECONDARY)
            self.assertIn(owner.body, fight.ball_grapples, 'encrypted secondary did not launch the claw')
            grapple = fight.ball_grapples[owner.body]
            self.assertFalse(grapple.attached)
            return grapple

        def finish_attach(owner, owner_link, grapple):
            for _ in range(12):
                self.run.step()
                self.run.command(owner_link, buttons=SECONDARY)
                if grapple.attached:
                    break
            self.assertTrue(grapple.attached)
            controller = ball_grapple.instance(owner, ball_grapple.GRAPPLE)
            self.assertTrue(controller.vars[435].value())
            return controller

        grapple = prepare(ball, link)
        if attached:
            controller = finish_attach(ball, link, grapple)
            self.run.until(lambda: observer.models[old_body].vars.get((controller.id, 435)) is True)
        self.assertEqual(pool.total, 600)
        self.assertIn(old_body, fight.grapple_seen)
        dropped = []
        apply = observer.apply

        def lose_first_destroy(packet):
            if attached and not dropped and packet.kind == KIND_FRAME:
                _, records = wire_frame(packet)
                if any(record.op == world.OP_DESTROY and record.entity == old_body for record, _ in records):
                    dropped.append(packet.seq)
                    return
            apply(packet)

        with patch.object(observer, "apply", side_effect=lose_first_destroy):
            self.run.send(link, FIN)
            self.assertTrue(ball.gone and script.retired)
            self.assertNotIn(old.conn, server.clients)
            self.assertNotIn(old_body, fight.ball_grapples)
            self.assertNotIn(old_body, fight.grapple_seen)
            self.run.until(lambda: old_body not in observer.models and old_body not in observer.fields)
        self.assertEqual(len(dropped), int(attached))
        delivered = {seq for seq, record in observer.records
                     if record.op == world.OP_DESTROY and record.entity == old_body}
        self.assertTrue(delivered)
        self.assertTrue(delivered.isdisjoint(dropped))

        (fresh,) = server.join_match(match, [(ball.account_lo, ball.name, 0, 0, False)])
        new_link, new_ball = self.run.connect(fresh)
        self.run.message(new_link, 21616, pick(new_ball.entity, ball_grapple.HERO))
        new_script = new_ball.body_script
        self.run.until(lambda: new_script.stream.base is not None and new_ball.body in observer.models)
        self.assertNotEqual(new_ball.body, old_body)
        self.assertNotEqual(new_link.conn, old.conn)
        before = self.motion(new_ball)
        self.run.send(link, payload=input_window(new_script.frame + 1, 6, buttons=SECONDARY))
        self.run.send(link, FIN)
        self.assertEqual(self.motion(new_ball), before)
        self.assertFalse(fight.ball_grapples)
        self.assertEqual(pool.total, 600)

        fresh_grapple = prepare(new_ball, new_link)
        hog.mover.position[:] = (x, y, z + 6)
        hog.mover.body = None
        controller = finish_attach(new_ball, new_link, fresh_grapple)
        attachment = (controller.id, 435)
        self.run.until(lambda: observer.models[new_ball.body].vars.get(attachment) is True)
        for _ in range(100):
            self.run.step()
            self.run.command(new_link, buttons=SECONDARY)
            if pool.total < 600:
                break
        self.assertEqual(pool.total, 550)
        self.assertEqual(new_ball.stats.value(stats.HERO_DAMAGE), 50)
        self.run.command(new_link)
        self.assertNotIn(new_ball.body, fight.ball_grapples)
        self.assertFalse(controller.vars[435].value())
        self.assertGreater(controller.states[25].remaining(), 0)
        self.run.until(lambda: observer.models[new_ball.body].vars.get(attachment) is False)
        for _ in range(24):
            self.run.step()
            self.run.command(new_link, buttons=SECONDARY)
        self.assertNotIn(new_ball.body, fight.ball_grapples)
        self.assertGreater(controller.states[25].remaining(), 0)
        self.assertEqual(pool.total, 550)
        self.assertNotIn(old_body, observer.models)

    def test_minefield_disconnect_retires_actor_streams_and_rejoin_casts_fresh(self):
        if not graph.GRAPHS_PATH.is_file() or graph.entity_body(ball_mines.BODY) is None:
            self.skipTest("prepared 2578/151A Minefield graph bundle required")
        for armed in (False, True):
            with self.subTest(armed=armed):
                self.check_minefield_rejoin(armed)

    def check_minefield_rejoin(self, armed):
        server = self.run.server
        old, rival = server.create_match(KANEZAKA_DM, [(1, "Ball", 0, 0, False), (2, "Hog", 0, 1, False)])
        link, ball = self.run.connect(old)
        hog_link, hog = self.run.connect(rival)
        hog_link.actor_definitions.add(ball_mines.BODY)
        self.run.message(link, 21616, pick(ball.entity, ball_mines.HERO))
        self.run.message(hog_link, 21616, pick(hog.entity, ROADHOG))
        match = ball.match
        field = match.combat.mines
        script = ball.body_script
        script.ult_seconds = 0
        self.run.until(lambda: script.stream.base is not None and hog.body_script.stream.base is not None)
        x, y, z = ball.mover.position
        hog.mover.position[:] = (x + 35, y, z)
        hog.mover.body = None
        pool = match.combat.vitals_of(next(t for t in match.combat._targets() if t.owner is hog))

        def commands(owner_link, count, *, ack_except=()):
            for _ in range(count):
                self.run.step(ack_except=ack_except)
                self.run.command(owner_link)

        def cast(owner_link, owner_script, *, ack_except=()):
            owner_script.set_ult_charge(owner_script.component.entity_var(ULT_COST).value())
            self.run.command(owner_link, buttons=32)
            commands(owner_link, 24, ack_except=ack_except)
            self.assertEqual(owner_script.component.entity_var(ULT_CHARGE).value(), 0)
            self.assertEqual(len(field.entities), 15)

        # One case leaves the observer's create packets unacknowledged on FIN.
        commands(link, 45)
        cast(link, script, ack_except=() if armed else (hog_link,))
        ids = set(field.entities)
        self.assertTrue(all(mine.source == ball.body for mine in field.entities.values()))
        self.assertTrue(all(not mine.landed for mine in field.entities.values()))
        if armed:
            first = next(iter(field.entities.values()))
            apply = hog_link.apply
            lost = []

            def drop_first_landing(packet):
                if not lost and packet.kind == KIND_FRAME:
                    _, records = wire_frame(packet)
                    for record, _ in records:
                        if (record.entity == first.entity and record.op == world.OP_UPDATE
                                and record.movement is not None):
                            lost.append(packet.seq)
                            return
                apply(packet)

            with patch.object(hog_link, "apply", side_effect=drop_first_landing):
                self.run.until(lambda: all(mine.armed for mine in field.entities.values()), limit=180)
            self.assertEqual(len(lost), 1)
            landed = [(seq, record.movement) for seq, record in hog_link.records
                      if record.entity == first.entity and record.op == world.OP_UPDATE
                      and record.movement is not None]
            self.assertTrue(landed)
            seq, movement = landed[-1]
            self.assertNotEqual(seq, lost[0])
            self.assertEqual(movement["velocity"], (0, 0, 0))
            for sent, actual in zip(movement["position"], first.position, strict=True):
                self.assertAlmostEqual(sent, actual, delta=1 / 1024)
            flying = [seq for seq, record in hog_link.records
                      if record.entity == first.entity and not record.op and record.movement is not None]
            self.assertTrue(flying)
            self.assertEqual(len(set(flying)), len(flying))
            self.assertTrue(all(hog_link.models[entity].applied > 0 for entity in ids))
            self.assertTrue(all(0x151A in hog_link.models[entity].graphs.values() for entity in ids))
        else:
            self.assertFalse(any(hog.client.bot_create_acked(entity) for entity in ids))
        self.assertEqual(pool.total, 600)
        self.assertEqual({record.entity for _, record in hog_link.records
                          if record.op == world.OP_CREATE and record.definition == ball_mines.BODY}, ids)

        old_body = ball.body
        self.run.send(link, FIN)
        self.assertTrue(ball.gone and script.retired)
        self.assertNotIn(old.conn, server.clients)
        self.assertFalse(field.entities)
        self.assertFalse(field.seen)
        self.run.until(lambda: ids.isdisjoint(hog_link.models) and ids.isdisjoint(hog_link.fields))
        self.assertEqual({record.entity for _, record in hog_link.records
                          if record.entity in ids and record.op == world.OP_DESTROY}, ids)
        after_destroy = len(hog_link.records)
        self.assertTrue(all(hog.client.bot_stream(entity) is None for entity in ids))

        (fresh,) = server.join_match(match, [(ball.account_lo, ball.name, 0, 0, False)])
        fresh_link, fresh_ball = self.run.connect(fresh)
        self.run.message(fresh_link, 21616, pick(fresh_ball.entity, ball_mines.HERO))
        fresh_script = fresh_ball.body_script
        fresh_script.ult_seconds = 0
        self.run.until(lambda: fresh_script.stream.base is not None)
        self.assertNotEqual(fresh_ball.body, old_body)
        self.assertNotEqual(fresh.conn, old.conn)
        fresh_script.set_ult_charge(0)
        before_motion = self.motion(fresh_ball)
        self.run.send(link, payload=input_window(fresh_script.frame + 1, 2, buttons=32))
        self.run.send(link, FIN)
        self.assertEqual(self.motion(fresh_ball), before_motion)
        self.run.command(fresh_link, buttons=32)
        commands(fresh_link, 45)
        self.assertFalse(field.entities)
        self.assertEqual(pool.total, 600)
        x, y, z = fresh_ball.mover.position
        fresh_ball.mover.position[:] = (x + 70, y, z)
        fresh_ball.mover.body = None
        cast(fresh_link, fresh_script)
        self.assertTrue(ids.isdisjoint(field.entities))
        self.assertTrue(all(mine.source == fresh_ball.body for mine in field.entities.values()))
        self.run.until(lambda: all(mine.armed for mine in field.entities.values()), limit=180)
        first = next(iter(field.entities.values()))
        if armed:
            # Rejoin as a spectator of the existing mine lifetime. The old
            # Roadhog connection must release its references without retiring Ball's mines.
            self.assertIs(first.viewers[hog.slot], hog.client)
            self.run.send(hog_link, FIN)
            self.assertNotIn(hog.slot, first.viewers)
            self.assertEqual(len(field.entities), 15)
            self.assertFalse(any(record.entity in ids for _, record in hog_link.records[after_destroy:]))
            (new_hog,) = server.join_match(match, [(hog.account_lo, hog.name, 0, 1, False)])
            hog_link, hog = self.run.connect(new_hog)
            hog_link.actor_definitions.add(ball_mines.BODY)
            self.run.message(hog_link, 21616, pick(hog.entity, ROADHOG))
            self.run.until(lambda: all(
                entity in hog_link.models and hog_link.models[entity].applied > 0 for entity in field.entities
            ))
            self.assertIs(first.viewers[hog.slot], hog.client)
            self.assertEqual(set(field.entities) & hog_link.models.keys(), set(field.entities))
            self.assertFalse(any(record.entity in ids for _, record in hog_link.records))
            pool = match.combat.vitals_of(next(t for t in match.combat._targets() if t.owner is hog))
        else:
            self.assertFalse(any(record.entity in ids for _, record in hog_link.records[after_destroy:]))
        self.assertEqual(pool.total, 600)
        hog.mover.position[:] = first.position
        hog.mover.body = None
        self.run.step()
        self.assertTrue(first.triggered)
        self.assertEqual(pool.total, 470)

    def test_unsafe_first_command_preserves_state_and_blocks_later_valid_input(self):
        _, link, player = self.soldier(LAST_VALID - 3)
        self.run.send(link, payload=input_window(LAST_VALID - 2, 1, forward=127))
        script = player.body_script
        before = self.motion(player), script.snapshot()
        self.run.send(link, payload=input_window(FIRST_UNSAFE, 2, buttons=FIRE, forward=127))
        self.assertIsNotNone(player.client.leaving_since)
        self.assertIs(player.body_script, script)
        self.assertEqual((self.motion(player), script.snapshot()), before)
        self.run.send(link, payload=input_window(LAST_VALID - 1, 1, buttons=FIRE))
        self.assertEqual((self.motion(player), script.snapshot()), before)

    def test_first_stale_high_and_half_range_frames_use_existing_body_anchor(self):
        _, link, player = self.soldier()
        self.assertIsNone(player.commands.last_frame)
        anchor = player.body_script.frame
        before = self.motion(player)
        self.run.send(link, payload=input_window(FIRST_UNSAFE, 2, buttons=FIRE))
        self.run.send(link, payload=input_window(anchor + FRAME_HALF, 1, buttons=FIRE))
        self.assertEqual(self.motion(player), before)
        self.assertEqual(self.ammo(player), 30)
        self.assertIsNone(player.client.leaving_since)
        self.run.command(link, buttons=FIRE)
        self.assertEqual(player.commands.last_frame, anchor + 1)
        self.assertEqual(self.ammo(player), 29)
