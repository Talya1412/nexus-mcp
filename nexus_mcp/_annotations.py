"""Tool annotation presets: readOnly / destructive / idempotent combinations."""

_READ_ONLY_ANNOTATIONS = {
    "title": "",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

# One-shot writes: create/send/upload/flow-start/telemetry/raw escape hatch.
_MUTATING_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

# Toggles and state setters: repeating the call converges to the same state.
_IDEMPOTENT_MUTATION_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

# Content removal / hard-to-undo publishing decisions.
_DESTRUCTIVE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}

# Removals that converge (untrack, close report): destructive yet repeatable.
_DESTRUCTIVE_IDEMPOTENT_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

