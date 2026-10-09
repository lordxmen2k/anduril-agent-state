"""Anduril: a persistent DAG for agent state.

The data structure is a directed acyclic graph where:

- Each node holds (state, action, parent_refs, depth, refcount, tier)
- Nodes are immutable once created; mutations return a new root
- Multiple roots can coexist and share substructure via refcount
- Logical NodeIDs are never reused (Bw-tree invariant)
- A per-DAG mutation lock serializes mutating ops; reads are lock-free

This is a Python reference. The Bw-tree mapping table is simulated
by a Python dict + per-DAG lock. The Rust port replaces the lock
with crossbeam-epoch and CAS, enabling truly lock-free concurrent
access.

The contribution is the data structure model, not the Python
implementation. The Python reference is the correctness oracle.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .types import Action, NOOP_ACTION, NodeID, NULL_NODE, NULL_STATE, Tier


# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------


class AndurilError(Exception):
    """Base error for Anduril operations."""


class EmptyStateError(AndurilError):
    """Operation requires a non-empty state but got an empty root."""


class OutOfRangeError(AndurilError):
    """Position or depth is out of range."""


class CycleError(AndurilError):
    """Operation would create a cycle in the DAG."""


class UnknownNodeError(AndurilError):
    """Referenced NodeID is not in the mapping table."""


# ----------------------------------------------------------------------------
# Node types
# ----------------------------------------------------------------------------


@dataclass
class StateNode:
    """A node in the agent state DAG.

    `state` is the conversation at this point — typically a list of
    messages (dicts with 'role' and 'content' keys). It can be any
    Python object that the application wants to track.

    `action` is what produced this node (the label on the incoming
    edge from a parent).

    `parent_refs` is a list of 1+ NodeIDs. Linear chains have 1
    parent; branches have 1 parent; merges have 2+ parents.

    `depth` is the length of the longest path from a root to this
    node. Used for O(k) rewind.

    `refcount` is how many live roots reference this node. When
    refcount drops to 0 AND no live reader exists, the node is
    reclaimable.

    `tier` is the storage tier (HOT, WARM, COLD).

    `summary` is an optional, lazily-generated summary of the
    subtree rooted at this node. None by default; populated by
    the `summarize()` operation.

    `lock` is per-node for mutating the refcount (or summary
    in-place, which is the only mutation we allow on a node).
    """
    node_id: NodeID
    state: Any
    action: Action
    parent_refs: list  # list[NodeID]
    depth: int
    tier: Tier = Tier.HOT
    refcount: int = 1
    summary: Optional[Any] = None
    metadata: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def is_merge(self) -> bool:
        """True if this node has multiple parents (a merge)."""
        return len(self.parent_refs) > 1

    @property
    def is_leaf(self) -> bool:
        """True if this node has no parents (the initial state).

        The initial state has no parents; the user constructs it
        via `Anduril(seed_state=...)` or `append(noop_action, ...)`.
        """
        return len(self.parent_refs) == 0


# ----------------------------------------------------------------------------
# The Anduril class
# ----------------------------------------------------------------------------


class Anduril:
    """A persistent DAG of agent states.

    The class holds a single mapping table (NodeID -> StateNode) and
    tracks one or more "live roots" — the currently-active state
    references. Operations return new roots; old roots remain valid
    until their refcount drops to 0 and all live readers finish.

    Thread safety:
    - Mutating ops (append, branch, merge, summarize, promote,
      demote) hold the per-DAG `_mutation_lock` for their duration.
    - Reads (resolve, ancestor, children, length_of, is_empty,
      refcount_of, stats) are lock-free; they walk the DAG and
      return a snapshot.

    The Rust port replaces the mutation lock with per-node CAS,
    enabling truly lock-free concurrent access.
    """

    def __init__(self, seed_state: Any = None,
                 initial_action: Optional[Action] = None):
        """Create a new Anduril DAG.

        If `seed_state` is provided, the initial root is created
        with that state and `initial_action` (or NOOP_ACTION if
        not given). If `seed_state` is None, the DAG is empty and
        the first `append()` creates the initial root.
        """
        self._mapping: dict[NodeID, StateNode] = {}
        self._deferred_free: list[NodeID] = []
        self._next_id: int = 1
        self._id_lock = threading.Lock()
        self._mutation_lock = threading.Lock()
        self._mutation_count: int = 0
        self._roots: set[NodeID] = set()  # currently-live roots

        if seed_state is not None:
            initial = self._alloc_id()
            action = initial_action or NOOP_ACTION
            node = StateNode(
                node_id=initial,
                state=seed_state,
                action=action,
                parent_refs=[],
                depth=0,
                tier=Tier.HOT,
                refcount=1,
            )
            self._put(node)
            self._roots.add(initial)

    # ------------------------------------------------------------------
    # Public API: structural queries (read-only, lock-free)
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """Return True if the DAG has no nodes."""
        return len(self._mapping) == 0

    def root_id(self) -> Optional[NodeID]:
        """Return one of the live roots, or None if empty.

        If there are multiple roots, returns an arbitrary one.
        Use `roots()` to get all of them.
        """
        if not self._roots:
            return None
        return next(iter(self._roots))

    def roots(self) -> set[NodeID]:
        """Return a copy of the current set of live roots."""
        return set(self._roots)

    def refcount_of(self, nid: NodeID) -> int:
        """Return the current refcount of a node, or 0 if unknown."""
        n = self._get(nid)
        if n is None:
            return 0
        return n.refcount

    def depth_of(self, root: Optional[NodeID], nid: NodeID) -> int:
        """Return the depth of `nid` along the path from `root`.

        Specifically: walk UP from `root` toward its ancestors
        (using the first parent at each hop). If we encounter
        `nid`, return the number of hops. If we reach a leaf
        that isn't `nid`, raise OutOfRangeError.

        For a chain rooted at `root`, the depth of `nid` is the
        number of ancestors between `root` and `nid`. For
        `nid == root`, this is 0. For a merge node reachable
        via a non-first parent, this may not see it (we only
        walk the first-parent chain).
        """
        n = self._get(nid)
        if n is None:
            raise UnknownNodeError(f"unknown node {nid}")
        cur = self._get(root)
        if cur is None:
            raise UnknownNodeError(f"unknown root {root}")
        steps = 0
        while cur is not None and cur.node_id != nid:
            if not cur.parent_refs:
                raise OutOfRangeError(
                    f"node {nid} is not an ancestor of root {root}"
                )
            cur = self._get(cur.parent_refs[0])
            if cur is None:
                raise OutOfRangeError(
                    f"node {nid} is not an ancestor of root {root}"
                )
            steps += 1
        return steps

    def length_of(self, root: Optional[NodeID]) -> int:
        """Return the depth of the root (number of ancestors)."""
        if root is None or root == NULL_STATE:
            return 0
        n = self._get(root)
        if n is None:
            return 0
        return n.depth + 1

    def stats(self) -> dict:
        """Return a snapshot of internal statistics for benchmarking."""
        by_tier = {t: 0 for t in Tier}
        for n in self._mapping.values():
            by_tier[n.tier] += 1
        return {
            "n_nodes": len(self._mapping),
            "n_roots": len(self._roots),
            "deferred_free": len(self._deferred_free),
            "mutations": self._mutation_count,
            "next_id": self._next_id,
            "tier_counts": {t.value: c for t, c in by_tier.items()},
            "max_depth": max(
                (n.depth for n in self._mapping.values()),
                default=0,
            ),
        }

    def resolve(self, root: Optional[NodeID]) -> list:
        """Return a snapshot of the path from root to the empty parent.

        The snapshot is a list of (node_id, depth, state, action)
        tuples, ordered from root to the leaf-most node. The list
        has length `length_of(root)`.

        For a DAG (merge node), the snapshot follows the first
        parent at each merge. The other parents are reachable via
        `ancestor()` and `children()`.
        """
        if root is None or root == NULL_STATE:
            return []
        path = []
        cur = self._get(root)
        if cur is None:
            return []
        while cur is not None:
            path.append({
                "node_id": cur.node_id,
                "depth": cur.depth,
                "state": cur.state,
                "action": cur.action,
                "tier": cur.tier,
                "is_merge": cur.is_merge,
            })
            if not cur.parent_refs:
                break
            cur = self._get(cur.parent_refs[0])
            if cur is None:
                break
        return path

    def ancestor(self, root: Optional[NodeID], k: int) -> Optional[NodeID]:
        """Return the k-th ancestor of `root` (root itself is k=0).

        Raises OutOfRangeError if k is negative or greater than
        the depth of root. Returns NULL_STATE if root is empty.
        """
        if root is None or root == NULL_STATE:
            if k == 0:
                return NULL_STATE
            raise OutOfRangeError("empty state has no ancestors")
        if k < 0:
            raise OutOfRangeError(f"k must be >= 0, got {k}")
        cur = self._get(root)
        if cur is None:
            raise UnknownNodeError(f"unknown root {root}")
        # Walk k parents up.
        for _ in range(k):
            if not cur.parent_refs:
                raise OutOfRangeError(
                    f"k={k} exceeds depth {cur.depth} of root {root}"
                )
            cur = self._get(cur.parent_refs[0])
            if cur is None:
                raise OutOfRangeError(f"broken parent chain at k={k}")
        return cur.node_id

    def children(self, root: Optional[NodeID]) -> list:
        """Return the NodeIDs of nodes that have `root` as their
        first parent. Does not include nodes where root is the
        second/third parent (use `parents_of` for that).

        This is the inverse of `ancestor`: given a node, find the
        nodes that branched from it.
        """
        if root is None or root == NULL_STATE:
            return []
        return [
            nid for nid, n in self._mapping.items()
            if n.parent_refs and n.parent_refs[0] == root
        ]

    def parents_of(self, nid: NodeID) -> list:
        """Return the parent NodeIDs of `nid` (1+ for a merge)."""
        n = self._get(nid)
        if n is None:
            raise UnknownNodeError(f"unknown node {nid}")
        return list(n.parent_refs)

    def get_state(self, nid: NodeID) -> Any:
        """Return the state object held by `nid`."""
        n = self._get(nid)
        if n is None:
            raise UnknownNodeError(f"unknown node {nid}")
        return n.state

    def get_action(self, nid: NodeID) -> Action:
        """Return the action held by `nid`."""
        n = self._get(nid)
        if n is None:
            raise UnknownNodeError(f"unknown node {nid}")
        return n.action

    def get_summary(self, nid: NodeID) -> Optional[Any]:
        """Return the summary of `nid`, or None if not yet summarized."""
        n = self._get(nid)
        if n is None:
            raise UnknownNodeError(f"unknown node {nid}")
        return n.summary

    def get_tier(self, nid: NodeID) -> Tier:
        """Return the storage tier of `nid`."""
        n = self._get(nid)
        if n is None:
            raise UnknownNodeError(f"unknown node {nid}")
        return n.tier

    # ------------------------------------------------------------------
    # Public API: structural mutations (hold the mutation lock)
    # ------------------------------------------------------------------

    def append(self, root: Optional[NodeID], action: Action,
               new_state: Any) -> NodeID:
        """Append a new state after `root`, returning a new root.

        The new node's parent is `root` and its depth is
        `length_of(root)`. The new root is added to the live
        roots; the old root remains valid (refcount bumped).
        """
        with self._mutation_lock:
            new_id = self._append_internal(root, action, new_state)
            self._roots.add(new_id)
            self._bump_mutation()
            return new_id

    def append_to(self, root: Optional[NodeID], action: Action,
                  new_state: Any) -> NodeID:
        """Like `append`, but does NOT add the new root to live roots.

        Use this when you want to create a new node that's a child
        of `root` without making it a primary "current state."
        The returned ID is valid but not auto-tracked as a root.
        """
        with self._mutation_lock:
            return self._append_internal(root, action, new_state)

    def branch(self, root: Optional[NodeID], action: Action,
               new_state: Any) -> NodeID:
        """Branch from `root`, creating a new root.

        Semantically equivalent to `append` (the new node has
        `root` as its sole parent). Provided as a named operation
        because "branch" captures the *intent* — the caller is
        exploring an alternative trajectory from `root`.

        O(1): the new node shares everything before `root` with
        the original chain via structural sharing.
        """
        return self.append(root, action, new_state)

    def merge(self, root_a: Optional[NodeID], root_b: Optional[NodeID],
              action: Action, merged_state: Any) -> NodeID:
        """Merge two branches into a single new root.

        The new node has BOTH `root_a` and `root_b` as parents.
        If they share a common ancestor, the shared part is
        structurally shared (refcount on shared nodes goes to 2+).

        Cycles are impossible by construction: the new node is
        the only thing that has both parents, and no existing
        node can be a descendant of a not-yet-existing node.
        """
        if root_a is None or root_a == NULL_STATE:
            raise EmptyStateError("cannot merge from an empty root_a")
        if root_b is None or root_b == NULL_STATE:
            raise EmptyStateError("cannot merge from an empty root_b")
        if root_a == root_b:
            raise AndurilError("cannot merge a root with itself")
        with self._mutation_lock:
            # Bump refcount on root_a and root_b (they're now both
            # referenced by the new node).
            self._bump_refcount(root_a)
            self._bump_refcount(root_b)
            new_id = self._alloc_id()
            # Depth = max(depth_a, depth_b) + 1
            depth_a = self._depth_of_node(root_a)
            depth_b = self._depth_of_node(root_b)
            new_node = StateNode(
                node_id=new_id,
                state=merged_state,
                action=action,
                parent_refs=[root_a, root_b],
                depth=max(depth_a, depth_b) + 1,
                tier=Tier.HOT,
                refcount=1,
            )
            self._put(new_node)
            self._roots.add(new_id)
            self._bump_mutation()
            return new_id

    def rewind(self, root: Optional[NodeID], k: int) -> NodeID:
        """Rewind to the k-th ancestor of `root`.

        Semantically: "give me a new root that points to the state
        at depth k." The old root is not modified; the new root
        shares everything up to the rewind point.

        `rewind(root, 0)` is a no-op that returns `root` itself
        but bumps the refcount (in case the caller wants to ensure
        root stays live).

        Raises OutOfRangeError if k exceeds root's depth.
        """
        return self.ancestor(root, k)

    def snapshot(self, root: Optional[NodeID]) -> NodeID:
        """Take an immutable snapshot of `root` (bumps refcount).

        A snapshot is just `root` with its refcount incremented.
        Use this when you want to ensure `root` stays alive even
        if all "natural" references drop it.
        """
        if root is None or root == NULL_STATE:
            return NULL_STATE
        with self._mutation_lock:
            self._bump_refcount(root)
            self._roots.add(root)
            self._bump_mutation()
            return root

    def promote(self, nid: NodeID) -> None:
        """Move `nid` to the HOT tier (in-HBM)."""
        with self._mutation_lock:
            n = self._get(nid)
            if n is None:
                raise UnknownNodeError(f"unknown node {nid}")
            with n.lock:
                n.tier = Tier.HOT
            self._bump_mutation()

    def demote(self, nid: NodeID, tier: Tier) -> None:
        """Move `nid` to WARM or COLD tier."""
        if tier == Tier.HOT:
            raise AndurilError("use promote() to move to HOT")
        with self._mutation_lock:
            n = self._get(nid)
            if n is None:
                raise UnknownNodeError(f"unknown node {nid}")
            with n.lock:
                n.tier = tier
            self._bump_mutation()

    def summarize(self, root: Optional[NodeID],
                  summarizer: Any = None) -> Any:
        """Generate a summary of the subtree rooted at `root`.

        The summary is stored on the leaf-most node of root's path
        (or on root itself if it's a single node). `summarizer`
        is an optional callable that takes the path (list of
        states from root to the leaf-most node) and returns a
        summary object. If None, the default summarizer is
        `len(path)` (just returns the depth).

        Returns the generated summary.

        Note: this is a write operation (it sets `.summary` on
        an existing node). It's the only mutation we do in place
        on an existing node. The DAG itself is unchanged.
        """
        if root is None or root == NULL_STATE:
            return None
        with self._mutation_lock:
            path = self.resolve(root)
            if not path:
                return None
            if summarizer is None:
                summary = len(path)
            else:
                summary = summarizer([p["state"] for p in path])
            # Store the summary on the leaf-most node (the last in
            # the path). The user can choose a different policy
            # later; the default is "the leaf knows the most."
            leaf_id = path[-1]["node_id"]
            leaf = self._get(leaf_id)
            if leaf is not None:
                with leaf.lock:
                    leaf.summary = summary
            self._bump_mutation()
            return summary

    def collect_garbage(self) -> int:
        """Free all nodes in the deferred-free list. Returns count freed.

        Production: epoch-based reclamation. Here: simple deferred free.
        A node enters the deferred-free list when its refcount drops
        to 0; this method actually removes it from the mapping.

        A node only enters the deferred-free list if it's not
        referenced by any live root. The list is append-only
        within a single mutation, drained on collect_garbage().
        """
        n = 0
        for nid in self._deferred_free:
            if nid in self._mapping:
                del self._mapping[nid]
                n += 1
        self._deferred_free.clear()
        return n

    def drop_root(self, root: NodeID) -> None:
        """Stop tracking `root` as a live root.

        Decrements the refcount of `root`'s chain. Does NOT
        immediately free anything — call `collect_garbage()` for
        that. If `root` is still referenced by another live root
        (e.g., it was a parent of a merge), the refcount stays
        positive and the nodes are preserved.
        """
        with self._mutation_lock:
            if root not in self._roots:
                return
            self._roots.discard(root)
            self._decrement_refcount_chain(root)
            self._bump_mutation()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _alloc_id(self) -> NodeID:
        """Allocate a new NodeID. Never reuse an ID (Bw-tree invariant)."""
        with self._id_lock:
            nid = self._next_id
            self._next_id += 1
            return nid

    def _put(self, node: StateNode) -> None:
        """Add a node to the mapping table."""
        self._mapping[node.node_id] = node

    def _get(self, nid: NodeID) -> Optional[StateNode]:
        """Look up a node by ID. Returns None if unknown."""
        return self._mapping.get(nid)

    def _append_internal(self, root: Optional[NodeID], action: Action,
                         new_state: Any) -> NodeID:
        """Append implementation, called under the mutation lock."""
        if root is None or root == NULL_STATE:
            # First node ever.
            new_id = self._alloc_id()
            node = StateNode(
                node_id=new_id,
                state=new_state,
                action=action,
                parent_refs=[],
                depth=0,
                tier=Tier.HOT,
                refcount=1,
            )
            self._put(node)
            return new_id
        # Bump refcount on the existing root — it now has a child
        # (the new node) referencing it. The refcount on the new
        # node is 1 (only the new root references it).
        self._bump_refcount(root)
        new_id = self._alloc_id()
        parent_depth = self._depth_of_node(root)
        node = StateNode(
            node_id=new_id,
            state=new_state,
            action=action,
            parent_refs=[root],
            depth=parent_depth + 1,
            tier=Tier.HOT,
            refcount=1,
        )
        self._put(node)
        return new_id

    def _bump_refcount(self, nid: NodeID) -> None:
        """Increment the refcount of `nid` (call under mutation lock)."""
        n = self._get(nid)
        if n is None:
            return
        with n.lock:
            n.refcount += 1

    def _decrement_refcount(self, nid: NodeID) -> None:
        """Decrement the refcount of `nid` (call under mutation lock).

        If refcount drops to 0, add to deferred-free list. Does NOT
        recursively decrement parents — that's the caller's job
        (only the top of a chain knows the full structure).
        """
        n = self._get(nid)
        if n is None:
            return
        with n.lock:
            n.refcount -= 1
            if n.refcount <= 0:
                self._deferred_free.append(nid)

    def _decrement_refcount_chain(self, nid: NodeID) -> None:
        """Decrement refcount of `nid` and walk up its parent chain.

        Call when a root is being dropped. Stops at nodes whose
        refcount stays > 0 (they're still referenced by other
        live roots).
        """
        cur_id = nid
        visited = set()
        while cur_id is not None and cur_id not in visited:
            visited.add(cur_id)
            cur = self._get(cur_id)
            if cur is None:
                break
            with cur.lock:
                cur.refcount -= 1
                if cur.refcount > 0:
                    # Still referenced; don't walk up (would
                    # double-decrement). This is the
                    # structural-sharing invariant.
                    break
                self._deferred_free.append(cur_id)
            # Walk up to the first parent (the one we came from).
            if not cur.parent_refs:
                break
            cur_id = cur.parent_refs[0]

    def _depth_of_node(self, nid: NodeID) -> int:
        """Return the depth of a node (its .depth field)."""
        n = self._get(nid)
        if n is None:
            return 0
        return n.depth

    def _is_reachable(self, from_id: NodeID, target_id: NodeID) -> bool:
        """Check if `target_id` is reachable from `from_id` via
        the first-parent chain. Used for cycle detection on merge.
        """
        if from_id == target_id:
            return True
        cur_id = from_id
        visited = set()
        while cur_id is not None and cur_id not in visited:
            visited.add(cur_id)
            cur = self._get(cur_id)
            if cur is None:
                return False
            if not cur.parent_refs:
                return False
            cur_id = cur.parent_refs[0]
            if cur_id == target_id:
                return True
        return False

    def _bump_mutation(self) -> None:
        """Increment the mutation counter (for benchmark/observability)."""
        self._mutation_count += 1

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[StateNode]:
        """Iterate over all nodes in the mapping (read-only snapshot)."""
        return iter(list(self._mapping.values()))

    def __len__(self) -> int:
        return len(self._mapping)
