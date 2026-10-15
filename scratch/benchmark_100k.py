import time
import tempfile
import statistics
from pathlib import Path
from gmaps_scraper.db import Database

def benchmark_100k():
    print("=== SEEDING 100,000 ROWS FOR H10 BENCHMARK ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "scale_100k.sqlite"
        db = Database(db_path)
        db.init_schema()

        t0_seed = time.perf_counter()
        batch_size = 10000
        statuses = ["pending", "details_done", "reviews_done", "failed"]
        categories = ["Coffee shop", "Bakery", "Cafe", "Restaurant", "Bar", "Bookstore"]

        with db.get_connection() as conn:
            for b in range(10):
                batch = []
                for i in range(batch_size):
                    idx = b * batch_size + i
                    batch.append((
                        f"scale_cid_{idx}",
                        f"0x1:0x{idx:x}",
                        f"ChIJ_{idx}",
                        f"Business Name #{idx}",
                        f"{idx} Main St, City, ST",
                        f"{idx} Main St",
                        40.7000 + (idx % 1000) * 0.0001,
                        -74.0000 - (idx % 1000) * 0.0001,
                        f"https://place{idx}.test" if idx % 3 == 0 else None,
                        f"+1 212 555 {idx:04d}" if idx % 2 == 0 else None,
                        f"+1212555{idx:04d}" if idx % 2 == 0 else None,
                        categories[(idx // 4) % len(categories)],
                        3.0 + (idx % 20) * 0.1,
                        (idx * 7) % 500,
                        statuses[idx % len(statuses)],
                        1 if idx % 4 == 0 else 0
                    ))
                conn.executemany("""
                    INSERT INTO places (
                        cid, ftid, place_id, name, address, street_address,
                        latitude, longitude, website, phone, phone_e164,
                        category, rating, review_count, status, claimed_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
            conn.commit()

        seed_elapsed = time.perf_counter() - t0_seed
        print(f"Seeded 100,000 rows in {seed_elapsed:.2f}s.")

        # Verify count
        with db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
            assert count == 100000
            print(f"Verified row count: {count}")

            # b) EXPLAIN QUERY PLAN
            print("\n=== EXPLAIN QUERY PLAN (List Endpoint Query) ===")
            queries_to_test = [
                ("Order by Rating Desc", "SELECT * FROM places WHERE status = 'details_done' ORDER BY rating DESC, cid DESC LIMIT 50 OFFSET 0;"),
                ("Order by Review Count", "SELECT * FROM places WHERE category = 'Coffee shop' ORDER BY review_count DESC, cid DESC LIMIT 50 OFFSET 0;"),
                ("Lookup by Status", "SELECT * FROM places WHERE status = 'pending' LIMIT 50;")
            ]
            for desc, sql in queries_to_test:
                plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
                print(f"\nQuery: {desc}")
                print(f"SQL: {sql}")
                for row in plan:
                    print(f"  Plan: {dict(row)}")

        # c) Benchmark 100 query runs for p50, p99 latency
        print("\n=== EXECUTING 100 REAL PAGINATED QUERIES AT 100K ROWS ===")
        latencies_ms = []
        for i in range(100):
            t0 = time.perf_counter()
            places, total = db.query_places(
                status="details_done",
                category="Coffee shop",
                min_rating=4.0,
                sort="rating_desc",
                page=(i % 10) + 1,
                page_size=50
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies_ms.append(elapsed_ms)
            if len(places) != 50:
                print(f"Iteration {i}: places count={len(places)}, total={total}")
            assert len(places) > 0
            assert total > 0

        p50 = statistics.median(latencies_ms)
        p90 = sorted(latencies_ms)[int(len(latencies_ms) * 0.90)]
        p99 = sorted(latencies_ms)[int(len(latencies_ms) * 0.99)]
        mean = statistics.mean(latencies_ms)
        min_l = min(latencies_ms)
        max_l = max(latencies_ms)

        print(f"\n--- LATENCY BENCHMARK RESULTS (100 runs over 100k rows) ---")
        print(f"Min:  {min_l:.2f} ms")
        print(f"Mean: {mean:.2f} ms")
        print(f"p50:  {p50:.2f} ms")
        print(f"p90:  {p90:.2f} ms")
        print(f"p99:  {p99:.2f} ms")
        print(f"Max:  {max_l:.2f} ms")
        assert p99 < 500.0, f"p99 latency {p99:.2f}ms exceeds 500ms requirement!"
        print("\nPASS: All queries completed well under 500ms limit!")

if __name__ == "__main__":
    benchmark_100k()
