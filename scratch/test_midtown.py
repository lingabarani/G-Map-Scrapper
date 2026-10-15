import asyncio
from gmaps_scraper.config import load_config
from gmaps_scraper.http import HttpClient
from gmaps_scraper.search import build_search_pb, parse_search_rpc_response
from gmaps_scraper.tiling import recursive_tile_search

async def test_midtown():
    cfg = load_config()
    client = HttpClient(user_agent=cfg.user_agent, accept_language=cfg.accept_language, hl=cfg.hl, gl=cfg.gl)
    # Midtown Manhattan around Grand Central / Bryant Park (all dense land)
    lat, lng, span = 40.753, -73.980, 0.015

    tile_results = []
    async def fetch(t_lat, t_lng, span_m):
        pb = build_search_pb(t_lat, t_lng, span_meters=span_m, num_results=20)
        url = f"https://www.google.com/search?tbm=map&authuser=0&hl=en&gl=us&pb={pb}&q=coffee"
        resp = await client.get(url)
        stubs = parse_search_rpc_response(resp.text)
        tile_results.append((t_lat, t_lng, span_m, len(stubs), [s.cid for s in stubs]))
        return stubs

    stubs = await recursive_tile_search(fetch, center_lat=lat, center_lng=lng, initial_span_deg=span, max_depth=2, overlap_pct=5.0)
    await client.close()

    total_raw = sum(t[3] for t in tile_results)
    unique = len(stubs)
    print(f"Midtown Search: {len(tile_results)} tiles processed")
    print(f"Total raw results: {total_raw}")
    print(f"Total unique places: {unique}")
    print(f"Duplication rate: {total_raw / unique:.2f}x (yield: {unique / total_raw * 100:.1f}%)")

if __name__ == "__main__":
    asyncio.run(test_midtown())
