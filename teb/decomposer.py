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
    """Return a flat list of Task objects using template-based decomposition.

    When the goal has clarifying answers, task descriptions and time estimates
    are adapted to the user's context (skill level, available time, timeline).
    """
    template_name = _detect_template(goal)
    template = _TEMPLATES[template_name]
    profile = _build_user_profile(goal.answers)

    tasks: List[Task] = []
    for idx, tt in enumerate(template.tasks):
        adapted = _adapt_template_task(tt, profile)
        parent = Task(
            goal_id=goal.id,  # type: ignore[arg-type]
            title=adapted.title,
            description=adapted.description,
            estimated_minutes=adapted.estimated_minutes,
            order_index=idx,
        )
        parent._subtask_templates = [  # type: ignore[attr-defined]
            _adapt_template_task(sub, profile) for sub in tt.subtasks
        ]
        tasks.append(parent)
    return tasks


# ─── Answer-aware adaptation ──────────────────────────────────────────────────

@dataclass
class _UserProfile:
    """Parsed user context extracted from clarifying answers."""
    skill_level: str = "unknown"     # beginner | intermediate | advanced | unknown
    minutes_per_day: int = 60        # parsed from time_per_day answer
    timeline: str = "medium"         # short (< 1 month) | medium | long | unknown
    has_technical_skills: bool = False
    income_urgent: bool = False
    raw_answers: Dict[str, str] = field(default_factory=dict)


def _build_user_profile(answers: Dict[str, str]) -> _UserProfile:
    """Parse clarifying answers into a structured user profile."""
    profile = _UserProfile(raw_answers=dict(answers))

    # ─── Skill level ───────────────────────────────────────────────────
    skill = answers.get("skill_level", "").lower()
    if any(w in skill for w in ("beginner", "none", "zero", "no experience", "never", "starting")):
        profile.skill_level = "beginner"
    elif any(w in skill for w in ("intermediate", "some", "a bit", "familiar", "decent")):
        profile.skill_level = "intermediate"
    elif any(w in skill for w in ("advanced", "expert", "professional", "years", "senior")):
        profile.skill_level = "advanced"

    # ─── Time per day ──────────────────────────────────────────────────
    time_str = answers.get("time_per_day", "").lower()
    mins = _parse_minutes(time_str)
    if mins > 0:
        profile.minutes_per_day = mins

    # ─── Timeline ──────────────────────────────────────────────────────
    tl = answers.get("timeline", "").lower()
    if any(w in tl for w in ("week", "days", "asap", "urgent", "quick", "immediately")):
        profile.timeline = "short"
    elif any(w in tl for w in ("month", "1-3", "a few months", "quarter")):
        profile.timeline = "medium"
    elif any(w in tl for w in ("year", "no rush", "long", "6 month", "no hurry")):
        profile.timeline = "long"

    # ─── Technical skills ──────────────────────────────────────────────
    tech = answers.get("technical_skills", "").lower()
    if any(w in tech for w in ("code", "python", "javascript", "design", "html", "program", "react",
                                "developer", "engineer", "writing", "marketing")):
        profile.has_technical_skills = True
    elif any(w in tech for w in ("none", "no", "zero", "nothing")):
        profile.has_technical_skills = False

    # ─── Income urgency ────────────────────────────────────────────────
    urgency = answers.get("income_urgency", "").lower()
    if any(w in urgency for w in ("this month", "30 day", "asap", "urgent", "need money now", "immediately")):
        profile.income_urgent = True

    return profile


def _parse_minutes(text: str) -> int:
    """Best-effort parse of a time string like '30 min', '2 hours', '1.5h'."""
    import re as _re
    # Try "N hours" / "Nh"
    m = _re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|r)?", text)
    if m:
        return int(float(m.group(1)) * 60)
    # Try "N minutes" / "Nmin" / "Nm"
    m = _re.search(r"(\d+)\s*m(?:in(?:ute)?s?)?", text)
    if m:
        return int(m.group(1))
    # Try bare number — assume minutes if ≤ 300, else hours
    m = _re.search(r"(\d+)", text)
    if m:
        val = int(m.group(1))
        return val if val <= 300 else val * 60
    return 0


def _time_scale(profile: _UserProfile) -> float:
    """Return a multiplier to scale task times based on the user's availability.

    If someone has only 30 min/day, tasks should be compressed to fit into
    short sessions. If they have 4 hours, tasks can be larger.
    """
    if profile.minutes_per_day <= 30:
        return 0.5
    if profile.minutes_per_day <= 60:
        return 0.75
    if profile.minutes_per_day <= 120:
        return 1.0
    return 1.25


def _skill_adjustment(profile: _UserProfile) -> tuple[float, str]:
    """Return (time_multiplier, context_note) based on skill level.

    Beginners need more time and more guidance in descriptions.
    Advanced users can skip basics and move faster.
    """
    if profile.skill_level == "beginner":
        return 1.3, "Since you're starting out, take extra time and don't rush this step."
    if profile.skill_level == "advanced":
        return 0.7, "Given your experience, you can move through this quickly."
    if profile.skill_level == "intermediate":
        return 1.0, "Adapt the depth to areas where you're less confident."
    return 1.0, ""


def _adapt_template_task(tt: _TemplateTask, profile: _UserProfile) -> _TemplateTask:
    """Return a new _TemplateTask with time/description adapted to the user profile."""
    if not profile.raw_answers:
        # No answers provided — return the original unchanged
        return tt

    time_factor = _time_scale(profile)
    skill_mult, skill_note = _skill_adjustment(profile)
    combined_factor = time_factor * skill_mult

    # Scale time, round to nearest 5, clamp 5..180
    scaled_minutes = max(5, min(180, round(tt.estimated_minutes * combined_factor / 5) * 5))

    # Build an adapted description
    desc = tt.description
    additions: List[str] = []
    if skill_note:
        additions.append(skill_note)
    if profile.minutes_per_day <= 30:
        additions.append(
            f"You have ~{profile.minutes_per_day} min/day, so break this into "
            f"multiple short sessions if needed."
        )
    if profile.timeline == "short":
        additions.append("Your timeline is tight — focus on the essentials and skip nice-to-haves.")
    elif profile.timeline == "long":
        additions.append("You have time on your side — aim for depth over speed.")

    if additions:
        desc = desc + " " + " ".join(additions)

    return _TemplateTask(
        title=tt.title,
        description=desc,
        estimated_minutes=scaled_minutes,
        subtasks=tt.subtasks,  # subtasks are adapted separately by the caller
    )


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


# ─── Task-level decomposition ─────────────────────────────────────────────────

def decompose_task(task: Task) -> List[Task]:
    """
    Break a single task into smaller sub-tasks.

    Uses AI when OPENAI_API_KEY is set; otherwise uses a template-based
    approach that splits the task into research → execute → verify steps,
    each ≤ 25 minutes.
    """
    if config.OPENAI_API_KEY:
        return _decompose_task_ai(task)
    return _decompose_task_template(task)


def _decompose_task_template(task: Task) -> List[Task]:
    """
    Template-based single-task decomposition.

    Splits any task into 3 focused micro-steps:
      1. Research/prepare  — understand what's needed
      2. Execute           — do the core work
      3. Verify/review     — confirm the result
    Each step gets roughly 1/3 of the parent's estimated time, capped at 25 min.
    """
    total = task.estimated_minutes
    third = max(5, total // 3)
    capped = min(third, 25)

    subtasks = [
        Task(
            goal_id=task.goal_id,
            parent_id=task.id,
            title=f"Research: {task.title}",
            description=(
                f"Spend {capped} minutes gathering what you need to complete "
                f"'{task.title}'. Look up any unknowns, collect links or tools, "
                f"and write a quick checklist of steps."
            ),
            estimated_minutes=capped,
            order_index=0,
        ),
        Task(
            goal_id=task.goal_id,
            parent_id=task.id,
            title=f"Execute: {task.title}",
            description=(
                f"Do the core work. Follow the checklist from the research step. "
                f"Focus only on finishing — don't polish or optimize yet."
            ),
            estimated_minutes=min(total - 2 * capped, 25) if total > 2 * capped else capped,
            order_index=1,
        ),
        Task(
            goal_id=task.goal_id,
            parent_id=task.id,
            title=f"Verify: {task.title}",
            description=(
                f"Review your work. Does it meet the goal? Fix any obvious gaps "
                f"and note anything to revisit later."
            ),
            estimated_minutes=capped,
            order_index=2,
        ),
    ]
    return subtasks


def _decompose_task_ai(task: Task) -> List[Task]:
    """Call an OpenAI-compatible API to break a single task into sub-tasks."""
    try:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
        )

        system_prompt = (
            "You are a task-decomposition assistant. "
            "Given a task title and description, break it into 2-5 smaller, "
            "concrete sub-tasks that each take ≤25 minutes. "
            "Each sub-task must have: title (str), description (str), "
            "estimated_minutes (int). "
            "Return ONLY valid JSON: {\"subtasks\": [...]}."
        )
        user_prompt = (
            f"Task: {task.title}\n"
            f"Description: {task.description}\n"
            f"Original estimate: {task.estimated_minutes} minutes\n\n"
            f"Break this into 2-5 concrete, actionable sub-tasks."
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
        subtask_list = data if isinstance(data, list) else data.get("subtasks", [])
        return _parse_task_subtasks(subtask_list, task)
    except Exception:
        return _decompose_task_template(task)


def _parse_task_subtasks(subtask_list: List[Any], parent: Task) -> List[Task]:
    result: List[Task] = []
    for idx, item in enumerate(subtask_list):
        if not isinstance(item, dict):
            continue
        result.append(Task(
            goal_id=parent.goal_id,
            parent_id=parent.id,
            title=str(item.get("title", "Subtask")),
            description=str(item.get("description", "")),
            estimated_minutes=min(int(item.get("estimated_minutes", 15)), 25),
            order_index=idx,
        ))
    return result if result else _decompose_task_template(parent)


# ─── Focus mode ───────────────────────────────────────────────────────────────

def get_focus_task(tasks: List[Task]) -> Optional[Task]:
    """
    Return the single next task the user should work on.

    Strategy:
      1. If any task is "in_progress", return the deepest (leaf) one.
      2. Otherwise, return the first "todo" task in order, preferring
         sub-tasks of the earliest incomplete parent.
    """
    if not tasks:
        return None

    # Build lookup structures
    by_id: Dict[int, Task] = {t.id: t for t in tasks if t.id is not None}
    children: Dict[Optional[int], List[Task]] = {}
    for t in tasks:
        children.setdefault(t.parent_id, []).append(t)

    # 1. Find in-progress tasks, pick deepest
    in_progress = [t for t in tasks if t.status == "in_progress"]
    if in_progress:
        # Find the one with no in-progress children (deepest)
        for t in sorted(in_progress, key=lambda x: x.order_index):
            child_ids = children.get(t.id, [])
            has_active_child = any(c.status == "in_progress" for c in child_ids)
            if not has_active_child:
                return t

    # 2. Walk the tree in order; find first actionable "todo" leaf
    def _first_todo(parent_id: Optional[int]) -> Optional[Task]:
        kids = children.get(parent_id, [])
        kids_sorted = sorted(kids, key=lambda x: (x.order_index, x.id or 0))
        for kid in kids_sorted:
            if kid.status in ("done", "skipped"):
                continue
            # If this task has children, recurse into them
            grandkids = children.get(kid.id, [])
            actionable_grandkids = [g for g in grandkids if g.status not in ("done", "skipped")]
            if actionable_grandkids:
                deeper = _first_todo(kid.id)
                if deeper:
                    return deeper
            # Leaf or no actionable children → this is the focus task
            if kid.status == "todo":
                return kid
        return None

    return _first_todo(None)


def get_progress_summary(tasks: List[Task]) -> Dict[str, Any]:
    """
    Return progress statistics for a set of tasks.

    Includes counts by status, completion percentage, and estimated
    remaining time.  Parents that have been decomposed into children are
    excluded from the time estimate to avoid double-counting.
    """
    top_level = [t for t in tasks if t.parent_id is None]

    # Identify tasks that have children (decomposed parents)
    parent_ids: Set[int] = set()
    for t in tasks:
        if t.parent_id is not None:
            parent_ids.add(t.parent_id)

    total = len(top_level)
    done = sum(1 for t in top_level if t.status in ("done", "skipped"))
    in_progress = sum(1 for t in top_level if t.status == "in_progress")
    todo = sum(1 for t in top_level if t.status == "todo")
    pct = round((done / total) * 100) if total else 0

    # Only count leaf tasks (those without children) for time estimation
    remaining_minutes = sum(
        t.estimated_minutes
        for t in tasks
        if t.status not in ("done", "skipped") and (t.id is None or t.id not in parent_ids)
    )

    return {
        "total_tasks": total,
        "done": done,
        "in_progress": in_progress,
        "todo": todo,
        "completion_pct": pct,
        "estimated_remaining_minutes": remaining_minutes,
    }
