import asyncio
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple
from .models import PlaceStub

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
