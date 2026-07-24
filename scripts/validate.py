#!/usr/bin/env python3
"""
Resume Validator Script
Validates generated resume HTML for:
1. Locked companies from work-history.md are all present
2. Valid title_lines (real titles from work-history.md)
3. About text contains 2+ quantified metrics
4. Orphan lines (single word on last line via headless render)
5. Page overflow (content exceeds A4 height)
6. Sparse lines (low pixel fill rate)

Usage:
  python3 validate.py <output.html> [--config <config.json>]

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
"""

import sys
import json
import re
import argparse
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
WORK_HISTORY_PATH = SKILL_DIR / "references" / "work-history.md"

# A4 dimensions
A4_HEIGHT_PX = 1122   # 297mm @ 96dpi
A4_WIDTH_PX  = 794    # 210mm @ 96dpi
OVERFLOW_TOLERANCE_PX = 10


def _parse_yaml_frontmatter(text):
    """Parse YAML frontmatter from markdown."""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    yaml_text = parts[1].strip()
    try:
        import yaml
        return yaml.safe_load(yaml_text) or {}
    except ImportError:
        pass
    # Basic fallback
    result = {}
    current_key = None
    current_list = None
    current_dict_list = None
    for line in yaml_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        m = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', stripped)
        if m and indent == 0:
            key, value = m.group(1), m.group(2).strip()
            current_key = key
            if value:
                if value.startswith("[") and value.endswith("]"):
                    items = value[1:-1]
                    result[key] = [i.strip().strip('"').strip("'") for i in items.split(",") if i.strip()]
                elif value.startswith('"') and value.endswith('"'):
                    result[key] = value[1:-1]
                else:
                    result[key] = value
                current_list = None
                current_dict_list = None
            else:
                result[key] = []
                current_list = result[key]
                current_dict_list = None
            continue
        if stripped.startswith("- ") and current_list is not None:
            item_content = stripped[2:].strip()
            km = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', item_content)
            if km:
                new_dict = {km.group(1): km.group(2).strip().strip('"').strip("'")}
                current_list.append(new_dict)
                current_dict_list = new_dict
            else:
                current_list.append(item_content.strip('"').strip("'"))
                current_dict_list = None
            continue
        if current_dict_list is not None and indent >= 4:
            km = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', stripped)
            if km:
                current_dict_list[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    return result


def load_work_history():
    """Load locked_companies and allowed titles from work-history.md."""
    result = {"locked_companies": [], "allowed_titles": []}
    if not WORK_HISTORY_PATH.exists():
        return result

    with open(WORK_HISTORY_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    fm = _parse_yaml_frontmatter(content)
    result["locked_companies"] = fm.get("locked_companies", [])

    # Extract all Role and Role_EN values from the markdown body as allowed titles
    titles = set()
    for m in re.finditer(r'^- Role(?:_EN)?:\s*(.+)$', content, re.MULTILINE):
        val = m.group(1).strip()
        if val:
            # Handle "Role1 / Role2" compound titles
            for part in val.split(" / "):
                titles.add(part.strip())
    result["allowed_titles"] = list(titles)

    return result


def check_about_text(html: str) -> list:
    """Check that about_text contains at least 2 quantified achievements."""
    errors = []
    about_match = re.search(r'class="about-text"[^>]*>(.*?)</(?:div|p)>', html, re.DOTALL)
    if not about_match:
        return errors
    about = about_match.group(1)
    about_plain = re.sub(r'<[^>]+>', '', about).strip()
    numbers = re.findall(r'\d[\d,]*(?:K\+?|%|\+)?', about_plain)
    if len(numbers) < 2:
        errors.append(
            f"about_text contains fewer than 2 quantified achievements ({len(numbers)} found: {numbers}). "
            "Include at least 2 specific numbers/metrics from experience."
        )
    return errors


def check_companies(html: str) -> list:
    """Check all locked_companies from work-history.md appear in HTML."""
    errors = []
    wh = load_work_history()
    locked = wh.get("locked_companies", [])
    if not locked:
        return errors

    companies_in_html = [c.strip() for c in re.findall(r'class="job-company"[^>]*>([^<]+)<', html)]

    for req in locked:
        if not any(req.lower() in c.lower() for c in companies_in_html):
            errors.append(f"Missing locked company: '{req}' -- must be included in experience")

    return errors


def check_title_lines(config_path: str) -> list:
    """Check title_lines in config against allowed titles from work-history.md."""
    errors = []
    if not config_path:
        return ["No config provided, skipping title_lines check"]

    wh = load_work_history()
    allowed = wh.get("allowed_titles", [])
    if not allowed:
        return []  # No whitelist available, skip check

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        for line in config.get("title_lines", []):
            if not any(allowed_t.lower() in line.lower() or line.lower() in allowed_t.lower()
                       for allowed_t in allowed):
                errors.append(f"Unrecognized title_line: '{line}' (not found in work-history.md roles)")
    except Exception as e:
        errors.append(f"Could not read config: {e}")
    return errors


def check_render(html_path: str) -> dict:
    """Use Playwright headless browser to check overflow and orphan lines."""
    result = {
        "overflow": False,
        "overflow_by_px": 0,
        "content_height": 0,
        "a4_height": A4_HEIGHT_PX,
        "orphan_lines": [],
        "sparse_lines": [],
        "screenshot_path": None,
        "render_error": None,
    }
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["render_error"] = "playwright not installed"
        return result

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": A4_WIDTH_PX, "height": A4_HEIGHT_PX})
            page.goto(f"file://{html_path}", wait_until="networkidle", timeout=15000)

            # Overflow check
            content_height = page.evaluate("""
                () => document.querySelector('.resume-container').scrollHeight
            """)
            result["content_height"] = content_height
            overflow_by = content_height - A4_HEIGHT_PX
            if overflow_by > OVERFLOW_TOLERANCE_PX:
                result["overflow"] = True
                result["overflow_by_px"] = overflow_by

            # Orphan line check (word-level, preserves bold/inline formatting)
            orphans = page.evaluate("""
                () => {
                    const Y_TOLERANCE = 3;
                    const results = [];

                    const selectors = [
                        '.skill-item', '.about-text', '.job-list li',
                        '.core-item', '.contact-text', '.edu-detail',
                        '.edu-school', '.job-role', '.title-line'
                    ];

                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const originalHTML = el.innerHTML;

                            const walker = document.createTreeWalker(
                                el, NodeFilter.SHOW_TEXT, null
                            );
                            const textNodes = [];
                            let node;
                            while ((node = walker.nextNode())) {
                                if (node.textContent.trim()) textNodes.push(node);
                            }

                            const injectedSpans = [];
                            for (const tn of textNodes) {
                                const words = tn.textContent.split(/[ \\t]+/);
                                const frag = document.createDocumentFragment();
                                words.forEach((word, i) => {
                                    if (word.trim()) {
                                        const sp = document.createElement('span');
                                        sp.className = '_w';
                                        sp.textContent = word;
                                        frag.appendChild(sp);
                                        injectedSpans.push(sp);
                                    }
                                    if (i < words.length - 1) {
                                        frag.appendChild(document.createTextNode(' '));
                                    }
                                });
                                tn.parentNode.replaceChild(frag, tn);
                            }

                            const lines = [];
                            for (const sp of injectedSpans) {
                                const rect = sp.getBoundingClientRect();
                                if (rect.width === 0) continue;
                                const y = rect.top;
                                const existing = lines.find(l => Math.abs(l.y - y) < Y_TOLERANCE);
                                if (existing) existing.words.push(sp.textContent);
                                else lines.push({ y, words: [sp.textContent] });
                            }

                            el.innerHTML = originalHTML;

                            if (lines.length >= 2) {
                                const lastLine = lines[lines.length - 1];
                                if (lastLine.words.length === 1) {
                                    const fullText = lines.map(l => l.words.join(' ')).join(' ');
                                    results.push({
                                        orphan_word: lastLine.words[0],
                                        context: fullText.slice(0, 60),
                                        selector: sel
                                    });
                                }
                            }
                        }
                    }
                    return results;
                }
            """)
            result["orphan_lines"] = orphans

            # Sparse line check (pixel fill rate)
            sparse = page.evaluate("""
                () => {
                    const Y_TOLERANCE = 3;
                    const FILL_THRESHOLD_NARROW = 0.65;
                    const FILL_THRESHOLD_WIDE   = 0.82;
                    const results = [];
                    const els = document.querySelectorAll('.core-item, .job-list li, .about-text');
                    for (const el of els) {
                        const originalHTML = el.innerHTML;
                        const containerWidth = el.getBoundingClientRect().width;
                        if (!containerWidth) { continue; }

                        const walker = document.createTreeWalker(
                            el, NodeFilter.SHOW_TEXT, null
                        );
                        const textNodes = [];
                        let node;
                        while ((node = walker.nextNode())) {
                            if (node.textContent.trim()) textNodes.push(node);
                        }

                        const injectedSpans = [];
                        for (const tn of textNodes) {
                            const words = tn.textContent.split(/[ \\t]+/);
                            const frag = document.createDocumentFragment();
                            words.forEach((word, i) => {
                                if (word.trim()) {
                                    const sp = document.createElement('span');
                                    sp.className = '_w';
                                    sp.textContent = word;
                                    frag.appendChild(sp);
                                    injectedSpans.push(sp);
                                }
                                if (i < words.length - 1) {
                                    frag.appendChild(document.createTextNode(' '));
                                }
                            });
                            tn.parentNode.replaceChild(frag, tn);
                        }

                        const lines = [];
                        for (const sp of injectedSpans) {
                            const rect = sp.getBoundingClientRect();
                            if (rect.width === 0) continue;
                            const y = rect.top;
                            const existing = lines.find(l => Math.abs(l.y - y) < Y_TOLERANCE);
                            if (existing) {
                                existing.words.push(sp.textContent);
                                existing.totalWidth += rect.width;
                            } else {
                                lines.push({ y, words: [sp.textContent], totalWidth: rect.width });
                            }
                        }

                        el.innerHTML = originalHTML;

                        if (lines.length < 2) continue;

                        let startLine = 0;
                        const isCoreItem = el.classList.contains('core-item');
                        if (isCoreItem && lines[0].words.join(' ').trim().endsWith(':')) startLine = 1;

                        const threshold = isCoreItem ? FILL_THRESHOLD_NARROW : FILL_THRESHOLD_WIDE;

                        for (let i = startLine; i < lines.length - 1; i++) {
                            const fillRate = lines[i].totalWidth / containerWidth;
                            if (fillRate < threshold) {
                                const fullText = lines.map(l => l.words.join(' ')).join(' ');
                                results.push({
                                    line_index: i,
                                    fill_rate: Math.round(fillRate * 100),
                                    word_count: lines[i].words.length,
                                    line_text: lines[i].words.join(' '),
                                    context: fullText.slice(0, 60)
                                });
                                break;
                            }
                        }
                    }
                    return results;
                }
            """)
            result["sparse_lines"] = sparse

            # Screenshot
            html_stem = Path(html_path).stem
            screenshot_path = f"/tmp/{html_stem}-preview.png"
            page.screenshot(path=screenshot_path, full_page=True)
            result["screenshot_path"] = screenshot_path

            browser.close()
    except Exception as e:
        result["render_error"] = str(e)

    return result


def main():
    parser = argparse.ArgumentParser(description="Validate resume HTML")
    parser.add_argument("html", help="Path to generated HTML file")
    parser.add_argument("--config", default=None, help="Path to config JSON")
    args = parser.parse_args()

    html_path = Path(args.html).resolve()
    if not html_path.exists():
        print(json.dumps({"pass": False, "error": f"File not found: {html_path}"}))
        sys.exit(1)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    errors = []
    warnings = []
    report = {}

    # Check 1: about_text quantified achievements
    about_errors = check_about_text(html)
    report["about_text"] = {"pass": len(about_errors) == 0, "errors": about_errors}
    errors.extend(about_errors)

    # Check 2: locked companies
    company_errors = check_companies(html)
    report["companies"] = {"pass": len(company_errors) == 0, "errors": company_errors}
    errors.extend(company_errors)

    # Check 3: title_lines whitelist
    title_errors = check_title_lines(args.config)
    report["title_lines"] = {"pass": len(title_errors) == 0, "errors": title_errors}
    errors.extend(title_errors)

    # Check 4: template placeholder residue detection
    placeholder_patterns = [
        (r'职位[123]', "title_lines"),
        (r'技能[1-7]', "skills"),
        (r'核心能力[1-6]', "core_competencies"),
    ]
    placeholder_errors = []
    for pattern, field_name in placeholder_patterns:
        if re.search(pattern, html):
            placeholder_errors.append(
                f"Template placeholder still present for '{field_name}' "
                f"(matched /{pattern}/) — field may be missing from config"
            )
    report["placeholders"] = {"pass": len(placeholder_errors) == 0, "errors": placeholder_errors}
    errors.extend(placeholder_errors)

    # Check 5: render checks (overflow + orphans + sparse)
    render = check_render(str(html_path))
    report["render"] = render

    if render.get("render_error"):
        warnings.append(f"Render check skipped: {render['render_error']}")
    else:
        if render["overflow"]:
            errors.append(
                f"Page overflow: content is {render['overflow_by_px']}px taller than A4 "
                f"({render['content_height']}px vs {A4_HEIGHT_PX}px)"
            )
        if render["orphan_lines"]:
            for o in render["orphan_lines"]:
                errors.append(f"Orphan word '{o['orphan_word']}' at end of line in [{o['selector']}]: \"{o['context']}\"")
        if render.get("sparse_lines"):
            for s in render["sparse_lines"]:
                warnings.append(f"Sparse line ({s['word_count']} words) in core-item -- large word-spacing will appear: \"{s['context']}\"")

    # Summary
    passed = len(errors) == 0
    report["pass"] = passed
    report["errors"] = errors
    report["warnings"] = warnings

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if render.get("screenshot_path"):
        print(f"\nScreenshot: {render['screenshot_path']}", file=sys.stderr)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
