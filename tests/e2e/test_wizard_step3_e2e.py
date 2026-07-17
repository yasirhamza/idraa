"""T11 wizard step-3 E2E tests (spec §9.5) — DEFERRED.

Per the T11 scope adjustment in the implementation brief: the 6 Playwright
E2E tests called out in spec §9.5 are deferred to a follow-up PR. They all
depend on the seed_user_login_e2e + seed_ot_library_entry_e2e fixture
scaffolding that's still stubbed in tests/e2e/conftest.py (Phase 1.5b).

Until those fixtures land, every E2E test in this repo (test_wizard_full_flow,
test_pr_iota_smoke, etc.) skips at fixture resolution time. The §9.5 cases
would skip identically, providing zero new signal — so we omit them here
rather than ship 6 skip-stubs that look like coverage.

§9.5 E2E cases to write once the infrastructure is in place:

  1. Happy path: pick library entry -> step 3 SME-row form -> finalize ->
     scenario detail page shows pooled sidecar.
  2. Analyst save-to-directory inline flow:
       a. Analyst types a new SME name in the step-3 combobox, picks the
          "+ Save 'X' to directory" footer item, which POSTs
          /scenarios/wizard/request-sme inline (auto-approved by T2).
       b. Admin visits SME directory, sees the new auto-approved row.
       c. Analyst re-loads step 3, finalizes with the saved SME row.
  3. Sanity-floor warning chip: low/high triple where high < min_support
     surfaces the per-row chip from the split likelihood/impact pages
     (step_3_likelihood.html / step_4_impact.html).
  4. IRIS reset preserves analyst rows: click "Reset to industry baseline"
     after the analyst added a custom row; only the IRIS row is reset,
     custom rows survive (MD-7 semantics).
  5. XSS payload SME name: an SME with name="<script>alert(1)</script>"
     renders escaped in the dropdown, no script execution.
  6. Apply-overlay end-to-end: pick an overlay -> rows multiplied -> finalize.

Tracked in #TBD (filed on T12 follow-up branch).
"""
