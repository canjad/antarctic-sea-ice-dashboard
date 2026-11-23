import reflex as rx
import antarctic_sea_ice_dashboard.models  # Register the schema without executing database operations.
from antarctic_sea_ice_dashboard.components.workspace import workspace
from antarctic_sea_ice_dashboard.states.uploaded_data import UploadedData
from antarctic_sea_ice_dashboard.states.scientific_analysis import ScientificAnalysis


def index() -> rx.Component:
    return workspace()


app = rx.App(
    theme=rx.theme(appearance="light"),
    head_components=[
        rx.el.link(rel="preconnect", href="https://fonts.googleapis.com"),
        rx.el.link(
            rel="preconnect",
            href="https://fonts.gstatic.com",
            cross_origin="",
        ),
        rx.el.link(
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap",
            rel="stylesheet",
        ),
    ],
)
app.add_page(
    index,
    route="/",
    title="Antarctic Sea Ice Explorer · Research Prototype",
    on_load=[UploadedData.load_catalogue, ScientificAnalysis.open_page("Overview")],
)
