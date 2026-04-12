# API Client Libraries

TEB exposes a REST API that any HTTP client can consume. Below are examples
for Python, JavaScript, and Go.

---

## Authentication

All API requests require authentication. Use one of:

- **Session cookie** – set after login via `/api/login`.
- **Personal API key** – pass as `Authorization: Bearer <key>`.

## Base URL

```
http://localhost:8000/api
```

If `BASE_PATH` is configured, prefix accordingly (e.g. `/teb/api`).

---

## Python Client

```python
import requests

BASE = "http://localhost:8000/api"
session = requests.Session()

# Login
session.post(f"{BASE}/login", json={
    "username": "alice",
    "password": "secret",
})

# List goals
goals = session.get(f"{BASE}/goals").json()
for g in goals:
    print(g["title"], g["status"])

# Create a task
task = session.post(f"{BASE}/tasks", json={
    "goal_id": 1,
    "title": "Write docs",
    "priority": 2,
}).json()
print("Created task:", task["id"])

# Update task status
session.put(f"{BASE}/tasks/{task['id']}", json={
    "status": "done",
})
```

### Using an API Key

```python
headers = {"Authorization": "Bearer teb_abc123..."}
goals = requests.get(f"{BASE}/goals", headers=headers).json()
```

---

## JavaScript Client

```javascript
const BASE = "http://localhost:8000/api";

// Login (browser – cookies are stored automatically)
await fetch(`${BASE}/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "alice", password: "secret" }),
  credentials: "include",
});

// List goals
const goals = await fetch(`${BASE}/goals`, { credentials: "include" })
  .then((r) => r.json());
console.log(goals);

// Create a goal
const newGoal = await fetch(`${BASE}/goals`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  credentials: "include",
  body: JSON.stringify({
    title: "Ship v2",
    description: "Release version 2.0",
  }),
}).then((r) => r.json());
```

### Using an API Key (Node.js)

```javascript
const headers = {
  Authorization: "Bearer teb_abc123...",
  "Content-Type": "application/json",
};

const goals = await fetch(`${BASE}/goals`, { headers }).then((r) => r.json());
```

---

## Go Client

```go
package main

import (
    "bytes"
    "encoding/json"
    "fmt"
    "net/http"
    "net/http/cookiejar"
)

const base = "http://localhost:8000/api"

func main() {
    jar, _ := cookiejar.New(nil)
    client := &http.Client{Jar: jar}

    // Login
    body, _ := json.Marshal(map[string]string{
        "username": "alice",
        "password": "secret",
    })
    client.Post(base+"/login", "application/json", bytes.NewReader(body))

    // List goals
    resp, _ := client.Get(base + "/goals")
    defer resp.Body.Close()
    var goals []map[string]interface{}
    json.NewDecoder(resp.Body).Decode(&goals)
    for _, g := range goals {
        fmt.Println(g["title"], g["status"])
    }
}
```

### Using an API Key

```go
req, _ := http.NewRequest("GET", base+"/goals", nil)
req.Header.Set("Authorization", "Bearer teb_abc123...")
resp, _ := http.DefaultClient.Do(req)
```

---

## Common Endpoints

| Method | Path                  | Description              |
|--------|-----------------------|--------------------------|
| POST   | `/api/login`          | Authenticate             |
| POST   | `/api/register`       | Create account           |
| GET    | `/api/goals`          | List goals               |
| POST   | `/api/goals`          | Create goal              |
| GET    | `/api/goals/{id}`     | Get goal details         |
| PUT    | `/api/goals/{id}`     | Update goal              |
| DELETE | `/api/goals/{id}`     | Delete goal              |
| GET    | `/api/tasks`          | List tasks (by goal_id)  |
| POST   | `/api/tasks`          | Create task              |
| PUT    | `/api/tasks/{id}`     | Update task              |
| DELETE | `/api/tasks/{id}`     | Delete task              |
| GET    | `/api/check-ins`      | List check-ins           |
| POST   | `/api/check-ins`      | Create check-in          |

See the interactive API docs at `/docs` (Swagger UI) or `/redoc` for the
full endpoint list.

## Error Handling

All errors return JSON:

```json
{
  "detail": "Not found"
}
```

| Status | Meaning               |
|--------|-----------------------|
| 400    | Bad request / validation error |
| 401    | Unauthorized          |
| 403    | Forbidden             |
| 404    | Resource not found    |
| 429    | Rate limit exceeded   |
| 500    | Internal server error |
