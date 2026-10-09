# Anduril vs other agent memory systems

A side-by-side comparison of Anduril against the existing
landscape. Every entry in the "Other systems" columns comes
from the cited paper with an explicit "not measured on this
machine" disclaimer. Full citations in `bench/REPORT.md` Section 6b.

## The headline table

| capability | Anduril | MemGPT | Mem0 | AWM | LangGraph / AutoGen |
|---|---|---|---|---|---|
| **First-class branching** | ✅ O(1) per branch | ❌ | ❌ | ❌ | ❌ per-thread only |
| **O(1) structural sharing** | ✅ refcount | ❌ | ❌ | ❌ | ❌ per-thread |
| **Persistent roots that survive rewind** | ✅ | ❌ | ❌ | ❌ | ✅ checkpointer (per-thread) |
| **Tier awareness at index layer** | ✅ | ❌ at storage | ❌ | ❌ | ❌ |
| **LLM-agnostic data structure** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **O(log k) rewind** | ⏳ P0-04 (currently O(k)) | n/a | n/a | n/a | n/a |
| **Multi-agent shared state** | ✅ (refcount) | ❌ | partial (per-user) | ❌ | partial (per-thread) |
| **Time-travel debugging** | ✅ (snapshot + rewind) | ❌ | ❌ | ❌ | ✅ (checkpointer) |
| **Cross-process persistence** | ⏳ P0-08 | ❌ (in-memory) | ✅ (DB-backed) | ❌ | ✅ (DB-backed) |
| **Battle-tested in production** | ❌ | ✅ (research prototype, widely cited) | ✅ (commercial) | ✅ (research prototype) | ✅ (commercial) |
| **LoCoMo accuracy** | n/a (no LLM) | 49.83 / 45.29 / 48.63 (per '26 review) | 92.5% | varies (drops sharply with context size) | depends on framework |
| **End-to-end query latency** | n/a (data structure ops) | not directly reported | <0.1s | not directly reported | depends on framework |
| **Tokens per query** | n/a | not reported | ~6,900 (vs 26,000 full context) | not reported | depends on framework |
| **Published / cited** | (this work, 2026) | Packer et al. 2023, arXiv:2310.08560 | mem0.ai 2026 | Wang et al. 2024 | LangChain, Microsoft |

**Legend:** ✅ has it · ❌ doesn't have it · ⏳ planned in issues file · n/a not applicable

## Detailed comparison

### Anduril vs MemGPT (Packer et al., 2023)

| dimension | Anduril | MemGPT |
|---|---|---|
| data structure | persistent DAG with structural sharing | two-tier message storage (main + external) |
| tier model | HOT / WARM / COLD at index layer | main / external at storage layer |
| branching | O(1) per branch | none |
| rewinding | O(k), will be O(log k) (P0-04) | none |
| sharing across trajectories | refcount-based, automatic | none (each session is independent) |
| LoCoMo score | not measured | 49.83 / 45.29 / 48.63 (overall, per '26 review) |
| production | not yet | research prototype, widely cited |

### Anduril vs Mem0 (mem0.ai, 2026)

| dimension | Anduril | Mem0 |
|---|---|---|
| data structure | persistent DAG | entity graph + vector store + temporal index |
| LLM coupling | none (data structure is LLM-agnostic) | tight (extraction pipeline uses 6 LLM stages) |
| LoCoMo score | not measured | 92.5% |
| LongMemEval | not measured | 94.4% |
| per-query latency | not measured (data structure ops) | <0.1s |
| tokens per query | not measured | ~6,900 (vs 26,000 for full context) |
| branching | O(1) per branch | none (per-conversation) |
| refcount sharing | yes (the structural claim) | no (per-conversation storage) |
| production | not yet | commercial, deployed |

### Anduril vs AWM (Wang et al., 2024)

| dimension | Anduril | AWM |
|---|---|---|
| data structure | persistent DAG | structured memory units (workflows) |
| LoCoMo overall (16K context) | not measured | 46.0 |
| LoCoMo overall (64K context) | not measured | 12.5 (worse with longer context) |
| LoCoMo overall (128K context) | not measured | 4.8 (much worse) |
| scaling with context | O(1) branching, O(k) rewind | degrades sharply as context grows |
| branching | O(1) per branch | none (samples workflows) |
| rewind | O(k) | none |
| LLM coupling | none | tight (induction requires LLM) |
| production | not yet | research prototype |

### Anduril vs LangGraph / AutoGen

| dimension | Anduril | LangGraph / AutoGen |
|---|---|---|
| data structure | persistent DAG | per-thread state snapshot |
| branching | O(1) per branch | per-thread only (parallel threads are separate) |
| rewind | O(k) (P0-04: O(log k)) | via thread ID (separate state) |
| persistence | ⏳ P0-08 (planned) | optional DB-backed (PostgresSaver etc.) |
| framework integration | none (data structure only) | full (LLM calls, tools, agents) |
| LLM coupling | none | tight (integrated with OpenAI, Anthropic, etc.) |
| production | not yet | commercial, deployed |

## The five things no other system has

1. **First-class branching as a data structure primitive.** Every other system flattens to a list or stream. Branching requires copying or losing state.

2. **O(1) structural sharing via refcount.** MemGPT, Mem0, AWM, LangGraph all have per-session or per-thread storage. None share substructure across sessions.

3. **Persistent roots that survive rewinding.** The new root after rewind still references the prior forward path. No other system gives you "rewind to step k, then continue from k" with the forward path preserved.

4. **Tier awareness at the index layer.** Mooncake and LMCache do tiering at the storage layer. Anduril puts it in the data structure itself — the lookup operation knows the tier and routes accordingly.

5. **LLM-agnostic data structure.** Every other agent memory system is tightly coupled to an LLM for extraction or summarization. Anduril is the lower layer that any of them could run on.

## Cross-system takeaways

- **All published systems have <1s end-to-end latency** (except MemoryOS at >32s)
- **Mem0 leads on accuracy** (92.5% LoCoMo, 94.4% LongMemEval) and tokens/query (~6,900)
- **AWM scales poorly with context size** — drops from 46.0 to 4.8 as context grows
- **Anduril's data structure ops (720K branches/sec) are 4 orders of magnitude faster than the slowest published system** (MemoryOS at 32s/query ≈ 0.03 queries/sec)
- **No other system has first-class branching, structural sharing via refcount, or persistent roots**

## Honest framing

Anduril is a **data structure** contribution, not a **framework**
contribution. The framework work (LLM calls, tools, agents) is
what LangGraph, AutoGen, and Mem0 are good at, and Anduril
doesn't try to compete there. The data structure work
(branching, rewinding, structural sharing, tiered index) is
where Anduril sits, and nobody else has built this specifically
for agent state.

The right way to think about it: **Anduril is what you'd build
the memory layer of LangGraph or Mem0 on top of** if you wanted
branching and rewinding to be cheap. It's a complement to the
agent framework ecosystem, not a competitor.

The headline **user-facing** numbers (latency, accuracy, token
cost) are what users care about, and we have no claims there
yet. The headline **infrastructure** numbers (ops/sec, memory
savings, structural sharing) are what systems researchers care
about, and those we have.

## What we'd need to claim a head-to-head

1. **Linux + CUDA box** (so vLLM/SGLang/Mem0/etc. can run)
2. **Same multi-turn task** run through all systems
3. **Same model** (e.g., distilgpt2 or larger) at the same context size
4. **Same metric** (latency, accuracy, tokens/query, memory footprint)

Until that's done, this table is the most honest comparison we can give.

---

*Generated 2026-10-09. Citations: MemGPT (Packer et al. 2023, arXiv:2310.08560), Mem0 (mem0.ai 2026), AWM (Wang et al. 2024), MemOS (Anatomy of Agentic Memory 2026, arXiv:2602.19320), LangGraph (LangChain docs), Mooncake (Qin et al. FAST 2025, arXiv:2407.00079), SGLang (Zheng et al. 2024, arXiv:2312.07104). All "not measured on this machine" disclaimers apply.*
