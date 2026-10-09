# GitHub Issues — Anduril

Source: *Anduril: a persistent DAG for agent state with O(1) branching and rewind* (in preparation). Each issue below is referenced from the chapter that documents the design. Issues are ordered by priority (P0 = must-do, P1 = high-value, P2 = nice-to-have, P3 = bookkeeping).

When pasting into GitHub, replace `[BOOK-CITE]` with the actual chapter reference and link to https://github.com/lordxmen2k/anduril-agent-state.

---

## P0-01: Lock-free Rust port — implement the production data structure

**Priority:** P0 (blocks the entire research claim)
**Labels:** `enhancement`, `rust`, `lock-free`, `good first issue` (for the scaffolding tasks)
**Estimated effort:** ~3 months

### Background

The book claims the Anduril DAG is lock-free under multi-agent
load, but the entire `rust/` directory is scaffold-only. The
Python reference uses a per-DAG mutation lock for thread safety,
which is correct but not lock-free. The actual research claim
requires the Rust port with per-node CAS and crossbeam-epoch.

### What needs to be done

1. Initialize the `rust/` crate (already exists as scaffold)
2. Define the `StateNode` struct (or `StateNode` + `InternalNode`
   if we go with a 32-way internal node layout)
3. Implement the `mapping` table as `DashMap<NodeID, Arc<Node>>`
4. Implement `append`, `branch`, `merge`, `rewind`, `ancestor`,
   `children`, `snapshot`, `promote`, `demote`, `summarize`
5. Replace `drop_root` with epoch-based reclamation
6. Pass the same 88 tests as the Python reference (ported as
   `cargo test`)

### Acceptance criteria

- All 88 Python tests have Rust counterparts that pass
- The lock-free read path is verified: many readers don't block
  on the mutating path
- The structural-sharing invariants (refcount on shared nodes,
  no double-free) hold under concurrent load

### Book reference

Ch 5 §5.4, Ch 8 §8.3.

---

## P0-02: Implement `summarize()` as a real operation — currently uses default len()

**Priority:** P0
**Labels:** `enhancement`, `python`
**Estimated effort:** 1-2 weeks

### Background

The default `summarize()` in the Python reference just returns
`len(path)` — the number of states. A real summary is a
compressed representation of the path: a list of key events, a
truncated history, or an LLM-generated summary.

### What needs to be done

1. Define a `Summary` protocol with a `compress(path: list[State]) -> Summary` method
2. Implement built-in `LengthSummary` (current default), `FirstLastSummary`,
   `EventSummary` (extracts tool calls and observations), and
   `LLMSummary` (calls an LLM to generate a summary)
3. Add `set_summarizer(name)` and `summarize(root, summarizer_name=...)`
4. Add tests that the summary round-trips correctly

### Acceptance criteria

- `summarize()` with no argument uses a reasonable default
  (current `LengthSummary` is fine, but the default could be
  `EventSummary` instead)
- `LLMSummary` can be plugged in with a custom callable
- The summary is stored on the leaf-most node (current behavior)

### Book reference

Ch 6 §6.3, Ch 11 §11.2.

---

## P0-03: Replace deferred-free list with `guard.defer()` (Rust port)

**Priority:** P0
**Labels:** `enhancement`, `rust`, `memory-safety`
**Estimated effort:** 1 week (after P0-01 lands)

### Background

The Python reference uses a simple `list[NodeID]` as the
deferred-free list, drained by `collect_garbage()`. The Rust
port should use `crossbeam_epoch::Guard::defer()` for safe
memory reclamation.

### What needs to be done

1. When a node's refcount drops to 0, call `guard.defer(move || drop(node))`
2. Make `drop_root` use the current thread's guard
3. Add tests for the reclamation correctness

### Acceptance criteria

- No memory leaks: every dropped node is eventually freed
- No use-after-free: a node is not freed while a reader still
  holds a reference
- Performance: the reclamation overhead is <1% of the mutating op cost

### Book reference

Ch 8 §8.4.

---

## P0-04: Depth index for O(1) rewind

**Priority:** P0
**Labels:** `enhancement`, `performance`
**Estimated effort:** 1-2 weeks

### Background

Currently `ancestor(root, k)` is O(k) — it walks the parent
chain. For a 1000-step agent history, rewinding to step 500 is
500 hops. With a per-node depth index, this can be O(1) by
pre-computing the k-th ancestor at each node (binary lifting).

### What needs to be done

1. On each node, store a `depth_ancestors: [NodeID]` array of
   size `log2(max_depth)`
2. `depth_ancestors[i]` is the 2^i-th ancestor of this node
3. `ancestor(root, k)` becomes O(log k) using binary lifting
4. When a new node is created, populate its `depth_ancestors`
   from its parent's `depth_ancestors`

### Acceptance criteria

- `ancestor(root, k)` is O(log k) instead of O(k)
- For 1000-step chains, rewind to step 500 is < 20 hops
- The depth_ancestors array is at most 10 entries for 1000-step chains

### Book reference

Ch 6 §6.4, Ch 12 §12.3.

---

## P0-05: Atomic NodeID counter (`fetch_add(1, Relaxed)`)

**Priority:** P0
**Labels:** `enhancement`, `rust`
**Estimated effort:** 1 day

### Background

The Python reference uses `self._next_id += 1` under a lock.
The Rust port should use `AtomicU64::fetch_add(1, Ordering::Relaxed)`.

### What needs to be done

1. Replace the locked `_next_id` with an `AtomicU64`
2. Verify that NodeIDs are still never reused (the Bw-tree invariant)

### Acceptance criteria

- NodeIDs are unique across all nodes
- No two allocations return the same ID

### Book reference

Ch 5 §5.2.

---

## P0-06: 32-way internal node layout (alternative to single nodes)

**Priority:** P0
**Labels:** `enhancement`, `data-structure`
**Estimated effort:** 2-3 weeks

### Background

The current Python reference uses a single node per state
action. For dense data (e.g., a 10K-token conversation), this
is wasteful. A 32-way internal node layout groups 32 sibling
states into one node, reducing memory by ~32x.

### What needs to be done

1. Add an `InternalNode` type that holds 32 child NodeIDs
2. Add an `ExternalNode` type for the actual data
3. Implement `append` to fill a 32-slot before creating a new
   internal node
4. Add a `compact()` operation that merges single-child chains
   into a 32-way layout

### Acceptance criteria

- 10K-state agent history uses ~10K/32 = ~313 internal nodes
  instead of 10K single nodes
- All existing tests pass with the new layout

### Book reference

Ch 7 §7.3.

---

## P0-07: 16-way merge (multi-parent convergence)

**Priority:** P0
**Labels:** `enhancement`, `data-structure`
**Estimated effort:** 1 week

### Background

Currently `merge(root_a, root_b, ...)` takes exactly 2 parents.
For multi-agent collaboration where 3+ agents converge on the
same state, we'd want `merge_many(roots, ...)` that creates a
single node with N parents.

### What needs to be done

1. Add `merge_many(roots: list[NodeID], action, merged_state) -> NodeID`
2. The new node has `len(roots)` parents
3. The refcount on each parent is bumped by 1

### Acceptance criteria

- `merge_many([r1, r2, r3, r4], ...)` creates a node with 4 parents
- All parents have refcount = 2 after the merge
- The new node's depth is `max(r.depth for r in roots) + 1`

### Book reference

Ch 7 §7.4.

---

## P0-08: Snapshot persistence (save/restore)

**Priority:** P0
**Labels:** `enhancement`, `persistence`
**Estimated effort:** 2 weeks

### Background

The current `snapshot()` bumps the refcount to keep a node
alive. To persist a snapshot across process restarts (e.g.,
for long-running agents that survive crashes), we need
serialization.

### What needs to be done

1. Add `save_snapshot(root, path) -> None` that serializes the
   DAG (or just the path) to disk
2. Add `load_snapshot(path) -> Anduril, root` that deserializes
3. Use a compact format (msgpack, parquet, or custom binary)
4. Add tests for round-trip correctness

### Acceptance criteria

- A saved snapshot can be loaded in a fresh process and produce
  the same state
- The save format is documented and versioned
- Loading a snapshot with no shared state restores the full DAG

### Book reference

Ch 9 §9.3.

---

## P0-09: Subgraph extract (sub-DAG as a standalone DAG)

**Priority:** P0
**Labels:** `enhancement`, `data-structure`
**Estimated effort:** 1 week

### Background

When you rewind to step k and want to forget the more recent
history, you can `drop_root` the recent nodes. But what if you
want to extract a sub-DAG (e.g., the part of the history that's
relevant to a sub-task) and pass it to another agent?

### What needs to be done

1. Add `extract(root, k) -> Anduril` that creates a new
   Anduril instance with just the sub-DAG up to depth k
2. The new instance has the same structure as the relevant part
   of the original
3. The original is unchanged

### Acceptance criteria

- `extract(root, 5)` produces a new Anduril with depth 5
- The new Anduril has the same data as the first 5 states of root
- The original Anduril is unchanged

### Book reference

Ch 11 §11.4.

---

## P0-10: Tier-aware storage (real HBM/DRAM/SSD routing)

**Priority:** P0
**Labels:** `enhancement`, `storage`
**Estimated effort:** 4-6 weeks

### Background

The current `tier` field is just a tag on each node. A real
implementation would route reads/writes based on the tier:
HOT nodes go to HBM, WARM to CPU DRAM, COLD to NVMe SSD.

### What needs to be done

1. Add a `Storage` trait with `read(nid)`, `write(nid, data)`,
   `promote(nid)`, `demote(nid)` operations
2. Implement `HBMStorage`, `DRAMStorage`, `SSDStorage` backends
3. Route reads through the tier: HOT nodes hit HBM, WARM hits DRAM
4. Add background promotion/demotion based on access patterns

### Acceptance criteria

- A 10K-state agent history with 10% HOT, 60% WARM, 30% COLD
  uses ~10% of the HBM that a fully HOT design would use
- Reads to HOT nodes are < 1µs
- Reads to COLD nodes are < 10ms

### Book reference

Ch 8 §8.5, Ch 13 §13.3.

---

## P1-11: Integration with LangGraph

**Priority:** P1
**Labels:** `enhancement`, `integration`
**Estimated effort:** 1-2 weeks

### Background

LangGraph is a popular framework for building stateful,
multi-actor applications with LLMs. Its current state backend
is a `MemorySaver` (in-memory) or `PostgresSaver` (DB-backed).
Anduril could be a third option with better branching/rewind
support.

### What needs to be done

1. Implement a `langgraph.checkpoint.AndurilSaver` class
2. Map LangGraph's state model to Anduril's DAG
3. Test with a real LangGraph example

### Acceptance criteria

- A LangGraph example using AndurilSaver runs end-to-end
- Branching and rewinding work in the LangGraph API
- The AndurilSaver passes LangGraph's test suite

### Book reference

Ch 14 §14.2.

---

## P1-12: Integration with AutoGen

**Priority:** P1
**Labels:** `enhancement`, `integration`
**Estimated effort:** 1-2 weeks

### Background

AutoGen is a multi-agent framework from Microsoft. Its state
backend is currently per-conversation. Anduril's structural
sharing could let multiple AutoGen agents share substructure.

### What needs to be done

1. Implement a custom AutoGen state manager using Anduril
2. Map AutoGen's conversation model to Anduril's DAG
3. Test with a real AutoGen example

### Acceptance criteria

- An AutoGen example using Anduril runs end-to-end
- Multiple agents can branch from a shared state

### Book reference

Ch 14 §14.3.

---

## P1-13: ReAct demo with a real LLM

**Priority:** P1
**Labels:** `enhancement`, `demo`
**Estimated effort:** 3-5 days

### Background

The current `examples/react_agent.py` uses a stub LLM. A real
demo would use distilgpt2 (or similar) to show the agent
actually reasoning about actions.

### What needs to be done

1. Replace the stub LLM with a real transformers call
2. Add a tool environment that actually executes (e.g., a
   calculator, a web search)
3. Show the agent branching when it considers alternatives

### Acceptance criteria

- A real ReAct agent runs for 5+ steps using Anduril as memory
- The DAG has 10+ nodes after the demo
- The agent actually answers a question correctly

### Book reference

Ch 14 §14.4.

---

## P1-14: Comparison benchmark vs MemGPT, LangChain memory, AWM

**Priority:** P1
**Labels:** `benchmark`
**Estimated effort:** 2-3 weeks

### Background

The chapter needs a fair head-to-head comparison of Anduril
against the existing agent memory systems. The benchmark
should measure: setup time, branching cost, rewinding cost,
memory overhead, throughput.

### What needs to be done

1. Implement the same multi-turn task in MemGPT, LangChain
   memory, AWM, and Anduril
2. Measure all the above
3. Write up the results

### Acceptance criteria

- All four systems run the same workload
- The results table is reproducible
- The comparison is honest about what Anduril does and doesn't
  do better

### Book reference

Ch 15 §15.2.

---

## P1-15: Multi-process sharing (mmap or shared memory)

**Priority:** P1
**Labels:** `enhancement`, `concurrency`
**Estimated effort:** 2-3 weeks

### Background

A long-running agent might span multiple processes (the LLM
inference process, the tool execution process, the memory
process). They need to share the agent's state DAG.

### What needs to be done

1. Investigate mmap or shared memory for the mapping table
2. Implement a process-safe Anduril that can be shared via
   mmap
3. Verify that multiple processes can read concurrently

### Acceptance criteria

- Two processes can read the same Anduril DAG via mmap
- Writes from one process are visible to the other
- Performance is within 2x of single-process Anduril

### Book reference

Ch 16 §16.3.

---

## P1-16: Streaming snapshot (incremental serialization)

**Priority:** P1
**Labels:** `enhancement`, `persistence`
**Estimated effort:** 1 week

### Background

Saving a 10K-state DAG at once might take seconds. A streaming
snapshot would let you save incrementally and recover from
incomplete snapshots.

### What needs to be done

1. Add a `SnapshotWriter` class that writes nodes as they're
   visited
2. Add a `SnapshotReader` that can resume from the last
   successfully written node
3. Add tests for partial recovery

### Acceptance criteria

- A 10K-state DAG can be saved in < 100ms chunks
- An interrupted save can be resumed without re-saving
  already-saved nodes

### Book reference

Ch 9 §9.4.

---

## P2-17: Visualization tool (DAG viewer)

**Priority:** P2
**Labels:** `enhancement`, `tooling`
**Estimated effort:** 1 week

### Background

A visual representation of the agent's state DAG would help
debugging. Something like Graphviz output or a simple HTML
viewer.

### What needs to be done

1. Add a `to_graphviz()` method that returns a DOT representation
2. Add a simple HTML viewer that renders the DAG in the browser
3. Show nodes colored by tier, edges labeled by action

### Acceptance criteria

- `anduril.to_graphviz()` returns valid DOT
- A demo HTML page renders the DAG with hover-to-see-state

### Book reference

Ch 10 §10.3.

---

## P2-18: Compression (zstd or similar)

**Priority:** P2
**Labels:** `enhancement`, `storage`
**Estimated effort:** 3-5 days

### Background

A 10K-state DAG with large state payloads could be compressed
on disk. zstd or similar would give 2-5x reduction.

### What needs to be done

1. Add a `compress_states` config option
2. Compress state payloads on write
3. Decompress on read

### Acceptance criteria

- 10K-state DAG with 1KB states takes ~3MB on disk instead of
  ~10MB

### Book reference

Ch 13 §13.4.

---

## P2-19: Query language (filter the DAG by predicate)

**Priority:** P2
**Labels:** `enhancement`, `query`
**Estimated effort:** 1 week

### Background

A user might want to ask: "show me all states where the agent
called tool X." A small query language would let them do this
without walking the DAG manually.

### What needs to be done

1. Add `query(predicate) -> list[NodeID]` that walks the DAG
   and returns matching nodes
2. Predicate can be: action kind, state field, tier, depth
3. Add `query_actions(kind) -> list[NodeID]` shortcut

### Acceptance criteria

- `query_actions('tool_call')` returns all tool-call nodes
- `query(state_field='last_observation', value='failed')` returns
  nodes with the matching observation

### Book reference

Ch 10 §10.4.

---

## P2-20: SQLite backend for tiered storage

**Priority:** P2
**Labels:** `enhancement`, `storage`
**Estimated effort:** 1-2 weeks

### Background

For COLD-tier nodes that don't fit in HBM or DRAM, an SQLite
backend would let the DAG scale beyond RAM.

### What needs to be done

1. Implement `SQLiteStorage` that uses SQLite for COLD nodes
2. Add a config option `cold_backend='sqlite'`
3. Test with a DAG that's larger than RAM

### Acceptance criteria

- A DAG with 100K states (most COLD) uses < 1GB RAM
- COLD reads are < 50ms

### Book reference

Ch 13 §13.5.

---

## P3-21: Documentation site (mkdocs or similar)

**Priority:** P3
**Labels:** `docs`
**Estimated effort:** 3-5 days

### Background

A docs site (mkdocs, sphinx, docusaurus) would make the API
discoverable. Right now everything is in `README.md` and
`tech_note.md`.

### What needs to be done

1. Set up mkdocs with the `material` theme
2. Add API reference (auto-generated from docstrings)
3. Add tutorials for common operations
4. Add the design doc as a chapter

### Acceptance criteria

- `mkdocs serve` works locally
- The site includes API reference, tutorials, and the design

### Book reference

Ch 1 §1.4.

---

## P3-22: Performance optimization pass

**Priority:** P3
**Labels:** `performance`
**Estimated effort:** 1 week

### Background

After all the P0/P1 work is done, the Python reference will
have many small inefficiencies. A profiling pass would catch
the worst ones.

### What needs to be done

1. Profile the existing operations
2. Identify the top 5 hotspots
3. Optimize them (carefully, with tests)
4. Re-measure and report the speedup

### Acceptance criteria

- All 88 tests still pass
- At least 2x speedup on the most common operations
- The optimizations are documented in code comments

### Book reference

Ch 17 §17.3.

---

## P3-23: Type stubs (.pyi) for editor support

**Priority:** P3
**Labels:** `tooling`
**Estimated effort:** 2-3 days

### Background

The Python reference has type hints but no `.pyi` stubs.
Editors can use stubs for better autocomplete and type
checking.

### What needs to be done

1. Generate or write `.pyi` files for the public API
2. Verify mypy/pyright is happy

### Acceptance criteria

- A user gets autocomplete on `Anduril.append` in their editor
- mypy --strict passes on the stubs

---

## P3-24: CI/CD (GitHub Actions)

**Priority:** P3
**Labels:** `ci`
**Estimated effort:** 2-3 days

### Background

Currently no CI. Tests are run manually.

### What needs to be done

1. Add `.github/workflows/test.yml`
2. Run on every push and PR
3. Run on Python 3.10, 3.11, 3.12, 3.13, 3.14

### Acceptance criteria

- CI runs on every push
- All 88 tests pass on CI
- The CI badge is in the README

---

## P3-25: Test for the test-anduril-tiers.py uncovered paths

**Priority:** P3
**Labels:** `tests`
**Estimated effort:** 1 day

### Background

Coverage report shows 79% on the Python reference. The
uncovered paths are mostly error-handling code.

### What needs to be done

1. Run `pytest --cov=src/anduril` and identify uncovered lines
2. Add tests for the uncovered paths
3. Aim for 90%+ coverage

### Acceptance criteria

- Coverage is 90%+
- All uncovered lines have a corresponding test
