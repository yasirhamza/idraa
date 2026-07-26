# Third-Party Licenses

Idraa incorporates a small amount of third-party source code. This file
reproduces the copyright and permission notices those components require, as a
condition of their (permissive) licenses.

This notice satisfies the attribution condition of the incorporated code and
applies **independently of the license under which Idraa itself is
distributed** — the MIT permission notice below must be retained whether Idraa
ships as proprietary, permissive, or undecided. Idraa's own license is not the
subject of this file.

For attribution of third-party **data** (framework crosswalks, ATT&CK catalog),
see the `data/*.NOTICE.md` files, which cover factual mappings rather than
incorporated code.

---

## collector / evaluator (tidyrisk) — MIT

**Where used:** `fair_cam/quantile_pooling/` is a Python port of the
truncated-lognormal and truncated-normal quantile-fitting and multi-component
pooling routines from `collector`'s `R/fit_distributions.R` (notably
`fit_lognorm_trunc` and `fit_norm_trunc`). Each ported module records its source
function and line range in-place; the port is pinned to an exact upstream commit
in `fair_cam/tests/quantile_pooling/fixtures/evaluator_commit_pinned.txt`.

**Upstream:** `github.com/davidski/collector` (David F. Severski; part of the
tidyrisk ecosystem)
**Pinned commit:** `061fc18d92c94509b5e72d0877763448d8580994`
**Permalink:** <https://github.com/davidski/collector/blob/061fc18d92c94509b5e72d0877763448d8580994/R/fit_distributions.R>

A port (R → Python translation) is a derivative work, so the upstream MIT notice
is reproduced verbatim below:

```
MIT License

Copyright (c) 2018 David F. Severski

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Scope: incorporation vs. invocation

The MIT notice above is required because `fair_cam/quantile_pooling/` **ports**
(translates) collector source into Python — a derivative work.

Separately, `scripts/regen_r_oracle.R` **invokes** collector through its public
R API (`library(collector)` → `collector::fit_lognorm_trunc`,
`combine_lognorm_trunc`, etc.) to regenerate the numeric validation oracle for
that port. This is *use* of the library, not incorporation of its source: no
collector source is vendored in this repository, and the generated oracle
(`r_oracle_outputs.json`) is not committed — the R-oracle parity tests skip when
it is absent, so CI runs without collector. Invoking a library via its public
API carries no attribution obligation; it is recorded here only for provenance.

---

## Not included here, and why

**pyfair** is intentionally **not** listed as an incorporated component. It was
formerly a runtime dependency and was removed in epic #324; the native engine
independently reimplements the same FAIR *node algebra* (Vose BetaPERT γ=4, the
per-iteration `LEF × LM` product form) from its mathematical definition, verified
against pyfair only via an equivalence harness. No pyfair source is retained.
Copyright does not extend to a method or algorithm (17 U.S.C. § 102(b)), so this
reimplementation carries no attribution obligation. pyfair is nonetheless
credited as the reference implementation throughout `docs/` and the engine
docstrings as a matter of provenance and courtesy.
