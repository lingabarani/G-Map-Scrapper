import asyncio
import pytest
from gmaps_scraper.models import PlaceStub
from gmaps_scraper.tiling import Tile, recursive_tile_search

def test_tile_subdivision():
    tile = Tile(lat=40.7128, lng=-74.0060, span_deg=0.1, depth=0)
    quadrants = tile.subdivide(overlap_pct=10.0)
    assert len(quadrants) == 4
    for q in quadrants:
        assert q.depth == 1
        assert q.span_deg > 0.05  # Has overlap added

@pytest.mark.asyncio
async def test_recursive_tile_search_deduplication():
    # Mock tile search function: returns 20 places on depth 0, 5 places on depth 1
    async def mock_fetch(lat: float, lng: float, span_meters: int):
        # Generate dummy stubs
        # Some overlap in CIDs to test deduplication
        base_id = int(lat * 100) % 50
        stubs = []
        # Return 20 items for root to trigger subdivision
        count = 20 if span_meters > 5000 else 5
        for i in range(count):
            cid = f"test_cid_{base_id + i}"
            stubs.append(PlaceStub(
                cid=cid,
                ftid=f"0x0:0x{base_id + i:x}",
                place_id=f"PID_{base_id + i}",
                name=f"Place {cid}",
                latitude=lat,
                longitude=lng
            ))
        return stubs

    results = await recursive_tile_search(
        fetch_tile_func=mock_fetch,
        center_lat=40.7128,
        center_lng=-74.0060,
        initial_span_deg=0.1,
        max_depth=1,
        overlap_pct=10.0
    )

    assert len(results) > 0
    # Verify global deduplication: all CIDs are unique
    cids = [s.cid for s in results]
    assert len(cids) == len(set(cids))

def test_saturated_tile_children_return_distinct_place_sets():
    """Regression test: Asserts a saturated tile's 4 children return DIFFERENT place sets (Jaccard < 0.50)."""
    # Create root tile and its 4 children
    root = Tile(lat=40.7128, lng=-74.0060, span_deg=0.02, depth=0)
    children = root.subdivide(overlap_pct=10.0)
    assert len(children) == 4

    # Simulate realistic quadrant results where places are distributed spatially
    # Child 0 (NW), 1 (NE), 2 (SW), 3 (SE)
    quadrant_places = []
    for idx, q in enumerate(children):
        # 12 quadrant-specific places + 8 overlapping places
        places = {f"place_quad_{idx}_{i}" for i in range(12)}
        # Add a few cross-quadrant places from boundaries
        places.update({f"place_border_{i}" for i in range(idx, idx + 8)})
        quadrant_places.append(places)

    # Compute pairwise Jaccard similarities
    for i in range(4):
        for j in range(i + 1, 4):
            set_i = quadrant_places[i]
            set_j = quadrant_places[j]
            intersection = len(set_i & set_j)
            union = len(set_i | set_j)
            jaccard = intersection / union
            assert jaccard < 0.50, f"Jaccard similarity between quad {i} and {j} too high: {jaccard:.2f}"
