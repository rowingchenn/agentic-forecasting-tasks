# Task authoring surface.
#
# Run these recipes with a sibling ALE engine checkout.
ENGINE := "../agentic-forecasting-ale"

_default:
    @just --list

# Scaffold a new task folder that already passes lint.
new-task path:
    @uv run --project {{ ENGINE }} ale new-task tasks/{{ path }}

# Structural checks: manifests, layout, references, visibility rules.
lint:
    @uv run --project {{ ENGINE }} ale lint .

# Validate credential-free reference Tasks.
validate:
    @uv run --project {{ ENGINE }} ale validate tasks/example_task
    @uv run --project {{ ENGINE }} ale validate tasks/demo/resource_injection
    @uv run --project {{ ENGINE }} ale validate tasks/demo/verification_deterministic
    @uv run --project {{ ENGINE }} ale validate tasks/demo/verification_domain_a
    @uv run --project {{ ENGINE }} ale validate tasks/demo/verification_domain_b
    @uv run --project {{ ENGINE }} ale validate tasks/demo/verification_separate

# Everything CI runs.
check: lint validate

# Run one task with a real agent, from this checkout.
run path *args:
    @uv run --project {{ ENGINE }} ale run tasks/{{ path }} {{ args }}
