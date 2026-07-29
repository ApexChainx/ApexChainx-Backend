# ADR-0002: MTTR-based SLA computation

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

SLA (Service Level Agreement) compliance can be measured by availability percentage, MTTR (Mean Time To Resolution), or MTBF (Mean Time Between Failures). We need a canonical metric.

## Decision

We use MTTR as the primary SLA metric, supplemented by availability percentage. MTTR directly answers the question "how fast do we recover?" which is most actionable for operations teams.

## Consequences

- SLA dashboards display MTTR prominently alongside availability
- SLA violation detection uses configurable MTTR thresholds
- Quarterly reports may change shape as we add more metrics
