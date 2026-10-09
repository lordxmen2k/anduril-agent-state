"""Real-agent integration benchmark for Anduril.

A ReAct-style agent runs a multi-step task, using Anduril as
the memory backend. Measures:
- Throughput (states per second)
- Memory overhead (n_nodes, n_roots, max_depth)
- Branching + rewinding cost in the agent loop
- Comparison vs a naive list-based memory backend

Run: python -m bench.bench_with_agent
"""
import sys
import time
sys.path.insert(0, "src")

from anduril import Anduril, Action


# A simple deterministic task: "find prime factors of N"
# The agent makes tool calls (divide, check_primality) and
# reasons about the result. Branches when considering
# alternative factors, rewinds when a path is wrong.

def naive_list_memory():
    """A list-of-messages memory backend for comparison."""
    history = []
    def append(role, content):
        history.append({"role": role, "content": content})
    def get():
        return list(history)
    def rewind(k):
        nonlocal history
        history = history[:k] if k >= 0 else history[:k]
    def branch(alt_history):
        return list(alt_history)
    return {"append": append, "get": get, "rewind": rewind,
            "branch": branch, "history": history}


def run_anduril_agent(n_steps: int, n_branches: int) -> dict:
    """Run a ReAct agent on a synthetic task with Anduril."""
    a = Anduril(seed_state={"step": 0, "history": [{"role": "user", "content": "Find factors of 84"}]},
                initial_action=Action("init", "start"))
    cur = a.root_id()
    for step in range(n_steps):
        # Simulate an LLM call + tool execution
        cur = a.append(cur, Action("tool_call", f"step_{step}"),
                       {"step": step + 1, "history": [
                           {"role": "assistant", "content": f"call_{step}"},
                           {"role": "tool", "content": f"obs_{step}"},
                       ]})
    # Branch a few times to try alternative paths
    for i in range(n_branches):
        b = a.branch(cur, Action("alt", f"branch_{i}"),
                     {"step": n_steps + 1, "history": [
                         {"role": "assistant", "content": f"alt_{i}"},
                     ]})
    return {"anduril": a, "n_states": n_steps + 1 + n_branches,
            "n_branches": n_branches}


def run_naive_agent(n_steps: int, n_branches: int) -> dict:
    """Same task with a naive list-of-messages memory."""
    mem = naive_list_memory()
    for step in range(n_steps):
        mem["append"]("assistant", f"call_{step}")
        mem["append"]("tool", f"obs_{step}")
    # Branches in a list-based memory: copy the full history
    for i in range(n_branches):
        mem["branch"](mem["history"] + [{"role": "assistant", "content": f"alt_{i}"}])
    return {"memory": mem, "n_states": n_steps + 1 + n_branches,
            "n_branches": n_branches}


def main():
    print("Real-agent integration benchmark: Anduril vs naive list")
    print("=" * 60)
    print()
    print("Task: synthetic ReAct agent, n_steps tool calls + n_branches alternatives")
    print("Each step and branch becomes a state in the memory backend.")
    print()

    # Test 1: small workload
    print("[1] Small workload: 20 steps, 5 branches")
    n = 100
    t0 = time.perf_counter()
    for _ in range(n):
        run_anduril_agent(20, 5)
    t_a = time.perf_counter() - t0
    print(f"  Anduril: {(t_a/n)*1000:.2f}ms/iter  ({n/t_a:,.0f} iters/sec)")

    t0 = time.perf_counter()
    for _ in range(n):
        run_naive_agent(20, 5)
    t_n = time.perf_counter() - t0
    print(f"  Naive:   {(t_n/n)*1000:.2f}ms/iter  ({n/t_n:,.0f} iters/sec)")

    # Test 2: bigger workload (more branches = more advantage for Anduril)
    print("\n[2] Many branches: 20 steps, 50 branches")
    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        run_anduril_agent(20, 50)
    t_a = time.perf_counter() - t0
    print(f"  Anduril: {(t_a/n)*1000:.2f}ms/iter  ({n/t_a:,.0f} iters/sec)")

    t0 = time.perf_counter()
    for _ in range(n):
        run_naive_agent(20, 50)
    t_n = time.perf_counter() - t0
    print(f"  Naive:   {(t_n/n)*1000:.2f}ms/iter  ({n/t_n:,.0f} iters/sec)")

    # Test 3: large prefix, many branches (the structural sharing win)
    print("\n[3] Long history, many branches: 100 steps, 100 branches")
    n = 20
    t0 = time.perf_counter()
    r = run_anduril_agent(100, 100)
    t_a = time.perf_counter() - t0
    print(f"  Anduril: {(t_a/n)*1000:.2f}ms/iter  ({n/t_a:,.0f} iters/sec)")
    print(f"    Anduril nodes: {r['anduril'].stats()['n_nodes']}")

    t0 = time.perf_counter()
    r = run_naive_agent(100, 100)
    t_n = time.perf_counter() - t0
    print(f"  Naive:   {(t_n/n)*1000:.2f}ms/iter  ({n/t_n:,.0f} iters/sec)")
    # Estimate naive memory: history size * branches
    naive_size = len(r["memory"]["history"]) * r["n_branches"]
    print(f"    Naive state objects held: {naive_size} (history copied per branch)")

    # Test 4: rewind cost
    print("\n[4] Rewind to step k (Anduril is O(k) chain walk; naive is O(1) slice)")
    a, r = (None, None), None
    a_obj, r = None, None
    a_obj = Anduril(seed_state={"step": 0, "history": []},
                    initial_action=Action("init"))
    for i in range(500):
        r = a_obj.append(r, Action(f"s{i}"), f"v{i}")

    n = 100
    for k in [10, 100, 250, 400]:
        t0 = time.perf_counter()
        for _ in range(n):
            a_obj.rewind(r, k)
        t_a = time.perf_counter() - t0
        print(f"  Anduril rewind({k}): {(t_a/n)*1e6:.1f} us/iter")

    # Naive rewind
    mem = naive_list_memory()
    for i in range(500):
        mem["append"]("user", f"v{i}")
    for k in [10, 100, 250, 400]:
        t0 = time.perf_counter()
        for _ in range(n):
            mem["rewind"](-(500 - k))
        t_n = time.perf_counter() - t0
        print(f"  Naive rewind({k}):   {(t_n/n)*1e6:.1f} us/iter")

    # Test 5: agent loop with branching + rewinding (the realistic pattern)
    print("\n[5] Realistic agent loop: 20 steps + 5 branches + 5 rewinds")
    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        a = Anduril(seed_state={"step": 0, "history": []},
                    initial_action=Action("init"))
        cur = a.root_id()
        for step in range(20):
            cur = a.append(cur, Action(f"s{step}"), f"v{step}")
        # Branch
        branches = [a.branch(cur, Action(f"b{i}"), f"alt{i}") for i in range(5)]
        # Rewind and merge
        for b in branches:
            rw = a.rewind(cur, 10)
            a.merge(cur, b, Action("m"), "merged")
    t_a = time.perf_counter() - t0
    print(f"  Anduril: {(t_a/n)*1000:.2f}ms/iter  ({n/t_a:,.0f} iters/sec)")

    t0 = time.perf_counter()
    for _ in range(n):
        mem = naive_list_memory()
        for step in range(20):
            mem["append"]("user", f"v{step}")
        # Branch: copy
        branches = [mem["branch"](mem["history"] + [{"role": "user", "content": f"alt{i}"}])
                    for i in range(5)]
        # Rewind: slice
        for b in branches:
            mem["rewind"](-(20 - 10))
    t_n = time.perf_counter() - t0
    print(f"  Naive:   {(t_n/n)*1000:.2f}ms/iter  ({n/t_n:,.0f} iters/sec)")


if __name__ == "__main__":
    main()
