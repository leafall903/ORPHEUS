"""104319 BE1E80 base tag union from two explicitly resolved resources.

This is only the base vector step. It neither resolves entity-definition
references nor constructs complete effective Tags: dynamic expressions, scene
instance additions, registry notifications and later lifetimes are separate.
Native comparison uses preallocated storage with successful copies.
"""


def base_tags(owner_resource: tuple, descriptor_resource: tuple | None) -> tuple:
    """Preserve native order and deduplicate owner then optional descriptor tags.

    None declares the descriptor resource absent. Owner resource availability
    must be resolved by the caller; an empty tuple is a known empty resource.
    Values are native uint32 keys, including zero. Unknown inputs are rejected.
    """
    sources = (owner_resource,) if descriptor_resource is None else (owner_resource, descriptor_resource)
    for values in sources:
        if type(values) is not tuple or any(type(key) is not int or not 0 <= key <= 0xFFFFFFFF
                                            for key in values):
            raise ValueError('expected resolved tuples of native uint32 tag keys')
    return tuple(dict.fromkeys(key for values in sources for key in values))
