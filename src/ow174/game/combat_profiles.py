"""Explicit external combat inputs for new matches."""

from dataclasses import dataclass

from ow174.game import dragon as dragon_module
from ow174.game import sonic as sonic_module


@dataclass(frozen=True)
class Profiles:
    sonic: sonic_module.Profile | None = None
    dragon: dragon_module.Profile | None = None
    projectile_absorption: frozenset[int] | None = None

    def configure(self, fight):
        if self.sonic is not None:
            fight.configure_sonic(self.sonic)
        if self.dragon is not None:
            fight.configure_dragonstrike(self.dragon)
        if self.projectile_absorption is not None:
            fight.matrix_definitions = self.projectile_absorption
