"""Published v10 controls and raster presentation; science stays in the adapter."""

import reflex as rx

from antarctic_sea_ice_dashboard.services.map_raster import LEGEND_GRADIENT
from antarctic_sea_ice_dashboard.states.explorer import Explorer as S


BUTTON = "min-h-9 rounded border border-slate-300 bg-white px-3 text-xs text-slate-700 disabled:opacity-40"
INPUT = "min-h-9 w-full rounded border border-slate-300 bg-white px-2 text-xs text-slate-800"


def product_controls() -> rx.Component:
    return rx.el.section(
        rx.el.h2("Published v10 product", class_name="text-sm font-semibold"),
        rx.el.label(
            "Product and content version",
            rx.el.select(
                rx.el.option("Select a verified product from this page", value=""),
                rx.foreach(S.map_products, lambda item: rx.el.option(item["label"], value=item["product_id"])),
                value=S.map_selector_value,
                aria_label="Product and content version",
                on_change=S.select_map_product,
                disabled=S.map_catalogue_loading,
                class_name=INPUT,
            ),
            class_name="block mt-3 text-xs space-y-2",
        ),
        rx.cond(S.selected_product_id != "", rx.el.p(
            f"Requested identity (availability is checked on each read): {S.selected_product_label}",
            class_name="mt-2 text-xs text-slate-500 break-all",
        )),
        rx.el.div(
            rx.el.button("Refresh products and frame", on_click=S.refresh_map_products, class_name=BUTTON),
            rx.el.button("Previous catalogue page", on_click=S.change_map_catalogue_page(-1),
                disabled=S.map_catalogue_loading | (S.map_catalogue_page == 0), class_name=BUTTON),
            rx.el.button("Next catalogue page", on_click=S.change_map_catalogue_page(1),
                disabled=S.map_catalogue_loading | ~S.map_catalogue_has_more, class_name=BUTTON),
            class_name="mt-3 flex flex-wrap gap-2",
        ),
        rx.el.p(f"Catalogue page {S.map_catalogue_page + 1}. Only complete, verified, published v10 products with a supported map contract are listed.", class_name="mt-2 text-xs text-slate-500"),
        rx.cond(S.map_catalogue_loading, rx.el.p("Reading published catalogue…", role="status", class_name="mt-2 text-xs")),
        rx.cond(S.map_catalogue_message != "", rx.el.p(S.map_catalogue_message, role="status", class_name="mt-2 text-xs text-amber-800")),
        class_name="rounded-lg border border-slate-200 bg-white p-4",
    )


def month_controls() -> rx.Component:
    return rx.el.div(
        rx.el.label(
            "Actual observation month",
            rx.el.select(
                rx.cond(S.available_months.length() == 0, rx.el.option("No available months", value="")),
                rx.foreach(S.available_months, lambda month: rx.el.option(month, value=month)),
                value=S.selected_month, on_change=S.select_map_month,
                aria_label="Actual observation month",
                disabled=S.available_months.length() == 0, class_name=INPUT,
            ),
            class_name="block text-xs space-y-2 min-w-48",
        ),
        rx.el.button("Previous month", on_click=S.step_map_month(-1), disabled=S.available_months.length() < 2, class_name=BUTTON),
        rx.el.button(rx.cond(S.map_playing, "Pause", "Play actual months"), on_click=S.toggle_map_play,
            disabled=S.available_months.length() < 2, class_name=BUTTON),
        rx.el.button("Next month", on_click=S.step_map_month(1), disabled=S.available_months.length() < 2, class_name=BUTTON),
        rx.el.p("Only manifest months are traversed, including gaps. Playback waits for each frame and wraps at the last actual month.", class_name="w-full text-xs text-slate-500"),
        class_name="flex flex-wrap items-end gap-3 border-b border-slate-200 p-4",
    )


def signed_legend() -> rx.Component:
    return rx.el.div(
        rx.el.p(f"10 m northward wind component · v10 · {S.map_unit}", class_name="font-semibold text-xs"),
        rx.el.div(style={"background": LEGEND_GRADIENT},
            class_name="mt-2 h-3 w-full border border-slate-300"),
        rx.el.div(rx.el.span(S.colour_negative), rx.el.span("0"), rx.el.span(S.colour_positive),
            class_name="mt-1 flex justify-between text-xs"),
        rx.el.p("Current-month symmetric range: ±max(abs(v10)). All-zero fields use ±1. The range can change between months; values are not clipped.", class_name="mt-2 text-xs text-slate-500"),
        rx.el.p("Blue: negative · neutral: valid zero · red: positive. Grey background: no retained data. No complete wind vector is inferred.", class_name="mt-2 text-xs text-slate-500"),
        class_name="border-t border-slate-200 p-4",
    )


def pixel_inspector() -> rx.Component:
    return rx.el.aside(
        rx.el.h2("Original pixel inspector", class_name="text-sm font-semibold"),
        rx.el.p("Select a location using Hughes 1980 geographic longitude and latitude. The containing 25 km cell is queried without interpolation or a nearest-cell search.", class_name="mt-2 text-xs text-slate-500"),
        rx.el.form(
            rx.el.input(type="hidden", name="frame_id", value=S.map_frame_id),
            rx.el.label("Longitude (degrees)", rx.el.input(name="longitude", required=True, max_length=100,
                placeholder="Longitude [-180, 180]", class_name=INPUT), class_name="block text-xs space-y-1"),
            rx.el.label("Latitude (degrees)", rx.el.input(name="latitude", required=True, max_length=100,
                placeholder="Latitude [-90, 90]", class_name=INPUT), class_name="block text-xs space-y-1"),
            rx.el.button("Query position", type="submit", disabled=(S.map_frame_id == ""), class_name=BUTTON),
            on_submit=S.inspect_position, class_name="mt-4 space-y-3",
        ),
        rx.cond(S.pixel_loading, rx.el.p("Reading original pixel…", role="status", class_name="mt-3 text-xs")),
        rx.el.p(S.pixel_status, class_name="mt-3 text-xs font-semibold"),
        rx.el.p(S.pixel_message, role="status", class_name="mt-2 text-xs text-slate-600"),
        rx.el.dl(rx.foreach(S.pixel_details, lambda item: rx.el.div(
            rx.el.dt(item["label"], class_name="text-slate-500"),
            rx.el.dd(item["value"], class_name="break-all text-slate-800"),
            class_name="mt-3 text-xs space-y-1",
        ))),
        rx.el.p("West/north edges are included; east/south edges are excluded. No hit tolerance is added. Reference centres are separately validated within 2 m per projected axis. Reported coordinates are the saved cell centre.", class_name="mt-4 text-xs text-slate-500"),
        class_name="rounded-lg border border-slate-200 bg-white p-4 min-w-0",
    )


def scientific_map() -> rx.Component:
    return rx.el.div(
        product_controls(),
        rx.el.div(
            rx.el.section(
                month_controls(),
                rx.cond(S.map_loading, rx.el.p("Loading the selected product and month…", role="status", class_name="p-4 text-sm")),
                rx.cond(S.map_error != "", rx.el.p(S.map_error, role="alert", class_name="p-4 text-sm text-red-800")),
                rx.cond(S.map_image_src != "",
                    rx.el.div(
                        rx.el.h2(f"v10 · {S.selected_month}", class_name="px-4 pt-4 text-sm font-semibold"),
                        rx.el.img(src=S.map_image_src, key=S.map_frame_id,
                            alt=f"Original-grid signed v10 raster for {S.selected_month}",
                            width=316, height=332,
                            style={"imageRendering": "pixelated"},
                            class_name="mx-auto block w-full max-w-[632px] h-auto bg-slate-200"),
                        rx.el.p(S.map_bounds, class_name="p-4 text-xs text-slate-500"),
                        rx.el.p(S.map_source, class_name="px-4 pb-4 text-xs text-slate-600 break-words"),
                        rx.el.p(f"Product: {S.selected_product_id} · Content: {S.selected_content_digest}", class_name="px-4 pb-4 text-xs text-slate-500 break-all"),
                        signed_legend(),
                    ),
                    rx.cond(~S.map_loading & (S.map_error == ""), rx.el.p(
                        "Select a published v10 product. If none is available, publish an eligible checked revision through Data after the operator configures the required resources. No replacement observations are generated.",
                        class_name="p-6 text-sm text-slate-600",
                    )),
                ),
                class_name="rounded-lg border border-slate-200 bg-white min-w-0 overflow-hidden",
            ),
            pixel_inspector(),
            class_name="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_320px] gap-4",
        ),
    )
