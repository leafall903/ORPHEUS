"""104319 B96D3A98's predicate on an explicitly resolved controller entity.

CC9F77..95 uses182C470 to test component75 presence and component92 absence.
The entity/component43/DA6910 selection and observation belong to the host.
"""

CLASS = "STU_B96D3A98"


def matches(components):
    """None declares no selected entity; an unknown selection must not use None."""
    if components is None:
        return False
    if (type(components) is not frozenset
            or any(type(index) is not int or not 0 <= index < 256 for index in components)):
        raise ValueError("controller query needs an explicit native component set")
    return 75 in components and 92 not in components
