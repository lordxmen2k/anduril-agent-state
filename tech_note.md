# Anduril: a persistent DAG for agent state — design note (draft v0.1)

**Author:** Gerald Enrique Nelson Mc Kenzie
**Date:** 2026-10-09
**Status:** draft v0.1 — design only, no implementation yet
**Target venue:** technical book / research paper

## 1. Motivation

Long-running agents accumulate state. The state is a record of
what the agent did, what it observed, and what it decided. In
practice the state is one of:

- **A linear chain** — ReAct, plan-and-execute, simple chat
- **A tree** — parallel tool exploration, beam search over actions
- **A DAG** — multi-path with merges (search-A and search-B both
  re-derive the same conclusion, or two agents collaborate and
  their trajectories merge)

Today, every mainstream framework (LangGraph, AutoGen, CrewAI,
MemGPT, AWM) flattens the state to a **list of messages**. The
list grows linearly, hits the context limit, and is summarized,
truncated, or re-sent in full. The structure of the actual
trajectory is lost.

We argue that a list is the wrong data structure. The right data
structure is a **persistent DAG** with structural sharing, where:

- Branching an agent at step `k` is O(1) — same memory cost as
  one extra root
- Rewinding to step `k` is O(1) — just take the k-th ancestor
- Merging two branches is O(1) if they share a recent prefix
- Querying "what did I try at this point?" is a tree walk, not a
  linear scan

The contribution is a **published, vendor-neutral reference
design** of a persistent DAG for agent state, with the operations,
invariants, and benchmarks spelled out. No such design exists in
the literature today.

## 2. The data structure

A **persistent DAG** where:

- Each node `n = (state, action, parent_refs)` holds
  - `state`: the conversation at this point (possibly summarized)
  - `action`: the tool call or LLM response that produced `n`
  - `parent_refs`: 1+ NodeIDs (linear = 1 parent, tree = 1 parent,
    DAG = 2+ parents for merges)
  - `refcount`: how many live roots reference this node
  - `tier`: HOT / WARM / COLD for the tiered storage

- Operations:
  - `append(root, action, result) → new_root` — extends the chain
  - `branch(root, alternative_action) → new_root` — fork from any
    point
  - `merge(root_a, root_b) → new_root` — converge two branches
  - `ancestor(root, k) → root_at_k` — rewind to depth k
  - `children(root) → [NodeID]` — query the branching structure
  - `summarize(root, k) → new_root` — collapse a subtree into a
    summary (the data structure itself does the compression)
  - `snapshot(root) → root` — capture an immutable reference

- Invariants:
  - **No mutation in place.** Every operation returns a new root.
  - **Structural sharing is explicit.** Each node carries a
    refcount; when refcount drops to 0 and no live reader exists,
    the node is reclaimable.
  - **Logical IDs are stable.** A NodeID, once issued, is never
    reused. (Bw-tree invariant — prevents ABA in lock-free reads.)
  - **The DAG is a DAG.** Cycles are forbidden at the API level
    (an action cannot reference its own ancestor as a parent).

## 3. Tiered storage

The state grows. We can't keep everything at full fidelity in
GPU HBM. We propose three tiers:

| tier | what's stored | latency | cost |
|---|---|---|---|
| HOT | full state, in HBM | µs | highest per byte |
| WARM | full state, in CPU DRAM, summarized on demand | ms | medium |
| COLD | full state in NVMe SSD, summaries in DRAM | 10 ms | lowest per byte |

A node's tier is set by `promote(node)` / `demote(node)` and is
*recorded in the data structure itself*, not at the storage layer.
The lookup operation knows the tier and routes accordingly. This
is in contrast to Mooncake/LMCache which put tier decisions at
the block-storage layer.

## 4. What transfers from rope-style data structures

Some principles from Bw-tree / persistent-data-structure work
transfer cleanly:

- **Persistent roots** — every operation returns a new root
- **Structural sharing via refcount** — subtrees shared across
  roots
- **Bw-tree mapping table** — logical NodeID → physical node,
  decoupled. Enables compaction (reorganize for locality) without
  invalidating any in-flight reference.
- **Tiered storage** — hot/warm/cold at the index layer, not the
  block-storage layer

What does NOT transfer:

- **Rebalance to O(log n)** — agent state is append-heavy, not
  random-position queries
- **Binary tree shape** — a DAG is the natural shape for agent
  state, not a binary tree
- **Block-based leaves** — agent state is variable-size messages,
  not 16-token blocks

## 5. What's new vs existing agent memory systems

| system | what it does | what's missing |
|---|---|---|
| **MemGPT** (Packer '23) | hierarchical memory tiers, LLM-driven paging | still a flat list at the top level; no DAG structure |
| **AWM** (Wang '24) | past trajectories as in-context examples | examples are sampled, not queryable as a tree |
| **Generative Agents** (Park '23) | memory stream + importance scoring | linear stream with decay, no branching |
| **LangGraph** (LangChain) | message list + optional summarization | no native branching or rewind |
| **LangGraph checkpointer** | saves state per node, can resume | each branch is a separate copy; no structural sharing |
| **AutoGen** | multi-agent state, manual context passing | state is duplicated across agents |
| **Anduril** (this work) | persistent DAG with structural sharing, tiered storage | not built yet |

The unique contribution is the **combination of persistence +
structural sharing + DAG shape + tiered index**, all four together.

## 6. What the benchmarks will measure

1. **Append throughput** — operations per second on a single thread
2. **Branch cost** — time and memory to fork from step k
3. **Rewind cost** — time to ancestor(k) for k = 10, 100, 1000
4. **Merge cost** — time to merge two branches with shared prefix
5. **Concurrent stress** — many threads appending/branching/reading
6. **Tier transition** — cost of promote/demote operations
7. **Real-agent integration** — ReAct agent running a multi-step
   task with Anduril as the memory backend

## 7. Open questions (resolved)

1. **Summarization at internal nodes** — *Lazy, on-demand.* The
   default state of an internal node has no summary. The
   `summarize()` operation generates one on demand. The lookup
   path falls back to the parent's state when no summary exists.
   This gives flexibility without paying for it when not needed.

2. **Depth indexing for O(1) rewind** — *Store `depth` as a field
   on every node.* `ancestor(root, k)` becomes an O(k) walk up
   the chain (each hop is a single field read), but predictable
   and cache-friendly. True O(1) via a per-node ancestor index is
   too much overhead for a Python reference; documented as
   P0-04 in `GITHUB_ISSUES.md` for the Rust port.

3. **Lock-free reads** — *Python reference uses a per-DAG
   mutation lock* (same shape as the rope project). Reads are
   lock-free. True lock-free CAS is the Rust port's claim
   (P0-01 in issues file).

4. **Cross-agent shared state** — *Reference counting just
   works.* Two agents that share a subtree both bump the
   refcount; when both drop, refcount=0 and the node is
   reclaimable. No special "cross-agent" handling needed.

## 8. Next steps

1. Lock the data structure model (this document)
2. Build a 200-line Python reference of the core class
3. Add 30+ unit tests, 9 property tests, 9 concurrent stress tests
4. Integrate with a real agent framework (LangGraph or AutoGen)
5. Run benchmarks, capture numbers
6. Write a paper / chapter

## 9. References

To be filled in. The closest prior art is the Bw-tree paper
(Levy et al. 2005), SGLang's RadixAttention (Zheng et al. 2024),
MemGPT (Packer et al. 2023), and the persistent data structures
literature (Okasaki 1999, Driscoll et al. 1989).
