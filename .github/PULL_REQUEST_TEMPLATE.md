## Summary

<!-- One or two sentences describing what this PR changes and why. -->

## Type of Change

<!-- Check all that apply -->

- [ ] New scrape target or metric
- [ ] Alert rule add / change
- [ ] Dashboard add / change
- [ ] Exporter code change
- [ ] CI / workflow change
- [ ] Documentation
- [ ] Bug fix
- [ ] Security hardening

## Validation Checklist

<!-- All items must be checked before requesting review. -->

- [ ] `just validate` passes (docker compose config + YAML lint)
- [ ] `just test` passes (pytest unit tests)
- [ ] `pixi run ruff check exporter/exporter.py` passes (if exporter changed)
- [ ] `pixi run bandit -ll exporter/exporter.py` shows no HIGH findings (if exporter changed)
- [ ] No credentials or secrets in the diff
- [ ] Existing metric names not renamed without dashboard update in this PR
- [ ] Alert rule changes do not regress existing alerts

## Related Issues

<!-- Link any issues this PR closes or is related to. -->
<!-- Example: Closes #123 -->
