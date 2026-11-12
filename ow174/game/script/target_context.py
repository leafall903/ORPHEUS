"""C75C10 temporal context from explicit component24 owner and component25 flags."""

from .target_aggregate import RootQuery
from .target_spatial import TemporalQuery, temporal_tick


def temporal_context(query, *, first_seconds, second_seconds, component24_owner, status25_flags):
    """Build the defined actor-present branch; missing status25 is not a zero default."""
    if not isinstance(query, RootQuery) or component24_owner is NotImplemented:
        raise ValueError("explicit query/component24 owner binding is required")
    if type(status25_flags) is not int or not 0 <= status25_flags < 1 << 32:
        raise ValueError("explicit component25 flags are required")
    query = query.prepared(0)
    return TemporalQuery((*query.center[:3], query.radius), query.any_bits, query.excluded_bits,
                         status25_flags & 0x07800000, temporal_tick(first_seconds),
                         temporal_tick(second_seconds), component24_owner)
