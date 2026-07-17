#!/usr/bin/env Rscript
# Regenerate fair_cam/tests/quantile_pooling/fixtures/r_oracle_outputs.json
# from the pinned evaluator/collector R package. See
# fair_cam/tests/quantile_pooling/fixtures/evaluator_commit_pinned.txt for the
# pinned commit hash. Run from the repo root:
#
#   Rscript scripts/regen_r_oracle.R
#
# If `collector` is not installed:
#   R -e 'remotes::install_github("davidski/collector",
#                                  ref="061fc18d92c94509b5e72d0877763448d8580994")'
#
# Output JSON shape:
#   {
#     "lognormal": {
#       "<fid>": {
#         "inputs":  {...optional, present for single-SME fits...},
#         "fit":     {"meanlog": ..., "sdlog": ..., "min_support": ..., "max_support": ...},
#         "pooling": {...optional, present for combine_lognorm_trunc cases...}
#       },
#       ...
#     },
#     "normal": { ... mirror structure ... }
#   }

suppressPackageStartupMessages({
  library(collector)
  library(jsonlite)
})

OUT <- "fair_cam/tests/quantile_pooling/fixtures/r_oracle_outputs.json"

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

ln_fit <- function(low, high, min = 0, max = Inf) {
  res <- collector::fit_lognorm_trunc(low = low, high = high, min = min, max = max)
  list(
    meanlog     = as.numeric(res$meanlog),
    sdlog       = as.numeric(res$sdlog),
    min_support = as.numeric(min),
    max_support = if (is.infinite(max)) Inf else as.numeric(max)
  )
}

nm_fit <- function(low, high, min = 0, max = Inf) {
  res <- collector::fit_norm_trunc(low = low, high = high, min = min, max = max)
  list(
    mean        = as.numeric(res$mean),
    sd          = as.numeric(res$sd),
    min_support = as.numeric(min),
    max_support = if (is.infinite(max)) Inf else as.numeric(max)
  )
}

ln_pool <- function(fits, weights = rep(1, length(fits))) {
  df <- data.frame(
    meanlog = sapply(fits, function(f) f$meanlog),
    sdlog   = sapply(fits, function(f) f$sdlog),
    min     = sapply(fits, function(f) f$min_support),
    max     = sapply(fits, function(f) f$max_support),
    weight  = weights
  )
  pooled <- collector::combine_lognorm_trunc(df)
  list(
    meanlog     = as.numeric(pooled$meanlog),
    sdlog       = as.numeric(pooled$sdlog),
    min_support = as.numeric(pooled$min),
    max_support = if (is.infinite(pooled$max)) Inf else as.numeric(pooled$max)
  )
}

nm_pool <- function(fits, weights = rep(1, length(fits))) {
  df <- data.frame(
    mean    = sapply(fits, function(f) f$mean),
    sd      = sapply(fits, function(f) f$sd),
    min     = sapply(fits, function(f) f$min_support),
    max     = sapply(fits, function(f) f$max_support),
    weight  = weights
  )
  pooled <- collector::combine_norm(df)
  list(
    mean        = as.numeric(pooled$mean),
    sd          = as.numeric(pooled$sd),
    min_support = as.numeric(pooled$min),
    max_support = if (is.infinite(pooled$max)) Inf else as.numeric(pooled$max)
  )
}

# ----------------------------------------------------------------------------
# Lognormal fixtures (per spec §9.1: 8 fixtures)
#
# fids "1", "2", "3", "8" are single-SME fits (test_fit_lognorm_trunc).
# fids "4", "5", "6", "7" are pooling fixtures (test_combine_lognorm_trunc).
# Inputs chosen to exercise:
#   - typical losses with default (q_low,q_high) = (0.05, 0.95)
#   - support truncation (min/max)
#   - wide vs narrow CIs
#   - pooling 2 / 3 / 5 SMEs with equal weights
#   - pooling with explicit non-uniform weights
# ----------------------------------------------------------------------------

# Singles
fid1_inputs <- list(low = 10000,  high = 100000, min_support = 0,    max_support = Inf)
fid2_inputs <- list(low = 100,    high = 500,    min_support = 0,    max_support = Inf)
fid3_inputs <- list(low = 50000,  high = 75000,  min_support = 1000, max_support = 1e7)
fid8_inputs <- list(low = 1,      high = 1000,   min_support = 0.1,  max_support = Inf)

fid1_fit <- ln_fit(fid1_inputs$low, fid1_inputs$high,
                   fid1_inputs$min_support, fid1_inputs$max_support)
fid2_fit <- ln_fit(fid2_inputs$low, fid2_inputs$high,
                   fid2_inputs$min_support, fid2_inputs$max_support)
fid3_fit <- ln_fit(fid3_inputs$low, fid3_inputs$high,
                   fid3_inputs$min_support, fid3_inputs$max_support)
fid8_fit <- ln_fit(fid8_inputs$low, fid8_inputs$high,
                   fid8_inputs$min_support, fid8_inputs$max_support)

# Pooling fixtures
# fid 4: pool 2 single-SME fits (fid1 + fid2), equal weights
fid4_components <- list(fid1_fit, fid2_fit)
fid4_weights    <- c(1, 1)
fid4_pool       <- ln_pool(fid4_components, fid4_weights)

# fid 5: pool 3 fits, equal weights; mid-range losses
fid5_components <- list(
  ln_fit(5000,  20000),
  ln_fit(10000, 40000),
  ln_fit(15000, 60000)
)
fid5_weights <- c(1, 1, 1)
fid5_pool    <- ln_pool(fid5_components, fid5_weights)

# fid 6: pool 3 fits, NON-uniform weights (2, 1, 1)
fid6_components <- fid5_components
fid6_weights    <- c(2, 1, 1)
fid6_pool       <- ln_pool(fid6_components, fid6_weights)

# fid 7: pool 5 fits, equal weights, with bounded support
fid7_components <- list(
  ln_fit(1000,  5000,  min = 100, max = 1e6),
  ln_fit(2000, 10000,  min = 100, max = 1e6),
  ln_fit(3000, 15000,  min = 100, max = 1e6),
  ln_fit(4000, 20000,  min = 100, max = 1e6),
  ln_fit(5000, 25000,  min = 100, max = 1e6)
)
fid7_weights <- rep(1, 5)
fid7_pool    <- ln_pool(fid7_components, fid7_weights)

lognormal <- list(
  "1" = list(inputs = fid1_inputs, fit = fid1_fit),
  "2" = list(inputs = fid2_inputs, fit = fid2_fit),
  "3" = list(inputs = fid3_inputs, fit = fid3_fit),
  "4" = list(
    pooling = list(
      components = fid4_components,
      weights    = as.list(fid4_weights),
      pooled     = fid4_pool
    )
  ),
  "5" = list(
    pooling = list(
      components = fid5_components,
      weights    = as.list(fid5_weights),
      pooled     = fid5_pool
    )
  ),
  "6" = list(
    pooling = list(
      components = fid6_components,
      weights    = as.list(fid6_weights),
      pooled     = fid6_pool
    )
  ),
  "7" = list(
    pooling = list(
      components = fid7_components,
      weights    = as.list(fid7_weights),
      pooled     = fid7_pool
    )
  ),
  "8" = list(inputs = fid8_inputs, fit = fid8_fit)
)

# ----------------------------------------------------------------------------
# Normal fixtures (per spec §9.1: 4 fixtures for vuln)
# fids "1", "2" are single-SME fits; "3", "4" are pooling fixtures.
# ----------------------------------------------------------------------------

nm1_inputs <- list(low = 0.05, high = 0.95, min_support = 0, max_support = 1)
nm2_inputs <- list(low = 0.20, high = 0.40, min_support = 0, max_support = 1)

nm1_fit <- nm_fit(nm1_inputs$low, nm1_inputs$high,
                  nm1_inputs$min_support, nm1_inputs$max_support)
nm2_fit <- nm_fit(nm2_inputs$low, nm2_inputs$high,
                  nm2_inputs$min_support, nm2_inputs$max_support)

# nm 3: pool 2 normal fits, equal weights
nm3_components <- list(nm1_fit, nm2_fit)
nm3_weights    <- c(1, 1)
nm3_pool       <- nm_pool(nm3_components, nm3_weights)

# nm 4: pool 3 normal fits with non-uniform weights
# Meth-4 T1 review: explicit min=0, max=1 to match Python's fit_norm_trunc
# default for vuln (max_support=1.0 per MD-4a). R's default max=Inf would
# drift from Python and break the eventual R-Python parity oracle.
nm4_components <- list(
  nm_fit(0.10, 0.30, min = 0, max = 1),
  nm_fit(0.20, 0.50, min = 0, max = 1),
  nm_fit(0.40, 0.80, min = 0, max = 1)
)
nm4_weights <- c(1, 2, 1)
nm4_pool    <- nm_pool(nm4_components, nm4_weights)

normal <- list(
  "1" = list(inputs = nm1_inputs, fit = nm1_fit),
  "2" = list(inputs = nm2_inputs, fit = nm2_fit),
  "3" = list(
    pooling = list(
      components = nm3_components,
      weights    = as.list(nm3_weights),
      pooled     = nm3_pool
    )
  ),
  "4" = list(
    pooling = list(
      components = nm4_components,
      weights    = as.list(nm4_weights),
      pooled     = nm4_pool
    )
  )
)

# ----------------------------------------------------------------------------
# Serialize
# ----------------------------------------------------------------------------

out_list <- list(lognormal = lognormal, normal = normal)
jsonlite::write_json(out_list, OUT, digits = 12, auto_unbox = TRUE, pretty = TRUE)
cat(sprintf("Wrote %s\n", OUT))
