"""Versioned retained-domain statistics with explicit physical-area blocks."""

from dataclasses import dataclass
import math
import logging

from antarctic_sea_ice_dashboard.services.csv_validation import RULESET_VERSION
from antarctic_sea_ice_dashboard.services.product_storage import ProductError, canonical_json, digest_bytes
from antarctic_sea_ice_dashboard.services.scientific_data import ProductSelection, ScientificMonth, read_scientific_month


logger = logging.getLogger(__name__)
REGION_KEY = "published_retained_domain_v1"
REGION_LABEL = "Published retained domain (not the whole Southern Ocean)"
REGION_VERSION = "retained-domain-1.0.0"
STATISTICS_VERSION = "retained-arithmetic-1.0.0"
AREA_IDENTITY = "unavailable-no-approved-physical-area-contract"


@dataclass(frozen=True)
class OverviewRequest:
    product: ProductSelection
    month: str
    region_key: str = REGION_KEY


@dataclass(frozen=True)
class OverviewResult:
    request: OverviewRequest
    code: str
    message: str
    request_id: str = ""
    arithmetic_mean: float | None = None
    valid_cell_count: int = 0
    missing_cell_count: int = 0
    domain_cell_count: int = 0
    valid_area: float | None = None
    coverage_fraction: float | None = None
    provenance: tuple[tuple[str, str], ...] = ()
    display_unit: str = ""


def region_cell_ids(frame: ScientificMonth, region_key: str) -> tuple[int, ...]:
    """Resolve the sole approved domain from its verified reference mask.

    Named ocean regions require separate versioned masks and remain unavailable.
    Grid-cell counts describe this retained domain, not physical area coverage.
    """
    if region_key != REGION_KEY:
        raise ProductError("REGION_UNAVAILABLE", "This region has no approved versioned mask. Select the published retained domain.")
    return frame.reference.grid_cell_ids


def frame_provenance(frame: ScientificMonth) -> tuple[tuple[str, str], ...]:
    """Describe the exact backend frame without exposing arrays or paths."""
    return (
        ("Product", frame.identity.product_id), ("Content", frame.identity.content_digest),
        ("Manifest", frame.identity.manifest_digest), ("Month", frame.identity.month),
        ("Variable", frame.variable.variable_key), ("Original unit", frame.original_unit),
        ("Internal unit", frame.variable.internal_unit), ("Display unit", frame.unit),
        ("Conversion", frame.variable.conversion_version),
        ("Ruleset", RULESET_VERSION),
        ("Profile", f"{frame.variable.profile_key} / {frame.variable.profile_version}"),
        ("Grid", frame.identity.grid_key), ("Mask", frame.definition.coordinate_sha256),
        ("Reference", str(frame.reference.identity.get("file_sha256"))),
        ("Region", f"{REGION_KEY} / {REGION_VERSION}"), ("Source", frame.source),
        ("Missing policy", frame.variable.missing_policy), ("Area identity", AREA_IDENTITY),
    )


def arithmetic_mean(values: list[float]) -> float | None:
    """Finite arithmetic sample mean; scale first to avoid finite sum overflow."""
    if not values:
        return None
    scale = max(abs(value) for value in values)
    mean = scale * (math.fsum(value / scale / len(values) for value in values) if scale else 0.0)
    return mean if math.isfinite(mean) else None


def load_overview(request: OverviewRequest) -> OverviewResult:
    """Return one exact-month scalar summary, or a path-free ProductError state.

    No physical-area source is approved. Area/coverage remain None independently
    of the available arithmetic mean. There is no cross-month interpolation.
    """
    try:
        frame = read_scientific_month(request.product, request.month)
        cell_ids = region_cell_ids(frame, request.region_key)
        values = [frame.values[cell] for cell in cell_ids if frame.values[cell] is not None and math.isfinite(frame.values[cell])]
        mean = arithmetic_mean(values)
        request_id = digest_bytes(canonical_json((frame.identity.frame_id, request.region_key,
            REGION_VERSION, frame.definition.coordinate_sha256, AREA_IDENTITY, STATISTICS_VERSION)))
        return OverviewResult(request, "ready" if mean is not None else "MEAN_UNAVAILABLE",
            "Arithmetic mean of valid retained cells; no area weighting. Physical coverage is unknown."
            if mean is not None else "There is no finite computable arithmetic mean for this request.",
            request_id, mean, len(values), len(cell_ids) - len(values), len(cell_ids),
            provenance=frame_provenance(frame) + (("Algorithm", STATISTICS_VERSION),), display_unit=frame.unit)
    except ProductError as error:
        return OverviewResult(request, error.code, str(error))
    except Exception as error:
        logger.error("Overview calculation unavailable (%s).", type(error).__name__)
        return OverviewResult(request, "ANALYSIS_UNAVAILABLE", "The exact-month summary could not be read or calculated. Refresh after resources are resolved.")
