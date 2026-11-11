"""Scoped world/event bridge to original1C7/0B8B charge graphs.

Formulas, clamps, ruleset scalars, blocked-charge#842 and timers remain authored.
Known combat-body classification and lifecycle activation are reconstructed.
Only static target tags and empty non-chargeable-damage detail arrays are handled;
native filter/phase/dynamic-tag/fold/credit equivalence remains unaccepted.
"""

import json
import math
import os
from functools import lru_cache
from pathlib import Path

from ow174.game import bots
from ow174.game.script import expr, message_receive, runtime
from ow174.paths import DATA_DIR

ORISA = 0x02E000000000013E
DEALT = 0x0240000000000012
ACTIVE = 0x0240000000000694
RESTORE = 0x02400000000014F2


def replacement_ratio(player, hero):
    """Bridge same-hero replacement only; do not retain a departed hero's meter."""
    script = player.body_script
    if (script is None or script.hero != ORISA or hero != ORISA
            or _metadata(script.world) is None):
        return None
    charge = script.component.entity_var(342).value()
    cost = script.component.entity_var(338).value()
    if not math.isfinite(charge) or not math.isfinite(cost) or cost <= 0:
        return None
    return max(0.0, charge / cost)


def restore(script, ratio, controller):
    """Use the original ratio message, including its authored refusal gates/clamp."""
    if ratio is None or script.hero != ORISA or _metadata(script.world) is None:
        return
    charge = next((i for i in script.component.instances.values() if i.graph.index == 0xB8B), None)
    if charge is not None:
        packet = message_receive.Packet(RESTORE, controller, controller,
                                       ((message_receive.PARAMETER_TYPE | 0x951, ratio),))
        message_receive.receive(charge, packet)
        script.component.run_now()


@lru_cache(maxsize=4)
def _read(path, stamp):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (data.get("schema") != "orpheus-orisa-charge-v1"
            or data.get("build") != "1.74.0.0.104319"):
        raise ValueError("Orisa charge metadata has wrong schema/build")
    return {int(k, 16): frozenset(int(t, 16) for t in v["tags"])
            for k, v in data["bodies"].items()}


def catalog():
    path = Path(os.environ.get("ORPHEUS_ORISA_CHARGE", DATA_DIR / "orisa_charge_174.json"))
    return _read(str(path), path.stat().st_mtime_ns) if path.is_file() else None


def _metadata(world):
    return world.match.combat.orisa.charge_metadata if world.match is not None else None


def _target(world, entity):
    if world.match is None:
        return None
    return next((t for t in world.match.combat._targets() if t.entity == entity), None)


def _body(target):
    owner = target.owner
    return owner.body if isinstance(owner, bots.Bot) else owner.hero.body


def native(world, cfg, instance):
    if world.hero != ORISA or world.match is None or instance.graph.index != 0xB8B:
        return NotImplemented
    records = _metadata(world)
    if records is None:
        return NotImplemented
    cls = cfg.get("$")
    if cls == "STU_F7262B36":
        entity = expr.to_entity(instance.evaluate(cfg.get("m_801B298F")))
        actor = next((p for p in world.match.players if p.body == entity), None)
        if actor is None or actor.gone:
            return expr.Entity(0)
        # Preserve the explicit manual-only test/GM setting. Positive values
        # use the authored5-points/second timer, not the old60-second fill model.
        script = actor.body_script
        return expr.Entity(actor.entity if script is None or script.ult_seconds > 0 else 0)
    if cls == "STU_A6B80C63":
        values = instance.evaluate(cfg.get("m_814F8EC3"))
        if values == ():
            return instance.evaluate(cfg.get("m_418D9D9D"))
        return NotImplemented  # general parameter-fold semantics still require restoration
    if cls == "STU_A58C1E63":
        source = expr.to_entity(instance.evaluate(cfg.get("m_source")))
        target = _target(world, expr.to_entity(instance.evaluate(cfg.get("m_target"))))
        owner = _target(world, source)
        if owner is None or target is None or source == target.entity:
            return False
        return world.match.game_map.free_for_all or target.team != owner.team
    if cls in ("STU_AC829876", "STU_25ECB55F"):
        target = _target(world, expr.to_entity(instance.evaluate(cfg.get("m_entity"))))
        if target is None or _body(target) not in records:
            return NotImplemented
        if cls == "STU_25ECB55F":
            return expr.guid_of(cfg.get("m_88B0050A")) in records[_body(target)]
        selected = {k: v for k, v in (cfg.get("m_filter") or {}).items() if v not in (None, 0)}
        if selected == {"$": "STUConfigVarFilter", "m_84065FD1": 1}:
            return True  # known catalog hero/NPC combat bodies, not arbitrary entities
    return NotImplemented


def foreign_var(world, entity, identifier):
    if world.hero == ORISA and world.match is not None and _metadata(world) is not None:
        target = _target(world, entity)
        script = getattr(target.owner, "body_script", None) if target is not None else None
        var = script.component.entity_var(identifier, create=False) if script is not None else None
        if var is not None:
            return var.value()
    return None


def activate(script):
    if script.hero != ORISA or script.world.match is None or _metadata(script.world) is None:
        return False
    # Scope the reconstruction to an actual shown, current body. Do not directly
    # assign charge permission#2426 or jump to the charge action.
    actor = next((p for p in script.world.match.players
                  if p.body_script is script and script.world.match._shown(p)), None)
    if actor is None:
        return True
    common = next((i for i in script.component.instances.values() if i.graph.index == 0x1C7), None)
    if common is not None:
        permission = common.node_state(common.graph.node(77))
        if not permission.active:
            runtime.send_instance_message(common, ACTIVE, {}, actor.entity)
            script.component.run_now()
    return True


def damage(shooter, target, amount, loadout):
    script = getattr(shooter, "body_script", None)
    if script is None or not activate(script):
        return
    # Vitals damage here already excludes overkill and deployable-only damage.
    # Current body pools have no implemented non-chargeable damage layer.
    params = ((0x13, float(amount)), (0x15, expr.Entity(target.entity)),
              (0x3D, expr.Asset(loadout) if loadout is not None else None), (0x624, ()))
    packet = message_receive.Packet(DEALT, script.entity, script.entity, tuple(
        (message_receive.PARAMETER_TYPE | key, value) for key, value in params))
    charge = next((i for i in script.component.instances.values() if i.graph.index == 0xB8B), None)
    if charge is not None:
        message_receive.receive(charge, packet)
    script.component.run_now()
