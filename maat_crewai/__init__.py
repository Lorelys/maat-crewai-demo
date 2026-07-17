"""Maat connector for CrewAI — validate handoffs + analyze Flow structure."""

from .validator import Finding, ValidationResult, validate

__all__ = ["Finding", "ValidationResult", "validate"]
__version__ = "0.1.0"
