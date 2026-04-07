"""
Task decomposition engine.

Two modes:
  - Template mode  : offline, pattern-based, always available.
  - AI mode        : requires OPENAI_API_KEY; sends goal + answers to an
                     OpenAI-compatible API and parses the JSON response.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from teb import config
from teb.models import Goal, Task


# ─── Clarifying Questions ─────────────────────────────────────────────────────

@dataclass
class ClarifyingQuestion:
    key: str          # unique key stored in goal.answers
    text: str
    hint: str = ""    # placeholder / example answer


# Generic questions asked for every goal type (by key so we can deduplicate)
_GENERIC_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="skill_level",
        text="What's your current skill/experience level in this area?",
        hint="e.g. complete beginner, some experience, intermediate…",
    ),
    ClarifyingQuestion(
        key="time_per_day",
        text="How much time per day can you realistically dedicate to this?",
        hint="e.g. 30 minutes, 1 hour, 2 hours…",
    ),
    ClarifyingQuestion(
        key="timeline",
        text="What's your target timeline — do you need results quickly or is a longer horizon OK?",
        hint="e.g. within 2 weeks, 1-3 months, no rush…",
    ),
]

_MONEY_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="technical_skills",
        text="Do you have any technical skills (coding, design, writing, marketing)?",
        hint="e.g. I can code Python, I'm good at writing, none yet…",
    ),
    ClarifyingQuestion(
        key="income_urgency",
        text="Do you need income within 30 days, or is a 3-6 month runway OK?",
        hint="e.g. I need money this month, I have savings for 3 months…",
    ),
]

_LEARN_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="learning_style",
        text="Do you prefer video courses, books, hands-on projects, or a mix?",
        hint="e.g. video + practice, reading, project-based…",
    ),
]

_FITNESS_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="gym_access",
        text="Do you have access to a gym, or are you working out at home?",
        hint="e.g. full gym, home with equipment, bodyweight only…",
    ),
    ClarifyingQuestion(
        key="fitness_goal",
        text="What's your specific fitness goal — lose weight, build muscle, run farther, or general health?",
        hint="e.g. lose 10 kg, run a 5k, build upper-body strength…",
    ),
]

_BUILD_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="tech_stack",
        text="What technology/language do you plan to use, or do you need help choosing?",
        hint="e.g. Python + FastAPI, React, no idea yet…",
    ),
    ClarifyingQuestion(
        key="target_users",
        text="Who is the primary user of this project — you, a specific audience, or the general public?",
        hint="e.g. just me, small team, general public…",
    ),
]


# ─── Template definitions ─────────────────────────────────────────────────────

@dataclass
class _TemplateTask:
    title: str
    description: str
    estimated_minutes: int
    subtasks: List["_TemplateTask"] = field(default_factory=list)


@dataclass
class _Template:
    name: str
    questions: List[ClarifyingQuestion]
    tasks: List[_TemplateTask]


def _t(title: str, desc: str, mins: int, subs: Optional[List[_TemplateTask]] = None) -> _TemplateTask:
    return _TemplateTask(title=title, description=desc, estimated_minutes=mins, subtasks=subs or [])


_TEMPLATES: Dict[str, _Template] = {
    "make_money_online": _Template(
        name="make_money_online",
        questions=_MONEY_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Research online income options",
               "Spend focused time researching 3-5 realistic income paths that match your existing skills "
               "(freelancing, content creation, digital products, consulting). Write down pros/cons for each.",
               60,
               [
                   _t("List your current skills and assets",
                      "Write down everything you know how to do, software you own, and audiences you have access to.",
                      20),
                   _t("Research 3 income models that fit your skills",
                      "Look up how people monetise those skills online. Find one concrete example per model.",
                      40),
               ]),
            _t("Pick one niche/approach and commit to it",
               "Choose the single best-fit income path based on your skills, timeline, and risk tolerance. "
               "Write a one-sentence positioning statement: 'I help [who] achieve [outcome] via [method]'.",
               30),
            _t("Set up your platform or storefront",
               "Create the minimum viable presence: a profile on Upwork/Fiverr, a Gumroad store, a simple "
               "landing page, or a GitHub portfolio — whichever matches your chosen path.",
               90,
               [
                   _t("Register on the platform or buy a domain",
                      "Create an account or register a domain (Namecheap/Cloudflare, ~$10/yr).", 15),
                   _t("Write your bio and service/product description",
                      "Craft a clear, benefit-driven description. Focus on the outcome for the buyer.", 45),
                   _t("Add one sample or portfolio item",
                      "Upload a work sample, write a demo article, or add a case study.", 30),
               ]),
            _t("Create your first offering",
               "Build or define the first thing you'll sell or offer. Keep scope minimal — "
               "an MVP service package, a short e-book, one freelance gig, or one product listing.",
               120),
            _t("Reach out and get your first customer/client",
               "Send 5-10 personalised outreach messages or publish your offer. "
               "Track responses in a simple spreadsheet.",
               60),
            _t("Deliver, collect feedback, and iterate",
               "Complete the first job or sale, ask for a review/testimonial, and identify one "
               "improvement for the next iteration.",
               60),
        ],
    ),
    "learn_skill": _Template(
        name="learn_skill",
        questions=_LEARN_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Assess your current level",
               "Take a free online assessment or spend 30 min trying a beginner exercise to calibrate "
               "your starting point honestly.",
               30),
            _t("Find and evaluate learning resources",
               "Identify 2-3 high-quality resources (course, book, YouTube series). Check reviews, "
               "check the curriculum, and pick one primary resource.",
               45,
               [
                   _t("Search for top-rated courses/books",
                      "Use Reddit, HN, or Coursera/Udemy reviews to shortlist options.", 20),
                   _t("Preview the first module of your top pick",
                      "Spend 15 min with the resource to confirm it matches your style.", 15),
               ]),
            _t("Schedule dedicated learning sessions",
               "Block recurring time in your calendar — consistency beats intensity. "
               "Even 25-minute Pomodoro sessions 5 days/week compound fast.",
               20),
            _t("Complete the first learning module",
               "Work through section/chapter 1 of your chosen resource without skipping. "
               "Take brief notes or build a tiny example as you go.",
               60),
            _t("Do a hands-on practice exercise",
               "Apply what you learned in a small project or exercise. Struggle is part of learning — "
               "resist the urge to look at the answer immediately.",
               60),
            _t("Assess progress and adjust plan",
               "After one week, review what clicked and what didn't. Adjust your resource or approach if needed.",
               30),
        ],
    ),
    "get_fit": _Template(
        name="get_fit",
        questions=_FITNESS_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Assess your current fitness baseline",
               "Measure starting metrics: weight, resting heart rate, how many push-ups/squats you can do, "
               "how far you can run without stopping. Write them down.",
               30),
            _t("Set a specific, measurable fitness goal",
               "Turn a vague goal into a SMART goal. "
               "Example: 'Run 5 km non-stop within 8 weeks' or 'Lose 4 kg in 10 weeks'.",
               20),
            _t("Choose a training program",
               "Pick a structured program suited to your goal and available equipment. "
               "Don't invent a plan — use a proven one (Couch to 5K, StrongLifts 5x5, etc.).",
               30,
               [
                   _t("Research 2 programs that fit your equipment and goal",
                      "Read the overview of each. Check Reddit communities for feedback.", 20),
                   _t("Pick one and read the full first week plan",
                      "Understand exactly what you need to do on Day 1.", 10),
               ]),
            _t("Schedule workouts for the first two weeks",
               "Block workout times in your calendar right now. Treat them like meetings. "
               "Prepare your gear the night before.",
               15),
            _t("Complete your first workout",
               "Do exactly what the program prescribes — no more, no less. Log reps/time/distance.",
               60),
            _t("Track your first week and reflect",
               "After 7 days, review adherence (did you do all sessions?) and how your body feels. "
               "Note one thing to improve next week.",
               20),
        ],
    ),
    "build_project": _Template(
        name="build_project",
        questions=_BUILD_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Define project requirements",
               "Write a one-page spec: the problem it solves, who uses it, the 3-5 must-have features, "
               "and what you explicitly will NOT build in v1.",
               45,
               [
                   _t("Write the problem statement",
                      "One paragraph: what pain/need does this solve and for whom?", 15),
                   _t("List must-have vs nice-to-have features",
                      "Use MoSCoW: Must, Should, Could, Won't. Keep Must-haves to ≤5.", 20),
               ]),
            _t("Set up the development environment",
               "Initialize the repository, install dependencies, configure linting/formatting, "
               "and get a 'Hello World' running end-to-end.",
               60),
            _t("Build the MVP core feature",
               "Implement the single most important feature — the one without which the project doesn't exist. "
               "Skip polish entirely for now.",
               180),
            _t("Write basic tests",
               "Add tests for the core happy path and one error case. "
               "Tests double as documentation of intent.",
               60),
            _t("Deploy or share for feedback",
               "Get the project in front of at least one real user (even just a friend). "
               "Use a free tier: Render, Railway, Vercel, GitHub Pages.",
               60),
            _t("Collect feedback and plan v2",
               "Note the top 3 pieces of feedback. Decide which to address next. "
               "Update your requirements doc.",
               30),
        ],
    ),
    "generic": _Template(
        name="generic",
        questions=_GENERIC_QUESTIONS,
        tasks=[
            _t("Research the topic thoroughly",
               "Spend focused time understanding the landscape: what others have done, what the common "
               "pitfalls are, and what success looks like. Use credible sources and take brief notes.",
               60),
            _t("Identify your key obstacles",
               "List the 3 biggest things that could stop you from reaching this goal. "
               "For each obstacle, write one potential mitigation.",
               30),
            _t("Break down into 3 concrete next actions",
               "From your research, list the 3 most important next steps — specific, verb-led, completable "
               "in a single sitting (e.g. 'Email X', 'Read chapter 3 of Y', 'Sign up for Z').",
               30),
            _t("Complete the smallest possible first step",
               "Pick the easiest item from your action list and do it right now. "
               "Momentum matters more than perfection at this stage.",
               30),
            _t("Review progress and set next milestone",
               "After completing the first step, assess how it went. "
               "Set a clear milestone for the next 7 days and write it down.",
               20),
        ],
    ),
}


# ─── Keyword detection ────────────────────────────────────────────────────────

_MONEY_KEYWORDS = re.compile(
    r"\b(money|income|earn|profit|revenue|cash|rich|wealth|side.?hustle|freelanc|passive)\b",
    re.I,
)
_MONEY_ONLINE_QUALIFIERS = re.compile(
    r"\b(online|internet|web|digital|remote|e-?commerce|blog|youtube|stream)\b",
    re.I,
)
_LEARN_KEYWORDS = re.compile(
    r"\b(learn|study|understand|master|practice|train|course|skill|read|teach.?myself)\b",
    re.I,
)
_FIT_KEYWORDS = re.compile(
    r"\b(fit|work(?:ing)?\s*out|exercise|health|weight|gym|run|jog|muscle|physique|diet|cardio|strength)\b",
    re.I,
)
_BUILD_KEYWORDS = re.compile(r"\b(build|create|develop|make|code|program|launch|ship)\b", re.I)
_BUILD_QUALIFIERS = re.compile(
    r"\b(app|application|website|web.?site|tool|project|product|saas|api|bot|script|software)\b",
    re.I,
)


def _detect_template(goal: Goal) -> str:
    text = f"{goal.title} {goal.description}"
    if _MONEY_KEYWORDS.search(text) and _MONEY_ONLINE_QUALIFIERS.search(text):
        return "make_money_online"
    if _FIT_KEYWORDS.search(text):
        return "get_fit"
    if _BUILD_KEYWORDS.search(text) and _BUILD_QUALIFIERS.search(text):
        return "build_project"
    if _LEARN_KEYWORDS.search(text):
        return "learn_skill"
    return "generic"


# ─── Public API ───────────────────────────────────────────────────────────────

def get_clarifying_questions(goal: Goal) -> List[ClarifyingQuestion]:
    """Return the ordered list of clarifying questions for this goal."""
    template_name = _detect_template(goal)
    template = _TEMPLATES[template_name]
    # Deduplicate by key while preserving order
    seen: Set[str] = set()
    questions: List[ClarifyingQuestion] = []
    for q in template.questions:
        if q.key not in seen:
            seen.add(q.key)
            questions.append(q)
    return questions


def get_next_question(goal: Goal) -> Optional[ClarifyingQuestion]:
    """Return the first unanswered clarifying question, or None if all answered."""
    for q in get_clarifying_questions(goal):
        if q.key not in goal.answers:
            return q
    return None


def _template_tasks_to_models(
    tasks: List[_TemplateTask],
    goal_id: int,
    parent_id: Optional[int] = None,
    order_offset: int = 0,
) -> List[Task]:
    """Flatten template tasks into Task model instances (subtasks have parent_id set)."""
    result: List[Task] = []
    for idx, tt in enumerate(tasks):
        t = Task(
            goal_id=goal_id,
            parent_id=parent_id,
            title=tt.title,
            description=tt.description,
            estimated_minutes=tt.estimated_minutes,
            order_index=order_offset + idx,
        )
        result.append(t)
        # Subtasks are added right after their parent; they'll get parent_id set
        # by the caller after the parent is persisted.
        if tt.subtasks:
            # We attach subtasks with a sentinel list so the storage layer can
            # fill in parent.id after the parent row is inserted.
            t._subtask_templates = tt.subtasks  # type: ignore[attr-defined]
    return result


def decompose_template(goal: Goal) -> List[Task]:
    """Return a flat list of Task objects using template-based decomposition."""
    template_name = _detect_template(goal)
    template = _TEMPLATES[template_name]

    tasks: List[Task] = []
    for idx, tt in enumerate(template.tasks):
        parent = Task(
            goal_id=goal.id,  # type: ignore[arg-type]
            title=tt.title,
            description=tt.description,
            estimated_minutes=tt.estimated_minutes,
            order_index=idx,
        )
        parent._subtask_templates = tt.subtasks  # type: ignore[attr-defined]
        tasks.append(parent)
    return tasks


def decompose_ai(goal: Goal) -> List[Task]:
    """
    Call an OpenAI-compatible API to decompose the goal.
    Falls back to template mode on any error.
    """
    try:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
        )

        answers_text = "\n".join(
            f"- {k}: {v}" for k, v in goal.answers.items()
        ) or "No clarifying answers provided."

        system_prompt = (
            "You are a goal-decomposition assistant. "
            "Given a user's goal and their answers to clarifying questions, "
            "produce a JSON array of tasks that will help them achieve the goal. "
            "Each task must have: title (str), description (str), estimated_minutes (int), "
            "and optionally subtasks (array of same shape). "
            "Return ONLY valid JSON with no prose."
        )
        user_prompt = (
            f"Goal: {goal.title}\n"
            f"Details: {goal.description}\n\n"
            f"Clarifying answers:\n{answers_text}\n\n"
            f"Produce up to {config.MAX_TASKS_PER_GOAL} tasks."
        )

        response = client.chat.completions.create(
            model=config.MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        # Accept {"tasks": [...]} or a bare array
        task_list: List[Any] = data if isinstance(data, list) else data.get("tasks", [])
        return _parse_ai_tasks(task_list, goal.id)  # type: ignore[arg-type]
    except Exception:
        # Gracefully fall back to template decomposition
        return decompose_template(goal)


def _parse_ai_tasks(
    task_list: List[Any],
    goal_id: int,
    parent_id: Optional[int] = None,
    order_offset: int = 0,
) -> List[Task]:
    result: List[Task] = []
    for idx, item in enumerate(task_list):
        if not isinstance(item, dict):
            continue
        subtask_data = item.get("subtasks", [])
        t = Task(
            goal_id=goal_id,
            parent_id=parent_id,
            title=str(item.get("title", "Untitled task")),
            description=str(item.get("description", "")),
            estimated_minutes=int(item.get("estimated_minutes", 30)),
            order_index=order_offset + idx,
        )
        if subtask_data:
            t._subtask_templates = subtask_data  # type: ignore[attr-defined]
        result.append(t)
    return result


def decompose(goal: Goal) -> List[Task]:
    """Entry point: choose AI or template mode based on config."""
    if config.OPENAI_API_KEY:
        return decompose_ai(goal)
    return decompose_template(goal)
