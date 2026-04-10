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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from teb import config
from teb.models import Goal, ProactiveSuggestion, SuccessPath, Task


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

_BOOK_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="book_genre",
        text="What kind of book? Fiction, non-fiction, technical, self-help?",
        hint="e.g. sci-fi novel, how-to guide, memoir…",
    ),
    ClarifyingQuestion(
        key="book_length",
        text="Roughly how long do you envision it — short (~20k words), medium (~50k), or long (~80k+)?",
        hint="e.g. short, medium, full novel…",
    ),
]

_STARTUP_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="startup_idea",
        text="What problem does your startup solve, and for whom?",
        hint="e.g. helps freelancers track invoices, simplifies meal planning for parents…",
    ),
    ClarifyingQuestion(
        key="startup_stage",
        text="Where are you now — just an idea, have a prototype, or already have users?",
        hint="e.g. idea stage, MVP built, 50 beta users…",
    ),
]

_JOB_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="job_target",
        text="What kind of role or industry are you targeting?",
        hint="e.g. software engineer at a startup, marketing manager, remote data analyst…",
    ),
    ClarifyingQuestion(
        key="job_timeline",
        text="How urgently do you need a new job?",
        hint="e.g. within a month, 3-6 months, exploring options…",
    ),
]

_HEALTH_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="health_focus",
        text="What aspect of health do you want to improve most?",
        hint="e.g. sleep, nutrition, stress, energy levels, chronic condition…",
    ),
    ClarifyingQuestion(
        key="health_constraints",
        text="Any constraints or conditions to keep in mind?",
        hint="e.g. bad knees, vegetarian, limited time, budget…",
    ),
]

_SIDE_PROJECT_QUESTIONS: List[ClarifyingQuestion] = [
    ClarifyingQuestion(
        key="project_type",
        text="What kind of side project — creative, technical, business, or something else?",
        hint="e.g. YouTube channel, open-source tool, Etsy shop, podcast…",
    ),
    ClarifyingQuestion(
        key="project_hours",
        text="How many hours per week can you dedicate to this alongside other commitments?",
        hint="e.g. 5 hours, 10 hours, weekends only…",
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

    "write_book": _Template(
        name="write_book",
        questions=_BOOK_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Define your book concept and outline",
               "Write a one-paragraph summary of your book: what it's about, who it's for, "
               "and what the reader will take away. Then sketch a chapter-by-chapter outline.",
               90,
               [
                   _t("Write a one-paragraph book summary", "Describe the core idea, audience, and takeaway.", 20),
                   _t("Draft a chapter outline", "List 8-12 chapters with a sentence each on what they cover.", 60),
               ]),
            _t("Set a daily writing target and schedule",
               "Decide how many words you'll write per day (500-1000 is common for beginners). "
               "Block that time in your calendar.",
               20),
            _t("Write the first chapter / first 2000 words",
               "Get the first words on paper. Don't edit, just write. "
               "The goal is momentum, not perfection.",
               120),
            _t("Build a writing habit — complete 5 consecutive writing sessions",
               "Stick to your daily schedule for 5 days in a row. Track your word count.",
               60),
            _t("Get early feedback on your first 5000 words",
               "Share your draft with 1-2 trusted readers. Ask what's working and what's confusing.",
               45),
            _t("Complete first draft",
               "Keep writing until you've finished all chapters. Aim for completion, not perfection.",
               90),
            _t("Revise and edit",
               "Read through the full draft. Fix structure issues, cut filler, strengthen weak sections.",
               120),
            _t("Prepare for publishing",
               "Research publishing options (self-publish, query agents, or online platforms). "
               "Format your manuscript accordingly.",
               60),
        ],
    ),

    "launch_startup": _Template(
        name="launch_startup",
        questions=_STARTUP_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Validate the problem exists",
               "Talk to 5-10 potential customers. Ask about their pain points — don't pitch. "
               "Document what you hear.",
               90,
               [
                   _t("Identify 10 potential customers to interview",
                      "Find people who match your target audience. Reach out via email, social, or in person.", 30),
                   _t("Conduct 5 customer interviews",
                      "Ask open-ended questions about their problems. Take notes. Look for patterns.", 60),
               ]),
            _t("Define your MVP scope",
               "Based on customer feedback, identify the smallest thing you can build that solves "
               "the core problem. Write it down as a feature list (max 3-5 features).",
               45),
            _t("Build a landing page",
               "Create a simple page explaining your solution with a signup form. "
               "Use a no-code tool or a simple template.",
               90),
            _t("Build or prototype the MVP",
               "Build the minimum product. Prioritise speed over perfection. "
               "Ship something real people can use within 2-4 weeks.",
               120),
            _t("Get 5 early users and collect feedback",
               "Launch to a small group. Watch how they use it. Ask what's broken or missing.",
               60),
            _t("Iterate based on feedback",
               "Fix the top 3 issues users reported. Add the one feature most requested.",
               90),
        ],
    ),

    "find_job": _Template(
        name="find_job",
        questions=_JOB_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Update your resume/CV",
               "Tailor your resume to the roles you're targeting. Use action verbs, quantify results, "
               "and keep it to 1-2 pages.",
               60),
            _t("Optimize your LinkedIn/portfolio",
               "Update headline, summary, and experience. Add a profile photo. "
               "Connect with people in your target industry.",
               45),
            _t("Research target companies and roles",
               "Identify 10-20 companies you'd like to work for. "
               "Look at their job boards, culture, and recent news.",
               60),
            _t("Apply to 5 positions with tailored applications",
               "For each application, customize your cover letter and highlight relevant experience.",
               90),
            _t("Prepare for interviews",
               "Practice common interview questions for your role. "
               "Prepare 3-5 stories using the STAR method.",
               60,
               [
                   _t("Research common interview questions", "Find 10 questions typical for your target role.", 20),
                   _t("Practice answers out loud", "Rehearse your responses. Time yourself.", 40),
               ]),
            _t("Network actively",
               "Reach out to 5 people in your target companies or industry. "
               "Ask for informational interviews, not job referrals.",
               45),
            _t("Follow up on applications and track progress",
               "Keep a spreadsheet of applications, dates, and statuses. Follow up after 1 week.",
               30),
        ],
    ),

    "improve_health": _Template(
        name="improve_health",
        questions=_HEALTH_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Assess your current baseline",
               "Record where you are now: sleep quality, energy levels, diet habits, exercise frequency, "
               "stress level. Be honest — this is just a starting point.",
               30),
            _t("Pick one area to focus on first",
               "Don't try to fix everything at once. Choose the area with the highest impact: "
               "sleep, nutrition, movement, or stress management.",
               20),
            _t("Set one measurable goal for the next 2 weeks",
               "e.g. 'Sleep 7+ hours 5 nights/week', 'Walk 30 min daily', 'Cook 4 dinners at home'.",
               15),
            _t("Build the habit — stick to it for 7 days",
               "Focus on consistency, not perfection. Track each day with a simple checkmark.",
               30),
            _t("Check in on progress and adjust",
               "After 7 days, review what worked and what didn't. Adjust the goal if needed.",
               20),
            _t("Add a second healthy habit",
               "Once the first habit is stable, add another. Stack habits for compound effect.",
               30),
        ],
    ),

    "side_project": _Template(
        name="side_project",
        questions=_SIDE_PROJECT_QUESTIONS + _GENERIC_QUESTIONS,
        tasks=[
            _t("Define the project scope and goal",
               "Write down exactly what you want to build/create and what 'done' looks like. "
               "Keep scope small enough to finish in 4-8 weeks.",
               30),
            _t("Research similar projects for inspiration",
               "Find 3-5 existing projects in the same space. Note what they do well and what's missing.",
               45),
            _t("Set up your workspace and tools",
               "Create the repo/workspace/account/channel. Install the tools you need. "
               "Get the boilerplate out of the way.",
               60),
            _t("Complete the first deliverable",
               "Build/create the first tangible piece: first episode, first feature, first product listing.",
               90),
            _t("Share it publicly and get feedback",
               "Post it where your target audience hangs out. Ask for honest feedback.",
               30),
            _t("Iterate and build a routine",
               "Based on feedback, improve and keep shipping regularly. "
               "Set a cadence (weekly, bi-weekly) and stick to it.",
               60),
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
    r"\b(fit|work(?:ing)?\s*out|exercise|weight|gym|run|jog|muscle|physique|diet|cardio|strength)\b",
    re.I,
)
_BUILD_KEYWORDS = re.compile(r"\b(build|create|develop|make|code|program|launch|ship)\b", re.I)
_BUILD_QUALIFIERS = re.compile(
    r"\b(app|application|website|web.?site|tool|project|product|saas|api|bot|script|software)\b",
    re.I,
)
_BOOK_KEYWORDS = re.compile(r"\b(book|write|novel|manuscript|author|publish|memoir|ebook)\b", re.I)
_STARTUP_KEYWORDS = re.compile(r"\b(startup|start.?up|company|found|co-?found|venture|business)\b", re.I)
_JOB_KEYWORDS = re.compile(r"\b(job|career|hire|employ|resume|interview|apply|position|role)\b", re.I)
_HEALTH_KEYWORDS = re.compile(
    r"\b(health|sleep|nutrition|stress|energy|wellness|mental.?health|well.?being|meditat)\b", re.I,
)
_SIDE_PROJECT_KEYWORDS = re.compile(
    r"\b(side.?project|hobby|personal.?project|passion.?project|weekend.?project|tinker|experiment)\b", re.I,
)


def _detect_template(goal: Goal) -> str:
    text = f"{goal.title} {goal.description}"
    if _MONEY_KEYWORDS.search(text) and _MONEY_ONLINE_QUALIFIERS.search(text):
        return "make_money_online"
    if _BOOK_KEYWORDS.search(text):
        return "write_book"
    if _STARTUP_KEYWORDS.search(text):
        return "launch_startup"
    if _JOB_KEYWORDS.search(text):
        return "find_job"
    if _FIT_KEYWORDS.search(text):
        return "get_fit"
    if _HEALTH_KEYWORDS.search(text):
        return "improve_health"
    if _BUILD_KEYWORDS.search(text) and _BUILD_QUALIFIERS.search(text):
        return "build_project"
    if _LEARN_KEYWORDS.search(text):
        return "learn_skill"
    if _SIDE_PROJECT_KEYWORDS.search(text):
        return "side_project"
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
    """Return the first unanswered clarifying question, or None if all answered.

    After the first 2 template questions are answered, generates dynamic
    AI-powered follow-up questions based on the user's answers so far (5.1).
    """
    template_questions = get_clarifying_questions(goal)

    # Count how many template questions have been answered
    answered_count = sum(1 for q in template_questions if q.key in goal.answers)

    # Return next unanswered template question if fewer than 2 answered
    if answered_count < 2:
        for q in template_questions:
            if q.key not in goal.answers:
                return q
        return None

    # Check if there are still unanswered template questions
    for q in template_questions:
        if q.key not in goal.answers:
            return q

    # 5.1: After all template questions answered, try generating a dynamic one
    if config.has_ai() and len(goal.answers) >= 2:
        dynamic = _generate_dynamic_question(goal)
        if dynamic:
            return dynamic

    return None


def _generate_dynamic_question(goal: Goal) -> Optional[ClarifyingQuestion]:
    """Generate an AI-powered follow-up question based on the user's answers so far."""
    try:
        from teb.ai_client import ai_chat_json  # noqa: PLC0415

        # Only generate if we haven't already asked a dynamic question
        dynamic_key = f"dynamic_{len(goal.answers)}"
        if dynamic_key in goal.answers:
            return None

        answers_text = "\n".join(f"- {k}: {v}" for k, v in goal.answers.items())

        result = ai_chat_json(
            system=(
                "You are a goal-clarification assistant. Based on the user's goal and "
                "their answers so far, generate ONE specific follow-up question that would help "
                "create a better, more personalized action plan. The question should dig deeper "
                "into something the user mentioned or clarify a gap in their answers. "
                "Return JSON: {\"key\": \"unique_key\", \"text\": \"question text\", \"hint\": \"example answer\"}"
            ),
            user=(
                f"Goal: {goal.title}\n"
                f"Description: {goal.description}\n\n"
                f"Answers so far:\n{answers_text}\n\n"
                f"Generate one follow-up question."
            ),
            temperature=0.3,
        )

        if result.get("text"):
            return ClarifyingQuestion(
                key=result.get("key", dynamic_key),
                text=str(result["text"]),
                hint=str(result.get("hint", "")),
            )
    except Exception:
        pass
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


def _fuzzy_match(text: str, keywords: List[str], threshold: float = 0.7) -> bool:
    """Check if text contains a fuzzy match to any keyword using difflib."""
    import difflib
    text_lower = text.lower()
    words = text_lower.split()
    for kw in keywords:
        kw_lower = kw.lower()
        # Direct substring match first (faster)
        if kw_lower in text_lower:
            return True
        # Fuzzy match each word against the keyword
        matches = difflib.get_close_matches(kw_lower, words, n=1, cutoff=threshold)
        if matches:
            return True
    return False


def _build_user_profile(answers: Dict[str, str]) -> _UserProfile:
    """Parse clarifying answers into a structured user profile.

    Uses fuzzy matching (5.3) to handle typos and varied phrasing.
    """
    profile = _UserProfile(raw_answers=dict(answers))

    # ─── Skill level ───────────────────────────────────────────────────
    skill = answers.get("skill_level", "").lower()
    if _fuzzy_match(skill, ["beginner", "none", "zero", "no experience", "never", "starting"]):
        profile.skill_level = "beginner"
    elif _fuzzy_match(skill, ["intermediate", "some", "a bit", "familiar", "decent"]):
        profile.skill_level = "intermediate"
    elif _fuzzy_match(skill, ["advanced", "expert", "professional", "years", "senior"]):
        profile.skill_level = "advanced"

    # ─── Time per day ──────────────────────────────────────────────────
    time_str = answers.get("time_per_day", "").lower()
    mins = _parse_minutes(time_str)
    if mins > 0:
        profile.minutes_per_day = mins

    # ─── Timeline ──────────────────────────────────────────────────────
    tl = answers.get("timeline", "").lower()
    if _fuzzy_match(tl, ["week", "days", "asap", "urgent", "quick", "immediately"]):
        profile.timeline = "short"
    elif _fuzzy_match(tl, ["month", "1-3", "a few months", "quarter"]):
        profile.timeline = "medium"
    elif _fuzzy_match(tl, ["year", "no rush", "long", "6 month", "no hurry"]):
        profile.timeline = "long"

    # ─── Technical skills ──────────────────────────────────────────────
    tech = answers.get("technical_skills", "").lower()
    if _fuzzy_match(tech, ["code", "python", "javascript", "design", "html", "program", "react",
                            "developer", "engineer", "writing", "marketing"]):
        profile.has_technical_skills = True
    elif _fuzzy_match(tech, ["none", "no", "zero", "nothing"]):
        profile.has_technical_skills = False

    # ─── Income urgency ────────────────────────────────────────────────
    urgency = answers.get("income_urgency", "").lower()
    if _fuzzy_match(urgency, ["this month", "30 day", "asap", "urgent", "need money now", "immediately"]):
        profile.income_urgent = True

    return profile


def _parse_minutes(text: str) -> int:
    """Best-effort parse of a time string like '30 min', '2 hours', '1.5h'."""
    # Try "N hours" / "Nh"
    m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|r)?", text)
    if m:
        return int(float(m.group(1)) * 60)
    # Try "N minutes" / "Nmin" / "Nm"
    m = re.search(r"(\d+)\s*m(?:in(?:ute)?s?)?", text)
    if m:
        return int(m.group(1))
    # Try bare number — assume minutes if ≤ 300, else hours
    m = re.search(r"(\d+)", text)
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


def _build_context_for_ai(goal: Goal) -> str:
    """Build rich context from user profile, platform patterns, and success paths
    to inject into the AI decomposition prompt for deeper, non-generic results."""
    from teb import storage as _storage  # noqa: PLC0415

    sections: List[str] = []

    # 1. User profile context
    if goal.user_id:
        try:
            profile = _storage.get_or_create_profile(goal.user_id)
            if profile:
                profile_lines = []
                if profile.skills:
                    profile_lines.append(f"Skills: {profile.skills}")
                if profile.experience_level and profile.experience_level != "unknown":
                    profile_lines.append(f"Experience level: {profile.experience_level}")
                if profile.available_hours_per_day:
                    profile_lines.append(f"Available hours/day: {profile.available_hours_per_day}")
                if profile.interests:
                    profile_lines.append(f"Interests: {profile.interests}")
                if profile.goals_completed:
                    profile_lines.append(f"Goals previously completed: {profile.goals_completed}")
                if profile.total_tasks_completed:
                    profile_lines.append(f"Total tasks completed: {profile.total_tasks_completed}")
                if profile_lines:
                    sections.append("USER PROFILE:\n" + "\n".join(profile_lines))
        except Exception:
            pass

    # 2. User behavior patterns (what they struggle with)
    if goal.user_id:
        try:
            behaviors = _storage.list_user_behaviors(goal.user_id)
            if behaviors:
                avoid_items = [b["pattern_key"] for b in behaviors if b["behavior_type"] == "avoids"]
                stall_items = [b["pattern_key"] for b in behaviors if b["behavior_type"] == "stalled"]
                behavior_lines = []
                if avoid_items:
                    behavior_lines.append(f"This user tends to struggle with: {', '.join(avoid_items[:5])}")
                if stall_items:
                    behavior_lines.append(f"This user has stalled on: {', '.join(stall_items[:3])}")
                if behavior_lines:
                    sections.append("USER BEHAVIOR PATTERNS:\n" + "\n".join(behavior_lines))
        except Exception:
            pass

    # 3. Platform-wide insights (aggregate learning from all users)
    try:
        patterns = _storage.get_platform_patterns()
        platform_lines = []

        # What goal types succeed most
        top_types = [g for g in patterns.get("goal_type_insights", [])
                     if g["completion_rate"] > 0 and g["total_goals"] >= 2]
        if top_types:
            best = top_types[0]
            platform_lines.append(
                f"Most successful goal type on platform: {best['goal_type']} "
                f"({best['completion_rate']}% completion rate across {best['total_goals']} goals)"
            )

        # Commonly skipped tasks (so AI avoids generating them)
        skipped = patterns.get("commonly_skipped_tasks", [])[:5]
        if skipped:
            skip_titles = [s["title"] for s in skipped]
            platform_lines.append(
                f"Tasks frequently skipped by users: {', '.join(skip_titles)}. "
                f"Avoid generating these exact tasks; instead provide practical alternatives."
            )

        # Popular services
        services = patterns.get("popular_services", [])[:5]
        if services:
            svc_names = [s["service"] for s in services]
            platform_lines.append(f"Most-used services on this platform: {', '.join(svc_names)}")

        if platform_lines:
            sections.append("PLATFORM-WIDE LEARNINGS (from all users):\n" + "\n".join(platform_lines))
    except Exception:
        pass

    # 4. Success path context (proven paths for similar goals)
    try:
        template_name = _detect_template(goal)
        paths = _storage.list_success_paths(goal_type=template_name)
        if paths:
            best_path = max(paths, key=lambda p: p.times_reused)
            steps = json.loads(best_path.steps_json) if best_path.steps_json else {}
            steps_list = steps.get("steps", steps) if isinstance(steps, dict) else steps
            if steps_list and isinstance(steps_list, list):
                step_titles = [s.get("title", s) if isinstance(s, dict) else str(s)
                              for s in steps_list[:8]]
                sections.append(
                    f"PROVEN PATH (used {best_path.times_reused} times for '{template_name}' goals):\n"
                    f"Steps: {' → '.join(step_titles)}\n"
                    f"Outcome: {best_path.outcome_summary or 'successful'}"
                )
    except Exception:
        pass

    return "\n\n".join(sections)


def decompose_ai(goal: Goal) -> List[Task]:
    """
    Call an OpenAI-compatible API to decompose the goal.

    Enhanced with:
    - User profile and behavior context
    - Platform-wide aggregate learnings
    - Proven success paths from similar goals
    - Richer prompt that produces actionable, experience-informed tasks

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

        # Build rich context from user data, platform patterns, and success paths
        context = _build_context_for_ai(goal)

        system_prompt = (
            "You are an expert goal-decomposition and execution-planning assistant. "
            "Your job is NOT to give generic advice. You must produce hyper-specific, "
            "immediately actionable tasks that a person can start in the next 15 minutes.\n\n"
            "RULES:\n"
            "1. Each task must be completable in a single focused session (5-60 minutes).\n"
            "2. Tasks must be concrete and verifiable — 'Register a Stripe account' not 'Set up payments'.\n"
            "3. Include the exact tools, websites, or commands to use in each task description.\n"
            "4. Order tasks so the first 2-3 produce visible progress (motivation matters).\n"
            "5. If the user is a beginner, prefer no-code tools and guided approaches.\n"
            "6. If a proven path exists in the context, use it as a foundation but adapt to this user.\n"
            "7. If the user avoids certain task types, provide alternatives.\n"
            "8. Each task must have: title (str), description (str), estimated_minutes (int), "
            "and optionally subtasks (array of same shape).\n"
            "9. Return ONLY valid JSON: {\"tasks\": [...]}."
        )
        user_prompt = (
            f"Goal: {goal.title}\n"
            f"Details: {goal.description}\n\n"
            f"Clarifying answers:\n{answers_text}\n"
        )
        if context:
            user_prompt += f"\n--- CONTEXT ---\n{context}\n--- END CONTEXT ---\n"
        user_prompt += f"\nProduce up to {config.MAX_TASKS_PER_GOAL} tasks."

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
    """Entry point: choose AI or template mode based on config.

    Before generating tasks, consults:
    - Success paths from similar goals (1.3) to reorder/modify tasks
    - User behavior patterns (1.2) to skip tasks the user avoids
    """
    if config.OPENAI_API_KEY:
        tasks = decompose_ai(goal)
    else:
        tasks = decompose_template(goal)

    # 1.3: Apply success path insights to reorder/modify tasks
    tasks = _apply_success_path_insights(goal, tasks)

    # 1.2: Apply user behavior patterns to filter/adapt tasks
    tasks = _apply_user_behavior(goal, tasks)

    return tasks


def _apply_success_path_insights(goal: Goal, tasks: List[Task]) -> List[Task]:
    """Apply insights from success paths of similar completed goals.

    Includes:
    - 1.3: Annotate commonly-skipped tasks
    - 2.3: Apply time scaling from past completions
    - 2.4: Surface commonly-added tasks as suggestions in descriptions
    """
    try:
        from teb import storage as _storage  # noqa: PLC0415
        template_name = _detect_template(goal)
        paths = _storage.list_success_paths(goal_type=template_name)
        if not paths or len(paths) < 2:
            return tasks

        insights = apply_success_paths(goal, paths)
        if not insights:
            return tasks

        # Collect commonly-skipped task titles from insights
        commonly_skipped: Set[str] = set()
        commonly_added: List[str] = []
        avg_time_factor = 1.0

        for insight in insights:
            if insight.get("type") == "commonly_skipped":
                for item in insight.get("items", []):
                    if isinstance(item, str):
                        commonly_skipped.add(item.lower())
            # 2.4: Collect commonly-added tasks
            if insight.get("type") == "commonly_added":
                for item in insight.get("items", []):
                    if isinstance(item, str):
                        commonly_added.append(item)
            # 2.3: Collect time scaling data
            if insight.get("type") == "average_tasks":
                avg_count = insight.get("value", 0)
                template_count = len(tasks)
                if avg_count > 0 and template_count > 0:
                    avg_time_factor = avg_count / template_count

        # Mark commonly-skipped tasks with a note rather than removing them
        for t in tasks:
            if t.title.lower() in commonly_skipped:
                t.description = (
                    t.description + " [Note: Many users skip this step. "
                    "Consider whether it applies to your situation.]"
                )

        # 2.3: Apply time scaling from past paths if significantly different.
        # Bounds [0.6, 1.5] ensure we don't over-compress or over-inflate estimates;
        # deviations outside this range likely indicate template mismatch, not real scaling.
        if 0.6 <= avg_time_factor <= 1.5 and avg_time_factor != 1.0:
            for t in tasks:
                t.estimated_minutes = max(5, round(t.estimated_minutes * avg_time_factor))

        # 2.4: Add note about commonly-added tasks to the last task
        if commonly_added and tasks:
            added_note = " [Tip from successful users: Consider also adding: " + ", ".join(commonly_added[:3]) + "]"
            tasks[-1].description = tasks[-1].description + added_note

        return tasks
    except Exception:
        return tasks


def _apply_user_behavior(goal: Goal, tasks: List[Task]) -> List[Task]:
    """Apply user behavior patterns to adapt tasks for the user."""
    if not goal.user_id:
        return tasks
    try:
        from teb import storage as _storage  # noqa: PLC0415
        behaviors = _storage.list_user_behaviors(goal.user_id)
        if not behaviors:
            return tasks

        avoids: Set[str] = set()
        is_chronic_staller = False
        for b in behaviors:
            if b["behavior_type"] == "avoids":
                avoids.add(b["pattern_key"].lower())
            if b["behavior_type"] == "stalled" and b.get("occurrences", 0) >= 3:
                is_chronic_staller = True

        # For users who avoid certain task types, add alternative guidance
        for t in tasks:
            title_lower = t.title.lower()
            for avoid_word in avoids:
                if avoid_word in title_lower:
                    t.description = (
                        t.description + f" [Tip: You've struggled with similar tasks before. "
                        f"Consider using a no-code alternative or asking for help.]"
                    )
                    break

        # For chronic stallers, reduce task scope
        if is_chronic_staller:
            for t in tasks:
                t.estimated_minutes = max(5, int(t.estimated_minutes * 0.7))
                if not t.description.endswith("]"):
                    t.description = (
                        t.description + " [Simplified: Focus on the minimum viable version.]"
                    )

        return tasks
    except Exception:
        return tasks


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

    Produces 2-4 context-aware micro-steps based on the task's title/description
    rather than a generic Research → Execute → Verify pattern.  Each step gets a
    portion of the parent's estimated time, capped at 25 min.
    """
    total = task.estimated_minutes
    title_lower = task.title.lower()
    desc_lower = task.description.lower()
    combined = f"{title_lower} {desc_lower}"

    # Choose a decomposition strategy based on what the task is about
    # More specific patterns are checked first to avoid false matches
    if any(w in combined for w in ("set up", "setup", "register", "sign up", "install", "configure")):
        steps = _decompose_setup_task(task, total)
    elif any(w in combined for w in ("reach out", "contact", "email", "message", "outreach", "network")):
        steps = _decompose_outreach_task(task, total)
    elif any(w in combined for w in ("research", "find", "search", "look up", "identify", "evaluate", "compare")):
        steps = _decompose_research_task(task, total)
    elif any(w in combined for w in ("schedule", "plan", "block", "organize", "prioritize")):
        steps = _decompose_planning_task(task, total)
    elif any(w in combined for w in ("assess", "measure", "track", "review", "reflect", "check")):
        steps = _decompose_review_task(task, total)
    elif any(w in combined for w in ("write", "create", "build", "develop", "design", "implement", "code")):
        steps = _decompose_creation_task(task, total)
    elif any(w in combined for w in ("complete", "finish", "do", "work through", "practice", "exercise")):
        steps = _decompose_execution_task(task, total)
    else:
        steps = _decompose_generic_task(task, total)

    return steps


def _cap_time(minutes: int) -> int:
    """Clamp task time to 5..25 minutes."""
    return max(5, min(25, minutes))


def _decompose_research_task(task: Task, total: int) -> List[Task]:
    half = _cap_time(total // 2)
    quarter = _cap_time(total // 4)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Define what you're looking for",
            description=f"Before diving in, write down 2-3 specific questions you need answered about '{task.title}'.",
            estimated_minutes=quarter, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Gather information from credible sources",
            description="Search for answers to your questions. Use 2-3 different sources. Take brief notes on key findings.",
            estimated_minutes=half, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Summarize findings and pick a direction",
            description="Write a short summary of what you found. Highlight the most actionable insight and decide on your next step.",
            estimated_minutes=quarter, order_index=2,
        ),
    ]


def _decompose_creation_task(task: Task, total: int) -> List[Task]:
    fifth = _cap_time(total // 5)
    half = _cap_time(total * 2 // 5)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Outline what you're creating",
            description=f"Sketch the structure or key components before starting work on '{task.title}'. Keep it rough — a list or bullet points is fine.",
            estimated_minutes=fifth, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Build the first draft or version",
            description="Work through your outline. Focus on getting something complete rather than perfect — you'll refine after.",
            estimated_minutes=half, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Review and polish",
            description="Read through or test what you created. Fix obvious problems and make one quality-of-life improvement.",
            estimated_minutes=fifth, order_index=2,
        ),
    ]


def _decompose_setup_task(task: Task, total: int) -> List[Task]:
    third = _cap_time(total // 3)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Gather requirements and credentials",
            description=f"Before setting up, confirm what you need: account details, software versions, API keys, etc.",
            estimated_minutes=third, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Complete the setup steps",
            description=f"Follow the setup process step by step. If you hit a blocker, note it and move on.",
            estimated_minutes=third, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Verify everything works",
            description="Test that the setup is functional. Try the basic operation once end-to-end.",
            estimated_minutes=third, order_index=2,
        ),
    ]


def _decompose_planning_task(task: Task, total: int) -> List[Task]:
    half = _cap_time(total // 2)
    quarter = _cap_time(total // 4)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="List everything that needs to happen",
            description="Brain-dump all items, appointments, or steps without worrying about order.",
            estimated_minutes=quarter, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Prioritize and assign time slots",
            description="Order the items by importance or deadline. Block specific times in your calendar or write time estimates.",
            estimated_minutes=half, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Set a reminder or checkpoint",
            description="Make sure you have a trigger to check progress. Set a phone alarm, calendar reminder, or note.",
            estimated_minutes=quarter, order_index=2,
        ),
    ]


def _decompose_review_task(task: Task, total: int) -> List[Task]:
    half = _cap_time(total // 2)
    quarter = _cap_time(total // 4)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Collect the data or metrics",
            description=f"Gather the numbers, notes, or observations you need to assess '{task.title}'.",
            estimated_minutes=quarter, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Analyze what's working and what isn't",
            description="Compare your results against your goal. Identify one win and one area to improve.",
            estimated_minutes=half, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Write down one adjustment for next time",
            description="Based on your analysis, commit to one specific change going forward.",
            estimated_minutes=quarter, order_index=2,
        ),
    ]


def _decompose_outreach_task(task: Task, total: int) -> List[Task]:
    third = _cap_time(total // 3)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Prepare your target list",
            description="Identify 5-10 specific people or places to reach out to. Find their contact info.",
            estimated_minutes=third, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Draft and send your messages",
            description="Write a short, personalized message. Send to your list. Don't over-think — a sent message beats a perfect draft.",
            estimated_minutes=third, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Log responses and follow up",
            description="Track who replied in a simple list. Send a follow-up to anyone who didn't respond after 2-3 days.",
            estimated_minutes=third, order_index=2,
        ),
    ]


def _decompose_execution_task(task: Task, total: int) -> List[Task]:
    setup = _cap_time(total // 5)
    main = _cap_time(total * 3 // 5)
    wrap = _cap_time(total // 5)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Prepare your workspace and materials",
            description=f"Get everything you need ready before starting '{task.title}'. Eliminate distractions.",
            estimated_minutes=setup, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Do the work",
            description="Focus on completing the task. If you get stuck for more than 5 minutes, skip to the next part and come back.",
            estimated_minutes=main, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Note what you finished and what's left",
            description="Write down what you accomplished and any items that still need attention.",
            estimated_minutes=wrap, order_index=2,
        ),
    ]


def _decompose_generic_task(task: Task, total: int) -> List[Task]:
    """Fallback for tasks that don't match a specific pattern."""
    third = _cap_time(total // 3)
    return [
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title=f"Clarify what '{task.title}' requires",
            description="Write down exactly what 'done' looks like for this task. List the key steps or deliverables.",
            estimated_minutes=third, order_index=0,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Work through the steps",
            description="Tackle each item from your list in order. Focus on progress, not perfection.",
            estimated_minutes=third, order_index=1,
        ),
        Task(
            goal_id=task.goal_id, parent_id=task.id,
            title="Check your result",
            description="Compare what you produced against your definition of 'done'. Fix any gaps.",
            estimated_minutes=third, order_index=2,
        ),
    ]


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


# ─── Active Coaching: Stagnation Detection & Nudges ──────────────────────────

def detect_stagnation(
    tasks: List[Task],
    last_checkin_age_hours: Optional[float],
    goal_status: str,
) -> Optional[Dict[str, str]]:
    """
    Analyze tasks and check-in recency to detect stagnation.

    Returns a nudge dict {"nudge_type": ..., "message": ...} if the user
    needs a push, or None if things are progressing fine.
    """
    if goal_status == "done":
        return None

    # If the goal is active but no check-in in over 48 hours
    if goal_status in ("decomposed", "in_progress"):
        if last_checkin_age_hours is not None and last_checkin_age_hours > 48:
            return {
                "nudge_type": "stagnation",
                "message": (
                    "It's been over 2 days since your last check-in. "
                    "Even 2 minutes of progress counts. What's one small thing "
                    "you can do right now?"
                ),
            }
        if last_checkin_age_hours is None and goal_status == "in_progress":
            return {
                "nudge_type": "reminder",
                "message": (
                    "You haven't done a check-in yet. Take 2 minutes to note "
                    "what you've done and what's blocking you."
                ),
            }

    # Check for tasks stuck in_progress for too long (no state change)
    in_progress_tasks = [t for t in tasks if t.status == "in_progress"]
    if len(in_progress_tasks) > 3:
        return {
            "nudge_type": "blocker_help",
            "message": (
                f"You have {len(in_progress_tasks)} tasks in progress at once. "
                "Pick the one closest to done, finish it, then move on. "
                "Multitasking kills momentum."
            ),
        }

    # Check if no tasks have been completed at all
    done_count = sum(1 for t in tasks if t.status in ("done", "skipped"))
    total = len(tasks)
    if total > 0 and done_count == 0 and goal_status == "in_progress":
        return {
            "nudge_type": "encouragement",
            "message": (
                "You haven't completed any tasks yet — that's OK! "
                "Focus on just the first one. Completing one task builds "
                "momentum for everything that follows."
            ),
        }

    return None


def analyze_checkin(done_summary: str, blockers: str) -> Dict[str, Any]:
    """
    Analyze a check-in response and provide coaching feedback.

    Returns a dict with "feedback" (coaching message) and "mood_detected"
    based on simple keyword analysis.
    """
    blockers_lower = blockers.lower()
    done_lower = done_summary.lower()

    # Detect mood from keywords
    mood = "neutral"
    if any(w in blockers_lower for w in ("stuck", "confused", "lost", "frustrated", "can't", "don't know")):
        mood = "frustrated"
    elif any(w in blockers_lower for w in ("nothing", "no time", "busy", "life", "distracted")):
        mood = "stuck"
    elif any(w in done_lower for w in ("finished", "completed", "done", "shipped", "launched", "earned")):
        mood = "positive"

    # Generate feedback
    feedback_parts: List[str] = []

    if mood == "frustrated":
        feedback_parts.append(
            "Sounds like you're hitting a wall. That's normal. "
            "Try breaking your current task into an even smaller step — "
            "something you can finish in 10 minutes."
        )
    elif mood == "stuck":
        feedback_parts.append(
            "Life gets in the way — it happens. The key is not letting "
            "a pause turn into a stop. Can you carve out just 15 minutes today?"
        )
    elif mood == "positive":
        feedback_parts.append(
            "Great progress! Keep the momentum going. "
            "What's the next smallest step you can take?"
        )

    if not done_summary.strip():
        feedback_parts.append(
            "No progress today? That's fine — but write down one tiny thing "
            "you'll do tomorrow so you have a clear starting point."
        )

    if blockers.strip() and mood != "frustrated":
        feedback_parts.append(
            f"Blocker noted: \"{blockers.strip()[:100]}\". "
            "Can you rephrase this as a task? E.g. 'Figure out X' or 'Ask Y about Z'."
        )

    return {
        "feedback": " ".join(feedback_parts) if feedback_parts else "Keep going — consistency beats intensity.",
        "mood_detected": mood,
    }


def suggest_outcome_metrics(goal_title: str, goal_description: str) -> List[Dict[str, Any]]:
    """
    Suggest measurable outcome metrics for a goal based on its content.
    Returns a list of metric suggestions with label, unit, and suggested target.
    """
    text = f"{goal_title} {goal_description}".lower()
    suggestions: List[Dict[str, Any]] = []

    # Money vertical
    if any(w in text for w in ("money", "income", "earn", "revenue", "cash", "profit", "freelanc")):
        suggestions.append({"label": "Revenue earned", "unit": "$", "target_value": 500})
        suggestions.append({"label": "Clients acquired", "unit": "clients", "target_value": 3})
        suggestions.append({"label": "Proposals sent", "unit": "proposals", "target_value": 10})

    # Learning vertical
    if any(w in text for w in ("learn", "study", "course", "skill", "read", "understand", "master")):
        suggestions.append({"label": "Modules completed", "unit": "modules", "target_value": 10})
        suggestions.append({"label": "Practice hours logged", "unit": "hours", "target_value": 20})
        suggestions.append({"label": "Projects built", "unit": "projects", "target_value": 1})

    # Fallback generic metrics
    if not suggestions:
        suggestions.append({"label": "Tasks completed", "unit": "tasks", "target_value": 10})
        suggestions.append({"label": "Hours invested", "unit": "hours", "target_value": 10})

    return suggestions


# ─── Proactive Suggestions ────────────────────────────────────────────────────

def generate_proactive_suggestions(
    goal: Goal,
    tasks: List[Task],
) -> List[ProactiveSuggestion]:
    """
    Proactively discover and suggest actions the user didn't think of.

    Analyzes the current goal, task state, and context to surface:
    - Optimization: ways to improve existing approach
    - Opportunity: new actions that could accelerate progress
    - Risk: potential pitfalls to avoid
    - Learning: skills or knowledge that would help
    """
    suggestions: List[ProactiveSuggestion] = []
    goal_id = goal.id or 0
    text = f"{goal.title} {goal.description}".lower()
    template = _detect_template(goal)

    done_count = sum(1 for t in tasks if t.status in ("done", "skipped"))
    total = len(tasks)
    in_progress = [t for t in tasks if t.status == "in_progress"]

    # ── Opportunity suggestions based on goal type ──

    if template == "make_money_online":
        task_titles = " ".join(t.title.lower() for t in tasks)
        if "portfolio" not in task_titles and "sample" not in task_titles:
            suggestions.append(ProactiveSuggestion(
                goal_id=goal_id,
                suggestion="Create a portfolio or work sample before reaching out to clients",
                rationale="Clients are much more likely to respond when they can see concrete examples of your work.",
                category="opportunity",
            ))
        if "automate" not in task_titles:
            suggestions.append(ProactiveSuggestion(
                goal_id=goal_id,
                suggestion="Look into automating repetitive parts of your workflow with AI tools",
                rationale="Tools like ChatGPT, Zapier, or Make.com can automate proposal writing, email follow-ups, and content creation.",
                category="optimization",
            ))

    elif template == "learn_skill":
        if "teach" not in text and "explain" not in text:
            suggestions.append(ProactiveSuggestion(
                goal_id=goal_id,
                suggestion="Try teaching what you've learned to someone else (or write a blog post)",
                rationale="The Feynman technique: teaching forces you to identify gaps in understanding and solidifies knowledge.",
                category="learning",
            ))

    elif template == "build_project":
        task_titles = " ".join(t.title.lower() for t in tasks)
        if "analytics" not in task_titles and "tracking" not in task_titles:
            suggestions.append(ProactiveSuggestion(
                goal_id=goal_id,
                suggestion="Add simple analytics from day one (even just a visit counter)",
                rationale="You can't improve what you don't measure. Free tools like Plausible or Umami take 5 minutes to set up.",
                category="optimization",
            ))

    # ── Progress-aware suggestions ──

    if total > 0 and done_count == 0 and len(in_progress) == 0:
        suggestions.append(ProactiveSuggestion(
            goal_id=goal_id,
            suggestion="Start with the smallest possible task — even 5 minutes counts",
            rationale="The hardest part is starting. Completing just one tiny task creates momentum for everything else.",
            category="opportunity",
        ))

    if total > 0 and done_count > 0 and done_count < total:
        pct = round((done_count / total) * 100)
        if pct >= 50:
            suggestions.append(ProactiveSuggestion(
                goal_id=goal_id,
                suggestion=f"You're {pct}% done! Consider sharing your progress for accountability",
                rationale="Public commitment increases follow-through. Tell a friend, post online, or just write it in a journal.",
                category="opportunity",
            ))

    if len(in_progress) > 2:
        suggestions.append(ProactiveSuggestion(
            goal_id=goal_id,
            suggestion="Focus on finishing one task before starting another",
            rationale="Context-switching significantly reduces productive time. Finishing one thing fully beats having three things half-done.",
            category="risk",
        ))

    # ── Generic always-useful suggestions ──
    if not suggestions:
        suggestions.append(ProactiveSuggestion(
            goal_id=goal_id,
            suggestion="Set a specific time tomorrow to work on this goal — even 15 minutes",
            rationale="Scheduling creates commitment. Vague intentions like 'I'll do it later' almost never convert to action.",
            category="opportunity",
        ))

    return suggestions


# ─── Adaptive Micro-Tasking (Drip Mode) ──────────────────────────────────────

_INITIAL_QUESTIONS_LIMIT = 5  # Ask up to 5 questions upfront, then drip


def get_next_drip_question(goal: Goal) -> Optional[ClarifyingQuestion]:
    """
    In drip mode, return the next clarifying question — but only up to
    _INITIAL_QUESTIONS_LIMIT upfront.  After that, questions are asked
    adaptively based on completed tasks.
    """
    all_questions = get_clarifying_questions(goal)
    answered_keys = set(goal.answers.keys())
    unanswered = [q for q in all_questions if q.key not in answered_keys]

    if not unanswered:
        return None

    # In drip mode, only serve up to INITIAL_QUESTIONS_LIMIT upfront
    answered_count = len(answered_keys)
    if answered_count < _INITIAL_QUESTIONS_LIMIT:
        return unanswered[0]

    # After the limit, remaining questions become "adaptive" — they're
    # returned when the system decides to ask them based on task progress.
    return None


def drip_next_task(
    goal: Goal,
    tasks: List[Task],
    completed_task: Optional[Task] = None,
) -> Optional[Dict[str, Any]]:
    """
    Adaptive drip: return the next single task to work on.

    Unlike full decomposition, drip mode:
    1. Gives exactly one task at a time
    2. Adapts the next task based on what the user completed
    3. If no tasks exist yet, creates the first one from the template
    4. If the last task was completed, generates the next logical one
    5. P2.3: Adapts time estimates based on actual vs estimated completion time
    6. P2.3: Detects stalls and surfaces smaller sub-task versions
    7. P2.2: Flags tasks commonly skipped by others

    Returns a dict with:
        - "task": the next Task to create/focus on (as dict), or None if done
        - "adaptive_question": an optional follow-up question to ask
        - "message": context about why this task was chosen
        - "skip_suggestion": optional hint that others skip this task
    """
    # Build profile from answers
    profile = _build_user_profile(goal.answers)
    template_name = _detect_template(goal)
    template = _TEMPLATES[template_name]

    top_level = [t for t in tasks if t.parent_id is None]
    done_count = sum(1 for t in top_level if t.status in ("done", "skipped"))
    total_template_tasks = len(template.tasks)

    # Use actual task count when real tasks exist (e.g. from AI Orchestrate),
    # only fall back to template count when no tasks exist yet.
    total_actual = len(top_level) if top_level else total_template_tasks

    # P2.3: Detect stall — if there's a current task that hasn't been completed in >2 days
    focus = get_focus_task(tasks)
    if focus and focus.status in ("todo", "in_progress"):
        stall_info = _detect_task_stall(focus)
        if stall_info:
            return {
                "task": focus.to_dict(),
                "is_new": False,
                "adaptive_question": None,
                "message": stall_info["message"],
                "stall_detected": True,
                "sub_task_suggestion": stall_info.get("sub_task"),
            }
        return {
            "task": focus.to_dict(),
            "is_new": False,
            "adaptive_question": None,
            "message": f"Continue working on your current task ({done_count}/{total_actual} completed).",
        }

    # If all actual tasks are done, we're complete
    if top_level and done_count >= total_actual:
        return {
            "task": None,
            "is_new": False,
            "adaptive_question": None,
            "message": "All tasks completed — well done! 🎉",
        }

    # If real tasks already exist (e.g. from AI Orchestrate or decompose),
    # surface the next todo task instead of creating new template tasks.
    if top_level:
        todo_tasks = sorted(
            [t for t in top_level if t.status == "todo"],
            key=lambda x: (x.order_index, x.id or 0),
        )
        if todo_tasks:
            next_task = todo_tasks[0]
            return {
                "task": next_task.to_dict(),
                "is_new": False,
                "adaptive_question": None,
                "message": f"Task {done_count + 1} of {total_actual}.",
            }
        # All tasks are in non-todo, non-done states (executing, failed, etc.)
        active = [t for t in top_level if t.status not in ("done", "skipped")]
        if active:
            return {
                "task": active[0].to_dict(),
                "is_new": False,
                "adaptive_question": None,
                "message": f"Task is {active[0].status} ({done_count}/{total_actual} completed).",
            }

    # Determine which template task comes next (only when no real tasks exist)
    next_index = done_count
    if next_index >= total_template_tasks:
        return {
            "task": None,
            "is_new": False,
            "adaptive_question": None,
            "message": "All planned tasks are done!",
        }

    # Adapt the next template task
    tt = template.tasks[next_index]
    adapted = _adapt_template_task(tt, profile)

    # P2.3: Scale time estimates based on user's pace
    time_scale = _compute_time_scale(tasks)
    if time_scale != 1.0:
        adapted = _TemplateTask(
            title=adapted.title,
            description=adapted.description,
            estimated_minutes=max(5, round(adapted.estimated_minutes * time_scale)),
            subtasks=adapted.subtasks,
        )

    # If the completed task gave us signals, adapt further
    message = f"Task {next_index + 1} of {total_template_tasks}."
    if completed_task:
        message = _drip_adaptation_message(completed_task, adapted, done_count, total_template_tasks)

    new_task = Task(
        goal_id=goal.id,  # type: ignore[arg-type]
        title=adapted.title,
        description=adapted.description,
        estimated_minutes=adapted.estimated_minutes,
        order_index=next_index,
    )

    # Attach subtask templates if the template task has them
    if tt.subtasks:
        new_task._subtask_templates = [  # type: ignore[attr-defined]
            _adapt_template_task(sub, profile) for sub in tt.subtasks
        ]

    # Check if we should ask an adaptive question
    adaptive_q = _get_adaptive_question(goal, done_count, template_name)

    # P2.2: Check if this task is commonly skipped by others
    skip_suggestion = _check_skip_rate(template_name, tt.title)

    result: Dict[str, Any] = {
        "task": new_task.to_dict(),
        "is_new": True,
        "adaptive_question": {"key": adaptive_q.key, "text": adaptive_q.text, "hint": adaptive_q.hint} if adaptive_q else None,
        "message": message,
    }
    if skip_suggestion:
        result["skip_suggestion"] = skip_suggestion

    return result


def _detect_task_stall(task: Task) -> Optional[Dict[str, Any]]:
    """P2.3: Check if a task has been stalled for more than 2 days."""
    if not task.updated_at:
        return None
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    task_time = task.updated_at.replace(tzinfo=timezone.utc) if task.updated_at.tzinfo is None else task.updated_at
    age = now - task_time
    if age > timedelta(days=2):
        days = age.days
        # Suggest a smaller sub-task
        sub_task = {
            "title": f"Quick win: Spend 15 minutes on \"{task.title}\"",
            "description": f"Instead of finishing the whole task, just spend 15 minutes making any progress. "
                          f"Open the file, write one paragraph, do one small step. "
                          f"It's been {days} days — starting is the hardest part.",
            "estimated_minutes": 15,
        }
        return {
            "message": f"It's been {days} days since you last worked on this. "
                       f"Want to try a smaller version to get momentum?",
            "sub_task": sub_task,
        }
    return None


def _compute_time_scale(tasks: List[Task]) -> float:
    """P2.3: Compute scaling factor based on actual vs estimated completion times.

    If the user consistently finishes faster, return < 1.0.
    If consistently slower, return > 1.0.
    Returns 1.0 if not enough data.
    """
    completed = [t for t in tasks if t.status == "done" and t.created_at and t.updated_at]
    if len(completed) < 2:
        return 1.0

    ratios = []
    for t in completed:
        created = t.created_at.replace(tzinfo=timezone.utc) if t.created_at.tzinfo is None else t.created_at
        updated = t.updated_at.replace(tzinfo=timezone.utc) if t.updated_at.tzinfo is None else t.updated_at
        actual_minutes = (updated - created).total_seconds() / 60
        if t.estimated_minutes > 0 and actual_minutes > 0:
            ratios.append(actual_minutes / t.estimated_minutes)

    if not ratios:
        return 1.0

    avg_ratio = sum(ratios) / len(ratios)
    # Clamp to reasonable range
    return max(0.5, min(2.0, avg_ratio))


def _check_skip_rate(template_name: str, task_title: str) -> Optional[str]:
    """P2.2: Check if this task is commonly skipped in success paths."""
    try:
        from teb import storage as _storage
        paths = _storage.list_success_paths(goal_type=template_name)
    except Exception:
        return None

    if len(paths) < 2:
        return None

    skip_count = 0
    total = len(paths)
    for sp in paths:
        raw = json.loads(sp.steps_json) if sp.steps_json else {}
        if isinstance(raw, dict):
            # Check deviations.skipped_template_tasks
            devs = raw.get("deviations", {})
            if task_title in devs.get("skipped_template_tasks", []):
                skip_count += 1
            # Also check steps list in the new dict format
            elif "steps" in raw:
                for step in raw["steps"]:
                    if step.get("title") == task_title and step.get("status") == "skipped":
                        skip_count += 1
                        break
        elif isinstance(raw, list):
            for step in raw:
                if step.get("title") == task_title and step.get("status") == "skipped":
                    skip_count += 1

    if total > 0 and skip_count / total >= 0.5:
        # P2.2: increment reuse counter for all paths that informed this decision
        for sp in paths:
            if sp.id is not None:
                try:
                    _storage.increment_success_path_reuse(sp.id)
                except Exception:
                    pass
        return f"Many people skip this step ({skip_count}/{total}). Still want to include it?"
    return None


def _drip_adaptation_message(
    completed: Task,
    next_task: _TemplateTask,
    done_count: int,
    total: int,
) -> str:
    """Generate a contextual message based on what was just completed."""
    pct = round((done_count / total) * 100) if total > 0 else 0
    parts = [f"Great work completing \"{completed.title}\"! ({pct}% done)"]

    if pct >= 50:
        parts.append("You're past the halfway point — momentum is building!")
    elif pct >= 25:
        parts.append("Solid progress. Each task gets you closer.")

    parts.append(f"Next up: \"{next_task.title}\"")
    return " ".join(parts)


def _get_adaptive_question(
    goal: Goal,
    tasks_completed: int,
    template_name: str,
) -> Optional[ClarifyingQuestion]:
    """
    After the initial 5 questions, drip additional questions based on progress.

    This creates a more natural conversation flow where questions are asked
    at relevant moments rather than all upfront.
    """
    answered = set(goal.answers.keys())

    # After completing 2 tasks, ask about pace
    if tasks_completed == 2 and "pace_feedback" not in answered:
        return ClarifyingQuestion(
            key="pace_feedback",
            text="How are the tasks feeling so far — too easy, about right, or too challenging?",
            hint="e.g. too easy, just right, a bit overwhelming…",
        )

    # After completing 4 tasks, ask about focus
    if tasks_completed == 4 and "focus_area" not in answered:
        return ClarifyingQuestion(
            key="focus_area",
            text="Which part of this goal are you enjoying most? We can lean into that.",
            hint="e.g. the research part, the hands-on work, outreach…",
        )

    # Money-specific: after 3 tasks, ask about first earnings
    if template_name == "make_money_online" and tasks_completed == 3 and "first_earnings" not in answered:
        return ClarifyingQuestion(
            key="first_earnings",
            text="Have you earned anything yet, even a small amount? What worked?",
            hint="e.g. $0 so far, $50 from a freelance gig, etc.",
        )

    return None


# ─── Success Path Learning ──────────────────────────────────────────────────

def capture_success_path(goal: Goal, tasks: List[Task]) -> Optional[SuccessPath]:
    """
    Automatically capture a success path when a goal is completed.

    Extracts the sequence of completed tasks (with their actual completion
    order and any notes) and saves it as a reusable pattern for future
    users with similar goals.

    P2.2: Also captures deviation patterns — which tasks were skipped,
    reordered, or added manually compared to the template.

    Returns the created SuccessPath, or None if the goal isn't complete.
    """
    if goal.status != "done":
        return None

    template_name = _detect_template(goal)
    top_level = [t for t in tasks if t.parent_id is None]
    completed = [t for t in top_level if t.status in ("done", "skipped")]

    if not completed:
        return None

    # Get template task titles for deviation analysis
    template = _TEMPLATES.get(template_name)
    template_titles = [tt.title for tt in template.tasks] if template else []

    # Build step summaries from completed tasks
    steps = []
    for t in sorted(completed, key=lambda x: x.order_index):
        step: Dict[str, Any] = {
            "title": t.title,
            "description": t.description[:200],
            "estimated_minutes": t.estimated_minutes,
            "status": t.status,
        }
        # Track if this was a template task or user-added
        step["from_template"] = t.title in template_titles
        # Include subtask info
        children = [c for c in tasks if c.parent_id == t.id]
        if children:
            step["subtasks_completed"] = sum(1 for c in children if c.status in ("done", "skipped"))
            step["subtasks_total"] = len(children)
        steps.append(step)

    # P2.2: Compute deviations
    actual_titles = [s["title"] for s in steps]
    skipped_template_tasks = [t for t in template_titles if t not in actual_titles
                              and t not in [x.title for x in top_level]]
    added_tasks = [s["title"] for s in steps if not s.get("from_template")]

    deviations: Dict[str, Any] = {
        "skipped_template_tasks": skipped_template_tasks,
        "added_tasks": added_tasks,
        "template_task_count": len(template_titles),
        "actual_task_count": len(top_level),
    }

    # Build outcome summary from task progression
    total_tasks = len(top_level)
    done_tasks = len(completed)
    skipped_tasks = sum(1 for t in completed if t.status == "skipped")

    outcome_parts = [f"Completed {done_tasks}/{total_tasks} tasks"]
    if skipped_tasks > 0:
        outcome_parts.append(f"({skipped_tasks} skipped)")
    if goal.answers:
        # Include relevant context from answers
        skill = goal.answers.get("skill_level", "")
        if skill:
            outcome_parts.append(f"Skill level: {skill}")
        timeline = goal.answers.get("timeline", "")
        if timeline:
            outcome_parts.append(f"Timeline: {timeline}")

    # Store deviations in the steps_json alongside step data
    path_data = {"steps": steps, "deviations": deviations}

    return SuccessPath(
        goal_type=template_name,
        steps_json=json.dumps(path_data),
        outcome_summary=". ".join(outcome_parts),
        source_goal_id=goal.id,
    )


def apply_success_paths(
    goal: Goal,
    success_paths: List[SuccessPath],
) -> List[Dict[str, Any]]:
    """
    Analyze existing success paths to provide recommendations for a new goal.

    Returns a list of insights derived from successful completions:
    - Which steps were most commonly completed vs skipped
    - Average task counts and time estimates
    - Tips from patterns in successful paths
    - P2.2: Deviation patterns — commonly skipped template tasks
    """
    if not success_paths:
        return []

    template_name = _detect_template(goal)
    relevant = [sp for sp in success_paths if sp.goal_type == template_name]

    if not relevant:
        return []

    insights: List[Dict[str, Any]] = []

    # Analyze step patterns across successful paths
    step_titles: Dict[str, int] = {}
    skip_titles: Dict[str, int] = {}
    skipped_template_tasks: Dict[str, int] = {}

    for sp in relevant:
        raw = json.loads(sp.steps_json) if sp.steps_json else []
        # Handle both old format (list of steps) and new format (dict with steps+deviations)
        if isinstance(raw, dict):
            steps = raw.get("steps", [])
            devs = raw.get("deviations", {})
            for t_title in devs.get("skipped_template_tasks", []):
                skipped_template_tasks[t_title] = skipped_template_tasks.get(t_title, 0) + 1
        else:
            steps = raw

        for step in steps:
            title = step.get("title", "")
            if title:
                step_titles[title] = step_titles.get(title, 0) + 1
                if step.get("status") == "skipped":
                    skip_titles[title] = skip_titles.get(title, 0) + 1

    # Find most commonly completed steps
    if step_titles:
        most_common = sorted(step_titles.items(), key=lambda x: x[1], reverse=True)[:3]
        insights.append({
            "type": "popular_steps",
            "message": "Most successful users completed these steps first",
            "steps": [{"title": t, "times_completed": c} for t, c in most_common],
        })

    # Find commonly skipped steps
    if skip_titles:
        most_skipped = sorted(skip_titles.items(), key=lambda x: x[1], reverse=True)[:2]
        insights.append({
            "type": "commonly_skipped",
            "message": "These steps are often skipped — consider if they're worth your time",
            "steps": [{"title": t, "times_skipped": c} for t, c in most_skipped],
        })

    # P2.2: Surface template tasks that are commonly skipped entirely
    if skipped_template_tasks:
        most_dropped = sorted(skipped_template_tasks.items(), key=lambda x: x[1], reverse=True)[:3]
        insights.append({
            "type": "template_deviations",
            "message": "Many people skip these template tasks — you might want to as well",
            "tasks": [{"title": t, "times_skipped": c, "total_paths": len(relevant)} for t, c in most_dropped],
        })

    # Reuse count as social proof
    total_reused = sum(sp.times_reused for sp in relevant)
    if total_reused > 0:
        insights.append({
            "type": "social_proof",
            "message": f"This approach has been successfully used {total_reused + len(relevant)} times by others.",
        })

    return insights


def validate_spending(
    amount: float,
    budget_daily_limit: float,
    budget_total_limit: float,
    spent_today: float,
    spent_total: float,
) -> Dict[str, Any]:
    """
    Validate whether a spending request fits within budget limits.

    Returns a dict with "allowed" (bool), "reason" (str), and
    "remaining_daily" / "remaining_total".
    """
    remaining_daily = budget_daily_limit - spent_today
    remaining_total = budget_total_limit - spent_total

    if amount <= 0:
        return {
            "allowed": False,
            "reason": "Amount must be positive",
            "remaining_daily": remaining_daily,
            "remaining_total": remaining_total,
        }

    if amount > remaining_daily:
        return {
            "allowed": False,
            "reason": f"Exceeds daily limit. Remaining today: ${remaining_daily:.2f}",
            "remaining_daily": remaining_daily,
            "remaining_total": remaining_total,
        }

    if amount > remaining_total:
        return {
            "allowed": False,
            "reason": f"Exceeds total budget. Remaining: ${remaining_total:.2f}",
            "remaining_daily": remaining_daily,
            "remaining_total": remaining_total,
        }

    return {
        "allowed": True,
        "reason": "Within budget limits",
        "remaining_daily": remaining_daily - amount,
        "remaining_total": remaining_total - amount,
    }
