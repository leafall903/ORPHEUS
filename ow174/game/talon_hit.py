"""Reconstructed 104319 Talon hit actions with external archive data."""

from functools import cache
import json
import math
import os
from pathlib import Path
import struct

from ow174.game.npc_specials import ENFORCER_BODY, TALON_HIT_MESSAGE, TROOPER_BODY
from ow174.game.script import expr, runtime

REACTION = 0x0D8000000000255F
HEAVY_BODY = 0x0400000000001EBB
SNIPER_BODY = 0x0400000000001E7D
REACTION_2560 = 0x0D80000000002560
REACTION_2596 = 0x0D80000000002596
REACTION_2639 = 0x0D80000000002639
TROOPER_BLEND = "00000000079C.021"
ENFORCER_BLEND = "0000000008A3.021"
HEAVY_BLEND = "0000000007C0.021"
SNIPER_BLEND = "0000000007BA.021"
RANGE_REACTIONS = (REACTION, REACTION_2560, REACTION_2596)
ACTION_REACTIONS = (*RANGE_REACTIONS, 0x0D800000000038DB)
COMMON_CATEGORY = expr.Asset(0x0280000000000080)
BODY_ACTIONS = {
    (TROOPER_BODY, 34): ACTION_REACTIONS,
    (ENFORCER_BODY, 34): (REACTION_2596, REACTION_2639),
    (HEAVY_BODY, 41): (REACTION_2560,),
    (SNIPER_BODY, 34): (REACTION_2596,),
    (SNIPER_BODY, 35): (REACTION,),
    (SNIPER_BODY, 41): (REACTION_2560,),
}
DIRECTIONAL_BLEND = {
    TROOPER_BODY: TROOPER_BLEND,
    ENFORCER_BODY: ENFORCER_BLEND,
    SNIPER_BODY: SNIPER_BLEND,
}


def _candidate_path():
    configured = os.environ.get("ORPHEUS_TALON_HIT_CANDIDATES")
    folder = os.environ.get("ORPHEUS_PVE_NPC_DATA")
    if configured:
        path = Path(configured)
    elif folder:
        path = Path(folder) / "talon_hit_candidates_174.json"
    else:
        return None
    return path if path.is_file() else None


def notify_damage(bot, sender, amount, direction, tick):
    """Reconstruct a directional range notification from an admitted body hit."""
    script = bot.body_script
    if bot.body not in (TROOPER_BODY, HEAVY_BODY) or bot.dead or script is None or script.component.owner_dead:
        return
    component = script.component
    path = _candidate_path()
    if path is None:
        return
    directional = bot.body == TROOPER_BODY
    if not directional and (component.entity_var(9956).value() is not None
                            or not _empty_reaction(path, HEAVY_BLEND, REACTION_2560)):
        return
    table_graph, table_position = (0x1231, 6) if directional else (0x0E54, 32)
    reactions = RANGE_REACTIONS if directional else (REACTION_2560,)
    table = next((i for i in component.instances.values() if i.graph.index == table_graph and i.initialized), None)
    listener = next((i for i in component.instances.values() if i.graph.index == 0x0E20 and i.initialized), None)
    if table is None or listener is None:
        return
    node = table.graph.node(table_position)
    if node is None or node.cls != "STU_F3104738":
        return
    x, _, z = direction
    length = math.hypot(x, z)
    if not length:
        return
    for row in node.field("m_hitReactions") or ():
        reaction = expr.guid_of(row.get("m_2DDFCB42"))
        condition = row.get("m_1E958CD3")
        if (reaction not in reactions or row.get("m_22A48A23")
                or not isinstance(condition, dict) or condition.get("$") != "STU_18904D02"):
            continue
        lower, upper = (table.evaluate(condition.get(key)) for key in ("m_min", "m_max"))
        # The amount source, inclusive comparison and notification payload are host reconstruction.
        # These three archive intervals are disjoint.
        if not (type(lower) is int and type(upper) is int and lower <= amount <= upper
                and (not directional or _direction_rows(path, reaction))):
            continue
        # The reconstructed Heavy route uses the graph's false-0407 branch
        # under its observed natural category. The original producer is open.
        payload = {0x0403: expr.Asset(reaction), 0x0407: directional, 0x041C: expr.Vec3(x / length, 0.0, z / length)}
        at = tick * component.frame_ms
        if not directional:
            runtime.send_instance_message(table, TALON_HIT_MESSAGE, payload, sender, at=at)
        runtime.send_instance_message(listener, TALON_HIT_MESSAGE, payload, sender, at=at)
        return


def _contains(bounds, value):
    low, high = bounds
    return low <= value <= high if low <= high else value >= low or value <= high


@cache
def _catalog(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data["build"] != 104319:
        raise ValueError("Talon hit candidates require build 104319")
    return data


@cache
def _complete_rows(path, asset):
    # The older filtered catalog cannot prove a negative across the model set.
    data = _catalog(path)
    expected = next((row["rows"] for row in data.get("sets_with_rows", ()) if row["asset"] == asset), 0)
    rows = tuple(row for row in data["matched_rows"] if row["asset"] == asset)
    return rows if expected > 0 and sorted(row["row"] for row in rows) == list(range(expected)) else ()


def _empty_reaction(path, asset, reaction):
    identifier = f"{reaction & 0xFFFF:04X}"
    rows = _complete_rows(path, asset)
    return bool(rows) and all(identifier not in row["required_ids"] for row in rows)


@cache
def _direction_rows(path, reaction=REACTION, asset=TROOPER_BLEND):
    data = _catalog(path)
    identifier = f"{reaction & 0xFFFF:04X}"
    rows = []
    for row in data["matched_rows"]:
        if (row["asset"] != asset
                or row["required_ids"] != [identifier, "25E5", "25E6", "25DC"]
                or row["required_values"] != ["00000000", "00000080", "00000080", "00000000"]):
            continue
        limits = {key: struct.unpack("<hh", int(value, 16).to_bytes(4, "little"))
                  for key, value in zip(row["numeric_ids"], row["numeric_values"], strict=True)}
        # This host action models the observed zero auxiliary query. A row
        # requiring any other auxiliary input does not belong to this query.
        if all(key == "25D9" or _contains(bounds, 0) for key, bounds in limits.items()):
            rows.append((int(row["handle"], 16), limits["25D9"]))
    if not rows and reaction == REACTION and asset == TROOPER_BLEND:
        raise ValueError("Talon hit candidates lack Trooper 079C.021 255F directional rows")
    return tuple(rows)


@cache
def _sniper_duration(path, alias):
    for row in _catalog(path).get("tree_bindings", ()):
        if (row["asset"] != SNIPER_BLEND or row.get("field") != "m_253EE7C8"
                or expr.guid_of(row["handle"]) != alias):
            continue
        clips = row.get("clips") or ()
        if len(clips) == 1:
            seconds = clips[0].get("scalar")
            if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0:
                return expr.f32(seconds)
    return None


def _ranked_aliases(rows, angle):
    # 17799E0 truncates degrees * 64 before the signed interval comparison.
    quantized = math.trunc(expr.f32(angle * 64))
    admitted = [(alias, bounds) for alias, bounds in rows if _contains(bounds, quantized)]
    best = -1
    for index in range(len(admitted) - 1, -1, -1):
        _, (low, high) = admitted[index]
        if low > high:
            score = 32
        else:
            half_width = (high - low) / 128
            centre = low / 64 + half_width
            distance = min(1.0, abs(angle - centre) / half_width) if half_width else 1.0
            score = 64 - expr.round_half_away(expr.f32(distance * 63))
        # CC386C visits backwards and replaces a lower row with the last row.
        if score > best:
            del admitted[index + 1:]
            best = score
        elif score < best:
            admitted[index] = admitted[-1]
            admitted.pop()
    return tuple(alias for alias, _ in admitted)


def hit_action(instance, node):
    component = instance.component
    world = component.world
    body = getattr(world, "body_definition", None)
    reactions = BODY_ACTIONS.get((body, node.pos))
    if instance.graph.index != 0x0E20 or component.owner_dead or world.match is None or not reactions:
        return NotImplemented
    config = node.field("m_59FEECBB")
    if (not isinstance(config, dict) or config.get("$") != "STU_7D0B26AA" or config.get("m_target") is not None
            or instance.evaluate(config.get("m_BEAC1E98")) is not None
            or any(instance.evaluate(config.get(key)) != COMMON_CATEGORY for key in ("m_36F7F4BC", "m_80DC3C62"))):
        return NotImplemented
    reaction = instance.evaluate(config.get("m_BB9B77AC"))
    if not isinstance(reaction, expr.Asset) or reaction.guid not in reactions:
        return NotImplemented
    vector = instance.evaluate(config.get("m_D08DDC33"))
    destination = expr.lvalue(node.field("m_0A915A83"))
    if not isinstance(vector, (expr.Vec3, tuple)) or destination is None:
        return NotImplemented
    axes = tuple(vector)
    if len(axes) != 3 or not all(type(value) in (int, float) and math.isfinite(value) for value in axes):
        return NotImplemented
    x, _, z = axes
    if x * x + z * z <= 0.00011920928955078125:  # CC28C3, horizontal vector gate.
        return NotImplemented
    actor = next((bot for bot in world.match.bots if bot.entity == component.entity and not bot.dead), None)
    path = _candidate_path()
    if actor is None or path is None:
        return NotImplemented
    if body == HEAVY_BODY:
        duration = expr.lvalue(node.field("m_5502D358"))
        if duration is None or not _empty_reaction(path, HEAVY_BLEND, reaction.guid):
            return NotImplemented
        instance.write(destination, None)
        instance.write(duration, 0.0)
        instance.follow(node, "m_outPlug")
        return True
    asset = DIRECTIONAL_BLEND[body]
    if body == SNIPER_BODY and not _complete_rows(path, asset):
        return NotImplemented
    rows = _direction_rows(path, reaction.guid, asset)
    if not rows and (body != ENFORCER_BODY or reaction.guid != REACTION_2639
                     or not _empty_reaction(path, asset, reaction.guid)):
        return NotImplemented
    # CC28CD reverses the horizontal vector. The host uses the live body yaw
    # as its reconstructed owner transform.
    yaw = round(actor.yaw) * math.tau / 65536
    sine, cosine = math.sin(yaw), math.cos(yaw)
    angle = expr.f32(math.degrees(math.atan2(z * sine - x * cosine, -x * sine - z * cosine)))
    aliases = _ranked_aliases(rows, angle)
    alias = None
    if aliases:
        ordinal = expr.to_int(instance.evaluate(node.field("m_9E677E71"))) if len(aliases) > 1 else 0
        alias = expr.Asset(aliases[ordinal % len(aliases)])
    if body == SNIPER_BODY and node.pos != 34:
        duration = expr.lvalue(node.field("m_5502D358"))
        # BBB100 copies the selected animation-data duration. An empty choice
        # leaves the duration result at its initialized zero.
        seconds = _sniper_duration(path, alias.guid) if alias is not None else 0.0
        if duration is None or seconds is None:
            return NotImplemented
        instance.write(duration, seconds)
    instance.write(destination, alias)
    instance.follow(node, "m_outPlug")
    return True
