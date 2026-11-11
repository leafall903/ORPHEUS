"""Scoped world/event bridge to original1C7/0B8B charge graphs.

Formulas, clamps, ruleset scalars, blocked-charge#842 and timers remain authored.
Known combat-body classification and lifecycle activation are reconstructed.
Only static target tags and empty non-chargeable-damage detail arrays are handled;
native filter/phase/dynamic-tag/fold/credit equivalence remains unaccepted.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from ow174.game import (
    bots,
    genji_combat,
    mei,
    moira,
    pharah,
    pulse_bomb,
    reaper_blossom,
    reinhardt_offense,
    sigma_combat,
    tracer_combat,
    winston_combat,
)
from ow174.game.script import expr, message_receive, nodes, runtime
from ow174.paths import DATA_DIR

ORISA = 0x02E000000000013E
HEROES = (ORISA, genji_combat.HERO, mei.HERO, moira.HERO, pharah.HERO, reaper_blossom.HERO,
          reinhardt_offense.HERO, sigma_combat.HERO, tracer_combat.HERO, winston_combat.HERO)
DEALT = 0x0240000000000012
HEALED = 0x0240000000000044
ACTIVE = 0x0240000000000694


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
    return world.match.combat.ultimate_charge.hosted_catalog() if world.match is not None else None


def _target(world, entity):
    if world.match is None:
        return None
    return next((t for t in world.match.combat._targets() if t.entity == entity), None)


def _body(target):
    owner = target.owner
    return owner.body if isinstance(owner, bots.Bot) else owner.hero.body


def native(world, cfg, instance):
    if world.hero not in HEROES or world.match is None or instance is None or instance.graph.index != 0xB8B:
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
        enabled = script is None or (script.ult_seconds > 0 and admits_combat_credit(script))
        return expr.Entity(actor.entity if enabled else 0)
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
        if target is None or target.is_device or _body(target) not in records:
            return NotImplemented
        if cls == "STU_25ECB55F":
            return expr.guid_of(cfg.get("m_88B0050A")) in records[_body(target)]
        selected = {k: v for k, v in (cfg.get("m_filter") or {}).items() if v not in (None, 0)}
        if selected == {"$": "STUConfigVarFilter", "m_84065FD1": 1}:
            return True  # known catalog hero/NPC combat bodies, not arbitrary entities
    return NotImplemented


def activate(script):
    if script.hero not in HEROES or script.world.match is None or _metadata(script.world) is None:
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


def admits_combat_credit(script, *, posthumous=False):
    return script.hero == ORISA or (
        not script.retired and (posthumous or not script.component.owner_dead)
        and not any(state.flags & nodes.Ability.RUNNING for state in script.ultimates)
        and (script.hero != pharah.HERO or pharah.barrage_channel(script.component) is None)
    )


def deliver(script, message, params):
    charge = next((i for i in script.component.instances.values() if i.graph.index == 0xB8B), None)
    if charge is not None:
        packet = message_receive.Packet(message, script.entity, script.entity, tuple(
            (message_receive.PARAMETER_TYPE | key, value) for key, value in params))
        message_receive.receive(charge, packet)
        script.component.run_now()


def damage(shooter, target, amount, loadout):
    script = getattr(shooter, "body_script", None)
    posthumous = (getattr(script, "hero", None) == reinhardt_offense.HERO
                  and loadout == reinhardt_offense.FIRE_STRIKE_LOADOUT)
    if (getattr(script, "hero", None) not in HEROES or target.is_device or amount <= 0
            or (script.hero == sigma_combat.HERO
                and (loadout not in sigma_combat.CHARGE_LOADOUTS or target.owner is shooter))
            or loadout in (genji_combat.BLADE_LOADOUT, reaper_blossom.LOADOUT,
                           moira.COALESCENCE_LOADOUT, pharah.BARRAGE_LOADOUT,
                           reinhardt_offense.SHATTER_LOADOUT, pulse_bomb.LOADOUT)
            or not admits_combat_credit(script, posthumous=posthumous) or not activate(script)):
        return
    # Vitals supplies the effective pool loss. Non-chargeable damage detail
    # remains an explicit empty array in the current body-pool model.
    deliver(script, DEALT, (
        (0x13, float(amount)), (0x15, expr.Entity(target.entity)),
        (0x3D, expr.Asset(loadout) if loadout is not None else None), (0x624, ()),
    ))


def healing(script, target, amount, tags):
    if amount > 0 and admits_combat_credit(script) and activate(script):
        deliver(script, HEALED, (
            (0x40, float(amount)), (0x41, tuple(expr.Asset(tag) for tag in tags)),
            (0x3F, expr.Entity(target)),
        ))
