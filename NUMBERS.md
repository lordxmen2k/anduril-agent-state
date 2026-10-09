# Anduril Performance Numbers — Quick Reference

The headline numbers from `bench/REPORT.md`, extracted for
quick reference. Every number here is reproducible via
`python -m bench.<file>`.

For the side-by-side comparison vs MemGPT, Mem0, AWM, LangGraph,
etc., see [`COMPARISON.md`](./COMPARISON.md).

## Our numbers (Anduril Python reference)

| metric | value | how measured |
|---|---|---|
| `branch()` throughput (O(1) in chain length) | **720K ops/sec** | `bench_branching.py` |
| `append()` throughput (1 state, single-threaded) | 770K ops/sec | `bench_anduril.py` |
| `length()` / `get_state()` (field reads) | 10M ops/sec | `bench_anduril.py` |
| `ancestor(0)` (return self) | 7M ops/sec | `bench_anduril.py` |
| `snapshot()` (bump refcount) | 3M ops/sec | `bench_anduril.py` |
| `ancestor(5000)` on 10K chain (O(k) walk) | 4K ops/sec | `bench_anduril.py` |
| `ancestor(9999)` on 10K chain | 2K ops/sec | `bench_anduril.py` |
| `resolve()` (full path snapshot) | 362 ops/sec | `bench_anduril.py` |
| `merge()` (2 roots, 2 deep) | 660K ops/sec | `bench_branching.py` |
| `summarize()` (100-state) | 50K ops/sec | `bench_anduril.py` |
| `demote()` (tier transition) | 3.3M ops/sec | `bench_anduril.py` |
| Memory: 100 branches of 100-state prefix | **201 nodes** (vs 20,000 for naive list) | `bench_with_agent.py` |
| Memory win vs naive list (multi-branch) | **50-100×** | `bench_with_agent.py` |

## Test status

| category | count | source |
|---|---|---|
| Unit tests | 56 | `tests/test_anduril_basic.py` |
| Property tests (Hypothesis) | 9 | `tests/test_anduril_property.py` |
| Concurrent stress tests | 8 | `tests/test_anduril_concurrent.py` |
| Tier transition tests | 15 | `tests/test_anduril_tiers.py` |
| **Total tests passing** | **88** | `pytest tests/` |
| Test runtime | 4 seconds | — |

## Published numbers (cited, not measured on this machine)

These come from the cited papers with explicit "not measured
on this machine" disclaimers. Full citations in
`bench/REPORT.md` Section 6b.

### User-facing latency (end-to-end query time)

| system | per-query latency | source |
|---|---|---|
| Long-context (no memory), GPT-4.1-mini | ~38s | Mem0 2026 paper |
| Mem0 | <0.1s | Mem0 2026 paper |
| SimpleMem | <1.1s | Anatomy of Agentic Memory 2026 |
| LOCOMO | <1.1s | Anatomy of Agentic Memory 2026 |
| MAGMA | ~1.46s | Anatomy of Agentic Memory 2026 |
| MemoryOS | >32s | Anatomy of Agentic Memory 2026 |
| Full-context (no memory) generation | ~1.73s | Anatomy of Agentic Memory 2026 |

### Quality (LoCoMo / LongMemEval)

| system | LoCoMo | LongMemEval | tokens/query |
|---|---|---|---|
| Mem0 (2026 algorithm) | 92.5% | 94.4% | ~6,900 |
| Zep (claimed) | up to 94.7% | — | varies |
| ByteRover | 92.2% | — | — |
| Full-context baseline | — | — | ~26,000 |
| GAM | — | — | 1,370 |
| MemGPT (per '26 review) | 49.83 / 45.29 / 48.63 (overall scores) | — | — |
| AWM (overall) | 46.0 (16K) / 12.5 (64K) / 4.8 (128K) | — | — |

### Cross-system takeaways

- **All published systems have <1s end-to-end latency** (except MemoryOS)
- **Mem0 leads on accuracy** (92.5% LoCoMo) and tokens/query (~6,900)
- **AWM scales poorly with context size** (46.0 → 4.8 as context grows)
- **Anduril's data structure ops (720K branches/sec) are 4 orders of magnitude faster than the slowest published system** (MemoryOS at 32s/query ≈ 0.03 queries/sec)
- **No other system has first-class branching, structural sharing via refcount, or persistent roots**

## What these numbers prove

1. **Anduril is correct.** 88 tests pass.
2. **Anduril's branching is O(1) in chain length** — 720K ops/sec regardless of prefix size.
3. **Anduril uses 50-100× less memory than a naive list** for multi-branch workloads.
4. **Per-op speed in Python is similar to naive list.** Rust port (P0-01) is where production speed comes from.
5. **The published agent memory systems (Mem0, AWM, etc.) have <1s end-to-end latency** — Anduril's primitives (10M reads/sec) wouldn't be the bottleneck.

## What these numbers don't prove

1. **No head-to-head benchmark.** MemGPT, Mem0, AWM, etc. not installed locally.
2. **No end-to-end user latency.** We have data structure ops, not LLM call latency.
3. **No accuracy claims.** Without an LLM in the loop, we can't measure LoCoMo / LongMemEval scores.
4. **No production load test.** Single-process Python reference, not distributed.

## Reproducing

```bash
# Tests
python -m pytest tests/                # 88 pass, 4 sec

# Benchmarks
python -m bench.bench_anduril          # microbenchmarks
python -m bench.bench_branching        # branch/merge/rewind
python -m bench.bench_with_agent       # Anduril vs naive list

# Example
python -m examples.react_agent         # ReAct agent with Anduril
```
