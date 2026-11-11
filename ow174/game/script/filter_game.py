"""104319 D0DA20 orchestration over explicitly supplied engine services.

This is not mounted as World.native/AC829876. Source/target lookup, live component
bits, dependencies, script filters, engine callback veto and extension matching
must be supplied by the caller; absent services never become permissive defaults.
The library condition and bit/relation stages execute their shared implementations.
"""

from ow174.game.script import filter_bits, filter_callbacks, filter_condition

MASKS = ("m_84065FD1", "m_1E14CCA8", "m_4EA686DB",
         "m_27CF71D5", "m_66747E7D", "m_89831602")
SCRIPT_FILTERS = ("m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3")


def _boolean(value):
    if type(value) is not bool:
        raise ValueError("native filter service must return bool")
    return value


def evaluate(instance, config, target, *, services, watcher=None):
    """Match native stage order; engine objects retain pointer-identity semantics.

    services implements lookup/has_component/bits/source/observe/missing_target,
    relationship/observe_script_filters/script_filters/engine_filter and
    extension_filter. Only reached services are required. A bits result of None
    declares component25 absent. engine_filter returns the native uint32 enum;
    only value1 vetoes. The proved empty-rule path needs no registry answer.
    The extension service owns its component/collection/int
    conversion stages and must not be replaced with a guessed default.
    """
    if config is None:
        return True
    if type(config) is not dict or config.get("$") != "STUConfigVarFilter":
        raise ValueError("native game filter requires STUConfigVarFilter")
    if type(target) is not int or not 0 <= target <= 0xFFFFFFFF:
        raise ValueError("native filter candidate must be a uint32 identity")
    masks = tuple(filter_bits._uint64(config.get(name, 0)) for name in MASKS)
    candidate = services.lookup(target)
    if candidate is None:
        services.missing_target(instance, watcher, target)
        return False
    if masks[0] != 0x3FFFFFFF:
        present = _boolean(services.has_component(candidate, 97))
        if not present and any(masks):
            return False
    bits = services.bits(candidate)
    if bits is not None:
        filter_bits._uint64(bits)
        if watcher is not None:
            services.observe(candidate, 25, watcher)
    source = services.source()
    if source is not None:
        source_bits = services.bits(source)
        if source_bits is not None:
            filter_bits._uint64(source_bits)
            if watcher is not None:
                services.observe(source, 25, watcher)
    if any(masks) and not filter_bits.matches(
            bits, *masks, source_present=source is not None, same_entity=source is candidate,
            relationship=lambda spectator: services.relationship(source, candidate, spectator)):
        return False
    if not filter_condition.evaluate(instance, config.get("m_condition"), target, watcher=watcher):
        return False
    scripted = any(config.get(name) is not None for name in SCRIPT_FILTERS)
    if watcher is not None and scripted:
        services.observe_script_filters(candidate, watcher)
    if scripted and not _boolean(services.script_filters(instance, watcher, candidate, config)):
        return False
    veto = filter_callbacks.empty_result(config)
    if veto is NotImplemented:
        veto = services.engine_filter(candidate, config, instance)
    if type(veto) is not int or not 0 <= veto <= 0xFFFFFFFF:
        raise ValueError("native engine filter callback must return uint32")
    if veto == 1:
        return False
    if filter_condition.evaluate_boolean(instance, config.get("m_F61726FE"),
                                         default=False, watcher=watcher):
        return _boolean(services.extension_filter(instance, watcher, candidate, config))
    return True
