import json
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


def fetch_page(url: str) -> dict:
    result = {
        "final_url": None,
        "status_code": None,
        "title": None,
        "meta_description": None,
        "h1s": [],
        "canonical": None,
        "raw_links": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        # Track the first response status (before redirect following)
        first_status: dict = {"code": None}

        def on_response(response):
            if first_status["code"] is None:
                first_status["code"] = response.status

        page.on("response", on_response)

        try:
            # "commit" fires as soon as the HTTP response headers are received,
            # even for 4xx/5xx — avoids ERR_HTTP_RESPONSE_CODE_FAILURE on errors
            page.goto(url, wait_until="commit", timeout=30000)
            result["status_code"] = first_status["code"]
            result["final_url"] = page.url

            time.sleep(2)

            # Only extract SEO fields for successful (2xx) pages
            if result["status_code"] and result["status_code"] < 400:
                result["title"] = page.title() or None

                meta = page.query_selector('meta[name="description"]')
                if meta:
                    result["meta_description"] = meta.get_attribute("content")

                h1_elements = page.query_selector_all("h1")
                result["h1s"] = [
                    el.inner_text().strip()
                    for el in h1_elements
                    if el.inner_text().strip()
                ]

                canonical = page.query_selector('link[rel="canonical"]')
                if canonical:
                    result["canonical"] = canonical.get_attribute("href")

                link_elements = page.query_selector_all("a[href]")
                result["raw_links"] = [
                    el.get_attribute("href")
                    for el in link_elements[:100]
                ]

        except PlaywrightTimeout:
            result["status_code"] = first_status["code"] or 408
            result["final_url"] = url
        except Exception as exc:
            print(f"[browser] Error fetching {url}: {exc}", file=sys.stderr)
            result["status_code"] = first_status["code"]
            # page.url is "about:blank" when navigation fails before commit
            result["final_url"] = page.url if page.url not in ("", "about:blank") else url
        finally:
            browser.close()

    return result


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://dev.to/dannwaneri"
    data = fetch_page(target)
    print(json.dumps(data, indent=2, ensure_ascii=False))
