import asyncio
from gmaps_scraper.config import load_config
from gmaps_scraper.http import HttpClient
from gmaps_scraper.reviews import build_boq_reviews_url, parse_reviews_response

async def paginate_787():
    cfg = load_config()
    client = HttpClient(user_agent=cfg.user_agent, accept_language=cfg.accept_language, hl=cfg.hl, gl=cfg.gl)
    ftid = "0x89c25bb26cc76c9f:0xa38e11c309a5d41e"
    cid = "11785376804253324318"

    token = None
    total_revs = 0
    page = 0
    # Let's test up to 10 pages and print the token at each hop
    while page < 10:
        page += 1
        url = build_boq_reviews_url(ftid, page_size=10, sort_code=1, pagination_token=token)
        resp = await client.get(url)
        revs, next_token = parse_reviews_response(resp.text, cid)
        total_revs += len(revs)
        print(f"Page {page}: Fetched {len(revs)} reviews (Total: {total_revs})")
        print(f"  Current Token: {token[:30] + '...' if token else 'None'}")
        print(f"  Next Token:    {next_token[:30] + '...' if next_token else 'None'}")

        if not revs:
            print("  Termination: revs list is empty")
            break
        if not next_token or next_token == token:
            print("  Termination: next_token is None or unchanged")
            break
        token = next_token

    await client.close()

if __name__ == "__main__":
    asyncio.run(paginate_787())
