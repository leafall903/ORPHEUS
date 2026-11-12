"""The bot only talks when the player turns "Bot talks" on in the dashboard."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ow174.accounts.profile import Profile
from ow174.jam.groups import CHAT_IN, FRIENDS
from ow174.lobby.handlers import chat


def make_session(bot_chat):
    player = SimpleNamespace(name="Alpha", account_lo=1, account={"+0x0": 1}, virtual=False)
    bot = SimpleNamespace(name="Bot", account_lo=2, account={"+0x0": 2}, virtual=True)
    player_session = MagicMock()
    social = MagicMock()
    social.accounts.bot = bot
    social.accounts.by_id.side_effect = {1: player, 2: bot}.get
    social.channel_members.return_value = [player, bot]
    social.chat_message.side_effect = lambda channel, sender, text, *rest: {"from": sender.name, "text": text}
    server = MagicMock(social=social)
    server.session_of.side_effect = lambda account_lo: player_session if account_lo == 1 else None
    session = MagicMock(server=server, account=player, profile=Profile(bot_chat=bot_chat))
    return session, player_session


class BotChatTests(unittest.TestCase):
    def test_window_hotkeys_use_the_owning_local_match_session(self):
        session, _ = make_session(bot_chat=False)
        server = session.server
        other = MagicMock(local=True, logged_in=True, npc_menu_open=True,
                          account=SimpleNamespace(account_lo=3))
        other.sock.getpeername.return_value = ("127.0.0.1", 61000)
        session.sock.getpeername.return_value = ("127.0.0.1", 61001)
        for candidate in (session, other):
            candidate.sock.getsockname.return_value = ("127.0.0.1", 3724)
        session.local = session.logged_in = True
        session.npc_menu_open = False
        server.social.sessions = {3: other, 1: session}
        server.game.npc_key.side_effect = [
            {"handled": True, "action": "menu", "menu_open": True, "choices": []},
            {"handled": True, "action": "spawn", "name": "Trooper"},
            {"handled": True, "action": "close_menu", "menu_open": False},
            {"handled": True, "action": "start", "entity": 0xC0001000},
            {"handled": True, "action": "stop", "count": 1},
            {"handled": True, "action": "remove", "entities": (0xC0001000,)},
        ]
        owners = {(61000, 3724): 10, (61001, 3724): 11}
        with patch.object(chat, "configured_catalog", return_value={}):
            for key in ("1", "=", "1", "Escape", "1", "0", "{", "-"):
                chat.npc_hotkey(server, 11, key, lambda *ports: owners.get(ports))
        self.assertEqual([call.args[:2] for call in server.game.npc_key.call_args_list],
                         [(1, "="), (1, "1"), (1, "Escape"), (1, "0"), (1, "{"), (1, "-")])
        self.assertFalse(server.game.npc_key.call_args_list[3].kwargs["menu_open"])
        self.assertEqual(session.send.call_count, 6)
        self.assertEqual(session.send.call_args.args[2]["text"], "Removed 1 NPCs.")
        self.assertIn("{ stops ([ without Shift also stops)", session.send.call_args_list[0].args[2]["text"])
        other.send.assert_not_called()
        self.assertFalse(session.npc_menu_open)

    def test_npc_commands_keep_menu_state_per_session_and_reply_privately(self):
        session, recipient = make_session(bot_chat=True)
        session.local = True
        session.npc_menu_open = False
        game = session.server.game
        game.npc_key.side_effect = [
            {"handled": True, "action": "menu", "menu_open": True,
             "choices": [{"key": "1", "name": "Trooper"}]},
            {"handled": True, "action": "spawn", "name": "Trooper"},
            {"handled": True, "action": "close_menu", "menu_open": False},
        ]
        with patch.object(chat, "configured_catalog", return_value={}) as catalog:
            for line in (".npc", "1", "Escape"):
                chat.chat(session, {"+0x90": line})
            self.assertEqual(catalog.call_count, 3)
        self.assertEqual([call.kwargs["menu_open"] for call in game.npc_key.call_args_list],
                         [False, True, True])
        self.assertFalse(session.npc_menu_open)
        self.assertIn("1 Trooper", session.send.call_args_list[0].args[2]["text"])
        self.assertIn("{ stops", session.send.call_args_list[0].args[2]["text"])
        self.assertEqual(session.send.call_args_list[1].args[2]["text"], "Spawned Trooper.")
        recipient.send.assert_not_called()
        chat.chat(session, {"+0x90": "1"})
        self.assertEqual(recipient.send.call_args.args[2]["text"], "Alpha, I hear you: 1")
        session.local = False
        chat.chat(session, {"+0x90": ".npc 0"})
        self.assertEqual(game.npc_key.call_count, 3)

    def channel_texts(self, bot_chat):
        session, player_session = make_session(bot_chat)
        chat.chat(session, {"+0x78": {"+0x10": 5}, "+0x90": "hi"})
        return [
            call.args[2]["text"] for call in player_session.send.call_args_list if call.args[0] == CHAT_IN
        ]

    def whisper_replies(self, bot_chat):
        session, _ = make_session(bot_chat)
        chat.whisper(session, {"+0x78": 9, "+0x80": {"+0x0": 2}, "+0xA0": "hi"})
        return [call for call in session.send.call_args_list if call.args[:2] == (FRIENDS, 27117)]

    def test_the_bot_is_quiet_by_default(self):
        self.assertFalse(Profile().bot_chat)
        self.assertEqual(self.channel_texts(bot_chat=False), ["hi"])
        self.assertEqual(self.whisper_replies(bot_chat=False), [])

    def test_the_bot_answers_when_the_switch_is_on(self):
        texts = self.channel_texts(bot_chat=True)
        self.assertEqual(texts[0], "hi")
        self.assertIn("I hear you", texts[1])
        self.assertEqual(len(self.whisper_replies(bot_chat=True)), 1)


if __name__ == "__main__":
    unittest.main()
