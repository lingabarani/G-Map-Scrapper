import asyncio
from gmaps_scraper.config import load_config
from gmaps_scraper.http import HttpClient
from gmaps_scraper.search import build_search_pb, parse_search_rpc_response

async def main():
    cfg = load_config()
    client = HttpClient(user_agent=cfg.user_agent, accept_language=cfg.accept_language, hl=cfg.hl, gl=cfg.gl)
    queries = ['scuba diving nyc', 'ethiopian restaurant fi di', 'pottery studio fidi', 'specialty coffee roastery financial district']
    for q in queries:
        pb = build_search_pb(40.704, -74.010, span_meters=500)
        url = f"https://www.google.com/search?tbm=map&authuser=0&hl=en&gl=us&pb={pb}&q={q}"
        resp = await client.get(url)
        stubs = parse_search_rpc_response(resp.text)
        print(f"Query '{q}': {len(stubs)} results")
    await client.close()

if __name__ == '__main__':
    asyncio.run(main())
