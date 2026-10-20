"""Small exact statistics for the gate, written out so the core has no numpy or scipy dependency."""

import math
import random
from functools import lru_cache


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@lru_cache(maxsize=None)
def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided p-value for the 2x2 table [[a, b], [c, d]] (rows: groups, columns: right / wrong).

    Sums the probability of every table with the same margins that is no more likely
    than the observed one."""
    row1, col1, n = a + b, a + c, a + b + c + d
    denom = math.comb(n, col1)

    def prob(x: int) -> float:
        return math.comb(row1, x) * math.comb(n - row1, col1 - x) / denom

    observed = prob(a)
    lo, hi = max(0, col1 - (n - row1)), min(row1, col1)
    return min(1.0, sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed * (1 + 1e-9)))


def benjamini_hochberg(pvals: list[float]) -> list[float]:
    """Adjusted p-values controlling the false discovery rate across many cases."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, pvals[i] * m / rank)
        adjusted[i] = min(1.0, running)
    return adjusted


def paired_bootstrap(diffs: list[float], n_boot: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """Mean of per-case differences with a 95% bootstrap interval (resampling cases)."""
    if not diffs:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    m = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(m)) / m for _ in range(n_boot))
    return (sum(diffs) / m, means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1])


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    """Agreement between two raters on yes/no labels, corrected for chance (1 = perfect, 0 = chance)."""
    n = len(a)
    if n == 0 or n != len(b):
        raise ValueError("need two equal-length, non-empty label lists")
    observed = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def sign_flip_test(diffs: list[float], n_perm: int = 4000, seed: int = 0, exact_up_to: int = 12) -> float:
    """Two-sided p-value for "the mean per-case difference is zero": if nothing changed, each case's
    difference is as likely to be +d as -d, so flip signs at random and see how often the total is at least
    as far from zero. Exact (every sign pattern) when few cases moved; Monte Carlo otherwise.
    Unlike a percentile bootstrap, it stays honest with rare events: with 5 cases moved the smallest
    possible p is 2/32 = 0.0625, so 5 scenarios can't make a significant difference on their own."""
    moved = [d for d in diffs if d != 0]
    if not moved:
        return 1.0
    observed = abs(sum(moved)) - 1e-12
    if len(moved) <= exact_up_to:
        hits = total = 0
        for mask in range(1 << len(moved)):
            s = sum(-d if mask >> i & 1 else d for i, d in enumerate(moved))
            hits += abs(s) >= observed
            total += 1
        return hits / total
    rng = random.Random(seed)
    hits = sum(abs(sum(d if rng.random() < 0.5 else -d for d in moved)) >= observed for _ in range(n_perm))
    return (hits + 1) / (n_perm + 1)


def bootstrap_test(diffs: list[float], alpha: float = 0.05, n_boot: int = 4000,
                   seed: int = 0) -> tuple[float, float, float, float]:
    """Mean of per-case differences, its (1 - alpha) bootstrap interval, and a two-sided bootstrap p-value
    (twice the share of resampled means on the far side of zero). Resampling cases keeps the replays of
    one case together, so they count as one piece of evidence, not several."""
    if not diffs:
        return (0.0, 0.0, 0.0, 1.0)
    if all(d == 0 for d in diffs):                     # nothing moved: skip the resampling
        return (0.0, 0.0, 0.0, 1.0)
    rng = random.Random(seed)
    m = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(m)) / m for _ in range(n_boot))
    lo_i = max(0, int(alpha / 2 * n_boot))
    hi_i = min(n_boot - 1, int((1 - alpha / 2) * n_boot) - 1)
    below = sum(x <= 0 for x in means) / n_boot
    above = sum(x >= 0 for x in means) / n_boot
    return (sum(diffs) / m, means[lo_i], means[hi_i], min(1.0, 2 * min(below, above)))


def bootstrap_ci(diffs: list[float], alpha: float = 0.05, n_boot: int = 4000, seed: int = 0) -> tuple[float, float, float]:
    """Mean of per-case differences with a (1 - alpha) bootstrap interval. Use a smaller alpha when
    several metrics are tested at once (Bonferroni: alpha / number of metrics)."""
    if not diffs:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    m = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(m)) / m for _ in range(n_boot))
    lo_i = max(0, int(alpha / 2 * n_boot))
    hi_i = min(n_boot - 1, int((1 - alpha / 2) * n_boot) - 1)
    return (sum(diffs) / m, means[lo_i], means[hi_i])
