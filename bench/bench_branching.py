"""Branching/merging/rewinding benchmarks for Anduril.

The unique operations: branch (O(1) structural sharing), merge
(DAG construction), rewind (O(k) ancestor walk). These are the
operations that distinguish Anduril from a list-of-messages
memory backend.

Run: python -m bench.bench_branching
"""
import sys
import time
sys.path.insert(0, "src")

from anduril import Anduril, Action


def build_chain(length: int) -> tuple[Anduril, int]:
    """Build a chain of `length` states, return (anduril, root)."""
    a = Anduril()
    r = a.append(None, Action("init"), "s0")
    for i in range(1, length):
        r = a.append(r, Action(f"step_{i}"), f"v{i}")
    a.collect_garbage()
    return a, r


def time_it(name: str, n: int, op) -> float:
    """Run `op` n times, print timing."""
    start = time.perf_counter()
    for _ in range(n):
        op()
    elapsed = time.perf_counter() - start
    ops = n / elapsed
    print(f"  {name:48s} {ops:>10,.0f} ops/sec  ({elapsed*1000:.1f}ms for {n})")
    return ops


def main():
    print("Anduril branching/merging/rewinding benchmarks")
    print("=" * 60)

    # Branching at various chain lengths
    print("\n[1] Branching from a chain of N states")
    print("  (each branch is O(1); the cost is independent of N)")
    for chain_len in [10, 100, 1000, 5000]:
        a, r = build_chain(chain_len)
        n_branches = 100
        counter = [0]

        def branch_op():
            counter[0] += 1
            a.branch(r, Action(f"b{counter[0]}"), f"v{counter[0]}")

        time_it(f"branch from chain of {chain_len} (x{n_branches})", n_branches, branch_op)
        s = a.stats()
        # The shared chain should still be one set of nodes
        # (plus N branch nodes)
        print(f"    total nodes after {n_branches} branches: {s['n_nodes']}")
        print(f"    max depth: {s['max_depth']}")

    # Merging at various chain lengths
    print("\n[2] Merging two chains of N states (in the same Anduril)")
    print("  (merge creates a node with 2 parents)")
    for chain_len in [10, 100, 1000]:
        a = Anduril()
        # Build two independent chains in the same Anduril
        r1 = a.append(None, Action("init1"), "s0")
        for i in range(1, chain_len):
            r1 = a.append(r1, Action(f"s1_{i}"), f"v1_{i}")
        r2 = a.append(None, Action("init2"), "t0")
        for i in range(1, chain_len):
            r2 = a.append(r2, Action(f"s2_{i}"), f"v2_{i}")
        n_merges = 50

        def merge_op():
            a.merge(r1, r2, Action("m"), "merged")

        time_it(f"merge chains of {chain_len} (x{n_merges})", n_merges, merge_op)

    # Rewind cost at various depths
    print("\n[3] Rewinding to depth k (O(k) walk)")
    print("  (rewind cost grows linearly with k; see P0-04 for O(log k) optimization)")
    a, r = build_chain(10_000)
    for k in [10, 100, 1000, 5000, 9000]:
        n_rewinds = 100
        def rewind_op(k=k):
            a.rewind(r, k)
        time_it(f"rewind(r, {k}) x {n_rewinds}", n_rewinds, rewind_op)

    # Many branches sharing a long prefix (the structural sharing win)
    print("\n[4] Many branches sharing a long prefix")
    print("  (each branch is O(1) memory; the prefix is shared)")
    for prefix_len in [100, 1000, 5000]:
        a, r = build_chain(prefix_len)
        n_branches = 100
        counter = [0]
        def many_branch_op():
            counter[0] += 1
            a.branch(r, Action(f"b{counter[0]}"), f"v{counter[0]}")
        time_it(f"branch (prefix={prefix_len}, x{n_branches})", n_branches, many_branch_op)
        s = a.stats()
        # nodes = prefix_len (chain) + n_branches (new branch nodes)
        expected_min = prefix_len + n_branches
        print(f"    expected nodes >= {expected_min}, actual: {s['n_nodes']}")
        print(f"    (sharing is the difference: {prefix_len} chain nodes + {n_branches} branch nodes, NOT {prefix_len * n_branches})")

    # Merge + rewind combined (the agent pattern)
    print("\n[5] Branch + explore + rewind + merge pattern")
    print("  (simulates: branch to try an alternative, walk forward, rewind, merge)")
    a, r = build_chain(100)
    n_iterations = 50
    counter = [0]
    def explore_op():
        counter[0] += 1
        # Branch from r
        b = a.branch(r, Action("explore"), f"v{counter[0]}")
        # Walk forward a few steps
        cur = b
        for i in range(10):
            cur = a.append(cur, Action(f"step{i}"), f"v{counter[0]}_{i}")
        # Rewind to before the branch
        rw = a.rewind(r, 0)
        # Merge the branch's start with the original
        a.merge(r, b, Action("merge"), f"merged_{counter[0]}")
    time_it(f"branch+walk+rewind+merge (x{n_iterations})", n_iterations, explore_op)


if __name__ == "__main__":
    main()
