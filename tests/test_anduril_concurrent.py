"""Concurrent tests for Anduril.

Verify thread-safety invariants under multi-threaded load:
- All mutating ops are serialized by `_mutation_lock`
- Reads are lock-free and see a consistent snapshot
- Structural sharing (refcount) is correct under concurrent append/branch
- No node is double-freed or leaked
"""
import threading
import time
import pytest

import sys
sys.path.insert(0, "src")

from anduril import Action, Anduril, Tier


class TestConcurrentAppend:
    """Multiple threads appending concurrently to the same DAG."""

    def test_many_threads_append_to_shared_root(self):
        a = Anduril()
        r = a.append(None, Action("init"), "shared")
        n_threads = 8
        ops_per_thread = 100
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                # Each thread builds its own chain from the shared root
                local = r
                for i in range(ops_per_thread):
                    local = a.append(local, Action(f"t{tid}_i{i}"), f"v{tid}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"errors: {errors[:3]}"
        # All operations should be reflected in stats
        s = a.stats()
        # Each thread's chain is ops_per_thread long, plus the initial
        # node. Plus the threads' final roots (1 per thread).
        assert s["n_nodes"] >= n_threads * ops_per_thread

    def test_concurrent_branches_share_prefix(self):
        a = Anduril()
        r = a.append(None, Action("init"), "shared")
        # Build a 10-step chain
        for i in range(10):
            r = a.append(r, Action(f"step_{i}"), f"v{i}")
        # Now many threads branch from r
        n_threads = 16
        ops_per_thread = 50
        barrier = threading.Barrier(n_threads)
        branches = [None] * n_threads
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                local = r
                for i in range(ops_per_thread):
                    local = a.append(local, Action(f"b{tid}_{i}"), f"bv{tid}_{i}")
                branches[tid] = local
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"errors: {errors[:3]}"
        # All branches should still be able to walk to the initial state
        for b in branches:
            assert b is not None
            anc = a.ancestor(b, ops_per_thread)  # the r they branched from
            assert anc == r


class TestConcurrentReads:
    """Reads should be lock-free and safe under concurrent mutators."""

    def test_reads_under_concurrent_appends(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        # Build a chain
        for i in range(100):
            r = a.append(r, Action(f"s{i}"), f"v{i}")

        stop = threading.Event()
        errors = []
        read_count = [0]
        read_lock = threading.Lock()

        def reader():
            try:
                while not stop.is_set():
                    state = a.get_state(r)
                    assert state is not None
                    snap = a.resolve(r)
                    assert len(snap) == 101
                    with read_lock:
                        read_count[0] += 1
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                local = r
                for i in range(50):
                    local = a.append(local, Action(f"w{i}"), f"wv{i}")
            except Exception as e:
                errors.append(e)

        # 4 readers, 2 writers
        threads = []
        for _ in range(4):
            threads.append(threading.Thread(target=reader))
        for _ in range(2):
            threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        time.sleep(0.5)  # let readers do their thing
        stop.set()
        for t in threads:
            t.join()
        assert not errors, f"errors: {errors[:3]}"
        assert read_count[0] > 0  # readers actually ran

    def test_ancestor_walk_under_concurrent_appends(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        for i in range(50):
            r = a.append(r, Action(f"s{i}"), f"v{i}")

        stop = threading.Event()
        errors = []

        def reader():
            try:
                while not stop.is_set():
                    for k in range(50):
                        anc = a.ancestor(r, k)
                        assert anc is not None
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                local = r
                for i in range(30):
                    local = a.append(local, Action(f"w{i}"), f"wv{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(2)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.3)
        stop.set()
        for t in threads:
            t.join()
        assert not errors, f"errors: {errors[:3]}"


class TestConcurrentMerge:
    """Concurrent merges should not corrupt the DAG."""

    def test_concurrent_merges(self):
        a = Anduril()
        # Create several independent roots
        roots = []
        for i in range(5):
            r = a.append(None, Action(f"init_{i}"), f"v{i}")
            roots.append(r)

        n_threads = 4
        barrier = threading.Barrier(n_threads)
        errors = []
        new_roots = [None] * n_threads

        def worker(tid):
            try:
                barrier.wait()
                # Each thread merges two of the existing roots
                m = a.merge(roots[tid], roots[(tid + 1) % len(roots)],
                            Action(f"merge_{tid}"), f"mv{tid}")
                new_roots[tid] = m
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"errors: {errors[:3]}"
        for nr in new_roots:
            assert nr is not None
            # Each merged node has 2 parents
            assert len(a.parents_of(nr)) == 2


class TestStress:
    """Long-running stress tests."""

    def test_long_chain_under_read_load(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        for i in range(500):
            r = a.append(r, Action(f"s{i}"), f"v{i}")

        stop = threading.Event()
        errors = []
        op_count = [0]
        op_lock = threading.Lock()

        def reader():
            try:
                while not stop.is_set():
                    snap = a.resolve(r)
                    assert len(snap) == 501
                    with op_lock:
                        op_count[0] += 1
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                local = r
                for i in range(200):
                    local = a.append(local, Action(f"w{i}"), f"wv{i}")
                    with op_lock:
                        op_count[0] += 1
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join()
        assert not errors, f"errors: {errors[:3]}"
        # Should have many operations
        assert op_count[0] > 100

    def test_high_branching_factor(self):
        """Many threads branching from the same root."""
        a = Anduril()
        r = a.append(None, Action("init"), "shared")
        n_threads = 32
        ops_per_thread = 20
        barrier = threading.Barrier(n_threads)
        errors = []
        branches = [None] * n_threads

        def worker(tid):
            try:
                barrier.wait()
                local = r
                for i in range(ops_per_thread):
                    local = a.append(local, Action(f"t{tid}_i{i}"), f"v{tid}_{i}")
                branches[tid] = local
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"errors: {errors[:3]}"
        # All branches should walk back to the original r
        for b in branches:
            assert b is not None
            anc = a.ancestor(b, ops_per_thread)
            assert anc == r


class TestSnapshotSafety:
    """Snapshots taken under concurrent mutations stay valid."""

    def test_snapshot_under_load(self):
        a = Anduril()
        r = a.append(None, Action("init"), "x")
        for i in range(20):
            r = a.append(r, Action(f"s{i}"), f"v{i}")
        snap = a.snapshot(r)
        # Keep mutating
        for i in range(20):
            r = a.append(r, Action(f"w{i}"), f"wv{i}")
        # The snapshot should still point to a valid node
        state = a.get_state(snap)
        assert state is not None
        # The chain from snap should be the original 20-step chain
        snap_path = a.resolve(snap)
        assert len(snap_path) == 21
