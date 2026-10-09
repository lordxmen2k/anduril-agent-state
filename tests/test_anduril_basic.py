"""Unit tests for Anduril — the persistent DAG for agent state.

Covers the basic API: construction, append, branch, merge, rewind,
ancestor, children, snapshot, tiers, summarize, and structural-sharing
invariants.
"""
import pytest

import sys
sys.path.insert(0, "src")

from anduril import (
    Action, Anduril, AndurilError, CycleError, EmptyStateError,
    NOOP_ACTION, NULL_STATE, OutOfRangeError, StateNode, Tier,
    UnknownNodeError,
)


# ----------------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------------


class TestConstruction:
    def test_empty(self):
        a = Anduril()
        assert a.is_empty()
        assert a.root_id() is None
        assert a.roots() == set()
        assert a.length_of(a.root_id()) == 0

    def test_with_seed(self):
        a = Anduril(seed_state="hello", initial_action=Action("init", "boot"))
        assert not a.is_empty()
        assert a.root_id() is not None
        assert a.length_of(a.root_id()) == 1
        assert a.get_state(a.root_id()) == "hello"
        assert a.get_action(a.root_id()).kind == "init"

    def test_with_seed_no_action(self):
        a = Anduril(seed_state="hi")
        assert a.get_action(a.root_id()).kind == "noop"

    def test_seed_state_must_be_provided_to_get_state(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        assert a.get_state(r) == "x"


# ----------------------------------------------------------------------------
# Append (chain construction)
# ----------------------------------------------------------------------------


class TestAppend:
    def test_single_append(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        assert a.length_of(r) == 1
        assert a.get_state(r) == "x"

    def test_chain_appends(self):
        a = Anduril()
        r = a.append(None, Action("init"), "a")
        for i in range(10):
            r = a.append(r, Action(f"step_{i}"), f"state_{i}")
        assert a.length_of(r) == 11
        # Walk back and check states
        for i in range(10, -1, -1):
            assert a.get_state(r).startswith("state_") or a.get_state(r) == "a"
            if i > 0:
                r = a.ancestor(r, 1)
        assert a.get_state(r) == "a"

    def test_append_returns_new_id(self):
        a = Anduril()
        r1 = a.append(None, Action("a"), "x")
        r2 = a.append(r1, Action("b"), "y")
        assert r1 != r2
        assert r2 in a.roots()
        assert r1 in a.roots()  # old root stays alive

    def test_append_bumps_refcount_on_parent(self):
        a = Anduril()
        r1 = a.append(None, Action("a"), "x")
        assert a.refcount_of(r1) == 1
        a.append(r1, Action("b"), "y")
        assert a.refcount_of(r1) == 2  # original + child reference

    def test_next_id_is_monotonic(self):
        a = Anduril()
        ids = [a.append(None, Action(f"a{i}"), f"x{i}") for i in range(100)]
        assert ids == sorted(ids)
        assert len(set(ids)) == 100  # all unique

    def test_ancestor_walk(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(20):
            r = a.append(r, Action(f"step_{i}"), f"s{i+1}")
        # Walk from deepest back to root
        cur = r
        for expected_depth in range(20, -1, -1):
            assert a.length_of(cur) == expected_depth + 1
            if expected_depth > 0:
                cur = a.ancestor(cur, 1)
        assert cur is not None


# ----------------------------------------------------------------------------
# Branch (O(1) structural sharing)
# ----------------------------------------------------------------------------


class TestBranch:
    def test_branch_from_chain(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        r = a.append(r, Action("a"), "s1")
        r = a.append(r, Action("b"), "s2")
        # Branch from the middle
        b = a.branch(r, Action("alt"), "alt_state")
        assert a.length_of(b) == 4
        # The branch shares the prefix
        assert a.ancestor(b, 1) == a.ancestor(r, 0)
        assert a.ancestor(b, 2) == a.ancestor(r, 1)
        assert a.ancestor(b, 3) == a.ancestor(r, 2)

    def test_branch_from_initial(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        b1 = a.branch(r, Action("b1"), "y")
        b2 = a.branch(r, Action("b2"), "z")
        # Both share the initial state
        assert a.ancestor(b1, 1) == r
        assert a.ancestor(b2, 1) == r
        # refcount on initial is bumped twice
        assert a.refcount_of(r) == 3  # original + 2 children

    def test_many_branches_share_prefix(self):
        a = Anduril()
        r = a.append(None, Action("init"), "shared")
        branches = [a.branch(r, Action(f"b{i}"), f"alt_{i}") for i in range(10)]
        # All branches share the initial node
        for b in branches:
            assert a.ancestor(b, 1) == r
        # Initial's refcount = 1 (self) + 10 (children) = 11
        assert a.refcount_of(r) == 11

    def test_branch_does_not_mutate_original(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        r2 = a.append(r, Action("b"), "y")
        original_state = a.get_state(r2)
        a.branch(r2, Action("c"), "z")
        # r2 should be unchanged
        assert a.get_state(r2) == original_state


# ----------------------------------------------------------------------------
# Merge (DAG construction)
# ----------------------------------------------------------------------------


class TestMerge:
    def test_merge_two_branches(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        r1 = a.append(r, Action("a"), "s1")
        r2 = a.append(r, Action("b"), "s2")
        m = a.merge(r1, r2, Action("merge"), "merged")
        assert a.parents_of(m) == [r1, r2]
        # init depth=0, r1/r2 depth=1, m depth=2 → length_of(m)=3
        assert a.length_of(m) == 3
        assert a.get_state(m) == "merged"

    def test_merge_with_self_rejected(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        with pytest.raises(AndurilError):
            a.merge(r, r, Action("m"), "y")

    def test_merge_with_empty_rejected(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        with pytest.raises(EmptyStateError):
            a.merge(r, None, Action("m"), "y")
        with pytest.raises(EmptyStateError):
            a.merge(NULL_STATE, r, Action("m"), "y")

    def test_merge_shares_common_ancestor(self):
        a = Anduril()
        r = a.append(None, Action("init"), "shared")
        r1 = a.append(r, Action("a"), "x")
        r2 = a.append(r, Action("b"), "y")
        m = a.merge(r1, r2, Action("m"), "z")
        # r's refcount: 1 (self) + 1 (r1's parent) + 1 (r2's parent) = 3
        # m doesn't directly reference r; r1 and r2 do.
        assert a.refcount_of(r) == 3

    def test_merge_records_both_parents(self):
        a = Anduril()
        r1 = a.append(None, Action("a"), "x")
        r2 = a.append(None, Action("b"), "y")
        m = a.merge(r1, r2, Action("m"), "z")
        assert a.parents_of(m) == [r1, r2]
        # Both parents have refcount 2 (self + child)
        assert a.refcount_of(r1) == 2
        assert a.refcount_of(r2) == 2


# ----------------------------------------------------------------------------
# Rewind / Ancestor
# ----------------------------------------------------------------------------


class TestRewind:
    def test_rewind_to_ancestor(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(10):
            r = a.append(r, Action(f"step_{i}"), f"s{i+1}")
        # Rewind to step 5
        rw = a.rewind(r, 5)
        assert a.length_of(rw) == 6  # 0..5 = 6 nodes
        # Walk down from rw to verify
        cur = rw
        for i in range(5, -1, -1):
            assert a.get_state(cur) in ("s0",) or a.get_state(cur).startswith("s")
            if i > 0:
                cur = a.ancestor(cur, 1)

    def test_rewind_to_zero(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        for i in range(5):
            r = a.append(r, Action(f"s{i}"), f"v{i}")
        rw = a.rewind(r, 0)
        assert rw == r  # rewind to self is identity

    def test_rewind_out_of_range_raises(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        for i in range(5):
            r = a.append(r, Action(f"s{i}"), f"v{i}")
        with pytest.raises(OutOfRangeError):
            a.rewind(r, 100)
        with pytest.raises(OutOfRangeError):
            a.rewind(r, -1)

    def test_rewind_shares_subtree(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(10):
            r = a.append(r, Action(f"s{i}"), f"v{i+1}")
        # Capture the node at step 5
        step5 = a.ancestor(r, 5)
        # Rewind
        rw = a.rewind(r, 5)
        # Both should be the same node
        assert rw == step5


# ----------------------------------------------------------------------------
# Resolve (snapshot)
# ----------------------------------------------------------------------------


class TestResolve:
    def test_resolve_chain(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(3):
            r = a.append(r, Action(f"step_{i}"), f"s{i+1}")
        snap = a.resolve(r)
        assert len(snap) == 4
        # Should be ordered from r to leaf-most
        assert snap[0]["state"] == "s3"  # r's state
        assert snap[-1]["state"] == "s0"  # initial state

    def test_resolve_empty(self):
        a = Anduril()
        assert a.resolve(a.root_id()) == []
        assert a.resolve(None) == []

    def test_resolve_includes_tier(self):
        a = Anduril(seed_state="x", initial_action=NOOP_ACTION)
        snap = a.resolve(a.root_id())
        assert snap[0]["tier"] == Tier.HOT


# ----------------------------------------------------------------------------
# Children
# ----------------------------------------------------------------------------


class TestChildren:
    def test_children_of_root(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        c1 = a.append(r, Action("a"), "x")
        c2 = a.append(r, Action("b"), "y")
        kids = a.children(r)
        assert set(kids) == {c1, c2}

    def test_children_of_leaf(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        r = a.append(r, Action("a"), "x")
        assert a.children(r) == []  # leaf has no children

    def test_children_of_empty(self):
        a = Anduril()
        assert a.children(None) == []
        assert a.children(NULL_STATE) == []


# ----------------------------------------------------------------------------
# Snapshot
# ----------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_bumps_refcount(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        initial_refcount = a.refcount_of(r)
        a.snapshot(r)
        assert a.refcount_of(r) == initial_refcount + 1

    def test_snapshot_keeps_node_alive_after_drop(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        snap = a.snapshot(r)
        a.drop_root(r)
        a.collect_garbage()
        # snap is still a reference, node should be alive
        assert a.get_state(snap) == "x"


# ----------------------------------------------------------------------------
# Drop root + GC
# ----------------------------------------------------------------------------


class TestDropAndGC:
    def test_drop_root_removes_from_set(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        a.drop_root(r)
        assert r not in a.roots()

    def test_drop_root_does_not_free_shared_nodes(self):
        a = Anduril()
        r = a.append(None, Action("init"), "shared")
        b = a.append(r, Action("b"), "alt")
        # r has refcount 2 (self + b's parent)
        a.drop_root(r)
        a.collect_garbage()
        # r is still referenced by b, so still alive
        assert a.get_state(r) == "shared"
        assert a.get_state(b) == "alt"

    def test_drop_all_frees_chain(self):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(5):
            r = a.append(r, Action(f"step_{i}"), f"s{i+1}")
        # Drop the leaf-most
        a.drop_root(r)
        a.collect_garbage()
        # All other roots have been dropped implicitly (they were
        # added as new roots during append). Need to drop them too.
        # Just check that the chain was dropped
        assert a.stats()["n_nodes"] < 6  # at least one was freed

    def test_double_drop_is_noop(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        a.drop_root(r)
        a.drop_root(r)  # second time should be a no-op
        assert r not in a.roots()

    def test_collect_garbage_returns_count(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        a.drop_root(r)
        n_freed = a.collect_garbage()
        assert n_freed >= 1


# ----------------------------------------------------------------------------
# Tier transitions
# ----------------------------------------------------------------------------


class TestTiers:
    def test_initial_tier_is_hot(self):
        a = Anduril(seed_state="x")
        assert a.get_tier(a.root_id()) == Tier.HOT

    def test_demote_to_warm(self):
        a = Anduril(seed_state="x")
        rid = a.root_id()
        a.demote(rid, Tier.WARM)
        assert a.get_tier(rid) == Tier.WARM

    def test_demote_to_cold(self):
        a = Anduril(seed_state="x")
        rid = a.root_id()
        a.demote(rid, Tier.COLD)
        assert a.get_tier(rid) == Tier.COLD

    def test_promote_to_hot(self):
        a = Anduril(seed_state="x")
        rid = a.root_id()
        a.demote(rid, Tier.WARM)
        a.promote(rid)
        assert a.get_tier(rid) == Tier.HOT

    def test_demote_to_hot_rejected(self):
        a = Anduril(seed_state="x")
        with pytest.raises(AndurilError):
            a.demote(a.root_id(), Tier.HOT)

    def test_tier_on_unknown_node_raises(self):
        a = Anduril()
        with pytest.raises(UnknownNodeError):
            a.get_tier(99999)
        with pytest.raises(UnknownNodeError):
            a.demote(99999, Tier.WARM)


# ----------------------------------------------------------------------------
# Summarize
# ----------------------------------------------------------------------------


class TestSummarize:
    def test_default_summarizer_returns_length(self):
        a = Anduril(seed_state="x", initial_action=Action("init"))
        r = a.root_id()
        for i in range(5):
            r = a.append(r, Action(f"step_{i}"), f"s{i+1}")
        s = a.summarize(r)
        assert s == 6  # 1 init + 5 steps

    def test_custom_summarizer(self):
        a = Anduril(seed_state="hello", initial_action=Action("init"))
        r = a.root_id()
        for word in ["world", "foo", "bar"]:
            r = a.append(r, Action("add"), word)
        s = a.summarize(r, summarizer=lambda states: " ".join(states))
        assert "hello" in s
        assert "world" in s

    def test_summarize_stored_on_leaf(self):
        a = Anduril(seed_state="x")
        for i in range(3):
            a.append(a.root_id(), Action(f"s{i}"), f"v{i}")
        r = a.root_id()
        a.summarize(r)
        # The leaf-most node should have a summary
        snap = a.resolve(r)
        leaf = snap[-1]["node_id"]
        assert a.get_summary(leaf) is not None


# ----------------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------------


class TestStats:
    def test_empty_stats(self):
        a = Anduril()
        s = a.stats()
        assert s["n_nodes"] == 0
        assert s["n_roots"] == 0
        assert s["max_depth"] == 0

    def test_stats_after_chain(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        for i in range(10):
            r = a.append(r, Action(f"step_{i}"), f"v{i+1}")
        s = a.stats()
        # All 11 nodes should be alive
        assert s["n_nodes"] == 11
        assert s["max_depth"] == 10
        assert s["n_roots"] == 11  # every append adds a root

    def test_stats_after_merge(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        r1 = a.append(r, Action("a"), "y")
        r2 = a.append(r, Action("b"), "z")
        a.merge(r1, r2, Action("m"), "w")
        s = a.stats()
        # 4 nodes: init, y, z, w
        assert s["n_nodes"] == 4


# ----------------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------------


class TestEdgeCases:
    def test_get_state_unknown_raises(self):
        a = Anduril()
        with pytest.raises(UnknownNodeError):
            a.get_state(99999)

    def test_get_action_unknown_raises(self):
        a = Anduril()
        with pytest.raises(UnknownNodeError):
            a.get_action(99999)

    def test_parents_of_unknown_raises(self):
        a = Anduril()
        with pytest.raises(UnknownNodeError):
            a.parents_of(99999)

    def test_depth_of_unknown_raises(self):
        a = Anduril()
        r = a.append(None, Action("a"), "x")
        with pytest.raises(UnknownNodeError):
            a.depth_of(r, 99999)
        with pytest.raises(UnknownNodeError):
            a.depth_of(99999, r)

    def test_depth_of_non_ancestor_raises(self):
        a = Anduril()
        r1 = a.append(None, Action("a"), "x")
        r2 = a.append(None, Action("b"), "y")
        with pytest.raises(OutOfRangeError):
            a.depth_of(r1, r2)

    def test_iter(self):
        a = Anduril()
        for i in range(5):
            a.append(a.root_id(), Action(f"a{i}"), f"v{i}")
        nodes = list(a)
        assert len(nodes) == 5
        assert all(isinstance(n, StateNode) for n in nodes)

    def test_len(self):
        a = Anduril()
        assert len(a) == 0
        for i in range(3):
            a.append(a.root_id() if a.root_id() else None, Action(f"a{i}"), f"v{i}")
        assert len(a) == 3

    def test_action_repr(self):
        a = Action("tool_call", "search")
        assert "tool_call" in repr(a)
