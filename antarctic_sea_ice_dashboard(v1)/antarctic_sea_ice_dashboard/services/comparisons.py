"""Same-month spatial pairing and descriptive Pearson statistics."""

from collections import OrderedDict
from dataclasses import dataclass
import math
import logging
from threading import Lock

from antarctic_sea_ice_dashboard.services.product_storage import ProductError, canonical_json, digest_bytes
from antarctic_sea_ice_dashboard.services.region_statistics import (
    AREA_IDENTITY, REGION_KEY, REGION_VERSION, frame_provenance, region_cell_ids,
)
from antarctic_sea_ice_dashboard.services.scientific_data import (
    ProductSelection, read_scientific_month, scientific_inventory,
)


logger = logging.getLogger(__name__)
COMPARISON_VERSION = "same-month-spatial-pearson-1.0.0"
MAX_DISPLAY_POINTS = 1000
COMPARISON_CACHE_SIZE = 4
_CACHE_LOCK = Lock()


@dataclass(frozen=True)
class ComparisonRequest:
    product_a: ProductSelection
    product_b: ProductSelection
    month: str
    region_key: str = REGION_KEY
    statistic: str = "spatial_pearson"
    display_limit: int = MAX_DISPLAY_POINTS


@dataclass(frozen=True)
class MonthIntersection:
    months: tuple[str, ...] = ()
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class DisplayPoint:
    grid_cell_id: int
    x: float
    y: float


@dataclass(frozen=True)
class ComparisonResult:
    request: ComparisonRequest
    code: str
    message: str
    request_id: str = ""
    paired_cell_count: int = 0
    displayed_point_count: int = 0
    spatial_pearson_r: float | None = None
    statistic_status: str = "not_computed"
    points: tuple[DisplayPoint, ...] = ()
    excluded_counts: tuple[tuple[str, int], ...] = ()
    x_label: str = ""
    y_label: str = ""
    provenance: tuple[tuple[str, str], ...] = ()


_CACHE: OrderedDict[str, ComparisonResult] = OrderedDict()


def comparison_months(product_a: ProductSelection, product_b: ProductSelection) -> MonthIntersection:
    """Return the canonical YYYY-MM intersection, never generated intervening months.

    Both exact published identities, rules and grid references are rechecked.
    Publication already requires canonical months; malformed forms are blocked
    upstream rather than guessed or paired by their position.
    """
    try:
        a, b = scientific_inventory(product_a), scientific_inventory(product_b)
        if a.grid_key != b.grid_key:
            raise ProductError("GRID_INCOMPATIBLE", "Spatial comparison requires the same verified grid; no regridding is performed.")
        months = tuple(sorted(set(a.months).intersection(b.months)))
        if not months:
            return MonthIntersection(code="NO_COMMON_MONTHS", message="These exact products have no actual months in common.")
        return MonthIntersection(months)
    except ProductError as error:
        return MonthIntersection(code=error.code, message=str(error))


def _pearson(pairs: list[tuple[int, float, float]]) -> tuple[float | None, str]:
    if len(pairs) < 2:
        return None, "insufficient_pairs"
    if (min(x for _, x, _ in pairs) == max(x for _, x, _ in pairs)
        or min(y for _, _, y in pairs) == max(y for _, _, y in pairs)):
        return None, "constant_field"
    x_scale = max(abs(x) for _, x, _ in pairs)
    y_scale = max(abs(y) for _, _, y in pairs)
    if x_scale == 0 or y_scale == 0:
        return None, "constant_field"
    xs = [x / x_scale for _, x, _ in pairs]
    ys = [y / y_scale for _, _, y in pairs]
    mx, my = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    dx, dy = [x - mx for x in xs], [y - my for y in ys]
    sx, sy = math.sqrt(math.fsum(x * x for x in dx)), math.sqrt(math.fsum(y * y for y in dy))
    if sx == 0 or sy == 0:
        return None, "zero_variance_or_numeric_resolution"
    r = math.fsum((x / sx) * (y / sy) for x, y in zip(dx, dy))
    if not math.isfinite(r) or abs(r) > 1.0 + 1e-12:
        return None, "nonfinite_result"
    # Only round-off outside the mathematical bound is clamped.
    return max(-1.0, min(1.0, r)), "computed"


def compare_products(request: ComparisonRequest) -> ComparisonResult:
    """Pair stable cells in an exact month and return bounded display samples.

    Complete valid pairs determine Pearson; evenly spaced positions in ascending
    canonical cell IDs determine only the display. Two pairs suffice for this
    descriptive coefficient. No p value, confidence interval, RMSE or causality
    claim is produced. All resource errors return structured unavailable states.
    """
    try:
        if (request.statistic != "spatial_pearson" or isinstance(request.display_limit, bool)
            or not isinstance(request.display_limit, int) or not 2 <= request.display_limit <= MAX_DISPLAY_POINTS):
            raise ProductError("SETTINGS_UNSUPPORTED", "Use spatial Pearson with a display limit from 2 to 1000.")
        a = read_scientific_month(request.product_a, request.month)
        b = read_scientific_month(request.product_b, request.month)
        if a.identity.grid_key != b.identity.grid_key or a.definition != b.definition:
            raise ProductError("GRID_INCOMPATIBLE", "The verified grids differ. Equal array sizes do not establish alignment.")
        if a.reference.identity != b.reference.identity:
            raise ProductError("REFERENCE_IDENTITY_MISMATCH", "The reference identities changed between reads. Retry the exact request after resources are stable.")
        a_ids, b_ids = set(region_cell_ids(a, request.region_key)), set(region_cell_ids(b, request.region_key))
        request_id = digest_bytes(canonical_json((a.identity.frame_id, b.identity.frame_id, request.month,
            request.region_key, REGION_VERSION, a.definition.coordinate_sha256, b.definition.coordinate_sha256,
            AREA_IDENTITY, request.statistic, request.display_limit, COMPARISON_VERSION)))
        # Fresh published bytes and references were checked before this lookup.
        with _CACHE_LOCK:
            cached = _CACHE.get(request_id)
            if cached is not None:
                _CACHE.move_to_end(request_id)
                return cached
        excluded = {"outside_a_mask": len(b_ids - a_ids), "outside_b_mask": len(a_ids - b_ids),
            "missing_a_only": 0, "missing_b_only": 0, "missing_both": 0}
        pairs: list[tuple[int, float, float]] = []
        for cell in sorted(a_ids & b_ids):
            x, y = a.values[cell], b.values[cell]
            valid_a = x is not None and math.isfinite(x)
            valid_b = y is not None and math.isfinite(y)
            if valid_a and valid_b:
                pairs.append((cell, x, y))
            else:
                excluded["missing_both" if not valid_a and not valid_b else "missing_a_only" if not valid_a else "missing_b_only"] += 1
        r, statistic_status = _pearson(pairs)
        count = min(len(pairs), request.display_limit)
        positions = range(len(pairs)) if count == len(pairs) else (
            index * (len(pairs) - 1) // (count - 1) for index in range(count)
        )
        points = tuple(DisplayPoint(*pairs[index]) for index in positions)
        provenance = tuple((f"{side} {label}", value) for side, frame in (("X", a), ("Y", b)) for label, value in frame_provenance(frame))
        result = ComparisonResult(request, "ready", "Same-month spatial mask intersection; statistics use every valid pair. Physical coverage is unknown.",
            request_id, len(pairs), len(points), r, statistic_status, points, tuple(excluded.items()),
            f"{a.variable.display_name} ({a.unit})", f"{b.variable.display_name} ({b.unit})",
            provenance + (("Algorithm", COMPARISON_VERSION), ("Display sampling", "Evenly spaced ascending grid_cell_id positions; endpoints included")))
        with _CACHE_LOCK:
            _CACHE[request_id] = result
            _CACHE.move_to_end(request_id)
            while len(_CACHE) > COMPARISON_CACHE_SIZE:
                _CACHE.popitem(last=False)
        return result
    except ProductError as error:
        return ComparisonResult(request, error.code, str(error))
    except Exception as error:
        logger.error("Spatial comparison unavailable (%s).", type(error).__name__)
        return ComparisonResult(request, "ANALYSIS_UNAVAILABLE", "The selected spatial comparison could not be read or calculated. Refresh after resources are resolved.")
