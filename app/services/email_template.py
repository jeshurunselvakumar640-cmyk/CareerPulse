"""
CareerPulse email templates.

Produces a table-based, inline-styled HTML digest plus a plain-text
alternative carrying the same information.

Design constraints (email clients):
  * max content width 640px
  * table-based layout, inline CSS only
  * no external stylesheets, no web fonts, no CSS that Gmail strips
  * bulletproof buttons (table + VML-free anchor padding)
  * every dynamic value HTML-escaped
  * URLs validated to http/https before being emitted
"""

import html
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

# -------------------------------------------------------------------
# ESCAPING / SANITIZING
# -------------------------------------------------------------------

def esc(value: Any) -> str:
    """HTML-escapes any value. Always use this for untrusted content."""
    if value is None:
        return ""
    s = str(value)
    # Defensive: strip control characters that could corrupt the document.
    s = "".join(ch for ch in s if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return html.escape(s, quote=True)


def safe_url(url: Any) -> Optional[str]:
    """
    Returns a validated http/https URL, or None.
    Blocks javascript:, data:, vbscript: and other injection vectors.
    """
    if not url or not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate:
        return None
    lowered = candidate.lower().replace("\n", "").replace("\r", "").replace("\t", "")
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return None
    if any(bad in lowered for bad in ("javascript:", "data:", "vbscript:", "file:")):
        return None
    if not re.match(r"^https?://[a-z0-9.\-]+(:\d+)?(/|$|\?)", lowered):
        return None
    return candidate


def clean_inline(value: Any) -> str:
    """
    Removes residual HTML/CSS contamination before text is placed in the email.
    Defense in depth: the upstream pipelines already clean, this guarantees it.
    """
    if value is None:
        return ""
    s = str(value)
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    # CSS utility-class fragments that leaked through upstream extraction
    s = re.sub(r"\b(?:class|style|href|src|alt)\s*=\s*[\"'][^\"']*[\"']", " ", s, flags=re.I)
    s = re.sub(r"\b(?:rounded|shadow|bg-|text-|border-|flex|grid|px-|py-|w-\d|h-\d|items-|justify-)[a-z0-9\-/\[\]\.]*", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def esc_clean(value: Any) -> str:
    return esc(clean_inline(value))


# -------------------------------------------------------------------
# FORMATTING HELPERS
# -------------------------------------------------------------------

def format_display_date(value: Any) -> str:
    """Human-readable publication date/time. Returns '' when unknown."""
    dt = _as_datetime(value)
    if not dt:
        return ""
    return dt.strftime("%b %-d, %Y") if _supports_dash_d() else dt.strftime("%b %d, %Y")


def format_display_datetime(value: Any) -> str:
    dt = _as_datetime(value)
    if not dt:
        return ""
    base = dt.strftime("%b %d, %Y") if _supports_dash_d() else dt.strftime("%b %d, %Y")
    base = base.replace("  ", " ")
    return f"{base}, {dt.strftime('%I:%M %p').lstrip('0')}"


def _supports_dash_d() -> bool:
    return False


def _as_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s[:24].strip(), fmt)
        except Exception:
            continue
    return None


TIER_COLORS = {
    "gold": ("#FDE68A", "#78350F", "#F59E0B"),
    "silver": ("#E2E8F0", "#334155", "#94A3B8"),
    "bronze": ("#FED7AA", "#7C2D12", "#C2703B"),
}

ACCENT = "#4F46E5"
ACCENT_DARK = "#1E1B4B"
INK = "#0F172A"
MUTED = "#64748B"
LINE = "#E2E8F0"
PAGE_BG = "#F1F5F9"


# -------------------------------------------------------------------
# HTML BUILDING BLOCKS
# -------------------------------------------------------------------

def _button(url: str, label: str, accent: str = ACCENT) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:separate;">'
        f'<tr><td align="center" bgcolor="{accent}" style="border-radius:8px;">'
        f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
        f'style="display:inline-block;padding:11px 20px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:13px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">'
        f'{esc(label)} &rarr;</a></td></tr></table>'
    )


def _section_heading(number: Optional[int], text: str, color: str = ACCENT) -> str:
    left = (
        f'<span style="display:inline-block;min-width:26px;color:{color};font-weight:800;">{number:02d}</span>'
        f'<span style="color:{color};">&nbsp;{esc(text)}</span>'
    ) if number is not None else f'<span style="color:{color};">{esc(text)}</span>'

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:0 0 16px 0;">'
        '<tr>'
        f'<td style="padding:0 0 10px 0;border-bottom:2px solid {ACCENT};'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:13px;'
        f'font-weight:800;letter-spacing:0.12em;text-transform:uppercase;">{left}</td>'
        '</tr></table>'
    )


def _news_card(index: int, story: Dict[str, Any]) -> str:
    title = esc_clean(story.get("title") or "Untitled")
    source = esc_clean(story.get("source_name") or story.get("source") or "Source")
    published = format_display_datetime(story.get("published_at"))
    summary = esc_clean(story.get("summary") or "")[:600]
    why = esc_clean(story.get("why_it_matters") or "")[:400]
    url = safe_url(story.get("source_url") or story.get("url"))

    meta_bits = [source]
    if published:
        meta_bits.append(published)

    why_block = ""
    if why:
        why_block = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse;margin:14px 0 0 0;">'
            '<tr><td style="background:#EEF2FF;border-left:3px solid #6366F1;border-radius:6px;padding:11px 14px;">'
            '<p style="margin:0 0 4px 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
            'font-size:10px;font-weight:800;letter-spacing:0.08em;text-transform:uppercase;color:#4338CA;">'
            'Why this matters to you</p>'
            f'<p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
            f'font-size:13px;line-height:1.6;color:#3730A3;">{why}</p>'
            '</td></tr></table>'
        )

    button_block = f'<div style="margin-top:16px;">{_button(url, "Read Article")}</div>' if url else ""

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:separate;margin:0 0 16px 0;">'
        '<tr><td style="background:#FFFFFF;border:1px solid ' + LINE + ';border-radius:12px;padding:20px;">'

        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;"><tr>'
        '<td width="34" valign="top" style="padding:0 12px 0 0;">'
        '<span style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:18px;font-weight:800;color:#C7D2FE;">'
        f'{index:02d}</span></td>'
        '<td valign="top">'
        f'<p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:16px;font-weight:700;line-height:1.4;color:{INK};">{title}</p>'
        f'<p style="margin:5px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:11px;color:{MUTED};">{" &middot; ".join(meta_bits)}</p>'
        '</td></tr></table>'

        + (f'<p style="margin:12px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
           f'font-size:14px;line-height:1.65;color:#334155;">{summary}</p>' if summary else "")
        + why_block
        + button_block

        + '</td></tr></table>'
    )


def _opportunity_card(opp: Dict[str, Any]) -> str:
    match = opp.get("match") or {}
    tier = (match.get("tier") or "Bronze").lower()
    bg, fg, border = TIER_COLORS.get(tier, TIER_COLORS["bronze"])
    score = match.get("score")

    title = esc_clean(opp.get("title") or "Untitled role")
    company = esc_clean(opp.get("company") or "Company not specified")
    # Location MUST come from the verified source, never from the user's profile.
    location = esc_clean(opp.get("location") or "")
    location_block = (f'<p style="margin:6px 0 0 0;font-size:13px;color:#334155;">&#128205; {location}</p>'
                      if location else "")

    badge_text = f"{tier.upper()} MATCH"
    if isinstance(score, (int, float)) and score > 0:
        badge_text += f" &middot; {int(score)}%"

    rows = ""

    def _row(label: str, value: str, value_color: str = "#334155") -> str:
        if not value:
            return ""
        return (
            '<tr>'
            f'<td valign="top" style="padding:3px 12px 3px 0;width:104px;font-size:12px;color:{MUTED};white-space:nowrap;">{label}</td>'
            f'<td valign="top" style="padding:3px 0;font-size:13px;color:{value_color};">{value}</td>'
            '</tr>'
        )

    employment = esc_clean(opp.get("employment_type") or "")
    experience = esc_clean(opp.get("experience_level") or "")
    if employment or experience:
        parts = [p for p in (employment, experience) if p]
        rows += _row("Type", " &middot; ".join(parts))

    required = ", ".join(esc_clean(s) for s in (opp.get("required_skills") or []) if s)
    rows += _row("Required", required)

    preferred = ", ".join(esc_clean(s) for s in (opp.get("preferred_skills") or []) if s)
    rows += _row("Preferred", preferred)

    matched = ", ".join(esc_clean(s) for s in (match.get("matched_skills") or opp.get("skill_analysis", {}).get("matched", [])) if s)
    rows += _row("&#10003; Matched", matched, value_color="#047857")

    missing = ", ".join(esc_clean(s) for s in (match.get("missing_skills") or opp.get("skill_analysis", {}).get("missing", [])) if s)
    rows += _row("Skill gap", missing, value_color="#B45309")

    # Deadline is only shown when actually verified.
    deadline = opp.get("deadline")
    if deadline:
        rows += _row("Deadline", esc_clean(format_display_date(deadline) or str(deadline)))

    url = safe_url(opp.get("application_url")) or safe_url(opp.get("source_url"))
    button_block = f'<div style="margin-top:16px;">{_button(url, "View Opportunity")}</div>' if url else ""

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:separate;margin:0 0 16px 0;">'
        '<tr><td style="background:#FFFFFF;border:1px solid ' + LINE + ';border-radius:12px;padding:20px;">'

        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;"><tr>'
        '<td valign="top">'
        f'<p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:16px;font-weight:700;line-height:1.35;color:{INK};">{title}</p>'
        f'<p style="margin:4px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:13px;color:{MUTED};">{company}</p>'
        + location_block +
        '</td>'
        f'<td align="right" valign="top" style="padding:0 0 0 12px;">'
        f'<span style="display:inline-block;background:{bg};color:{fg};border:1px solid {border};'
        f'border-radius:999px;padding:4px 10px;font-size:10px;font-weight:800;letter-spacing:0.06em;white-space:nowrap;">'
        f'{badge_text}</span></td>'
        '</tr></table>'

        + (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
           f'style="border-collapse:collapse;margin-top:14px;">{rows}</table>' if rows else "")
        + button_block

        + '</td></tr></table>'
    )


def _snapshot(news_count: int, opp_count: int, gold_count: int, gap_count: int) -> str:
    def cell(value: int, label: str) -> str:
        return (
            '<td align="center" valign="top" style="padding:0 6px;">'
            f'<p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
            f'font-size:26px;font-weight:800;line-height:1.1;color:{INK};">{value}</p>'
            f'<p style="margin:4px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
            f'font-size:10px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:{MUTED};">{esc(label)}</p>'
            '</td>'
        )

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;background:#F8FAFC;border:1px solid ' + LINE + ';border-radius:12px;">'
        '<tr><td style="padding:18px 10px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;"><tr>'
        + cell(news_count, "Stories")
        + cell(opp_count, "Opportunities")
        + cell(gold_count, "Gold")
        + cell(gap_count, "Skills")
        + '</tr></table>'
        '</td></tr></table>'
    )


def _empty_note(message: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">'
        '<tr><td style="background:#F8FAFC;border:1px dashed ' + LINE + ';border-radius:12px;padding:22px;text-align:center;">'
        f'<p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:13px;color:{MUTED};">{esc(message)}</p>'
        '</td></tr></table>'
    )


def _footer(period: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;margin-top:28px;">'
        '<tr><td style="padding:24px 0 0 0;border-top:1px solid ' + LINE + ';text-align:center;">'
        f'<p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:15px;font-weight:800;color:{INK};">CareerPulse</p>'
        f'<p style="margin:4px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:12px;color:{MUTED};">Your Personal Career Opportunity Agent</p>'
        f'<p style="margin:12px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'font-size:12px;font-weight:600;color:{ACCENT};letter-spacing:0.04em;">Discover. Match. Grow.</p>'
        + (f'<p style="margin:12px 0 0 0;font-size:11px;color:#94A3B8;">{esc(period)}</p>' if period else "")
        + '</td></tr></table>'
    )


# -------------------------------------------------------------------
# MAIN TEMPLATES
# -------------------------------------------------------------------

def build_digest_html(
    candidate_name: str,
    stories: Sequence[Dict[str, Any]],
    opportunities: Sequence[Dict[str, Any]],
    skill_gaps: Sequence[str],
    period_label: str = "",
    dashboard_url: Optional[str] = None,
) -> str:
    """Renders the full HTML digest. All dynamic content is escaped."""

    stories = list(stories or [])[:6]
    opportunities = list(opportunities or [])

    gold_count = sum(
        1 for o in opportunities
        if (o.get("match") or {}).get("tier", "").lower() == "gold"
    )

    first_name = (candidate_name or "there").strip().split()[0] if str(candidate_name or "").strip() else "there"

    # ---- news section ----
    if stories:
        news_inner = "".join(_news_card(i + 1, s) for i, s in enumerate(stories))
    else:
        news_inner = _empty_note("No major technology developments were verified in the last 24 hours.")

    # ---- opportunities section ----
    if opportunities:
        opp_inner = "".join(_opportunity_card(o) for o in opportunities)
    else:
        opp_inner = _empty_note("No verified opportunities matching your profile were found today.")

    # ---- skill gaps section ----
    gaps = [g for g in (skill_gaps or []) if g][:6]
    gaps_inner = "".join(
        f'<span style="display:inline-block;margin:0 6px 8px 0;background:#EEF2FF;border:1px solid #C7D2FE;'
        f'border-radius:999px;padding:6px 12px;font-size:12px;font-weight:700;color:#3730A3;">{esc_clean(g)}</span>'
        for g in gaps
    ) if gaps else (
        '<p style="margin:0;font-size:13px;color:#64748B;">All primary required technical skills in your recent matches are currently satisfied.</p>'
    )

    snapshot = _snapshot(len(stories), len(opportunities), gold_count, len(gaps))

    dashboard_block = ""
    dash = safe_url(dashboard_url)
    if dash:
        dashboard_block = f'<div style="margin-top:18px;">{_button(dash, "Open CareerPulse Dashboard", "#0F172A")}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>CareerPulse Daily Digest</title>
</head>
<body style="margin:0;padding:0;background:{PAGE_BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;background:{PAGE_BG};">
<tr><td align="center" style="padding:24px 12px;">

<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;max-width:640px;">

<!-- BRAND HEADER -->
<tr><td style="background:{ACCENT_DARK};border-radius:14px 14px 0 0;padding:28px 28px 26px 28px;">
  <p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:22px;font-weight:800;color:#FFFFFF;letter-spacing:-0.01em;">CareerPulse</p>
  <p style="margin:5px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:13px;color:#A5B4FC;letter-spacing:0.02em;">Your Personal Career Opportunity Agent</p>
</td></tr>

<!-- GREETING -->
<tr><td style="background:#FFFFFF;padding:28px 28px 24px 28px;border-left:1px solid {LINE};border-right:1px solid {LINE};">
  <p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:17px;font-weight:700;color:{INK};">Hello {esc(first_name)} &#128075;</p>
  <p style="margin:14px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:20px;font-weight:800;line-height:1.3;color:{INK};letter-spacing:-0.01em;">Your 24-hour Technology &amp; Career Digest</p>
  {f'<p style="margin:6px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:13px;color:{MUTED};">{esc(period_label)}</p>' if period_label else ''}
  {dashboard_block}
</td></tr>

<!-- TECHNOLOGY NEWS -->
<tr><td style="background:#FFFFFF;padding:8px 28px 24px 28px;border-left:1px solid {LINE};border-right:1px solid {LINE};">
  {_section_heading(None, "Technology Intelligence")}
  {news_inner}
</td></tr>

<!-- OPPORTUNITIES -->
<tr><td style="background:#FFFFFF;padding:8px 28px 24px 28px;border-left:1px solid {LINE};border-right:1px solid {LINE};">
  {_section_heading(None, "Career Opportunities for You")}
  {opp_inner}
</td></tr>

<!-- SKILL GAPS -->
<tr><td style="background:#FFFFFF;padding:8px 28px 24px 28px;border-left:1px solid {LINE};border-right:1px solid {LINE};">
  {_section_heading(None, "Skills to Strengthen")}
  <p style="margin:0 0 12px 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:12px;color:{MUTED};">Based on today&rsquo;s verified opportunities:</p>
  <p style="margin:0;">{gaps_inner}</p>
</td></tr>

<!-- SNAPSHOT -->
<tr><td style="background:#FFFFFF;padding:8px 28px 24px 28px;border-left:1px solid {LINE};border-right:1px solid {LINE};">
  {_section_heading(None, "Your Daily Snapshot")}
  {snapshot}
</td></tr>

<!-- FOOTER -->
<tr><td style="background:#FFFFFF;border-radius:0 0 14px 14px;padding:4px 28px 28px 28px;border-left:1px solid {LINE};border-right:1px solid {LINE};border-bottom:1px solid {LINE};">
  {_footer(period_label)}
</td></tr>

</table>
<p style="margin:16px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:11px;color:#94A3B8;text-align:center;">CareerPulse &middot; AI-powered career opportunity discovery</p>

</td></tr>
</table>
</body>
</html>"""


def build_digest_text(
    candidate_name: str,
    stories: Sequence[Dict[str, Any]],
    opportunities: Sequence[Dict[str, Any]],
    skill_gaps: Sequence[str],
    period_label: str = "",
    dashboard_url: Optional[str] = None,
) -> str:
    """
    Plain-text alternative with the same information.
    Uses explicit 'Read: <url>' lines - never Markdown links.
    """
    stories = list(stories or [])[:6]
    opportunities = list(opportunities or [])
    gaps = [g for g in (skill_gaps or []) if g][:6]

    first_name = (str(candidate_name).strip().split()[0]) if str(candidate_name or "").strip() else "there"

    lines: List[str] = []
    lines.append("CAREERPULSE")
    lines.append("Your Personal Career Opportunity Agent")
    lines.append("")
    lines.append(f"Hello {first_name},")
    lines.append("")
    lines.append("Your 24-hour Technology & Career Digest")
    if period_label:
        lines.append(period_label)
    lines.append("")

    # ---- NEWS ----
    lines.append("=" * 60)
    lines.append("TECHNOLOGY INTELLIGENCE")
    lines.append("=" * 60)
    lines.append("")
    if stories:
        for i, story in enumerate(stories, 1):
            lines.append(f"{i}. {clean_inline(story.get('title') or 'Untitled')}")
            source = clean_inline(story.get("source_name") or story.get("source") or "Source")
            lines.append(f"Source: {source}")
            published = format_display_datetime(story.get("published_at"))
            if published:
                lines.append(f"Published: {published}")
            summary = clean_inline(story.get("summary") or "")
            if summary:
                lines.append(summary)
            why = clean_inline(story.get("why_it_matters") or "")
            if why:
                lines.append("")
                lines.append(f"Why this matters to you: {why}")
            url = safe_url(story.get("source_url") or story.get("url"))
            if url:
                lines.append("")
                lines.append(f"Read: {url}")
            lines.append("-" * 50)
            lines.append("")
    else:
        lines.append("No major technology developments were verified in the last 24 hours.")
        lines.append("")

    # ---- OPPORTUNITIES ----
    lines.append("=" * 60)
    lines.append("CAREER OPPORTUNITIES FOR YOU")
    lines.append("=" * 60)
    lines.append("")
    if opportunities:
        for opp in opportunities:
            match = opp.get("match") or {}
            tier = (match.get("tier") or "Bronze").upper()
            score = match.get("score")
            score_txt = f" ({int(score)}%)" if isinstance(score, (int, float)) and score > 0 else ""

            lines.append(clean_inline(opp.get("title") or "Untitled role"))
            lines.append(f"Company: {clean_inline(opp.get('company') or 'Not specified')}")

            # Verified source location only.
            location = clean_inline(opp.get("location") or "")
            if location:
                lines.append(f"Location: {location}")

            lines.append(f"Match: {tier}{score_txt}")

            required = ", ".join(clean_inline(s) for s in (opp.get("required_skills") or []) if s)
            if required:
                lines.append(f"Required: {required}")
            preferred = ", ".join(clean_inline(s) for s in (opp.get("preferred_skills") or []) if s)
            if preferred:
                lines.append(f"Preferred: {preferred}")
            matched = ", ".join(clean_inline(s) for s in (match.get("matched_skills") or []) if s)
            if matched:
                lines.append(f"Matched Skills: {matched}")
            missing = ", ".join(clean_inline(s) for s in (match.get("missing_skills") or []) if s)
            if missing:
                lines.append(f"Skill Gap: {missing}")

            deadline = opp.get("deadline")
            if deadline:
                lines.append(f"Deadline: {format_display_date(deadline) or clean_inline(str(deadline))}")

            url = safe_url(opp.get("application_url")) or safe_url(opp.get("source_url"))
            if url:
                lines.append(f"Apply: {url}")
            lines.append("-" * 50)
            lines.append("")
    else:
        lines.append("No verified opportunities matching your profile were found today.")
        lines.append("")

    # ---- SKILL GAPS ----
    lines.append("=" * 60)
    lines.append("SKILLS TO STRENGTHEN")
    lines.append("=" * 60)
    lines.append("")
    if gaps:
        for g in gaps:
            lines.append(f"- {clean_inline(g)}")
    else:
        lines.append("- All primary required technical skills are currently satisfied.")
    lines.append("")

    # ---- SNAPSHOT ----
    gold_count = sum(1 for o in opportunities if (o.get("match") or {}).get("tier", "").lower() == "gold")
    lines.append("=" * 60)
    lines.append("YOUR DAILY SNAPSHOT")
    lines.append("=" * 60)
    lines.append(f"{len(stories)} Technology Stories")
    lines.append(f"{len(opportunities)} Relevant Opportunities")
    lines.append(f"{gold_count} Gold Matches")
    lines.append(f"{len(gaps)} Skills To Explore")
    lines.append("")

    dash = safe_url(dashboard_url)
    if dash:
        lines.append(f"Dashboard: {dash}")
        lines.append("")

    lines.append("Regards,")
    lines.append("CareerPulse")
    lines.append("Discover. Match. Grow.")

    return "\n".join(lines)


def build_test_email_html(subject_line: str = "CareerPulse SMTP Test") -> str:
    """Minimal HTML payload for /api/email/test."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(subject_line)}</title></head>
<body style="margin:0;padding:0;background:{PAGE_BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;background:{PAGE_BG};">
<tr><td align="center" style="padding:32px 12px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;width:100%;max-width:640px;">
<tr><td style="background:{ACCENT_DARK};border-radius:14px 14px 0 0;padding:28px;">
  <p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:22px;font-weight:800;color:#FFFFFF;">CareerPulse</p>
  <p style="margin:5px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:13px;color:#A5B4FC;">Your Personal Career Opportunity Agent</p>
</td></tr>
<tr><td style="background:#FFFFFF;border-radius:0 0 14px 14px;padding:28px;border-left:1px solid {LINE};border-right:1px solid {LINE};border-bottom:1px solid {LINE};">
  <p style="margin:0 0 14px 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:18px;font-weight:800;color:{INK};">CareerPulse SMTP Test</p>
  <p style="margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.6;color:#334155;">HTML email delivery is working successfully.</p>
  <p style="margin:14px 0 0 0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:13px;color:{MUTED};">This message includes both an HTML part and a plain-text fallback.</p>
</td></tr>
</table>
</td></tr></table>
</body>
</html>"""