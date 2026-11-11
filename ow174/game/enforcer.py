"""Manual Enforcer weapon gate and authored shield recovery host."""

from ow174.game.script import expr, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E00000000001CE
BODY = 0x0400000000002279
SHIELD_GRAPH = 0x0E62
SHIELD_MARKER = 1764
RECEIVED_DAMAGE = 0x0240000000000011
CURRENT_POOL, MAX_POOL = "STU_783B4CF0", "STU_68883EAC"


def has_recovery(bot):
    return (bot.hero == HERO and bot.body == BODY and not bot.dead
            and bot.body_script is not None and bot.spawner.get("manual_spawn") is True)


def controller_components(bot):
    if bot.hero == HERO and bot.body == BODY and bot.spawner.get("manual_spawn") is True:
        return (0x4B,)
    return None


def native(world, cfg, instance):
    """Supply scoped shield queries and the declared manual weapon gate."""
    if _shield_scope(world, instance) and cfg.get("$") in (CURRENT_POOL, MAX_POOL):
        pool = cfg.get("m_7E8B666C") or {}
        if (pool.get("m_7E8B666C") == SHIELDS_POOL
                and instance.evaluate(cfg.get("m_entity")) == expr.Entity(instance.component.entity)):
            vitals = world.match.combat.vitals.get(instance.component.entity)
            if vitals is not None:
                return expr.f32((vitals.now if cfg["$"] == CURRENT_POOL else vitals.most)[SHIELDS_POOL])
    if (cfg.get("$") != "STU_B96D3A98" or world.hero != HERO or world.controller_components is None
            or instance is None or instance.graph.index != 0x13EB):
        return NotImplemented
    target = cfg.get("m_entity")
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    return 0x4B in world.controller_components and 0x5C not in world.controller_components


def _shield_scope(world, instance):
    return (world.hero == HERO and world.match is not None and instance is not None
            and instance.graph.index == SHIELD_GRAPH and not instance.component.strict)


def received_damage(fight, bot, vitals, source):
    if not has_recovery(bot):
        return
    script = bot.body_script
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == SHIELD_GRAPH), None)
    if not _shield_scope(component.world, instance) or vitals.most[SHIELDS_POOL] <= 0:
        return
    if component.entity_var(SHIELD_MARKER, create=False) is None:
        component.set(SHIELD_MARKER, vitals.most[SHIELDS_POOL])
    # The reconstructed damage event supplies the graph's empty tag list.
    runtime.send_instance_message(instance, RECEIVED_DAMAGE, {0x3D: ()}, source)
    if script.frame >= fight.tick:
        component.run_now()


def modify_health(world, state, elapsed_ms):
    if state.node.pos != 51 or not _shield_scope(world, state.instance):
        return NotImplemented
    if not elapsed_ms:
        wait = state.instance.states.get(state.instance.graph.node(52).state)
        if wait is not None and not wait.active and wait.ended_at == state.start:
            # An overdue Wait is delivered at the new frame, but recovery starts at its deadline.
            state.last_applied = min(state.start, wait.start + wait.ms)
        return True
    fight = world.match.combat
    body = state.instance.component.entity
    bot = next((b for b in fight.match.bots if b.entity == body and not b.dead), None)
    vitals = fight.vitals.get(body)
    if bot is None or vitals is None or vitals.dead or vitals.now[SHIELDS_POOL] >= vitals.most[SHIELDS_POOL]:
        state.request(runtime.ABORT)
        return True
    amount = state.node.field("m_modifyHealth")["m_amount"]
    rate = expr.to_float(state.evaluate(amount, watch=False))
    restored = rate * elapsed_ms / 1000
    if restored > 0:
        vitals.now[SHIELDS_POOL] = min(vitals.most[SHIELDS_POOL], vitals.now[SHIELDS_POOL] + restored)
        vitals.changed.add(SHIELDS_POOL)
        fight._sync_body_health(bot, vitals)
    return True
