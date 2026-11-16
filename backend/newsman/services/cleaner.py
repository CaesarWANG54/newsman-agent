from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import trafilatura
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid"}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_KEYS and not key.lower().startswith(TRACKING_PREFIXES)
    ]
    host = (parts.hostname or "").lower()
    if parts.port and (parts.scheme.lower(), parts.port) not in {("https", 443), ("http", 80)}:
        host += f":{parts.port}"
    clean = parts._replace(
        scheme=parts.scheme.lower(),
        netloc=host,
        path=parts.path.rstrip("/") or "/",
        fragment="",
        query=urlencode(sorted(query)),
    )
    return urlunsplit(clean)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def fetch_with_playwright(url: str, timeout_ms: int = 15000) -> str:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            return await page.content()
        finally:
            await browser.close()


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    status: str
    method: str = "http"
    http_status: int | None = None
    latency_ms: float = 0


def usable_body(text: str) -> bool:
    minimum = 80 if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 30 else 200
    return len(text) >= minimum


async def extract_article(
    url: str,
    fallback_html: str | None = None,
    *,
    allow_dynamic: bool = True,
) -> ExtractionResult:
    started = perf_counter()
    html = fallback_html or ""
    status_code = None
    if not html:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "NewsManAgent/0.1"})
                status_code = response.status_code
                response.raise_for_status()
                html = response.text
        except httpx.TimeoutException:
            return ExtractionResult("", "timeout", latency_ms=(perf_counter() - started) * 1000)
        except httpx.HTTPStatusError as exc:
            return ExtractionResult(
                "",
                "http_error",
                http_status=exc.response.status_code,
                latency_ms=(perf_counter() - started) * 1000,
            )
        except httpx.RequestError:
            return ExtractionResult(
                "", "network_error", latency_ms=(perf_counter() - started) * 1000
            )

    title = BeautifulSoup(html, "html.parser").title
    if title and any(
        word in title.get_text().lower()
        for word in (
            "access denied",
            "just a moment",
            "subscribe to continue",
        )
    ):
        return ExtractionResult(
            "",
            "blocked_body",
            http_status=status_code,
            latency_ms=(perf_counter() - started) * 1000,
        )

    extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
    text = clean_text(extracted)
    if usable_body(text):
        return ExtractionResult(
            text, "available", http_status=status_code, latency_ms=(perf_counter() - started) * 1000
        )

    if not allow_dynamic:
        return ExtractionResult(
            text,
            "insufficient",
            http_status=status_code,
            latency_ms=(perf_counter() - started) * 1000,
        )

    try:
        dynamic_html = await fetch_with_playwright(url)
    except Exception:
        return ExtractionResult(
            text,
            "insufficient",
            http_status=status_code,
            latency_ms=(perf_counter() - started) * 1000,
        )

    dynamic_text = clean_text(
        trafilatura.extract(dynamic_html, include_comments=False, include_tables=False)
    )
    best = dynamic_text if len(dynamic_text) > len(text) else text
    return ExtractionResult(
        best,
        "available" if usable_body(best) else "insufficient",
        method="playwright",
        http_status=status_code,
        latency_ms=(perf_counter() - started) * 1000,
    )


async def extract_article_text(url: str, fallback_html: str | None = None) -> str:
    return (await extract_article(url, fallback_html)).text
