"""Read every statescript chunk the runtime tests queue for a game client, the way the client reads it.

    python -B -m tests.chunk_sweep                    every runtime test
    python -B -m tests.chunk_sweep discover -s tests/runtime -p "test_game_bots.py"

Run it from the repository root with the runtime data in place. The tests run as usual with
server.Client._queue_current_entity watched: each client keeps a model of every entity's statescript
(decode.ClientBody) and applies its chunks in the order they are queued. A chunk the client would crash
on, refuse or misread is listed with the test that queued it, and the exit code is 1.

Not read, and counted as unchecked: a fragment of a split mode frame, a state class without a verified
codec, a graph the bundle lacks (a test's own), a delta whose base was never queued, and the deltas
after any of these on the same entity. A chunk a test hands to a mock client is not seen at all.
"""

import sys
import unittest
import weakref
from collections import Counter
from contextlib import contextmanager

from ow174.game import server, world
from ow174.game.bits import BitReader
from ow174.game.script import decode

# DecodeError texts for what the decoder cannot read, as opposed to what the client would misread.
UNREAD = ("fragments", "no codec for", "the payload of")


class Report:
    def __init__(self) -> None:
        self.models = weakref.WeakKeyDictionary()  # client -> {entity: ClientBody, None once unreadable}
        self.counts = Counter()
        self.failures: list[tuple[str, str, int, str]] = []  # (test, kind, entity, what)
        self.test = ""

    def read(self, client, item) -> None:
        """One queued world.EntityUpdate, as that client would take it."""
        models = self.models.setdefault(client, {})
        if item.op in (world.OP_CREATE, world.OP_DESTROY):
            models.pop(item.entity, None)
        if item.chunk is None:
            return
        first = decode.w_u32(BitReader(item.chunk.getvalue()))
        if item.entity not in models or (models[item.entity] is None and first == 0):
            models[item.entity] = _model(client, item.entity)  # a full frame makes it readable again
        model = models[item.entity]
        if model is None or first > model.applied + 1:
            self.counts["unchecked"] += 1
            return
        if item.chunk_last <= model.applied:
            self.counts["repeated"] += 1
            return
        kind, what = _apply(model, item)
        self.counts[kind] += 1
        if kind != "ok":
            models[item.entity] = None
        if kind not in ("ok", "unchecked"):
            self.failures.append((self.test, kind, item.entity, what))

    def summary(self) -> str:
        lines = ["chunks: " + ", ".join(f"{count} {kind}" for kind, count in sorted(self.counts.items()))]
        grouped: dict[tuple[str, str], list[str]] = {}
        for test, kind, _entity, what in self.failures:
            grouped.setdefault((kind, what), []).append(test)
        for (kind, what), tests in sorted(grouped.items()):
            lines.append(f"{kind} x{len(tests)}: {what}")
            lines.extend(f"    {test}" for test in sorted(set(tests))[:5])
        return "\n".join(lines)


def _model(client, entity: int) -> decode.ClientBody:
    """The entity's statescript before any chunk: a player's body has its hero's own graphs already."""
    match = getattr(getattr(client, "player", None), "match", None)
    for player in getattr(match, "players", ()):
        if getattr(player, "has_body", False) and player.body == entity:
            try:
                return decode.ClientBody.for_hero(player.hero.guid)
            except StopIteration:  # a hero without a body record in the bundle
                break
    return decode.ClientBody()


def _apply(model: decode.ClientBody, item) -> tuple[str, str]:
    try:
        model.apply(item)
    except decode.ClientCrash as error:
        return "crash", str(error)
    except decode.FrameRefused as error:
        return "refused", str(error)
    except decode.DecodeError as error:
        return ("unchecked" if str(error).startswith(UNREAD) else "misread"), str(error)
    except AttributeError as error:  # decode.graph_of gave None: a graph the bundle lacks, as a test's own
        return "unchecked", str(error)
    except Exception as error:  # noqa: BLE001 - the decoder fell over: not what it expects either
        return "misread", f"{type(error).__name__}: {error}"
    return "ok", ""


@contextmanager
def watch():
    """Read every chunk queued while the block runs; yields the Report."""
    report = Report()
    queue, run = server.Client._queue_current_entity, unittest.TestCase.run

    def queued(client, item):
        report.read(client, item)
        return queue(client, item)

    def ran(test, result=None):
        report.test = test.id()
        return run(test, result)

    server.Client._queue_current_entity, unittest.TestCase.run = queued, ran
    try:
        yield report
    finally:
        server.Client._queue_current_entity, unittest.TestCase.run = queue, run


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv) or ["discover", "-s", "tests/runtime"]
    with watch() as report:
        unittest.main(module=None, argv=["chunk_sweep", *arguments], exit=False)
    print(report.summary())
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
