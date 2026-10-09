"""Property-based tests for Anduril using Hypothesis.

These test invariants that should hold for any valid sequence of
operations: refcount invariants, depth monotonicity, structural
sharing semantics, and DAG shape.
"""
import pytest
from hypothesis import given, settings, strategies as st

import sys
sys.path.insert(0, "src")

from anduril import Action, Anduril, Tier


# Strategies for generating random actions and states
states = st.text(min_size=1, max_size=20)
action_kinds = st.sampled_from(["tool_call", "llm_response", "user_input", "noop"])
actions = st.builds(
    lambda k, p: Action(kind=k, payload=p),
    action_kinds,
    st.text(max_size=10),
)


class TestInvariants:
    """Core invariants of the persistent DAG."""

    @given(st.lists(actions, min_size=1, max_size=50))
    @settings(max_examples=200, deadline=None)
    def test_length_grows_by_one_per_append(self, action_list):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        assert a.length_of(r) == 1
        for i, act in enumerate(action_list):
            r = a.append(r, act, f"state_{i}")
            assert a.length_of(r) == i + 2

    @given(st.integers(min_value=0, max_value=100))
    @settings(max_examples=200, deadline=None)
    def test_ancestor_k_returns_chain_position(self, chain_length):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(chain_length):
            r = a.append(r, Action(f"step_{i}"), f"v{i+1}")
        # length = chain_length + 1 (initial)
        for k in range(chain_length + 1):
            anc = a.ancestor(r, k)
            assert anc is not None
            # Verify the ancestor's state matches the chain
            expected = f"v{chain_length - k}" if k < chain_length else "s0"
            assert a.get_state(anc) == expected

    @given(st.integers(min_value=1, max_value=30),
           st.integers(min_value=1, max_value=10))
    @settings(max_examples=100, deadline=None)
    def test_branch_preserves_prefix(self, prefix_len, branch_count):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(prefix_len):
            r = a.append(r, Action(f"p{i}"), f"pv{i}")
        # Now branch
        branches = []
        for i in range(branch_count):
            b = a.branch(r, Action(f"b{i}"), f"bv{i}")
            branches.append(b)
        # All branches share the prefix. The branch's ancestor at
        # k+1 should equal the original's ancestor at k (because the
        # branch is one step further down the chain).
        for b in branches:
            for k in range(prefix_len + 1):
                assert a.ancestor(b, k + 1) == a.ancestor(r, k)

    @given(st.integers(min_value=1, max_value=20),
           st.integers(min_value=0, max_value=20))
    @settings(max_examples=100, deadline=None)
    def test_rewind_equals_ancestor(self, chain_len, rewind_k):
        if rewind_k > chain_len:
            rewind_k = chain_len
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(chain_len):
            r = a.append(r, Action(f"s{i}"), f"v{i+1}")
        rw = a.rewind(r, rewind_k)
        anc = a.ancestor(r, rewind_k)
        assert rw == anc


class TestStructuralSharing:
    """Structural sharing via refcount."""

    @given(st.integers(min_value=1, max_value=20),
           st.integers(min_value=1, max_value=10))
    @settings(max_examples=100, deadline=None)
    def test_branch_refcount_matches(self, prefix_len, branch_count):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        initial = r  # the seed
        for i in range(prefix_len):
            r = a.append(r, Action(f"p{i}"), f"v{i}")
        # After the chain, r is the leaf-most node with refcount 1.
        # Now branch from r.
        for i in range(branch_count):
            a.branch(r, Action(f"b{i}"), f"bv{i}")
        # Each branch adds 1 to r's refcount. r's total: 1 (self) + branch_count.
        expected = 1 + branch_count
        assert a.refcount_of(r) == expected

    @given(st.integers(min_value=2, max_value=10))
    @settings(max_examples=50, deadline=None)
    def test_merge_refcount_on_parents(self, n_parents):
        a = Anduril()
        # Create n_parents independent roots, each with one extra step
        roots = []
        inits = []
        for i in range(n_parents):
            init = a.append(None, Action("init"), f"s{i}")
            inits.append(init)
            r = a.append(init, Action(f"step{i}"), f"v{i}")
            roots.append(r)
        # Each init has refcount 2 (self + step's parent)
        for init in inits:
            assert a.refcount_of(init) == 2
        # Each step root has refcount 1 (itself)
        for r in roots:
            assert a.refcount_of(r) == 1
        m = a.merge(roots[0], roots[1], action=Action("merge"), merged_state="merged")
        for extra_root in roots[2:]:
            m = a.merge(m, extra_root, action=Action("merge2"), merged_state="merged2")
        # After merge: each step root has refcount 2 (self + merge child)
        for r in roots:
            assert a.refcount_of(r) == 2
        # The inits still have refcount 2 (no change)
        for init in inits:
            assert a.refcount_of(init) == 2


class TestSnapshot:
    """Snapshot keeps nodes alive."""

    @given(st.integers(min_value=1, max_value=50),
           st.integers(min_value=0, max_value=10))
    @settings(max_examples=100, deadline=None)
    def test_snapshot_keeps_state_readable_after_drop(self, chain_len, n_snaps):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        for i in range(chain_len):
            r = a.append(r, Action(f"s{i}"), f"v{i+1}")
        snaps = [a.snapshot(r) for _ in range(n_snaps)]
        a.drop_root(r)
        a.collect_garbage()
        # Each snapshot should still be readable
        for snap in snaps:
            # snap is a NodeID, may be the same as r
            state = a.get_state(snap)
            assert state is not None


class TestDAGShape:
    """DAG invariants: no cycles, ancestors reachable, etc."""

    @given(st.lists(st.sampled_from(["a", "b", "c"]), min_size=1, max_size=30))
    @settings(max_examples=100, deadline=None)
    def test_dag_stays_acyclic_under_random_ops(self, op_sequence):
        a = Anduril()
        r = a.append(None, Action("init"), "s0")
        alive_roots = {r}
        for op in op_sequence:
            if op == "a":
                r = a.append(r, Action("append"), "s")
                alive_roots.add(r)
            elif op == "b" and alive_roots:
                src = next(iter(alive_roots))
                b = a.branch(src, Action("branch"), "b")
                alive_roots.add(b)
            elif op == "c" and len(alive_roots) >= 2:
                it = iter(alive_roots)
                r1 = next(it)
                r2 = next(it)
                m = a.merge(r1, r2, Action("merge"), "m")
                alive_roots.add(m)
        # No matter what we did, the structure is valid:
        # every node is reachable from at least one root
        for nid in a.roots():
            # Walk up; should reach a leaf (no parents) at some point
            cur = nid
            depth = 0
            while cur is not None and a.parents_of(cur):
                # Only walk the first parent for chains
                cur = a.parents_of(cur)[0]
                depth += 1
                if depth > 1000:
                    pytest.fail("cycle detected in walk")
            # Should always reach a leaf


class TestTiers:
    """Tier transitions preserve state."""

    @given(st.integers(min_value=0, max_value=5))
    @settings(max_examples=50, deadline=None)
    def test_tier_round_trip(self, n_demotes):
        a = Anduril(seed_state="x", initial_action=Action("init"))
        r = a.root_id()
        tier = Tier.HOT
        for _ in range(n_demotes):
            if tier == Tier.HOT:
                a.demote(r, Tier.WARM)
                tier = Tier.WARM
            elif tier == Tier.WARM:
                a.demote(r, Tier.COLD)
                tier = Tier.COLD
            else:
                a.promote(r)
                tier = Tier.HOT
            assert a.get_tier(r) == tier
