"""Type definitions for Anduril.

The data structure has two node types (state nodes and the mapping
table entries), four fundamental types (NodeID, StateID, Action,
NULL_STATE), and one tier enum. All IDs are 64-bit ints. Once
issued, an ID is never reused — this is the Bw-tree invariant
that prevents ABA in lock-free reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# A unique, never-reused identifier for a node in the DAG.
# Type alias for clarity in signatures.
NodeID = int

# A "null" or absent node reference. Distinct from 0 (the first
# valid ID) so that "no node" is unambiguous.
NULL_NODE: NodeID = -1

# A "null" or absent state root. Same convention.
NULL_STATE: NodeID = -1


class Tier(Enum):
    """Storage tier for a state node.

    The tier is recorded in the data structure itself, not at the
    storage layer. The lookup operation routes according to tier.

    - HOT: full state in HBM (µs latency)
    - WARM: full state in CPU DRAM, summarized on demand (ms)
    - COLD: full state in NVMe SSD, summary in DRAM (10s of ms)
    """
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


@dataclass
class Action:
    """An action that produced a transition between states.

    `kind` is a free-form string ("tool_call", "llm_response",
    "user_input", "merge", etc.). `payload` holds the action data.
    For tool calls: tool name, args, result. For LLM responses:
    the model output. For merges: the two parent state IDs.

    The action is the *label* on the edge, not the edge itself.
    """
    kind: str
    payload: Any = None
    metadata: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Action(kind={self.kind!r}, payload={self.payload!r})"


# A "trivial" action for cases where no real action occurred
# (e.g., initialization, snapshots).
NOOP_ACTION = Action(kind="noop", payload=None)
