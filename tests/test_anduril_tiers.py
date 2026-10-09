"""Tier transition tests for Anduril.

Verify the hot/warm/cold tier system: transitions, persistence,
and combined behavior with structural sharing.
"""
import pytest

import sys
sys.path.insert(0, "src")

from anduril import Action, Anduril, AndurilError, Tier


class TestBasicTier:
    def test_default_is_hot(self):
        a = Anduril(seed_state="x")
        assert a.get_tier(a.root_id()) == Tier.HOT

    def test_demote_to_warm(self):
        a = Anduril(seed_state="x")
        a.demote(a.root_id(), Tier.WARM)
        assert a.get_tier(a.root_id()) == Tier.WARM

    def test_demote_to_cold(self):
        a = Anduril(seed_state="x")
        a.demote(a.root_id(), Tier.COLD)
        assert a.get_tier(a.root_id()) == Tier.COLD

    def test_promote_back_to_hot(self):
        a = Anduril(seed_state="x")
        a.demote(a.root_id(), Tier.COLD)
        a.promote(a.root_id())
        assert a.get_tier(a.root_id()) == Tier.HOT

    def test_demote_to_hot_rejected(self):
        """Use promote() for HOT transitions, not demote."""
        a = Anduril(seed_state="x")
        with pytest.raises(AndurilError):
            a.demote(a.root_id(), Tier.HOT)

    def test_chain_inherits_hot(self):
        a = Anduril(seed_state="x")
        r = a.root_id()
        for i in range(5):
            r = a.append(r, Action(f"s{i}"), f"v{i}")
        # All nodes are HOT by default
        snap = a.resolve(r)
        for step in snap:
            assert a.get_tier(step["node_id"]) == Tier.HOT

    def test_individual_node_tier(self):
        a = Anduril(seed_state="x")
        r = a.root_id()
        r1 = a.append(r, Action("a"), "y")
        r2 = a.append(r1, Action("b"), "z")
        # Demote only the middle node
        a.demote(r1, Tier.WARM)
        assert a.get_tier(r) == Tier.HOT
        assert a.get_tier(r1) == Tier.WARM
        assert a.get_tier(r2) == Tier.HOT


class TestTierStats:
    def test_tier_counts(self):
        a = Anduril(seed_state="x")
        r = a.root_id()
        r1 = a.append(r, Action("a"), "y")
        r2 = a.append(r1, Action("b"), "z")
        a.demote(r, Tier.WARM)
        a.demote(r2, Tier.COLD)
        s = a.stats()
        assert s["tier_counts"]["hot"] == 1  # r1
        assert s["tier_counts"]["warm"] == 1  # r
        assert s["tier_counts"]["cold"] == 1  # r2

    def test_tier_survives_append(self):
        a = Anduril(seed_state="x")
        r = a.root_id()
        a.demote(r, Tier.COLD)
        r1 = a.append(r, Action("a"), "y")
        # r is still COLD, r1 is HOT (default for new nodes)
        assert a.get_tier(r) == Tier.COLD
        assert a.get_tier(r1) == Tier.HOT


class TestTierWithStructuralSharing:
    """Tier + refcount + structural sharing interactions."""

    def test_tier_on_shared_node(self):
        """A node shared across branches keeps its tier."""
        a = Anduril(seed_state="x")
        r = a.append(None, Action("init"), "shared")
        b1 = a.branch(r, Action("b1"), "alt1")
        b2 = a.branch(r, Action("b2"), "alt2")
        a.demote(r, Tier.WARM)
        # All three should see r as WARM
        assert a.get_tier(r) == Tier.WARM
        # Refcount on r should be 3 (self + 2 branches)
        assert a.refcount_of(r) == 3

    def test_promote_one_branch_does_not_promote_other(self):
        """Each node has its own tier; promoting one doesn't affect siblings."""
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        b1 = a.branch(r, Action("b1"), "y")
        b2 = a.branch(r, Action("b2"), "z")
        a.demote(b1, Tier.WARM)
        # b2 is still HOT
        assert a.get_tier(b1) == Tier.WARM
        assert a.get_tier(b2) == Tier.HOT


class TestTierWithMerge:
    """Merged nodes have their own tier, parents keep theirs."""

    def test_merged_node_tier_default(self):
        a = Anduril()
        r1 = a.append(None, Action("a"), "x")
        r2 = a.append(None, Action("b"), "y")
        m = a.merge(r1, r2, Action("m"), "z")
        assert a.get_tier(m) == Tier.HOT

    def test_can_demote_merged_node(self):
        a = Anduril()
        r1 = a.append(None, Action("a"), "x")
        r2 = a.append(None, Action("b"), "y")
        m = a.merge(r1, r2, Action("m"), "z")
        a.demote(m, Tier.COLD)
        assert a.get_tier(m) == Tier.COLD
        # Parents are still HOT
        assert a.get_tier(r1) == Tier.HOT
        assert a.get_tier(r2) == Tier.HOT


class TestTierRoundTrip:
    """Demote/promote round-trips preserve identity."""

    def test_demote_promote_demote(self):
        a = Anduril(seed_state="x")
        rid = a.root_id()
        for _ in range(10):
            a.demote(rid, Tier.WARM)
            a.promote(rid)
            a.demote(rid, Tier.COLD)
            a.promote(rid)
        assert a.get_tier(rid) == Tier.HOT

    def test_tier_preserved_in_snapshot(self):
        a = Anduril(seed_state="x")
        rid = a.root_id()
        a.demote(rid, Tier.WARM)
        snap = a.snapshot(rid)
        assert a.get_tier(snap) == Tier.WARM
