"""
Tool and service discovery engine.

Autonomously discovers new tools and services relevant to a user's goals
and skill level. Uses both AI-powered discovery and template-based matching.

This is marked as VERY IMPORTANT by the project owner.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from teb import storage
from teb.integrations import _BUILTIN_INTEGRATIONS, find_matching_integrations

logger = logging.getLogger(__name__)


# ─── Service Knowledge Base ──────────────────────────────────────────────────
# Curated catalog of tools organized by category and skill level.
# This acts as the base knowledge that gets extended by AI discovery.

_DISCOVERABLE_SERVICES: List[Dict[str, Any]] = [
    # ── No-code / Low-code platforms ──────────────────────────────────────
    {
        "service_name": "bubble",
        "category": "no-code",
        "description": "Visual web app builder — build full apps without writing code",
        "url": "https://bubble.io",
        "capabilities": ["build web apps", "create databases", "design UIs", "add workflows"],
        "skill_level": "beginner",
        "use_cases": ["mvp", "startup", "side_project", "prototype"],
    },
    {
        "service_name": "webflow",
        "category": "no-code",
        "description": "Visual website builder with CMS and hosting",
        "url": "https://webflow.com",
        "capabilities": ["design websites", "manage content", "host sites", "e-commerce"],
        "skill_level": "beginner",
        "use_cases": ["portfolio", "business_site", "landing_page", "blog"],
    },
    {
        "service_name": "airtable",
        "category": "no-code",
        "description": "Spreadsheet-database hybrid for organizing any kind of data",
        "url": "https://airtable.com",
        "capabilities": ["create databases", "build views", "automate workflows", "api access"],
        "skill_level": "beginner",
        "use_cases": ["crm", "project_management", "inventory", "content_calendar"],
    },
    {
        "service_name": "zapier",
        "category": "automation",
        "description": "Connect apps and automate workflows without code",
        "url": "https://zapier.com",
        "capabilities": ["connect apps", "automate tasks", "create workflows", "schedule actions"],
        "skill_level": "beginner",
        "use_cases": ["automation", "integration", "notifications", "data_sync"],
    },
    {
        "service_name": "make",
        "category": "automation",
        "description": "Visual automation platform (formerly Integromat) for complex workflows",
        "url": "https://www.make.com",
        "capabilities": ["visual workflows", "api connections", "data transformation", "scheduling"],
        "skill_level": "intermediate",
        "use_cases": ["automation", "data_pipeline", "integration"],
    },
    # ── Freelancing / Income platforms ────────────────────────────────────
    {
        "service_name": "upwork",
        "category": "freelancing",
        "description": "Freelancing marketplace — find clients for any skill",
        "url": "https://upwork.com",
        "capabilities": ["find freelance work", "submit proposals", "get paid", "build reputation"],
        "skill_level": "beginner",
        "use_cases": ["make_money_online", "freelancing", "side_project"],
    },
    {
        "service_name": "fiverr",
        "category": "freelancing",
        "description": "Service marketplace — list your skills as purchasable gigs",
        "url": "https://fiverr.com",
        "capabilities": ["list services", "sell skills", "get reviews", "earn money"],
        "skill_level": "beginner",
        "use_cases": ["make_money_online", "freelancing", "side_income"],
    },
    {
        "service_name": "gumroad",
        "category": "e-commerce",
        "description": "Sell digital products (ebooks, courses, software) directly",
        "url": "https://gumroad.com",
        "capabilities": ["sell digital products", "process payments", "email customers", "analytics"],
        "skill_level": "beginner",
        "use_cases": ["write_book", "sell_course", "digital_products", "make_money_online"],
    },
    {
        "service_name": "lemonsqueezy",
        "category": "e-commerce",
        "description": "Modern payments platform for selling digital products and SaaS",
        "url": "https://lemonsqueezy.com",
        "capabilities": ["sell software", "subscriptions", "license keys", "tax handling"],
        "skill_level": "intermediate",
        "use_cases": ["launch_startup", "saas", "digital_products"],
    },
    # ── Banking / Finance ─────────────────────────────────────────────────
    {
        "service_name": "mercury",
        "category": "banking",
        "description": "Online business banking for startups — API-first bank accounts",
        "url": "https://mercury.com",
        "capabilities": ["business accounts", "api banking", "transfers", "treasury"],
        "skill_level": "intermediate",
        "use_cases": ["launch_startup", "business_banking", "finance"],
    },
    {
        "service_name": "wise",
        "category": "banking",
        "description": "International money transfers and multi-currency accounts",
        "url": "https://wise.com",
        "capabilities": ["international transfers", "multi-currency", "business accounts", "api"],
        "skill_level": "beginner",
        "use_cases": ["international_payments", "freelancing", "business"],
    },
    # ── Learning Platforms ────────────────────────────────────────────────
    {
        "service_name": "coursera",
        "category": "learning",
        "description": "Online courses from top universities and companies",
        "url": "https://coursera.org",
        "capabilities": ["take courses", "earn certificates", "learn skills", "degree programs"],
        "skill_level": "beginner",
        "use_cases": ["learn_skill", "career", "education"],
    },
    {
        "service_name": "udemy",
        "category": "learning",
        "description": "Marketplace for online courses on any topic",
        "url": "https://udemy.com",
        "capabilities": ["take courses", "learn skills", "practice projects"],
        "skill_level": "beginner",
        "use_cases": ["learn_skill", "career_change", "upskilling"],
    },
    # ── Hosting / Deployment ──────────────────────────────────────────────
    {
        "service_name": "railway",
        "category": "hosting",
        "description": "Simple cloud deployment — deploy from GitHub in minutes",
        "url": "https://railway.app",
        "capabilities": ["deploy apps", "databases", "cron jobs", "auto-scaling"],
        "skill_level": "intermediate",
        "use_cases": ["deploy", "hosting", "build_project", "launch_startup"],
    },
    {
        "service_name": "render",
        "category": "hosting",
        "description": "Cloud hosting for web services, databases, and static sites",
        "url": "https://render.com",
        "capabilities": ["deploy web services", "managed databases", "static sites", "cron jobs"],
        "skill_level": "intermediate",
        "use_cases": ["deploy", "hosting", "build_project"],
    },
    {
        "service_name": "netlify",
        "category": "hosting",
        "description": "Deploy and host static sites and serverless functions",
        "url": "https://netlify.com",
        "capabilities": ["static hosting", "serverless functions", "forms", "identity"],
        "skill_level": "beginner",
        "use_cases": ["portfolio", "blog", "landing_page", "build_project"],
    },
    # ── Design ────────────────────────────────────────────────────────────
    {
        "service_name": "canva",
        "category": "design",
        "description": "Easy graphic design tool for non-designers",
        "url": "https://canva.com",
        "capabilities": ["create graphics", "design social media", "presentations", "marketing materials"],
        "skill_level": "beginner",
        "use_cases": ["marketing", "social_media", "branding", "content"],
    },
    {
        "service_name": "figma",
        "category": "design",
        "description": "Collaborative design tool for UI/UX and prototyping",
        "url": "https://figma.com",
        "capabilities": ["design UIs", "prototype", "collaborate", "design systems"],
        "skill_level": "intermediate",
        "use_cases": ["build_project", "design", "prototype", "launch_startup"],
    },
    # ── Marketing / Content ───────────────────────────────────────────────
    {
        "service_name": "mailchimp",
        "category": "marketing",
        "description": "Email marketing and audience management platform",
        "url": "https://mailchimp.com",
        "capabilities": ["email campaigns", "audience management", "automations", "landing pages"],
        "skill_level": "beginner",
        "use_cases": ["marketing", "email_list", "launch_startup"],
    },
    {
        "service_name": "notion",
        "category": "productivity",
        "description": "All-in-one workspace for notes, docs, and project management",
        "url": "https://notion.so",
        "capabilities": ["notes", "wikis", "databases", "project management", "templates"],
        "skill_level": "beginner",
        "use_cases": ["organization", "planning", "documentation", "write_book"],
    },
]


# ─── Discovery Engine ────────────────────────────────────────────────────────

def discover_for_goal(goal_title: str, goal_description: str = "",
                      user_skill_level: str = "beginner",
                      template_name: str = "") -> List[Dict[str, Any]]:
    """Discover relevant tools and services for a given goal.

    Combines:
    1. Keyword matching against the discoverable services catalog
    2. Template-based matching (goal template → service use_cases)
    3. Skill-level filtering
    4. Built-in integration catalog matching

    Args:
        goal_title: The goal title/description
        goal_description: Additional goal context
        user_skill_level: 'beginner', 'intermediate', or 'advanced'
        template_name: Optional template name from decomposer

    Returns:
        Ranked list of service recommendations
    """
    text = f"{goal_title} {goal_description}".lower()
    results: List[Dict[str, Any]] = []

    # 1. Match from discoverable services catalog
    for svc in _DISCOVERABLE_SERVICES:
        score = _score_service(svc, text, template_name, user_skill_level)
        if score > 0:
            results.append({
                **svc,
                "score": score,
                "source": "discovery_catalog",
            })

    # 2. Match from built-in integrations
    integration_matches = find_matching_integrations(text)
    for integ in integration_matches:
        results.append({
            "service_name": integ["service_name"],
            "category": integ["category"],
            "description": f"API integration: {', '.join(integ.get('capabilities', [])[:3])}",
            "url": integ.get("docs_url", integ.get("base_url", "")),
            "capabilities": integ.get("capabilities", []),
            "score": 5,  # Lower score since these need API keys
            "source": "integration_catalog",
        })

    # 3. Match from previously discovered services in DB
    try:
        db_services = storage.list_discovered_services(limit=100)
        for ds in db_services:
            ds_text = f"{ds['service_name']} {ds['description']} {' '.join(ds.get('capabilities', []))}".lower()
            overlap = len(set(text.split()) & set(ds_text.split()))
            if overlap > 0:
                results.append({
                    **ds,
                    "score": overlap + ds.get("relevance_score", 0),
                    "source": "previously_discovered",
                })
    except Exception:
        pass  # DB might not have the table yet

    # Deduplicate by service_name, keeping highest score
    seen: Dict[str, Dict[str, Any]] = {}
    for r in results:
        name = r["service_name"]
        if name not in seen or r["score"] > seen[name]["score"]:
            seen[name] = r

    # Sort by score descending
    ranked = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:10]


def discover_for_user(user_id: int) -> List[Dict[str, Any]]:
    """Discover services based on user's goals and behavior patterns.

    Looks at:
    - User's active goals
    - User's behavior patterns (what they avoid/prefer)
    - User's profile (skill level)
    """
    try:
        goals = storage.list_goals(user_id=user_id)
    except Exception:
        return []

    # Get user profile for skill level
    skill_level = "beginner"
    try:
        profiles = storage.list_user_profiles()
        user_profile = next((p for p in profiles if getattr(p, "user_id", None) == user_id), None)
        if user_profile:
            skill_level = user_profile.experience_level or "beginner"
    except Exception:
        pass

    # Get behavior patterns
    avoids: set[str] = set()
    try:
        behaviors = storage.list_user_behaviors(user_id, behavior_type="avoids")
        avoids = {b["pattern_key"] for b in behaviors}
    except Exception:
        pass

    all_recommendations: List[Dict[str, Any]] = []
    for goal in goals[:5]:  # Limit to 5 most recent goals
        recs = discover_for_goal(goal.title, goal.description, skill_level)
        # Filter out services the user avoids
        recs = [r for r in recs if r["service_name"] not in avoids]
        all_recommendations.extend(recs)

    # Deduplicate
    seen: Dict[str, Dict[str, Any]] = {}
    for r in all_recommendations:
        name = r["service_name"]
        if name not in seen or r["score"] > seen[name]["score"]:
            seen[name] = r

    return sorted(seen.values(), key=lambda x: x["score"], reverse=True)[:15]


def record_discovery(service_name: str, category: str, description: str,
                     url: str, capabilities: List[str],
                     discovered_by: str = "ai", relevance_score: float = 0.5) -> Dict[str, Any]:
    """Record a newly discovered service into the database for future recommendations."""
    return storage.create_discovered_service(
        service_name=service_name,
        category=category,
        description=description,
        url=url,
        capabilities=json.dumps(capabilities),
        discovered_by=discovered_by,
        relevance_score=relevance_score,
    )


# ─── Scoring Logic ────────────────────────────────────────────────────────────

def _score_service(service: Dict[str, Any], text: str,
                   template_name: str, user_skill_level: str) -> float:
    """Score a service's relevance to a goal."""
    score = 0.0

    # Direct name mention
    if service["service_name"] in text:
        score += 15

    # Category match
    if service.get("category", "") in text:
        score += 5

    # Capability keyword matching
    for cap in service.get("capabilities", []):
        words = cap.lower().split()
        matches = sum(1 for w in words if w in text and len(w) > 3)
        score += matches * 2

    # Template-based matching
    if template_name and template_name in service.get("use_cases", []):
        score += 10

    # Use-case keyword matching
    for uc in service.get("use_cases", []):
        if uc.replace("_", " ") in text or uc in text:
            score += 5

    # Skill level bonus: prefer services matching user's level
    svc_level = service.get("skill_level", "intermediate")
    level_order = {"beginner": 0, "intermediate": 1, "advanced": 2}
    user_level_num = level_order.get(user_skill_level, 0)
    svc_level_num = level_order.get(svc_level, 1)

    if svc_level_num <= user_level_num:
        score += 3  # Accessible to user
    elif svc_level_num == user_level_num + 1:
        score += 1  # Slight stretch
    # else: too advanced, no bonus

    # Description keyword matching
    desc_words = service.get("description", "").lower().split()
    for w in desc_words:
        if w in text and len(w) > 4:
            score += 1

    return score


# ─── AI-Powered Discovery ────────────────────────────────────────────────────

def ai_discover_services(goal_text: str) -> List[Dict[str, Any]]:
    """Use AI to discover services relevant to a goal.

    Falls back gracefully when no AI provider is configured.
    Returns a list of discovered service dicts.
    """
    try:
        from teb.ai_client import ai_chat_json
        from teb import config

        if not config.has_ai():
            return []

        prompt = f"""Given this user goal: "{goal_text}"

Suggest 3-5 specific online tools or services (not generic advice) that would help accomplish this goal.
For each, provide:
- service_name (lowercase, no spaces)
- category (one of: no-code, automation, freelancing, e-commerce, banking, learning, hosting, design, marketing, productivity, analytics, development, social, ai)
- description (one sentence)
- url (the service website)
- capabilities (list of 3-5 things it can do)

Return JSON array."""

        result = ai_chat_json(
            system="You are a tool/service recommendation engine. Return only valid JSON arrays.",
            user=prompt,
        )

        if isinstance(result, list):
            # Store discoveries for future use
            for svc in result:
                if isinstance(svc, dict) and svc.get("service_name"):
                    try:
                        record_discovery(
                            service_name=svc["service_name"],
                            category=svc.get("category", ""),
                            description=svc.get("description", ""),
                            url=svc.get("url", ""),
                            capabilities=svc.get("capabilities", []),
                            discovered_by="ai",
                            relevance_score=0.7,
                        )
                    except Exception:
                        pass
            return result
        return []
    except Exception as e:
        logger.debug("AI discovery unavailable: %s", e)
        return []
