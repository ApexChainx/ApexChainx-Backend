# ApexChainx Architecture Diagrams

This document contains Mermaid diagrams illustrating the architecture, key data flows, and subsystem interactions of the ApexChainx backend.

---

## 1. System Overview

High-level view of the three-repo monorepo and how traffic flows through the system.

```mermaid
graph TD
    User([👤 User])
    FE[apexchainx-fe\nFrontend UI]
    BE[apexchainx-be\nBackend API ← this repo]
    DB[(PostgreSQL)]
    Redis[(Redis)]
    Celery[Celery Workers]
    SorobanAdapter[Soroban Adapter]
    Contract[apexchainx-contracts\nSoroban Smart Contracts]
    Stellar[Stellar Network]

    User -->|HTTP/S| FE
    FE -->|REST API| BE
    BE -->|SQLAlchemy ORM| DB
    BE -->|Cache / Rate limit| Redis
    BE -->|Enqueue tasks| Celery
    Celery -->|Read/Write| DB
    Celery -->|Pub/Sub| Redis
    BE -->|SLA settlement| SorobanAdapter
    SorobanAdapter -->|XDR invoke| Contract
    Contract -->|On-chain tx| Stellar
    Stellar -->|Tx result| SorobanAdapter
    SorobanAdapter -->|Settlement result| BE
    BE -->|Response| FE
    FE -->|UI update| User
```

> The frontend **never** calls contracts directly. All Soroban interactions are brokered exclusively through the backend.

---

## 2. Request Routing and Middleware Stack

Sequence of middleware layers every inbound HTTP request passes through before reaching a route handler.

```mermaid
graph LR
    Request([Inbound Request])
    CORSMiddleware[CORS Middleware]
    SecurityHeaders[Security Headers\nMiddleware]
    CorrelationID[Correlation ID\nMiddleware]
    PayloadSize[Payload Size Guard\nMiddleware]
    ContentType[Content-Type\nMiddleware]
    ETag[ETag Middleware]
    Idempotency[Idempotency\nMiddleware]
    APIVersion[API Version\nMiddleware]
    RouteHandler[FastAPI Route Handler]

    Request --> CORSMiddleware --> SecurityHeaders --> CorrelationID --> PayloadSize
    PayloadSize --> ContentType --> ETag --> Idempotency --> APIVersion --> RouteHandler
```

---

## 3. Outage and SLA Lifecycle

Step-by-step flow from outage creation through SLA settlement on-chain.

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI BE
    participant DB as PostgreSQL
    participant Audit as Audit Log
    participant SLA as SLA Calculator
    participant Adapter as Soroban Adapter
    participant Contract as Soroban Contract

    Client->>API: POST /api/v1/outages (create)
    API->>DB: Insert outage record
    API->>Audit: Emit OUTAGE_CREATED event
    API-->>Client: 201 outage response

    Client->>API: PATCH /api/v1/outages/{id} (resolve, mttr_minutes)
    API->>DB: Update outage → resolved
    API->>Audit: Emit OUTAGE_RESOLVED event

    API->>SLA: compute_sla(outage, policy)
    SLA-->>API: SLA outcome (penalty | reward | compliant)

    API->>DB: Persist SLA result
    API->>Audit: Emit SLA_COMPUTED event

    alt CONTRACT_EXECUTION_MODE = contract
        API->>Adapter: trigger_payment(sla_result)
        Adapter->>Contract: invoke XDR tx
        Contract-->>Adapter: tx hash
        Adapter-->>API: payment confirmed
        API->>DB: Persist payment record
        API->>Audit: Emit PAYMENT_TRIGGERED event
    end

    API-->>Client: Resolved outage + SLA result
```

---

## 4. SLA Computation Sub-diagram

Internal logic of the MTTR-based SLA calculator.

```mermaid
flowchart TD
    Start([Outage resolved\nwith mttr_minutes])
    LoadPolicy[Load SLA Policy\napp/services/sla/config.py]
    Compare{mttr_minutes vs\npolicy thresholds}
    Compliant[Outcome: COMPLIANT\nno penalty / no reward]
    Penalty[Outcome: PENALTY\npenalty_amount computed]
    Reward[Outcome: REWARD\nreward_amount computed]
    PersistSLA[Persist SLA Result\nsla_repository.py]
    EmitAudit[Emit Audit Event\naudit_log.py]
    TriggerPayment{CONTRACT_EXECUTION_MODE?}
    LocalAdapter[Local Adapter\nno on-chain tx]
    SorobanBridge[Soroban Bridge\non-chain settlement]
    Done([SLA Result Returned])

    Start --> LoadPolicy --> Compare
    Compare -->|within threshold| Compliant
    Compare -->|over threshold| Penalty
    Compare -->|under threshold| Reward
    Compliant --> PersistSLA
    Penalty --> PersistSLA
    Reward --> PersistSLA
    PersistSLA --> EmitAudit --> TriggerPayment
    TriggerPayment -->|local| LocalAdapter --> Done
    TriggerPayment -->|contract| SorobanBridge --> Done
```

---

## 5. Webhook Delivery Sub-diagram

Signed, versioned webhook delivery with retry logic and circuit-breaker protection.

```mermaid
sequenceDiagram
    participant API as FastAPI BE
    participant Celery as Celery Worker
    participant Signing as Webhook Signing\nService
    participant Breaker as Circuit Breaker
    participant Consumer as Webhook Consumer

    API->>Celery: Enqueue webhook_deliver task\n(event, endpoint, idempotency_key)
    Celery->>Signing: sign_payload(event, secret, version)
    Signing-->>Celery: X-Apex-Signature-{version}

    loop Retry (up to max_retries)
        Celery->>Breaker: check circuit state
        alt Circuit OPEN
            Breaker-->>Celery: reject (backoff)
        else Circuit CLOSED / HALF-OPEN
            Celery->>Consumer: POST event + signature headers
            alt 2xx response
                Consumer-->>Celery: success
                Celery->>Breaker: record success
            else 4xx / 5xx / timeout
                Consumer-->>Celery: failure
                Celery->>Breaker: record failure
                Celery->>Celery: exponential backoff
            end
        end
    end
```

---

## 6. Authentication and Token Flow

JWT-based authentication with token families and refresh token rotation.

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI BE
    participant AuthStore as Auth Store
    participant TokenFamilyRepo as Token Family\nRepository
    participant RateLimit as Rate Limiter\n(Redis)

    Client->>API: POST /api/v1/auth/login
    API->>RateLimit: Check login rate limit
    RateLimit-->>API: allowed / blocked
    API->>AuthStore: verify credentials
    AuthStore-->>API: user record
    API->>TokenFamilyRepo: Create token family
    API-->>Client: access_token + refresh_token

    Client->>API: POST /api/v1/auth/refresh
    API->>TokenFamilyRepo: Validate refresh token family
    alt family valid + token not revoked
        TokenFamilyRepo-->>API: OK
        API-->>Client: new access_token + rotated refresh_token
    else token reuse detected
        TokenFamilyRepo-->>API: REUSE DETECTED → revoke family
        API-->>Client: 401 Unauthorized
    end
```

---

## 7. Component Dependency Map

Static dependency map between the major application layers.

```mermaid
graph BT
    Routes[API Route Handlers\napp/api/v1/endpoints/]
    Services[Domain Services\napp/services/]
    Repos[Repositories\napp/repositories/]
    Models[ORM Models\napp/models/orm/]
    DB[(PostgreSQL\nvia SQLAlchemy)]
    Cache[(Redis\ncache + rate limit)]
    Tasks[Celery Tasks\napp/tasks/]
    Config[Settings\napp/core/config.py]

    Routes --> Services
    Routes --> Repos
    Services --> Repos
    Services --> Cache
    Repos --> Models
    Models --> DB
    Tasks --> Services
    Tasks --> Repos
    Config --> Services
    Config --> Routes
```

---

*Diagrams render natively in GitHub Markdown. Use [mermaid.live](https://mermaid.live) for local preview.*
