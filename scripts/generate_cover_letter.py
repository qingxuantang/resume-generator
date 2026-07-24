#!/usr/bin/env python3
"""
Cover Letter Generator
Generates cover letters from JSON config and HTML template.

Personal info (name, email, links) is extracted from references/work-history.md.

Usage:
    python3 generate_cover_letter.py <config.json> <output.html>

The template path is auto-selected (single shared template). Language is
read from config["language"] ("en" or "cn").
"""

import json
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
TEMPLATE_PATH = SKILL_DIR / "assets" / "template-cover-letter.html"
WORK_HISTORY_PATH = SKILL_DIR / "references" / "work-history.md"


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
                if value.startswith('"') and value.endswith('"'):
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
    """Load name and contact info from work-history.md."""
    result = {"name": "", "name_en": "", "email": "", "links": []}
    if not WORK_HISTORY_PATH.exists():
        return result
    with open(WORK_HISTORY_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    fm = _parse_yaml_frontmatter(content)
    result["name"] = fm.get("name", "")
    result["name_en"] = fm.get("name_en", "")
    result["email"] = fm.get("email", "")
    result["links"] = fm.get("links", [])
    return result


# Language-specific labels
LABELS = {
    "en": {
        "page_title_fmt": "Cover Letter - {name} - {company}",
        "cover_letter_title": "Cover Letter",
        "application_label": "Application for:",
        "to_label": "To:",
        "default_greeting": "Dear Hiring Team,",
        "default_sign_off": "Best regards,",
        "lang_attr": "en",
    },
    "cn": {
        "page_title_fmt": "求职信 - {name} - {company}",
        "cover_letter_title": "求职信",
        "application_label": "应聘职位：",
        "to_label": "致：",
        "default_greeting": "尊敬的招聘团队：",
        "default_sign_off": "此致敬礼，",
        "lang_attr": "zh-CN",
    },
}


def render_paragraph(p: str) -> str:
    """Render a paragraph. Supports '**Label:** text' and '**Label**: text' syntax."""
    p = p.strip()
    m = re.match(r'^\*\*([^*]+):\*\*\s*(.+)$', p, re.DOTALL)
    if m:
        return f'<p><strong>{m.group(1)}:</strong> {m.group(2).strip()}</p>'
    m = re.match(r'^\*\*([^*]+)\*\*:\s*(.+)$', p, re.DOTALL)
    if m:
        return f'<p><strong>{m.group(1)}:</strong> {m.group(2).strip()}</p>'
    m = re.match(r'^\*\*([^*]+)\uff1a\*\*\s*(.+)$', p, re.DOTALL)
    if m:
        return f'<p><strong>{m.group(1)}\uff1a</strong>{m.group(2).strip()}</p>'
    p_inline = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', p)
    return f'<p>{p_inline}</p>'


def render_body(intro: str, sections: list, closing) -> str:
    """Build the <p>/<h2> body content."""
    parts = []
    if intro:
        parts.append(render_paragraph(intro))

    for section in sections or []:
        title = section.get("title", "").strip()
        paragraphs = section.get("paragraphs", [])
        if title:
            parts.append(f'<h2>{title}</h2>')
        for p in paragraphs:
            parts.append(render_paragraph(p))

    if closing:
        if isinstance(closing, str):
            parts.append(render_paragraph(closing))
        elif isinstance(closing, list):
            for p in closing:
                parts.append(render_paragraph(p))

    return "\n            ".join(parts)


def generate(config_path: str, output_path: str):
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    lang = config.get("language", "en").lower()
    if lang not in LABELS:
        raise ValueError(f"Unsupported language: {lang!r} (expected 'en' or 'cn')")
    labels = LABELS[lang]

    wh = load_work_history()

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    company = config.get("company", "")
    job_title = config.get("job_title", "")
    hiring_team = config.get("hiring_team", f"{company} Hiring Team")
    date_str = config.get("date", "")
    greeting = config.get("greeting", labels["default_greeting"])
    intro = config.get("intro", "")
    sections = config.get("sections", [])
    closing = config.get("closing", "")
    sign_off = config.get("sign_off", labels["default_sign_off"])

    # Build display name
    name = wh.get("name", "")
    name_en = wh.get("name_en", "")
    if name_en and name:
        display_name = f"{name_en.upper()} {name}"
        signature_cursive = name_en.split()[0] if name_en else ""
        signature_print = f"{name_en} ({name})"
    elif name_en:
        display_name = name_en.upper()
        signature_cursive = name_en.split()[0] if name_en else ""
        signature_print = name_en
    else:
        display_name = name
        signature_cursive = name
        signature_print = name

    # Build contact line
    email = wh.get("email", "")
    links = wh.get("links", [])
    contact_parts = []
    if email:
        contact_parts.append(email)
    for link in links:
        if isinstance(link, dict):
            contact_parts.append(link.get("display", link.get("url", "")))
    contact_line = " | ".join(contact_parts)

    body = render_body(intro, sections, closing)

    page_title = labels["page_title_fmt"].format(
        name=name_en or name, company=company
    )

    replacements = {
        "{{LANG}}": labels["lang_attr"],
        "{{PAGE_TITLE}}": page_title,
        "{{COVER_LETTER_TITLE}}": labels["cover_letter_title"],
        "{{HEADER_NAME}}": display_name,
        "{{DATE}}": date_str,
        "{{APPLICATION_LABEL}}": labels["application_label"],
        "{{TO_LABEL}}": labels["to_label"],
        "{{JOB_TITLE}}": job_title,
        "{{HIRING_TEAM}}": hiring_team,
        "{{GREETING}}": greeting,
        "{{SIGN_OFF}}": sign_off,
        "{{BODY}}": body,
        "{{SIGNATURE_CURSIVE}}": signature_cursive,
        "{{SIGNATURE_PRINT}}": signature_print,
        "{{CONTACT_LINE}}": contact_line,
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: generate_cover_letter.py <config.json> <output.html>")
        sys.exit(1)
    generate(sys.argv[1], sys.argv[2])
