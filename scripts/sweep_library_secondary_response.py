# scripts/sweep_library_secondary_response.py
"""READ-ONLY diagnostic for issue #175: which adopted scenarios still carry the
pre-reclassification library loss ranges?

The wizard seeds one ``scenario_sme_estimates`` row per fieldset from the library
entry via ``_quantile_pair`` (Beta-PERT 5/95 quantiles of a capped node,
exp(mu +/- 1.645 sigma) of a catastrophic one) and re-fits the stored node from
that pair -- so an adopted scenario is NOT a copy of the entry, and the library
migration (b5e2c7a9d413) deliberately leaves scenarios alone. This sweep
classifies every scenario pinned to one of the 24 renumbered entries, per loss
fieldset:

  pristine  -- exactly one SME identity whose pair equals the PRE-change seeded
               pair within a cent  -> a candidate for the (separate) repair migration.
               Read off the SME rows, which only the wizard re-estimation
               replace-all clears (routes/scenarios.py, and that path re-fits the
               node from the same rows, so node and rows stay consistent);
               loss_pinning.refresh_loss_from_library and ScenarioService.update
               both rewrite the loss node and leave the seed rows behind. So a
               fieldset whose stored node is neither the pre-change entry node
               nor the post-change one (copy-current, below) but was hand-edited
               in the expert form or refreshed from an org override still
               classifies pristine. The repair PR MUST re-verify each pristine
               candidate's stored node against the current entry/override before
               rewriting it (over-report is the safe direction for the gate).
  current   -- one identity, pair equals the POST-change seeded pair
  stale     -- one identity, matches neither (adopted before an earlier
               recalibration; already diverged from the library)
  modified  -- more than one SME identity (analyst rows added)
  pinned    -- the stored node carries an analyst_pin stamp (loss_pinning).
               The app's own library refresh refuses the WHOLE scenario when
               either loss field is pinned (loss_pinning._resolve_refresh, rule
               D23: scenario-level, unlike pin/unpin's per-field scope), so the
               sweep applies the same rule: a scenario with any pinned field is
               classed ``pinned`` and is never a repair candidate, whatever its
               other side looks like (pinned wins over copy-stale too). The
               per-FIELD skip-guard both prior recalibration migrations use
               (loss_pinning module docstring; c4e4d441087c, b3f8a2d94c1e) would
               instead repair the unpinned side, so this is the conservative
               reading, not the only one: ``pinned_stale_side > 0`` means a
               pinned scenario still carries pre-change values on its other
               fieldset (node or surviving seed rows; a pinned scenario whose
               other side is ``modified`` is NOT counted here even when its
               pool still holds the seed row -- no ``modified`` fieldset is a
               repair candidate anywhere in this sweep; the printed line names
               it), and the repair PR must state which precedent it follows
               (D23 scenario-level skip, or the field-level skip) before
               treating the gate as clean.
  none      -- no SME rows for the fieldset

A second adoption path bypasses SME rows: the library loss-refresh action
(``services/loss_pinning.refresh_loss_from_library``) copies the entry's PL/SL
dicts into the scenario verbatim. So the sweep first compares the stored nodes
(sidecar ignored) with the PRE-change entry nodes:

  copy-stale -- primary_loss or secondary_loss equals the pre-change entry node
                -> a candidate for the (separate) repair migration (new node
                written verbatim -- preserving the scenario's minted ``max``,
                see b5e2c7a9d413)
  copy-current -- equals the POST-change entry node (refreshed after
                b5e2c7a9d413 shipped); treated as current for today's ALE.
                Its SME rows are still read: pre-change seed rows survive the
                refresh, and a later re-estimation rehydrates them
                (wizard_state.load_sme_rows) and pools them into the re-fit, so
                ``copy_current_stale_rows`` counts scenarios in which ANY
                surviving SME identity on a copy-current fieldset still carries
                the pre-change seeded pair -- alone (pristine rows) or pooled
                with analyst rows (modified rows); the fieldset is printed as
                ``copy-current*``. Scoped to scenarios that reach the class
                branch: a pinned or copy-stale scenario still has its other
                side's rows read and printed as ``copy-current*``, but it
                short-circuits before this counter (a pinned one is counted in
                ``pinned_stale_side`` instead, which covers pristine,
                copy-stale and copy-current* sides; a copy-stale one is already
                in the gate) -- read the printed lines, not just the SUMMARY,
                when either counter is > 0. The repair PR must sweep rows
                separately before calling a deployment clean.

The check is per fieldset (each refreshed field is taken independently from
the entry or an org override, so one side can be a copy while the other is
authored); the pl/sl columns name the copy-stale fieldset(s). Scenario class:
pinned if either fieldset is pinned (D23, above); else copy-stale if either
fieldset is; else pristine if either fieldset is pristine (e.g. the analyst
added an SL estimate while PL still sits on the library seed); else modified if
either fieldset is modified; else current if pl is current and sl
current-or-none; otherwise stale.

Mean-preservation caveat for the repair PR: #175 moves response share from
primary to secondary, so on an UNCAPPED (PERT) scenario it is mean-preserving
across BOTH fieldsets (Σshares fixed; PL falls, SL rises) and not otherwise. On
one of the four catastrophic entries an adopted scenario carries a capacity
``max`` and samples the truncated lognormal, whose mean is concave in the split,
so even a both-sided repair RAISES its inherent mean (up to ~7.1% as the cap
approaches the D19 floor, ~6.5% at 1.5x, <0.15% past ~100x -- register B5). A
one-sided repair -- rewriting only the pristine/copy-stale side of a mixed
scenario (``modified | pristine``, ``copy-stale | modified``, ...) -- shifts the
inherent PL+SL mean by the transferred share: 3.75% to 22.8% across the 24
entries on an uncapped scenario (SL-only inflates, PL-only deflates), and
asymmetrically on a capped catastrophic one, where one org-minted cap sits on
BOTH fields (loss_pinning._resolve_refresh) just above the pre-change PL p95,
truncating PL hard and SL barely: at the D19 floor PL-only runs -2.8%
(chemical-process-safety-attack) to -13.1% (telecom-lawful-intercept...) and
SL-only +7.0% to +20.3%; as the cap recedes both converge on those four
entries' own uncapped values (PL-only -5.1% to -22.8%, SL-only +5.1% to +22.8%).
Where the other side is ``modified`` the old library seed row is still pooled
there, so the moved share would be counted on both sides (or, with the
orientation reversed, on neither); pooling dilutes the seed row's weight, so the
band above is an upper bound for that class. The repair PR must state, per
mixed class, whether it repairs one side, both, or skips the scenario; this
sweep only names the candidates.
``affected`` = scenarios this sweep classified (pinned to a version-1 renumbered
entry, not soft-deleted, no pinned field); the repair gate is
``pristine + copy_stale``, not ``affected``.
Repair trigger (documented in the migration): any pristine or copy-stale
scenario on a deployment -> land the repair as its own PR. Exit code is 0
always (2 = usage / missing file); the SUMMARY line is the gate. Output carries
scenario ids and library slugs only (never scenario names -- user content).
Soft-deleted scenarios are skipped: a deleted row must not drive the repair gate.
Scenarios whose pin names an entry version other than 1 (or none at all) are
skipped too: the
OLD/NEW tables are derived from the version-1 snapshot only, and the migration's
``WHERE version = 1`` leaves any other entry row untouched. skipped_* > 0 means
the sweep could NOT classify those rows -- they are unclassified, not clean;
investigate them before declaring a deployment clean.

Usage:
    uv run python scripts/sweep_library_secondary_response.py /path/to/idraa.db

Opened with SQLite URI mode=ro (the sweep_run_samples_finite.py idiom); run
against a clean online-backup copy or the live DB, not a raw cp of a WAL database.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

CENT = 0.011  # cents rounding of a seeded pair (client-side) + float slack

# Pre-/post-#175 wizard-seeded (p5, p95) pairs per renumbered slug, 10 dp, as printed
# by scripts/build_secondary_response_reclass.py (old_new_pairs). Values derive from
# the seed library, not from any deployment.
OLD_PAIRS: dict[str, dict[str, list[float]]] = {
    "ransomware-on-ehr": {
        "pl": [44340.7148699309, 3401225.679902088],
        "sl": [15396.0815527962, 1180981.138911413],
    },
    "ransomware-on-historian": {
        "pl": [11299.6734991399, 866759.586364862],
        "sl": [4035.5976781882, 309556.9951244743],
    },
    "unauthorized-plc-modification": {
        "pl": [48818.6165952049, 13109752.891416026],
        "sl": [4881.8616594914, 1310975.2891337974],
    },
    "safety-system-bypass": {
        "pl": [51869.7801315527, 13929112.44690059],
        "sl": [5492.0943666182, 1474847.2001923632],
    },
    "insider-data-theft-financial": {
        "pl": [22112.8639909655, 1696202.713079331],
        "sl": [33169.2959861783, 2544304.069598285],
    },
    "ransomware-healthcare-small-practice": {
        "pl": [38182.2822518168, 2928833.2245679675],
        "sl": [12316.8652424119, 944784.9111425512],
    },
    "data-breach-notification-regulatory-tail": {
        "pl": [16496.1965369039, 1265367.2239298457],
        "sl": [34642.0127264804, 2657271.170174599],
    },
    "generative-ai-prompt-injection": {
        "pl": [16670.8881605296, 1278767.2252172036],
        "sl": [22227.8508817784, 1705022.9670385239],
    },
    "chemical-process-safety-attack": {
        "pl": [53700.4782544931, 14420728.180495238],
        "sl": [6102.3270742776, 1638719.1113939607],
    },
    "accidental-insider-exposure": {
        "pl": [11085.1787177033, 850306.4199924391],
        "sl": [13548.5517665945, 1039263.4022523101],
    },
    "education-student-records-insider": {
        "pl": [5506.1031332959, 422354.4755218892],
        "sl": [7708.5443864507, 591296.2657181032],
    },
    "healthcare-staff-credential-phish": {
        "pl": [16011.924814055, 1228220.384402425],
        "sl": [16627.7680764191, 1275459.6299782405],
    },
    "food-recall-data-tampering": {
        "pl": [53070.8735754575, 4070886.5111710476],
        "sl": [24324.1503899574, 1865822.9843792375],
    },
    "financial-transaction-tampering": {
        "pl": [55282.159973315, 4240506.782383934],
        "sl": [28746.7231863156, 2205063.526854359],
    },
    "healthcare-record-alteration": {
        "pl": [28944.6333180452, 2220244.541060512],
        "sl": [16011.924814055, 1228220.384402425],
    },
    "tolling-plant-ransomware-customer-liability": {
        "pl": [77395.0239687394, 5936709.495805287],
        "sl": [30958.0095866955, 2374683.798260737],
    },
    "pipeline-nomination-scada-curtailment-shipper-penalty": {
        "pl": [9362.5866136005, 718172.2287044213],
        "sl": [3551.3259568408, 272410.1557122341],
    },
    "energy-settlement-platform-tampering-offtaker-liability": {
        "pl": [7748.3475418138, 594349.4306154022],
        "sl": [2421.3586068305, 185734.1970683647],
    },
    "law-enforcement-records-extortion-breach": {
        "pl": [7098.2293407603, 544481.0708724139],
        "sl": [6388.4064064149, 490032.9637645099],
    },
    "telecom-lawful-intercept-nationstate-compromise": {
        "pl": [19716.6187761556, 5294701.448689598],
        "sl": [5257.7650068819, 1411920.3862922627],
    },
    "law-firm-privileged-data-ransomware-extortion": {
        "pl": [36618.9027667302, 2808911.6926822835],
        "sl": [22785.095054743, 1747767.2754382107],
    },
    "k12-edtech-vendor-breach": {
        "pl": [9635.6804829265, 739120.3321371195],
        "sl": [6056.7134465992, 464589.923072063],
    },
    "judiciary-court-system-ransomware": {
        "pl": [10647.3440110533, 816721.6063019349],
        "sl": [5205.3681832441, 399286.1186412998],
    },
    "edge-ransomware-perimeter-gateway": {
        "pl": [56962.7376383297, 4369418.188708463],
        "sl": [16275.0678965902, 1248405.196768058],
    },
}
NEW_PAIRS: dict[str, dict[str, list[float]]] = {
    "ransomware-on-ehr": {
        "pl": [39413.9687754943, 3023311.7156389933],
        "sl": [20322.8276497259, 1558895.1033657426],
    },
    "ransomware-on-historian": {
        "pl": [10008.2822421381, 767701.3479264403],
        "sl": [5326.9889352176, 408615.2335650105],
    },
    "unauthorized-plc-modification": {
        "pl": [45767.4530551474, 12290393.334935257],
        "sl": [7933.0251968187, 2130334.8448814047],
    },
    "safety-system-bypass": {
        "pl": [48818.6165952049, 13109752.891416026],
        "sl": [8543.2579038074, 2294206.755902875],
    },
    "insider-data-theft-financial": {
        "pl": [14373.3615940194, 1102531.7634932692],
        "sl": [40908.7983836865, 3137975.019227471],
    },
    "ransomware-healthcare-small-practice": {
        "pl": [33871.3794139747, 2598158.5054381182],
        "sl": [16627.7680764192, 1275459.6299782405],
    },
    "data-breach-notification-regulatory-tail": {
        "pl": [9072.9080948944, 695951.9731305277],
        "sl": [42065.3011661462, 3226686.420794156],
    },
    "generative-ai-prompt-injection": {
        "pl": [9526.2218063546, 730724.1287214468],
        "sl": [29372.5172375457, 2253066.0636564246],
    },
    "chemical-process-safety-attack": {
        "pl": [50649.3147163745, 13601368.624535143],
        "sl": [9153.4906113416, 2458078.667070871],
    },
    "accidental-insider-exposure": {
        "pl": [7390.1191451958, 566870.9466662525],
        "sl": [17243.6113390109, 1322698.8755715112],
    },
    "education-student-records-insider": {
        "pl": [3578.9670366153, 274530.4090871554],
        "sl": [9635.6804829265, 739120.3321371223],
    },
    "healthcare-staff-credential-phish": {
        "pl": [9237.6489313635, 708588.6833227454],
        "sl": [23402.0439588885, 1795091.3310408948],
    },
    "food-recall-data-tampering": {
        "pl": [45331.3711791993, 3477215.5616377303],
        "sl": [32063.652785859, 2459493.933885187],
    },
    "financial-transaction-tampering": {
        "pl": [47542.6575786954, 3646835.83297632],
        "sl": [36486.2255846381, 2798734.476545996],
    },
    "healthcare-record-alteration": {
        "pl": [24633.7304833472, 1889569.8221718357],
        "sl": [20322.8276497259, 1558895.1033657426],
    },
    "tolling-plant-ransomware-customer-liability": {
        "pl": [68549.8783726045, 5258228.410592822],
        "sl": [39803.1551836543, 3053164.8835363844],
    },
    "pipeline-nomination-scada-curtailment-shipper-penalty": {
        "pl": [8878.3148914434, 681025.3892300746],
        "sl": [4035.5976781882, 309556.9951244754],
    },
    "energy-settlement-platform-tampering-offtaker-liability": {
        "pl": [6779.8040992533, 520055.7518012289],
        "sl": [3389.9020494909, 260027.8758902001],
    },
    "law-enforcement-records-extortion-breach": {
        "pl": [4022.329959788, 308539.2734961964],
        "sl": [9464.3057871902, 725974.7611256187],
    },
    "telecom-lawful-intercept-nationstate-compromise": {
        "pl": [14020.7066848539, 3765121.0300685484],
        "sl": [10953.6770978874, 2941500.804833784],
    },
    "law-firm-privileged-data-ransomware-extortion": {
        "pl": [28481.368818024, 2184709.0942667224],
        "sl": [30922.6290012828, 2371969.8736875965],
    },
    "k12-edtech-vendor-breach": {
        "pl": [6332.0186028144, 485707.6468136664],
        "sl": [9360.3753260211, 718002.6083425701],
    },
    "judiciary-court-system-ransomware": {
        "pl": [8754.4828537612, 671526.6540880101],
        "sl": [7098.2293407602, 544481.0708724061],
    },
    "edge-ransomware-perimeter-gateway": {
        "pl": [50452.7104798787, 3870056.110015429],
        "sl": [22785.095054743, 1747767.2754382107],
    },
}
# Pre-change entry nodes (the dicts the library-refresh path copies verbatim).
OLD_NODES: dict[str, dict[str, dict[str, Any]]] = {
    "ransomware-on-ehr": {
        "pl": {
            "distribution": "PERT",
            "low": 24478.8629170978,
            "mode": 24478.8629170978,
            "high": 6570284.009215002,
        },
        "sl": {
            "distribution": "PERT",
            "low": 8499.6051799546,
            "mode": 8499.6051799546,
            "high": 2281348.6144199492,
        },
    },
    "ransomware-on-historian": {
        "pl": {
            "distribution": "PERT",
            "low": 6238.130336978,
            "mode": 6238.130336978,
            "high": 1674354.243465197,
        },
        "sl": {
            "distribution": "PERT",
            "low": 2227.9036917359,
            "mode": 2227.9036917359,
            "high": 597983.6583691587,
        },
    },
    "unauthorized-plc-modification": {
        "pl": {"distribution": "lognormal", "mean": 13.5923670067, "sigma": 1.7},
        "sl": {"distribution": "lognormal", "mean": 11.2897819137, "sigma": 1.7},
    },
    "safety-system-bypass": {
        "pl": {"distribution": "lognormal", "mean": 13.6529916285, "sigma": 1.7},
        "sl": {"distribution": "lognormal", "mean": 11.4075649493, "sigma": 1.7},
    },
    "insider-data-theft-financial": {
        "pl": {
            "distribution": "PERT",
            "low": 12207.6914620596,
            "mode": 12207.6914620596,
            "high": 3276622.7857167795,
        },
        "sl": {
            "distribution": "PERT",
            "low": 18311.5371929403,
            "mode": 18311.5371929403,
            "high": 4914934.178535162,
        },
    },
    "ransomware-healthcare-small-practice": {
        "pl": {
            "distribution": "PERT",
            "low": 21079.0208467746,
            "mode": 21079.0208467746,
            "high": 5657744.563892181,
        },
        "sl": {
            "distribution": "PERT",
            "low": 6799.6841440603,
            "mode": 6799.6841440603,
            "high": 1825078.8915618842,
        },
    },
    "data-breach-notification-regulatory-tail": {
        "pl": {
            "distribution": "PERT",
            "low": 9106.9378304997,
            "mode": 9106.9378304997,
            "high": 2444360.5980919134,
        },
        "sl": {
            "distribution": "PERT",
            "low": 19124.5694434875,
            "mode": 19124.5694434875,
            "high": 5133157.255842193,
        },
    },
    "generative-ai-prompt-injection": {
        "pl": {
            "distribution": "PERT",
            "low": 9203.3785919994,
            "mode": 9203.3785919994,
            "high": 2470245.917817102,
        },
        "sl": {
            "distribution": "PERT",
            "low": 12271.1714565912,
            "mode": 12271.1714565912,
            "high": 3293661.223915026,
        },
    },
    "chemical-process-safety-attack": {
        "pl": {"distribution": "lognormal", "mean": 13.6876771865, "sigma": 1.7},
        "sl": {"distribution": "lognormal", "mean": 11.512925465, "sigma": 1.7},
    },
    "accidental-insider-exposure": {
        "pl": {
            "distribution": "PERT",
            "low": 6119.7157293962,
            "mode": 6119.7157293962,
            "high": 1642571.0023364294,
        },
        "sl": {
            "distribution": "PERT",
            "low": 7479.652558434,
            "mode": 7479.652558434,
            "high": 2007586.780709387,
        },
    },
    "education-student-records-insider": {
        "pl": {
            "distribution": "PERT",
            "low": 3039.7151738019,
            "mode": 3039.7151738019,
            "high": 815879.0735761295,
        },
        "sl": {
            "distribution": "PERT",
            "low": 4255.6012432324,
            "mode": 4255.6012432324,
            "high": 1142230.702982354,
        },
    },
    "healthcare-staff-credential-phish": {
        "pl": {
            "distribution": "PERT",
            "low": 8839.5893866819,
            "mode": 8839.5893866819,
            "high": 2372602.5588703253,
        },
        "sl": {
            "distribution": "PERT",
            "low": 9179.5735940193,
            "mode": 9179.5735940193,
            "high": 2463856.5034845187,
        },
    },
    "food-recall-data-tampering": {
        "pl": {
            "distribution": "PERT",
            "low": 29298.4595073642,
            "mode": 29298.4595073642,
            "high": 7863894.685296549,
        },
        "sl": {
            "distribution": "PERT",
            "low": 13428.4606082077,
            "mode": 13428.4606082077,
            "high": 3604285.064272953,
        },
    },
    "financial-transaction-tampering": {
        "pl": {
            "distribution": "PERT",
            "low": 30519.2286528861,
            "mode": 30519.2286528861,
            "high": 8191556.963684621,
        },
        "sl": {
            "distribution": "PERT",
            "low": 15869.9988996067,
            "mode": 15869.9988996067,
            "high": 4259609.621144428,
        },
    },
    "healthcare-record-alteration": {
        "pl": {
            "distribution": "PERT",
            "low": 15979.2577376458,
            "mode": 15979.2577376458,
            "high": 4288935.394929958,
        },
        "sl": {
            "distribution": "PERT",
            "low": 8839.5893866819,
            "mode": 8839.5893866819,
            "high": 2372602.5588703253,
        },
    },
    "tolling-plant-ransomware-customer-liability": {
        "pl": {
            "distribution": "PERT",
            "low": 42726.9201174072,
            "mode": 42726.9201174072,
            "high": 11468179.750062102,
        },
        "sl": {
            "distribution": "PERT",
            "low": 17090.7680465211,
            "mode": 17090.7680465211,
            "high": 4587271.899906274,
        },
    },
    "pipeline-nomination-scada-curtailment-shipper-penalty": {
        "pl": {
            "distribution": "PERT",
            "low": 5168.7365649398,
            "mode": 5168.7365649398,
            "high": 1387322.0874466621,
        },
        "sl": {
            "distribution": "PERT",
            "low": 1960.555248747,
            "mode": 1960.555248747,
            "high": 526225.6193700691,
        },
    },
    "energy-settlement-platform-tampering-offtaker-liability": {
        "pl": {
            "distribution": "PERT",
            "low": 4277.5750879631,
            "mode": 4277.5750879631,
            "high": 1148128.624023218,
        },
        "sl": {
            "distribution": "PERT",
            "low": 1336.742214996,
            "mode": 1336.742214996,
            "high": 358790.195009287,
        },
    },
    "law-enforcement-records-extortion-breach": {
        "pl": {
            "distribution": "PERT",
            "low": 3918.6689591336,
            "mode": 3918.6689591336,
            "high": 1051795.914164762,
        },
        "sl": {
            "distribution": "PERT",
            "low": 3526.8020630716,
            "mode": 3526.8020630716,
            "high": 946616.3227083709,
        },
    },
    "telecom-lawful-intercept-nationstate-compromise": {
        "pl": {"distribution": "lognormal", "mean": 12.6857171518, "sigma": 1.7},
        "sl": {"distribution": "lognormal", "mean": 11.3639613118, "sigma": 1.7},
    },
    "law-firm-privileged-data-ransomware-extortion": {
        "pl": {
            "distribution": "PERT",
            "low": 20215.9370598961,
            "mode": 20215.9370598961,
            "high": 5426087.332804897,
        },
        "sl": {
            "distribution": "PERT",
            "low": 12578.8052816517,
            "mode": 12578.8052816517,
            "high": 3376232.1181732104,
        },
    },
    "k12-edtech-vendor-breach": {
        "pl": {
            "distribution": "PERT",
            "low": 5319.5015539649,
            "mode": 5319.5015539649,
            "high": 1427788.3787076408,
        },
        "sl": {
            "distribution": "PERT",
            "low": 3343.6866911676,
            "mode": 3343.6866911676,
            "high": 897466.98092985,
        },
    },
    "judiciary-court-system-ransomware": {
        "pl": {
            "distribution": "PERT",
            "low": 5878.0034386523,
            "mode": 5878.0034386523,
            "high": 1577693.8712342277,
        },
        "sl": {
            "distribution": "PERT",
            "low": 2873.6905700423,
            "mode": 2873.6905700423,
            "high": 771317.0037237805,
        },
    },
    "edge-ransomware-perimeter-gateway": {
        "pl": {
            "distribution": "PERT",
            "low": 31447.0132049419,
            "mode": 31447.0132049419,
            "high": 8440580.29565119,
        },
        "sl": {
            "distribution": "PERT",
            "low": 8984.860915656,
            "mode": 8984.860915656,
            "high": 2411594.370174873,
        },
    },
}
NEW_NODES: dict[str, dict[str, dict[str, Any]]] = {
    "ransomware-on-ehr": {
        "pl": {
            "distribution": "PERT",
            "low": 21758.9892608694,
            "mode": 21758.9892608694,
            "high": 5840252.452964859,
        },
        "sl": {
            "distribution": "PERT",
            "low": 11219.4788375594,
            "mode": 11219.4788375594,
            "high": 3011380.171039504,
        },
    },
    "ransomware-on-historian": {
        "pl": {
            "distribution": "PERT",
            "low": 5525.2011556327,
            "mode": 5525.2011556327,
            "high": 1482999.47278979,
        },
        "sl": {
            "distribution": "PERT",
            "low": 2940.8328730964,
            "mode": 2940.8328730964,
            "high": 789338.4290486504,
        },
    },
    "unauthorized-plc-modification": {
        "pl": {
            "distribution": "lognormal",
            "mean": 13.5278284855,
            "sigma": 1.7,
        },
        "sl": {
            "distribution": "lognormal",
            "mean": 11.7752897295,
            "sigma": 1.7,
        },
    },
    "safety-system-bypass": {
        "pl": {
            "distribution": "lognormal",
            "mean": 13.5923670067,
            "sigma": 1.7,
        },
        "sl": {
            "distribution": "lognormal",
            "mean": 11.8493977016,
            "sigma": 1.7,
        },
    },
    "insider-data-theft-financial": {
        "pl": {
            "distribution": "PERT",
            "low": 7934.999450279,
            "mode": 7934.999450279,
            "high": 2129804.810699881,
        },
        "sl": {
            "distribution": "PERT",
            "low": 22584.2292050312,
            "mode": 22584.2292050312,
            "high": 6061752.153635362,
        },
    },
    "ransomware-healthcare-small-practice": {
        "pl": {
            "distribution": "PERT",
            "low": 18699.1313946984,
            "mode": 18699.1313946984,
            "high": 5018966.951401304,
        },
        "sl": {
            "distribution": "PERT",
            "low": 9179.5735940194,
            "mode": 9179.5735940194,
            "high": 2463856.5034845187,
        },
    },
    "data-breach-notification-regulatory-tail": {
        "pl": {
            "distribution": "PERT",
            "low": 5008.8158065525,
            "mode": 5008.8158065525,
            "high": 1344398.3288908857,
        },
        "sl": {
            "distribution": "PERT",
            "low": 23222.6914661408,
            "mode": 23222.6914661408,
            "high": 6233119.524695968,
        },
    },
    "generative-ai-prompt-injection": {
        "pl": {
            "distribution": "PERT",
            "low": 5259.073481329,
            "mode": 5259.073481329,
            "high": 1411569.095945523,
        },
        "sl": {
            "distribution": "PERT",
            "low": 16215.4765681407,
            "mode": 16215.4765681407,
            "high": 4352338.046022559,
        },
    },
    "chemical-process-safety-attack": {
        "pl": {
            "distribution": "lognormal",
            "mean": 13.6291809798,
            "sigma": 1.7,
        },
        "sl": {
            "distribution": "lognormal",
            "mean": 11.9183905731,
            "sigma": 1.7,
        },
    },
    "accidental-insider-exposure": {
        "pl": {
            "distribution": "PERT",
            "low": 4079.8104862974,
            "mode": 4079.8104862974,
            "high": 1095047.33489989,
        },
        "sl": {
            "distribution": "PERT",
            "low": 9519.5578014825,
            "mode": 9519.5578014825,
            "high": 2555110.4481324335,
        },
    },
    "education-student-records-insider": {
        "pl": {
            "distribution": "PERT",
            "low": 1975.8148629563,
            "mode": 1975.8148629563,
            "high": 530321.3978204805,
        },
        "sl": {
            "distribution": "PERT",
            "low": 5319.5015539649,
            "mode": 5319.5015539649,
            "high": 1427788.378707646,
        },
    },
    "healthcare-staff-credential-phish": {
        "pl": {
            "distribution": "PERT",
            "low": 5099.7631077993,
            "mode": 5099.7631077993,
            "high": 1368809.1686054093,
        },
        "sl": {
            "distribution": "PERT",
            "low": 12919.3998727793,
            "mode": 12919.3998727793,
            "high": 3467649.893716546,
        },
    },
    "food-recall-data-tampering": {
        "pl": {
            "distribution": "PERT",
            "low": 25025.7674959634,
            "mode": 25025.7674959634,
            "high": 6717076.710381544,
        },
        "sl": {
            "distribution": "PERT",
            "low": 17701.1526194117,
            "mode": 17701.1526194117,
            "high": 4751103.039135093,
        },
    },
    "financial-transaction-tampering": {
        "pl": {
            "distribution": "PERT",
            "low": 26246.53664239,
            "mode": 26246.53664239,
            "high": 7044738.989012441,
        },
        "sl": {
            "distribution": "PERT",
            "low": 20142.6909121471,
            "mode": 20142.6909121471,
            "high": 5406427.596365268,
        },
    },
    "healthcare-record-alteration": {
        "pl": {
            "distribution": "PERT",
            "low": 13599.3682873054,
            "mode": 13599.3682873054,
            "high": 3650157.7829049667,
        },
        "sl": {
            "distribution": "PERT",
            "low": 11219.4788375594,
            "mode": 11219.4788375594,
            "high": 3011380.171039504,
        },
    },
    "tolling-plant-ransomware-customer-liability": {
        "pl": {
            "distribution": "PERT",
            "low": 37843.8435327222,
            "mode": 37843.8435327222,
            "high": 10157530.63581261,
        },
        "sl": {
            "distribution": "PERT",
            "low": 21973.8446316611,
            "mode": 21973.8446316611,
            "high": 5897921.014277817,
        },
    },
    "pipeline-nomination-scada-curtailment-shipper-penalty": {
        "pl": {
            "distribution": "PERT",
            "low": 4901.3881215039,
            "mode": 4901.3881215039,
            "high": 1315564.0483275985,
        },
        "sl": {
            "distribution": "PERT",
            "low": 2227.9036917359,
            "mode": 2227.9036917359,
            "high": 597983.6583691608,
        },
    },
    "energy-settlement-platform-tampering-offtaker-liability": {
        "pl": {
            "distribution": "PERT",
            "low": 3742.8782020595,
            "mode": 3742.8782020595,
            "high": 1004612.5460449496,
        },
        "sl": {
            "distribution": "PERT",
            "low": 1871.4391009548,
            "mode": 1871.4391009548,
            "high": 502306.2730023566,
        },
    },
    "law-enforcement-records-extortion-breach": {
        "pl": {
            "distribution": "PERT",
            "low": 2220.5790768555,
            "mode": 2220.5790768555,
            "high": 596017.6846968973,
        },
        "sl": {
            "distribution": "PERT",
            "low": 5224.8919452409,
            "mode": 5224.8919452409,
            "high": 1402394.552147049,
        },
    },
    "telecom-lawful-intercept-nationstate-compromise": {
        "pl": {
            "distribution": "lognormal",
            "mean": 12.3447905648,
            "sigma": 1.7,
        },
        "sl": {
            "distribution": "lognormal",
            "mean": 12.0979304869,
            "sigma": 1.7,
        },
    },
    "law-firm-privileged-data-ransomware-extortion": {
        "pl": {
            "distribution": "PERT",
            "low": 15723.5066018411,
            "mode": 15723.5066018411,
            "high": 4220290.147656551,
        },
        "sl": {
            "distribution": "PERT",
            "low": 17071.2357385107,
            "mode": 17071.2357385107,
            "high": 4582029.303000551,
        },
    },
    "k12-edtech-vendor-breach": {
        "pl": {
            "distribution": "PERT",
            "low": 3495.6724496095,
            "mode": 3495.6724496095,
            "high": 938260.9345420286,
        },
        "sl": {
            "distribution": "PERT",
            "low": 5167.515795142,
            "mode": 5167.515795142,
            "high": 1386994.4249931846,
        },
    },
    "judiciary-court-system-ransomware": {
        "pl": {
            "distribution": "PERT",
            "low": 4833.0250496847,
            "mode": 4833.0250496847,
            "high": 1297214.960826451,
        },
        "sl": {
            "distribution": "PERT",
            "low": 3918.6689591336,
            "mode": 3918.6689591336,
            "high": 1051795.9141647469,
        },
    },
    "edge-ransomware-perimeter-gateway": {
        "pl": {
            "distribution": "PERT",
            "low": 27853.0688387817,
            "mode": 27853.0688387817,
            "high": 7475942.547608651,
        },
        "sl": {
            "distribution": "PERT",
            "low": 12578.8052816517,
            "mode": 12578.8052816517,
            "high": 3376232.1181732104,
        },
    },
}


def _identity(sme_id: str | None, sme_name: str | None) -> str:
    # Canonicalise the FK text (hyphenated vs hex spellings are one SME) so a
    # pristine fieldset is never mis-reported as modified -- mirrors
    # wizard_finalize.row_identity_uuid's FK-or-casefolded-name partition.
    return _canonical(sme_id) if sme_id is not None else f"freetext:{(sme_name or '').casefold()}"


def _dedup_latest(
    rows: list[tuple[str | None, str | None, float, float]],
) -> list[tuple[float, float]]:
    """Latest-per-identity (rows arrive ordered by recorded_at ASC) -- mirrors
    services.wizard_finalize._dedup_latest_per_sme."""
    seen: dict[str, tuple[float, float]] = {}
    for sme_id, sme_name, low, high in rows:
        seen[_identity(sme_id, sme_name)] = (float(low), float(high))
    return list(seen.values())


def _matches(pair: tuple[float, float], ref: list[float] | tuple[float, float]) -> bool:
    return abs(pair[0] - round(ref[0], 2)) <= CENT and abs(pair[1] - round(ref[1], 2)) <= CENT


def has_pre_change_identity(
    rows: list[tuple[str | None, str | None, float, float]],
    old: list[float] | tuple[float, float],
) -> bool:
    """True when ANY surviving SME identity still carries the pre-change seeded pair --
    the rows a re-estimation would rehydrate into the re-fit, whether the fieldset is
    ``pristine`` (that row alone) or ``modified`` (that row pooled with analyst rows)."""
    return any(_matches(p, old) for p in _dedup_latest(rows))


def classify_fieldset(
    rows: list[tuple[str | None, str | None, float, float]],
    old: list[float] | tuple[float, float],
    new: list[float] | tuple[float, float],
) -> str:
    deduped = _dedup_latest(rows)
    if not deduped:
        return "none"
    if len(deduped) > 1:
        return "modified"
    (pair,) = deduped
    if _matches(pair, old):
        return "pristine"
    if _matches(pair, new):
        return "current"
    return "stale"


def is_copy_of(node: dict[str, Any] | None, old_node: dict[str, Any]) -> bool:
    """True when a stored scenario node is the pre-change entry node.

    Ignores the pooling sidecar and the org-minted capacity ``max`` that
    ``services/loss_pinning.py::_resolve_refresh`` adds to every lognormal field
    it refreshes (``new_dist["max"] = minted``); library nodes carry neither."""
    if not isinstance(node, dict):
        return False
    bare = {k: v for k, v in node.items() if k not in ("distribution_fit_metadata", "max")}
    return bare == old_node


def _stamp_source(dist: dict[str, Any] | None) -> str | None:
    """Mirror of services/loss_pinning._stamp_source: the sigma_recalibration.source
    stamp on a field."""
    if not isinstance(dist, dict):
        return None
    meta = dist.get("distribution_fit_metadata")
    if not isinstance(meta, dict):
        return None
    stamp = meta.get("sigma_recalibration")
    if not isinstance(stamp, dict):
        return None
    source = stamp.get("source")
    return source if isinstance(source, str) else None


def _scenario_class(pl: str, sl: str) -> str:
    """Per fieldset, like copy-stale: any pristine fieldset makes the scenario a repair
    candidate (the pl/sl columns name which side). A pinned field takes the whole
    scenario out (D23, module docstring); the sweep applies this after its own
    pinned/copy-stale early-outs (see ``sweep``). copy-current counts as current."""
    if "pinned" in (pl, sl):
        return "pinned"
    pl, sl = (("current" if c.startswith("copy-current") else c) for c in (pl, sl))
    if "pristine" in (pl, sl):
        return "pristine"
    if "modified" in (pl, sl):
        return "modified"
    if pl == "current" and sl in ("current", "none"):
        return "current"
    return "stale"


def _canonical(raw: object) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except ValueError:
        return str(raw)


def sweep(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        slug_by_entry = {
            str(r[0]).replace("-", "").lower(): r[1]
            for r in conn.execute("SELECT id, slug FROM scenario_library_entries WHERE version = 1")
        }
        summary = {
            "affected_scenarios": 0,
            "pristine": 0,
            "current": 0,
            "stale": 0,
            "modified": 0,
            "copy_stale": 0,
            "pinned": 0,
            "pinned_stale_side": 0,
            "copy_current_stale_rows": 0,
            "skipped_pin_version": 0,
            "skipped_deleted": 0,
        }
        print("scenario_id | slug | pl | sl | class")
        for sid, pin, status, pl_raw, sl_raw in conn.execute(
            "SELECT id, library_pin, status, primary_loss, secondary_loss FROM scenarios"
        ):
            p = (json.loads(pin) if pin else None) or {}
            entry_hex = str(p.get("entry_id", "")).replace("-", "").lower()
            slug = slug_by_entry.get(entry_hex)
            if slug not in OLD_PAIRS:
                continue
            if status == "deleted":
                summary["skipped_deleted"] += 1
                continue
            if p.get("version") != 1:
                summary["skipped_pin_version"] += 1
                continue
            nodes = {
                fs: (json.loads(raw) if isinstance(raw, str) else raw)
                for fs, raw in (("pl", pl_raw), ("sl", sl_raw))
            }
            classes: dict[str, str] = {}
            stale_rows_under_current = False
            for fs in ("pl", "sl"):
                # Pin stamp first: is_copy_of strips the sidecar, so a pinned node
                # must never be shadowed by a copy match.
                if _stamp_source(nodes[fs]) == "analyst_pin":
                    classes[fs] = "pinned"
                    continue
                if is_copy_of(nodes[fs], OLD_NODES[slug][fs]):
                    classes[fs] = (
                        "copy-stale"  # per fieldset: the other side may be an org override
                    )
                    continue
                rows = conn.execute(
                    "SELECT sme_id, sme_name, low, high FROM scenario_sme_estimates "
                    "WHERE scenario_id = :sid AND fieldset = :fs "
                    "ORDER BY recorded_at ASC, id ASC",
                    {"sid": sid, "fs": fs},
                ).fetchall()
                row_class = classify_fieldset(rows, OLD_PAIRS[slug][fs], NEW_PAIRS[slug][fs])
                if is_copy_of(nodes[fs], NEW_NODES[slug][fs]):
                    # Refreshed after b5e2c7a9d413 shipped. The rows are still read:
                    # a surviving pre-change seed row (alone or pooled) re-enters the
                    # node on the next re-estimation (module docstring); "*" marks it.
                    if has_pre_change_identity(rows, OLD_PAIRS[slug][fs]):
                        classes[fs] = "copy-current*"
                        stale_rows_under_current = True
                    else:
                        classes[fs] = "copy-current"
                    continue
                classes[fs] = row_class
            if "pinned" in classes.values():
                # D23: the app refuses to refresh this scenario at all; not a candidate.
                summary["pinned"] += 1
                if {"pristine", "copy-stale", "copy-current*"} & set(classes.values()):
                    summary["pinned_stale_side"] += 1
                print(f"{_canonical(sid)} | {slug} | {classes['pl']} | {classes['sl']} | pinned")
                continue
            if "copy-stale" in classes.values():
                summary["affected_scenarios"] += 1
                summary["copy_stale"] += 1
                print(
                    f"{_canonical(sid)} | {slug} | {classes['pl']} | {classes['sl']} | copy-stale"
                )
                continue
            klass = _scenario_class(classes["pl"], classes["sl"])
            summary["affected_scenarios"] += 1
            summary[klass] += 1
            if stale_rows_under_current:
                summary["copy_current_stale_rows"] += 1
            print(f"{_canonical(sid)} | {slug} | {classes['pl']} | {classes['sl']} | {klass}")
        print(
            f"SUMMARY affected={summary['affected_scenarios']} pristine={summary['pristine']} "
            f"copy_stale={summary['copy_stale']} current={summary['current']} "
            f"stale={summary['stale']} modified={summary['modified']} "
            f"pinned={summary['pinned']} pinned_stale_side={summary['pinned_stale_side']} "
            f"copy_current_stale_rows={summary['copy_current_stale_rows']} "
            f"skipped_pin_version={summary['skipped_pin_version']} "
            f"skipped_deleted={summary['skipped_deleted']}"
        )
        return summary
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print(__doc__ if len(argv) != 1 else f"no such file: {argv[0]}", file=sys.stderr)
        return 2
    sweep(Path(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
