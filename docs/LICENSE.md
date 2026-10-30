# Licensing Information (`LICENSE.md`)

## 1. Project Licensing Status

> [!IMPORTANT]
> **No formal repository license file (`LICENSE` or `LICENSE.txt`) exists in the repository root.**
> An explicit licensing decision has not yet been declared by the project author or copyright holder. All rights are reserved by default under statutory copyright laws until a formal open-source or proprietary software license is explicitly assigned.

Repository maintainers are advised to select and commit a standard open-source license (such as MIT, Apache 2.0, or AGPLv3) or a commercial proprietary license depending on the intended distribution model.

---

## 2. Third-Party Dependency Licenses

The `gmaps-scraper` application depends upon third-party open-source software libraries, each governed by its respective copyright and license terms:

| Dependency Package | Declared License | Licensing Scope |
| :--- | :--- | :--- |
| `fastapi` | MIT License | Web framework and routing engine |
| `uvicorn` | BSD-3-Clause License | ASGI web server implementation |
| `jinja2` | BSD-3-Clause License | HTML template engine |
| `pydantic` | MIT License | Data validation and typing |
| `pydantic-settings`| MIT License | Configuration management |
| `typer` | MIT License | Command-line interface builder |
| `rich` | MIT License | Terminal formatting and tables |
| `httpx` | BSD-3-Clause License | Asynchronous HTTP client library |
| `tenacity` | Apache License 2.0 | Exponential backoff and retry decorators |
| `pandas` | BSD-3-Clause License | Tabular data analysis and manipulation |
| `pyarrow` | Apache License 2.0 | Parquet columnar binary file serialization |
| `pyyaml` | MIT License | YAML configuration parser |
| `pytest` | MIT License | Automated testing framework |
| `pytest-asyncio` | Apache License 2.0 | Asynchronous Pytest plugin |
| `brotli` | MIT License | Brotli decompression algorithm |
| `requests` | Apache License 2.0 | HTTP library |

All dependencies are permissible for internal and commercial use under their respective permissive open-source licenses (MIT, Apache 2.0, BSD-3-Clause).

---

## 3. Data Ingestion & Legal Notice

The software is designed as a data collection, verification, and analysis tool:
1. **Public Information**: The scraper extracts publicly available business directory metadata, operating schedules, public corporate website content, and Schema.org structured data.
2. **Robots.txt & Politeness**: Operational configurations must respect server rate limits, network politeness principles, and website terms of service.
3. **Data Protection & Privacy**: Users of this software are responsible for ensuring that their collection, storage, and processing of business contact channels comply with relevant jurisdictions' data privacy regulations (e.g., GDPR, CCPA, CAN-SPAM).
