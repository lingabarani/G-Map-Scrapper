# Developer & Contributor Guide (`CONTRIBUTING.md`)

## 1. Development Environment Setup

### 1.1 Prerequisites
- Python 3.10+ (Python 3.12 recommended).
- Git.
- SQLite 3.35+ (standard with Python 3.10+).

### 1.2 Local Setup Workflow
```bash
# 1. Clone the repository
git clone <repo-url> gmaps-scraper
cd gmaps-scraper

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Or .venv\Scripts\Activate.ps1 on Windows

# 3. Install dependencies in editable mode
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

# 4. Initialize local SQLite database
python -m gmaps_scraper init

# 5. Run automated test suite to confirm baseline
pytest
```

---

## 2. Code Architecture & Conventions

### 2.1 Code Style & Standards
- **Typing**: Use standard Python type hints (`typing.Optional`, `typing.List`, `typing.Dict`, `typing.Union`, or Python 3.10+ `|` syntax).
- **Pydantic Models**: All configuration and data exchange models must inherit from `pydantic.BaseModel` or `pydantic_settings.BaseSettings`.
- **Async Execution**: I/O-bound operations (HTTP requests, database reads/writes in worker loops) must use `async`/`await`. Avoid blocking standard library calls inside async functions.
- **Zero-CDN Rule**: Never add remote CDN links (e.g., Google Fonts, unpkg, cdnjs, Cloudflare) to HTML templates. All assets must reside locally in `src/gmaps_scraper/web/static/`.
- **Zero External Scraping API Rule**: Never introduce external paid scraping APIs (SerpApi, ScraperAPI, Apify). All Google Maps and website extraction logic must connect directly via `HttpClient`.

---

## 3. Extending the Platform

### 3.1 How to Add or Modify a Business Category
Business categories and verticals are defined in [`config/verticals.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/verticals.yaml):
1. Open [`config/verticals.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/verticals.yaml).
2. Add a new vertical block or append keywords to an existing block:
   ```yaml
   legal:
     label: "Legal & Professional Services"
     keywords:
       - lawyer
       - attorney
       - law firm
       - legal
       - notary
   ```
3. Run `pytest tests/test_taxonomy_and_filters.py` to verify precedence and classification behavior.

### 3.2 How to Add a New Region
Geographic boundaries are governed by [`config/pipeline2_regions.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/pipeline2_regions.yaml) and [`src/gmaps_scraper/scope.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/scope.py):
1. Update [`config/pipeline2_regions.yaml`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/config/pipeline2_regions.yaml) under the relevant country code with the region abbreviation and full name.
2. If necessary, define the geographic bounding box coordinates in [`src/gmaps_scraper/geo/boundaries.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/geo/boundaries.py).
3. Validate with `pytest tests/test_scope_and_tasks.py`.

### 3.3 Modifying Extraction or Normalization Logic
1. **Google Maps RPC**: Update [`search.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/search.py) or [`details.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/details.py). Ensure updates match verified JSON paths in [`docs/FIELD_MAP.md`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/docs/FIELD_MAP.md).
2. **Website Crawling & Profiling**: Update [`src/gmaps_scraper/website/extractor.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/website/extractor.py).
3. **Database Schema Changes**: Add schema migrations or update table definitions in [`src/gmaps_scraper/db.py:Database.init_schema()`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/db.py#L12). Update tests in `tests/test_db.py`.

---

## 4. Testing & Verification Requirements

Before submitting any code changes or opening a Pull Request:
1. **Run the Full Test Suite**:
   ```bash
   pytest
   ```
   All tests must pass (188/188).
2. **Add Regression Tests**:
   - For bug fixes, add a test reproducing the original issue and asserting the fix.
   - For new modules or endpoints, add unit tests with mocked fixtures under `tests/`.
3. **Check Code Formatting**:
   - Ensure clean imports and zero unhandled exceptions.

---

## 5. Security & Secret Protection Rules

- **Zero Credentials**: Never commit passwords, personal API tokens, SSH keys, or private proxy credentials.
- **SSRF Integrity**: Never disable or bypass [`src/gmaps_scraper/website/ssrf.py`](file:///r:/Roshan%20Onedata/Gmap%20scarp/gmaps-scraper/src/gmaps_scraper/website/ssrf.py) validation checks.
- **Data Privacy**: Do not commit real customer records, sensitive personal emails, or production database files (`*.sqlite`) to Git. Use the provided synthetic demo data fixtures in `tests/fixtures/` and `src/gmaps_scraper/web/demo_data.py`.

---

## 6. Git Branching & Pull Request Process

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. Commit changes with clear, descriptive commit messages:
   ```bash
   git commit -m "feat(extractor): add Schema.org Corporation JSON-LD support"
   ```
3. Ensure git status is clean and all 188 tests pass before pushing:
   ```bash
   git push origin feature/your-feature-name
   ```
