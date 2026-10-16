# Task authoring surface. The engine is the ale/ submodule of the sibling run repository.
ENGINE := "../agentic-forecasting/ale"

_default:
    @just --list

# Structural checks: manifests, layout, references, visibility rules.
lint:
    @uv run --project {{ ENGINE }} ale lint .

# Everything CI runs. Validation needs a staged run day; see the run repository's README.
check: lint
