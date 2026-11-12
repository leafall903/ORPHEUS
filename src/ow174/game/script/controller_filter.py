"""Explicit controller filter inputs; no automatic native attachment assumptions.

Providers own the live input values and provenance. A historical presence sample
can initialize a declared reconstruction, but is not a native lifetime guarantee.
Unknown reached payloads reject; body variables/health are never substituted.
"""

from ow174.game.script import expr, filter_bits

INPUT_FIELDS = ("component_masks", "current_bits", "replicated_bits", "primary_status",
                "health_status", "flags28", "provenance")


class ControllerFilterInputs:
    def __init__(self, owner, component_masks, current_bits, replicated_bits, *, provenance,
                 primary_status=None, health_status=None, flags28=None):
        if type(owner) is not int or not 0 <= owner <= 0xFFFFFFFF or not expr.valid_entity(owner):
            raise ValueError("controller filter inputs require a live native owner identity")
        if type(component_masks) not in (tuple, list):
            raise ValueError("controller filter inputs require explicit native mask words")
        self.owner = owner
        self.component_masks = tuple(component_masks)
        self.current_bits = current_bits
        self.replicated_bits = replicated_bits
        self.primary_status = primary_status
        self.health_status = health_status
        self.flags28 = flags28
        self.provenance = provenance
        self.revision = 0
        self.retired = False
        self.bound_host = None
        self.validate()

    def validate(self):
        filter_bits.has_component(self.component_masks, 0)
        filter_bits._uint64(self.current_bits)
        if self.replicated_bits is not None:
            filter_bits._uint64(self.replicated_bits)
            if not self.has_component(26):
                raise ValueError("replicated filter payload requires known component26 presence")
        for value in (self.primary_status, self.health_status):
            if value is not None and type(value) is not bool:
                raise ValueError("controller status payload must be bool or unavailable")
        if (self.flags28 is not None and
                (type(self.flags28) is not int or not 0 <= self.flags28 <= 0xFFFFFFFF)):
            raise ValueError("controller component28 flags must be uint32 or unavailable")
        if type(self.provenance) is not str or not self.provenance.strip():
            raise ValueError("controller filter inputs require explicit provenance")
        if self.bound_host is not None and not self.has_component(47):
            raise ValueError("bound controller filter inputs contradict the live component47 VM")

    def has_component(self, identifier):
        if self.retired:
            raise NotImplementedError("controller filter inputs are retired")
        return filter_bits.has_component(self.component_masks, identifier)

    def bits(self):
        self.validate()
        if not self.has_component(25):
            return None
        replicated = None
        if self.has_component(26):
            if self.replicated_bits is None:
                raise NotImplementedError("controller component26 payload is unavailable")
            replicated = self.replicated_bits
        status = filter_bits.status10_from_masks(
            self.component_masks,
            primary=(lambda: self.primary_status) if self.primary_status is not None else None,
            health=(lambda: self.health_status) if self.health_status is not None else None,
            flags=(lambda: self.flags28) if self.flags28 is not None else None)
        return filter_bits.project(self.current_bits, replicated, status10=status)

    def update(self, **changes):
        if self.retired:
            raise ValueError("retired controller filter inputs cannot update")
        if changes.keys() - set(INPUT_FIELDS):
            raise ValueError("unknown controller filter input update")
        values = {name: getattr(self, name) for name in INPUT_FIELDS}
        values.update(changes)
        checked = ControllerFilterInputs(self.owner, **values)
        if self.bound_host is not None and not checked.has_component(47):
            raise ValueError("bound controller filter inputs contradict the live component47 VM")
        for name in INPUT_FIELDS:
            setattr(self, name, getattr(checked, name))
        self.revision += 1

    def retire(self):
        if not self.retired:
            self.retired = True
            self.bound_host = None
            self.revision += 1
