This repository enables a set of HTTP security headers to protect clients and reduce risk.

Headers applied by default (unless disabled via configuration):

- Strict-Transport-Security: max-age=63072000; includeSubDomains
  - Enforces HTTPS and tells browsers to access the site via TLS for the specified max-age. Disabled in the `local` environment to avoid development friction when running locally without TLS.

- X-Content-Type-Options: nosniff
  - Prevents some browsers from MIME-type sniffing a response away from the declared Content-Type.

- X-Frame-Options: DENY
  - Prevents the site from being framed by other sites to mitigate clickjacking.

- Referrer-Policy: no-referrer
  - Prevents the Referer header from being sent, avoiding accidental leakage of sensitive URLs.

- Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
  - Very restrictive default policy that prevents loading external scripts, styles, frames, etc. There is an opt-in flag to relax this policy for the built-in OpenAPI/Swagger UI only.

- Permissions-Policy: interest-cohort=()
  - Opts out of the Federated Learning of Cohorts (FLoC) tracking mechanism.

Configuration flags

- SECURITY_HEADERS_ENABLED (bool): Enable or disable the middleware adding the headers.
- ENVIRONMENT (string): When set to "local" HSTS is not emitted.
- SECURITY_CSP_SWAGGER_PERMISSIVE (bool): When true, the middleware serves a more permissive CSP for Swagger/OpenAPI docs only.

Why these defaults?

These headers provide layered browser-side protections that reduce the attack surface for XSS, clickjacking, mixed-content, and information leakage. The defaults are intentionally conservative; if an endpoint requires exceptions (e.g. embedded third-party UIs), prefer targeted exceptions rather than weakening the global defaults.
