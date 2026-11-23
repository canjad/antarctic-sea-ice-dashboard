"""Real published-product Compare and Overview controls and result views."""

import reflex as rx
import reflex_xy as xy

from antarctic_sea_ice_dashboard.services.region_statistics import REGION_KEY, REGION_LABEL
from antarctic_sea_ice_dashboard.states.scientific_analysis import ScientificAnalysis as A


BUTTON = "min-h-9 rounded border border-slate-300 px-3 text-xs hover:bg-slate-50 disabled:opacity-50"
PANEL = "rounded-lg border border-slate-200 bg-white p-5"
INPUT = "mt-2 block w-full min-h-10 rounded border border-slate-300 bg-white px-2 text-xs"


def product_selector(side: str) -> rx.Component:
    return rx.el.label(
        "X / Overview product" if side == "a" else "Y product",
        rx.el.select(
            rx.el.option("Select an eligible published product", value=""),
            rx.foreach(A.products, lambda p: rx.el.option(p["label"], value=p["product_id"])),
            value=A.selector_a if side == "a" else A.selector_b,
            aria_label="X / Overview product" if side == "a" else "Y product",
            on_change=A.select_product_a if side == "a" else A.select_product_b,
            class_name=INPUT,
        ),
        rx.el.span(A.product_a_label if side == "a" else A.product_b_label,
            class_name="mt-2 block break-all text-slate-500"),
        class_name="block text-xs min-w-0",
    )


def analysis_controls(compare: bool) -> rx.Component:
    return rx.el.div(
        rx.el.div(
            product_selector("a"),
            product_selector("b") if compare else rx.fragment(),
            class_name="grid grid-cols-1 lg:grid-cols-2 gap-4",
        ),
        rx.el.div(
            rx.el.label("Actual common month" if compare else "Actual product month",
                rx.el.select(
                    rx.cond(A.months.length() == 0, rx.el.option("No available months", value="")),
                    rx.foreach(A.months, lambda month: rx.el.option(month, value=month)),
                    value=A.month, on_change=A.select_month, disabled=A.months.length() == 0, class_name=INPUT,
                    aria_label="Actual common month" if compare else "Actual product month",
                ), class_name="text-xs"),
            rx.el.label("Versioned region",
                rx.el.select(rx.el.option(REGION_LABEL, value=REGION_KEY), value=A.region_key,
                    on_change=A.select_region, class_name=INPUT, aria_label="Versioned region"), class_name="text-xs"),
            class_name="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4",
        ),
        rx.el.p("Only actual YYYY-MM months are offered; gaps are preserved. Named ocean regions are unavailable until versioned masks are approved.", class_name="mt-3 text-xs text-slate-500"),
        rx.el.div(
            rx.el.button("Refresh exact products", on_click=A.refresh_products, class_name=BUTTON),
            rx.el.button("Previous catalogue page", on_click=A.change_catalogue_page(-1),
                disabled=A.catalogue_loading | (A.catalogue_page == 0), class_name=BUTTON),
            rx.el.button("Next catalogue page", on_click=A.change_catalogue_page(1),
                disabled=A.catalogue_loading | ~A.catalogue_has_more, class_name=BUTTON),
            class_name="mt-3 flex flex-wrap gap-2",
        ),
        rx.el.p(f"Catalogue page {A.catalogue_page + 1}. Products on other pages remain pinned by their explicit identity above.", class_name="mt-2 text-xs text-slate-500"),
        rx.cond(A.catalogue_loading, rx.el.p("Reading published catalogue…", role="status", class_name="mt-2 text-xs")),
        rx.cond(A.catalogue_message != "", rx.el.p(A.catalogue_message, role="status", class_name="mt-2 text-xs text-amber-800")),
        rx.el.details(
            rx.el.summary("Unavailable candidate variables", class_name="cursor-pointer text-xs"),
            rx.foreach(A.unavailable_variables, lambda item: rx.el.p(f"{item['label']}: {item['value']}", class_name="mt-2 text-xs text-slate-600")),
            class_name="mt-4",
        ),
        class_name=PANEL,
    )


def provenance() -> rx.Component:
    return rx.cond(A.result_id != "", rx.el.details(
        rx.el.summary("Product, unit, mask and algorithm provenance", class_name="cursor-pointer text-xs font-semibold"),
        rx.el.p(f"Request identity: {A.result_id}", class_name="mt-2 text-xs break-all"),
        rx.el.dl(rx.foreach(A.details, lambda item: rx.el.div(
            rx.el.dt(item["label"], class_name="text-slate-500"),
            rx.el.dd(item["value"], class_name="break-all"), class_name="mt-2 text-xs",
        ))), class_name=PANEL + " mt-4",
    ))


def result_status() -> rx.Component:
    return rx.el.div(
        rx.el.h2(A.result_title, class_name="font-semibold text-sm"),
        rx.cond(A.loading, rx.el.p("Reading this exact request…", role="status", class_name="mt-2 text-xs")),
        rx.el.p(A.result_message, role="status", class_name="mt-2 text-xs text-slate-600"),
        class_name="mt-4 mb-4",
    )


def unavailable_metric(label: str, reason: str) -> rx.Component:
    return rx.el.div(rx.el.h3(label, class_name="text-sm font-semibold"),
        rx.el.p("Unavailable", class_name="mt-2 text-slate-500"),
        rx.el.p(reason, class_name="mt-2 text-xs text-slate-500"), class_name=PANEL)


def scientific_overview() -> rx.Component:
    return rx.el.div(
        analysis_controls(False), result_status(),
        rx.cond(A.result_id != "", rx.el.div(
            rx.el.h3("Valid-sample arithmetic mean", class_name="text-sm font-semibold"),
            rx.el.p(A.arithmetic_mean_text, class_name="mt-2 text-2xl text-sky-900"),
            rx.el.p(f"Valid cells: {A.valid_cell_count} · Missing cells: {A.missing_cell_count} · Retained-domain cells: {A.domain_cell_count}", class_name="mt-2 text-xs"),
            rx.el.p("Signed northward component; not wind-speed magnitude. Each valid sample has equal arithmetic weight. No physical-area interpretation is attached to this mean or the cell counts.", class_name="mt-2 text-xs text-slate-500"),
            class_name=PANEL + " mb-4",
        )),
        rx.el.div(
            unavailable_metric("Area-weighted mean / valid area / coverage fraction", "AREA_UNAVAILABLE: No approved physical cell-area identity or full-region area denominator is registered. Projected 25 km spacing is not a physical cell area."),
            unavailable_metric("Mean sea ice concentration / mean SST", "VARIABLE_UNAVAILABLE: Approved variable products, units, missing-value policies and publication profiles are absent."),
            unavailable_metric("Sea ice area", "Requires approved concentration normalised to [0, 1] and matched physical cell areas. Neither resource contract is available."),
            unavailable_metric("Sea ice extent / Marginal Ice Zone", "Requires approved concentration, versioned valid-domain mask, physical areas and an explicit threshold policy. Thresholds remain undecided."),
            unavailable_metric("Wind speed", "Only the signed northward wind component is supported. A complete wind-speed magnitude requires additional approved component data."),
            unavailable_metric("Cross-month trend", "No comparable-coverage trend policy is approved. The selected exact-month summary preserves inventory gaps and does not interpolate a series."),
            class_name="grid grid-cols-1 lg:grid-cols-2 gap-4",
        ), provenance(),
    )


def scientific_compare() -> rx.Component:
    return rx.el.div(
        analysis_controls(True), result_status(),
        rx.cond(A.result_id != "", rx.el.div(
            rx.el.div(
                rx.el.p(f"Spatial Pearson r: {A.spatial_pearson_text}", class_name="text-sm font-semibold"),
                rx.el.p(f"All valid pairs: {A.paired_cell_count} · Displayed points: {A.displayed_point_count}", class_name="mt-2 text-xs"),
                rx.el.p(f"X axis: {A.x_label}", class_name="mt-3 text-xs"),
                rx.el.p(f"Y axis: {A.y_label}", class_name="mt-1 text-xs"),
                class_name="mb-3",
            ),
            rx.cond(A.displayed_point_count > 0,
                xy.chart(
                    xy.scatter("x", "y", color="#3b88a0", size=5, opacity=0.65),
                    # XY plans require static structure; exact quantity/unit labels
                    # remain bound to the selected result in the text above.
                    xy.x_axis(label="X observation"), xy.y_axis(label="Y observation"), xy.modebar(False),
                    xy.interaction_config(navigation=False), data=A.paired_scatter,
                    height="360px", class_name="w-full min-w-[300px]", key=A.result_id,
                ),
                rx.el.p("No valid paired observations to display.", class_name="text-xs"),
            ),
            rx.el.p("Pairing uses the verified grid and mask intersection. Display points use evenly spaced ascending cell IDs; Pearson uses every valid pair. Spatial correlation describes this month only and does not establish causality or statistical significance. No p values, confidence intervals, differences or RMSE are provided.", class_name="mt-4 text-xs text-slate-500"),
            rx.el.p("Missing/nonfinite observations are excluded from pairs, never treated as zero. The current publication profile rejects such retained observations before reading. Coverage is not a physical-area fraction.", class_name="mt-2 text-xs text-slate-500"),
            rx.el.dl(rx.foreach(A.exclusions, lambda item: rx.el.div(
                rx.el.dt(item["label"]), rx.el.dd(item["value"]), class_name="flex justify-between text-xs mt-2",
            ))), class_name=PANEL,
        )), provenance(),
    )
