# scripts/build_secondary_response_reclass.py
"""Issue #175: apply the regulator-and-judgment reaction rule to the seed library.

Every entry that fires a ``fines`` secondary share (FAIR's fines-and-judgments
form: a regulator's, court's or contractual counterparty's reaction) splits its
``response`` budget into a primary part (internal IR, forensics, recovery) and a
secondary part (the response that reaction forces), per the FAIR stakeholder
test (Jones & Freund, 2nd ed. (2026), Ch. 3; docs/reference/loss-magnitude-
forms.md section 2; loss-form-share-rubric.md section 4). One data_disclosure
entry with no fines share but a notification-bearing third-party-data breach is
renumbered explicitly (EXTRA_RENUMBERED). The split is WITHIN the existing
budget: sum(shares) and the inherent PL+SL mean are unchanged; PL falls, SL
rises.

Fractions (secondary share of the response budget): 3/7 where the threat type
resolves to the rubric's data_disclosure default (data_disclosure itself, and
social_engineering phishing-to-breach entries), 1/3 for every other threat type.
The basis string has two variants: personal-data obligation (notification,
credit monitoring, third-party legal defense) or proceeding (regulatory-
investigation cooperation, counterparty claims handling, third-party legal defense),
selected by PROCEEDING_BASIS_SLUGS. Espionage data_disclosure entries with no
fines share carry no secondary response (NO_SECONDARY_RESPONSE_ALLOWLIST,
guarded in tests/integration/test_loss_form_stakeholder_test.py).

Also appends the per-entry stakeholder-test justification to the five
competitive_advantage rows (share None; no numeric effect).

Nodes are re-derived exactly as tests/integration/test_library_loss_
differentiation.py::test_loss_params_reconstruct_from_envelope_and_shares pins
them: mu = round(mu_sector + ln sum, 10) FIRST, sigma = 1.7, then the PERT
collapse for capped entries / native lognormal for catastrophic. Idempotent.

Usage:
    uv run python scripts/build_secondary_response_reclass.py          # rewrite both seed files
    uv run python scripts/build_secondary_response_reclass.py --check  # exit 1 if a rewrite would change anything
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_FILES = (
    ROOT / "data" / "seed_library_entries.json",
    ROOT / "data" / "seed_library_entries_extension.json",
)
ENVELOPES = ROOT / "data" / "loss_form_envelopes.json"

Z_0_95 = 1.6448536269514722  # scipy.stats.norm.ppf(0.95), inlined like every builder
SIGMA = 1.7  # WITHIN_SCENARIO_SIGMA_DEFAULT (services/calibration.py)


class ReclassError(ValueError):
    """A rule precondition failed for a named entry (never silently skipped; survives -O)."""


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ReclassError(msg)


# Selected by threat_event_type. social_engineering has no rubric default of its own: §3
# routes phishing→breach entries to the data_disclosure default (BEC entries are
# beyond-envelope and never fire fines), so it takes 3/7 too.
# CALIBRATION (fair-departures-register.md B5): conventions of the same grade as the
# shares, not identifiable from the corpus; 1/3 is the conservative side of FAIR-CAM
# §3.3.3's "predominantly secondary" reading (less control credit, residual higher).
SECONDARY_FRACTION: dict[str, float] = {"data_disclosure": 3 / 7, "social_engineering": 3 / 7}
DEFAULT_SECONDARY_FRACTION = 1 / 3

_BASIS_PREFIX = (
    "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md "
    "§4 regulator-and-judgment reaction rule): "
)
SECONDARY_RESPONSE_BASIS_DATA = _BASIS_PREFIX + (
    "notification, credit monitoring and third-party legal defense forced by the "
    "regulator/customer reaction to a personal-data breach"
)
SECONDARY_RESPONSE_BASIS_PROCEEDING = _BASIS_PREFIX + (
    "regulatory-investigation cooperation, counterparty claims handling and third-party "
    "legal defense forced by the regulator/counterparty reaction"
)

# Copied verbatim from scripts/build_d_iii_a_recalibration.py:40-59 (18 keys; scripts are
# not a package). tests/unit/test_build_secondary_response_reclass.py asserts equality.
IND2SEC = {
    "agriculture": "food_agriculture",
    "education": "education",
    "education_services": "education",
    "finance_and_insurance": "financial_services",
    "financial": "financial_services",
    "health_care_and_social_assistance": "healthcare",
    "healthcare": "healthcare",
    "information": "technology_saas",
    "manufacturing": "manufacturing",
    "professional": "professional_services",
    "professional_and_business_services": "professional_services",
    "public": "government_public",
    "retail": "retail_ecommerce",
    "retail_trade": "retail_ecommerce",
    "transportation": "transportation_logistics",
    "transportation_and_warehousing": "transportation_logistics",
    "utilities": "energy_utilities",
    "hospitality": "hospitality",
}

EXTRA_RENUMBERED: frozenset[str] = frozenset({"telecom-lawful-intercept-nationstate-compromise"})

RENUMBERED_SLUGS: frozenset[str] = frozenset(
    {
        "ransomware-on-ehr",
        "ransomware-on-historian",
        "unauthorized-plc-modification",
        "safety-system-bypass",
        "insider-data-theft-financial",
        "ransomware-healthcare-small-practice",
        "data-breach-notification-regulatory-tail",
        "generative-ai-prompt-injection",
        "chemical-process-safety-attack",
        "accidental-insider-exposure",
        "education-student-records-insider",
        "healthcare-staff-credential-phish",
        "food-recall-data-tampering",
        "financial-transaction-tampering",
        "healthcare-record-alteration",
        "tolling-plant-ransomware-customer-liability",
        "pipeline-nomination-scada-curtailment-shipper-penalty",
        "energy-settlement-platform-tampering-offtaker-liability",
        "law-enforcement-records-extortion-breach",
        "law-firm-privileged-data-ransomware-extortion",
        "k12-edtech-vendor-breach",
        "judiciary-court-system-ransomware",
        "edge-ransomware-perimeter-gateway",
        "telecom-lawful-intercept-nationstate-compromise",
    }
)

# Assigned from each entry's description (spec section 3.2): a regulator, court or
# counterparty proceeding with no data-subject notification named.
PROCEEDING_BASIS_SLUGS: frozenset[str] = frozenset(
    {
        "ransomware-on-historian",
        "unauthorized-plc-modification",
        "safety-system-bypass",
        "chemical-process-safety-attack",
        "food-recall-data-tampering",
        "tolling-plant-ransomware-customer-liability",
        "pipeline-nomination-scada-curtailment-shipper-penalty",
        "energy-settlement-platform-tampering-offtaker-liability",
        "financial-transaction-tampering",  # funds diversion: penalties, audit, rollback; no data subjects
        "healthcare-record-alteration",  # OCR integrity investigation, patient notification where reportable, malpractice defense
        "edge-ransomware-perimeter-gateway",  # no exfiltration/regulated data named; regulatory investigation
    }
)

NO_SECONDARY_RESPONSE_ALLOWLIST: dict[str, str] = {
    "crop-science-ip-exfiltration": (
        "trade-secret exfiltration; the DTSA misappropriation litigation the entry names is "
        "the org acting as plaintiff, a primary response cost; no fines share, no notification "
        "duty for the org's own IP"
    ),
    "education-research-ip-exfiltration": (
        "nation-state exfiltration of the institution's own pre-publication research; no fines "
        "share today. The description's grant-funding penalties are a funder reaction the "
        "profile does not yet carry -- tracked as a curation gap in the #175 follow-up issue"
    ),
    "edge-espionage-nationstate": (
        "espionage of the org's own data/secrets with no ransom and no fines share; response is "
        "IR plus rip-and-replace, primary"
    ),
    "email-client-zeroclick-espionage": (
        "nation-state espionage with no fines share; the entry models no data-subject "
        "exposure. If curation later names one (mailbox compromise can expose "
        "correspondents' data), it must be renumbered, not allowlisted"
    ),
}

CA_JUSTIFICATION: dict[str, str] = {
    "insider-ip-theft-manufacturing": (
        "stakeholder test: primary — the compromised asset is the differentiator itself "
        "(process/design IP); market position erodes as a result of the threat action, not a "
        "stakeholder reaction; customer defection is the reputation form (secondary)."
    ),
    "ip-theft-by-competitor": (
        "stakeholder test: primary — the competitor is the threat agent, not a reacting "
        "secondary stakeholder; the compromised trade secrets are the differentiator itself."
    ),
    "crop-science-ip-exfiltration": (
        "stakeholder test: primary — competitor-sponsored actors are the threat agent, not a "
        "reacting secondary stakeholder; seed genetics and formulation IP are the "
        "differentiator itself."
    ),
    "education-research-ip-exfiltration": (
        "stakeholder test: primary — the compromised asset is the differentiator itself "
        "(pre-publication research and grant-funded IP); priority of publication is lost as a "
        "result of the threat action; partner/funder reputational reactions are carried "
        "as reputation (secondary); the grant-funding penalty itself is not yet carried (tracked in #181)."
    ),
    "competitor-trade-secret-recruit": (
        "stakeholder test: primary — the recruiting rival is the threat agent, not a reacting "
        "secondary stakeholder; the trade secrets carried out are the differentiator itself."
    ),
}
JUSTIFIED_SLUGS: frozenset[str] = frozenset(CA_JUSTIFICATION)

# Never touched by this rule (asserted): beyond-envelope BEC tier and the T0 wiper override.
_UNTOUCHABLE = frozenset({"destructive-wiper-nationstate"})


def sector(e: dict) -> str:
    ind = (e.get("calibration_anchor") or {}).get("industry")
    return IND2SEC.get(ind) or IND2SEC.get(
        (e.get("applicable_industries") or [None])[0], "technology_saas"
    )


def _share(profile: list[dict], form: str, kind: str) -> float:
    return sum((p.get("share") or 0.0) for p in profile if p["form"] == form and p["kind"] == kind)


def _sum(profile: list[dict], kind: str) -> float:
    return sum((p.get("share") or 0.0) for p in profile if p["kind"] == kind)


def _node(mu_s: float, share_sum: float, shape: str) -> dict:
    mu = round(mu_s + math.log(share_sum), 10)  # round FIRST (builder convention)
    if shape == "catastrophic":
        return {"distribution": "lognormal", "mean": mu, "sigma": SIGMA}
    low = round(math.exp(mu - Z_0_95 * SIGMA), 10)
    high = round(math.exp(mu + Z_0_95 * SIGMA), 10)
    return {"distribution": "PERT", "low": low, "mode": low, "high": high}


def _split(total: float, fraction: float, slug: str) -> tuple[float, float]:
    scaled = total * fraction * 100
    _check(
        abs(scaled - math.floor(scaled) - 0.5) > 1e-9,
        f"{slug}: rounding tie on the 2-dp grid: {total} x {fraction}",
    )
    s = round(total * fraction, 2)
    p = round(total - s, 2)
    _check(abs(p + s - total) < 1e-9, f"{slug}: split does not preserve the budget")
    _check(s > 0 and p > 0, f"{slug}: degenerate split {total} -> {p}/{s}")
    return p, s


def _recompute_roles(profile: list[dict]) -> None:
    shares = [p["share"] for p in profile if p.get("share") is not None]
    dom = max(shares) if shares else None
    for p in profile:
        if p.get("share") is None:
            continue  # provenance_only rows (beyond-envelope) are not composed
        p["composition_role"] = "dominant" if p["share"] == dom else "contributing"


def _apply_rule(e: dict, env: dict[str, dict]) -> bool:
    profile = e["loss_form_profile"]
    slug = e["slug"]
    fires = _share(profile, "fines", "secondary") > 0 or slug in EXTRA_RENUMBERED
    if not fires or _share(profile, "response", "secondary") > 0:
        return False
    _check(
        slug not in _UNTOUCHABLE and e.get("loss_tier") != "vendor",
        f"{slug}: beyond-envelope/override entry must not be renumbered",
    )
    primary_rows = [
        p for p in profile if p["form"] == "response" and p["kind"] == "primary" and p.get("share")
    ]
    _check(
        len(primary_rows) == 1,
        f"{slug}: fires fines but has no single response/primary share to split",
    )
    primary_row = primary_rows[0]
    total = primary_row["share"]
    fraction = SECONDARY_FRACTION.get(e["threat_event_type"], DEFAULT_SECONDARY_FRACTION)
    p, s = _split(total, fraction, slug)
    primary_row["share"] = p
    profile.append(
        {
            "form": "response",
            "kind": "secondary",
            "magnitude_basis": (
                SECONDARY_RESPONSE_BASIS_PROCEEDING
                if slug in PROCEEDING_BASIS_SLUGS
                else SECONDARY_RESPONSE_BASIS_DATA
            ),
            "citations": [],
            "verified": False,
            "composition_role": "contributing",
            "share": s,
        }
    )
    _recompute_roles(profile)
    sp, ss = _sum(profile, "primary"), _sum(profile, "secondary")
    _check(sp + ss <= 1.0 + 1e-9, f"{slug}: coherence bound breached {sp + ss}")
    mu_s = env[sector(e)]["mean"]
    shape = e["loss_shape"]
    e["primary_loss"] = _node(mu_s, sp, shape)
    e["secondary_loss"] = _node(mu_s, ss, shape)
    return True


def _apply_justification(e: dict) -> bool:
    text = CA_JUSTIFICATION.get(e["slug"])
    if text is None:
        return False
    row = next(p for p in e["loss_form_profile"] if p["form"] == "competitive_advantage")
    if row["magnitude_basis"].endswith(text):
        return False
    row["magnitude_basis"] = f"{row['magnitude_basis']} — {text}"
    _check(
        len(row["magnitude_basis"]) <= 512,
        f"{e['slug']}: magnitude_basis exceeds the 512-char DTO cap",
    )
    return True


def reclassify(rows: list[dict], env: dict[str, dict]) -> list[str]:
    """Apply the rule + CA justifications in place; return changed slugs."""
    changed: list[str] = []
    for e in rows:
        touched = _apply_rule(e, env)
        touched = _apply_justification(e) or touched
        if touched:
            changed.append(e["slug"])
    return changed


def seeded_pair(node: dict) -> tuple[float, float]:
    """The (low, high) pair the wizard seeds into scenario_sme_estimates for this node:
    routes/scenario_wizard_seeding.py::_library_seed_rows ->
    services/wizard_helpers._quantile_pair(run_executor._dict_to_fair_distribution(node)).
    Beta-PERT 5/95 quantiles for a capped node, exp(mu +/- 1.645 sigma) for catastrophic --
    NOT the PERT bounds. Imported from the app (this script runs inside the venv)."""
    # Same guard as scripts/build_loss_pert_conversion.py:20-26 so the script also
    # runs outside the editable install.
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from idraa.services.run_executor import _dict_to_fair_distribution
    from idraa.services.wizard_helpers import _quantile_pair

    q = _quantile_pair(_dict_to_fair_distribution(node))
    return (round(float(q["low"]), 10), round(float(q["high"]), 10))


def old_new_pairs(before: list[dict], after: list[dict]) -> dict[str, dict[str, tuple]]:
    """slug -> {"pl": (old_seeded_pair, new_seeded_pair), "sl": ...} for entries whose nodes changed."""
    prev = {e["slug"]: e for e in before}
    out: dict[str, dict[str, tuple]] = {}
    for e in after:
        b = prev[e["slug"]]
        for fs, key in (("pl", "primary_loss"), ("sl", "secondary_loss")):
            if b.get(key) == e.get(key):
                continue
            out.setdefault(e["slug"], {})[fs] = (seeded_pair(b[key]), seeded_pair(e[key]))
    return out


def _load() -> tuple[list[list[dict]], dict[str, dict]]:
    files = [json.loads(p.read_text(encoding="utf-8")) for p in SEED_FILES]
    env = {r["sector"]: r for r in json.loads(ENVELOPES.read_text(encoding="utf-8"))}
    return files, env


def main(argv: list[str]) -> int:
    check = "--check" in argv
    files, env = _load()
    before = copy.deepcopy(files)
    changed_all: list[str] = []
    for rows in files:
        changed_all += reclassify(rows, env)
    if check:
        print(f"{len(changed_all)} entries would change")
        return 1 if changed_all else 0
    flat_before = [e for rows in before for e in rows]
    flat_after = [e for rows in files for e in rows]
    pairs = old_new_pairs(flat_before, flat_after)
    if changed_all:  # refuse BEFORE writing anything
        expected = RENUMBERED_SLUGS | JUSTIFIED_SLUGS
        _check(
            set(changed_all) == expected,
            f"changed set != 24 renumbered + 5 justified: {sorted(set(changed_all) ^ expected)}",
        )
        _check(
            set(pairs) == set(RENUMBERED_SLUGS),
            f"renumbered set != RENUMBERED_SLUGS: {sorted(set(pairs) ^ RENUMBERED_SLUGS)}",
        )
    for path, rows in zip(SEED_FILES, files, strict=True):
        path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"changed {len(changed_all)} entries; {len(pairs)} renumbered")
    print("slug | fieldset | old seeded (p5, p95) | new seeded (p5, p95)")
    for slug in sorted(pairs):
        for fs, (o, n) in pairs[slug].items():
            print(f"{slug} | {fs} | {o} | {n}")
    print(
        "\nOLD_PAIRS = "
        + json.dumps(
            {s: {fs: list(v[0]) for fs, v in d.items()} for s, d in pairs.items()}, indent=2
        )
    )
    print(
        "\nNEW_PAIRS = "
        + json.dumps(
            {s: {fs: list(v[1]) for fs, v in d.items()} for s, d in pairs.items()}, indent=2
        )
    )
    prev = {e["slug"]: e for e in flat_before}
    old_nodes = {s: {"pl": prev[s]["primary_loss"], "sl": prev[s]["secondary_loss"]} for s in pairs}
    print("\nOLD_NODES = " + json.dumps(old_nodes, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
