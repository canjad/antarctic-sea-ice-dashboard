"""Compatibility imports for the shared product-publication utilities."""

from wind_pipeline.storage import atomic_write_json, atomic_write_text, staged_product

__all__ = ["atomic_write_json", "atomic_write_text", "staged_product"]
