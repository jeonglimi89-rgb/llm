---
name: Bug report
about: Report something that broke
title: "[bug] "
labels: [bug, needs-triage]
assignees: []
---

## Summary

<!-- One-line description -->

## Reproduction

1. Start stack: `./deploy.sh local`
2. Request: `curl ...`
3. Observed: ...
4. Expected: ...

## Environment

- Version: <!-- e.g. v0.1.0 or commit sha -->
- Deploy mode: <!-- local / local-tls / pilot-vm / prod-eks -->
- OS: <!-- Linux / Windows WSL / macOS -->
- Python: <!-- 3.12.x -->

## Logs

<details>
<summary>Orchestrator logs</summary>

```
(paste last 50 lines of docker logs llm-orchestrator-1 or /data/logs/dispatch.log)
```
</details>

## Metrics at time of failure

<!-- Optional: paste /metrics snippet or Grafana screenshot -->

## Trace ID (if OTEL enabled)

<!-- Trace ID from response headers or Jaeger -->
