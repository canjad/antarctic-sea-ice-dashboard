import reflex as rx
import reflex_xy

config = rx.Config(
    app_name="antarctic_sea_ice_dashboard",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        reflex_xy.XYPlugin(),
    ],
)
