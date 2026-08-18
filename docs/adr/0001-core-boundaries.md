# ADR 0001: Keep execution independent from browser backends

## Status

Accepted

## Decision

The workflow engine communicates with browsers exclusively through the asynchronous
`BrowserAdapter` contract. Steps may depend on that contract, but they may not import Pydoll or
another browser implementation directly.

Step implementations are discovered through `StepRegistry`. The executor resolves expressions,
constructs the registered step, and invokes its `execute` method without branching on step names.

## Consequences

- Browser adapters can be replaced without changing workflow execution.
- New steps do not require edits to the executor.
- Adapter-specific capabilities must be exposed deliberately through the shared contract.

