"""Declared manual NPC controller policy; native allocation remains open."""

import hashlib
import struct

from ow174.game import assassin
from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
WEAPON = 0x13EB


def manual_components(bot):
    """Reconstruct4B present/5C absent for manual Enforcer and Assassin bodies."""
    eligible = bot.hero in (ENFORCER, assassin.HERO) and bot.spawner.get('manual_spawn') is True
    return (0x4B,) if eligible else None


def controller_predicate(components):
    """B96D3A98's native tail requires4B and excludes5C."""
    return components is not None and 0x4B in components and 0x5C not in components


def native(world, cfg, instance):
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.graph.index != WEAPON):
        return NotImplemented
    target = cfg.get('m_entity')
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    if cfg.get('$') == 'STU_B96D3A98':
        return controller_predicate(world.controller_components)
    if cfg.get('$') == 'STU_9B7648C3':
        # ReplicatedBody.set_health supplies the authoritative aggregate here.
        from ow174.game.script.driver import OWNER_HEALTH

        slot = instance.component.entity_var(OWNER_HEALTH, create=False)
        return expr.f32(slot.value()) if slot is not None else 0.0
    return NotImplemented


def launch_inputs(world, state, number, activation, count):
    """Declared manual shot context;not the native engine's seed/table policy.

    A stateless SHAKE stream keys each actual activation/shot to one azimuth and
    two uint32 words per pellet. Existing causal identifiers fully determine it;
    this neither advances the VM RNG nor adds hidden mutable simulation state.
    """
    if (world.hero != ENFORCER or not controller_predicate(world.controller_components)
            or state.instance.component.world is not world or state.instance.graph.index != WEAPON
            or state.node.pos != 24 or count != 25):
        return None
    from ow174.game import launch_ring

    key = struct.pack('<6Q', state.instance.component.entity, state.instance.id, state.node.pos,
                      number, activation[0], activation[1])
    data = hashlib.shake_256(b'ow174.manual.enforcer.launch.v1\0' + key).digest(4 + 8 * count)
    words = struct.unpack(f'<{1 + 2 * count}I', data)
    return launch_ring.Inputs(launch_ring.uniform(words[0], 0, launch_ring.TAU),
                             tuple(zip(words[1::2], words[2::2], strict=True)))
