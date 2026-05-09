# Security Policy

## Reporting Security Vulnerabilities

**Do not open public issues for security vulnerabilities.**

We take security seriously. If you discover a security vulnerability, please report it responsibly.

## How to Report

### GitHub Private Vulnerability Reporting (Preferred)

Use [GitHub's private vulnerability reporting](../../security/advisories/new) to
submit a vulnerability report directly on this repository. This is the preferred
channel — reports are private, tracked, and linked to the repository.

Send an email to: **<villmow.products@gmail.com>**

### Email (Fallback)

Send an email to: **<villmow.products@gmail.com>**

### What to Include

Please include as much of the following information as possible:

- **Description** - Clear description of the vulnerability
- **Impact** - Potential impact and severity assessment
- **Steps to reproduce** - Detailed steps to reproduce the issue
- **Affected files** - Which configuration files or scripts are affected
- **Suggested fix** - If you have a suggested fix or mitigation

### Example Report

```text
Subject: [SECURITY] Grafana provisioned with anonymous admin access

Description:
The Docker Compose configuration sets GF_AUTH_ANONYMOUS_ORG_ROLE=Admin,
granting any unauthenticated user full Grafana admin privileges including
the ability to modify dashboards and data sources.

Impact:
An attacker on the network could modify or delete dashboards, create
new data sources, or exfiltrate metrics data.

Steps to Reproduce:
1. Start the stack: just start
2. Open http://<host>:3000 in a browser (no login required)
3. Observe full admin access to Grafana

Affected Files:
docker-compose.yml (Grafana environment variables)

Suggested Fix:
Set GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer or require authentication.
```

## Response Timeline

We aim to respond to security reports within the following timeframes:

| Stage                    | Timeframe              |
|--------------------------|------------------------|
| Initial acknowledgment   | 48 hours               |
| Preliminary assessment   | 1 week                 |
| Fix development          | Varies by severity     |
| Public disclosure        | After fix is released  |

## Severity Assessment

We use the following severity levels:

| Severity     | Description                          | Response           |
|--------------|--------------------------------------|--------------------|
| **Critical** | Remote code execution, data breach   | Immediate priority |
| **High**     | Privilege escalation, data exposure  | High priority      |
| **Medium**   | Limited impact vulnerabilities       | Standard priority  |
| **Low**      | Minor issues, hardening              | Scheduled fix      |

## Responsible Disclosure

We follow responsible disclosure practices:

1. **Report privately** - Do not disclose publicly until a fix is available
2. **Allow reasonable time** - Give us time to investigate and develop a fix
3. **Coordinate disclosure** - We will work with you on disclosure timing
4. **Credit** - We will credit you in the security advisory (if desired)

## What We Will Do

When you report a vulnerability:

1. Acknowledge receipt within 48 hours
2. Investigate and validate the report
3. Develop and test a fix
4. Release the fix
5. Publish a security advisory

## Scope

### In Scope

- Prometheus configuration and scrape targets
- Grafana provisioning and dashboard definitions
- Alert rules and notification channels
- Custom Python exporter (`exporter/`)
- Docker Compose files
- Justfile recipes

### Out of Scope

- Upstream Prometheus, Grafana, or Loki vulnerabilities (report upstream)
- Application code in other HomericIntelligence repos (report to that repo)
- Social engineering attacks
- Physical security

## Security Best Practices

When contributing to ProjectArgus:

- Never embed credentials in configuration files — use environment variables
- Restrict Grafana to authenticated access (avoid anonymous admin)
- Bind monitoring and metrics ports to localhost or the internal network only
- Review alert rules for sensitive data leakage in notification payloads
- Keep upstream images pinned to specific versions

## Contact

For security-related questions that are not vulnerability reports:

- Open a GitHub Discussion with the "security" tag
- Email: <villmow.products@gmail.com>

---

Thank you for helping keep HomericIntelligence secure!
