"""Explicit external Hanzo host profiles; no environment or runtime auto-loading."""

from dataclasses import dataclass

from ow174.game import dragon as dragon_module
from ow174.game import sonic as sonic_module


@dataclass(frozen=True)
class Profiles:
    sonic: sonic_module.Profile | None = None
    dragon: dragon_module.Profile | None = None

    def configure(self, fight):
        if self.sonic is not None:
            fight.configure_sonic(self.sonic)
        if self.dragon is not None:
            fight.configure_dragonstrike(self.dragon)
