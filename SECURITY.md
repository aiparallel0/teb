# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ |

## Reporting a Vulnerability

If you discover a security vulnerability in teb, please report it responsibly:

1. **Do not** open a public issue
2. Email the maintainers at the contact information listed in the repository, or use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
3. Include a description of the vulnerability, steps to reproduce, and potential impact
4. We will acknowledge receipt within 48 hours and provide a timeline for a fix

## Security Considerations

### Authentication

- JWT tokens are signed with `TEB_JWT_SECRET` — **always change this in production**
- Passwords are hashed with bcrypt
- Auth endpoints are rate-limited (20 requests/minute per IP)
- Accounts are locked after repeated failed login attempts
- Refresh tokens enable session management without long-lived access tokens

### Data Protection

- API credentials are encrypted at rest using Fernet symmetric encryption (via `TEB_SECRET_KEY`)
- SQLite database should be stored on an encrypted volume in production
- Sensitive configuration values should be set via environment variables, not committed to source

### Financial Safety

- All spending is subject to budget limits (daily and total caps)
- Per-transaction approval workflow prevents unauthorized spending
- Category-based limits restrict spending to approved categories
- Denial reasons are logged for audit trails

### Deployment

- Docker image runs as a non-root user (`appuser`)
- CORS origins should be explicitly configured via `TEB_CORS_ORIGINS` (do not use `*` in production)
- Health check endpoint (`GET /health`) does not expose sensitive information
- Set `TEB_LOG_LEVEL=WARNING` or higher in production to avoid logging sensitive data

### Dependencies

- Dependencies are pinned to specific versions in `requirements.txt`
- Regularly update dependencies to patch known vulnerabilities

## Best Practices for Production

1. Generate a strong `TEB_JWT_SECRET`: `python -c "import secrets; print(secrets.token_urlsafe(64))"`
2. Generate a Fernet key for `TEB_SECRET_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
3. Use HTTPS (terminate TLS at a reverse proxy like nginx or Caddy)
4. Set explicit `TEB_CORS_ORIGINS` for your domain
5. Back up the SQLite database regularly
6. Monitor the health endpoint for availability
7. Review spending approvals and execution logs periodically
