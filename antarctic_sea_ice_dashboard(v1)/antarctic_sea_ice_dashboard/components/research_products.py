"""Publication and controlled registration forms in the existing Data style."""

import reflex as rx

from antarctic_sea_ice_dashboard.components.uploaded_data import BUTTON, INPUT, PRIMARY
from antarctic_sea_ice_dashboard.states.data_quality import DataQuality as Q
from antarctic_sea_ice_dashboard.states.research_products import ProductCard, ProductOperationView, ResearchProducts as P, SourceOption
from antarctic_sea_ice_dashboard.states.uploaded_data import UploadedData as D


TEXT = "text-xs leading-relaxed text-slate-600"
PANEL = "mb-6 min-w-0 rounded-lg border border-slate-200 bg-white p-4"


def message(value: rx.Var[str], *, error: bool = False) -> rx.Component:
    return rx.cond(value != "", rx.el.p(value, role="alert" if error else "status", class_name="mt-3 whitespace-pre-wrap break-words text-xs " + ("text-red-700" if error else "text-slate-700")))


def publication_control() -> rx.Component:
    """The latest quality view is a convenience; backend gates are authoritative."""
    return rx.el.div(
        rx.el.h3("Publish immutable CSV revision", class_name="text-sm font-semibold text-slate-800"),
        rx.el.p("Publication preserves saved strings and column order. It requires a current complete pass and available grid evidence; later working edits do not change the product.", class_name="mt-1 " + TEXT),
        rx.el.button(
            "Publish current revision",
            on_click=P.publish_current(D.selected_id, D.data_revision, Q.start_token),
            disabled=P.busy | D.busy | D.refresh_required | Q.loading | Q.run_busy | Q.workspace_outdated
            | (Q.latest_status != "passed") | (Q.read_error != "") | (Q.dataset_id != D.selected_id)
            | (Q.workspace_revision != D.data_revision),
            class_name="mt-3 " + PRIMARY,
        ),
        class_name="mt-5 border-t border-slate-200 pt-4",
    )


def operation_result() -> rx.Component:
    return rx.el.div(
        rx.el.h3("Saved product operation", class_name="text-sm font-semibold text-slate-800"),
        rx.el.label("Product ID", rx.el.input(value=P.operation_id, on_change=P.change_operation_id, placeholder="Saved product identity", disabled=P.busy, class_name="mt-1 " + INPUT), class_name="mt-3 block " + TEXT),
        rx.el.div(
            rx.el.button("Read saved operation", on_click=P.read_operation(False), disabled=P.busy | (P.operation_id == ""), class_name=BUTTON),
            rx.el.button("Recover prepared operation", on_click=P.read_operation(True), disabled=P.busy | (P.operation_id == ""), class_name=BUTTON),
            rx.el.button("Find last CSV request", on_click=P.find_last_csv_request, disabled=P.busy | (P.last_csv_request == ""), class_name=BUTTON),
            rx.cond(P.operation_status == "published", rx.el.button("Read this product manifest", on_click=P.show_manifest(P.operation_id), disabled=P.busy, class_name=BUTTON)),
            class_name="mt-3 flex flex-wrap gap-2",
        ),
        message(P.operation_status), message(P.operation_message), message(P.operation_error, error=True),
        message(P.last_csv_request),
        rx.foreach(P.found_operations, operation_option),
        rx.foreach(P.block_reasons, lambda reason: rx.el.p(reason, class_name="mt-1 text-xs text-amber-800")),
        rx.cond(P.busy, rx.el.p("Backend product request in progress…", role="status", class_name="mt-3 " + TEXT)),
        class_name="mt-5 border-t border-slate-200 pt-4",
    )


def operation_option(item: ProductOperationView) -> rx.Component:
    return rx.el.button(f"{item['product_id']} · {item['status']}", on_click=P.change_operation_id(item["product_id"]), disabled=P.busy, class_name="mt-2 break-all " + BUTTON)


def source_registration() -> rx.Component:
    return rx.el.section(
        rx.el.h2("Register an existing checking data product", class_name="text-xl font-medium text-[#172e40]"),
        rx.el.p("Select a configured source and explicit months. Only those monthly CSVs and bounded evidence files are copied. Historical reports currently produce blocked registrations while scientific provenance and method acceptance remain incomplete.", class_name="mt-1 " + TEXT),
        rx.el.label("Source product", rx.el.select(
            rx.foreach(P.source_options, lambda item: source_option(item)),
            value=P.source_id, on_change=P.change_source, disabled=P.busy, class_name="mt-1 " + INPUT,
        ), class_name="mt-3 block " + TEXT),
        rx.el.button("List actual source months", on_click=P.load_source_months, disabled=P.busy | P.source_loading, class_name="mt-3 " + BUTTON),
        message(P.source_months),
        rx.el.label("Months to register (1 to 12, comma separated YYYY-MM)", rx.el.input(value=P.requested_months, on_change=P.change_months, disabled=P.busy, placeholder="1980-01, 1980-02", class_name="mt-1 " + INPUT), class_name="mt-3 block " + TEXT),
        rx.el.div(
            rx.el.button("Inspect selected scope", on_click=P.inspect_source, disabled=P.busy | P.source_loading, class_name=BUTTON),
            rx.el.button("Copy and register selected scope", on_click=P.register_selected, disabled=P.busy | P.source_loading | (P.inventory == ""), class_name=PRIMARY),
            class_name="mt-3 flex flex-wrap gap-2",
        ),
        message(P.inventory), message(P.source_error, error=True), operation_result(),
        class_name=PANEL,
    )


def source_option(item: SourceOption) -> rx.Component:
    return rx.el.option(item["label"], value=item["source_id"])


def product_card(item: ProductCard) -> rx.Component:
    return rx.el.article(
        rx.el.h3(item["product_id"], class_name="break-all text-xs font-semibold text-slate-800"),
        rx.el.p(f"{item['variable']} · {item['unit']} · integrity: {item['integrity']}", class_name="mt-1 " + TEXT),
        rx.el.p(f"Actual months: {item['months']}", class_name="mt-1 " + TEXT),
        rx.el.p(f"Grid: {item['grid']} · source: {item['source']}", class_name="mt-1 break-words " + TEXT),
        rx.el.button("Read verified manifest", on_click=P.show_manifest(item["product_id"]), disabled=item["integrity"] != "verified", class_name="mt-3 " + BUTTON),
        class_name="rounded-md border border-slate-200 p-3",
    )


def product_catalogue() -> rx.Component:
    return rx.el.section(
        rx.el.h2("Published research products", class_name="text-xl font-medium text-[#172e40]"),
        rx.el.p("Only committed publications appear here. Integrity is verified from stored bytes on each read; unavailable products retain their identity and cannot be consumed. Eligible products can be selected in Map Explorer, Compare and Overview.", class_name="mt-1 " + TEXT),
        rx.el.div(
            rx.el.button("Refresh product catalogue", on_click=P.refresh_catalogue, disabled=P.catalogue_loading, class_name=BUTTON),
            rx.el.button("Previous", on_click=P.change_catalogue_page(-1), disabled=P.catalogue_loading | (P.catalogue_page == 0), class_name=BUTTON),
            rx.el.button("Next", on_click=P.change_catalogue_page(1), disabled=P.catalogue_loading | ~P.has_more, class_name=BUTTON),
            class_name="mt-3 flex flex-wrap gap-2",
        ),
        message(P.catalogue_error, error=True),
        rx.cond(P.products.length() == 0, rx.el.p("No published products are shown on this page.", class_name="mt-3 " + TEXT)),
        rx.el.div(rx.foreach(P.products, product_card), class_name="mt-3 grid gap-3 md:grid-cols-2"),
        message(P.manifest_error, error=True),
        rx.cond(P.manifest_text != "", rx.el.details(rx.el.summary(f"Manifest · {P.manifest_product_id}", class_name="cursor-pointer text-xs font-medium"), rx.el.pre(P.manifest_text, class_name="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words text-[11px]"), open=True, class_name="mt-4")),
        on_mount=P.refresh_catalogue, class_name=PANEL,
    )
