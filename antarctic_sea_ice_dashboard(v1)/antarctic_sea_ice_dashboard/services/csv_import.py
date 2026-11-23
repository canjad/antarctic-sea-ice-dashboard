"""Validate bounded CSV imports without changing their string values."""

import csv
import io
import logging
from pathlib import PurePath


MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 10
MAX_CELL = 32_768

logger = logging.getLogger(__name__)


def _validate_quote_boundaries(text: str) -> None:
    """Reject quote placements that ``csv.reader(strict=True)`` can accept."""
    state = "field_start"
    physical_line = 1
    previous_character = ""

    # This guard checks quote placement only; csv.reader remains the parser.
    for character in text:
        if state == "quoted":
            if character == '"':
                state = "after_quote"
        elif state == "after_quote":
            if character == '"':
                state = "quoted"
            elif character in ",\r\n":
                state = "field_start"
            else:
                raise ValueError(
                    "Invalid CSV quoting near physical file line "
                    f"{physical_line}: a closing quote must be followed by "
                    "a comma, a record ending or the end of the file."
                )
        elif state == "field_start":
            if character == '"':
                state = "quoted"
            elif character not in ",\r\n":
                state = "unquoted"
        elif character == '"':
            raise ValueError(
                "Invalid CSV quoting near physical file line "
                f"{physical_line}: quote the entire field and double any "
                "quotes inside it."
            )
        elif character in ",\r\n":
            state = "field_start"

        if character == "\r" or (
            character == "\n" and previous_character != "\r"
        ):
            physical_line += 1
        previous_character = character


def parse_csv(
    filename: str, data: bytes
) -> tuple[list[str], list[list[str]]]:
    """Return ordered headers and string records from one bounded UTF-8 CSV.

    Args:
        filename: The supplied filename, which must have a .csv extension.
        data: The complete file contents, optionally beginning with a UTF-8 BOM.

    Returns:
        The original header values and data records in their original order.
        Header-only files return an empty record list. Cells are not trimmed or
        converted. Data record numbers exclude the header; quoted records may
        span several physical file lines.

    Raises:
        ValueError: If the extension, byte limit, encoding, headers, quoting,
            record width, record count or cell lengths violate the import rules.
    """
    if "\x00" in filename:
        raise ValueError(
            "The filename contains a null character. Rename the file and retry."
        )
    if PurePath(filename).suffix.lower() != ".csv":
        raise ValueError("Choose a file with a .csv extension.")
    if len(data) > MAX_BYTES:
        raise ValueError("This CSV exceeds the 5 MB per-file limit.")
    if not data:
        raise ValueError("The file is empty. Include a header row.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        logger.exception("Could not decode the uploaded CSV as UTF-8.")
        raise ValueError(
            "Use UTF-8 or UTF-8 with BOM encoding; this file could not be decoded."
        ) from error

    _validate_quote_boundaries(text)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = next(reader, [])
        if not headers or any(not name.strip() for name in headers):
            raise ValueError(
                "Every column needs a nonempty header in the first row."
            )
        if len(headers) > MAX_COLUMNS:
            raise ValueError("Use at most 10 columns per CSV.")
        if len(set(name.strip() for name in headers)) != len(headers):
            raise ValueError(
                "Column headers must be unique, including after trimming whitespace."
            )
        if any(len(name) > MAX_CELL for name in headers):
            raise ValueError(
                "A header is too long. Limit each header to 32,768 characters."
            )

        records: list[list[str]] = []
        for record_number, cells in enumerate(reader, start=1):
            if record_number > MAX_ROWS:
                raise ValueError("Use at most 100,000 data rows per CSV.")
            if len(cells) != len(headers):
                raise ValueError(
                    f"Data record {record_number} has {len(cells)} fields; "
                    f"expected {len(headers)}. Check blank lines, commas and quoting."
                )
            if any(len(cell) > MAX_CELL for cell in cells):
                raise ValueError(
                    f"Data record {record_number} contains a cell exceeding "
                    "32,768 characters."
                )
            records.append(cells)
        return headers, records
    except csv.Error as error:
        logger.exception("Could not parse the uploaded CSV.")
        raise ValueError(
            "Invalid CSV quoting or an oversized field near physical file line "
            f"{reader.line_num}."
        ) from error
