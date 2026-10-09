"""ReAct-style agent using Anduril as the memory backend.

This is a minimal example showing how to wire Anduril into a
ReAct (Reason + Act) loop. The agent builds up state as it
iterates, and Anduril indexes each state transition as a node
in the persistent DAG.

The agent loop:
1. Start with a question/prompt
2. LLM proposes an action (tool call or "I have the answer")
3. Execute the action, observe the result
4. Append the new state to Anduril
5. Repeat until the LLM says "done"

The interesting Anduril operations this demo exercises:
- append: each new state becomes a child of the current root
- branch: when the agent considers alternative actions
- rewind: when an action fails and the agent backtracks
- merge: when the agent combines two investigation paths
- snapshot: to checkpoint the state for later inspection

This is a demo; in a real agent the LLM calls would be replaced
with calls to an actual model (transformers, OpenAI API, etc.).
Here we use a stub LLM that generates a deterministic sequence
of actions so the demo is reproducible.
"""
import sys
sys.path.insert(0, "src")

from anduril import Action, Anduril, Tier


# A stub LLM: given a state, returns the next action.
# Real usage: replace with model.generate(state) or openai.ChatCompletion.
def stub_llm_decide(state: dict) -> tuple[str, str]:
    """Decide the next action based on current state.

    Returns (action_kind, payload). The agent loop terminates when
    kind == "finish".
    """
    step = state.get("step", 0)
    if step == 0:
        return "tool_call", "search"
    if step == 1:
        return "tool_call", "compute"
    if step == 2:
        return "tool_call", "search_again"
    return "finish", "the answer is 42"


# A stub tool environment: maps action payloads to observations.
def stub_tool(action_kind: str, payload: str) -> str:
    """Execute a tool call. Returns the observation."""
    if action_kind == "tool_call":
        if payload == "search":
            return "found: the question asks for a number"
        if payload == "compute":
            return "intermediate: 6 * 7 = 42"
        if payload == "search_again":
            return "confirmed: 42 is correct"
    return f"unknown: {payload}"


def run_agent(question: str, max_steps: int = 10) -> Anduril:
    """Run a ReAct loop with Anduril as memory.

    Returns the Anduril instance with the full state history.
    """
    # Initial state
    a = Anduril(seed_state={
        "step": 0,
        "question": question,
        "history": [{"role": "user", "content": question}],
    }, initial_action=Action("init", "start"))

    current = a.root_id()
    for step in range(max_steps):
        state = a.get_state(current)
        action_kind, payload = stub_llm_decide(state)
        if action_kind == "finish":
            # Append the final answer
            current = a.append(current, Action("finish", payload), {
                **state,
                "step": state["step"] + 1,
                "history": state["history"] + [
                    {"role": "assistant", "content": payload}
                ],
            })
            break
        # Execute the action
        observation = stub_tool(action_kind, payload)
        # Append the new state (action + observation)
        current = a.append(current, Action(action_kind, payload), {
            "step": state["step"] + 1,
            "question": state["question"],
            "history": state["history"] + [
                {"role": "assistant", "content": f"call {payload}"},
                {"role": "tool", "content": observation},
            ],
            "last_observation": observation,
        })
    return a


def demo_branching():
    """Demo: when the agent considers an alternative action, branch."""
    print("=== Branching demo ===")
    a = Anduril(seed_state={"step": 0, "history": []},
                initial_action=Action("init"))
    r = a.append(a.root_id(), Action("tool_call", "search_A"),
                 {"step": 1, "history": ["tried A"]})
    # Branch: consider an alternative from the same point
    r_alt = a.branch(r, Action("tool_call", "search_B"),
                     {"step": 1, "history": ["tried B"]})
    # Both share the initial state (r_alt's grandparent = r's parent = init)
    init = a.ancestor(r, 1)  # r's parent = the initial state
    assert a.ancestor(r_alt, 2) == init  # r_alt's grandparent
    print(f"  initial state (shared): {a.get_state(init)}")
    print(f"  branch A:               {a.get_state(r)}")
    print(f"  branch B:               {a.get_state(r_alt)}")
    print(f"  initial refcount:       {a.refcount_of(init)} (1 + 2 branches)")
    print()


def demo_rewind():
    """Demo: when an action fails, rewind and try something else."""
    print("=== Rewind demo ===")
    a = Anduril(seed_state={"step": 0, "history": []},
                initial_action=Action("init"))
    r = a.append(a.root_id(), Action("tool_call", "search"),
                 {"step": 1, "history": ["tried"]})
    r = a.append(r, Action("tool_call", "compute"),
                 {"step": 2, "history": ["tried", "failed"]})
    r = a.append(r, Action("tool_call", "another"),
                 {"step": 3, "history": ["tried", "failed", "failed"]})
    print(f"  depth before rewind: {a.length_of(r)}")
    # Rewind to step 1 and try a different path
    rw = a.rewind(r, 2)
    print(f"  depth after rewind(2): {a.length_of(rw)}")
    # Continue from the rewind point
    new_path = a.append(rw, Action("tool_call", "different"),
                        {"step": 2, "history": ["tried", "different"]})
    print(f"  new path depth: {a.length_of(new_path)}")
    print(f"  new path state: {a.get_state(new_path)}")
    print()


def demo_merge():
    """Demo: when two investigation paths converge, merge them."""
    print("=== Merge demo ===")
    a = Anduril(seed_state={"step": 0, "history": []},
                initial_action=Action("init"))
    # Two parallel investigations
    r1 = a.append(a.root_id(), Action("tool_call", "path_A"),
                  {"step": 1, "history": ["A"]})
    r2 = a.append(a.root_id(), Action("tool_call", "path_B"),
                  {"step": 1, "history": ["B"]})
    # Both arrive at the same conclusion
    m = a.merge(r1, r2, Action("merge"),
                {"step": 2, "history": ["merged A and B"]})
    print(f"  merged state: {a.get_state(m)}")
    print(f"  parents: {a.parents_of(m)}")
    print()


def main():
    print("Anduril ReAct Agent Demo")
    print("=" * 50)
    print()

    # Demo 1: full agent run
    print("=== Full agent run ===")
    a = run_agent("What is 6 * 7?", max_steps=10)
    s = a.stats()
    # Find the leaf-most root: a root that has no children
    # (no other node lists it as first parent)
    final_root = None
    for rid in a.roots():
        if not a.children(rid):
            final_root = rid
            break
    if final_root is None:
        final_root = a.root_id()
    final_state = a.get_state(final_root)
    print(f"  final state: {final_state}")
    print(f"  stats: {s}")
    print()

    # Demo 2: branching
    demo_branching()

    # Demo 3: rewind
    demo_rewind()

    # Demo 4: merge
    demo_merge()


if __name__ == "__main__":
    main()
