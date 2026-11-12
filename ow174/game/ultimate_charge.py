"""Deliver resolved damage to the authored 0B8B charge graph.

Ordinary Match supplies a scoped admission policy for its combat bodies.
Native filter and tag services take precedence when a host provides them.
"""

from contextvars import ContextVar

from ow174.game.bots import Bot
from ow174.game.deflection import GENJI
from ow174.game.script import expr, runtime

REINHARDT = 0x02E0000000000007
AUTHORED_CHARGE_HEROES = frozenset((REINHARDT, GENJI))
DAMAGE = 0x0240000000000012
_MATCH_EVENT = -1  # server metadata outside the authored uint16 parameter keys
_ADMITTED = ContextVar("combat_charge_admission", default=None)


def match_admission(instance, cfg):
    """Resolve the 0B8B target gates for the current server damage event."""
    admission = _ADMITTED.get()
    if admission is None or instance is not admission[0]:
        return NotImplemented
    scopes = instance.component.parameter_scopes
    if not scopes or not isinstance(scopes[-1], dict) or scopes[-1].get(_MATCH_EVENT) is not admission[2]:
        return NotImplemented
    cls = cfg.get("$")
    if cls == "STU_AC829876":
        definition = cfg.get("m_filter") or {}
        active_fields = {key: value for key, value in definition.items() if value not in (None, 0)}
        if active_fields != {"$": "STUConfigVarFilter", "m_84065FD1": 1}:
            return NotImplemented
        positions = (53, 54)
        target_field = "m_entity"
    elif cls == "STU_A58C1E63":
        if instance.evaluate(cfg.get("m_source")) != expr.Entity(instance.component.entity):
            return NotImplemented
        if expr.truthy(instance.evaluate(cfg.get("m_AFBBE9C3"))):
            return NotImplemented
        positions = (28,)
        target_field = "m_target"
    else:
        return NotImplemented
    for position in positions:
        node = instance.graph.node(position)
        options = node.field("m_condition.m_configVars") if node is not None else None
        if (options and cfg is options[0]
                and instance.evaluate(cfg.get(target_field)) == expr.Entity(admission[1])):
            return True
    return NotImplemented


def damage(shooter, target, amount, tags, *, reflected_strike=False, source_team=None):
    script = getattr(shooter, 'body_script', None)
    hero = getattr(script, 'hero', None)
    if hero != REINHARDT and not (hero == GENJI and reflected_strike):
        return
    if script.retired or script.world.match is None or amount <= 0:
        return
    match = script.world.match
    victim = target.owner
    team = shooter.team if source_team is None else source_team
    if (shooter not in match.players or script.entity != shooter.body or shooter.gone
            or not shooter.has_body or not shooter.spawned or shooter.client is None
            or match.ended or victim is shooter
            or (not match.game_map.free_for_all and victim.team == team)):
        return
    if isinstance(victim, Bot):
        current = victim in match.bots and target.entity == victim.entity
    else:
        current = (victim in match.players and victim.has_body and victim.spawned
                   and not victim.gone and victim.client is not None and target.entity == victim.body)
    if not current:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0xB8B), None)
    if instance is None:
        return
    # Empty excluded-detail list: this bridge's amount is already net pool loss.
    # Native gross/net itemization parity remains a separate accounting frontier.
    # The server event identifies the body that lost the pools.
    params = {0x13: float(amount), 0x15: expr.Entity(target.entity),
              0x3D: tuple(expr.Asset(tag) for tag in tags), 0x624: ()}
    event = (shooter.body, target.entity)
    params[_MATCH_EVENT] = event
    admission = _ADMITTED.set((instance, target.entity, event))
    try:
        runtime.send_instance_message(instance, DAMAGE, params, shooter.body)
        script.component.run_now()
    finally:
        _ADMITTED.reset(admission)
