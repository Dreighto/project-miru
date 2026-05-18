"""In-memory priority queue for the shadow loop.

The queue holds (canonical_code, print_id, reason) tuples. PR-A keeps it
simple: FIFO. PR-C will extend with priority ordering (low-confidence and
stale rows surface first).

The queue is not persistent — on restart, the loop reconstructs it from
the learning pool state (rows missing or stale get re-added). This avoids
adding a separate persistence layer; the pool IS the source of truth.

Matches the interface contract documented in PRO-911:

    class PriorityQueue:
        def add(self, canonical_code: str, print_id: str, reason: str) -> None: ...
        def next(self) -> tuple[str, str] | None: ...

PRO-908 PR-A.
"""

from __future__ import annotations

from collections import deque


class PriorityQueue:
    def __init__(self) -> None:
        self._items: deque[tuple[str, str, str]] = deque()
        self._seen: set[tuple[str, str]] = set()

    def add(self, canonical_code: str, print_id: str, reason: str) -> None:
        """Add an item. No-ops if (canonical_code, print_id) is already in the queue."""
        key = (canonical_code, print_id)
        if key in self._seen:
            return
        self._items.append((canonical_code, print_id, reason))
        self._seen.add(key)

    def next(self) -> tuple[str, str] | None:
        """Pop the next (canonical_code, print_id). Returns None if empty."""
        if not self._items:
            return None
        canonical_code, print_id, _reason = self._items.popleft()
        self._seen.discard((canonical_code, print_id))
        return (canonical_code, print_id)

    def __len__(self) -> int:
        return len(self._items)

    def is_empty(self) -> bool:
        return not self._items
