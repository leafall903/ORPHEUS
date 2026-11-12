"""C762F0 category publication decisions with explicit geometry/component services.

This does not supply an actor inventory, physical component decode or tree storage.
"""

from dataclasses import dataclass

from .target_scan import _key, _mask

CATEGORIES = (0x10000, 1, 0x40, 0x40000, 0x100000)
COMBINED_CATEGORY = 0x150041
MANAGER_SLOT = 0x41  # original BDC100 literal


def _u32(value):
    if type(value) is not int or not 0 <= value < 1 << 32:
        raise ValueError("category publication requires a native uint32")
    return value


def _call(callback, *args):
    if not callable(callback):
        raise ValueError("category publication service is unavailable")
    return callback(*args)


def _null(value):
    return value is None or (type(value) is int and value == 0)


@dataclass
class Version:
    current: int
    published: int


def publish(entity, flags, *, check_revision, has_component, bounds24, bounds4,
            version24, transform_bits, upsert):
    """Native priority/geometry/revision gates, then ordered categories and combined key."""
    if _u32(flags) & 4:
        return ()
    if type(check_revision) is not bool:
        raise ValueError("category revision check must be explicit")
    dynamic = _call(has_component, entity, 24)
    if type(dynamic) is not bool:
        raise ValueError("component24 availability is unknown")
    if not dynamic:
        static = _call(has_component, entity, 4)
        if type(static) is not bool:
            raise ValueError("component4 availability is unknown")
        if not static:
            return ()
    bounds = _call(bounds24 if dynamic else bounds4, entity)
    if bounds is NotImplemented:
        raise ValueError("category publication geometry is unknown")
    if bounds is None:
        return ()
    if dynamic and check_revision:
        version = _call(version24, entity)
        if not isinstance(version, Version):
            raise ValueError("component24 version is unknown")
        current, previous = _u32(version.current), _u32(version.published)
        if current == previous:
            return ()
        version.published = current  # original writes before mask/registry service calls
    bits = _mask(_call(transform_bits, entity))
    categories = tuple(category for category in CATEGORIES if bits & category == category)
    if categories:
        categories += (COMBINED_CATEGORY,)
    for category in categories:
        _call(upsert, entity, category, bounds, bits, not dynamic)
    return categories


def route_upsert(entity, category, bounds, bits, first_root, *, manager,
                 ensure_first, ensure_second, update_first, update_second):
    """C768C0 decoded manager/ensure/update routing; null ensure result skips the update."""
    _key(category)
    bits = _mask(bits)
    if type(first_root) is not bool:
        raise ValueError("category root family must be explicit")
    root_manager = _call(manager, entity, MANAGER_SLOT)
    if _null(root_manager) or root_manager is NotImplemented:
        raise ValueError("category root manager is unavailable")
    root = _call(ensure_first if first_root else ensure_second, root_manager, category, True)
    if root is NotImplemented:
        raise ValueError("category root ensure result is unknown")
    if _null(root):
        return False
    _call(update_first if first_root else update_second, root, entity, bounds, bits)
    return True


def filter_changed(entity, removal_bits, *, has_component, publish_now, remove):
    """C75000 publishes with revision check off, then removes missing category memberships."""
    bits = _mask(removal_bits)
    dynamic = _call(has_component, entity, 24)
    if type(dynamic) is not bool:
        raise ValueError("component24 availability is unknown")
    if not dynamic:
        static = _call(has_component, entity, 4)
        if type(static) is not bool:
            raise ValueError("component4 availability is unknown")
        if not static:
            return ()
    _call(publish_now, entity, False)
    missing = tuple(category for category in CATEGORIES if bits & category != category)
    if len(missing) == len(CATEGORIES):
        missing += (COMBINED_CATEGORY,)
    for category in missing:
        _call(remove, entity, category)
    return missing


def route_remove(entity, category, *, has_component, manager, find_first, find_second, erase):
    """C75A20 root routing and C75B20 null guard; find uses create=False."""
    _key(category)
    dynamic = _call(has_component, entity, 24)
    if type(dynamic) is not bool:
        raise ValueError("component24 availability is unknown")
    if not dynamic:
        static = _call(has_component, entity, 4)
        if type(static) is not bool:
            raise ValueError("component4 availability is unknown")
        if not static:
            return False
    root_manager = _call(manager, entity, MANAGER_SLOT)
    if _null(root_manager) or root_manager is NotImplemented:
        raise ValueError("category root manager is unavailable")
    root = _call(find_second if dynamic else find_first, root_manager, category, False)
    if root is NotImplemented:
        raise ValueError("category root find result is unknown")
    if _null(root):
        return False
    _call(erase, root, entity)
    return True
