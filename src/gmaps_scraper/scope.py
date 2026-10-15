from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "pipeline2_regions.yaml"
_SCOPE_CACHE: Optional[Dict[str, Any]] = None

ALLOWED_COUNTRIES = ("US", "MX")

class RegionInfo(BaseModel):
    code: str
    name: str
    country_code: str
    bbox: Optional[Dict[str, float]] = None

def load_scope_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    global _SCOPE_CACHE
    if _SCOPE_CACHE is not None and config_path is None:
        return _SCOPE_CACHE
    
    path = config_path or _CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Pipeline 2 regions configuration not found at {path}")
    
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    
    if config_path is None:
        _SCOPE_CACHE = data
    return data

def validate_target_country(country_code: str) -> str:
    """
    Validates that a country code is strictly permitted for Pipeline 2.
    Only United States ('US') and Mexico ('MX') are supported.
    """
    cc = country_code.strip().upper()
    if cc not in ALLOWED_COUNTRIES:
        raise ValueError(
            f"Country '{country_code}' is not supported for Pipeline 2. "
            f"Permitted countries are strictly: {list(ALLOWED_COUNTRIES)}"
        )
    return cc

def get_target_countries(config_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    data = load_scope_config(config_path)
    return data.get("target_countries", {})

def get_us_states(config_path: Optional[Path] = None) -> Dict[str, RegionInfo]:
    """Returns exactly the 50 configured US states keyed by 2-letter postal code."""
    data = load_scope_config(config_path)
    states = data.get("us_states", {})
    return {
        code.upper(): RegionInfo(code=code.upper(), name=info["name"], country_code="US")
        for code, info in states.items()
    }

def get_mexico_entities(config_path: Optional[Path] = None) -> Dict[str, RegionInfo]:
    """Returns exactly the 32 Mexican federal entities keyed by entity code."""
    data = load_scope_config(config_path)
    entities = data.get("mexico_entities", {})
    return {
        code.upper(): RegionInfo(code=code.upper(), name=info["name"], country_code="MX")
        for code, info in entities.items()
    }

def get_regions_for_country(country_code: str, config_path: Optional[Path] = None) -> Dict[str, RegionInfo]:
    cc = validate_target_country(country_code)
    if cc == "US":
        return get_us_states(config_path)
    elif cc == "MX":
        return get_mexico_entities(config_path)
    return {}

def get_region(country_code: str, region_code: str, config_path: Optional[Path] = None) -> RegionInfo:
    regions = get_regions_for_country(country_code, config_path)
    rc = region_code.strip().upper()
    if rc not in regions:
        raise ValueError(
            f"Region '{region_code}' not found in country '{country_code}'. "
            f"Available regions: {list(regions.keys())}"
        )
    return regions[rc]
