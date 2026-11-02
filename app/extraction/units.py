"""Deterministic unit parsing and conversion for Phase 4/5."""

from __future__ import annotations

import re

_PRESSURE = {
    "pa": ("mbar", 0.01),
    "hpa": ("mbar", 1.0),
    "kpa": ("mbar", 10.0),
    "mbar": ("mbar", 1.0),
    "µbar": ("mbar", 0.001),
    "μbar": ("mbar", 0.001),
    "ubar": ("mbar", 0.001),
    "microbar": ("mbar", 0.001),
    "bar": ("mbar", 1000.0),
    "torr": ("mbar", 1.333223684),
    "mtorr": ("mbar", 0.001333223684),
    "atm": ("mbar", 1013.25),
}
_POWER = {"w": ("W", 1.0), "kw": ("W", 1000.0), "mw": ("W", 0.001)}
_LENGTH = {"nm": ("nm", 1.0), "um": ("nm", 1000.0), "µm": ("nm", 1000.0), "mm": ("nm", 1_000_000.0), "cm": ("nm", 10_000_000.0), "m": ("nm", 1_000_000_000.0)}
_TIME = {"s": ("min", 1 / 60), "sec": ("min", 1 / 60), "min": ("min", 1.0), "h": ("min", 60.0), "hr": ("min", 60.0)}
_DISTANCE = {"mm": ("mm", 1.0), "cm": ("mm", 10.0), "m": ("mm", 1000.0), "um": ("mm", 0.001), "µm": ("mm", 0.001)}
_FREQUENCY = {"hz": ("kHz", 0.001), "khz": ("kHz", 1.0), "mhz": ("kHz", 1000.0)}
_FLOW = {"sccm": ("sccm", 1.0), "slm": ("sccm", 1000.0), "ml/min": ("sccm", 1.0)}


def parse_numeric(value: str) -> float | None:
    text = (value or "").strip().replace(",", ".")
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    scientific = re.search(r"(\d+(?:\.\d+)?)\s*[×xX]\s*10\s*([\-\^]?)\s*(\d+)", text)
    if scientific:
        exponent = int(scientific.group(3))
        if scientific.group(2) in {"-", "−"}:
            exponent = -exponent
        return float(scientific.group(1)) * (10 ** exponent)
    # Plain "10−6 Torr" notation used in vacuum papers.
    plain_power = re.search(r"(?<![\d.])10\s*-\s*(\d+)", text)
    if plain_power:
        return 10 ** (-int(plain_power.group(1)))
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def convert(value: float, unit: str, family: str) -> tuple[float | None, str | None]:
    key = (unit or "").strip().casefold().replace("°", "")
    table = {
        "pressure": _PRESSURE,
        "power": _POWER,
        "length": _LENGTH,
        "time": _TIME,
        "distance": _DISTANCE,
        "frequency": _FREQUENCY,
        "flow": _FLOW,
    }.get(family)
    if table is None:
        return value, unit
    if key not in table:
        return None, None
    target_unit, factor = table[key]
    return value * factor, target_unit


def normalize_temperature(value: float, unit: str) -> tuple[float | None, str]:
    key = (unit or "c").casefold().replace("°", "")
    if key in {"c", "celsius"}:
        return value, "°C"
    if key in {"k", "kelvin"}:
        return value - 273.15, "°C"
    if key in {"f", "fahrenheit"}:
        return (value - 32) * 5 / 9, "°C"
    return None, "°C"


def in_range(value: float | None, minimum: float, maximum: float) -> bool:
    return value is not None and minimum <= value <= maximum
