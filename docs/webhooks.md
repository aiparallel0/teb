# Webhook Documentation

TEB can send HTTP POST requests to external URLs when events occur.

---

## Setting Up a Webhook

```bash
curl -X POST http://localhost:8000/api/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/hooks/teb",
    "secret": "my-webhook-secret",
    "events_json": "[\"goal.created\", \"task.completed\"]"
  }'
```

| Field        | Required | Description                              |
|--------------|----------|------------------------------------------|
| url          | ✅       | HTTPS endpoint to receive events         |
| secret       |          | Shared secret for HMAC signature         |
| events_json  |          | JSON array of event types to subscribe   |

## Event Types

| Event                | Trigger                              |
|----------------------|--------------------------------------|
| `goal.created`       | A new goal is created                |
| `goal.updated`       | A goal is modified                   |
| `goal.completed`     | A goal status changes to completed   |
| `goal.deleted`       | A goal is deleted                    |
| `task.created`       | A new task is created                |
| `task.updated`       | A task is modified                   |
| `task.completed`     | A task status changes to done        |
| `task.deleted`       | A task is deleted                    |
| `check_in.created`   | A new check-in is submitted          |
| `milestone.reached`  | A milestone is marked complete       |
| `user.registered`    | A new user registers                 |

## Payload Schema

Every webhook delivery is an HTTP POST with a JSON body:

```json
{
  "event": "task.completed",
  "timestamp": "2025-01-15T10:30:00Z",
  "data": {
    "id": 42,
    "title": "Write documentation",
    "status": "done",
    "goal_id": 7
  }
}
```

### Headers

| Header                  | Description                          |
|-------------------------|--------------------------------------|
| `Content-Type`          | `application/json`                   |
| `X-TEB-Event`          | Event type (e.g. `task.completed`)   |
| `X-TEB-Delivery`       | Unique delivery ID (UUID)            |
| `X-TEB-Signature-256`  | HMAC-SHA256 of the body using secret |

### Signature Verification

If a `secret` is configured, verify the signature:

```python
import hashlib, hmac

def verify(body: bytes, secret: str, signature: str) -> bool:
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

## Retry Behaviour

| Attempt | Delay   |
|---------|---------|
| 1       | 0 s     |
| 2       | 1 s     |
| 3       | 5 s     |
| 4       | 25 s    |

If all 4 attempts fail (non-2xx response or timeout), the delivery is marked
as failed. You can view delivery status via the API.

## Webhook Rules

Use webhook rules to filter which payloads are sent:

```bash
curl -X POST http://localhost:8000/api/webhook-rules \
  -H "Content-Type: application/json" \
  -d '{
    "webhook_id": 1,
    "field": "data.priority",
    "operator": "gte",
    "value": "3"
  }'
```

Only events matching **all** rules for a webhook will be delivered.

## Managing Webhooks

| Method | Path                      | Description            |
|--------|---------------------------|------------------------|
| GET    | `/api/webhooks`           | List all webhooks      |
| POST   | `/api/webhooks`           | Create a webhook       |
| DELETE | `/api/webhooks/{id}`      | Delete a webhook       |
| GET    | `/api/webhook-rules`      | List rules             |
| POST   | `/api/webhook-rules`      | Create a rule          |
| DELETE | `/api/webhook-rules/{id}` | Delete a rule          |

## Best Practices

1. **Always use HTTPS** for your webhook endpoint.
2. **Verify signatures** to ensure payloads are from TEB.
3. **Respond quickly** – return 2xx within 5 seconds; do heavy processing
   asynchronously.
4. **Handle duplicates** – use `X-TEB-Delivery` to de-duplicate.
5. **Monitor failures** – check delivery status and fix broken endpoints
   promptly.
