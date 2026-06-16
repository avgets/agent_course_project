import asyncio
import json
import sys
from urllib.parse import urlparse

from crawl4ai import (
    AsyncWebCrawler,
    AdaptiveCrawler,
    AdaptiveConfig,
    BrowserConfig,
    CrawlerRunConfig,
)

STEALTH_BROWSER_CONFIG = BrowserConfig(
    browser_type="chromium",
    headless=True,
    viewport_width=1366,
    viewport_height=768,
    user_agent_mode="random",
    java_script_enabled=True,
    verbose=True,
)

STEALTH_HEADERS = {
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

PAGE_RUN_CONFIG = CrawlerRunConfig(
    word_count_threshold=1,
    page_timeout=15000,
    wait_until="networkidle",
    scan_full_page=True,
    remove_overlay_elements=True,
    simulate_user=True,
    magic=True,
)

BLACKLIST_SEARCH_DOMAINS = {
    "yandex.ru",
    "ya.ru",
    "yabs.yandex.ru",
}

ADAPTIVE_QUERY = (
    "инвесторам дивиденды акции облигации ценные бумаги купон купонная выплата "
    "дефолт неисполнение обязательств дополнительная эмиссия "
    "дополнительный выпуск раскрытие информации финансовая отчетность показатели"
    "корпоративные события инвестиции"
)


def _normalize_links(links):
    out = []
    seen = set()

    for link in links or []:
        href = (link.get("href") or "").strip()
        text = (link.get("text") or "").strip()

        if not href.startswith("http"):
            continue
        if href in seen:
            continue

        seen.add(href)
        out.append({
            "text": text[:300],
            "href": href,
        })

    return out


def _search_candidates(result):
    raw_internal = result.links.get("internal", []) if result.links else []
    raw_external = result.links.get("external", []) if result.links else []

    candidates = []
    seen = set()

    for link in list(raw_internal) + list(raw_external):
        href = (link.get("href") or "").strip()
        text = (link.get("text") or "").strip()

        if not href.startswith("http"):
            continue

        netloc = urlparse(href).netloc.lower()
        if any(bad in netloc for bad in BLACKLIST_SEARCH_DOMAINS):
            continue

        if href in seen:
            continue

        seen.add(href)
        candidates.append({
            "title": text[:300],
            "url": href,
        })

        if len(candidates) >= 10:
            break

    return candidates


def _normalize_adaptive_docs(docs):
    out = []

    for doc in docs or []:
        if isinstance(doc, dict):
            out.append({
                "url": doc.get("url", ""),
                "title": doc.get("title", ""),
                "content": (doc.get("content") or "")[:5000],
                "score": doc.get("score"),
                "metadata": doc.get("metadata", {}),
            })
        else:
            out.append({
                "repr": str(doc)[:5000]
            })

    return out


async def crawl_search(url: str) -> dict:
    async with AsyncWebCrawler(
        config=STEALTH_BROWSER_CONFIG
    ) as crawler:
        result = await crawler.arun(
            url=url,
            config=PAGE_RUN_CONFIG,
            headers=STEALTH_HEADERS,
        )

    if not result:
        return {
            "ok": False,
            "error": "search crawl returned no result",
        }

    return {
        "ok": True,
        "url": url,
        "title": getattr(result, "title", "") or "",
        "markdown": (result.markdown or "")[:12000],
        "search_results": _search_candidates(result),
    }


async def crawl_page(url: str) -> dict:
    async with AsyncWebCrawler(
        config=STEALTH_BROWSER_CONFIG
    ) as crawler:
        result = await crawler.arun(
            url=url,
            config=PAGE_RUN_CONFIG,
            headers=STEALTH_HEADERS,
        )

    if not result:
        return {
            "ok": False,
            "error": f"failed to crawl {url}",
        }

    return {
        "ok": True,
        "url": url,
        "title": getattr(result, "title", "") or "",
        "markdown": (result.markdown or "")[:15000],
        "internal_links": _normalize_links((result.links or {}).get("internal", []))[:50],
        "external_links": _normalize_links((result.links or {}).get("external", []))[:50],
    }


async def crawl_adaptive(url: str) -> dict:
    config = AdaptiveConfig(
        confidence_threshold=0.5,
        max_pages=20,
        top_k_links=3,
        min_gain_threshold=0.08,
    )

    async with AsyncWebCrawler(
        config=STEALTH_BROWSER_CONFIG
    ) as crawler:
        adaptive = AdaptiveCrawler(crawler, config=config)
        state = await adaptive.digest(
            start_url=url,
            query=ADAPTIVE_QUERY,
        )
        top_docs = adaptive.get_relevant_content(top_k=10)

    return {
        "ok": True,
        "url": url,
        "adaptive_query": ADAPTIVE_QUERY,
        "adaptive_confidence": getattr(adaptive, "confidence", None),
        "adaptive_coverage_stats": getattr(adaptive, "coverage_stats", {}),
        "adaptive_crawled_urls": list(getattr(state, "crawled_urls", [])),
        "adaptive_top_docs": _normalize_adaptive_docs(top_docs),
    }


async def main():
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "usage: python crawl_bridge.py <search|page|adaptive> <url>",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)

    mode = sys.argv[1].strip().lower()
    url = sys.argv[2].strip()

    try:
        if mode == "search":
            payload = await crawl_search(url)
        elif mode == "page":
            payload = await crawl_page(url)
        elif mode == "adaptive":
            payload = await crawl_adaptive(url)
        else:
            payload = {
                "ok": False,
                "error": f"unknown mode: {mode}",
            }
    except Exception as e:
        payload = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }

    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())