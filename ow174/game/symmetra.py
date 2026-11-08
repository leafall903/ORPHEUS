"""104319 Photon Projector world bridge; full hero acceptance remains open.

157C's primary is TrackRay/ModifyHealth, not WeaponVolley. Populate its two
ray results from the combat world and apply the authored damage per second.
Capsule traces, the eye origin and per-command damage integration are server
reconstructions. Charge-level modifiers, barriers and other defensive services
are not supplied here and must not be inferred from this bridge's tests.
"""

from ow174.game.script import expr, graph

HERO = 0x02E0000000000016
WEAPON = 0x157C


def primary(combat, player, command):
    # Import locally: combat owns geometry and calls this bridge after body input.
    from ow174.game.combat import _number, aim, ray_capsule, ray_world

    if player.hero.guid != HERO:
        return
    script = player.body_script
    for instance in tuple(script.component.instances.values()):
        if instance.graph.index != WEAPON:
            continue
        search = instance.states.get(76)
        active = command is not None and search is not None and search.active
        origin = combat._eye(player)
        direction = aim(*command.aim()) if active else (0.0, 0.0, 1.0)
        reach = _number(instance.evaluate({"$": "STUConfigVarDynamic",
                                          "m_identifier": 1648, "m_60DB8F99": 0}), 0.0)
        view_tick = (command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
                     if active else combat.tick)
        selected = None
        with script.component.operation():
            for index in (100, 101):
                state = instance.states.get(index)
                target = None
                hit = False
                if active and state is not None and state.active and reach > 0:
                    radius_cfg = state.node.field("m_57DAE1B5")
                    radius = _number(state.evaluate(radius_cfg, watch=False), 0.0)
                    best = None
                    for candidate in combat._enemies(player):
                        shown = combat.seen(candidate, view_tick)
                        if shown is None:
                            continue
                        a, b = shown.segment()
                        distance = ray_capsule(origin, direction, a, b, shown.radius + radius)
                        if distance is not None and distance <= reach and (
                            best is None or distance < best[0]
                        ):
                            best = distance, candidate
                    wall = ray_world(combat._collision(), origin, direction, reach)
                    if best is not None and (wall is None or best[0] < wall):
                        target, hit = best[1], True
                    elif wall is not None:
                        hit = True
                # TrackRay 100 is preferred; 101 supplies the authored 0.2 m fallback.
                if selected is None and target is not None:
                    selected = target
                output = instance.graph.state(index).field("m_target")
                for name, value in (
                    ("m_692DFF72", expr.Entity(target.entity if target else expr.INVALID_ENTITY)),
                    ("m_750377AA", hit),
                ):
                    cfg = output.get(name)
                    if cfg is not None and cfg.get("$") == "STUConfigVarDynamic":
                        instance.write((cfg.get("m_60DB8F99", graph.INSTANCE), cfg["m_identifier"]), value)
        if not active or selected is None:
            continue
        damage = instance.state(86)
        modify = damage.node.field("m_modifyHealth")
        amount = _number(damage.evaluate(modify.get("m_amount"), watch=False), 0.0)
        loadout = damage.evaluate(modify.get("m_loadout"), watch=False)
        combat._damage(player, selected, amount * player.quanta / 1e6, False,
                       loadout.guid if isinstance(loadout, expr.Asset) else None, command.frame)
