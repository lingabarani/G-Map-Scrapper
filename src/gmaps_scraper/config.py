import os
from pathlib import Path
from typing import List, Optional, Union
import yaml
from pydantic import BaseModel, Field, ConfigDict
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

class ScraperConfig(BaseSettings):
    model_config = ConfigDict(extra="ignore")

    concurrency: int = 10                  # Raised: parallel HTTP slots
    category_workers: Union[int, str] = 10 # Raised: all categories concurrent by default
    all_categories_concurrent: bool = True  # All categories run at the same time per state
    request_concurrency: int = 10          # Raised: max shared concurrent requests
    per_host_rate: float = 8.0             # Raised: requests/sec to Google (auto-throttled)
    max_retries: int = 5                   # Raised: more retries before giving up
    backoff_base: float = 1.5
    tile_base_zoom: int = 15
    tile_max_depth: int = 3
    tile_overlap_pct: int = 10
    max_reviews_per_place: int = 100
    download_photos: bool = True
    photo_max_width: int = 800
    # ---------- Identity rotation (anti-block) ----------
    rotate_identity_on_block: bool = True  # Auto-rotate UA/cookie/proxy on CAPTCHA/429
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    accept_language: str = "en-US,en;q=0.9"
    hl: str = "en"
    gl: str = "us"
    proxies: List[str] = Field(default_factory=list)
    headless_fallback: bool = False
    db_path: Union[str, Path] = "data/gmaps.sqlite"
    raw_data_dir: Union[str, Path] = "data/raw"
    photos_dir: Union[str, Path] = "data/photos"
    databases_dir: Union[str, Path] = "data/databases"
    exports_dir: Union[str, Path] = "data/exports"
    logs_dir: Union[str, Path] = "logs"
    web_port: int = 8765
    web_host: str = "127.0.0.1"
    # ---------- Live Raw Export Policy ----------
    export_every_n_records: int = 500
    export_interval_seconds: float = 30.0
    live_raw_export_dir: Optional[str] = None

    @property
    def absolute_db_path(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def absolute_databases_dir(self) -> Path:
        p = Path(self.databases_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def absolute_photos_dir(self) -> Path:
        p = Path(self.photos_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def absolute_exports_dir(self) -> Path:
        p = Path(self.exports_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def absolute_raw_dir(self) -> Path:
        p = Path(self.raw_data_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def absolute_logs_dir(self) -> Path:
        p = Path(self.logs_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p

def load_config(user_config_path: Optional[str] = None) -> ScraperConfig:
    data = {}
    default_yaml = PROJECT_ROOT / "config" / "default.yaml"
    if default_yaml.exists():
        with open(default_yaml, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if loaded:
                data.update(loaded)

    user_yaml = Path(user_config_path) if user_config_path else PROJECT_ROOT / "config" / "user.yaml"
    if user_yaml.exists():
        with open(user_yaml, "r", encoding="utf-8") as f:
            loaded_user = yaml.safe_load(f)
            if loaded_user:
                data.update(loaded_user)

    return ScraperConfig(**data)
