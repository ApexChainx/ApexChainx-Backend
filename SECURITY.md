# Security Policy

## Supported Versions

Only the latest release on the `main` branch receives security fixes. Older branches and forks are not officially supported.

| Version / Branch | Supported |
|------------------|-----------|
| `main` (latest)  | ✅ Yes     |
| Any prior tag    | ❌ No      |

If you are running a pinned tag that is behind `main`, upgrade to `main` before reporting; the issue may already be fixed.

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

We use GitHub's [Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) feature so that reports stay confidential until a patch is ready.

### How to report

1. Go to the [Security tab](https://github.com/ApexChainx/ApexChainx-Backend/security) of this repository.
2. Click **"Report a vulnerability"**.
3. Fill in the form with as much detail as possible (see the template below).
4. Submit. You will receive an acknowledgement within **3 business days**.

Alternatively, email **security@apexchainx.com** with the subject line `[SECURITY] <short description>`. Use the same information template below.

### Information to include

```
- Component affected (e.g. auth endpoint, payment service, webhook delivery)
- Description of the vulnerability and potential impact
- Steps to reproduce or a proof-of-concept
- Affected versions / branches
- Suggested remediation (optional)
```

> **PGP**: If you need to encrypt your report, request our public key by emailing security@apexchainx.com with the subject `[SECURITY] PGP key request`. We will respond with our key within 1 business day.

---

## Disclosure Timeline

We follow a **90-day coordinated disclosure** policy aligned with industry best practices (Project Zero / CERT/CC model).

| Milestone | Target |
|-----------|--------|
| Acknowledgement | ≤ 3 business days after report |
| Initial triage and severity rating | ≤ 7 business days after report |
| Status update to reporter | Every 14 days until resolved |
| Patch and advisory published | ≤ 90 days after report |
| Public disclosure (if no patch) | 90 days after report, with notice to reporter |

We may request a **short extension** (up to 14 days) for complex issues. We will communicate this to the reporter before the deadline. If a vulnerability is being actively exploited in the wild, we will accelerate the timeline and coordinate with the reporter.

---

## Severity Rating

We rate findings using [CVSS v3.1](https://www.first.org/cvss/calculator/3.1):

| Severity | CVSS Score | Expected response |
|----------|-----------|-------------------|
| Critical | 9.0 – 10.0 | Patch within 7 days |
| High     | 7.0 – 8.9  | Patch within 30 days |
| Medium   | 4.0 – 6.9  | Patch within 60 days |
| Low      | 0.1 – 3.9  | Patch within 90 days |
| Informational | 0.0  | Addressed at maintainer discretion |

---

## Security Update Process

When a vulnerability is confirmed:

1. **A private fork or branch** is created to develop the fix without public exposure.
2. **A GitHub Security Advisory** is drafted in the repository Security tab.
3. The fix is reviewed, tested, and merged into `main`.
4. A **new release tag** is created, referencing the advisory.
5. The advisory is published simultaneously with (or just after) the release.
6. The original reporter is credited in the advisory (unless they request anonymity).
7. This `SECURITY.md` and the `CHANGELOG` are updated if process changes are needed.

Downstream users and integrators are notified via:
- The GitHub Security Advisory (watch the repo to receive notifications)
- Release notes on the tagged release

---

## Scope

The following are **in scope** for vulnerability reports:

- All code in this repository (`ApexChainx-Backend`)
- Authentication and authorisation logic
- Input validation and injection flaws
- Secrets handling and environment variable exposure
- Webhook signature verification
- Stellar / Soroban payment integration paths
- Dependencies listed in `requirements.txt` with a direct exploit path through this codebase

The following are **out of scope**:

- Vulnerabilities in upstream dependencies that do not have a direct exploit path through this codebase (report those upstream)
- Denial-of-service attacks that require overwhelming infrastructure resources (network flooding, etc.)
- Social engineering of maintainers
- Physical attacks
- Vulnerabilities in the frontend (`apexchainx-fe`) or smart contracts (`apexchainx-contracts`) repos — report those in the respective repos

---

## HTTP Security Headers

This repository ships security headers middleware. See the configuration flags below; these are separate from the vulnerability disclosure process.

| Header | Default value |
|--------|--------------|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` (disabled in `local` env) |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `no-referrer` |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'` |
| `Permissions-Policy` | `interest-cohort=()` |

Configuration flags:

| Flag | Type | Description |
|------|------|-------------|
| `SECURITY_HEADERS_ENABLED` | bool | Enable / disable the headers middleware |
| `ENVIRONMENT` | string | Set to `local` to suppress HSTS during development |
| `SECURITY_CSP_SWAGGER_PERMISSIVE` | bool | Serve a relaxed CSP for the Swagger UI only |

---

## Bug Bounty

There is currently no paid bug bounty programme. We do publicly credit all reporters who responsibly disclose valid vulnerabilities (unless anonymity is requested).

---

## Contact

| Channel | Address |
|---------|---------|
| Private vulnerability report | [GitHub Security tab](https://github.com/ApexChainx/ApexChainx-Backend/security/advisories/new) |
| Email | security@apexchainx.com |
