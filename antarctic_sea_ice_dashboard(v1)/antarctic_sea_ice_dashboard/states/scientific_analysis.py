"""Selection coordination for real Compare and Overview; science stays backend-owned."""

import asyncio
from typing import TypedDict

import reflex as rx
import reflex_xy as xy

from antarctic_sea_ice_dashboard.services.comparisons import ComparisonRequest, compare_products, comparison_months
from antarctic_sea_ice_dashboard.services.product_storage import ProductError
from antarctic_sea_ice_dashboard.services.region_statistics import REGION_KEY, OverviewRequest, load_overview
from antarctic_sea_ice_dashboard.services.scientific_data import ProductSelection, scientific_catalogue, scientific_inventory
from antarctic_sea_ice_dashboard.services.variables import UNAVAILABLE_VARIABLES


class AnalysisProductOption(TypedDict):
    product_id: str
    content_digest: str
    variable_key: str
    label: str


class AnalysisDetail(TypedDict):
    label: str
    value: str


class ScatterPoint(TypedDict):
    grid_cell_id: int
    x: float
    y: float


class ScientificAnalysis(rx.State):
    analysis_page: str = "Overview"
    products: list[AnalysisProductOption] = []
    catalogue_page: int = 0
    catalogue_has_more: bool = False
    catalogue_loading: bool = False
    catalogue_message: str = ""
    product_a_id: str = ""
    product_b_id: str = ""
    product_a_label: str = ""
    product_b_label: str = ""
    month: str = ""
    months: list[str] = []
    region_key: str = REGION_KEY
    loading: bool = False
    result_id: str = ""
    result_title: str = "Select explicit published products and an actual month."
    result_message: str = ""
    details: list[AnalysisDetail] = []
    exclusions: list[AnalysisDetail] = []
    scatter_points: list[ScatterPoint] = []
    x_label: str = "X variable"
    y_label: str = "Y variable"
    paired_cell_count: int = 0
    displayed_point_count: int = 0
    spatial_pearson_text: str = "Not computed"
    arithmetic_mean_text: str = "Unavailable"
    valid_cell_count: int = 0
    missing_cell_count: int = 0
    domain_cell_count: int = 0
    unavailable_variables: list[AnalysisDetail] = [
        {"label": label, "value": reason} for label, reason in UNAVAILABLE_VARIABLES.items()
    ]
    _product_a: ProductSelection | None = None
    _product_b: ProductSelection | None = None
    _generation: int = 0
    _catalogue_generation: int = 0
    _reader_active: bool = False
    _reader_epoch: int = 0
    _catalogue_active: bool = False
    _catalogue_epoch: int = 0

    @rx.var
    def selector_a(self) -> str:
        return self.product_a_id if any(p["product_id"] == self.product_a_id for p in self.products) else ""

    @rx.var
    def selector_b(self) -> str:
        return self.product_b_id if any(p["product_id"] == self.product_b_id for p in self.products) else ""

    @xy.data
    def paired_scatter(self) -> dict[str, list[float]]:
        return {"x": [point["x"] for point in self.scatter_points], "y": [point["y"] for point in self.scatter_points]}

    def _invalidate(self, clear_month: bool = False) -> None:
        self._generation += 1
        self.result_id = ""
        self.result_title = "Select explicit published products and an actual month."
        self.result_message = ""
        self.details = []
        self.exclusions = []
        self.scatter_points = []
        self.paired_cell_count = self.displayed_point_count = 0
        self.spatial_pearson_text = "Not computed"
        self.arithmetic_mean_text = "Unavailable"
        self.valid_cell_count = self.missing_cell_count = self.domain_cell_count = 0
        self.x_label, self.y_label = "X variable", "Y variable"
        self.loading = False
        if clear_month:
            self.month = ""
            self.months = []

    @rx.event
    def open_page(self, page: str):
        self.analysis_page = page
        self._invalidate(clear_month=True)
        self._catalogue_generation += 1
        self.catalogue_loading = False
        if page in ("Overview", "Compare"):
            return ScientificAnalysis.refresh_products

    @rx.event
    def refresh_products(self):
        self.products = []
        self.catalogue_message = ""
        self.catalogue_loading = True
        self._catalogue_generation += 1
        self._invalidate(clear_month=True)
        return [ScientificAnalysis.read_catalogue, ScientificAnalysis.read_analysis]

    @rx.event
    def change_catalogue_page(self, amount: int):
        if amount not in (-1, 1) or self.catalogue_loading:
            return
        if (amount < 0 and self.catalogue_page == 0) or (amount > 0 and not self.catalogue_has_more):
            return
        self.catalogue_page += amount
        return ScientificAnalysis.refresh_products

    @rx.event(background=True)
    async def read_catalogue(self) -> None:
        async with self:
            if self._catalogue_active:
                return
            self._catalogue_active = True
            self._catalogue_epoch += 1
            epoch = self._catalogue_epoch
        try:
            while True:
                async with self:
                    if self.analysis_page not in ("Overview", "Compare"):
                        self._catalogue_active = False
                        return
                    generation, page = self._catalogue_generation, self.catalogue_page
                catalogue = await asyncio.to_thread(scientific_catalogue, page)
                async with self:
                    if generation != self._catalogue_generation:
                        continue
                    self.products = [{"product_id": p.product_id, "content_digest": p.content_digest,
                        "variable_key": p.variable_key, "label": p.label} for p in catalogue.products]
                    self.catalogue_has_more = catalogue.has_more
                    self.catalogue_message = (f"{catalogue.code}: {catalogue.message}" if catalogue.code else
                        catalogue.message or ("" if catalogue.products else "No eligible published products on this page. No observations are generated."))
                    self.catalogue_loading = False
                    self._catalogue_active = False
                    return
        finally:
            async with self:
                if epoch == self._catalogue_epoch:
                    self._catalogue_active = False

    def _select_product(self, product_id: str, side: str) -> None:
        option = next((p for p in self.products if p["product_id"] == product_id), None)
        if option is None:
            return
        selection = ProductSelection(option["product_id"], option["content_digest"], option["variable_key"])
        if side == "a":
            self._product_a = selection
            self.product_a_id, self.product_a_label = product_id, option["label"]
        else:
            self._product_b = selection
            self.product_b_id, self.product_b_label = product_id, option["label"]
        self._invalidate(clear_month=True)

    @rx.event
    def select_product_a(self, product_id: str):
        self._select_product(product_id, "a")
        return ScientificAnalysis.read_analysis

    @rx.event
    def select_product_b(self, product_id: str):
        self._select_product(product_id, "b")
        return ScientificAnalysis.read_analysis

    @rx.event
    def select_month(self, month: str):
        if month not in self.months:
            return
        self.month = month
        self._invalidate()
        return ScientificAnalysis.read_analysis

    @rx.event
    def select_region(self, region_key: str):
        self.region_key = region_key
        self._invalidate()
        return ScientificAnalysis.read_analysis

    @rx.event(background=True)
    async def read_analysis(self) -> None:
        async with self:
            if self._reader_active:
                return
            self._reader_active = True
            self._reader_epoch += 1
            epoch = self._reader_epoch
        try:
            while True:
                async with self:
                    page = self.analysis_page
                    if page not in ("Overview", "Compare") or self._product_a is None or (page == "Compare" and self._product_b is None):
                        self.loading = False
                        self._reader_active = False
                        return
                    generation = self._generation
                    a, b, month, region = self._product_a, self._product_b, self.month, self.region_key
                    self.loading = True
                try:
                    if page == "Compare":
                        inventory = await asyncio.to_thread(comparison_months, a, b)
                        if inventory.code:
                            raise ProductError(inventory.code, inventory.message)
                        months = list(inventory.months)
                    else:
                        inventory = await asyncio.to_thread(scientific_inventory, a)
                        months = list(inventory.months)
                    selected_month = month or months[0]
                    if selected_month not in months:
                        raise ProductError("MONTH_NOT_AVAILABLE", "The selected month is absent from this exact request's actual inventory.")
                    if page == "Compare":
                        result = await asyncio.to_thread(compare_products, ComparisonRequest(a, b, selected_month, region))
                    else:
                        result = await asyncio.to_thread(load_overview, OverviewRequest(a, selected_month, region))
                    error_message = ""
                except ProductError as error:
                    result = None
                    months = []
                    error_message = f"{error.code}: {error}"
                async with self:
                    if generation != self._generation:
                        continue
                    self.loading = False
                    self.months = months
                    if result is None:
                        self.result_message = error_message
                    elif not result.request_id:
                        self.result_message = f"{result.code}: {result.message}"
                    else:
                        self.month = selected_month
                        self.result_id = result.request_id
                        self.result_title = f"{page} · {selected_month} · {region}"
                        self.result_message = result.message
                        self.details = [{"label": label, "value": value} for label, value in result.provenance]
                        if page == "Compare":
                            self.paired_cell_count, self.displayed_point_count = result.paired_cell_count, result.displayed_point_count
                            self.spatial_pearson_text = f"{result.spatial_pearson_r:.6g}" if result.spatial_pearson_r is not None else f"Not computable: {result.statistic_status}"
                            self.x_label, self.y_label = result.x_label, result.y_label
                            self.scatter_points = [{"grid_cell_id": point.grid_cell_id, "x": point.x, "y": point.y} for point in result.points]
                            self.exclusions = [{"label": label, "value": str(value)} for label, value in result.excluded_counts]
                        else:
                            self.arithmetic_mean_text = (f"{result.arithmetic_mean:+.6g} {result.display_unit}"
                                if result.arithmetic_mean is not None else "Not computable for this valid sample set")
                            self.valid_cell_count, self.missing_cell_count = result.valid_cell_count, result.missing_cell_count
                            self.domain_cell_count = result.domain_cell_count
                    self._reader_active = False
                    return
        finally:
            async with self:
                if epoch == self._reader_epoch:
                    self._reader_active = False
