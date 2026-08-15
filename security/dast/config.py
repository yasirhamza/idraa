"""SSOT for the DAST harness: excluded surface, example budget, seed identity.

Every other module in ``security/dast/`` imports its constants from here
rather than re-declaring them — this is the single place a reviewer needs to
check to answer "what does the fuzzer touch, and as whom."
"""

from __future__ import annotations

# Heavy (Monte-Carlo/report) routes + the whole session/account-mutating
# surface. idraa is a form-POST app, so destructive/session-killing ops are
# mostly POST (not the DELETE verb) — exclude by PATH, not just method.
#
# `--exclude-method DELETE` is kept as belt-and-suspenders in the orchestrator
# (Task 5), but this PATH regex is the real protection: it is matched with
# ``re.search`` against the OpenAPI path *template* (e.g.
# ``/scenarios/{scenario_id}/delete``), not the concrete request URL, so it
# correctly drops parameterized destructive routes too.
EXCLUDED_PATH_REGEX = (
    r"^/(runs|reports|analyses)(/|$)"  # heavy: budget
    r"|^/(account|users|mfa|settings|auth)(/|$)"  # session/account mutation
    r"|/(delete|deactivate|cancel|purge-samples)$"
    r"|^/(logout|setup)$"
)

# CI-budget floor; raise toward the ~5-min budget (§7 of the design doc: this
# is the real coverage lever under fixed-corpus, deterministic generation).
MAX_EXAMPLES = 25

# Seeded identity for the ephemeral admin (seed.py::seed) — a non-routable
# local email and a fixed, recognizable org name. The password
# is NEVER a constant: it is generated at runtime (secrets.token_urlsafe)
# by seed.py and never committed or logged.
SEED_EMAIL = "dast-admin@ci.local"
SEED_ORG = "DAST"
