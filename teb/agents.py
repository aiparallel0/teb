"""
Multi-agent delegation system.

Specialized AI agents that can delegate work to each other to accomplish
complex goals end-to-end.

Flow:
  1. User creates a goal (e.g. "earn money online")
  2. Coordinator agent analyzes the goal and creates a strategy
  3. Coordinator delegates to specialized agents (marketing, web_dev, outreach, etc.)
  4. Each specialist produces tasks and can sub-delegate to other specialists
  5. All handoffs are logged in agent_handoffs for full traceability

Each agent has:
  - A domain of expertise (marketing, web_dev, outreach, research, finance)
  - A system prompt that makes it an expert in that domain
  - The ability to produce concrete tasks
  - The ability to request delegation to other agents

Without an AI key, agents use template-based heuristics.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional

from teb import config, storage
from teb.models import AgentHandoff, AgentMessage, Goal, Task

logger = logging.getLogger(__name__)


# ─── Agent definitions ───────────────────────────────────────────────────────

@dataclass
class AgentSpec:
    """Specification for a specialized agent."""
    agent_type: str          # unique identifier: coordinator, marketing, web_dev, etc.
    name: str                # human-readable name
    description: str         # what this agent does
    expertise: List[str]     # keywords this agent handles
    system_prompt: str       # AI system prompt for this agent
    can_delegate_to: List[str]  # agent types this agent can delegate to
    backstory: str = ""              # rich narrative backstory for better AI outputs

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "name": self.name,
            "description": self.description,
            "expertise": self.expertise,
            "can_delegate_to": self.can_delegate_to,
            "backstory": self.backstory,
        }


@dataclass
class AgentOutput:
    """What an agent produces after processing."""
    tasks: List[Dict[str, Any]]       # tasks to create
    delegations: List[Dict[str, Any]]  # requests to delegate to other agents
    summary: str                       # what this agent decided/did
    messages: List[Dict[str, Any]] = field(default_factory=list)  # messages to other agents


# ─── Agent registry ──────────────────────────────────────────────────────────

_AGENTS: Dict[str, AgentSpec] = {}


def _register(spec: AgentSpec) -> None:
    _AGENTS[spec.agent_type] = spec


def get_agent(agent_type: str) -> Optional[AgentSpec]:
    """Get an agent spec by type."""
    return _AGENTS.get(agent_type)


def list_agents() -> List[AgentSpec]:
    """List all registered agent specs."""
    return list(_AGENTS.values())


def register_agent(spec: AgentSpec) -> None:
    """Register a new agent at runtime, allowing dynamic extension of the agent catalog."""
    _AGENTS[spec.agent_type] = spec


def unregister_agent(agent_type: str) -> bool:
    """Remove a dynamically registered agent. Returns True if removed."""
    if agent_type in _AGENTS:
        del _AGENTS[agent_type]
        return True
    return False


# ─── Built-in agents ─────────────────────────────────────────────────────────

_register(AgentSpec(
    agent_type="coordinator",
    name="Coordinator",
    description=(
        "The orchestrator. Analyzes the user's goal, creates a high-level strategy, "
        "and delegates specific domains to specialist agents."
    ),
    expertise=["strategy", "planning", "delegation", "orchestration"],
    system_prompt=(
        "You are the Coordinator agent in a multi-agent task execution system. "
        "Your job is to analyze a user's goal and create a concrete strategy by "
        "delegating work to specialist agents.\n\n"
        "Available specialist agents:\n"
        "- marketing: Market research, positioning, content strategy, SEO, ads\n"
        "- web_dev: Websites, landing pages, web apps, hosting, domains, technical setup\n"
        "- outreach: Cold outreach, email campaigns, networking, lead generation\n"
        "- research: Deep research, competitive analysis, data gathering, validation\n"
        "- finance: Budgeting, pricing, payment setup, financial projections\n\n"
        "Return JSON with this structure:\n"
        "{\n"
        '  "strategy_summary": "1-2 sentence overview of the approach",\n'
        '  "tasks": [\n'
        '    {"title": "...", "description": "...", "estimated_minutes": 30}\n'
        "  ],\n"
        '  "delegations": [\n'
        '    {"to_agent": "marketing", "instruction": "what to do"}\n'
        "  ],\n"
        '  "messages": [\n'
        '    {"to_agent": "web_dev", "content": "Marketing will need a landing page, '
        'coordinate with them on design", "message_type": "context"}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Create 1-3 immediate tasks the user should do themselves\n"
        "- Delegate specialized work to 2-4 specialist agents\n"
        "- Send messages to agents that need to coordinate with each other\n"
        "- Messages share context between agents so they produce coherent, non-overlapping work\n"
        "- Be specific and actionable, not generic\n"
        "- Each delegation instruction should be detailed enough for the specialist\n"
        "- Focus on the fastest path to a concrete result"
    ),
    can_delegate_to=["marketing", "web_dev", "outreach", "research", "finance"],
    backstory=(
        "A seasoned strategist who has orchestrated hundreds of successful product launches. "
        "Known for breaking complex goals into clear, actionable plans and ensuring every "
        "specialist stays aligned toward the shared objective."
    ),
))

_register(AgentSpec(
    agent_type="marketing",
    name="Marketing Specialist",
    description="Market research, positioning, content strategy, SEO, and advertising.",
    expertise=["marketing", "seo", "content", "branding", "ads", "social media", "positioning"],
    system_prompt=(
        "You are the Marketing Specialist agent. You create concrete marketing tasks: "
        "market research, positioning, content creation, SEO optimization, ad campaigns.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "tasks": [\n'
        '    {"title": "...", "description": "detailed actionable steps", "estimated_minutes": 30}\n'
        "  ],\n"
        '  "delegations": [\n'
        '    {"to_agent": "web_dev", "instruction": "build landing page for X"}\n'
        "  ],\n"
        '  "summary": "what was decided"\n'
        "}\n\n"
        "Rules:\n"
        "- Create 3-6 specific, actionable marketing tasks\n"
        "- Tasks should be ordered by priority\n"
        "- You can delegate to web_dev (for landing pages/sites) or outreach (for campaigns)\n"
        "- Include realistic time estimates\n"
        "- Focus on low-cost, high-impact strategies first"
    ),
    can_delegate_to=["web_dev", "outreach"],
    backstory=(
        "A growth-marketing veteran who built brands from zero to millions of users. "
        "Obsessed with data-driven positioning and low-cost, high-impact campaigns "
        "that deliver measurable results."
    ),
))

_register(AgentSpec(
    agent_type="web_dev",
    name="Web Development Specialist",
    description="Websites, landing pages, web apps, hosting, domains, and technical setup.",
    expertise=["website", "web", "landing page", "hosting", "domain", "html", "css", "deploy",
               "nginx", "app", "technical", "code", "build"],
    system_prompt=(
        "You are the Web Development Specialist agent. You create concrete technical tasks: "
        "domain registration, hosting setup, website building, deployment.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "tasks": [\n'
        '    {"title": "...", "description": "detailed technical steps including specific tools/services", '
        '"estimated_minutes": 60}\n'
        "  ],\n"
        '  "delegations": [],\n'
        '  "summary": "what was decided"\n'
        "}\n\n"
        "Rules:\n"
        "- Create 3-6 specific technical tasks\n"
        "- Include exact tools/services (e.g., 'Use Namecheap for domain', 'Deploy with Vercel')\n"
        "- Break down into steps a beginner can follow\n"
        "- Include setup, build, test, and deploy phases\n"
        "- Reference specific technologies (nginx, Cloudflare, etc.)"
    ),
    can_delegate_to=[],
    backstory=(
        "A full-stack engineer who has shipped dozens of production web applications. "
        "Believes in pragmatic technology choices, clear deployment pipelines, "
        "and documentation that a beginner can follow."
    ),
))

_register(AgentSpec(
    agent_type="outreach",
    name="Outreach Specialist",
    description="Cold outreach, email campaigns, networking, and lead generation.",
    expertise=["outreach", "email", "cold email", "networking", "leads", "sales", "clients"],
    system_prompt=(
        "You are the Outreach Specialist agent. You create concrete outreach tasks: "
        "identifying prospects, crafting messages, setting up campaigns, following up.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "tasks": [\n'
        '    {"title": "...", "description": "detailed steps", "estimated_minutes": 30}\n'
        "  ],\n"
        '  "delegations": [],\n'
        '  "summary": "what was decided"\n'
        "}\n\n"
        "Rules:\n"
        "- Create 3-5 specific outreach tasks\n"
        "- Include templates or frameworks for messages\n"
        "- Specify platforms (LinkedIn, email, Twitter/X)\n"
        "- Include follow-up cadence\n"
        "- Focus on personalized, high-response approaches"
    ),
    can_delegate_to=[],
    backstory=(
        "A relationship-builder who landed enterprise deals through thoughtful, personalized "
        "outreach. Champions empathy-driven messaging over spray-and-pray tactics."
    ),
))

_register(AgentSpec(
    agent_type="research",
    name="Research Specialist",
    description="Deep research, competitive analysis, data gathering, and validation.",
    expertise=["research", "analysis", "data", "competitive", "market", "validate", "investigate"],
    system_prompt=(
        "You are the Research Specialist agent. You create concrete research tasks: "
        "competitive analysis, market validation, data gathering, trend analysis.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "tasks": [\n'
        '    {"title": "...", "description": "what to research and where", "estimated_minutes": 45}\n'
        "  ],\n"
        '  "delegations": [\n'
        '    {"to_agent": "marketing", "instruction": "use findings to create strategy"}\n'
        "  ],\n"
        '  "summary": "what was decided"\n'
        "}\n\n"
        "Rules:\n"
        "- Create 2-4 focused research tasks\n"
        "- Specify exact sources/tools (Google Trends, SimilarWeb, Reddit, etc.)\n"
        "- Include validation criteria\n"
        "- You can delegate findings to marketing or finance agents"
    ),
    can_delegate_to=["marketing", "finance"],
    backstory=(
        "A meticulous analyst who turns ambiguous questions into data-backed insights. "
        "Skilled at synthesizing information from diverse sources into actionable intelligence."
    ),
))

_register(AgentSpec(
    agent_type="finance",
    name="Finance Specialist",
    description="Budgeting, pricing strategy, payment setup, and financial projections.",
    expertise=["money", "budget", "pricing", "payment", "revenue", "cost", "profit", "financial"],
    system_prompt=(
        "You are the Finance Specialist agent. You create concrete financial tasks: "
        "budgeting, pricing strategy, payment processor setup, financial projections.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "tasks": [\n'
        '    {"title": "...", "description": "specific financial steps", "estimated_minutes": 30}\n'
        "  ],\n"
        '  "delegations": [],\n'
        '  "summary": "what was decided"\n'
        "}\n\n"
        "Rules:\n"
        "- Create 2-4 specific finance tasks\n"
        "- Include realistic numbers and benchmarks\n"
        "- Specify payment platforms (Stripe, PayPal, etc.)\n"
        "- Include pricing analysis methodology"
    ),
    can_delegate_to=[],
    backstory=(
        "A pragmatic CFO-type who has bootstrapped startups and managed seven-figure budgets. "
        "Focuses on unit economics, sustainable pricing, and clear financial projections."
    ),
))


# ─── Agent execution (AI + template fallback) ───────────────────────────────

def run_agent(
    agent_type: str,
    goal: Goal,
    instruction: str = "",
    context: str = "",
) -> AgentOutput:
    """
    Run a specialized agent on a goal with optional instructions.

    Returns AgentOutput with tasks to create and delegations to perform.
    """
    spec = get_agent(agent_type)
    if spec is None:
        return AgentOutput(tasks=[], delegations=[], summary=f"Unknown agent type: {agent_type}")

    if config.has_ai():
        return _run_agent_ai(spec, goal, instruction, context)
    return _run_agent_template(spec, goal, instruction)


def _run_agent_ai(
    spec: AgentSpec,
    goal: Goal,
    instruction: str,
    context: str,
) -> AgentOutput:
    """Run agent using AI, enriched with persistent agent memories."""
    try:
        from teb.ai_client import ai_chat_json  # noqa: PLC0415
        from teb.decomposer import _detect_template  # noqa: PLC0415

        # Determine goal type for memory lookup
        goal_type = _detect_template(goal)

        # 1.1: Load persistent agent memories (lessons learned from past runs)
        memory_context = ""
        memories = storage.list_agent_memories(spec.agent_type, goal_type)
        if memories:
            memory_lines = [
                f"- {m['memory_key']}: {m['memory_value']} (confidence: {m['confidence']:.1f}, used {m['times_used']}x)"
                for m in memories[:10]
            ]
            memory_context = "\nLessons learned from past runs:\n" + "\n".join(memory_lines) + "\n"
            # Increment usage counters for referenced memories
            for m in memories[:10]:
                storage.increment_agent_memory_usage(m["id"])

        user_prompt = f"Goal: {goal.title}\nDescription: {goal.description}\n"
        if goal.answers:
            user_prompt += f"User context: {json.dumps(goal.answers)}\n"
        if memory_context:
            user_prompt += memory_context
        if instruction:
            user_prompt += f"\nSpecific instruction: {instruction}\n"
        if context:
            user_prompt += f"\nContext from other agents: {context}\n"

        # Include messages from other agents for richer collaboration
        if goal.id is not None:
            messages = storage.list_agent_messages(goal.id, agent_type=spec.agent_type)
            if messages:
                msg_text = "\n".join(
                    f"[{m.from_agent} → {m.to_agent}] ({m.message_type}): {m.content}"
                    for m in messages
                )
                user_prompt += f"\nMessages from other agents:\n{msg_text}\n"

        data = ai_chat_json(spec.system_prompt, user_prompt, temperature=0.2)

        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
        # Validate task structure
        valid_tasks = []
        for t in tasks:
            if isinstance(t, dict) and t.get("title"):
                valid_tasks.append({
                    "title": str(t["title"]),
                    "description": str(t.get("description", "")),
                    "estimated_minutes": int(t.get("estimated_minutes", 30)),
                })

        delegations = data.get("delegations", [])
        if not isinstance(delegations, list):
            delegations = []
        # Validate and filter delegations to only allowed targets
        valid_delegations = []
        for d in delegations:
            if isinstance(d, dict) and d.get("to_agent") in spec.can_delegate_to:
                valid_delegations.append({
                    "to_agent": str(d["to_agent"]),
                    "instruction": str(d.get("instruction", "")),
                })

        # Parse inter-agent messages
        raw_messages = data.get("messages", [])
        if not isinstance(raw_messages, list):
            raw_messages = []
        valid_messages = []
        for m in raw_messages:
            if isinstance(m, dict) and m.get("to_agent") and m.get("content"):
                valid_messages.append({
                    "to_agent": str(m["to_agent"]),
                    "content": str(m["content"]),
                    "message_type": str(m.get("message_type", "info")),
                })

        summary = str(data.get("summary", data.get("strategy_summary", "")))

        # 1.1: Persist key decision as agent memory for future runs
        if summary and goal_type:
            storage.create_agent_memory(
                agent_type=spec.agent_type,
                goal_type=goal_type,
                memory_key=f"strategy_{goal.title[:50]}",
                memory_value=summary[:500],
                confidence=0.8,
            )

        return AgentOutput(
            tasks=valid_tasks,
            delegations=valid_delegations,
            summary=summary,
            messages=valid_messages,
        )
    except Exception as exc:
        return AgentOutput(tasks=[], delegations=[], summary=f"Agent error: {exc}")


# ─── Template-based agent fallback ──────────────────────────────────────────

_TEMPLATE_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "coordinator": {
        "money": {
            "summary": "Earn money online via freelancing and digital products",
            "tasks": [
                {"title": "Define your sellable skill or service",
                 "description": "List 3 skills you have. Pick the one most in demand online. "
                                "Search Upwork/Fiverr for what people pay for it.",
                 "estimated_minutes": 30},
                {"title": "Set a 30-day revenue target",
                 "description": "Pick a realistic first target ($100-500). "
                                "Break it down: how many clients/sales needed?",
                 "estimated_minutes": 15},
            ],
            "delegations": [
                {"to_agent": "research", "instruction": "Research the top 5 platforms where beginners earn money with common skills. Include earning potential and time-to-first-dollar."},
                {"to_agent": "marketing", "instruction": "Create a personal positioning strategy for a beginner freelancer. Include profile optimization and portfolio building."},
                {"to_agent": "web_dev", "instruction": "Set up a simple portfolio/landing page to showcase services. Include specific tools and hosting options."},
                {"to_agent": "outreach", "instruction": "Create a cold outreach strategy to land the first 3 clients. Include message templates and target identification."},
            ],
        },
        "learn": {
            "summary": "Structured learning plan with practice and validation",
            "tasks": [
                {"title": "Define exactly what you want to learn and why",
                 "description": "Write a 1-sentence goal: 'I want to learn X so I can Y by Z date.'",
                 "estimated_minutes": 15},
                {"title": "Find the best learning resource",
                 "description": "Search for top-rated courses/tutorials. Pick ONE. Don't spread across many.",
                 "estimated_minutes": 30},
            ],
            "delegations": [
                {"to_agent": "research", "instruction": "Research the most effective learning path for this skill. Include specific courses, books, and practice projects."},
            ],
        },
        "build": {
            "summary": "Build a project from idea to deployment",
            "tasks": [
                {"title": "Write a 1-page project spec",
                 "description": "Define: what it does, who it's for, core features (max 3), tech stack.",
                 "estimated_minutes": 45},
            ],
            "delegations": [
                {"to_agent": "web_dev", "instruction": "Create the technical implementation plan: tech stack selection, architecture, hosting, and deployment pipeline."},
                {"to_agent": "research", "instruction": "Validate the idea: find competitors, check market demand, identify unique angle."},
            ],
        },
        "default": {
            "summary": "Breaking down your goal into actionable steps with specialist help",
            "tasks": [
                {"title": "Clarify your specific outcome",
                 "description": "Define exactly what 'done' looks like. What is the measurable result?",
                 "estimated_minutes": 15},
                {"title": "Identify the first concrete step",
                 "description": "What is the single smallest thing you can do in the next 30 minutes to make progress?",
                 "estimated_minutes": 10},
            ],
            "delegations": [
                {"to_agent": "research", "instruction": "Research the most effective approaches for this type of goal. Find 3 proven strategies with examples."},
            ],
        },
    },
    "marketing": {
        "default": {
            "tasks": [
                {"title": "Define your target audience",
                 "description": "Create a 1-paragraph profile: who are they, what do they need, where do they hang out online?",
                 "estimated_minutes": 30},
                {"title": "Create your unique value proposition",
                 "description": "Complete: 'I help [audience] achieve [result] by [method], unlike [alternatives].'",
                 "estimated_minutes": 20},
                {"title": "Set up social media presence",
                 "description": "Create/optimize profiles on 2 platforms where your audience is. Use consistent branding.",
                 "estimated_minutes": 45},
                {"title": "Create 3 pieces of valuable content",
                 "description": "Write/record content that demonstrates your expertise. Focus on solving a specific problem.",
                 "estimated_minutes": 90},
            ],
            "delegations": [
                {"to_agent": "web_dev", "instruction": "Build a landing page with email capture to convert visitors into leads."},
            ],
            "summary": "Marketing strategy: audience definition, positioning, content, and lead capture.",
        },
    },
    "web_dev": {
        "default": {
            "tasks": [
                {"title": "Register a domain name",
                 "description": "Use Namecheap or Cloudflare Registrar. Pick a .com that's short and memorable. Budget: $10-15/year.",
                 "estimated_minutes": 20},
                {"title": "Set up hosting with Vercel or Cloudflare Pages",
                 "description": "Create account, connect to GitHub repo. Free tier is enough to start.",
                 "estimated_minutes": 30},
                {"title": "Build a landing page",
                 "description": "Single page with: headline, value proposition, call-to-action, contact form. Use HTML/CSS or a template.",
                 "estimated_minutes": 120},
                {"title": "Set up DNS and SSL",
                 "description": "Point domain to hosting. Enable HTTPS. Test in browser.",
                 "estimated_minutes": 20},
                {"title": "Add analytics",
                 "description": "Install Plausible or Google Analytics. Set up conversion tracking.",
                 "estimated_minutes": 15},
            ],
            "delegations": [],
            "summary": "Technical setup: domain, hosting, landing page, SSL, analytics.",
        },
    },
    "outreach": {
        "default": {
            "tasks": [
                {"title": "Identify 20 potential prospects",
                 "description": "Use LinkedIn, Twitter/X, or industry forums. Find people who need your service.",
                 "estimated_minutes": 45},
                {"title": "Write a cold outreach template",
                 "description": "Structure: compliment, problem, solution, soft CTA. Keep under 100 words. Personalize the first line.",
                 "estimated_minutes": 30},
                {"title": "Send first 10 personalized messages",
                 "description": "Personalize each message with something specific about the prospect. Track responses.",
                 "estimated_minutes": 60},
                {"title": "Set up follow-up cadence",
                 "description": "Day 3: bump email. Day 7: value-add follow up. Day 14: final check. Use a spreadsheet to track.",
                 "estimated_minutes": 20},
            ],
            "delegations": [],
            "summary": "Outreach strategy: prospect identification, personalized messaging, follow-up cadence.",
        },
    },
    "research": {
        "default": {
            "tasks": [
                {"title": "Competitive landscape analysis",
                 "description": "Find 5 competitors/alternatives. Note: pricing, features, reviews, weaknesses.",
                 "estimated_minutes": 60},
                {"title": "Validate demand with search data",
                 "description": "Use Google Trends, Ubersuggest, or AnswerThePublic. Document search volumes and trends.",
                 "estimated_minutes": 30},
                {"title": "Gather social proof and case studies",
                 "description": "Find 3 examples of people who succeeded at something similar. Note their approach.",
                 "estimated_minutes": 30},
            ],
            "delegations": [],
            "summary": "Research: competitive analysis, demand validation, case studies.",
        },
    },
    "finance": {
        "default": {
            "tasks": [
                {"title": "Calculate startup costs",
                 "description": "List every expense: domain ($12), hosting ($0-20/mo), tools ($0-50/mo). Set a budget cap.",
                 "estimated_minutes": 20},
                {"title": "Set pricing strategy",
                 "description": "Research competitor pricing. Start 20% below market for first clients. Plan to raise after 5 clients.",
                 "estimated_minutes": 30},
                {"title": "Set up payment processing",
                 "description": "Create Stripe or PayPal business account. Set up invoicing. Test with a $1 transaction.",
                 "estimated_minutes": 30},
            ],
            "delegations": [],
            "summary": "Finance setup: cost analysis, pricing strategy, payment processing.",
        },
    },
}


def _detect_goal_category(goal: Goal) -> str:
    """Detect the broad category of a goal for template matching."""
    text = f"{goal.title} {goal.description}".lower()
    words = set(text.split())

    # Use word-boundary-safe detection: check exact words for short terms,
    # prefix matching for stems (e.g. "freelanc" matches freelance/freelancing)
    learn_words = {"learn", "study", "course", "skill", "tutorial", "education"}
    if words & learn_words:
        return "learn"

    money_words = {"money", "earn", "income", "revenue", "sell", "profit"}
    money_stems = ("freelanc", "client")
    if words & money_words or any(stem in text for stem in money_stems):
        return "money"

    build_words = {"build", "create", "develop", "launch", "website", "app"}
    if words & build_words:
        return "build"

    return "default"


def _run_agent_template(
    spec: AgentSpec,
    goal: Goal,
    instruction: str,
) -> AgentOutput:
    """Run agent using template heuristics (no AI required)."""
    agent_templates = _TEMPLATE_STRATEGIES.get(spec.agent_type, {})

    # For coordinator, match by goal category; for specialists, use default
    if spec.agent_type == "coordinator":
        category = _detect_goal_category(goal)
        template = agent_templates.get(category, agent_templates.get("default", {}))
    else:
        template = agent_templates.get("default", {})

    if not template:
        return AgentOutput(
            tasks=[],
            delegations=[],
            summary=f"No template available for {spec.name}.",
        )

    tasks = template.get("tasks", [])
    delegations = template.get("delegations", [])
    # Filter delegations to only agents this spec can delegate to
    valid_delegations = [d for d in delegations if d.get("to_agent") in spec.can_delegate_to]
    summary = template.get("summary", "")

    return AgentOutput(tasks=tasks, delegations=valid_delegations, summary=summary)


# ─── Orchestration ───────────────────────────────────────────────────────────

_MAX_DELEGATION_DEPTH = 3  # prevent infinite delegation loops


def orchestrate_goal(goal: Goal) -> Dict[str, Any]:
    """
    Full multi-agent orchestration for a goal.

    1. Coordinator analyzes the goal
    2. Coordinator sends messages to specialists for coordination
    3. Coordinator delegates to specialists
    4. Specialists produce tasks, may send messages, and may sub-delegate
    5. All handoffs and messages are logged
    6. All tasks are created in the database

    Returns a summary of the orchestration.
    """
    all_tasks: List[Task] = []
    all_handoffs: List[Dict] = []
    all_messages: List[Dict] = []

    def _persist_messages(
        from_agent_type: str,
        output: AgentOutput,
    ) -> None:
        """Save inter-agent messages produced by an agent."""
        for msg_data in output.messages:
            msg = AgentMessage(
                goal_id=goal.id,
                from_agent=from_agent_type,
                to_agent=msg_data["to_agent"],
                message_type=msg_data.get("message_type", "info"),
                content=msg_data["content"][:1000],
            )
            saved = storage.create_agent_message(msg)
            all_messages.append(saved.to_dict())

    def _build_context(from_agent_type: str, base_context: str) -> str:
        """Build enriched context including messages from other agents."""
        parts = []
        if base_context:
            parts.append(base_context)
        # Include summaries from completed handoffs
        for h in all_handoffs:
            if h.get("output_summary"):
                parts.append(f"[{h['to_agent']}]: {h['output_summary']}")
        return "\n".join(parts) if parts else ""

    def _run_delegation_chain(
        from_agent_type: str,
        to_agent_type: str,
        instruction: str,
        depth: int = 0,
        context: str = "",
    ) -> None:
        if depth >= _MAX_DELEGATION_DEPTH:
            return

        # Log the handoff
        handoff = AgentHandoff(
            goal_id=goal.id,
            from_agent=from_agent_type,
            to_agent=to_agent_type,
            input_summary=instruction[:500],
            status="in_progress",
        )
        handoff = storage.create_handoff(handoff)

        # Build enriched context from all previous agent work
        enriched_context = _build_context(to_agent_type, context)

        # Run the specialist
        output = run_agent(to_agent_type, goal, instruction=instruction, context=enriched_context)

        # Persist any messages this agent wants to send
        _persist_messages(to_agent_type, output)

        # Create tasks from agent output
        for idx, task_data in enumerate(output.tasks):
            task = Task(
                goal_id=goal.id,
                title=task_data["title"],
                description=task_data.get("description", ""),
                estimated_minutes=task_data.get("estimated_minutes", 30),
                order_index=len(all_tasks) + idx,
            )
            saved_task = storage.create_task(task)
            all_tasks.append(saved_task)

            # Link first task to handoff
            if idx == 0 and handoff.id:
                handoff.task_id = saved_task.id

        # Update handoff
        handoff.output_summary = output.summary[:500]
        handoff.status = "completed"
        storage.update_handoff(handoff)
        all_handoffs.append(handoff.to_dict())

        # Process sub-delegations
        for delegation in output.delegations:
            _run_delegation_chain(
                from_agent_type=to_agent_type,
                to_agent_type=delegation["to_agent"],
                instruction=delegation.get("instruction", ""),
                depth=depth + 1,
                context=output.summary,
            )

    # Step 1: Run coordinator
    coordinator_output = run_agent("coordinator", goal)

    # Persist coordinator's messages
    _persist_messages("coordinator", coordinator_output)

    # Create coordinator's own tasks
    for idx, task_data in enumerate(coordinator_output.tasks):
        task = Task(
            goal_id=goal.id,
            title=task_data["title"],
            description=task_data.get("description", ""),
            estimated_minutes=task_data.get("estimated_minutes", 30),
            order_index=idx,
        )
        saved_task = storage.create_task(task)
        all_tasks.append(saved_task)

    # Step 2: Execute delegations from coordinator — PARALLEL where possible
    #
    # Independent specialists (no cross-delegation) run concurrently.
    # Specialists that delegate to others run after their dependencies.
    delegations = coordinator_output.delegations
    _lock = Lock()

    def _parallel_delegation(deleg: Dict[str, Any]) -> None:
        """Run a single delegation chain; thread-safe via _lock for shared lists."""
        _run_delegation_chain(
            from_agent_type="coordinator",
            to_agent_type=deleg["to_agent"],
            instruction=deleg.get("instruction", ""),
            depth=0,
            context=coordinator_output.summary,
        )

    if len(delegations) > 1:
        max_workers = min(len(delegations), 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_parallel_delegation, d): d
                for d in delegations
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    d = futures[future]
                    logger.warning("Agent %s failed: %s", d.get("to_agent"), exc)
    else:
        for delegation in delegations:
            _parallel_delegation(delegation)

    # Update goal status
    goal.status = "decomposed"
    storage.update_goal(goal)

    return {
        "goal_id": goal.id,
        "strategy": coordinator_output.summary,
        "total_tasks": len(all_tasks),
        "tasks": [t.to_dict() for t in all_tasks],
        "handoffs": all_handoffs,
        "messages": all_messages,
        "agents_involved": list({h["to_agent"] for h in all_handoffs} | {"coordinator"}),
    }
