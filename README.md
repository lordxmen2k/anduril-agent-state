# Anduril — a persistent DAG for agent state

*"Narsil was broken; the shards were kept; Anduril was reforged."*

A persistent directed acyclic graph (DAG) for indexing the state of
long-running agents. Mutations return a new immutable root; the old
roots remain valid. Multiple agents, branches, and rewinds share
substructure via reference counting, so forking an agent at step 50
is O(1) regardless of how long its history is.

**Status:** Phase 1 implemented. Python reference with 88 passing
tests, full bench suite, and ReAct agent demo. The data structure
model is specified in `tech_note.md`.

## Performance numbers

See [`NUMBERS.md`](./NUMBERS.md) for a quick-reference summary of all
benchmarks. The full report with caveats and methodology is in
[`bench/REPORT.md`](./bench/REPORT.md).

**Headline numbers:**
- Branch from any chain length: **~720K ops/sec, O(1)**
- 100 branches of 100-state prefix: **201 nodes** (vs 20,000 for naive list)
- 88 tests pass in 4 seconds

## Why

Today, every agent framework (LangGraph, AutoGen, CrewAI, MemGPT,
AWM) treats agent state as a **list of messages**. Append, maybe
summarize, sometimes truncate. The actual state of an agent is a
**DAG** — most agents are linear chains, some are trees (try
tool-A vs tool-B), a few are real DAGs (multiple paths converging).
The list-of-messages abstraction throws the structure away.

The cost:
- Context overflow at 50-200 turns
- No cheap branching (every alternative trajectory is a full copy)
- No cheap rewind (going back means re-executing or full snapshots)

Anduril makes branching, rewinding, and merging O(1) operations on
the data structure.

## The metaphor

In Tolkien's *Lord of the Rings*, Narsil was the sword of Elendil
that broke beneath Sauron. Its shards were kept for three thousand
years. They were reforged into Anduril, "Flame of the West," and
given to Aragorn — the returning king — to rally the free peoples.

The data structure mirrors that:
- **Narsil (the broken state)** is an agent's history at the end of
  a failed task. The structure is intact, the task is not.
- **The shards were kept** — we never discard history, we keep
  every node reachable.
- **Anduril (the reforged state)** is a new root that references
  the shards structurally. The shards don't move; the new root
  is just a new "view" into the same DAG.
- **Aragorn reveals it when it's time** — the data structure
  stays immutable until queried, then a fresh root can be
  forged from any prior point.

## What ships in v0

- [ ] `tech_note.md` — design proposal (draft v0.1)
- [ ] `Anduril` class — persistent DAG with structural sharing
- [ ] `append`, `branch`, `merge`, `rewind`, `ancestor` operations
- [ ] tiered storage (`hot`, `warm`, `cold`)
- [ ] unit tests + property tests + concurrent stress tests
- [ ] comparison to MemGPT, LangChain memory, AWM
- [ ] example ReAct agent using Anduril as the memory backend

## Project layout

```
Anduril-Agent-DAG/
  .gitignore
  LICENSE                                Apache 2.0
  README.md                              this file
  tech_note.md                           design proposal
  GITHUB_ISSUES.md                       prioritized issues P0–P3
  pyproject.toml                         package metadata
  src/anduril/
    __init__.py                          public API
    types.py                             NodeID, StateRef, Action, NULL_STATE
    core.py                              Anduril (the persistent DAG class)
    tiers.py                             hot/warm/cold tier management
  tests/
    test_anduril_basic.py                unit tests (append/branch/rewind)
    test_anduril_property.py             property tests
    test_anduril_concurrent.py           multi-threaded stress
    test_anduril_tiers.py                hot/warm/cold transition tests
  examples/
    react_agent.py                       ReAct agent with Anduril memory
  docs/
    persistence_model.md                 how persistence + sharing work
    branching_model.md                   how branching and merge work
    comparison.md                        vs MemGPT, LangChain memory, AWM
```

## License

Apache 2.0. See [`LICENSE`](./LICENSE).

## Author

Gerald Enrique Nelson Mc Kenzie — lordxmen2k@gmail.com
