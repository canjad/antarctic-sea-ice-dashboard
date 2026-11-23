"""Data-page publication requests and bounded, read-only product catalogue."""

import asyncio
from collections.abc import AsyncIterator
import json
import logging
from typing import TypedDict

import reflex as rx

from antarctic_sea_ice_dashboard.services.product_contracts import ProductOperation
from antarctic_sea_ice_dashboard.services.product_storage import ProductError
from antarctic_sea_ice_dashboard.services.research_products import (
    find_csv_product_operations, get_product_operation, list_available_products, publish_csv_revision,
    read_product_manifest, recover_product, register_source_product,
)
from antarctic_sea_ice_dashboard.services.source_products import (
    SOURCES, available_source_months, inspect_source_product,
)
from antarctic_sea_ice_dashboard.states.data_quality import DataQuality
from antarctic_sea_ice_dashboard.states.explorer import Explorer
from antarctic_sea_ice_dashboard.states.uploaded_data import UploadedData


logger = logging.getLogger(__name__)
CATALOGUE_PAGE_SIZE = 10


class ProductCard(TypedDict):
    product_id: str
    variable: str
    unit: str
    months: str
    grid: str
    source: str
    integrity: str


class SourceOption(TypedDict):
    source_id: str
    label: str


class ProductOperationView(TypedDict):
    product_id: str
    status: str


def _safe_error(error: Exception, fallback: str) -> str:
    logger.error("Product page operation failed (%s).", type(error).__name__)
    return str(error) if isinstance(error, ProductError) else fallback


class ResearchProducts(rx.State):
    source_options: list[SourceOption] = [{"source_id": item.source_id, "label": item.label} for item in SOURCES]
    source_id: str = SOURCES[0].source_id
    requested_months: str = ""
    source_months: str = ""
    inventory: str = ""
    source_error: str = ""
    operation_id: str = ""
    operation_status: str = ""
    operation_message: str = ""
    operation_error: str = ""
    last_csv_request: str = ""
    found_operations: list[ProductOperationView] = []
    block_reasons: list[str] = []
    busy: bool = False
    source_loading: bool = False
    catalogue_loading: bool = False
    catalogue_error: str = ""
    products: list[ProductCard] = []
    catalogue_page: int = 0
    has_more: bool = False
    manifest_product_id: str = ""
    manifest_text: str = ""
    manifest_error: str = ""
    _source_generation: int = 0
    _catalogue_generation: int = 0
    _manifest_generation: int = 0
    _inspected_scope: tuple[str, tuple[str, ...]] | None = None
    _last_csv_target: tuple[str, int] | None = None

    def _apply_operation(self, operation: ProductOperation) -> None:
        self.operation_id = operation.product_id
        self.operation_status = operation.publication_status
        self.block_reasons = list(operation.block_reasons)
        self.operation_message = {
            "published": "Publication committed. Use Read saved operation or the product catalogue to reopen this exact product.",
            "blocked": "The selected source files and evidence were registered. Scientific evidence is incomplete; this registration is excluded from the published catalogue.",
            "preparing": "The journal records preparation. Read status before explicitly recovering or repeating the original request.",
            "publishing": "Files were prepared. Recover the operation to verify final placement and eligibility before visibility.",
            "failed": "Publication did not finish. Recover this operation; interrupted preparation requires repeating the original scoped request.",
        }[operation.publication_status]

    def _begin_request(self) -> None:
        self.busy = True
        self.operation_id = ""
        self.operation_status = ""
        self.operation_message = ""
        self.operation_error = ""
        self.block_reasons = []

    @rx.event
    def change_source(self, value: str) -> None:
        if self.busy or value not in {item.source_id for item in SOURCES}:
            return
        self.source_id = value
        self.requested_months = ""
        self.source_months = ""
        self.inventory = ""
        self.source_error = ""
        self.source_loading = False
        self._source_generation += 1
        self._inspected_scope = None

    @rx.event
    def change_months(self, value: str) -> None:
        if not self.busy:
            self.requested_months = value[:200]
            self._inspected_scope = None
            self.inventory = ""
            self._source_generation += 1
            self.source_loading = False

    @rx.event
    def change_operation_id(self, value: str) -> None:
        if not self.busy:
            self.operation_id = value[:32]
            self.operation_status = ""
            self.operation_message = ""
            self.block_reasons = []

    @rx.event(background=True)
    async def load_source_months(self) -> None:
        async with self:
            if self.source_loading or self.busy:
                return
            source_id = self.source_id
            generation = self._source_generation
            self.source_loading = True
            self.source_error = ""
        try:
            months = await asyncio.to_thread(available_source_months, source_id)
            message = ", ".join(months) or "No actual monthly CSV files were found for this source."
            failure = ""
        except Exception as error:
            message = ""
            failure = _safe_error(error, "The configured source could not be inventoried. Ask the operator to check its read-only configuration.")
        async with self:
            if generation == self._source_generation:
                self.source_months = message
                self.source_error = failure
                self.source_loading = False

    @rx.event(background=True)
    async def inspect_source(self) -> None:
        async with self:
            if self.source_loading or self.busy:
                return
            scope = (self.source_id, tuple(item.strip() for item in self.requested_months.split(",") if item.strip()))
            generation = self._source_generation
            self.source_loading = True
            self.source_error = ""
            self._inspected_scope = None
        try:
            inventory = await asyncio.to_thread(inspect_source_product, *scope)
            details = f"Source: {inventory.source_id}\nVariable: {inventory.variable_key}\nUnit: {inventory.unit}\nMonths: {', '.join(inventory.months)}\nGrid: {inventory.grid_key or 'Unaccepted native grid'}\nMethod: {inventory.method}\nSelected files: {len(inventory.files)}\nBytes: {sum(item.size_bytes for item in inventory.files)}\nBlocking gaps: {', '.join(inventory.gaps)}"
            failure = ""
        except Exception as error:
            details = ""
            failure = _safe_error(error, "The selected source scope could not be inspected. No files were copied.")
        async with self:
            if generation == self._source_generation:
                self.inventory = details
                self.source_error = failure
                self.source_loading = False
                if not failure:
                    self._inspected_scope = scope

    @rx.event(background=True)
    async def publish_current(self, dataset_id: int, revision: int, quality_token: str) -> AsyncIterator[object]:
        async with self:
            uploaded = await self.get_state(UploadedData)
            quality = await self.get_state(DataQuality)
            explorer = await self.get_state(Explorer)
            if (self.busy or explorer.page != "Data" or uploaded.busy or uploaded.refresh_required
                or (dataset_id, revision) != (uploaded.selected_id, uploaded.data_revision)
                or not quality_token or quality_token != quality.start_token
                or quality._identity_key != uploaded._selected_identity_key):
                return
            identity = uploaded._selected_identity_key
            self._begin_request()
            self._last_csv_target = (identity, revision)
            self.last_csv_request = f"Dataset {dataset_id}, revision {revision}"
            self.found_operations = []
        try:
            operation = await asyncio.to_thread(publish_csv_revision, dataset_id, revision, expected_identity_key=identity)
            async with self:
                self._apply_operation(operation)
        except Exception as error:
            async with self:
                self.operation_id = error.product_id or "" if isinstance(error, ProductError) else ""
                self.operation_error = _safe_error(error, "Publication response was unavailable. Read the saved operation or retry the same revision to locate its journal entry.")
        finally:
            async with self:
                self.busy = False
        yield ResearchProducts.refresh_catalogue

    @rx.event(background=True)
    async def find_last_csv_request(self) -> None:
        async with self:
            if self.busy or self._last_csv_target is None:
                return
            target = self._last_csv_target
            self.busy = True
            self.operation_error = ""
        try:
            operations = await asyncio.to_thread(find_csv_product_operations, *target)
            async with self:
                self.found_operations = [{"product_id": item.product_id, "status": item.publication_status} for item in operations]
                if not operations:
                    self.operation_error = "No accepted journal entry was found for the last CSV request. No publication is inferred."
        except Exception as error:
            async with self:
                self.operation_error = _safe_error(error, "Could not find the last CSV request. Its source identity remains saved in this page session; retry the lookup.")
        finally:
            async with self:
                self.busy = False

    @rx.event(background=True)
    async def register_selected(self) -> AsyncIterator[object]:
        async with self:
            if self.busy or self._inspected_scope is None:
                return
            scope = self._inspected_scope
            self._begin_request()
        try:
            # The backend re-inventories and hashes the scope. Inspection never
            # serves as authority for a subsequent copy or scientific pass.
            operation = await asyncio.to_thread(register_source_product, *scope)
            async with self:
                self._apply_operation(operation)
        except Exception as error:
            async with self:
                self.operation_id = error.product_id or "" if isinstance(error, ProductError) else ""
                self.operation_error = _safe_error(error, "Source registration did not return a result. Read its saved operation before retrying.")
        finally:
            async with self:
                self.busy = False
        yield ResearchProducts.refresh_catalogue

    @rx.event(background=True)
    async def read_operation(self, recover: bool = False) -> AsyncIterator[object]:
        async with self:
            if self.busy:
                return
            product_id = self.operation_id
            self.busy = True
            self.operation_error = ""
        try:
            operation = await asyncio.to_thread(recover_product if recover else get_product_operation, product_id)
            async with self:
                self._apply_operation(operation)
        except Exception as error:
            async with self:
                self.operation_error = _safe_error(error, "The saved operation is unavailable. Keep its product ID and retry the status read.")
        finally:
            async with self:
                self.busy = False
        yield ResearchProducts.refresh_catalogue

    @rx.event(background=True)
    async def refresh_catalogue(self) -> None:
        async with self:
            self._catalogue_generation += 1
            generation = self._catalogue_generation
            page = self.catalogue_page
            self.catalogue_loading = True
            self.catalogue_error = ""
        try:
            items = await asyncio.to_thread(list_available_products, offset=page * CATALOGUE_PAGE_SIZE, limit=CATALOGUE_PAGE_SIZE + 1)
            products: list[ProductCard] = [{
                "product_id": item.product_id, "variable": item.variable_key, "unit": item.unit,
                "months": ", ".join(item.months), "grid": item.grid_key,
                "source": item.source, "integrity": item.integrity_status,
            } for item in items[:CATALOGUE_PAGE_SIZE]]
            failure = ""
        except Exception as error:
            items = ()
            products = []
            failure = _safe_error(error, "The product catalogue could not refresh. A completed publication remains committed; read its saved operation above.")
        async with self:
            if generation == self._catalogue_generation:
                self.products = products
                self.has_more = len(items) > CATALOGUE_PAGE_SIZE
                self.catalogue_error = failure
                self.catalogue_loading = False

    @rx.event
    def change_catalogue_page(self, delta: int) -> object:
        if self.catalogue_loading or delta not in {-1, 1} or (delta == 1 and not self.has_more):
            return None
        self.catalogue_page = max(0, self.catalogue_page + delta)
        return ResearchProducts.refresh_catalogue

    @rx.event(background=True)
    async def show_manifest(self, product_id: str) -> None:
        async with self:
            self._manifest_generation += 1
            generation = self._manifest_generation
            self.manifest_product_id = product_id
            self.manifest_text = ""
            self.manifest_error = ""
        try:
            manifest = await asyncio.to_thread(read_product_manifest, product_id)
            text = json.dumps(manifest, ensure_ascii=True, allow_nan=False, indent=2)
            failure = ""
        except Exception as error:
            text = ""
            failure = _safe_error(error, "This exact product is unavailable. Its files may be missing or modified; no other version was selected.")
        async with self:
            if generation == self._manifest_generation:
                self.manifest_text = text
                self.manifest_error = failure
