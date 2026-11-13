"""Explicit source filters precede publication; ordinary NPCs retain team mapping."""
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from test_mei_ice_presentation import decode_ice_components

from ow174.game import npc
from ow174.game.match import TEAM_BIT
from ow174.game.script import graph


class OwnedNpcFilterCaptureTests(unittest.TestCase):
    def test_explicit_filter_precedes_vm_and_survives_current_late_and_retry_creates(self):
        run = Run(KANEZAKA_DM, ('Owner', 'Enemy'))
        match, owner = run.match, run.players[0]
        template = match.bots[0] if match.bots else None
        if template is None:
            # Original manual Trooper definition; its separate AI is not enabled.
            definition = npc.Definition(npc.TROOPER_HERO, npc.TROOPER_BODY, 'Owned NPC', 200)
        else:
            definition = npc.Definition(template.hero, template.body, 'Owned NPC', template.health)
        captured = owner.team_bit
        seen = []
        prepare = match._prepare_bot_script

        def observe(bot, **kwargs):
            seen.append(bot.spawner.get('source_filter_bits'))
            return prepare(bot, **kwargs)

        # Missing graph is explicit here; actual authored Portal/Sentry encrypted
        # commands and VM source preservation are covered by the external replay.
        with patch.object(graph, 'entity_body', return_value=None), patch.object(
                match, '_prepare_bot_script', side_effect=observe):
            bot = npc.spawn(match, definition, owner.mover.position, team=owner.team,
                            source_filter_bits=captured)
        self.assertEqual(seen, [captured])
        current = [record for record in owner.client.entities if record.entity == bot.entity]
        self.assertEqual(len(current), 1)
        queued = current[0]
        owner.team = bot.team = 1
        for record in (queued, match._bot_create_update(bot, run.players[1]), queued):
            self.assertEqual(decode_ice_components(record.build(0), create=True)[26][0], captured)
        self.assertEqual(bot.spawner['source_filter_bits'], captured)

    def test_ordinary_npc_fallback_and_invalid_capture_do_not_allocate(self):
        run = Run()
        match = run.match
        template = match.bots[0]
        definition = npc.Definition(template.hero, template.body, 'Ordinary NPC', template.health)
        with patch.object(graph, 'entity_body', return_value=None):
            ordinary = npc.spawn(match, definition, run.players[0].mover.position, team=1)
        self.assertNotIn('source_filter_bits', ordinary.spawner)
        self.assertEqual(decode_ice_components(
            match._bot_create_update(ordinary).build(0), create=True)[26][0], TEAM_BIT << 1)
        before = (match.combat.next_bot, tuple(match.bots))
        for value in (True, -1, 1 << 64, 1.0, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                npc.spawn(match, definition, run.players[0].mover.position, team=1,
                          source_filter_bits=value)
            self.assertEqual((match.combat.next_bot, tuple(match.bots)), before)
