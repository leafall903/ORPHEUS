"""104319 D0DA20 orchestration over explicitly supplied engine services.

The full coordinator requires explicit engine services. The scoped manual 13C2
backend evaluates its source-independent path with bound controller inputs and
reconstructed native-expression polling. General lookup, observers, script
filters and keyed rules require separate services.
"""

from ow174.game.script import filter_bits, filter_callbacks, filter_condition

MASKS = ("m_84065FD1", "m_1E14CCA8", "m_4EA686DB",
         "m_27CF71D5", "m_66747E7D", "m_89831602")
SCRIPT_FILTERS = ("m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3")
SOURCE_INDEPENDENT_FIELDS = frozenset(("$", "m_EE729DCB", "m_condition", "m_93FA1642",
                                     "m_C1903771", "m_F61726FE", "m_B40362FC", *MASKS, *SCRIPT_FILTERS))


def source_independent(config):
    """Prove no source relationship, script-filter, registry or extension input affects the result."""
    if (type(config) is not dict or config.get("$") != "STUConfigVarFilter"
            or config.keys() - SOURCE_INDEPENDENT_FIELDS or config.get("m_EE729DCB", 0) != 0):
        return False
    masks = tuple(filter_bits._uint64(config.get(name, 0)) for name in MASKS)
    return (not any(masks[3:]) and filter_callbacks.empty_result(config) == 2
            and all(config.get(name) is None for name in (*SCRIPT_FILTERS, "m_F61726FE", "m_B40362FC")))


def evaluate_target(instance, config, target, *, inputs, watcher=None):
    """Proved source-independent value path with explicit live target inputs.

    Native component observer registration is not reconstructed by this path;
    the VM's existing per-frame native polling supplies reevaluation. This is
    neither a general filter registry nor a complete native event backend.
    """
    from ow174.game.script.controller_filter import ControllerFilterInputs

    if not source_independent(config):
        return NotImplemented
    if type(inputs) is not ControllerFilterInputs or inputs.retired or inputs.owner != target:
        raise NotImplementedError("live target controller filter inputs are unavailable")
    revision = inputs.revision
    masks = tuple(config.get(name, 0) for name in MASKS)
    if masks[0] != 0x3FFFFFFF and not inputs.has_component(97) and any(masks):
        return False
    bits = inputs.bits()
    result = (not any(masks) or filter_bits.matches(bits, *masks))
    if result:
        result = filter_condition.evaluate(instance, config.get("m_condition"), target, watcher=watcher)
    if inputs.retired or inputs.revision != revision:
        raise NotImplementedError("controller filter inputs changed during evaluation")
    return result


def evaluate_native(evaluation, config):
    if config.get("$") != "STU_AC829876":
        return NotImplemented
    context = evaluation.instance.component.world.entity_filter_context(evaluation.instance.component.entity)
    if context is NotImplemented:
        return NotImplemented
    from ow174.game.script.npc_context import NpcContext

    if type(context) is not NpcContext:
        raise ValueError("unknown native filter context")
    return context.native_filter(config, evaluation.instance, watcher=evaluation.watcher)


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
