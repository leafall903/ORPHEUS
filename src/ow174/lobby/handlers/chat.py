"""Chat channels and whispers, and the commands a player types in any chat while in a match:
.hero <name> plays another hero, .leave goes back to the menu, and .npc opens the sandbox console."""

from ow174.accounts.registry import Account
from ow174.game.content import find_hero
from ow174.game.npc_console import configured_catalog
from ow174.jam.groups import CHAT_IN, CHAT_OUT, FRIENDS, FRIENDS_OUT
from ow174.lobby.router import Router
from ow174.lobby.session import Session

routes = Router()

BOT_REPLY = "{name}, I hear you: {text}"
NPC_CONSOLE_KEYS = frozenset(('=', '-', '0', '{', 'Escape', *'123456789'))


@routes.on(CHAT_OUT, 21700)
def chat(session: Session, value: dict) -> None:
    social = session.server.social
    channel = value.get("+0x78") or {}
    text = value.get("+0x90") or ""
    if text.startswith(".") and _match_command(session, channel, text):
        return
    if session.local and session.npc_menu_open is True and text in NPC_CONSOLE_KEYS:
        game = session.server.game
        if game is not None and game.in_match(session.account.account_lo):
            _private_reply(session, channel, text, _npc_command(session, game, text))
            return
        session.npc_menu_open = False
    message = social.chat_message(channel, session.account, text, value.get("+0xB8", 0))
    members = social.channel_members(channel)
    _deliver(session.server, members, message)
    session.log(f"[chat #{channel.get('+0x10')}] {session.account.name}: {text}")
    bot = social.accounts.bot
    if bot in members and session.profile.bot_chat:
        reply_text = BOT_REPLY.format(name=session.account.name, text=text)
        _deliver(session.server, members, social.chat_message(channel, bot, reply_text))


def _match_command(session: Session, channel: dict, text: str) -> bool:
    """Run a match command. False when it is not one, so the text goes out as chat."""
    game = session.server.game
    command, _, rest = text[1:].partition(" ")
    command = command.lower()
    if command not in ("hero", "leave", "npc") or game is None:
        return False
    if command == "hero":
        hero = find_hero(rest.strip())
        if hero is None:
            reply = f"No hero called '{rest.strip()}'."
        elif game.switch_hero(session.account.account_lo, hero):
            reply = f"You play {hero.name} now."
        else:
            reply = "You are not in a match."
    elif command == "leave":
        reply = (
            "Back to the menu." if game.send_home(session.account.account_lo) else "You are not in a match."
        )
    else:
        reply = _npc_command(session, game, rest.strip() or "=")
    _private_reply(session, channel, text, reply)
    return True


def _private_reply(session, channel, text, reply):
    session.log(f"[chat] {text} -> {reply}")
    bot = session.server.social.accounts.bot
    session.send(CHAT_IN, 20400, session.server.social.chat_message(channel, bot, reply))


def npc_hotkey(server, pid: int, key: str, connection_owner) -> None:
    """Route a foreground key to the local match session owned by that client."""
    if key not in NPC_CONSOLE_KEYS or server.game is None:
        return
    with server.state_lock:
        for session in server.social.sessions.values():
            if not session.local or not session.logged_in or session.account is None:
                continue
            if key not in ("=", "0", "{", "-") and not session.npc_menu_open:
                continue
            account = session.account
            channel = server.social.match_chat_of(account)
            if channel is None or not server.game.in_match(account.account_lo):
                continue
            try:
                peer_port = session.sock.getpeername()[1]
                local_port = session.sock.getsockname()[1]
            except OSError:
                continue
            if connection_owner(peer_port, local_port) == pid:
                reply = _npc_command(session, server.game, key, hotkey=True)
                _private_reply(session, channel, key, reply)
                return


def _npc_command(session, game, key: str, *, hotkey=False) -> str:
    if not session.local:
        return "The NPC console is only available from the local client."
    try:
        result = game.npc_key(session.account.account_lo, key, configured_catalog(),
                              menu_open=session.npc_menu_open)
    except ValueError as error:
        return str(error)
    if not result["handled"]:
        return "Open the spawn menu with .npc = before selecting an enemy."
    action = result["action"]
    if action in ("menu", "close_menu"):
        session.npc_menu_open = result["menu_open"]
    if action == "menu":
        choices = ", ".join(f'{entry["key"]} {entry["name"]}' for entry in result["choices"])
        instructions = "Press a key." if hotkey else "Submit a key in chat."
        stop_hint = "{ stops ([ without Shift also stops)" if hotkey else "{ stops"
        return f"NPC spawn menu: {choices}. {instructions} 0 attacks, {stop_hint}, - removes, Escape closes."
    if action == "spawn":
        return f'Spawned {result["name"]}.'
    if action == "start":
        return "Targeted NPC attacks."
    if action == "stop":
        return f'Stopped {result["count"]} NPCs.'
    if action == "remove":
        return f'Removed {len(result["entities"])} NPCs.'
    return "NPC spawn menu closed."


def _deliver(server, members: list[Account], message: dict) -> None:
    for account in members:
        recipient = server.session_of(account.account_lo)
        if recipient:
            recipient.send(CHAT_IN, 20400, message)


@routes.on(CHAT_OUT, 21701)
def channel_members(session: Session, value: dict) -> None:
    session.send(CHAT_IN, 20401, session.server.social.who(value.get("+0x78") or {}))


@routes.on(FRIENDS_OUT, 27004)
def whisper(session: Session, value: dict) -> None:
    """A Battle.net whisper. 27116 completes the request, and the target gets 27117 {sender, text}.

    The sender's client shows its own line itself, so it gets no copy.
    """
    text = value.get("+0xA0") or ""
    session.send(FRIENDS, 27116, {"+0x78": value.get("+0x78", 0), "+0x80": 0})
    target = _whisper_target(session, value)
    if target is None:
        session.log(f"[<<<] Whisper to unknown player {value}")
        return
    session.log(f"[whisper] {session.account.name} -> {target.name}: {text}")
    if target.virtual:
        if session.profile.bot_chat:
            reply = BOT_REPLY.format(name=session.account.name, text=text)
            session.send(FRIENDS, 27117, {"+0x78": target.account, "+0x88": reply})
        return
    recipient = session.server.session_of(target.account_lo)
    if recipient:
        recipient.send(FRIENDS, 27117, {"+0x78": session.account.account, "+0x88": text})


def _whisper_target(session: Session, value: dict) -> Account | None:
    """The first known player in the whisper who is not the sender. Either field may hold the sender."""
    accounts = session.server.social.accounts
    for key in ("+0x80", "+0x90"):
        player = value.get(key) or {}
        account = accounts.by_id(player.get("+0x0", 0))
        if account and account.account_lo != session.account.account_lo:
            return account
    return None
