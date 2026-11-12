"""D.Va's two bodies: the mech she enters play in and the pilot who leaves it (docs/research/dva-forms.md).

The game data has one hero body, the pilot (040A). Her graph 026A makes the mech (0407) as an entity she
owns and boards it, and the mech's graphs tell her when to leave. This server plays one body for a player,
so a change of form is a change of body, as a hero pick is: the same hero under a new entity, where the
old one stood. The graphs still decide when: the mech's eject message, the pilot's Call Mech and her
boarding state, the fuse of Self-Destruct and the node that destroys the mech.
"""

import logging
import math
from dataclasses import dataclass, field

from ow174.game.content import heroes, spawn_form
from ow174.game.script import expr
from ow174.game.script.expr import Entity

log = logging.getLogger(__name__)

HERO = 0x02E000000000007A
PILOT, MECH = 0x040000000000040A, 0x0400000000000407
BODIES = (PILOT, MECH)
PILOT_GRAPH, SELF_DESTRUCT = 0x026A, 0x0B2F
EJECT = 0x024000000000016C  # the mech to its owner's hero: leave me (0276 node 5, 0B2F node 17)
MADE_MECH, CALLED_MECH = 7, 15  # 026A's two STU_B6E3C77D: when the pilot spawns, and Call Mech's
SELF_DESTRUCTING, DISTANCE, CALLED = 842, 680, 38  # #842e its Stack holds on; node 24's input; her mech
BLACKOUT = 6828  # the pilot's screen is black while it is on: her boarding, until she is stowed
# The searches that hit what they find, by body and graph: the search's node, the node and field that
# hold the amount, and the height of the search's point above the mech's root (its m_05BE62DD).
# Self-Destruct's is the search when the fuse ends, and node 24 gives the damage for a distance.
# Call Mech's is the search 0.7 s after the mech is made, around it.
BLASTS = {
    (MECH, SELF_DESTRUCT): (8, 24, "m_value", 1.3),
    (PILOT, PILOT_GRAPH): (35, 36, "m_modifyHealth.m_amount", 0.5),
}


@dataclass
class Loose:
    """A mech nobody plays: the one she left to blow up, or the one she called and has not boarded."""

    entity: int
    owner: object  # the match.Player who made it
    position: tuple
    yaw: int
    script: object = None  # the left mech's BodyScript: its graphs run on, its frames are no longer sent
    hit: set = field(default_factory=set)  # the bodies its search has hit: a search runs many times


class Forms:
    """The form changes of one match: what the graphs asked for in a tick, done between the scripts' runs."""

    def __init__(self, match) -> None:
        self.match = match
        self.loose: dict[int, Loose] = {}
        self.pending: list[tuple] = []

    def update(self) -> None:
        pending, self.pending = self.pending, []
        for kind, player, *rest in pending:
            if player.gone or not player.has_body:
                continue
            if kind == "eject":
                self.eject(player)
            elif kind == "board":
                self._board(player, *rest)
            else:
                self._deliver(player, *rest)
        for loose in list(self.loose.values()):
            self._run(loose)

    def wrecked(self, owner) -> bool:
        """A hit emptied a body's health: whether it is her mech, which she leaves alive."""
        if getattr(owner, "has_body", False) and owner.hero.guid == HERO and owner.hero.body == MECH:
            if ("eject", owner) not in self.pending:
                self.pending.append(("eject", owner))
            return True
        return False

    def eject(self, player) -> bool:
        """The player leaves her mech for the pilot where it stands. A mech that is self-destructing
        stays, and its graphs run on to the blast; any other goes at once."""
        match = self.match
        if player.hero.body != MECH:
            return False
        mech, script, mover = player.body, player.body_script, player.mover
        held = script.component.entity_var(SELF_DESTRUCTING, create=False) if script is not None else None
        bomb = held is not None and bool(held.value())
        at = (tuple(mover.position), mover.yaw)
        if bomb:
            match.detach_body_script(player)
            self.loose[mech] = Loose(mech, player, *at, script)
        if not match.switch_hero(player, heroes()[HERO], at=at, keep=bomb):
            if bomb:  # no free body id: she stays in it
                del self.loose[mech]
                player.body_script = script
            return False
        log.info("[game] %s: %s ejected at (%.1f, %.1f, %.1f)%s", match.label(), player.name, *at[0],
                 ", the mech self-destructs" * bomb)
        return True

    def call_mech(self, player, position: tuple) -> int | None:
        """Call Mech's mech, standing at its landing point until she boards it."""
        match = self.match
        entity = match._next_body_id(player)
        if entity is None:
            return None
        loose = self.loose[entity] = Loose(entity, player, position, player.mover.yaw)
        match.show_loose(loose, spawn_form(heroes()[HERO]))
        log.info("[game] %s: %s calls her mech to (%.1f, %.1f, %.1f)", match.label(), player.name, *position)
        return entity

    def destroy(self, entity: int) -> bool:
        loose = self.loose.pop(entity, None)
        if loose is None:
            return False
        if loose.script is not None:
            loose.script.retire()
        self.match.destroy_body(loose.owner, entity)
        return True

    def _board(self, player, entity: int) -> None:
        loose = self.loose.get(entity)
        if loose is None or loose.owner is not player or player.hero.body != PILOT:
            return
        # The called mech is a body without movement state, as a bot's. Her own needs a create with
        # it, so the one she plays is a new entity on the same spot. Its id is taken while the called
        # one still holds its own.
        if self.match.switch_hero(player, spawn_form(heroes()[HERO]), at=(loose.position, player.mover.yaw)):
            log.info("[game] %s: %s is in her mech again", self.match.label(), player.name)
        self.destroy(entity)

    def _deliver(self, player, sender: int, message: int, params: dict) -> None:
        from ow174.game.script import runtime

        script = player.body_script
        if script is None or script.retired or script.entity == sender or script.component.fault is not None:
            return
        try:
            if runtime.send_message(script.component, message, params, sender):
                script.component.run_now()
        except Exception:
            log.exception("[game] %s: %s's body message handling failed", self.match.label(), player.name)
            self.match.stop_body_script(player, failed=True)

    def _run(self, loose: Loose) -> None:
        """A left mech's graphs, on its owner's command clock."""
        script, owner = loose.script, loose.owner
        stale = script is None and (owner.hero.body != PILOT or self.match.combat.is_dead(owner))
        if owner.gone or stale or (script is not None and script.component.fault is not None):
            self.destroy(loose.entity)  # its owner left, or will not board it: she died, or is in one
        elif script is not None:
            try:
                script.command(self.match._newest_frame(owner), 0)
            except Exception:
                log.exception("[game] %s: %s's left mech failed", self.match.label(), owner.name)
                self.destroy(loose.entity)


def respawn_form(hero):
    """The body a dead hero comes back in: a dead pilot returns in her mech."""
    return spawn_form(heroes()[HERO]) if hero.guid == HERO else hero


def on_foot(script) -> None:
    """A pilot's new body script. Her graph's Entry turns the boarding blackout on, and in the data
    her being stowed in the mech she boards at once turns it off again (026A node 115). This server's
    pilot is on foot from the start, so it is off from the start."""
    if script.world.body_definition == PILOT:
        for instance in script.component.instances.values():
            if instance.graph.index == PILOT_GRAPH:
                instance.write((expr.INSTANCE, BLACKOUT), False)


def _player(match, body: int):
    return next((player for player in match.players
                 if player.has_body and not player.gone and player.body == body), None)


def native(world, cfg: dict, instance):
    """The config vars D.Va's graphs find one another's entities with."""
    match = world.match
    if match is None or instance is None or world.body_definition not in BODIES:
        return NotImplemented
    cls = cfg.get("$")
    if cls in ("STU_F7262B36", "STU_F76C486C"):
        # Component 43 names the player who owns an entity (CDBDE0), component 72 the one it was made
        # for (CE2CF0). A mech she left has no owner, and still knows whose it was.
        entity = cfg.get("m_801B298F" if cls == "STU_F7262B36" else "m_D63CBEC2")
        if instance.evaluate(entity) != Entity(world.body):
            return NotImplemented
        owner = _player(match, world.body)
        loose = match.forms.loose.get(world.body)
        if owner is None and loose is not None and cls == "STU_F76C486C":
            owner = loose.owner
        return Entity(owner.entity if owner is not None else 0)
    if cls == "STU_0AC15F78":
        # Component 44 of a player: the hero he plays (CEDBD0), in the data always the pilot. Here she
        # is not inside her mech, so the player himself stands for her: BodyWorld.send_message hands
        # what the graphs send there to the body he plays now.
        target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        return Entity(target if any(player.entity == target for player in match.players) else 0)
    if cls == "STU_71A87E92":  # the entity's translation (CCC750): a body's feet
        target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        found = match.script_body_position(target, world.body)
        return found if found is not None else NotImplemented
    if cls in ("STU_021D01CD", "STU_C8C4C20D"):  # a mech nobody plays faces as it was put
        loose = match.forms.loose.get(expr.to_entity(instance.evaluate(cfg.get("m_entity"))))
        if loose is None:
            return NotImplemented
        turn = loose.yaw * math.tau / 65536
        if cls == "STU_C8C4C20D":
            return expr.f32(math.degrees(turn))
        return expr.Vec3(expr.f32(math.sin(turn)), 0.0, expr.f32(math.cos(turn)))
    if cls == "STU_E72F6348":  # whether the entity exists (CE4A60)
        target = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        return bool(target) and (
            target in match.forms.loose
            or any(target in (player.entity, player.body) for player in match.players if not player.gone)
            or any(bot.entity == target for bot in match.bots))
    return NotImplemented


def message(world, target: int, message: int, params: dict) -> bool:
    """A message one of her bodies sends to its owner's hero: the eject, or one for the body played now."""
    match = world.match
    owner = next((player for player in match.players if player.entity == target), None)
    if owner is None:
        return False
    if message != EJECT:
        match.forms.pending.append(("message", owner, world.body, message, dict(params)))
    elif owner.has_body and owner.body == world.body:
        match.forms.pending.append(("eject", owner))
    return True


def create_owned_entity(world, instance, node):
    """STU_B6E3C77D in the pilot's graph: her mech. Call Mech's stands at its landing point until she
    boards it. The one her graph makes as she spawns is the mech she has just left, or none: a pilot
    this server makes is on foot."""
    match = world.match
    if match is None or world.body_definition != PILOT or instance.graph.index != PILOT_GRAPH:
        return NotImplemented
    if node.pos != CALLED_MECH:
        return node.pos == MADE_MECH or NotImplemented
    player = _player(match, world.body)
    if player is not None:
        # shortcut: the search for a landing spot is not played (nodes.Placement), and without one
        # m_position is her own root less 0.75 m, her feet. Use m_position once the search is played.
        entity = match.forms.call_mech(player, tuple(player.mover.position))
        if entity is not None:
            instance.write(expr.lvalue(node.field("m_B3019338")), Entity(entity))
    instance.follow(node, "m_outPlug", None)
    return True


def destroy_entity(world, instance, node):
    """STU_523EE38D in her graphs: the mech that blew up, or the called one she did not reach."""
    match = world.match
    if match is None or world.body_definition not in BODIES:
        return NotImplemented
    match.forms.destroy(expr.to_entity(instance.evaluate(node.field("m_entity"))))
    return True


def board(world, state) -> None:
    """STU_CA861E44 in the pilot's graph begins: she is in the entity it names."""
    match = world.match
    player = _player(match, world.body) if match is not None else None
    if player is not None and world.body_definition == PILOT:
        entity = expr.to_entity(state.evaluate(state.node.field("m_C98661CF"), watch=False))
        match.forms.pending.append(("board", player, entity))


def blast(world, state):
    """One of her two searches that hit what they find: every hostile body in clear sight within the
    radius of the mech takes the graph's amount, for Self-Destruct by its distance."""
    match, instance = world.match, state.instance
    search, holder, where, height = BLASTS.get((world.body_definition, instance.graph.index), (None,) * 4)
    if match is None or state.node.pos != search:
        return NotImplemented
    mech = world.body
    if world.body_definition == PILOT:
        called = instance.evaluate({"$": "STUConfigVarDynamic", "m_identifier": CALLED})
        mech = expr.to_entity(called)
    loose = match.forms.loose.get(mech)
    owner = loose.owner if loose is not None else _player(match, mech)
    if owner is None:
        return ()
    feet = loose.position if loose is not None else tuple(owner.mover.position)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    amount = instance.graph.node(holder).field(where)
    found = match.combat.blast_targets(owner, (feet[0], feet[1] + height, feet[2]), radius)
    if loose is not None:  # a search runs many times while its state is on: each body once
        found = [(target, distance) for target, distance in found if target.entity not in loose.hit]
        loose.hit.update(target.entity for target, _ in found)
    if found:
        log.info("[game] %s: %s's mech hits %d within %.1f m of (%.1f, %.1f, %.1f)", match.label(),
                 owner.name, len(found), radius, *feet)
    for target, distance in found:
        # shortcut: the hits are given here, not by the graph's own ModifyHealth states (their
        # knockback rings and tags are not played), which get no targets.
        instance.write((expr.INSTANCE, DISTANCE), expr.f32(distance))
        damage = expr.to_float(instance.evaluate(amount))
        if math.isfinite(damage) and damage > 0.0:
            match.combat._damage(owner, target, damage, False, None, instance.component.frame)
    return ()
