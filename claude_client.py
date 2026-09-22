"""
claude_client.py
Thin shared wrapper around the Anthropic API for the DD1391 pipeline
(dd1391_parser.py Phase 1, award_matcher.py Phase 2). Centralizes:
  - model routing (Haiku by default everywhere; Sonnet only where a
    caller explicitly escalates - see CRITERIA.md cost controls)
  - tool-forced structured output (never free-text JSON parsing)
  - a running cost estimate printed to the terminal during batch runs

Pricing constants are approximate (per published list price, not exact
invoiced cost) and exist ONLY to give a running terminal estimate so a
batch can be stopped early if it's trending over budget - not for billing.
"""

import os

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_HAIKU_MODEL, CLAUDE_SONNET_MODEL

# $ per million tokens, list price at time of writing.
PRICING = {
    CLAUDE_HAIKU_MODEL: {"input": 1.00, "output": 5.00},
    CLAUDE_SONNET_MODEL: {"input": 3.00, "output": 15.00},
}


class CostTracker:
    def __init__(self, budget_usd: float = None):
        self.budget_usd = budget_usd
        self.total_usd = 0.0
        self.calls = 0

    def add(self, model: str, usage) -> float:
        rates = PRICING.get(model, {"input": 3.00, "output": 15.00})
        cost = (usage.input_tokens / 1_000_000) * rates["input"] + \
               (usage.output_tokens / 1_000_000) * rates["output"]
        self.total_usd += cost
        self.calls += 1
        print(f"    [{model}] {usage.input_tokens} in / {usage.output_tokens} out "
              f"-> ${cost:.4f} (running total: ${self.total_usd:.4f})")
        if self.budget_usd and self.total_usd > self.budget_usd:
            raise RuntimeError(
                f"Cost budget exceeded: ${self.total_usd:.2f} > ${self.budget_usd:.2f} budget. "
                "Stopping before making another API call."
            )
        return cost


_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set - add it to .env")
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def call_tool(model: str, system: str, content, tool: dict, tracker: CostTracker,
              max_tokens: int = 4096):
    """
    Call Claude with a single forced tool call for structured output.
    `content` is either a plain string (text-only) or a list of Anthropic
    content blocks (for vision: text + image blocks).
    Returns the tool's input dict (the structured extraction/ranking), or
    raises if Claude didn't call the tool.
    """
    client = get_client()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": content}],
    )
    tracker.add(model, message.usage)

    for block in message.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            return block.input
    raise RuntimeError(f"Claude did not call the expected tool '{tool['name']}'")


REPORT_AWARD_RESEARCH_TOOL = {
    "name": "report_award_research",
    "description": "Report what you found after researching a MILCON project's award online.",
    "input_schema": {
        "type": "object",
        "properties": {
            "found": {"type": "boolean", "description": "Whether you found anything conclusive about this specific project's award"},
            "mechanism": {"type": "string", "enum": ["standard_contract", "ota", "unknown"]},
            "awardee": {"type": "string", "description": "Company/JV name that won the award, if found"},
            "piid": {"type": "string", "description": "The contract/task order PIID, ONLY if mechanism=standard_contract"},
            "notice_id": {"type": "string", "description": "The OTA agreement/notice number, ONLY if mechanism=ota (a DIU OTA has no PIID - don't force one)"},
            "status_note": {"type": "string", "description": "Only set if research suggests the project is 'not constructed', 'descoped', or 'cancelled'"},
            "reasoning": {"type": "string", "description": "1-2 sentences citing what you found and where (e.g. a NAVFAC press release, a SAM.gov notice)"},
        },
        "required": ["found", "mechanism", "reasoning"],
    },
}


def web_research_award(query: str, tracker: CostTracker, model: str = None) -> dict:
    """
    Used when the hard filter in award_matcher.py finds 0 candidates.
    Does its own research in ONE Claude call (never looped/retried
    automatically, per CRITERIA.md) - Claude may issue several underlying
    web searches within that single call, first generally (who was this
    awarded to, what's the contract/task order number) and then
    specifically checking sam.gov to confirm. Distinguishes a standard
    contract (PIID) from a non-standard Defense Innovation Unit OTA
    (notice_id instead - forcing an OTA into a PIID field would be the
    wrong shape for it, see schema.py's notice_id column).
    Returns the structured report_award_research tool input, or a
    best-effort {"found": False, ...} dict if Claude never called it.
    """
    model = model or CLAUDE_SONNET_MODEL  # web research needs a model capable of good synthesis
    client = get_client()
    message = client.messages.create(
        model=model,
        max_tokens=1500,
        tools=[
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 4},
            REPORT_AWARD_RESEARCH_TOOL,
        ],
        tool_choice={"type": "auto"},
        messages=[{
            "role": "user",
            "content": (
                f"Research this MILCON project's award: {query}\n\n"
                "First search the web generally for news/press releases about who this "
                "project was awarded to and any contract/task order number. Then "
                "specifically search sam.gov for that award to confirm the details. If "
                "it was awarded through a Defense Innovation Unit Other Transactional "
                "Authority (OTA) rather than a standard contract, report whatever "
                "notice/agreement number exists as notice_id, not piid. If you see "
                "language like 'not constructed', 'descoped', or 'cancelled', report "
                "that in status_note. When done researching, call "
                "report_award_research with your findings - do not guess if you "
                "found nothing conclusive."
            ),
        }],
    )
    tracker.add(model, message.usage)
    for block in message.content:
        if block.type == "tool_use" and block.name == "report_award_research":
            return block.input
    text = "".join(b.text for b in message.content if b.type == "text")
    return {"found": False, "mechanism": "unknown", "reasoning": text or "No structured result returned."}


def web_search_once(query: str, tracker: CostTracker, model: str = None) -> str:
    """Kept for callers that just want a free-text summary from a single
    targeted search (e.g. app.py's base-name research). See
    web_research_award() for the structured award-lookup version."""
    model = model or CLAUDE_SONNET_MODEL  # web search needs a model capable of good synthesis
    client = get_client()
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
        messages=[{
            "role": "user",
            "content": (
                f"Search for: {query}\n\n"
                "Report ONLY what you find. If you find nothing relevant, say that "
                "plainly - do not guess."
            ),
        }],
    )
    tracker.add(model, message.usage)
    return "".join(b.text for b in message.content if b.type == "text")
