# Plugin Development Guide

Build and publish plugins that extend TEB's functionality.

---

## Overview

TEB plugins are defined by a **manifest** (JSON metadata) and optional
**views** (HTML/JS injected into the UI). Plugins are registered via the API
and appear in the Plugin Marketplace.

## Plugin Manifest

Every plugin starts with a manifest:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "A short description of what the plugin does.",
  "author": "Your Name",
  "entry_point": "main.js",
  "permissions": ["read:goals", "write:tasks"],
  "config_schema": {
    "api_key": { "type": "string", "required": true },
    "enabled": { "type": "boolean", "default": true }
  }
}
```

| Field           | Required | Description                              |
|-----------------|----------|------------------------------------------|
| name            | ✅       | Unique identifier                        |
| version         | ✅       | Semver version string                    |
| description     | ✅       | Human-readable summary                   |
| author          | ✅       | Author name or organisation              |
| entry_point     |          | JS file to load in the browser           |
| permissions     |          | List of API scopes the plugin needs      |
| config_schema   |          | User-configurable settings               |

## Step-by-Step Tutorial

### 1. Scaffold

Create a directory for your plugin:

```bash
mkdir my-teb-plugin && cd my-teb-plugin
```

Create `manifest.json` with the structure above.

### 2. Implement Logic

Create `main.js`:

```javascript
// main.js – runs in the browser when the plugin is activated
(function () {
  console.log("My TEB plugin loaded!");

  // Access TEB APIs via fetch()
  fetch("/api/goals")
    .then((r) => r.json())
    .then((goals) => {
      console.log("Goals:", goals);
    });
})();
```

### 3. Register via API

```bash
curl -X POST http://localhost:8000/api/plugins \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-plugin",
    "version": "1.0.0",
    "description": "Demo plugin",
    "author": "Dev",
    "manifest_json": "{\"entry_point\":\"main.js\"}",
    "enabled": true
  }'
```

### 4. Add a Custom View (optional)

Plugin views inject HTML into the TEB UI:

```bash
curl -X POST http://localhost:8000/api/plugin-views \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_id": 1,
    "view_name": "sidebar-widget",
    "html_content": "<div class=\"my-widget\">Hello from plugin!</div>",
    "position": "sidebar"
  }'
```

### 5. Publish to Marketplace

```bash
curl -X POST http://localhost:8000/api/plugin-listings \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-plugin",
    "description": "A handy TEB extension.",
    "author": "Dev",
    "version": "1.0.0",
    "manifest_url": "https://example.com/manifest.json",
    "category": "productivity"
  }'
```

## Plugin Lifecycle

1. **Install** – admin registers the manifest via the API.
2. **Activate** – the plugin is enabled and its entry point loads.
3. **Configure** – users adjust settings defined in `config_schema`.
4. **Update** – push a new version and update the manifest.
5. **Uninstall** – admin deletes the plugin record.

## Best Practices

- Keep plugins small and focused on one feature.
- Use the `permissions` field to request only the scopes you need.
- Handle errors gracefully – the host app should not break.
- Include a README and changelog with your plugin.
- Test against the latest TEB release before publishing.

## API Reference

| Endpoint                  | Method | Description                    |
|---------------------------|--------|--------------------------------|
| `/api/plugins`            | GET    | List installed plugins         |
| `/api/plugins`            | POST   | Register a new plugin          |
| `/api/plugins/{id}`       | DELETE | Remove a plugin                |
| `/api/plugin-views`       | GET    | List plugin views              |
| `/api/plugin-views`       | POST   | Add a view                     |
| `/api/plugin-listings`    | GET    | Browse the marketplace         |
| `/api/plugin-listings`    | POST   | Publish a listing              |
