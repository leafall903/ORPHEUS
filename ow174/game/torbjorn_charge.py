"""Scoped Torbjorn/Genji/Orisa host for actual0B8B charge timers and damage messages.

The sandbox supplies ordinary character/team eligibility and tagged damage
for the Adaptive Shield deduction. Native engine tags and pre-round gating
remain incomplete. Actual graph formulas retain cost, rate and modifiers.
"""

from math import dist

from ow174.game.script import expr, runtime

HERO = 0x02E0000000000006
GENJI = 0x02E0000000000029
ORISA = 0x02E000000000013E
WRECKING_BALL = 0x02E00000000001CA
SUPPORTED_HEROES = frozenset((HERO, GENJI, ORISA))
CONTROLLER = 0x0B8B
DAMAGE = 0x0240000000000012
ADAPTIVE_SHIELD_TAG = 0x3EBB
SANDBOX_SECONDS = 60.0


def owner(world, component):
    if world.hero not in SUPPORTED_HEROES or world.match is None or component.strict:
        return None
    return next((player for player in world.match.players
                 if not player.gone and player.client is not None and player.hero.guid == world.hero
                 and player.body == world.body and player.body_script is not None
                 and player.body_script.component is component), None)


def enabled(script):
    return owner(script.world, script.component) is not None


def adaptive_shield_targets(fight, state):
    component = state.instance.component
    if component.strict or (state.instance.graph.index, state.node.pos) != (0x1566, 20):
        return NotImplemented
    player = next((p for p in fight.match.players if p.body == component.entity and not p.gone), None)
    if player is None or fight.is_dead(player):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    return tuple(expr.Entity(target.entity) for target in fight._enemies(player)
                 if getattr(target.owner, "combat_owner", None) is None
                 and dist(player.mover.position, target.feet) <= radius)


def sync_adaptive_shield(fight, player, script):
    if player.hero.guid != WRECKING_BALL:
        return
    target = next((target for target in fight._targets() if target.owner is player), None)
    if target is None:
        return
    vitals = fight.vitals_of(target)
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1566), None)
    state = instance.states.get(6) if instance is not None else None
    source = (instance.id, state.counter) if state is not None and state.active and not vitals.dead else None
    if source == vitals.adaptive_shield_source:
        return
    vitals.adaptive_shield_source = source
    pool = vitals.temporary_shields.get(ADAPTIVE_SHIELD_TAG)
    if pool is not None:
        vitals.decay_temporary_shields(ADAPTIVE_SHIELD_TAG, pool.now)
    if source is not None:
        amount = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
        vitals.grant_temporary_shields(ADAPTIVE_SHIELD_TAG, amount, amount)
    fight._sync_body_health(player, vitals)


def prepare(script):
    if not enabled(script):
        return
    # Common1C7 normally initializes this permission through missing game-mode
    # context. Supply only an unset sandbox permission; retain explicit false.
    permission = script.component.entity_var(2426)
    if permission.value() is None:
        script.component.set(2426, True)


def _passive_timer(script):
    for instance in script.component.instances.values():
        if instance.graph.index == CONTROLLER:
            state = instance.states.get(1)
            if state is not None and state.active:
                return next((event for event in instance.queue
                             if event.state is state and event.kind == runtime.TIMER
                             and event.param == 1), None)
    return None


def passive_timer_due(script):
    timer = _passive_timer(script)
    if timer is not None:
        return timer.time
    return getattr(script.world, "passive_charge_due", None)


def passive_wait_duration(state, duration):
    world = state.instance.component.world
    due = getattr(world, "passive_charge_due", None)
    if due is not None and state.instance.graph.index == CONTROLLER and state.index == 1:
        world.passive_charge_due = None
        return max(0, due - state.now)
    return duration


def native(world, cfg, instance):
    player = owner(world, instance.component)
    if player is None or instance.graph.index != CONTROLLER:
        return NotImplemented
    cls = cfg.get("$")
    if cls == "STU_F7262B36":
        # Nondefault ult_seconds remains an explicit sandbox timer override.
        return player.body_script.ult_seconds == SANDBOX_SECONDS and not world.match.assembling()
    if cls not in ("STU_AC829876", "STU_A58C1E63", "STU_A6B80C63"):
        return NotImplemented
    if cls == "STU_A6B80C63":
        records = instance.evaluate(cfg.get("m_814F8EC3"))
        selectors = (cfg.get("m_C239F989") or {}).get("m_configVars") or ()
        if not records:
            return 0.0
        if len(selectors) == 1 and instance.evaluate(selectors[0]) == expr.Asset(0x0D80000000003EBB):
            return sum(loss for tag, loss in records if tag == ADAPTIVE_SHIELD_TAG)
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity" if cls == "STU_AC829876" else "m_target")))
    target = next((target for target in world.match.combat._targets() if target.entity == entity), None)
    if cls == "STU_AC829876":
        filters = cfg.get("m_filter") or {}
        if filters.get("m_84065FD1") != 1:
            return NotImplemented
        # Ordinary players and NPC characters qualify; deployables have an
        # owning combat actor. Other native tag/filter branches stay unmodelled.
        return target is not None and getattr(target.owner, "combat_owner", None) is None
    return (target is not None and target.owner is not player
            and (world.match.game_map.free_for_all or target.team != player.team))


def tags(script, loadout):
    # Keep actual authored ultimate tag025A, which0B8B/36 rejects. A persistent
    # Molten pool uses the same loadout as16DA's initial projectile.
    from ow174.game.script import graph

    instances = {instance.graph.index: instance for instance in script.component.instances.values()}
    # Current-owner graphs cover its own attacks. Retain source Torbjorn graphs
    # for reflected attacks,including Molten's authored ultimate exclusion tag.
    indices = dict.fromkeys((*instances, 0x1683, 0x168C, 0x0043, 0x16DA, 0x1689))
    for index in indices:
        definition = graph.graph(index)
        if definition is None:
            continue
        context = instances[index].context() if index in instances else None
        if context is None:
            context = next(iter(script.component.instances.values())).context()
        for node in definition.nodes:
            modify = node.field("m_modifyHealth") or (node.field("m_6396149F") or {}).get("m_modifyHealth")
            if not modify:
                continue
            value = expr.evaluate(modify.get("m_loadout"), context)
            if value == expr.Asset(loadout):
                return tuple(expr.evaluate(row.get("m_EE3055BD"), context)
                             for row in modify.get("m_148F3152") or ())
    return ()


def damage(fight, player, target, amount, loadout, adaptive_shield_loss):
    if fight.match.assembling():
        return
    script = getattr(player, "body_script", None)
    world = getattr(script, "world", None)
    if world is None or world.match is not fight.match or not enabled(script) or amount <= 0:
        return
    prepare(script)
    controller = next((instance for instance in script.component.instances.values()
                       if instance.graph.index == CONTROLLER), None)
    if controller is None:
        return
    # Native event012 maps13/15/3D/624 to loss336,victim393,tags510,pools16100.
    records = ((ADAPTIVE_SHIELD_TAG, expr.f32(adaptive_shield_loss)),) if adaptive_shield_loss > 0 else ()
    runtime.send_instance_message(controller, DAMAGE, {
        0x13: expr.f32(amount), 0x15: expr.Entity(target.entity),
        0x3D: tags(script, loadout) if loadout is not None else (), 0x624: records,
    }, player.body)
    script.component.run_now()
