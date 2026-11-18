"""Compare saved sea-ice concentration predictions with observations."""

import asyncio
import json
import logging
from typing import TypedDict

import reflex as rx
from sqlalchemy import select

from antarctic_sea_ice_dashboard.cleaning import (
    EXTENT_THRESHOLD,
    in_physical_range,
    parse_coordinates,
    parse_number,
    percent_factor,
)
from antarctic_sea_ice_dashboard.models import (
    UploadedDataset,
    UploadedDatasetRow,
)
from antarctic_sea_ice_dashboard.states.antarctic_globe import AntarcticGlobe
from antarctic_sea_ice_dashboard.states.uploaded_data import (
    MAX_ROWS,
    UploadedData,
)


class PredictionOption(TypedDict):
    id: int
    name: str
    snapshot: str


class ModelResult(TypedDict):
    predictions: list[PredictionOption]
    selected_id: int
    prediction_name: str
    snapshot: str
    accuracy: str
    matched_points: int
    observation_files: int
    message: str


class PredictionInsights(rx.State):
    """Track prediction selection and its same-snapshot accuracy summary."""

    predictions: list[PredictionOption] = []
    selected_prediction_id: int = 0
    prediction_name: str = ""
    snapshot: str = ""
    accuracy: str = ""
    matched_points: int = 0
    observation_files: int = 0
    message: str = (
        "Map a numeric Prediction column in Data to compare model output with recorded concentration."
    )
    loading: bool = False
    request_id: int = 0

    @staticmethod
    def _number(value: str) -> float | None:
        return parse_number(value)

    @staticmethod
    def _spatial_totals(
        session,
        dataset: UploadedDataset,
        columns: list[str],
        mapping: dict[str, str],
        measurement_key: str,
    ) -> dict[tuple[float, float], tuple[float, int]]:
        required = ("latitude", "longitude", measurement_key)
        if any(mapping.get(field) not in columns for field in required):
            return {}
        indices = {
            field: columns.index(mapping[field]) for field in required
        }
        located: list[tuple[tuple[float, float], float]] = []
        rows = session.execute(
            select(UploadedDatasetRow.cell_values_json)
            .where(UploadedDatasetRow.dataset_id == dataset.id)
            .order_by(
                UploadedDatasetRow.row_position,
                UploadedDatasetRow.id,
            )
            .limit(MAX_ROWS + 1)
            .execution_options(yield_per=1000)
        )
        for position, (raw,) in enumerate(rows, start=1):
            if position > MAX_ROWS:
                raise ValueError(
                    f"CSV dataset {dataset.name} exceeds the supported {MAX_ROWS:,}-row limit."
                )
            try:
                cells = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                not isinstance(cells, list)
                or len(cells) != len(columns)
                or not all(isinstance(cell, str) for cell in cells)
            ):
                continue
            coordinates = parse_coordinates(
                cells[indices["latitude"]], cells[indices["longitude"]]
            )
            value = parse_number(cells[indices[measurement_key]])
            if coordinates is None or value is None:
                continue
            latitude, longitude = coordinates
            located.append(
                ((round(latitude, 5), round(longitude, 5)), value)
            )
        scale = percent_factor(
            (value for _, value in located),
            mapping.get("value_scale", "auto"),
        )
        totals: dict[tuple[float, float], tuple[float, int]] = {}
        for coordinate, value in located:
            value *= scale
            if not in_physical_range(measurement_key, value):
                continue
            total, count = totals.get(coordinate, (0.0, 0))
            totals[coordinate] = (total + value, count + 1)
        return totals

    @staticmethod
    def _score(
        prediction_totals: dict[tuple[float, float], tuple[float, int]],
        observation_totals: dict[tuple[float, float], tuple[float, int]],
        extent: bool = False,
    ) -> tuple[float | None, int]:
        """Return 100 minus mean absolute error and the matched point count.

        With ``extent`` the prediction is an ice-extent probability or class
        (0–100 after scaling), so each observation becomes 100 when its
        concentration reaches the 15% extent threshold and 0 otherwise.
        """
        matches = prediction_totals.keys() & observation_totals.keys()
        if not matches:
            return None, 0
        errors = []
        for coordinate in matches:
            prediction_total, prediction_count = prediction_totals[coordinate]
            observation_total, observation_count = observation_totals[coordinate]
            prediction_value = prediction_total / prediction_count
            observation_value = observation_total / observation_count
            if extent:
                observation_value = (
                    100.0 if observation_value >= EXTENT_THRESHOLD else 0.0
                )
            errors.append(abs(prediction_value - observation_value))
        return max(0.0, 100.0 - sum(errors) / len(errors)), len(matches)

    @staticmethod
    def _result(selected_id: int = 0) -> ModelResult:
        with rx.session() as session:
            datasets = session.scalars(
                select(UploadedDataset)
                .where(UploadedDataset.source == "uploaded_csv")
                .order_by(
                    UploadedDataset.created_at.desc(),
                    UploadedDataset.id.desc(),
                )
            ).all()
            catalogue: list[
                tuple[UploadedDataset, dict[str, str], list[str], dict]
            ] = []
            options: list[PredictionOption] = []
            for dataset in datasets:
                summary = UploadedData._safe_summary(dataset)
                if not (summary["map_ready"] or summary["model_ready"]):
                    continue
                mapping = json.loads(dataset.map_mapping_json)
                columns = json.loads(dataset.column_names_json)
                catalogue.append((dataset, mapping, columns, summary))
                if summary["model_ready"]:
                    options.append(
                        {
                            "id": dataset.id,
                            "name": dataset.name,
                            "snapshot": summary["snapshot"],
                        }
                    )

            result: ModelResult = {
                "predictions": options,
                "selected_id": 0,
                "prediction_name": "",
                "snapshot": "",
                "accuracy": "",
                "matched_points": 0,
                "observation_files": 0,
                "message": "",
            }
            if not options:
                result["message"] = (
                    "No dated Prediction datasets are configured. In Data, map the prediction column and set the matching snapshot year and month."
                )
                return result

            option = next(
                (item for item in options if item["id"] == selected_id),
                options[0],
            )
            result["selected_id"] = option["id"]
            result["prediction_name"] = option["name"]
            result["snapshot"] = option["snapshot"]
            prediction_dataset = next(
                item[0] for item in catalogue if item[0].id == option["id"]
            )
            prediction_mapping = json.loads(
                prediction_dataset.map_mapping_json
            )
            prediction_columns = json.loads(
                prediction_dataset.column_names_json
            )
            prediction_totals = PredictionInsights._spatial_totals(
                session,
                prediction_dataset,
                prediction_columns,
                prediction_mapping,
                "prediction",
            )
            observation_totals: dict[
                tuple[float, float], tuple[float, int]
            ] = {}
            observation_files = 0
            for dataset, mapping, columns, summary in catalogue:
                if (
                    summary["group_key"] != option["snapshot"]
                    or mapping.get("prediction")
                    or not mapping.get("sea_ice_concentration")
                ):
                    continue
                points = PredictionInsights._spatial_totals(
                    session,
                    dataset,
                    columns,
                    mapping,
                    "sea_ice_concentration",
                )
                if not points:
                    continue
                observation_files += 1
                for coordinate, (total, count) in points.items():
                    previous_total, previous_count = observation_totals.get(
                        coordinate, (0.0, 0)
                    )
                    observation_totals[coordinate] = (
                        previous_total + total,
                        previous_count + count,
                    )
            result["observation_files"] = observation_files
            extent = (
                prediction_mapping.get("prediction_type", "concentration")
                == "extent_probability"
            )
            accuracy, matched_points = PredictionInsights._score(
                prediction_totals, observation_totals, extent
            )
            result["matched_points"] = matched_points
            if accuracy is None:
                result["message"] = (
                    f"No valid geographic coordinates overlap between prediction and recorded concentration for {option['snapshot']}."
                    if observation_files
                    else f"No recorded sea-ice concentration dataset is mapped for {option['snapshot']}."
                )
                return result
            result["accuracy"] = f"{accuracy:.1f}%"
            result["message"] = (
                "Prediction is an ice-extent probability or class. Each recorded concentration is converted to extent (100 when at least 15%, otherwise 0); accuracy is 100% minus the mean absolute difference across matched locations."
                if extent
                else "Accuracy is 100% minus mean absolute error in concentration percentage points across matched geographic observations. Fraction-form values (0–1) are converted to percent."
            )
            return result

    @rx.event
    def refresh(self):
        self.request_id += 1
        self.loading = True
        self.prediction_name = ""
        self.snapshot = ""
        self.accuracy = ""
        self.matched_points = 0
        self.observation_files = 0
        self.message = "Calculating prediction accuracy…"
        return PredictionInsights.load(
            self.request_id, self.selected_prediction_id
        )

    @rx.event
    def select_prediction(self, value: str):
        try:
            selected_id = int(value)
        except ValueError:
            self.message = "Choose a prediction dataset."
            return
        self.selected_prediction_id = selected_id
        return PredictionInsights.refresh

    @rx.event(background=True)
    async def load(self, request_id: int, selected_id: int):
        try:
            result = await asyncio.to_thread(
                PredictionInsights._result, selected_id
            )
            async with self:
                if request_id != self.request_id:
                    return
                self.predictions = result["predictions"]
                self.selected_prediction_id = result["selected_id"]
                self.prediction_name = result["prediction_name"]
                self.snapshot = result["snapshot"]
                self.accuracy = result["accuracy"]
                self.matched_points = result["matched_points"]
                self.observation_files = result["observation_files"]
                self.message = result["message"]
            # Show the recorded concentration the prediction was scored
            # against, rather than whatever Map Explorer last displayed.
            if result["snapshot"]:
                yield AntarcticGlobe.show_snapshot(
                    result["snapshot"], "sea_ice_concentration"
                )
        except Exception as error:
            logging.exception("Could not calculate prediction accuracy.")
            async with self:
                if request_id == self.request_id:
                    self.prediction_name = ""
                    self.snapshot = ""
                    self.accuracy = ""
                    self.matched_points = 0
                    self.message = (
                        str(error)
                        if isinstance(error, ValueError)
                        else "Could not load Model Insights. Refresh and retry."
                    )
        finally:
            async with self:
                if request_id == self.request_id:
                    self.loading = False
