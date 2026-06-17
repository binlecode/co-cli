"""Queue-control core for /queue list/clear/pop.

Operates on a `deque[str]` by reference and prints via the module-level
`console`. Both entry points (idle dispatch and mid-turn bypass) call this
same core so the two paths cannot diverge. 1-based indices in user surface
(C4). No `Frontend` parameter — list-style commands print through console
(matches help.py / tasks.py — Phase 2 C6).
"""

from __future__ import annotations

from collections import deque

from co_cli.display.core import console, make_table

_PREVIEW_BUDGET = 60


def _truncate(text: str, budget: int = _PREVIEW_BUDGET) -> str:
    text = text.replace("\n", " ")
    if len(text) <= budget:
        return text
    return text[: budget - 1] + "…"


def run_queue_control(queue: deque[str] | None, args: str) -> None:
    """Run `list` (no args), `clear`, or `pop [n]` against the queue.

    Prints results via `console`. 1-based indices in the user surface; bad
    indices or unknown subcommands surface as a usage error and leave the
    queue unchanged.
    """
    if queue is None:
        console.print("[bold red]/queue is only available in the REPL.[/bold red]")
        return

    parts = args.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "" or sub == "list":
        _list(queue)
        return
    if sub == "clear":
        _clear(queue)
        return
    if sub == "pop":
        _pop(queue, rest)
        return

    console.print(f"[bold red]Usage:[/bold red] /queue [list|clear|pop [n]] (got: {sub!r})")


def _list(queue: deque[str]) -> None:
    if not queue:
        console.print("[dim]Queue is empty.[/dim]")
        return
    table = make_table("#", "Item")
    for i, item in enumerate(queue, start=1):
        table.add_row(str(i), _truncate(item))
    console.print(table)


def _clear(queue: deque[str]) -> None:
    n = len(queue)
    queue.clear()
    if n == 0:
        console.print("[dim]Queue was already empty.[/dim]")
    elif n == 1:
        console.print("Dropped 1 queued item.")
    else:
        console.print(f"Dropped {n} queued items.")


def _pop(queue: deque[str], rest: str) -> None:
    if not queue:
        console.print("[dim]Queue is empty.[/dim]")
        return

    arg = rest.strip()
    if arg == "":
        index_1 = len(queue)
    else:
        try:
            index_1 = int(arg)
        except ValueError:
            console.print(
                f"[bold red]Usage:[/bold red] /queue pop [n] — n must be a positive integer (got: {arg!r})"
            )
            return

    if index_1 < 1 or index_1 > len(queue):
        console.print(
            f"[bold red]Usage:[/bold red] /queue pop {index_1} — index out of range (queue depth {len(queue)})"
        )
        return

    items = list(queue)
    dropped = items.pop(index_1 - 1)
    queue.clear()
    queue.extend(items)
    console.print(f"Dropped #{index_1}: {_truncate(dropped)!r}")
