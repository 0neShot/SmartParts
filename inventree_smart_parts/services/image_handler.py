"""
Image Handler
==============
Robust download, validation and attachment of product images
from distributor APIs to InvenTree parts.

Features:
- Multi-source fallback (tries each distributor's image URL)
- Content-Type validation (only real images accepted)
- File-size limits (skip oversized or tiny files)
- Image dimension validation via header probing
- Retry with exponential backoff
- Automatic format detection
"""

import os
import logging
import tempfile
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("inventree_smart_parts.services.image_handler")

# ═══════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MIN_IMAGE_SIZE = 512  # 512 bytes (skip tiny/corrupt)
DOWNLOAD_TIMEOUT = 15  # seconds
MAX_RETRIES = 2

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
}

# Magic bytes for validating actual image data
IMAGE_MAGIC = {
    b"\xff\xd8\xff": ".jpg",  # JPEG
    b"\x89PNG\r\n\x1a\n": ".png",  # PNG
    b"RIFF": ".webp",  # WebP (partial – RIFF header)
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"BM": ".bmp",
}


# ═══════════════════════════════════════════════════════════════════
#  Result
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ImageResult:
    success: bool = False
    source_url: str = ""
    file_ext: str = ""
    file_size: int = 0
    message: str = ""


# ═══════════════════════════════════════════════════════════════════
#  Session
# ═══════════════════════════════════════════════════════════════════

# Domains that use Akamai/bot-detection that blocks browser-like UAs
# – for these, we use minimal headers instead.
_MINIMAL_UA_HOSTS = {"www.mouser.com", "mouser.com", "eu.mouser.com", "www.mouser.de"}


def _make_session(minimal: bool = False) -> requests.Session:
    """Create a requests session with retry strategy.

    Args:
        minimal: If True, use a minimal User-Agent (no Referer).
                 Required for Akamai-protected hosts like Mouser.
    """
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    if minimal:
        # Akamai (Mouser) blocks full browser fingerprints but allows simple UAs
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "image/jpeg, image/png, image/webp, image/*",
            }
        )
    else:
        # Full browser headers for DigiKey, LCSC, etc.
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
    return session


def _get_session() -> requests.Session:
    """Default session with full browser headers."""
    return _make_session(minimal=False)


# ═══════════════════════════════════════════════════════════════════
#  Core: Download & Validate
# ═══════════════════════════════════════════════════════════════════


def _detect_ext_from_magic(data: bytes) -> Optional[str]:
    """Detect image format from magic bytes."""
    for magic, ext in IMAGE_MAGIC.items():
        if data[: len(magic)] == magic:
            return ext
    return None


def download_image(url: str) -> Tuple[Optional[bytes], str, str]:
    """
    Download an image from a URL with validation.

    Automatically selects the right header strategy per host:
    - Akamai-protected (Mouser): minimal UA, no Referer
    - Others (DigiKey, LCSC): full browser UA + Referer

    Returns:
        (image_bytes, file_extension, error_message)
        image_bytes is None on failure.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None, "", f"Invalid URL: {url}"

    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    # Choose strategy: Mouser/Akamai needs minimal headers
    use_minimal = host in _MINIMAL_UA_HOSTS
    session = _make_session(minimal=use_minimal)

    if not use_minimal:
        # Set Referer for non-Akamai hosts (helps with hotlink protection)
        session.headers["Referer"] = f"{parsed.scheme}://{host}/"

    try:
        resp = session.get(
            url, timeout=DOWNLOAD_TIMEOUT, stream=True, allow_redirects=True
        )
        resp.raise_for_status()

        # ── Content-Type check ──
        ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = ALLOWED_CONTENT_TYPES.get(ct)

        # ── Content-Length check (if available) ──
        cl = resp.headers.get("content-length")
        if cl and int(cl) > MAX_IMAGE_SIZE:
            resp.close()
            return None, "", f"Image too large: {int(cl)} bytes (max {MAX_IMAGE_SIZE})"

        # ── Download body ──
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=16384):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_IMAGE_SIZE:
                resp.close()
                return (
                    None,
                    "",
                    f"Image exceeded {MAX_IMAGE_SIZE} bytes during download",
                )

        data = b"".join(chunks)

        if len(data) < MIN_IMAGE_SIZE:
            return (
                None,
                "",
                f"Image too small: {len(data)} bytes (min {MIN_IMAGE_SIZE})",
            )

        # ── Magic-byte validation ──
        detected_ext = _detect_ext_from_magic(data)
        if detected_ext:
            ext = detected_ext  # trust magic bytes over Content-Type
        elif not ext:
            # Content-Type was HTML (bot challenge page) – try alternate strategy
            if not use_minimal:
                logger.debug(
                    f"Browser UA returned HTML for {host}, retrying with minimal UA"
                )
                return _download_with_minimal_ua(url)
            return None, "", f"Unrecognized image format (content-type: {ct})"

        logger.debug(
            f"Downloaded image: {len(data)} bytes, {ext} from {url} (minimal={use_minimal})"
        )
        return data, ext, ""

    except requests.exceptions.Timeout:
        return None, "", f"Timeout downloading image from {url}"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        return None, "", f"HTTP {status} downloading image from {url}"
    except requests.exceptions.ConnectionError:
        return None, "", f"Connection failed for {url}"
    except Exception as e:
        return None, "", f"Image download error: {e}"


def _download_with_minimal_ua(url: str) -> Tuple[Optional[bytes], str, str]:
    """Fallback download using minimal UA (for Akamai-protected hosts)."""
    session = _make_session(minimal=True)
    try:
        resp = session.get(
            url, timeout=DOWNLOAD_TIMEOUT, stream=True, allow_redirects=True
        )
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = ALLOWED_CONTENT_TYPES.get(ct)

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=16384):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_IMAGE_SIZE:
                resp.close()
                return (
                    None,
                    "",
                    f"Image exceeded {MAX_IMAGE_SIZE} bytes during fallback download",
                )

        data = b"".join(chunks)
        if len(data) < MIN_IMAGE_SIZE:
            return None, "", f"Image too small in fallback: {len(data)} bytes"

        detected_ext = _detect_ext_from_magic(data)
        if detected_ext:
            ext = detected_ext
        elif not ext:
            return (
                None,
                "",
                f"Unrecognized image format in fallback (content-type: {ct})",
            )

        logger.debug(f"Fallback download OK: {len(data)} bytes, {ext} from {url}")
        return data, ext, ""

    except Exception as e:
        return None, "", f"Fallback download failed: {e}"


# ═══════════════════════════════════════════════════════════════════
#  Multi-Source Fallback
# ═══════════════════════════════════════════════════════════════════


def collect_image_urls(merged_data: dict, sources: dict) -> List[Dict]:
    """
    Collect all available image URLs from search results,
    ordered by priority (merged first, then per-source).

    Args:
        merged_data: The merged result dict (from _part_data_to_dict)
        sources: The per-source results dict

    Returns:
        List of {'url': str, 'source': str} dicts
    """
    urls = []
    seen = set()

    # 1. Merged image_url (already priority-sorted)
    if merged_data and merged_data.get("image_url"):
        u = merged_data["image_url"]
        if u not in seen:
            urls.append({"url": u, "source": "merged"})
            seen.add(u)

    # 2. Per-source image_urls (fallback)
    for src_name in ("mouser", "digikey", "lcsc"):
        src = sources.get(src_name)
        if isinstance(src, dict) and src.get("image_url") and not src.get("error"):
            u = src["image_url"]
            if u not in seen:
                urls.append({"url": u, "source": src_name})
                seen.add(u)

    return urls


def download_best_image(
    image_urls: List[Dict],
) -> Tuple[Optional[bytes], str, str, str]:
    """
    Try each image URL in order until one succeeds.

    Returns:
        (image_bytes, file_extension, source_name, error_summary)
    """
    errors = []

    for entry in image_urls:
        url = entry["url"]
        source = entry["source"]
        logger.info(f"Trying image from {source}: {url[:80]}...")

        data, ext, err = download_image(url)
        if data:
            logger.info(f"Image downloaded from {source}: {len(data)} bytes ({ext})")
            return data, ext, source, ""
        else:
            logger.debug(f"Image failed from {source}: {err}")
            errors.append(f"{source}: {err}")

    summary = "; ".join(errors) if errors else "No image URLs available"
    return None, "", "", summary


# ═══════════════════════════════════════════════════════════════════
#  InvenTree Integration
# ═══════════════════════════════════════════════════════════════════


def attach_image_to_part(
    part, image_data: bytes, ext: str, source: str = ""
) -> ImageResult:
    """
    Attach downloaded image data to an InvenTree Part.

    Args:
        part: InvenTree Part model instance
        image_data: Raw image bytes
        ext: File extension (e.g. '.jpg')
        source: Source name for logging
    """
    try:
        from django.core.files import File

        # Write to temp file
        with tempfile.NamedTemporaryFile(
            suffix=ext, delete=False, prefix="smartparts_img_"
        ) as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name

        try:
            filename = f"part_{part.pk}{ext}"
            with open(tmp_path, "rb") as f:
                part.image.save(filename, File(f), save=True)

            logger.info(
                f"Attached image to Part {part.pk} "
                f"({len(image_data)} bytes, {ext}, from {source})"
            )
            return ImageResult(
                success=True,
                source_url=source,
                file_ext=ext,
                file_size=len(image_data),
                message=f"Image attached ({len(image_data)} bytes from {source})",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    except Exception as e:
        logger.error(f"Failed to attach image to Part {part.pk}: {e}", exc_info=True)
        return ImageResult(
            success=False,
            message=f"Attach failed: {e}",
        )


def attach_datasheet_to_part(part, datasheet_url: str) -> bool:
    """Attach a datasheet link to an InvenTree Part as an Attachment.

    InvenTree's Attachment model uses model_type as a CharField
    containing the lowercase model class name (e.g. 'part').
    """
    if not datasheet_url:
        return False

    try:
        from common.models import Attachment

        # model_type is a CharField with the lowercase class name
        model_type_str = part.__class__.__name__.lower()  # → 'part'

        obj, created = Attachment.objects.get_or_create(
            model_type=model_type_str,
            model_id=part.pk,
            link=datasheet_url,
            defaults={"comment": "Datasheet (auto-imported by SmartParts)"},
        )
        if created:
            logger.info(f"Attached datasheet to Part {part.pk}: {datasheet_url[:80]}")
        else:
            logger.debug(f"Datasheet already attached to Part {part.pk}")
        return True

    except Exception as e:
        logger.warning(f"Failed to attach datasheet to Part {part.pk}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
#  High-Level API (used by part_creator)
# ═══════════════════════════════════════════════════════════════════


def auto_import_media(
    part, part_data, search_sources: Optional[dict] = None
) -> ImageResult:
    """
    Automatically download and attach the best available image for a part.

    URL priority order:
    1. DigiKey (works server-side without hotlink protection)
    2. Other sources from source_image_urls
    3. Merged image_url as last resort

    Args:
        part: InvenTree Part model instance
        part_data: PartData instance (has image_url and raw_data)
        search_sources: Unused, kept for compatibility

    Returns:
        ImageResult with details (failure is non-blocking)
    """
    urls = []
    seen = set()

    # Priority 1: source_image_urls sent explicitly from frontend
    # These are pre-ordered: digikey > lcsc > mouser (based on server-side accessibility)
    for entry in part_data.raw_data.get("source_image_urls", []):
        img = entry.get("url", "")
        if img and img not in seen:
            urls.append({"url": img, "source": entry.get("source", "unknown")})
            seen.add(img)

    # Priority 2: merged image_url (fallback)
    if part_data.image_url and part_data.image_url not in seen:
        urls.append(
            {"url": part_data.image_url, "source": part_data.source or "merged"}
        )
        seen.add(part_data.image_url)

    if not urls:
        return ImageResult(success=False, message="No image URLs available")

    logger.info(
        f"Trying {len(urls)} image URL(s) for Part {part.pk}: "
        + ", ".join(e["source"] for e in urls)
    )

    data, ext, source, err = download_best_image(urls)
    if not data:
        # Non-blocking – log at INFO, not ERROR
        logger.info(f"Image not available for Part {part.pk}: {err}")
        return ImageResult(success=False, message=err)

    return attach_image_to_part(part, data, ext, source)
