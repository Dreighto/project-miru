"""Tests for the shadow-loop priority queue."""

from __future__ import annotations

from services.shadow_loop.priority_queue import PriorityQueue


def test_add_then_next_returns_in_fifo_order():
    q = PriorityQueue()
    q.add("OP01-001", "OP01-001", "first")
    q.add("OP01-002", "OP01-002", "second")
    q.add("OP01-003", "OP01-003", "third")
    assert q.next() == ("OP01-001", "OP01-001")
    assert q.next() == ("OP01-002", "OP01-002")
    assert q.next() == ("OP01-003", "OP01-003")
    assert q.next() is None


def test_add_dedupes_on_canonical_and_print_id():
    q = PriorityQueue()
    q.add("OP01-001", "OP01-001", "first")
    q.add("OP01-001", "OP01-001", "second-same-key")
    assert len(q) == 1
    assert q.next() == ("OP01-001", "OP01-001")
    assert q.next() is None


def test_same_canonical_but_different_print_is_not_a_dupe():
    q = PriorityQueue()
    q.add("OP01-001", "OP01-001", "base")
    q.add("OP01-001", "OP01-001_p1", "parallel")
    assert len(q) == 2


def test_empty_next_returns_none():
    q = PriorityQueue()
    assert q.is_empty()
    assert q.next() is None


def test_re_add_after_pop_works():
    """The dedupe set should clear when an item is popped so it can be re-queued."""
    q = PriorityQueue()
    q.add("OP01-001", "OP01-001", "first")
    q.next()
    q.add("OP01-001", "OP01-001", "second")
    assert q.next() == ("OP01-001", "OP01-001")
