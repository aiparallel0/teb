# FAQ & Troubleshooting

Common questions and solutions for TEB.

---

## General

### Q: What is TEB?

TEB is an AI-powered goal and task management platform built with FastAPI and
SQLite. It combines planning, execution, browser automation, and coaching in
a single application.

### Q: What are the system requirements?

- Python 3.10 or newer
- SQLite 3.35 or newer (bundled with Python)
- A modern web browser (Chrome, Firefox, Safari, Edge)

### Q: Is TEB free?

Yes. TEB is open-source software.

---

## Installation

### Q: `pip install teb` fails with a version error

Make sure you are using Python 3.10+:

```bash
python --version   # Should be 3.10 or higher
pip install --upgrade pip
pip install teb
```

### Q: The server won't start – "Address already in use"

Another process is using port 8000. Either stop it or choose a different port:

```bash
PORT=3001 teb
```

### Q: How do I run behind a reverse proxy?

Set the `BASE_PATH` environment variable so TEB knows its URL prefix:

```bash
BASE_PATH=/teb teb
```

Then configure your proxy (nginx, Caddy, etc.) to forward `/teb/*` to the
TEB server.

---

## Features

### Q: How does AI decomposition work?

When you click **AI Decompose** on a goal, TEB sends the goal description to
its AI pipeline which returns a list of suggested tasks. You can review and
edit each suggestion before saving.

### Q: Can I use TEB without AI features?

Absolutely. AI features are optional. You can create and manage goals, tasks,
and timelines entirely manually.

### Q: How do webhooks retry?

Failed webhook deliveries are retried up to 3 times with exponential back-off
(1 s, 5 s, 25 s). See [Webhook Docs](webhooks.md) for full details.

### Q: How do I create a plugin?

See the [Plugin Development Guide](plugin-guide.md) for a step-by-step
walkthrough.

---

## Troubleshooting

### Database is locked

SQLite allows only one writer at a time. If you see `database is locked`:

1. Make sure only one TEB instance is writing to the same database file.
2. Increase the busy timeout (default is 5 000 ms).
3. Check for long-running transactions and optimise them.

### 401 Unauthorized on API calls

- Ensure you are sending a valid session cookie or API key header.
- Personal API keys must be passed as `Authorization: Bearer <key>`.
- Check that the key has not expired.

### CSS or JS not loading

- Hard-refresh your browser (`Ctrl+Shift+R`).
- Clear the service worker cache in DevTools → Application → Service Workers.
- Verify the `BASE_PATH` is set correctly if using a reverse proxy.

### Push notifications not working

- The browser must support the Web Push API.
- Make sure you granted notification permission.
- Check that the VAPID keys are configured on the server.

---

## Still Stuck?

- Search existing [GitHub Issues](https://github.com/user/teb/issues)
- Open a new issue with steps to reproduce
- Join the community chat (see [Community Links](#))
