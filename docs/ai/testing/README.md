---
phase: testing
title: Testing
description: Test plans, coverage reports, and quality criteria
---

# Testing

## Test Strategy
**What is the overall approach to testing?**

- Unit tests: cover individual functions and modules
- Integration tests: cross-component and end-to-end flows
- Coverage target: 100%

## Test Files
**Links to feature test files**

| Feature | Test File | Coverage |
|---------|-----------|----------|
| — | — | — |

## Running Tests
```bash
pytest tests/ -v --cov=. --cov-report=term-missing
```

## Quality Gates
- All tests pass
- Coverage >= 100% for changed modules
- No regressions in existing tests
