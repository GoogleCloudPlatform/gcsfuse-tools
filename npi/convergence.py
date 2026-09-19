"""Pure-Python statistical & adaptive convergence engine for GCSFuse NPI suite.

Provides exact Student's t critical values and two-sided p-values via the
regularized incomplete beta function, robust Modified Z-Score (MAD) outlier
filtering with zero-MAD noise-floor safeguards, adaptive convergence evaluation,
and Welch's two-sample t-test comparison with practical effect size
classification. Uses ONLY Python 3.13 standard library modules.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext
import math
import statistics
from typing import Sequence

MAD_NORMAL_CONSISTENCY: float = 0.6744897501960817

_DEC_HALF_LN_PI = Decimal(
    "0.57236494292470008707171367567652935582364747645790578556936222965924667794404237"
)
_DEC_HALF_LN_2PI = Decimal(
    "0.91893853320467274178032973640561763986139747363778341281715154048276569592726039"
)
_BERNOULLI_EVEN: tuple[tuple[int, int], ...] = (
    (1, 6),
    (-1, 30),
    (1, 42),
    (-1, 30),
    (5, 66),
    (-691, 2730),
    (7, 6),
    (-3617, 510),
    (43867, 798),
    (-174611, 330),
    (854513, 138),
    (-236364091, 2730),
    (8553103, 6),
)


@dataclass
class ConvergenceResult:
    """Result of adaptive convergence evaluation and single-value estimation."""

    representative_value: float      # Outlier-trimmed mean (canonical single value)
    median: float                    # Sample median of inliers
    raw_mean: float                  # Unfiltered arithmetic mean of steady-state samples
    std_dev: float                   # Sample standard deviation of inliers (0.0 if n_inliers < 2)
    ci_half_width: float             # Student's t confidence interval half-width H_{1-alpha}
    relative_margin_of_error: float  # ci_half_width / abs(representative_value) (0.0 if mean == 0 and ci == 0)
    confidence_level: float          # e.g., 0.95
    convergence_threshold: float     # Target relative margin of error threshold (e.g., 0.05)
    converged: bool                  # True iff n_steady >= min_iterations and relative_margin_of_error <= convergence_threshold
    n_total: int                     # Total samples passed (including warmup)
    n_steady: int                    # Steady-state samples evaluated (after warmup separation)
    n_inliers: int                   # Number of retained inlier samples after MAD filtering
    outliers_removed: list[float]    # List of rejected outlier values
    inliers: list[float]             # List of retained inlier values
    warmup_samples: list[float]      # Separated warm-up samples (e.g. Iteration 1 when keep_mount=True)


@dataclass
class ComparisonResult:
    """Result of Welch's two-sample t-test and practical effect size comparison."""

    baseline_value: float            # Canonical representative value of baseline
    candidate_value: float           # Canonical representative value of candidate
    baseline_ci_half_width: float    # 95% CI half-width of baseline
    candidate_ci_half_width: float   # 95% CI half-width of candidate
    delta_abs: float                 # candidate_value - baseline_value
    delta_pct: float                 # ((candidate_value - baseline_value) / baseline_value) * 100.0
    t_stat: float                    # Welch's two-sample t-statistic
    degrees_of_freedom: float        # Welch-Satterthwaite effective degrees of freedom
    p_value: float                   # Two-sided p-value from exact Student's t distribution
    cohens_d: float                  # Pooled/Welch standardized effect size
    statistically_significant: bool  # True iff p_value < alpha
    practically_significant: bool    # True iff abs(delta_pct) >= min_effect_pct
    verdict: str                     # One of: "SIGNIFICANT_IMPROVEMENT", "SIGNIFICANT_REGRESSION", "NOISE / NEUTRAL", "INSUFFICIENT_SAMPLES"


def _log_beta(a: float, b: float) -> float:
    """Computes ln B(a, b) = ln Gamma(a) + ln Gamma(b) - ln Gamma(a + b) without cancellation."""
    p = min(a, b)
    q = max(a, b)
    if q < 50.0:
        return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)

    u = p / q
    if u < 1.0e-2:
        poly = 0.0
        term = -u
        for j in range(1, 16):
            poly += term / (j + 1.0)
            term *= -u
        q_log1p_minus_u = p * poly
    else:
        q_log1p_minus_u = q * (math.log1p(u) - u)

    coeffs = (
        1.0 / 12.0,
        -1.0 / 360.0,
        1.0 / 1260.0,
        -1.0 / 1680.0,
        1.0 / 1188.0,
    )
    stirling_diff = 0.0
    qp = q + p
    for k, c in enumerate(coeffs, start=1):
        power = 2 * k - 1
        stirling_diff += c * (q ** (-power) - qp ** (-power))

    lgamma_q_minus_qp = (
        -p * math.log(q)
        - (p - 0.5) * math.log1p(u)
        - q_log1p_minus_u
        + stirling_diff
    )
    return math.lgamma(p) + lgamma_q_minus_qp


def _dec_ln_gamma(z: Decimal) -> Decimal:
    """Arbitrary-precision log-gamma via recurrence shift + 13-term Stirling series."""
    if z == Decimal("0.5"):
        return _DEC_HALF_LN_PI
    prod = Decimal(1)
    while z < Decimal(25):
        prod *= z
        z += Decimal(1)
    shift = prod.ln() if prod != Decimal(1) else Decimal(0)
    res = (z - Decimal("0.5")) * z.ln() - z + _DEC_HALF_LN_2PI
    z2 = z * z
    zp = z
    for k, (num, den) in enumerate(_BERNOULLI_EVEN, start=1):
        coeff = Decimal(num) / (Decimal(den) * Decimal(2 * k) * Decimal(2 * k - 1))
        res += coeff / zp
        zp *= z2
    return res - shift


def _decimal_betainc_core(
    a: Decimal, b: Decimal, x: Decimal, oneminusx: Decimal
) -> Decimal:
    """Evaluates regularized incomplete beta I_x(a, b) in arbitrary-precision Decimal without recursion."""
    one = Decimal(1)
    thresh = (a + one) / (a + b + Decimal(2))
    flip = False
    if x > thresh:
        a, b = b, a
        x, oneminusx = oneminusx, x
        flip = True

    log_b = _dec_ln_gamma(a) + _dec_ln_gamma(b) - _dec_ln_gamma(a + b)
    log_pref = a * x.ln() + b * oneminusx.ln() - log_b
    if log_pref < Decimal(-1000):
        val = Decimal(0)
    else:
        pref = log_pref.exp()
        qab = a + b
        qap = a + one
        qam = a - one
        c = one
        d = one - qab * x / qap
        fpmin = Decimal("1e-500")
        if abs(d) < fpmin:
            d = fpmin
        d = one / d
        h = d
        eps = Decimal("1e-35")
        for m in range(1, 20001):
            dm = Decimal(m)
            m2 = Decimal(2 * m)
            aa = dm * (b - dm) * x / ((qam + m2) * (a + m2))
            d = one + aa * d
            if abs(d) < fpmin:
                d = fpmin
            c = one + aa / c
            if abs(c) < fpmin:
                c = fpmin
            d = one / d
            h *= d * c

            aa = -(a + dm) * (qab + dm) * x / ((a + m2) * (qap + m2))
            d = one + aa * d
            if abs(d) < fpmin:
                d = fpmin
            c = one + aa / c
            if abs(c) < fpmin:
                c = fpmin
            d = one / d
            delta = d * c
            h *= delta
            if abs(delta - one) < eps:
                break
        val = (pref / a) * h

    res = one - val if flip else val
    if res < Decimal(0):
        return Decimal(0)
    if res > one:
        return one
    return res


def betainc(a: float, b: float, x: float) -> float:
    """Computes regularized incomplete beta function I_x(a, b) using pure stdlib."""
    if math.isnan(a) or math.isnan(b) or math.isnan(x):
        return math.nan
    if a <= 0.0 or b <= 0.0:
        raise ValueError(f"Parameters a and b must be strictly positive, got a={a}, b={b}")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if a == b and x == 0.5:
        return 0.5

    threshold = (a + 1.0) / (a + b + 2.0)
    flip = x > threshold
    if flip:
        a, b, x = b, a, 1.0 - x

    with localcontext() as ctx:
        ctx.prec = 55
        da = Decimal.from_float(a)
        db = Decimal.from_float(b)
        dx = Decimal.from_float(x)
        one = Decimal(1)
        oneminusx = one - dx

        log_b = _dec_ln_gamma(da) + _dec_ln_gamma(db) - _dec_ln_gamma(da + db)
        log_pref = da * dx.ln() + db * oneminusx.ln() - log_b
        if log_pref < Decimal(-1000):
            return 1.0 if flip else 0.0

        pref = log_pref.exp()
        qab = da + db
        qap = da + one
        qam = da - one
        c = one
        d = one - qab * dx / qap
        fpmin = Decimal("1e-500")
        if abs(d) < fpmin:
            d = fpmin
        d = one / d
        h = d
        eps = Decimal("1e-35")

        for m in range(1, 20001):
            dm = Decimal(m)
            m2 = Decimal(2 * m)
            aa = dm * (db - dm) * dx / ((qam + m2) * (da + m2))
            d = one + aa * d
            if abs(d) < fpmin:
                d = fpmin
            c = one + aa / c
            if abs(c) < fpmin:
                c = fpmin
            d = one / d
            h *= d * c

            aa = -(da + dm) * (qab + dm) * dx / ((da + m2) * (qap + m2))
            d = one + aa * d
            if abs(d) < fpmin:
                d = fpmin
            c = one + aa / c
            if abs(c) < fpmin:
                c = fpmin
            d = one / d
            delta = d * c
            h *= delta
            if abs(delta - one) < eps:
                break

        val = (pref / da) * h
        res = float(one - val if flip else val)
        return min(1.0, max(0.0, res))


def student_t_two_sided_pvalue(t_stat: float, df: float) -> float:
    """Computes exact two-sided p-value P(|T_df| >= |t_stat|) for Student's t distribution."""
    if math.isnan(t_stat) or math.isnan(df):
        return math.nan
    if df <= 0.0:
        raise ValueError(f"Degrees of freedom df must be strictly positive, got df={df}")
    if math.isinf(t_stat):
        return 0.0
    if t_stat == 0.0:
        return 1.0
    if math.isinf(df):
        return math.erfc(abs(t_stat) / math.sqrt(2.0))

    t2 = t_stat * t_stat
    if t2 == 0.0:
        return 1.0
    if math.isinf(t2):
        return 0.0

    with localcontext() as ctx:
        ctx.prec = 50
        t_dec = Decimal.from_float(t_stat)
        df_dec = Decimal.from_float(df)
        t2_dec = t_dec * t_dec
        a = df_dec / Decimal(2)
        b = Decimal("0.5")
        denom = df_dec + t2_dec
        x = df_dec / denom
        oneminusx = t2_dec / denom
        return float(_decimal_betainc_core(a, b, x, oneminusx))


def _student_t_log_pdf(t: float, df: float) -> float:
    """Logarithm of Student's t probability density function f_df(t) without cancellation."""
    return (
        -0.5 * math.log(df)
        - _log_beta(0.5 * df, 0.5)
        - 0.5 * (df + 1.0) * math.log1p((t * t) / df)
    )


_T_CRIT_CACHE: dict[tuple[float, float], float] = {}


def student_t_critical_value(df: float, confidence: float = 0.95) -> float:
    """Computes two-sided Student's t critical value t_{alpha/2, df} for given confidence level."""
    if math.isnan(df) or math.isnan(confidence):
        return math.nan
    if df <= 0.0:
        raise ValueError(f"Degrees of freedom df must be strictly positive, got df={df}")
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"Confidence level must be in (0, 1), got confidence={confidence}")

    cache_key = (df, confidence)
    cached = _T_CRIT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    alpha = 1.0 - confidence
    if math.isinf(df):
        res = statistics.NormalDist().inv_cdf(1.0 - 0.5 * alpha)
        if len(_T_CRIT_CACHE) < 1024:
            _T_CRIT_CACHE[cache_key] = res
        return res

    if df == 1.0:
        res = 1.0 / math.tan(0.5 * math.pi * alpha)
        if len(_T_CRIT_CACHE) < 1024:
            _T_CRIT_CACHE[cache_key] = res
        return res
    if df == 2.0:
        res = math.sqrt(2.0 / (alpha * (2.0 - alpha)) - 2.0)
        if len(_T_CRIT_CACHE) < 1024:
            _T_CRIT_CACHE[cache_key] = res
        return res

    z = statistics.NormalDist().inv_cdf(1.0 - 0.5 * alpha)
    z2 = z * z
    z3 = z2 * z
    z5 = z3 * z2
    if df >= 1.0:
        t = (
            z
            + (z3 + z) / (4.0 * df)
            + (5.0 * z5 + 16.0 * z3 + 3.0 * z) / (96.0 * df * df)
            + (3.0 * z5 * z2 + 19.0 * z5 + 17.0 * z3 - 15.0 * z) / (384.0 * df * df * df)
        )
    else:
        log_b = _log_beta(0.5 * df, 0.5)
        t = math.sqrt(df) * math.exp(-math.log(max(1e-300, alpha * 0.5 * df)) / df - log_b / df)

    if t <= 0.0 or not math.isfinite(t):
        t = max(z, 1.0)

    lo = 0.0
    hi = math.inf
    best_t = t
    best_abs_err = math.inf
    prev_abs_err = math.inf

    for _ in range(60):
        p = student_t_two_sided_pvalue(t, df)
        err = p - alpha
        abs_err = abs(err)
        if abs_err < best_abs_err:
            best_abs_err = abs_err
            best_t = t
        if abs_err <= 1.0e-15 * alpha:
            if len(_T_CRIT_CACHE) < 1024:
                _T_CRIT_CACHE[cache_key] = t
            return t

        if err > 0.0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)

        log_pdf = _student_t_log_pdf(t, df)
        if log_pdf < -700.0:
            if math.isinf(hi):
                t_next = t * 2.0
            else:
                t_next = 0.5 * (lo + hi)
        else:
            deriv = -2.0 * math.exp(log_pdf)
            t_next = t - err / deriv

        if (
            t_next <= lo
            or t_next >= hi
            or not math.isfinite(t_next)
            or abs_err >= prev_abs_err
            or (math.isfinite(hi) and abs(t_next - t) > 0.5 * (hi - lo))
        ):
            if math.isinf(hi):
                t_next = max(lo * 2.0, t * 2.0, 1.0)
            else:
                t_next = 0.5 * (lo + hi)

        prev_abs_err = abs_err

        if abs(t_next - t) <= 1.0e-15 * max(1.0, t):
            p_next = student_t_two_sided_pvalue(t_next, df)
            if abs(p_next - alpha) < best_abs_err:
                best_t = t_next
            if len(_T_CRIT_CACHE) < 1024:
                _T_CRIT_CACHE[cache_key] = best_t
            return best_t
        t = t_next

    if len(_T_CRIT_CACHE) < 1024:
        _T_CRIT_CACHE[cache_key] = best_t
    return best_t


def filter_outliers_mad(
    samples: Sequence[float],
    mad_threshold: float = 3.5,
) -> tuple[list[float], list[float]]:
    """Filter outliers using Modified Z-Score (MAD) with MAD=0 noise-floor and majority-retention safeguards."""
    xs = [float(x) for x in samples]
    n = len(xs)
    if n < 3:
        return list(xs), []

    med = statistics.median(xs)
    deviations = [abs(x - med) for x in xs]
    mad = statistics.median(deviations)

    eps_mad = 1e-9 * max(1.0, abs(med))
    if mad > eps_mad:
        is_outlier = [
            (MAD_NORMAL_CONSISTENCY * d / mad) > mad_threshold
            for d in deviations
        ]
    else:
        tau_floor = 1e-3 * max(1.0, abs(med))
        is_outlier = [d > tau_floor for d in deviations]

    min_keep = max(2, math.ceil(n / 2))
    n_kept = sum(1 for flag in is_outlier if not flag)
    if n_kept < min_keep:
        ranked_indices = sorted(range(n), key=lambda i: (deviations[i], i))
        keep_set = set(ranked_indices[:min_keep])
        is_outlier = [i not in keep_set for i in range(n)]

    inliers = [xs[i] for i in range(n) if not is_outlier[i]]
    outliers = [xs[i] for i in range(n) if is_outlier[i]]
    return inliers, outliers


def evaluate_convergence(
    samples: Sequence[float],
    min_iterations: int = 3,
    convergence_threshold: float = 0.05,
    confidence_level: float = 0.95,
    warmup_iterations: int = 0,
    mad_threshold: float = 3.5,
) -> ConvergenceResult:
    """Evaluate statistical convergence and compute canonical single-value & interval estimates."""
    all_samples = [float(x) for x in samples]
    n_total = len(all_samples)
    eff_warmup = max(0, warmup_iterations)
    warmup_samples = all_samples[:eff_warmup]
    steady_samples = all_samples[eff_warmup:]
    n_steady = len(steady_samples)

    if n_steady == 0:
        return ConvergenceResult(
            representative_value=0.0,
            median=0.0,
            raw_mean=0.0,
            std_dev=0.0,
            ci_half_width=float("inf"),
            relative_margin_of_error=float("inf"),
            confidence_level=confidence_level,
            convergence_threshold=convergence_threshold,
            converged=False,
            n_total=n_total,
            n_steady=0,
            n_inliers=0,
            outliers_removed=[],
            inliers=[],
            warmup_samples=warmup_samples,
        )

    raw_mean = statistics.fmean(steady_samples)
    inliers, outliers_removed = filter_outliers_mad(
        steady_samples, mad_threshold=mad_threshold
    )
    n_inliers = len(inliers)
    representative_value = statistics.fmean(inliers)
    med = statistics.median(inliers)

    if n_inliers < 2:
        std_dev = 0.0
        ci_half_width = float("inf")
        relative_margin_of_error = float("inf")
    else:
        std_dev = statistics.stdev(inliers)
        if std_dev == 0.0:
            ci_half_width = 0.0
            relative_margin_of_error = 0.0
        else:
            t_crit = student_t_critical_value(
                df=float(n_inliers - 1), confidence=confidence_level
            )
            se = std_dev / math.sqrt(n_inliers)
            ci_half_width = t_crit * se
            abs_mean = abs(representative_value)
            if abs_mean > 0.0:
                relative_margin_of_error = ci_half_width / abs_mean
            elif ci_half_width == 0.0:
                relative_margin_of_error = 0.0
            else:
                relative_margin_of_error = float("inf")

    converged = bool(
        n_steady >= min_iterations
        and n_inliers >= 2
        and relative_margin_of_error <= convergence_threshold
    )

    return ConvergenceResult(
        representative_value=representative_value,
        median=med,
        raw_mean=raw_mean,
        std_dev=std_dev,
        ci_half_width=ci_half_width,
        relative_margin_of_error=relative_margin_of_error,
        confidence_level=confidence_level,
        convergence_threshold=convergence_threshold,
        converged=converged,
        n_total=n_total,
        n_steady=n_steady,
        n_inliers=n_inliers,
        outliers_removed=outliers_removed,
        inliers=inliers,
        warmup_samples=warmup_samples,
    )


def compare_samples_welch(
    baseline_samples: Sequence[float],
    candidate_samples: Sequence[float],
    alpha: float = 0.05,
    min_effect_pct: float = 2.0,
    higher_is_better: bool = True,
    confidence_level: float = 0.95,
    mad_threshold: float = 3.5,
) -> ComparisonResult:
    """Compare baseline and candidate sample distributions using Welch's two-sample t-test.

    Pre-filters both sample sets with robust MAD outlier detection via evaluate_convergence()
    so transient stalls do not corrupt sample means or inflate sample variances.
    """
    base_conv = evaluate_convergence(
        baseline_samples,
        confidence_level=confidence_level,
        mad_threshold=mad_threshold,
    )
    cand_conv = evaluate_convergence(
        candidate_samples,
        confidence_level=confidence_level,
        mad_threshold=mad_threshold,
    )

    n_b = base_conv.n_inliers
    n_c = cand_conv.n_inliers
    y_b = base_conv.representative_value
    y_c = cand_conv.representative_value
    s_b = base_conv.std_dev
    s_c = cand_conv.std_dev

    delta_abs = y_c - y_b
    if y_b != 0.0:
        delta_pct = (delta_abs / y_b) * 100.0
    elif delta_abs == 0.0:
        delta_pct = 0.0
    else:
        delta_pct = math.copysign(math.inf, delta_abs)

    practically_sig = bool(abs(delta_pct) >= min_effect_pct)

    # EC-3 & EC-4: Insufficient samples in baseline or candidate
    if n_b < 2 or n_c < 2:
        return ComparisonResult(
            baseline_value=y_b,
            candidate_value=y_c,
            baseline_ci_half_width=base_conv.ci_half_width,
            candidate_ci_half_width=cand_conv.ci_half_width,
            delta_abs=delta_abs,
            delta_pct=delta_pct,
            t_stat=0.0,
            degrees_of_freedom=0.0,
            p_value=1.0,
            cohens_d=0.0,
            statistically_significant=False,
            practically_significant=practically_sig,
            verdict="INSUFFICIENT_SAMPLES",
        )

    v_b = (s_b * s_b) / n_b
    v_c = (s_c * s_c) / n_c
    se_delta = math.sqrt(v_b + v_c)
    s_pool = math.sqrt(((s_b * s_b) + (s_c * s_c)) / 2.0)

    # EC-1: Zero variance in both baseline and candidate
    if se_delta == 0.0:
        df = float(n_b + n_c - 2)
        if delta_abs == 0.0:
            # EC-1a: Equal means with zero variance
            return ComparisonResult(
                baseline_value=y_b,
                candidate_value=y_c,
                baseline_ci_half_width=base_conv.ci_half_width,
                candidate_ci_half_width=cand_conv.ci_half_width,
                delta_abs=0.0,
                delta_pct=0.0,
                t_stat=0.0,
                degrees_of_freedom=df,
                p_value=1.0,
                cohens_d=0.0,
                statistically_significant=False,
                practically_significant=False,
                verdict="NOISE / NEUTRAL",
            )
        else:
            # EC-1b: Unequal means with zero variance
            t_stat = math.copysign(math.inf, delta_abs)
            cohens_d = math.copysign(math.inf, delta_abs)
            p_value = 0.0
            stat_sig = True
    else:
        # Normal case & EC-2 (one zero variance, one positive variance)
        t_stat = delta_abs / se_delta
        df_num = (v_b + v_c) ** 2
        df_denom = ((v_b ** 2) / (n_b - 1)) + ((v_c ** 2) / (n_c - 1))
        df = df_num / df_denom
        p_value = student_t_two_sided_pvalue(t_stat, df)
        cohens_d = delta_abs / s_pool if s_pool > 0.0 else math.copysign(math.inf, delta_abs)
        stat_sig = bool(p_value < alpha)

    if stat_sig and practically_sig:
        if higher_is_better:
            verdict = "SIGNIFICANT_IMPROVEMENT" if delta_abs > 0.0 else "SIGNIFICANT_REGRESSION"
        else:
            verdict = "SIGNIFICANT_IMPROVEMENT" if delta_abs < 0.0 else "SIGNIFICANT_REGRESSION"
    else:
        verdict = "NOISE / NEUTRAL"

    return ComparisonResult(
        baseline_value=y_b,
        candidate_value=y_c,
        baseline_ci_half_width=base_conv.ci_half_width,
        candidate_ci_half_width=cand_conv.ci_half_width,
        delta_abs=delta_abs,
        delta_pct=delta_pct,
        t_stat=t_stat,
        degrees_of_freedom=df,
        p_value=p_value,
        cohens_d=cohens_d,
        statistically_significant=stat_sig,
        practically_significant=practically_sig,
        verdict=verdict,
    )
