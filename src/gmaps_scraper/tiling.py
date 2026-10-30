import asyncio
import re
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from .models import PlaceStub
from .geo.boundaries import get_region_bounding_boxes, GeoBoundingBox

# -----------------------------------------------------------------------------
# Legacy Tile & Recursive Search (Preserved for backwards compatibility)
# -----------------------------------------------------------------------------

class Tile:
    def __init__(self, lat: float, lng: float, span_deg: float, depth: int = 0):
        self.lat = lat
        self.lng = lng
        self.span_deg = span_deg
        self.depth = depth

    def subdivide(self, overlap_pct: float = 10.0) -> List["Tile"]:
        """Subdivides this tile into 4 overlapping quadrants."""
        half_span = self.span_deg / 2.0
        overlap = half_span * (overlap_pct / 100.0)
        eff_half = half_span + overlap
        offset = half_span / 2.0

        quadrants = [
            Tile(self.lat + offset, self.lng - offset, eff_half, self.depth + 1),  # NW
            Tile(self.lat + offset, self.lng + offset, eff_half, self.depth + 1),  # NE
            Tile(self.lat - offset, self.lng - offset, eff_half, self.depth + 1),  # SW
            Tile(self.lat - offset, self.lng + offset, eff_half, self.depth + 1),  # SE
        ]
        return quadrants

async def recursive_tile_search(
    fetch_tile_func: Callable[[float, float, int], Coroutine[Any, Any, List[PlaceStub]]],
    center_lat: float,
    center_lng: float,
    initial_span_deg: float,
    max_depth: int = 3,
    overlap_pct: float = 10.0,
    on_tile_complete: Optional[Callable[[int, int], None]] = None,
    max_results: Optional[int] = None
) -> List[PlaceStub]:
    """
    Executes recursive grid subdivision:
    If a tile returns 20 places (Google's hard cap), it subdivides into 4 quadrants
    with configured overlap and recurses up to max_depth.
    Globally dedupes by CID.
    """
    root_tile = Tile(center_lat, center_lng, initial_span_deg, depth=0)
    queue = [root_tile]
    all_stubs: Dict[str, PlaceStub] = {}
    tiles_processed = 0

    while queue:
        tile = queue.pop(0)
        # Approximate meters: 1 deg lat ~ 111,000 m
        span_meters = max(50, int(tile.span_deg * 111000))
        stubs = await fetch_tile_func(tile.lat, tile.lng, span_meters)
        tiles_processed += 1

        for s in stubs:
            if s.cid not in all_stubs:
                all_stubs[s.cid] = s
                if max_results and len(all_stubs) >= max_results:
                    break

        if max_results and len(all_stubs) >= max_results:
            if on_tile_complete:
                on_tile_complete(tiles_processed, len(all_stubs))
            break

        # Hard cap condition: if exactly 20 and depth < max_depth, subdivide
        if len(stubs) >= 20 and tile.depth < max_depth:
            sub_tiles = tile.subdivide(overlap_pct=overlap_pct)
            queue.extend(sub_tiles)

        if on_tile_complete:
            on_tile_complete(tiles_processed, len(all_stubs))

    return list(all_stubs.values())


# -----------------------------------------------------------------------------
# Enterprise Geographic Tile System (Section 3 & Section 8)
# -----------------------------------------------------------------------------

def sanitize_slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    return s.strip("_") or "all"


class GeoTile(BaseModel):
    """
    Geographic Tile representation with persistent bounds, stable deterministic ID,
    parent/child subdivision relationships, and collection metrics.
    """
    tile_id: str
    parent_tile_id: Optional[str] = None
    task_id: str
    run_id: str
    country_code: str
    region_code: str
    category: str
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float
    center_lat: float
    center_lng: float
    span_deg: float
    depth: int = 0
    status: str = "pending"  # pending, running, completed, subdivided, retry_pending, failed, limit_reached
    attempts: int = 0
    retry_count: int = 0
    results_count: int = 0
    new_records: int = 0
    updated_records: int = 0
    duplicate_cids: int = 0
    last_error: Optional[str] = None
    http_status: Optional[int] = None
    subdivision_status: Optional[str] = None
    last_checkpoint: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def span_meters(self) -> int:
        """Approximates radius/span in meters for Google Maps search RPC."""
        return max(50, int(self.span_deg * 111000))

    def subdivide(self, overlap_pct: float = 10.0, max_depth: int = 3, min_span_deg: float = 0.02) -> List["GeoTile"]:
        """
        Subdivides this tile into 4 quadrants (NW, NE, SW, SE) with configured overlap.
        Enforces maximum depth and minimum tile span to prevent infinite recursion.
        """
        if self.depth >= max_depth or self.span_deg <= min_span_deg:
            return []

        half_span = self.span_deg / 2.0
        overlap = half_span * (overlap_pct / 100.0)
        eff_half = half_span + overlap
        offset = half_span / 2.0

        quadrant_specs = [
            ("nw", self.center_lat + offset, self.center_lng - offset),
            ("ne", self.center_lat + offset, self.center_lng + offset),
            ("sw", self.center_lat - offset, self.center_lng - offset),
            ("se", self.center_lat - offset, self.center_lng + offset),
        ]

        child_tiles: List["GeoTile"] = []
        for q_code, c_lat, c_lng in quadrant_specs:
            child_id = f"{self.tile_id}_{q_code}"
            child = GeoTile(
                tile_id=child_id,
                parent_tile_id=self.tile_id,
                task_id=self.task_id,
                run_id=self.run_id,
                country_code=self.country_code,
                region_code=self.region_code,
                category=self.category,
                min_lat=round(c_lat - eff_half / 2.0, 6),
                max_lat=round(c_lat + eff_half / 2.0, 6),
                min_lng=round(c_lng - eff_half / 2.0, 6),
                max_lng=round(c_lng + eff_half / 2.0, 6),
                center_lat=round(c_lat, 6),
                center_lng=round(c_lng, 6),
                span_deg=round(eff_half, 6),
                depth=self.depth + 1,
                status="pending",
                attempts=0,
                retry_count=0,
                created_at=datetime.now().isoformat(),
                updated_at=datetime.now().isoformat(),
            )
            child_tiles.append(child)

        return child_tiles


def generate_region_grid_tiles(
    country_code: str,
    region_code: str,
    category: str,
    task_id: str,
    run_id: str,
    step_deg: float = 0.5,
    max_tiles_per_region: int = 150
) -> List[GeoTile]:
    """
    Generates a deterministic set of root geographic tiles covering the verified
    boundaries of the specified region (US state or Mexican federal entity).
    Handles disconnected components (such as Alaska's mainland & Aleutians).
    Assigns each tile a stable, deterministic ID.
    """
    cc = country_code.strip().upper()
    rc = region_code.strip().upper()
    cat_slug = sanitize_slug(category)

    bboxes = get_region_bounding_boxes(cc, rc)
    tiles: List[GeoTile] = []

    # Adaptive step adjustment for very small entities (e.g. CDMX, TLA)
    # vs large territories (e.g. TX, CA, CHH)
    for part_idx, bbox in enumerate(bboxes):
        lat_span = bbox.lat_span
        lng_span = bbox.lng_span

        eff_step = step_deg
        # Small territory adaptation: ensure at least 1 tile covering center
        if lat_span < step_deg and lng_span < step_deg:
            eff_step = max(lat_span, lng_span, 0.05)

        cur_lat = bbox.min_lat + (eff_step / 2.0)
        y_idx = 0
        while cur_lat <= bbox.max_lat + (eff_step * 0.25):
            cur_lng = bbox.min_lng + (eff_step / 2.0)
            x_idx = 0
            while cur_lng <= bbox.max_lng + (eff_step * 0.25):
                tile_id = f"t_{cc.lower()}_{rc.lower()}_{cat_slug}_p{part_idx}_y{y_idx}_x{x_idx}"
                tile = GeoTile(
                    tile_id=tile_id,
                    parent_tile_id=None,
                    task_id=task_id,
                    run_id=run_id,
                    country_code=cc,
                    region_code=rc,
                    category=category,
                    min_lat=round(cur_lat - eff_step / 2.0, 6),
                    max_lat=round(cur_lat + eff_step / 2.0, 6),
                    min_lng=round(cur_lng - eff_step / 2.0, 6),
                    max_lng=round(cur_lng + eff_step / 2.0, 6),
                    center_lat=round(cur_lat, 6),
                    center_lng=round(cur_lng, 6),
                    span_deg=round(eff_step, 6),
                    depth=0,
                    status="pending",
                    created_at=datetime.now().isoformat(),
                    updated_at=datetime.now().isoformat(),
                )
                tiles.append(tile)
                if len(tiles) >= max_tiles_per_region:
                    return tiles
                cur_lng += eff_step
                x_idx += 1
            cur_lat += eff_step
            y_idx += 1

    return tiles
