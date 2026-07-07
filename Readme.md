# I don't have a name yet

An application that turns application and feature specifications into working test code.

## The goal

Testing is where most teams fall behind. Writing unit, integration, and end-to-end tests by
hand is slow, and coverage gaps pile up faster than anyone can close them. This project aims to
change that, to be a future tool of test automation.

The application takes in application and feature specifications and writes the test code for
them. It breaks each specification down into simple, comprehensive test cases, gathers the
right context for each one, and generates accurate tests from that context to get teams
closer to 100% test coverage without the manual grind.

## How it works

Under the hood, the application uses hybrid agentic RAG (and other methods) to pull the context
it needs to generate correct tests. Specifications go in; the system decomposes them into test
cases, retrieves the relevant context for each, and produces unit, integration, and end-to-end
tests as output.

## Where it's headed

The MVP focuses on generating tests from specifications. Beyond that, the plan is to connect the
application directly to Jira and other feature-tracking tools, so that test code is generated
automatically as features are defined, targeting frameworks like Selenium and TypeScript-based
suites for real end-to-end coverage.

## Future concern: prompt injection in generated test code

As the system grows, one important safety issue is that generated test code may be influenced by
untrusted specification text, such as summaries or descriptions pulled from the retrieved corpus.
A compromised or poisoned source could attempt to steer the model into producing harmful or
unexpected test code, such as shell commands or other side effects. Because generated code is
effectively untrusted output, it should be treated as something that requires human review before
execution, and ideally should be run in a sandboxed environment rather than directly in a
developer or CI workflow.

## Status

Early development. The MVP is being built from the ground up, starting with the retrieval
pipeline and its evaluation harness.