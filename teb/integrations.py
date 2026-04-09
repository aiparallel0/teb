"""
Pre-built integration registry.

Ships with knowledge of popular services (Stripe, Namecheap, Vercel,
SendGrid, etc.) so the executor and browser automation engine can
generate better plans even without AI — and so that AI plans are more
accurate when available.

Each integration describes:
  - The service name and category
  - Base URL and auth pattern
  - Capabilities (what it can do)
  - Common API endpoints (method, path, description)
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from teb import storage
from teb.models import Integration


# ─── Built-in catalog ────────────────────────────────────────────────────────

_BUILTIN_INTEGRATIONS: List[Dict] = [
    {
        "service_name": "stripe",
        "category": "payment",
        "base_url": "https://api.stripe.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://stripe.com/docs/api",
        "capabilities": [
            "accept payments",
            "create customers",
            "manage subscriptions",
            "send invoices",
            "handle refunds",
            "create payment links",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v1/customers", "description": "Create a customer"},
            {"method": "POST", "path": "/v1/payment_intents", "description": "Create a payment intent"},
            {"method": "POST", "path": "/v1/products", "description": "Create a product"},
            {"method": "POST", "path": "/v1/prices", "description": "Create a price"},
            {"method": "POST", "path": "/v1/subscriptions", "description": "Create a subscription"},
            {"method": "POST", "path": "/v1/invoices", "description": "Create an invoice"},
            {"method": "POST", "path": "/v1/payment_links", "description": "Create a payment link"},
            {"method": "GET", "path": "/v1/balance", "description": "Get account balance"},
        ],
    },
    {
        "service_name": "namecheap",
        "category": "domain",
        "base_url": "https://api.namecheap.com",
        "auth_type": "api_key",
        "auth_header": "X-Api-Key",
        "docs_url": "https://www.namecheap.com/support/api/intro/",
        "capabilities": [
            "register domains",
            "manage DNS records",
            "check domain availability",
            "transfer domains",
            "renew domains",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/xml.response?command=namecheap.domains.check", "description": "Check domain availability"},
            {"method": "GET", "path": "/xml.response?command=namecheap.domains.create", "description": "Register a domain"},
            {"method": "GET", "path": "/xml.response?command=namecheap.domains.dns.setHosts", "description": "Set DNS records"},
            {"method": "GET", "path": "/xml.response?command=namecheap.domains.getList", "description": "List domains"},
        ],
    },
    {
        "service_name": "vercel",
        "category": "hosting",
        "base_url": "https://api.vercel.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://vercel.com/docs/rest-api",
        "capabilities": [
            "deploy websites",
            "manage projects",
            "configure domains",
            "manage environment variables",
            "view deployment logs",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v13/deployments", "description": "Create a deployment"},
            {"method": "GET", "path": "/v9/projects", "description": "List projects"},
            {"method": "POST", "path": "/v9/projects", "description": "Create a project"},
            {"method": "GET", "path": "/v6/deployments", "description": "List deployments"},
            {"method": "POST", "path": "/v10/projects/{id}/domains", "description": "Add a domain to project"},
        ],
    },
    {
        "service_name": "sendgrid",
        "category": "email",
        "base_url": "https://api.sendgrid.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://docs.sendgrid.com/api-reference",
        "capabilities": [
            "send transactional emails",
            "send marketing campaigns",
            "manage contacts",
            "create email templates",
            "track email analytics",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v3/mail/send", "description": "Send an email"},
            {"method": "POST", "path": "/v3/marketing/contacts", "description": "Add contacts"},
            {"method": "GET", "path": "/v3/templates", "description": "List email templates"},
            {"method": "POST", "path": "/v3/marketing/singlesends", "description": "Create a campaign"},
        ],
    },
    {
        "service_name": "github",
        "category": "development",
        "base_url": "https://api.github.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://docs.github.com/en/rest",
        "capabilities": [
            "create repositories",
            "manage issues",
            "create pull requests",
            "manage releases",
            "configure webhooks",
            "manage GitHub Pages",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/user/repos", "description": "Create a repository"},
            {"method": "GET", "path": "/repos/{owner}/{repo}", "description": "Get a repository"},
            {"method": "POST", "path": "/repos/{owner}/{repo}/issues", "description": "Create an issue"},
            {"method": "POST", "path": "/repos/{owner}/{repo}/pages", "description": "Enable GitHub Pages"},
        ],
    },
    {
        "service_name": "cloudflare",
        "category": "hosting",
        "base_url": "https://api.cloudflare.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://developers.cloudflare.com/api/",
        "capabilities": [
            "manage DNS records",
            "configure CDN",
            "manage SSL certificates",
            "deploy Workers",
            "manage Cloudflare Pages",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/client/v4/zones", "description": "List zones"},
            {"method": "POST", "path": "/client/v4/zones/{zone_id}/dns_records", "description": "Create DNS record"},
            {"method": "POST", "path": "/client/v4/pages/projects", "description": "Create a Pages project"},
        ],
    },
    {
        "service_name": "twitter",
        "category": "social",
        "base_url": "https://api.twitter.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://developer.twitter.com/en/docs",
        "capabilities": [
            "post tweets",
            "search tweets",
            "manage followers",
            "send direct messages",
            "create lists",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/2/tweets", "description": "Create a tweet"},
            {"method": "GET", "path": "/2/tweets/search/recent", "description": "Search recent tweets"},
            {"method": "GET", "path": "/2/users/me", "description": "Get authenticated user"},
        ],
    },
    {
        "service_name": "linkedin",
        "category": "social",
        "base_url": "https://api.linkedin.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://learn.microsoft.com/en-us/linkedin/",
        "capabilities": [
            "share posts",
            "manage connections",
            "search profiles",
            "send messages",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v2/ugcPosts", "description": "Create a post"},
            {"method": "GET", "path": "/v2/me", "description": "Get profile"},
        ],
    },
    {
        "service_name": "plausible",
        "category": "analytics",
        "base_url": "https://plausible.io/api",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://plausible.io/docs/stats-api",
        "capabilities": [
            "track page views",
            "view site analytics",
            "manage sites",
            "create shared links",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/v1/stats/realtime/visitors", "description": "Get realtime visitors"},
            {"method": "GET", "path": "/v1/stats/aggregate", "description": "Get aggregate stats"},
            {"method": "POST", "path": "/v1/sites", "description": "Create a site"},
        ],
    },
    {
        "service_name": "openai",
        "category": "ai",
        "base_url": "https://api.openai.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://platform.openai.com/docs/api-reference",
        "capabilities": [
            "generate text",
            "generate images",
            "generate code",
            "create embeddings",
            "transcribe audio",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v1/chat/completions", "description": "Generate chat completion"},
            {"method": "POST", "path": "/v1/images/generations", "description": "Generate images"},
            {"method": "POST", "path": "/v1/embeddings", "description": "Create embeddings"},
        ],
    },
    {
        "service_name": "digitalocean",
        "category": "hosting",
        "base_url": "https://api.digitalocean.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://docs.digitalocean.com/reference/api/",
        "capabilities": [
            "create droplets",
            "manage DNS",
            "create databases",
            "manage Kubernetes",
            "object storage",
            "deploy apps",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/v2/droplets", "description": "List droplets"},
            {"method": "POST", "path": "/v2/droplets", "description": "Create a droplet"},
            {"method": "GET", "path": "/v2/databases", "description": "List databases"},
            {"method": "POST", "path": "/v2/databases", "description": "Create a database"},
            {"method": "GET", "path": "/v2/domains", "description": "List domains"},
            {"method": "POST", "path": "/v2/domains/{domain_name}/records", "description": "Create DNS record"},
            {"method": "GET", "path": "/v2/apps", "description": "List apps"},
            {"method": "POST", "path": "/v2/apps", "description": "Create an app"},
        ],
    },
    {
        "service_name": "aws-s3",
        "category": "storage",
        "base_url": "https://s3.amazonaws.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://docs.aws.amazon.com/s3/",
        "capabilities": [
            "store files",
            "serve static assets",
            "host websites",
            "version objects",
            "presigned URLs",
        ],
        "common_endpoints": [
            {"method": "PUT", "path": "/{bucket}/{key}", "description": "Upload an object"},
            {"method": "GET", "path": "/{bucket}/{key}", "description": "Download an object"},
            {"method": "DELETE", "path": "/{bucket}/{key}", "description": "Delete an object"},
            {"method": "GET", "path": "/{bucket}?list-type=2", "description": "List objects in bucket"},
        ],
    },
    {
        "service_name": "twilio",
        "category": "messaging",
        "base_url": "https://api.twilio.com",
        "auth_type": "api_key",
        "auth_header": "Authorization",
        "docs_url": "https://www.twilio.com/docs/api",
        "capabilities": [
            "send SMS",
            "send WhatsApp messages",
            "make voice calls",
            "verify phone numbers",
            "send emails (SendGrid)",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/2010-04-01/Accounts/{AccountSid}/Messages.json", "description": "Send a message"},
            {"method": "GET", "path": "/2010-04-01/Accounts/{AccountSid}/Messages.json", "description": "List messages"},
            {"method": "POST", "path": "/2010-04-01/Accounts/{AccountSid}/Calls.json", "description": "Make a call"},
        ],
    },
    {
        "service_name": "hubspot",
        "category": "crm",
        "base_url": "https://api.hubapi.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://developers.hubspot.com/docs/api/overview",
        "capabilities": [
            "manage contacts",
            "create deals",
            "track emails",
            "manage pipelines",
            "marketing automation",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/crm/v3/objects/contacts", "description": "Create a contact"},
            {"method": "GET", "path": "/crm/v3/objects/contacts", "description": "List contacts"},
            {"method": "POST", "path": "/crm/v3/objects/deals", "description": "Create a deal"},
            {"method": "GET", "path": "/crm/v3/objects/deals", "description": "List deals"},
            {"method": "POST", "path": "/marketing/v3/emails", "description": "Create a marketing email"},
        ],
    },
    {
        "service_name": "airtable",
        "category": "database",
        "base_url": "https://api.airtable.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://airtable.com/developers/web/api/introduction",
        "capabilities": [
            "create records",
            "list records",
            "update records",
            "delete records",
            "manage bases",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/v0/{baseId}/{tableId}", "description": "List records"},
            {"method": "POST", "path": "/v0/{baseId}/{tableId}", "description": "Create records"},
            {"method": "PATCH", "path": "/v0/{baseId}/{tableId}/{recordId}", "description": "Update a record"},
            {"method": "DELETE", "path": "/v0/{baseId}/{tableId}/{recordId}", "description": "Delete a record"},
        ],
    },
    {
        "service_name": "notion",
        "category": "productivity",
        "base_url": "https://api.notion.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://developers.notion.com/reference",
        "capabilities": [
            "create pages",
            "query databases",
            "update blocks",
            "manage users",
            "search content",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v1/pages", "description": "Create a page"},
            {"method": "POST", "path": "/v1/databases/{database_id}/query", "description": "Query a database"},
            {"method": "PATCH", "path": "/v1/pages/{page_id}", "description": "Update a page"},
            {"method": "GET", "path": "/v1/search", "description": "Search content"},
        ],
    },
    {
        "service_name": "slack",
        "category": "messaging",
        "base_url": "https://slack.com/api",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://api.slack.com/methods",
        "capabilities": [
            "send messages",
            "create channels",
            "upload files",
            "manage users",
            "search messages",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/chat.postMessage", "description": "Send a message"},
            {"method": "POST", "path": "/conversations.create", "description": "Create a channel"},
            {"method": "POST", "path": "/files.upload", "description": "Upload a file"},
            {"method": "GET", "path": "/users.list", "description": "List users"},
            {"method": "GET", "path": "/conversations.list", "description": "List channels"},
        ],
    },
    {
        "service_name": "discord",
        "category": "messaging",
        "base_url": "https://discord.com/api",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://discord.com/developers/docs/intro",
        "capabilities": [
            "send messages",
            "create channels",
            "manage roles",
            "send webhooks",
            "manage servers",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v10/channels/{channel_id}/messages", "description": "Send a message"},
            {"method": "POST", "path": "/v10/guilds/{guild_id}/channels", "description": "Create a channel"},
            {"method": "PATCH", "path": "/v10/guilds/{guild_id}/members/{user_id}", "description": "Update a member"},
            {"method": "POST", "path": "/webhooks/{webhook_id}/{webhook_token}", "description": "Execute webhook"},
        ],
    },
    {
        "service_name": "shopify",
        "category": "e-commerce",
        "base_url": "https://{shop}.myshopify.com/admin/api/2024-01",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://shopify.dev/docs/api/admin-rest",
        "capabilities": [
            "manage products",
            "create orders",
            "manage customers",
            "handle inventory",
            "process refunds",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/products.json", "description": "List products"},
            {"method": "POST", "path": "/products.json", "description": "Create a product"},
            {"method": "GET", "path": "/orders.json", "description": "List orders"},
            {"method": "POST", "path": "/orders.json", "description": "Create an order"},
            {"method": "GET", "path": "/customers.json", "description": "List customers"},
        ],
    },
    {
        "service_name": "mailgun",
        "category": "email",
        "base_url": "https://api.mailgun.net",
        "auth_type": "api_key",
        "auth_header": "Authorization",
        "docs_url": "https://documentation.mailgun.com/",
        "capabilities": [
            "send emails",
            "manage mailing lists",
            "track email events",
            "validate emails",
            "manage domains",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v3/{domain}/messages", "description": "Send an email"},
            {"method": "GET", "path": "/v3/{domain}/events", "description": "Get email events"},
            {"method": "POST", "path": "/v3/lists", "description": "Create a mailing list"},
            {"method": "GET", "path": "/v3/address/validate", "description": "Validate an email address"},
        ],
    },
    {
        "service_name": "resend",
        "category": "email",
        "base_url": "https://api.resend.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://resend.com/docs/api-reference",
        "capabilities": [
            "send transactional emails",
            "manage domains",
            "create email templates",
            "track events",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/emails", "description": "Send an email"},
            {"method": "GET", "path": "/emails/{email_id}", "description": "Get email details"},
            {"method": "POST", "path": "/domains", "description": "Add a domain"},
            {"method": "GET", "path": "/domains", "description": "List domains"},
        ],
    },
    {
        "service_name": "supabase",
        "category": "database",
        "base_url": "https://{project_ref}.supabase.co",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "docs_url": "https://supabase.com/docs/reference/api/introduction",
        "capabilities": [
            "manage PostgreSQL database",
            "authentication",
            "real-time subscriptions",
            "file storage",
            "edge functions",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/rest/v1/{table}", "description": "Query a table"},
            {"method": "POST", "path": "/rest/v1/{table}", "description": "Insert rows"},
            {"method": "PATCH", "path": "/rest/v1/{table}", "description": "Update rows"},
            {"method": "DELETE", "path": "/rest/v1/{table}", "description": "Delete rows"},
            {"method": "POST", "path": "/auth/v1/signup", "description": "Sign up a user"},
        ],
    },
    {
        "service_name": "anthropic",
        "category": "ai",
        "base_url": "https://api.anthropic.com",
        "auth_type": "api_key",
        "auth_header": "x-api-key",
        "docs_url": "https://docs.anthropic.com/en/api",
        "capabilities": [
            "generate text",
            "analyze images",
            "process documents",
            "function calling",
            "long context",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/v1/messages", "description": "Create a message"},
            {"method": "GET", "path": "/v1/models", "description": "List models"},
        ],
    },
    {
        "service_name": "google-maps",
        "category": "maps",
        "base_url": "https://maps.googleapis.com",
        "auth_type": "api_key",
        "auth_header": "Authorization",
        "docs_url": "https://developers.google.com/maps/documentation",
        "capabilities": [
            "geocode addresses",
            "get directions",
            "search places",
            "calculate distance matrix",
            "embed maps",
        ],
        "common_endpoints": [
            {"method": "GET", "path": "/maps/api/geocode/json", "description": "Geocode an address"},
            {"method": "GET", "path": "/maps/api/directions/json", "description": "Get directions"},
            {"method": "GET", "path": "/maps/api/place/nearbysearch/json", "description": "Search nearby places"},
            {"method": "GET", "path": "/maps/api/distancematrix/json", "description": "Calculate distance matrix"},
        ],
    },
    {
        "service_name": "zapier",
        "category": "automation",
        "base_url": "https://hooks.zapier.com",
        "auth_type": "api_key",
        "auth_header": "X-Api-Key",
        "docs_url": "https://zapier.com/developer/documentation",
        "capabilities": [
            "trigger zaps",
            "automate workflows",
            "connect apps",
            "schedule tasks",
            "transform data",
        ],
        "common_endpoints": [
            {"method": "POST", "path": "/hooks/catch/{account_id}/{hook_id}/", "description": "Trigger a zap via webhook"},
        ],
    },
]


def seed_integrations() -> int:
    """Seed the integration registry with built-in service catalog. Returns count created."""
    created = 0
    for item in _BUILTIN_INTEGRATIONS:
        existing = storage.get_integration(item["service_name"])
        if existing:
            continue
        integration = Integration(
            service_name=item["service_name"],
            category=item["category"],
            base_url=item["base_url"],
            auth_type=item["auth_type"],
            auth_header=item["auth_header"],
            docs_url=item.get("docs_url", ""),
            capabilities=json.dumps(item.get("capabilities", [])),
            common_endpoints=json.dumps(item.get("common_endpoints", [])),
        )
        storage.create_integration(integration)
        created += 1
    return created


def get_catalog() -> List[Dict]:
    """Return the full built-in catalog (does not require DB)."""
    return [
        {
            "service_name": item["service_name"],
            "category": item["category"],
            "base_url": item["base_url"],
            "capabilities": item.get("capabilities", []),
        }
        for item in _BUILTIN_INTEGRATIONS
    ]


def find_matching_integrations(task_text: str) -> List[Dict]:
    """Find integrations from the catalog that are relevant to a task description."""
    text = task_text.lower()
    matches = []
    for item in _BUILTIN_INTEGRATIONS:
        score = 0
        # Check service name
        if item["service_name"] in text:
            score += 10
        # Check capabilities
        for cap in item.get("capabilities", []):
            if any(word in text for word in cap.lower().split()):
                score += 1
        # Check category
        if item["category"] in text:
            score += 3
        if score > 0:
            matches.append({"integration": item, "score": score})

    matches.sort(key=lambda m: m["score"], reverse=True)
    return [m["integration"] for m in matches[:5]]


def get_endpoints_for_service(service_name: str) -> List[Dict]:
    """Get common API endpoints for a known service."""
    for item in _BUILTIN_INTEGRATIONS:
        if item["service_name"] == service_name:
            return item.get("common_endpoints", [])
    return []
