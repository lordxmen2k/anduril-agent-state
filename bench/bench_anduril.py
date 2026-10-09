"""Microbenchmarks for Anduril.

Per-op throughput on a single thread, on chains of various lengths.
Compares append vs branch vs merge vs rewind vs resolve.

Run: python -m bench.bench_anduril
"""
import sys
import time
sys.path.insert(0, "src")

from anduril import Anduril, Action


def bench(name: str, n: int, setup, op) -> float:
    """Run `op` n times. After each op, call `setup` to restore state.

    Returns ops per second.
    """
    start = time.perf_counter()
    for _ in range(n):
        state = setup()
        op(state)
    elapsed = time.perf_counter() - start
    ops = n / elapsed
    print(f"  {name:42s} {ops:>10,.0f} ops/sec  ({elapsed*1000:.1f}ms for {n})")
    return ops


def main():
    print("Anduril microbenchmarks (single-threaded)")
    print("=" * 60)

    # Build a 10K-state chain
    a = Anduril()
    r = a.append(None, Action("init"), "s0")
    for i in range(10_000):
        r = a.append(r, Action(f"step_{i}"), f"v{i+1}")
    a.collect_garbage()
    chain_root = r
    print(f"\nSetup: 10K-state chain, depth {a.length_of(r)}")
    print(f"  stats: {a.stats()}")
    print()

    # Reads on the chain
    bench("length() x 10000", 10_000,
          lambda: a, lambda x: x.length_of(chain_root))
    bench("get_state(head) x 10000", 10_000,
          lambda: a, lambda x: x.get_state(chain_root))
    bench("ancestor(0) x 10000", 10_000,
          lambda: a, lambda x: x.ancestor(chain_root, 0))
    bench("ancestor(5000) x 10000", 10_000,
          lambda: a, lambda x: x.ancestor(chain_root, 5000))
    bench("ancestor(9999) x 10000", 10_000,
          lambda: a, lambda x: x.ancestor(chain_root, 9999))
    bench("resolve() x 1000", 1_000,
          lambda: a, lambda x: x.resolve(chain_root))
    bench("snapshot() x 1000", 1_000,
          lambda: a, lambda x: x.snapshot(chain_root))
    bench("children(head) x 1000", 1_000,
          lambda: a, lambda x: x.children(chain_root))

    print()
    # Mutation throughput (each iteration reseeds the rope)
    def setup_append():
        return Anduril()
    def op_append(a_):
        r = a_.append(None, Action("a"), "x")
    bench("append (1 state) x 1000", 1_000, setup_append, op_append)

    def setup_append_chain():
        a_ = Anduril()
        r = a_.append(None, Action("init"), "x")
        return (a_, r)
    def op_append_chain(state):
        a_, r = state
        return a_.append(r, Action("a"), "y")
    bench("append (chain) x 1000", 1_000, setup_append_chain, op_append_chain)

    def setup_branch():
        a_ = Anduril()
        r = a_.append(None, Action("init"), "x")
        for i in range(100):
            r = a_.append(r, Action(f"s{i}"), f"v{i}")
        return (a_, r)
    def op_branch(state):
        a_, r = state
        return a_.branch(r, Action("b"), "alt")
    bench("branch (from 100-state) x 1000", 1_000, setup_branch, op_branch)

    def setup_merge():
        a_ = Anduril()
        r1 = a_.append(None, Action("a"), "x")
        r1 = a_.append(r1, Action("b"), "y")
        r2 = a_.append(None, Action("c"), "z")
        r2 = a_.append(r2, Action("d"), "w")
        return (a_, r1, r2)
    def op_merge(state):
        a_, r1, r2 = state
        return a_.merge(r1, r2, Action("m"), "merged")
    bench("merge (2 roots, 2 deep) x 1000", 1_000, setup_merge, op_merge)

    print()
    # Tier transitions
    a_ = Anduril(seed_state="x")
    r_ = a_.root_id()
    def setup_tier():
        return r_
    def op_demote(rid):
        a_.demote(rid, Tier.WARM)  # type: ignore
    from anduril import Tier
    bench("demote() x 1000", 1_000, setup_tier, op_demote)

    # Summarize
    a_ = Anduril(seed_state="x")
    r_ = a_.root_id()
    for i in range(100):
        r_ = a_.append(r_, Action(f"s{i}"), f"v{i}")
    def setup_sum():
        return r_
    def op_sum(rid):
        a_.summarize(rid)
    bench("summarize (100-state) x 1000", 1_000, setup_sum, op_sum)

    print()
    print("Note: single-threaded with a global mutation lock.")
    print("Concurrent throughput is measured in stress_test.py.")


if __name__ == "__main__":
    main()
