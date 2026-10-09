"""Anduril: a persistent DAG for agent state with O(1) branching and rewind.

A directed acyclic graph of immutable state nodes with structural
sharing via reference counting. Mutations return new roots; old
roots remain valid until their refcount drops to 0 and all live
readers finish.

Public API:
    Anduril        — the persistent DAG class
    StateNode      — a node in the DAG
    Action         — the label on an edge
    Tier           — HOT / WARM / COLD storage tier
    NodeID, NULL_NODE, NULL_STATE — type aliases and sentinels
    AndurilError, EmptyStateError, OutOfRangeError, CycleError,
        UnknownNodeError — error types
"""
from .core import (
    Anduril,
    AndurilError,
    CycleError,
    EmptyStateError,
    OutOfRangeError,
    StateNode,
    UnknownNodeError,
)
from .types import (
    Action,
    NOOP_ACTION,
    NodeID,
    NULL_NODE,
    NULL_STATE,
    Tier,
)

__version__ = "0.0.1.dev0"

__all__ = [
    "Anduril",
    "StateNode",
    "Action",
    "Tier",
    "NodeID",
    "NULL_NODE",
    "NULL_STATE",
    "NOOP_ACTION",
    "AndurilError",
    "EmptyStateError",
    "OutOfRangeError",
    "CycleError",
    "UnknownNodeError",
]
