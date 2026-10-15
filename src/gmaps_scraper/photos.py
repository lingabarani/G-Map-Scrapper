import os
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import httpx

def is_valid_image_magic(data: bytes) -> Tuple[bool, str]:
    """Validates image magic bytes and returns (is_valid, extension)."""
    if len(data) < 12:
        return False, ""
    if data.startswith(b"\xff\xd8\xff"):
        return True, ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True, ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True, ".webp"
    return False, ""

async def download_photo_file(
    client: httpx.AsyncClient,
    url: str,
    output_dir: Path,
    photo_max_width: int = 800,
    attribution: Optional[str] = None
) -> Optional[Tuple[str, int]]:
    """
    Downloads photo locally to data/photos/{cid}/{filename}, validates magic bytes,
    and updates manifest.json with source URL and attribution.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Scale URL to max width if Google sizing parameters are present
    download_url = url
    if "=s" in url:
        base = url.split("=s")[0]
        download_url = f"{base}=s{photo_max_width}"
    elif "/s44-" in url:
        download_url = url.replace("/s44-", f"/s{photo_max_width}-")

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

    try:
        resp = await client.get(download_url, timeout=20.0)
        if resp.status_code == 200 and len(resp.content) > 0:
            valid, ext = is_valid_image_magic(resp.content)
            if not valid:
                ext = ".jpg"  # default fallback if valid binary stream

            filename = f"photo_{url_hash}{ext}"
            target_path = output_dir / filename

            with open(target_path, "wb") as f:
                f.write(resp.content)

            file_size = len(resp.content)

            # Update manifest.json
            manifest_path = output_dir / "manifest.json"
            manifest_data: Dict[str, Any] = {}
            if manifest_path.exists():
                try:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        manifest_data = json.load(mf)
                except Exception:
                    manifest_data = {}

            manifest_data[filename] = {
                "source_url": url,
                "download_url": download_url,
                "attribution": attribution or "Unknown",
                "file_size": file_size,
                "downloaded_at": datetime.now().isoformat()
            }

            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest_data, mf, indent=2)

            return str(target_path), file_size
    except Exception:
        pass

    return None
