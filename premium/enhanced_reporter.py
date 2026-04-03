"""
Professional PDF report generator for SEO audit results.

HTML is built first; WeasyPrint converts it to PDF.
Falls back to fpdf2 on systems where WeasyPrint's native libraries
(GTK/Pango) are unavailable (common on Windows without the GTK runtime).
"""

import logging
import os
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

_FIELD_SEVERITY = {
    "canonical": "high",
    "h1":        "high",
    "title":     "medium",
    "description": "medium",
    "broken_links": "low",
}

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_FIX_TEXT = {
    "title":       "Shorten title to under 60 characters",
    "description": "Add or shorten meta description to under 160 characters",
    "canonical":   "Add a canonical link tag pointing to the preferred URL",
    "broken_links": "Fix or remove broken internal links",
}


def assign_severity(field: str, result: dict) -> str:
    """Return 'high', 'medium', or 'low' for a failing field."""
    return _FIELD_SEVERITY.get(field, "low")


def _h1_fix(result: dict) -> str:
    h1 = result.get("h1") or {}
    if h1.get("count", 0) == 0:
        return "Add a single H1 tag to the page"
    return "Remove duplicate H1 tags — keep only one"


def _fix_text(field: str, result: dict) -> str:
    if field == "h1":
        return _h1_fix(result)
    return _FIX_TEXT.get(field, "Review and fix this field")


def _failing_fields(result: dict) -> list[str]:
    """Return list of field names that are FAIL, sorted high -> medium -> low."""
    fields = ("title", "description", "h1", "canonical", "broken_links")
    failing = [
        f for f in fields
        if (result.get(f) or {}).get("status") == "FAIL"
    ]
    return sorted(failing, key=lambda f: _SEVERITY_ORDER[assign_severity(f, result)])


def _is_pass(result: dict) -> bool:
    return len(_failing_fields(result)) == 0


def _method_label(method: str) -> str:
    return {"deterministic": "Tier 1 (Deterministic)", "haiku": "Tier 2 (Haiku)", "sonnet": "Tier 3 (Sonnet)"}.get(method, method or "—")


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; font-family: Arial, sans-serif; }
body { padding: 32px; color: #1a1a1a; font-size: 13px; }
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 16px; margin: 24px 0 8px; border-bottom: 2px solid #e2e8f0; padding-bottom: 4px; }
h3 { font-size: 13px; margin: 16px 0 6px; color: #4a5568; }
.dashboard { background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 6px;
             padding: 20px; margin-bottom: 32px; }
.stats { display: flex; gap: 32px; margin-top: 12px; flex-wrap: wrap; }
.stat { text-align: center; }
.stat .num { font-size: 28px; font-weight: bold; }
.stat .lbl { font-size: 11px; color: #718096; text-transform: uppercase; letter-spacing: .5px; }
.pass { color: #276749; }
.fail { color: #c53030; }
.url-section { margin-bottom: 40px; page-break-inside: avoid; }
.url-header { display: flex; justify-content: space-between; align-items: center;
              background: #2d3748; color: white; padding: 10px 16px; border-radius: 4px 4px 0 0; }
.url-header .url { font-size: 12px; word-break: break-all; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 12px;
         font-weight: bold; font-size: 11px; }
.badge-pass { background: #c6f6d5; color: #22543d; }
.badge-fail { background: #fed7d7; color: #742a2a; }
.screenshot-placeholder { background: #edf2f7; border: 2px dashed #cbd5e0;
    height: 80px; display: flex; align-items: center; justify-content: center;
    color: #a0aec0; font-size: 12px; margin: 12px 0; border-radius: 4px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th { background: #edf2f7; text-align: left; padding: 6px 10px; font-size: 11px;
     text-transform: uppercase; letter-spacing: .4px; color: #4a5568; }
td { padding: 6px 10px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
.sev-high   { color: #c53030; font-weight: bold; }
.sev-medium { color: #c05621; }
.sev-low    { color: #2b6cb0; }
.method-tag { font-size: 10px; color: #718096; margin-top: 8px; }
.common-fails { margin-top: 10px; }
.common-fails li { margin: 3px 0 3px 18px; }
.badge-warn    { background: #fefcbf; color: #744210; }
.badge-missing { background: #e2e8f0; color: #4a5568; }
.metrics { display: flex; gap: 20px; margin: 8px 0; flex-wrap: wrap; }
.metric  { background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 4px; padding: 8px 14px; min-width: 80px; }
.metric .val { font-size: 15px; font-weight: bold; }
.metric .lbl { font-size: 10px; color: #718096; text-transform: uppercase; letter-spacing: .4px; }
.not-checked { background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 4px;
               padding: 10px 14px; color: #a0aec0; font-size: 12px; font-style: italic; margin: 8px 0; }
.flags-list { margin: 6px 0 0 18px; }
.flags-list li { margin: 2px 0; color: #c53030; font-size: 12px; }
"""


def _performance_html(result: dict) -> str:
    perf = result.get("performance")
    if perf is None:
        return '<h3>Performance</h3><div class="not-checked">Not checked</div>'

    mobile = perf.get("mobile_score")
    desktop = perf.get("desktop_score")
    lcp = perf.get("lcp")
    cls_ = perf.get("cls")
    inp = perf.get("inp")
    status = perf.get("status", "FAIL")

    def score_badge(score):
        if score is None:
            return '<span class="badge badge-missing">N/A</span>'
        cls = "badge-pass" if score >= 70 else "badge-fail"
        return f'<span class="badge {cls}">{score}</span>'

    overall_cls = "badge-pass" if status == "PASS" else "badge-fail"

    return f"""<h3>Performance</h3>
<div class="metrics">
  <div class="metric"><div class="val">{score_badge(mobile)}</div><div class="lbl">Mobile Score</div></div>
  <div class="metric"><div class="val">{score_badge(desktop)}</div><div class="lbl">Desktop Score</div></div>
  <div class="metric"><div class="val">{lcp if lcp is not None else "—"}s</div><div class="lbl">LCP</div></div>
  <div class="metric"><div class="val">{cls_ if cls_ is not None else "—"}</div><div class="lbl">CLS</div></div>
  <div class="metric"><div class="val">{inp if inp is not None else "—"}ms</div><div class="lbl">INP</div></div>
</div>
<p>Overall: <span class="badge {overall_cls}">{status}</span></p>"""


def _structured_data_html(result: dict) -> str:
    sd = result.get("structured_data")
    if sd is None:
        return '<h3>Structured Data</h3><div class="not-checked">Not checked</div>'

    status = sd.get("status", "MISSING")
    blocks = sd.get("blocks") or []
    flags = sd.get("flags") or []

    _badge_map = {
        "PASS":    "badge-pass",
        "WARN":    "badge-warn",
        "FAIL":    "badge-fail",
        "MISSING": "badge-missing",
    }
    badge_cls = _badge_map.get(status, "badge-missing")
    block_count = len(blocks)
    flags_html = ""
    if flags:
        items = "".join(f"<li>{f}</li>" for f in flags)
        flags_html = f'<ul class="flags-list">{items}</ul>'

    return f"""<h3>Structured Data</h3>
<p><span class="badge {badge_cls}">{status}</span> &nbsp; {block_count} block(s) found</p>
{flags_html}"""


def _build_html(results: list, project_name: str) -> str:
    pass_count = sum(1 for r in results if _is_pass(r))
    fail_count = len(results) - pass_count
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Most common failing fields
    all_failing = [f for r in results for f in _failing_fields(r)]
    common = Counter(all_failing).most_common(5)

    common_html = ""
    if common:
        items = "".join(f"<li>{f} ({c} URL{'s' if c > 1 else ''})</li>" for f, c in common)
        common_html = f'<div class="common-fails"><strong>Most common issues:</strong><ul>{items}</ul></div>'

    dashboard = f"""
<div class="dashboard">
  <h2>Audit Summary — {project_name}</h2>
  <div class="stats">
    <div class="stat"><div class="num">{len(results)}</div><div class="lbl">URLs Audited</div></div>
    <div class="stat"><div class="num pass">{pass_count}</div><div class="lbl">Passed</div></div>
    <div class="stat"><div class="num fail">{fail_count}</div><div class="lbl">Failed</div></div>
  </div>
  {common_html}
  <p style="margin-top:12px;color:#718096;font-size:11px;">Generated: {now}</p>
</div>"""

    url_sections = []
    for result in results:
        url = result.get("url") or result.get("final_url", "unknown")
        overall = _is_pass(result)
        badge_cls = "badge-pass" if overall else "badge-fail"
        badge_txt = "PASS" if overall else "FAIL"
        method = _method_label(result.get("method", ""))

        # Screenshot or placeholder
        screenshot_path = result.get("screenshot_path")
        if screenshot_path and os.path.isfile(screenshot_path):
            screenshot_html = f'<img src="{screenshot_path}" style="max-width:100%;max-height:200px;margin:12px 0;border-radius:4px;" alt="screenshot">'
        else:
            screenshot_html = '<div class="screenshot-placeholder">[ No screenshot available ]</div>'

        # Issues table sorted high → medium → low
        failing = _failing_fields(result)
        if failing:
            rows = ""
            for field in failing:
                sev = assign_severity(field, result)
                fix = _fix_text(field, result)
                rows += (
                    f'<tr><td>{field}</td>'
                    f'<td class="sev-{sev}">{sev.upper()}</td>'
                    f'<td class="fail">FAIL</td>'
                    f'<td>{fix}</td></tr>'
                )
            issues_html = f"""
<table>
  <thead><tr><th>Field</th><th>Severity</th><th>Status</th><th>Suggested Fix</th></tr></thead>
  <tbody>{rows}</tbody>
</table>"""
        else:
            issues_html = '<p style="color:#276749;margin:8px 0;">All fields pass — no issues found.</p>'

        perf_html = _performance_html(result)
        sd_html = _structured_data_html(result)

        url_sections.append(f"""
<div class="url-section">
  <div class="url-header">
    <span class="url">{url}</span>
    <span class="badge {badge_cls}">{badge_txt}</span>
  </div>
  <div style="padding:12px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 4px 4px;">
    {screenshot_html}
    <h3>Issues</h3>
    {issues_html}
    {perf_html}
    {sd_html}
    <p class="method-tag">Analysis method: {method}</p>
  </div>
</div>""")

    body = dashboard + "\n".join(url_sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>SEO Audit — {project_name}</title>
<style>{_CSS}</style></head>
<body>
<h1>SEO Audit Report</h1>
<p style="color:#718096;margin-bottom:16px;">Project: <strong>{project_name}</strong></p>
{body}
</body></html>"""


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


def _generate_via_weasyprint(html: str, output_path: str) -> None:
    from weasyprint import HTML
    HTML(string=html).write_pdf(output_path)


def _generate_via_fpdf2(results: list, project_name: str, output_path: str) -> None:
    """Fallback PDF generator using fpdf2 when WeasyPrint native libs are absent."""
    from fpdf import FPDF

    pass_count = sum(1 for r in results if _is_pass(r))
    fail_count = len(results) - pass_count
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(20, 20, 20)

    # --- Summary page ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "SEO Audit Report", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Project: {project_name}", ln=True)
    pdf.cell(0, 7, f"Generated: {now}", ln=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Summary Dashboard", ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(60, 7, f"URLs Audited: {len(results)}")
    pdf.set_text_color(39, 103, 73)
    pdf.cell(60, 7, f"Passed: {pass_count}")
    pdf.set_text_color(197, 48, 48)
    pdf.cell(0, 7, f"Failed: {fail_count}", ln=True)
    pdf.set_text_color(0, 0, 0)

    # Common failing fields
    all_failing = [f for r in results for f in _failing_fields(r)]
    common = Counter(all_failing).most_common(5)
    if common:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "Most Common Issues:", ln=True)
        pdf.set_font("Helvetica", "", 10)
        for field, count in common:
            pdf.cell(0, 6, f"  - {field}: {count} URL{'s' if count > 1 else ''}", ln=True)

    # --- Per-URL pages ---
    _SEV_COLORS = {
        "high":   (197, 48, 48),
        "medium": (192, 86, 33),
        "low":    (43, 108, 176),
    }

    for result in results:
        pdf.add_page()
        url = result.get("url") or result.get("final_url", "unknown")
        overall = _is_pass(result)

        pdf.set_font("Helvetica", "B", 12)
        status_color = (39, 103, 73) if overall else (197, 48, 48)
        pdf.set_text_color(*status_color)
        pdf.cell(0, 8, f"{'PASS' if overall else 'FAIL'}", ln=False)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        pdf.ln(6)
        pdf.multi_cell(0, 5, url)
        pdf.ln(3)

        # Screenshot or placeholder
        screenshot_path = result.get("screenshot_path")
        if screenshot_path and os.path.isfile(screenshot_path):
            try:
                pdf.image(screenshot_path, w=min(170, pdf.epw))
                pdf.ln(3)
            except Exception:
                pass
        else:
            pdf.set_fill_color(237, 242, 247)
            pdf.set_draw_color(203, 213, 224)
            pdf.rect(pdf.get_x(), pdf.get_y(), 170, 20, style="FD")
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(160, 174, 192)
            pdf.cell(170, 20, "[ No screenshot available ]", align="C", ln=True)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

        # Issues table
        failing = _failing_fields(result)
        if failing:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 7, "Issues (sorted by severity):", ln=True)
            # Header row
            pdf.set_fill_color(237, 242, 247)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(35, 6, "Field", border=1, fill=True)
            pdf.cell(25, 6, "Severity", border=1, fill=True)
            pdf.cell(130, 6, "Suggested Fix", border=1, fill=True, ln=True)
            pdf.set_font("Helvetica", "", 9)
            for field in failing:
                sev = assign_severity(field, result)
                fix = _fix_text(field, result)
                r2, g2, b2 = _SEV_COLORS[sev]
                pdf.set_text_color(r2, g2, b2)
                pdf.cell(35, 6, field, border=1)
                pdf.cell(25, 6, sev.upper(), border=1)
                pdf.set_text_color(0, 0, 0)
                pdf.cell(130, 6, fix[:75], border=1, ln=True)
        else:
            pdf.set_text_color(39, 103, 73)
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 7, "All fields pass -- no issues found.", ln=True)
            pdf.set_text_color(0, 0, 0)

        # Performance section
        perf = result.get("performance")
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, "Performance:", ln=True)
        pdf.set_font("Helvetica", "", 9)
        if perf is None:
            pdf.set_text_color(160, 174, 192)
            pdf.cell(0, 6, "  Not checked", ln=True)
            pdf.set_text_color(0, 0, 0)
        else:
            status_color = (39, 103, 73) if perf.get("status") == "PASS" else (197, 48, 48)
            pdf.set_text_color(*status_color)
            pdf.cell(0, 6, f"  Status: {perf.get('status','?')}  "
                           f"Mobile: {perf.get('mobile_score','?')}  "
                           f"Desktop: {perf.get('desktop_score','?')}", ln=True)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(0, 6, f"  LCP: {perf.get('lcp','?')}s  "
                           f"CLS: {perf.get('cls','?')}  "
                           f"INP: {perf.get('inp','?')}ms", ln=True)

        # Structured data section
        sd = result.get("structured_data")
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, "Structured Data:", ln=True)
        pdf.set_font("Helvetica", "", 9)
        if sd is None:
            pdf.set_text_color(160, 174, 192)
            pdf.cell(0, 6, "  Not checked", ln=True)
            pdf.set_text_color(0, 0, 0)
        else:
            sd_status = sd.get("status", "MISSING")
            block_count = len(sd.get("blocks") or [])
            _sd_colors = {
                "PASS": (39, 103, 73), "WARN": (116, 66, 16),
                "FAIL": (197, 48, 48), "MISSING": (113, 128, 150),
            }
            pdf.set_text_color(*_sd_colors.get(sd_status, (0, 0, 0)))
            pdf.cell(0, 6, f"  Status: {sd_status}  Blocks: {block_count}", ln=True)
            pdf.set_text_color(0, 0, 0)
            for flag in (sd.get("flags") or []):
                pdf.set_text_color(197, 48, 48)
                pdf.multi_cell(0, 5, f"    - {flag}")
                pdf.set_text_color(0, 0, 0)

        method = _method_label(result.get("method", ""))
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(113, 128, 150)
        pdf.cell(0, 6, f"Analysis method: {method}", ln=True)
        pdf.set_text_color(0, 0, 0)

    pdf.output(output_path)


def generate_pdf(results: list, project_name: str, output_path: str) -> str:
    """
    Generate a PDF audit report.

    Tries WeasyPrint first; falls back to fpdf2 if native libs are absent.
    Always builds the HTML string as an intermediate representation.
    Returns output_path.
    """
    html = _build_html(results, project_name)

    if _weasyprint_available():
        try:
            _generate_via_weasyprint(html, output_path)
            logger.info("[enhanced_reporter] PDF written via WeasyPrint: %s", output_path)
            return output_path
        except Exception as exc:
            logger.warning("[enhanced_reporter] WeasyPrint failed (%s) — using fpdf2 fallback", exc)

    _generate_via_fpdf2(results, project_name, output_path)
    logger.info("[enhanced_reporter] PDF written via fpdf2: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import tempfile

    failures = []

    def run(name, fn):
        try:
            fn()
            print(f"Test {name} PASS")
        except Exception as exc:
            print(f"Test {name} FAIL: {exc}")
            failures.append(name)

    _FIXTURES = [
        {
            "url": "https://example.com/",
            "final_url": "https://example.com/",
            "status_code": 200,
            "title": {"value": "Example", "length": 7, "status": "PASS"},
            "description": {"value": None, "length": 0, "status": "FAIL"},
            "h1": {"count": 1, "value": "Welcome", "status": "PASS"},
            "canonical": {"value": None, "status": "FAIL"},
            "broken_links": {"broken": [], "count": 0, "status": "PASS", "capped": False},
            "flags": ["Meta description missing", "Canonical missing"],
            "method": "deterministic",
        },
        {
            "url": "https://example.com/about",
            "final_url": "https://example.com/about",
            "status_code": 200,
            "title": {"value": "About Us — Example Company", "length": 26, "status": "PASS"},
            "description": {"value": "We are a great company.", "length": 23, "status": "PASS"},
            "h1": {"count": 0, "value": None, "status": "FAIL"},
            "canonical": {"value": "https://example.com/about", "status": "PASS"},
            "broken_links": {"broken": ["https://example.com/dead"], "count": 1, "status": "FAIL", "capped": False},
            "flags": ["H1 missing", "Broken links"],
            "method": "haiku",
            # no screenshot_path key — should trigger placeholder
        },
        {
            "url": "https://example.com/blog",
            "final_url": "https://example.com/blog",
            "status_code": 200,
            "title": {"value": "Blog", "length": 4, "status": "PASS"},
            "description": {"value": "Our latest posts.", "length": 17, "status": "PASS"},
            "h1": {"count": 1, "value": "Blog", "status": "PASS"},
            "canonical": {"value": "https://example.com/blog", "status": "PASS"},
            "broken_links": {"broken": [], "count": 0, "status": "PASS", "capped": False},
            "flags": [],
            "method": "sonnet",
            "screenshot_path": "/nonexistent/screenshot.png",  # missing file → placeholder
        },
    ]

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, "test_report.pdf")

        # Test 1 & 2: generate_pdf produces a non-empty file > 1KB
        def test_pdf_created():
            generate_pdf(_FIXTURES, "TestProject", pdf_path)
            assert os.path.exists(pdf_path), "PDF not created"
            size = os.path.getsize(pdf_path)
            assert size > 1024, f"PDF too small: {size} bytes"
        run("1+2: PDF created and > 1KB", test_pdf_created)

        # Test 3: issues sorted high -> medium -> low in HTML
        def test_issue_sort_order():
            html = _build_html(_FIXTURES, "TestProject")
            # For fixture[0]: failing fields are description(medium) and canonical(high)
            # canonical should appear before description in the HTML
            idx_canonical = html.find(">canonical<")
            idx_description = html.find(">description<")
            assert idx_canonical != -1, "canonical not in HTML"
            assert idx_description != -1, "description not in HTML"
            assert idx_canonical < idx_description, (
                f"canonical (high) should appear before description (medium) "
                f"but got indices canonical={idx_canonical}, description={idx_description}"
            )
        run("3: issues sorted high -> medium -> low in HTML", test_issue_sort_order)

        # Test 4: summary dashboard with correct pass/fail counts
        def test_dashboard_counts():
            html = _build_html(_FIXTURES, "TestProject")
            # 1 full pass (blog), 2 fails
            assert "Passed" in html, "Passed label missing"
            assert "Failed" in html, "Failed label missing"
            assert ">1<" in html or ">1 <" in html or "num pass" in html, "pass count not found"
            # Check pass=1, fail=2 appear somewhere
            assert html.count(">1<") >= 1 or "pass\">1" in html or ">1</div>" in html
        run("4: summary dashboard contains pass/fail counts", test_dashboard_counts)

        # Test 4 (stronger): check actual numbers by parsing the dashboard section
        def test_dashboard_numbers():
            html = _build_html(_FIXTURES, "TestProject")
            import re
            # Extract the stats section numbers
            nums = re.findall(r'class="num[^"]*">(\d+)<', html)
            assert len(nums) >= 3, f"Expected at least 3 stat numbers, got {nums}"
            total, passed, failed = int(nums[0]), int(nums[1]), int(nums[2])
            assert total == 3, f"Expected total=3, got {total}"
            assert passed == 1, f"Expected passed=1, got {passed}"
            assert failed == 2, f"Expected failed=2, got {failed}"
        run("4b: dashboard shows correct totals (3 total, 1 pass, 2 fail)", test_dashboard_numbers)

        # Test 5: missing screenshot renders placeholder div, not error
        def test_screenshot_placeholder():
            html = _build_html(_FIXTURES, "TestProject")
            assert "screenshot-placeholder" in html, "Placeholder class not found"
            assert "No screenshot available" in html, "Placeholder text not found"
            # Should appear at least twice (fixtures 1 and 2 have no valid screenshot)
            count = html.count("screenshot-placeholder")
            assert count >= 2, f"Expected >= 2 placeholders, got {count}"
        run("5: missing screenshot renders placeholder div", test_screenshot_placeholder)

    print()
    if failures:
        print(f"{len(failures)} test(s) failed: {failures}")
        sys.exit(1)
    else:
        print("All 5 acceptance tests passed.")
