import logging
import time

import httpx

_API_BASE = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _fetch_strategy(url: str, strategy: str, api_key: str) -> dict | None:
    endpoint = f"{_API_BASE}?url={url}&strategy={strategy}&key={api_key}"
    try:
        resp = httpx.get(endpoint, timeout=30)
    except Exception as exc:
        logging.warning("pagespeed: request error (%s %s): %s", strategy, url, exc)
        return None

    if resp.status_code == 429:
        logging.warning("pagespeed: 429 rate-limited (%s %s), retrying in 5s", strategy, url)
        time.sleep(5)
        try:
            resp = httpx.get(endpoint, timeout=30)
        except Exception as exc:
            logging.warning("pagespeed: retry request error (%s %s): %s", strategy, url, exc)
            return None
        if resp.status_code != 200:
            logging.warning("pagespeed: retry failed with %s (%s %s)", resp.status_code, strategy, url)
            return None

    if resp.status_code != 200:
        logging.warning("pagespeed: non-200 response %s (%s %s)", resp.status_code, strategy, url)
        return None

    return resp.json()


def _extract_metrics(data: dict) -> dict:
    lr = data.get("lighthouseResult", {})
    categories = lr.get("categories", {})
    audits = lr.get("audits", {})

    raw_score = (categories.get("performance") or {}).get("score")
    score = int(round(raw_score * 100)) if raw_score is not None else None

    lcp_ms = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    lcp = round(lcp_ms / 1000, 2) if lcp_ms is not None else None

    cls_raw = (audits.get("cumulative-layout-shift") or {}).get("numericValue")
    cls = round(cls_raw, 3) if cls_raw is not None else None

    inp_raw = (audits.get("interaction-to-next-paint") or {}).get("numericValue")
    if inp_raw is None:
        inp_raw = (audits.get("total-blocking-time") or {}).get("numericValue")
    inp = int(round(inp_raw)) if inp_raw is not None else None

    return {"score": score, "lcp": lcp, "cls": cls, "inp": inp}


def check_pagespeed(url: str, api_key: str | None) -> dict | None:
    if api_key is None:
        return None

    mobile_data = _fetch_strategy(url, "mobile", api_key)
    if mobile_data is None:
        return None

    desktop_data = _fetch_strategy(url, "desktop", api_key)
    if desktop_data is None:
        return None

    mobile = _extract_metrics(mobile_data)
    desktop = _extract_metrics(desktop_data)

    mobile_score = mobile["score"]
    status = "PASS" if (mobile_score is not None and mobile_score >= 70) else "FAIL"

    return {
        "score": mobile_score,
        "lcp": mobile["lcp"],
        "cls": mobile["cls"],
        "inp": mobile["inp"],
        "mobile_score": mobile_score,
        "desktop_score": desktop["score"],
        "status": status,
    }
