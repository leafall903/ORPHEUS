# Client research notes

Findings about the retail **1.74.0.0.104319** client that came out of a separate offline
restoration effort on the same build. That effort used its own lobby and game service, so each
note is rewritten here around what the client does and expects, not around that service's code.

These notes add to the [research evidence ledger](../RESEARCH_EVIDENCE.txt). They do not replace
the [verification record](../STATE.md): a result seen in that other service is evidence about the
client, not acceptance of ORPHEUS.

## How to read a note

- **Seen live** means the original client was run against a local service and the result was
  observed on screen or read from the client's memory without writing to it.
- **Read from data** means the claim comes from the client's own statescript graphs or other
  assets of the installation.
- **Read from code** means the claim comes from disassembly of the client executable. Such
  claims name the function.
- **Compared with ORPHEUS** sections were written by reading the source at `main` `d52e72f`.
  Nothing in them was tested against ORPHEUS in the client.
- **Seen live against ORPHEUS** sections record runs of the original client against this
  server.
- A graph is named by the low 16 bits of its GUID in hex, as elsewhere in this repository
  (`01CF`). `#392` is a statescript variable, `st108` a state index, `0765.025` a game message.

## Addresses

Code addresses are image-relative offsets: the address in the loaded executable minus its image
base. Source comments in this repository cite the same functions as absolute addresses with image
base `0x7FF788F10000`, so offset `0xA0D4F0` here is `0x7FF78991D4F0` there.

## Notes

| Note | What it settles | What ORPHEUS can take from it |
| --- | --- | --- |
| [Kill feed](kill-feed.md) | The client-side gates between a body's death message and a drawn kill feed line. | The login settings left the gating option at zero for a new profile. They now default it on. A bot kill needs a pack with the bot bodies' statescript, and the bot's chunk must not share a datagram with reliable messages: the client loses it. With both, the line draws. |
| [Bot nameplates and hit feedback](bot-nameplates-and-hit-feedback.md) | What the overhead name and health widget, the hit marker and the elimination notice each need from the server. | Bots now have controllers and the elimination notice names them. With a pack that holds the bot bodies the overhead name and health bar draw. A confirmation of the hit flag is pending. |
| [Tab board](tab-board.md) | Why the scoreboard key does nothing, and the layout of the board's numbers. | The board opens with names, numbers and medals in the Practice Range, Deathmatch and Team Deathmatch. Control and the other modes are held back until seen with an elimination; the objective modes need their phase states. |
| [TrackTargets](track-targets.md) | How the client's target search picks its entities: the filter's masks, the range and cone, sight, the order by score and the result. | Guardian Angel and the Caduceus Staff find their ally on the server. Hack, and the same searches of other heroes, are not answered yet. |
| [Shot origin and client position](shot-origin-and-client-position.md) | Per-hero eye heights, the click aim and view delay, and how to read the client's own position. | Eye heights for the other heroes, and a way to measure drift between server and client. |
| [Remote weapon fire](remote-weapon-fire.md) | How a client draws another body's shots: shot modes, the shot record, variable bindings in remote frames. | Remote frames write a binding's state with the wrong width. |

## Not ported yet

The source effort has more notes: movement abilities, the statescript payloads of individual
states, map collision props, mode graphs and match flow, menus and progression, and the per-hero
weapon surveys. They will be added in the same form. Screenshots, logs and captures that back the
live results stay in local storage, as the contribution guide asks.
