"""State shared by source discovery and product publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np


@dataclass
class SourceCatalog:
    sources: list[dict]
    excluded: list[dict]
    records: dict[str, list[tuple[dict, int]]]
    months: list[str]
    latitude: np.ndarray
    longitude: np.ndarray
    coordinates: np.ndarray


@dataclass(frozen=True)
class WeightBuildOptions:
    weights_dir: Path
    reference_dir: Path
    arc_subdivisions: int = 8
    geodesic_segment_m: float = 2500.0


@dataclass(frozen=True)
class RemapOptions:
    output_dir: Path
    weights_dir: Path
    reference_dir: Path
    mode: Literal["sample", "full"]
    months: tuple[str, ...] = ()

    @property
    def full(self) -> bool:
        return self.mode == "full"

    @property
    def sample(self) -> bool:
        return self.mode == "sample"
