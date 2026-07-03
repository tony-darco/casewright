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

## Status

Early development. The MVP is being built from the ground up, starting with the retrieval
pipeline and its evaluation harness.