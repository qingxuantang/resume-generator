#!/usr/bin/env python3
"""
Resume Generator Script
Generates customized resumes from JSON config and HTML template.

Personal info (name, email, phone, links, education) is extracted from
references/work-history.md — NOT from the JSON config.

Usage:
    python3 generate.py <config.json> <template.html> <output.html>
"""

import json
import sys
import re
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
PHOTO_BASE64_PATH = SKILL_DIR / "assets" / "photo-base64.txt"
WORK_HISTORY_PATH = SKILL_DIR / "references" / "work-history.md"


# ── Abbreviation expansion map ────────────────────────────────────────────────
# Applied to ALL string fields in config before rendering.
# Keys are matched as whole words (case-sensitive).
# Safety net only — the LLM should still write full forms in the config.
ABBR_MAP = {
    "comms": "communications",
    "Comms": "Communications",
    "XHS": "Xiaohongshu",
    "xhs": "Xiaohongshu",
    "dept.": "department",
    "Dept.": "Department",
    "mgmt": "management",
    "Mgmt": "Management",
    "coord.": "coordination",
    "Coord.": "Coordination",
    "collab.": "collaboration",
    "Collab.": "Collaboration",
    "comms.": "communications",
    "PR Comms": "PR Communications",
}


def expand_abbreviations(obj):
    """Recursively expand abbreviations in all string fields of a config object."""
    if isinstance(obj, str):
        result = obj
        for abbr, full in ABBR_MAP.items():
            # Match whole word only (handles punctuation boundaries too)
            result = re.sub(r'(?<![A-Za-z])' + re.escape(abbr) + r'(?![A-Za-z])', full, result)
        return result
    elif isinstance(obj, list):
        return [expand_abbreviations(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: expand_abbreviations(v) for k, v in obj.items()}
    return obj


# ── Work history loader ─────────────────────────────────────────────────────

def _parse_yaml_frontmatter(text):
    """Parse YAML frontmatter from markdown. Uses pyyaml if available,
    otherwise falls back to basic key-value parsing."""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    yaml_text = parts[1].strip()

    # Try pyyaml first
    try:
        import yaml
        return yaml.safe_load(yaml_text) or {}
    except ImportError:
        pass

    # Basic fallback parser
    result = {}
    current_key = None
    current_list = None
    current_dict_list = None

    for line in yaml_text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # Top-level key: value
        m = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', stripped)
        if m and indent == 0:
            key, value = m.group(1), m.group(2).strip()
            current_key = key
            if value:
                # Handle inline lists like ["a", "b"]
                if value.startswith("[") and value.endswith("]"):
                    items = value[1:-1]
                    result[key] = [i.strip().strip('"').strip("'") for i in items.split(",") if i.strip()]
                elif value.startswith('"') and value.endswith('"'):
                    result[key] = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    result[key] = value[1:-1]
                else:
                    result[key] = value
                current_list = None
                current_dict_list = None
            else:
                # Could be a list or nested structure
                result[key] = []
                current_list = result[key]
                current_dict_list = None
            continue

        # List item with nested dict: "  - key: value"
        if stripped.startswith("- ") and current_list is not None:
            item_content = stripped[2:].strip()
            km = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', item_content)
            if km:
                # Start of a dict item in a list
                new_dict = {km.group(1): km.group(2).strip().strip('"').strip("'")}
                current_list.append(new_dict)
                current_dict_list = new_dict
            else:
                current_list.append(item_content.strip('"').strip("'"))
                current_dict_list = None
            continue

        # Continuation of dict in list: "    key: value"
        if current_dict_list is not None and indent >= 4:
            km = re.match(r'^(\w[\w_]*)\s*:\s*(.*)', stripped)
            if km:
                val = km.group(2).strip().strip('"').strip("'")
                current_dict_list[km.group(1)] = val
                continue

    return result


def load_work_history():
    """Load and parse references/work-history.md, returning a dict with:
    name, name_en, email, phone, links, locked_companies, education"""
    result = {
        "name": "",
        "name_en": "",
        "email": "",
        "phone": "",
        "links": [],
        "locked_companies": [],
        "education": [],
    }

    if not WORK_HISTORY_PATH.exists():
        print(f"Warning: {WORK_HISTORY_PATH} not found, using empty defaults")
        return result

    with open(WORK_HISTORY_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    fm = _parse_yaml_frontmatter(content)

    result["name"] = fm.get("name", "")
    result["name_en"] = fm.get("name_en", "")
    result["email"] = fm.get("email", "")
    result["phone"] = fm.get("phone", "")
    result["links"] = fm.get("links", [])
    result["locked_companies"] = fm.get("locked_companies", [])
    result["education"] = fm.get("education", [])

    return result


# ── Personal info injection ──────────────────────────────────────────────────

def inject_personal_info(html, wh):
    """Replace personal info placeholders with data from work-history.md."""
    name = wh.get("name", "")
    name_en = wh.get("name_en", "")
    email = wh.get("email", "")
    phone = wh.get("phone", "")
    links = wh.get("links", [])
    education = wh.get("education", [])

    html = html.replace("{{NAME}}", name)
    html = html.replace("{{NAME_EN}}", name_en)
    html = html.replace("{{EMAIL}}", email)

    # Phone — inject a contact-item if phone exists and placeholder present
    if "{{PHONE}}" in html:
        if phone:
            phone_html = f'''<div class="contact-item">
                        <svg class="contact-icon" viewBox="0 0 24 24" fill="#5b7a9d">
                            <path d="M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z" />
                        </svg>
                        <span class="contact-text">{phone}</span>
                    </div>'''
            html = html.replace("{{PHONE}}", phone_html)
        else:
            html = html.replace("{{PHONE}}", "")

    # Links — generate contact-item HTML for each link
    if "{{LINKS}}" in html:
        links_html = ""
        # SVG icons for common link types
        link_icons = {
            "linkedin": '<path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z" />',
            "github": '<path d="M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0l4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z" />',
            "default": '<path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zm6.93 6h-2.95c-.32-1.25-.78-2.45-1.38-3.56 1.84.63 3.37 1.91 4.33 3.56zM12 4.04c.83 1.2 1.48 2.53 1.91 3.96h-3.82c.43-1.43 1.08-2.76 1.91-3.96zM4.26 14C4.1 13.36 4 12.69 4 12s.1-1.36.26-2h3.38c-.08.66-.14 1.32-.14 2 0 .68.06 1.34.14 2H4.26zm.82 2h2.95c.32 1.25.78 2.45 1.38 3.56-1.84-.63-3.37-1.9-4.33-3.56zm2.95-8H5.08c.96-1.66 2.49-2.93 4.33-3.56C8.81 5.55 8.35 6.75 8.03 8zM12 19.96c-.83-1.2-1.48-2.53-1.91-3.96h3.82c-.43 1.43-1.08 2.76-1.91 3.96zM14.34 14H9.66c-.09-.66-.16-1.32-.16-2 0-.68.07-1.35.16-2h4.68c.09.65.16 1.32.16 2 0 .68-.07 1.34-.16 2zm.25 5.56c.6-1.11 1.06-2.31 1.38-3.56h2.95c-.96 1.65-2.49 2.93-4.33 3.56zM16.36 14c.08-.66.14-1.32.14-2 0-.68-.06-1.34-.14-2h3.38c.16.64.26 1.31.26 2s-.1 1.36-.26 2h-3.38z" />',
        }
        for link in links:
            if isinstance(link, dict):
                label = link.get("label", "").lower()
                display = link.get("display", link.get("url", ""))
                icon_path = link_icons.get(label, link_icons["default"])
                links_html += f'''                    <div class="contact-item">
                        <svg class="contact-icon" viewBox="0 0 24 24" fill="#5b7a9d">
                            {icon_path}
                        </svg>
                        <span class="contact-text">{display}</span>
                    </div>\n'''
        html = html.replace("{{LINKS}}", links_html)

    # Education — generate edu-item HTML
    if "{{EDUCATION}}" in html:
        edu_html = ""
        for edu in education:
            if isinstance(edu, dict):
                school = edu.get("school", "")
                degree = edu.get("degree", "")
                detail = edu.get("detail", "")
                period = edu.get("period", "")
                edu_html += f'                <div class="edu-item">\n'
                edu_html += f'                    <div class="edu-school">{school}</div>\n'
                if degree:
                    edu_html += f'                    <div class="edu-detail">{degree}</div>\n'
                if detail:
                    edu_html += f'                    <div class="edu-detail">{detail}</div>\n'
                if period:
                    edu_html += f'                    <div class="edu-time">{period}</div>\n'
                edu_html += f'                </div>\n'
        html = html.replace("{{EDUCATION}}", edu_html)

    return html


# ── Template and file I/O ────────────────────────────────────────────────────

def load_template(template_path):
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()

def load_photo_base64():
    """Load photo base64 data from separate file"""
    if PHOTO_BASE64_PATH.exists():
        with open(PHOTO_BASE64_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def save_output(content, output_path):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated: {output_path}")


# ── HTML replacement functions ───────────────────────────────────────────────

def replace_title(html, title_lines):
    """Replace the 3 title lines in sidebar"""
    pattern = r'<div class="title">.*?</div>'
    new_title = '<div class="title">\n'
    for line in title_lines[:3]:
        new_title += f'                    <span class="title-line">{line}</span>\n'
    new_title += '                </div>'
    return re.sub(pattern, new_title, html, flags=re.DOTALL)

def replace_about(html, about_text):
    """Replace about me section (handles both <p> and <div> containers)"""
    pattern = r'<(p|div) class="about-text">.*?</\1>'
    def _repl(m):
        tag = m.group(1)
        return f'<{tag} class="about-text">\n                    {about_text}\n                </{tag}>'
    return re.sub(pattern, _repl, html, flags=re.DOTALL)

def replace_skills(html, skills):
    """Replace 7 skill items in sidebar"""
    pattern = r'<ul class="skill-list">.*?</ul>'
    new_skills = '<ul class="skill-list">\n'
    for skill in skills[:7]:
        new_skills += f'                    <li class="skill-item">{skill}</li>\n'
    new_skills += '                </ul>'
    return re.sub(pattern, new_skills, html, flags=re.DOTALL)

def replace_core_competencies(html, competencies):
    """Replace 6 core competency items"""
    pattern = r'<div class="core-grid">.*</div>\s*</section>'
    new_core = '<div class="core-grid">\n'
    for comp in competencies[:6]:
        title = comp.get("title", "")
        desc = comp.get("desc", "")
        new_core += f'''                    <div class="core-item">
                        <span class="core-title">{title}:</span> {desc}
                    </div>\n'''
    new_core += '                </div>\n            </section>'
    return re.sub(pattern, new_core, html, flags=re.DOTALL)

def replace_page_title(html, company, wh):
    """Replace HTML title, using name from work-history"""
    pattern = r'<title>.*?</title>'
    name = wh.get("name", "") or wh.get("name_en", "")
    return re.sub(pattern, f'<title>{name} - {company} Resume</title>', html)

def replace_highlight_keywords(html, highlights):
    """Replace highlight keywords in job entries"""
    for old, new in highlights.items():
        pattern = r'<span class="highlight">' + re.escape(old) + r'</span>'
        html = re.sub(pattern, f'<span class="highlight">{new}</span>', html)
    return html

def replace_experience(html, experience):
    """Replace the entire timeline block with experience entries from config."""
    timeline_start = html.find("<!-- TIMELINE_START -->")
    timeline_end = html.find("<!-- TIMELINE_END -->")
    if timeline_start == -1 or timeline_end == -1:
        print("Warning: TIMELINE_START/END markers not found, skipping experience replacement")
        return html

    entries_html = ""
    for job in experience:
        company = job.get("company", "")
        period = job.get("period", "")
        role = job.get("role", "")
        bullets = job.get("bullets", [])

        bullet_items = ""
        for b in bullets:
            b = b.strip()
            if b.startswith("**") and ("**:" in b or "**：" in b):
                if "**:" in b:
                    label, _, rest = b[2:].partition("**:")
                else:
                    label, _, rest = b[2:].partition("**：")
                bullet_items += f'                            <li><span class="highlight">{label}</span>: {rest.strip()}</li>\n'
            else:
                bullet_items += f'                            <li>{b}</li>\n'

        entries_html += f'''                    <div class="job-entry">
                        <div class="job-header">
                            <span class="job-company">{company}</span>
                            <span class="job-period">{period}</span>
                        </div>
                        <div class="job-role">{role}</div>
                        <ul class="job-list">
{bullet_items}                        </ul>
                    </div>\n'''

    before = html[:timeline_start]
    after = html[timeline_end + len("<!-- TIMELINE_END -->"):]
    new_block = "<!-- TIMELINE_START -->\n                <div class=\"timeline\">\n" + entries_html + "                </div>\n                <!-- TIMELINE_END -->"
    return before + new_block + after


def replace_sidebar_section_title(html, new_title):
    """Replace sidebar section title"""
    pattern = r'<div class="section-title-sidebar">Contact</div>'
    return re.sub(pattern, f'<div class="section-title-sidebar">{new_title}</div>', html)

def inject_photo(html):
    """Inject photo base64 data into placeholder. Photo is required."""
    photo_base64 = load_photo_base64()
    if "{{PHOTO_BASE64}}" in html:
        if not photo_base64:
            print("ERROR: Photo is required but assets/photo-base64.txt is missing or empty.")
            print("Please provide a photo during onboarding before generating a resume.")
            sys.exit(1)
        # Detect image format and add data URI prefix
        if photo_base64.startswith("iVBOR"):
            mime = "image/png"
        elif photo_base64.startswith("/9j/"):
            mime = "image/jpeg"
        else:
            mime = "image/jpeg"
        data_uri = f"data:{mime};base64,{photo_base64}"
        html = html.replace("{{PHOTO_BASE64}}", data_uri)
        print(f"Photo injected successfully ({mime})")
    return html


# ── Main generation ──────────────────────────────────────────────────────────

def generate_resume(config_path, template_path, output_path):
    # Load config
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Expand abbreviations across all string fields (safety net; LLM should
    # still write full forms — see SKILL.md Anti-Patterns)
    config = expand_abbreviations(config)

    # Load work history for personal info
    wh = load_work_history()

    # Load template
    html = load_template(template_path)

    # Inject personal info from work-history.md (name, email, phone, links, education)
    html = inject_personal_info(html, wh)

    # Apply config replacements
    if "company" in config:
        html = replace_page_title(html, config["company"], wh)

    if "title_lines" in config:
        html = replace_title(html, config["title_lines"])

    if "about_text" in config:
        html = replace_about(html, config["about_text"])

    if "skills" in config:
        html = replace_skills(html, config["skills"])

    if "core_competencies" in config:
        html = replace_core_competencies(html, config["core_competencies"])

    if "experience" in config:
        html = replace_experience(html, config["experience"])

    if "highlight_keywords" in config:
        html = replace_highlight_keywords(html, config["highlight_keywords"])

    # sidebar_section_title only applies to CN template
    if "sidebar_section_title" in config and "BASIC INFO" not in html:
        html = replace_sidebar_section_title(html, config["sidebar_section_title"])

    # Check for unrecognized config keys
    KNOWN_KEYS = {"company", "title_lines", "about_text", "skills",
                  "core_competencies", "experience", "highlight_keywords",
                  "sidebar_section_title"}
    unknown = set(config.keys()) - KNOWN_KEYS
    if unknown:
        print(f"\n⚠ WARNING: Unrecognized config keys (will be ignored): {unknown}")
        print(f"  Valid keys: {sorted(KNOWN_KEYS)}")

    # Inject photo base64 at the end
    html = inject_photo(html)

    # Save output
    save_output(html, output_path)
    return output_path


def inject_hyphens(output_path):
    """Inject hyphens:auto into .core-item CSS as sparse-line fallback."""
    with open(output_path, "r", encoding="utf-8") as f:
        html = f.read()
    if "hyphens: auto" not in html:
        html = html.replace(
            ".core-item {",
            ".core-item {\n            hyphens: auto;\n            -webkit-hyphens: auto;"
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        print("  Sparse lines detected -- hyphens:auto injected into core-item as fallback")
        return True
    return False


def check_playwright():
    """Check if Playwright is available, print clear message if not."""
    try:
        import importlib
        importlib.import_module("playwright.sync_api")
        return True
    except ImportError:
        print("\n⚠ WARNING: Playwright is not installed. Layout validation (A4 overflow,")
        print("  orphan words, sparse lines) will be SKIPPED. Install with:")
        print("    pip install playwright && playwright install chromium")
        print("  Without Playwright, the resume may have layout issues.\n")
        return False


def run_validation(output_path, config_path=None):
    """Run validate.py on the generated file and print results."""
    check_playwright()
    validator = Path(__file__).parent / "validate.py"
    cmd = [sys.executable, str(validator), output_path]
    if config_path:
        cmd += ["--config", config_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Print screenshot path hint from stderr
    if result.stderr.strip():
        print(result.stderr.strip())
    # Pretty-print JSON result
    try:
        report = json.loads(result.stdout)
        print("\n-- Validation Report --------------------------------------------------")
        print(f"  About text:  {'PASS' if report.get('about_text', {}).get('pass', True) else 'FAIL (fewer than 2 metrics)'}")
        print(f"  Companies:   {'PASS' if report['companies']['pass'] else 'FAIL'}")
        print(f"  Title lines: {'PASS' if report['title_lines']['pass'] else 'FAIL'}")
        render = report.get("render", {})
        if render.get("render_error"):
            print(f"  Render:      skipped ({render['render_error']})")
        else:
            overflow_msg = f"OVERFLOW +{render['overflow_by_px']}px" if render["overflow"] else "PASS"
            print(f"  Overflow:    {overflow_msg}  ({render['content_height']}px / {render['a4_height']}px A4)")
            if render["orphan_lines"]:
                for o in render["orphan_lines"]:
                    print(f"  ORPHAN:      '{o['orphan_word']}' at end of [{o['selector']}]: \"{o['context'][:50]}\"")
        if report["errors"]:
            print("  Errors:")
            for e in report["errors"]:
                print(f"    * {e}")
        if report["warnings"]:
            for w in report["warnings"]:
                print(f"    ! {w}")
        overall = "ALL CHECKS PASSED" if report["pass"] else "VALIDATION FAILED -- fix issues before sending"
        print(f"\n  {overall}")
        print("-----------------------------------------------------------------------")

        # Sparse line fallback: inject hyphens:auto if warnings present
        sparse_warnings = [w for w in report.get("warnings", []) if "Sparse line" in w]
        if sparse_warnings and report["pass"]:
            injected = inject_hyphens(output_path)
            if injected:
                print("  Re-running validation after hyphens injection...")
                cmd2 = [sys.executable, str(validator), output_path]
                if config_path:
                    cmd2 += ["--config", config_path]
                r2 = subprocess.run(cmd2, capture_output=True, text=True)
                try:
                    rep2 = json.loads(r2.stdout)
                    render2 = rep2.get("render", {})
                    overflow2 = render2.get("overflow", False)
                    orphans2 = rep2.get("render", {}).get("orphan_lines", [])
                    if orphans2:
                        # Revert hyphens
                        with open(output_path, "r", encoding="utf-8") as f:
                            html_rev = f.read()
                        html_rev = html_rev.replace(
                            "            hyphens: auto;\n            -webkit-hyphens: auto;\n", ""
                        )
                        with open(output_path, "w", encoding="utf-8") as f:
                            f.write(html_rev)
                        print("  hyphens caused orphan words -- reverted. Fix desc text manually.")
                        print(f"\n  VALIDATION FAILED -- fix issues before sending")
                        print("-----------------------------------------------------------------------")
                    elif overflow2:
                        with open(output_path, "r", encoding="utf-8") as f:
                            html_rev = f.read()
                        html_rev = html_rev.replace(
                            "            hyphens: auto;\n            -webkit-hyphens: auto;\n", ""
                        )
                        with open(output_path, "w", encoding="utf-8") as f:
                            f.write(html_rev)
                        print("  hyphens caused overflow -- reverted. Sparse line warnings remain (cosmetic only).")
                        print(f"\n  ALL CHECKS PASSED (sparse line warnings accepted)")
                        print("-----------------------------------------------------------------------")
                    else:
                        print(f"\n  ALL CHECKS PASSED (hyphens applied)")
                        print("-----------------------------------------------------------------------")
                except Exception:
                    print(r2.stdout)

    except Exception:
        print(result.stdout)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: generate.py <config.json> <template.html> <output.html>")
        sys.exit(1)

    config_path = sys.argv[1]
    template_path = sys.argv[2]
    output_path = sys.argv[3]

    generate_resume(config_path, template_path, output_path)
    run_validation(output_path, config_path)
