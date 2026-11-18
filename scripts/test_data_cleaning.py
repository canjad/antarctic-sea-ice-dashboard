"""Tests for shared numeric cleaning and the Data page import/edit rules."""

import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from antarctic_sea_ice_dashboard.cleaning import (
    in_physical_range,
    parse_coordinates,
    parse_number,
    percent_factor,
)
from antarctic_sea_ice_dashboard.states.model_insights import (
    PredictionInsights,
)
from antarctic_sea_ice_dashboard.states.uploaded_data import UploadedData


class NumberParsingTests(unittest.TestCase):
    def test_accepts_plain_scientific_and_thousands_numbers(self):
        for raw, expected in (
            ("75", 75.0),
            (" -1.5 ", -1.5),
            ("1e2", 100.0),
            (".5", 0.5),
            ("1,234", 1234.0),
            ("12,345.5", 12345.5),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(parse_number(raw), expected)

    def test_rejects_decimal_commas_underscores_and_missing_tokens(self):
        for raw in ("0,5", "1,5", "0,500", "1_000", "", "NA", "nan", "inf", "abc", "1e999"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_number(raw))

    def test_every_page_parses_the_same_way(self):
        from antarctic_sea_ice_dashboard.states.antarctic_globe import (
            AntarcticGlobe,
        )

        for raw in ("0,5", "1_000", "42", " 3.5 "):
            with self.subTest(raw=raw):
                self.assertEqual(
                    AntarcticGlobe._number(raw), PredictionInsights._number(raw)
                )

    def test_longitudes_from_0_to_360_are_normalized(self):
        self.assertEqual(parse_coordinates("-70", "190"), (-70.0, -170.0))
        self.assertEqual(parse_coordinates("-70", "360"), (-70.0, 0.0))
        self.assertEqual(parse_coordinates("-70", "180"), (-70.0, -180.0))
        self.assertEqual(parse_coordinates("-70", "-42.5"), (-70.0, -42.5))
        self.assertIsNone(parse_coordinates("-70", "361"))
        self.assertIsNone(parse_coordinates("-91", "10"))


class ScaleAndRangeTests(unittest.TestCase):
    def test_one_stray_value_does_not_flip_a_fraction_file(self):
        values = [0.0, 0.2, 0.55, 0.9, 1.0] * 100 + [2.0]
        self.assertEqual(percent_factor(values), 100.0)

    def test_percent_file_with_few_ice_cells_stays_percent(self):
        values = [0.0] * 10000 + [35.0, 80.0, 99.0]
        self.assertEqual(percent_factor(values), 1.0)

    def test_all_zero_or_binary_values_default_to_fraction(self):
        self.assertEqual(percent_factor([0.0, 1.0, 0.0]), 100.0)

    def test_explicit_setting_overrides_detection(self):
        self.assertEqual(percent_factor([0.0, 1.0], "percent"), 1.0)
        self.assertEqual(percent_factor([50.0], "fraction"), 100.0)

    def test_fill_values_are_out_of_physical_range(self):
        self.assertFalse(in_physical_range("sea_surface_temperature", -999))
        self.assertFalse(in_physical_range("wind_speed", -2))
        self.assertFalse(in_physical_range("air_temperature", 9999))
        self.assertTrue(in_physical_range("sea_surface_temperature", -1.8))
        self.assertTrue(in_physical_range("wind_speed", 12.5))


class ExtentScoringTests(unittest.TestCase):
    def test_extent_prediction_is_scored_against_thresholded_observation(self):
        prediction = {(-70.0, 10.0): (90.0, 1), (-71.0, 11.0): (10.0, 1)}
        observation = {(-70.0, 10.0): (40.0, 1), (-71.0, 11.0): (5.0, 1)}
        accuracy, matched = PredictionInsights._score(
            prediction, observation, extent=True
        )
        self.assertEqual(matched, 2)
        self.assertEqual(accuracy, 90.0)


class ImportCleaningTests(unittest.TestCase):
    def test_blank_rows_and_empty_trailing_columns_are_skipped(self):
        report: dict = {}
        headers, rows = UploadedData._parse_csv(
            "excel.csv",
            b"Lat,Lon,SIC,,\r\n-70,10,50,,\r\n,,,,\r\n\r\n-71,11,60\r\n",
            report,
        )
        self.assertEqual(headers, ["Lat", "Lon", "SIC"])
        self.assertEqual(rows, [["-70", "10", "50"], ["-71", "11", "60"]])
        self.assertEqual(report["skipped_blank_rows"], 2)
        self.assertEqual(report["dropped_columns"], 2)

    def test_values_under_unnamed_columns_are_rejected(self):
        with self.assertRaises(ValueError):
            UploadedData._parse_csv("bad.csv", b"Lat,Lon,\n-70,10,oops\n")

    def test_gbk_file_is_decoded_and_reported(self):
        report: dict = {}
        headers, rows = UploadedData._parse_csv(
            "gbk.csv", "纬度,经度\n-70,10\n".encode("gbk"), report
        )
        self.assertEqual(headers, ["纬度", "经度"])
        self.assertEqual(report["encoding"], "gb18030")
        self.assertIn("GB18030", UploadedData._import_notes(report))


class UploadDateInputTests(unittest.TestCase):
    def test_clearing_year_input_does_not_raise_or_keep_stale_value(self):
        state = SimpleNamespace(upload_year="2023", upload_month="9")
        UploadedData.set_upload_year.fn(state, "")
        UploadedData.set_upload_month.fn(state, " 10 ")
        self.assertEqual(state.upload_year, "")
        self.assertEqual(state.upload_month, "10")


class DamagedRecordTests(unittest.TestCase):
    def test_damaged_mapping_gives_not_ready_summary_instead_of_raising(self):
        dataset = SimpleNamespace(
            id=3,
            name="Broken",
            original_filename="broken.csv",
            row_count=2,
            column_names_json=json.dumps(["Lat", "Lon"]),
            map_mapping_json="{not json",
            map_layer_active=True,
            created_at=datetime.now(UTC),
        )
        summary = UploadedData._safe_summary(dataset)
        self.assertFalse(summary["map_ready"])
        self.assertFalse(summary["active"])
        self.assertEqual(summary["columns"], 2)
        self.assertIn("damaged", summary["map_message"])


class MappingOptionTests(unittest.TestCase):
    def test_options_default_and_validate(self):
        columns = ["Lat", "Lon", "SIC"]
        base = {"latitude": "Lat", "longitude": "Lon", "sea_ice_concentration": "SIC"}
        clean = UploadedData._validate_mapping(columns, 1, base)
        self.assertEqual(clean["value_scale"], "auto")
        self.assertEqual(clean["prediction_type"], "concentration")
        with self.assertRaises(ValueError):
            UploadedData._validate_mapping(
                columns, 1, base | {"value_scale": "kelvin"}
            )


class RowEditValidationTests(unittest.TestCase):
    columns = ["Lat", "Lon", "SST", "Note"]
    mapping = {
        "latitude": "Lat",
        "longitude": "Lon",
        "sea_surface_temperature": "SST",
    }

    def problem(self, cells):
        return UploadedData._row_problem(self.columns, self.mapping, cells)

    def test_valid_and_blank_cells_pass(self):
        self.assertEqual(self.problem(["-70", "200", "1.5", "anything"]), "")
        self.assertEqual(self.problem(["", "", "", ""]), "")

    def test_bad_cells_are_rejected(self):
        self.assertIn("Lat", self.problem(["abc", "10", "1", ""]))
        self.assertIn("longitude", self.problem(["-70", "400", "1", ""]))
        self.assertIn("SST", self.problem(["-70", "10", "-999", ""]))
        self.assertIn("SST", self.problem(["-70", "10", "0,5", ""]))


if __name__ == "__main__":
    unittest.main()
