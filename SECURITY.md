# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest (`main`) | ✅ |
| Older releases | ❌ (update to latest) |

## Reporting a Vulnerability

If you discover a security vulnerability in OmniBridge, please **do not** open a public GitHub issue.

Instead, report it privately via:

- **Telegram:** [@Config_Vortex55](https://t.me/Config_Vortex55) (DM)
- **GitHub Security Advisories:** Use the "Report a vulnerability" button on the Security tab

Please include:
1. Description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Suggested fix (if any)

We aim to respond within **48 hours** and release a fix within **7 days** for critical issues.

## Security Best Practices for Deployment

When deploying OmniBridge, please follow these guidelines:

### Credentials
- **Change** `ADMIN_PASSWORD` to a strong, unique password (minimum 16 characters)
- **Change** `ADMIN_SECRET_KEY` to a long random string: `openssl rand -hex 32`
- **Never** commit your `.env` file or any file containing real credentials

### Network
- Run OmniBridge behind a **reverse proxy** (nginx, Caddy) with TLS in production
- Restrict access to the `/admin` path via IP allowlist if possible
- Do not expose port `8080` directly to the internet without TLS

### API Keys
- The default `sk-test` key is intended for **local development only**
- Revoke or delete it before any public deployment
- Rotate API keys regularly via the admin dashboard

### Gemini Cookies
- Gemini `__Secure-1PSID` cookies are sensitive — treat them like passwords
- They are stored in the local SQLite database; protect the `data/` directory
- The auto-refresh mechanism keeps cookies fresh, but expired cookies are flagged in the admin dashboard

## Scope

The following are **in scope** for security reports:
- Authentication bypass in the admin panel
- API key validation bypass
- SQL injection or data corruption
- Sensitive data exposure
- Remote code execution

The following are **out of scope**:
- Rate limiting (OmniBridge relies on upstream provider limits)
- Security of the upstream Google Gemini service itself
- Issues in third-party libraries (report those upstream)
