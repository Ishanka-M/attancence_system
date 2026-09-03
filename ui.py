"""
ui.py
=====
The presentation layer: one stylesheet plus a set of small HTML component
helpers. Keeping it here means app.py stays about behaviour, and every
page gets the same cards, badges, headers and empty states.

Everything renders through st.markdown(..., unsafe_allow_html=True).
Native Streamlit widgets are styled by the same stylesheet, so buttons,
inputs, tabs and tables match the custom blocks.
"""
from __future__ import annotations

import html

import streamlit as st

# ───────────────────────────── tokens ─────────────────────────────
INK = "#0f1720"
INK_2 = "#33404f"
MUTED = "#6b7784"
FAINT = "#98a2ae"
LINE = "#e3e7ec"
LINE_2 = "#eef1f4"
BG = "#f6f7f9"
SURFACE = "#ffffff"

ACCENT = "#0d6e63"
ACCENT_2 = "#0f8578"
OK = "#177245"
WARN = "#9a5f0b"
DANGER = "#b02318"
INFO = "#2a5d99"

NAV_BG = "#0e1621"
NAV_BG_2 = "#16202d"
NAV_TEXT = "#c8d2dc"

TONES = {
    "ok": OK, "warn": WARN, "danger": DANGER, "info": INFO,
    "accent": ACCENT, "muted": MUTED, "neutral": INK_2,
}


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


# ───────────────────────────── stylesheet ─────────────────────────────
def inject_css():
    st.markdown(f"""
<style>
  /* ── base ─────────────────────────────────────────────── */
  .stApp {{ background:{BG}; }}

  /* Streamlit's toolbar floats over the page, so clear room for it and
     let the page scroll underneath instead of being hidden by it. */
  header[data-testid="stHeader"] {{ background:transparent; height:2.5rem;
      z-index:9; }}
  header[data-testid="stHeader"]::before {{ content:none; }}
  .block-container {{ padding-top:3.4rem; padding-bottom:3.5rem;
      padding-left:2rem; padding-right:2rem; max-width:1500px; }}
  @media (max-width:1200px) {{
    .block-container {{ padding-left:1.1rem; padding-right:1.1rem; }}
  }}

  h1,h2,h3,h4,h5 {{ color:{INK}; letter-spacing:-.015em; font-weight:640; }}
  p, span, label, div {{ color:{INK_2}; }}
  a {{ color:{ACCENT}; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  #MainMenu, footer {{ visibility:hidden; }}

  /* default vertical rhythm is very loose for a dense operations screen */
  section.main div[data-testid="stVerticalBlock"] {{ gap:.62rem; }}
  section.main div[data-testid="stHorizontalBlock"] {{ gap:.7rem; }}

  /* ── top bar ──────────────────────────────────────────── */
  .tb {{ display:flex; align-items:center; justify-content:space-between;
         gap:1rem; flex-wrap:wrap;
         background:{SURFACE}; border:1px solid {LINE}; border-radius:12px;
         padding:.7rem 1.1rem; margin-bottom:1.1rem; }}
  .tb .brand {{ display:flex; align-items:center; gap:.65rem; }}
  .tb .mark {{ width:30px; height:30px; border-radius:8px; flex:none;
               background:linear-gradient(135deg,{ACCENT},{ACCENT_2});
               color:#fff; font-size:.82rem; font-weight:700;
               display:flex; align-items:center; justify-content:center; }}
  .tb .name {{ font-size:.95rem; font-weight:660; color:{INK}; line-height:1.15; }}
  .tb .sub {{ font-size:.73rem; color:{FAINT}; }}
  .tb .meta {{ display:flex; align-items:center; gap:1.35rem;
               flex-wrap:wrap; }}
  @media (max-width:900px) {{
    .tb {{ padding:.6rem .8rem; }}
    .tb .meta {{ gap:.9rem; width:100%; justify-content:flex-start; }}
    .tb .m {{ text-align:left; }}
  }}
  .tb .m {{ text-align:right; }}
  .tb .m .k {{ font-size:.67rem; color:{FAINT}; letter-spacing:.02em; }}
  .tb .m .v {{ font-size:.82rem; font-weight:600; color:{INK};
               font-variant-numeric:tabular-nums; }}

  /* ── page header ──────────────────────────────────────── */
  .ph {{ margin:0 0 1.8rem 0; padding-bottom:.8rem; }}
  .ph .row {{ display:flex; align-items:flex-start; gap:1rem; }}
  .ph .ic {{ font-size:1.6rem; line-height:1.5; }}
  .ph .t {{ font-size:1.75rem; font-weight:720; color:{INK}; line-height:1.25; letter-spacing:-.02em; }}
  .ph .s {{ font-size:.92rem; color:{MUTED}; margin-top:.4rem; max-width:85ch; line-height:1.45; }}
  .ph .rule {{ height:2px; background:linear-gradient(90deg, {ACCENT}, {ACCENT}00); margin-top:1rem; }}

  /* ── section heading ──────────────────────────────────── */
  .sec {{ margin:2rem 0 1rem 0; padding-left:.8rem; border-left:3px solid {ACCENT}; }}
  .sec .t {{ font-size:1.02rem; font-weight:680; color:{INK};
             display:flex; align-items:center; gap:.55rem; letter-spacing:-.01em; }}
  .sec .t .n {{ width:24px; height:24px; border-radius:6px; flex:none;
                background:{ACCENT}; color:#fff; font-size:.75rem;
                font-weight:700; display:flex; align-items:center;
                justify-content:center; }}
  .sec .d {{ font-size:.85rem; color:{MUTED}; margin-top:.25rem; line-height:1.4; }}

  /* ── stat cards ───────────────────────────────────────── */
  /* auto-fit keeps the cards on screen at any width instead of overflowing */
  .grid {{ display:grid; gap:.7rem; margin:.15rem 0 .35rem 0;
           grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); }}
  .g2 {{ grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); }}
  .g3 {{ grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); }}
  .g4 {{ grid-template-columns:repeat(auto-fit,minmax(195px,1fr)); }}
  .g5 {{ grid-template-columns:repeat(auto-fit,minmax(175px,1fr)); }}
  .stat {{ background:{SURFACE}; border:1px solid {LINE}; border-radius:12px;
           padding:1.15rem 1.25rem; position:relative; overflow:hidden;
           transition:all .2s ease; box-shadow:0 1px 2px rgba(15,23,32,.04); }}
  .stat:hover {{ border-color:{ACCENT}40; box-shadow:0 4px 12px rgba(13,110,99,.08); 
                 transform:translateY(-2px); }}
  .stat .edge {{ position:absolute; left:0; top:0; bottom:0; width:4px; border-radius:12px 0 0 12px; }}
  .stat .v {{ font-size:1.85rem; font-weight:720; line-height:1.1; color:{INK};
              font-variant-numeric:tabular-nums; letter-spacing:-.025em; }}
  .stat .l {{ font-size:.82rem; color:{MUTED}; margin-top:.35rem; font-weight:550; }}
  .stat .n {{ font-size:.765rem; color:{FAINT}; margin-top:.5rem; }}

  /* ── badges ───────────────────────────────────────────── */
  .bdg {{ display:inline-block; padding:.15rem .55rem; border-radius:6px;
          font-size:.735rem; font-weight:620; border:1px solid;
          white-space:nowrap; }}
  .bdg + .bdg {{ margin-left:.3rem; }}

  /* ── pipeline strip ───────────────────────────────────── */
  .pipe {{ display:flex; align-items:stretch; gap:.5rem; margin:.5rem 0 1rem 0;
           flex-wrap:wrap; }}
  .pipe .seg {{ min-width:160px; }}
  .pipe .seg {{ flex:1; background:{SURFACE}; border:1.5px solid {LINE};
                border-radius:12px; padding:1rem 1.1rem; position:relative;
                transition:all .2s ease; }}
  .pipe .seg:hover {{ border-color:{ACCENT}50; box-shadow:0 2px 8px rgba(13,110,99,.06); }}
  .pipe .seg .n {{ font-size:1.52rem; font-weight:720; color:{INK};
                   font-variant-numeric:tabular-nums; line-height:1.1; }}
  .pipe .seg .c {{ font-size:.8rem; color:{MUTED}; margin-top:.3rem; font-weight:550; }}
  .pipe .seg .bar {{ height:4px; border-radius:2px; margin-top:.8rem;
                     background:{LINE_2}; overflow:hidden; }}
  .pipe .seg .bar i {{ display:block; height:100%; border-radius:2px; transition:width .3s ease; }}
  .pipe .arrow {{ display:flex; align-items:center; color:{FAINT};
                  font-size:1.05rem; padding:0 .15rem; }}
  @media (max-width:1000px) {{ .pipe {{ flex-wrap:wrap; }} .pipe .arrow {{ display:none; }} }}

  /* ── stepper ──────────────────────────────────────────── */
  .steps {{ display:flex; gap:.45rem; margin:.2rem 0 1rem 0; flex-wrap:wrap; }}
  .steps .s {{ min-width:150px; }}
  .steps .s {{ flex:1; background:{SURFACE}; border:1px solid {LINE};
               border-radius:10px; padding:.6rem .8rem; }}
  .steps .s .k {{ display:flex; align-items:center; gap:.45rem;
                  font-size:.79rem; font-weight:600; color:{FAINT}; }}
  .steps .s .k b {{ width:18px; height:18px; border-radius:50%; flex:none;
                    background:{LINE_2}; color:{FAINT}; font-size:.68rem;
                    display:flex; align-items:center; justify-content:center; }}
  .steps .s.on {{ border-color:{ACCENT}55; background:{ACCENT}0a; }}
  .steps .s.on .k {{ color:{ACCENT}; }}
  .steps .s.on .k b {{ background:{ACCENT}; color:#fff; }}
  .steps .s.done .k {{ color:{INK_2}; }}
  .steps .s.done .k b {{ background:{OK}; color:#fff; }}

  /* ── callouts ─────────────────────────────────────────── */
  .note {{ border:1px solid; border-radius:10px; padding:.7rem .9rem;
           font-size:.83rem; margin:.35rem 0 .8rem 0; }}
  .note .t {{ font-weight:640; margin-bottom:.14rem; }}
  .note .b {{ opacity:.92; }}

  /* ── empty state ──────────────────────────────────────── */
  .empty {{ background:{SURFACE}; border:1px dashed #d6dce3; border-radius:12px;
            padding:2.1rem 1.4rem; text-align:center; margin:.4rem 0 1rem 0; }}
  .empty .ic {{ font-size:1.6rem; opacity:.5; }}
  .empty .t {{ font-size:.98rem; font-weight:620; color:{INK}; margin-top:.5rem; }}
  .empty .s {{ font-size:.83rem; color:{MUTED}; margin-top:.3rem;
               max-width:52ch; margin-left:auto; margin-right:auto; }}

  /* ── file tile ────────────────────────────────────────── */
  .file {{ background:{SURFACE}; border:1px solid {LINE}; border-radius:10px;
           padding:.6rem .75rem; margin-bottom:.4rem; }}
  .file .h {{ display:flex; align-items:center; gap:.5rem; }}
  .file .ic {{ width:26px; height:26px; border-radius:6px; flex:none;
               display:flex; align-items:center; justify-content:center;
               font-size:.66rem; font-weight:700; color:#fff; }}
  .file .nm {{ font-size:.815rem; font-weight:590; color:{INK};
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .file .mt {{ font-size:.715rem; color:{FAINT}; margin-top:.18rem; }}

  /* ── key/value list ───────────────────────────────────── */
  .kv {{ background:{SURFACE}; border:1px solid {LINE}; border-radius:10px;
         padding:.15rem .9rem; }}
  .kv .r {{ display:flex; justify-content:space-between; gap:1rem;
            padding:.5rem 0; border-bottom:1px solid {LINE_2}; font-size:.825rem; }}
  .kv .r:last-child {{ border-bottom:none; }}
  .kv .k {{ color:{MUTED}; }}
  .kv .v {{ color:{INK}; font-weight:590; font-variant-numeric:tabular-nums;
            text-align:right; }}

  /* ── native widgets ───────────────────────────────────── */
  .stButton>button, .stDownloadButton>button {{
      border-radius:8px; font-weight:560; font-size:.845rem;
      border:1px solid {LINE}; padding:.34rem .85rem; transition:all .13s ease; }}
  .stButton>button:hover, .stDownloadButton>button:hover {{
      border-color:{ACCENT}; color:{ACCENT}; }}
  .stButton>button[kind="primary"] {{
      background:{ACCENT}; border-color:{ACCENT}; color:#fff; }}
  .stButton>button[kind="primary"]:hover {{
      background:{ACCENT_2}; border-color:{ACCENT_2}; color:#fff; }}

  div[data-testid="stDataFrame"] {{ border:1px solid {LINE}; border-radius:10px;
      overflow:hidden; background:{SURFACE}; }}
  div[data-testid="stDataFrame"] * {{ font-size:.825rem; }}
  .stDataFrame {{ font-size:.825rem; }}

  div[data-testid="stExpander"] {{ border:1px solid {LINE}; border-radius:10px;
                                   background:{SURFACE}; }}
  div[data-testid="stExpander"] summary {{ font-size:.85rem; font-weight:580; }}

  div[data-testid="stFileUploader"] {{ background:{SURFACE};
      border:1px dashed #d1d8e0; border-radius:11px; padding:.55rem .75rem; }}
  div[data-testid="stFileUploader"] section {{ padding:.35rem 0; }}

  .stTabs [data-baseweb="tab-list"] {{ gap:.15rem; border-bottom:1px solid {LINE}; }}
  .stTabs [data-baseweb="tab"] {{ font-size:.855rem; font-weight:560;
      padding:.5rem .85rem; color:{MUTED}; }}
  .stTabs [aria-selected="true"] {{ color:{ACCENT}; }}

  .stTextInput input, .stNumberInput input, .stTextArea textarea,
  div[data-baseweb="select"]>div {{ border-radius:8px; font-size:.85rem; }}

  div[data-testid="stMetric"] {{ background:{SURFACE}; border:1px solid {LINE};
      border-radius:10px; padding:.7rem .9rem; }}

  /* ── sidebar (HIDDEN) ───────────────────────────────────── */
  section[data-testid="stSidebar"] {{ display: none !important; }}
  .block-container {{ padding-left: 2rem !important; padding-right: 2rem !important; }}

  .navbrand {{ display: none !important; }}
  .navlabel {{ display: none !important; }}
  .navfoot {{ display: none !important; }}

  /* nav rows are buttons so only one can ever look active */
  section[data-testid="stSidebar"] .stButton {{ display: none !important; }}

  /* Unused - nav moved to top */
</style>
""", unsafe_allow_html=True)


# ───────────────────────────── components ─────────────────────────────
def topbar(title: str, subtitle: str = "", meta: list[tuple[str, str]] | None = None):
    m = "".join(f'<div class="m"><div class="k">{esc(k)}</div>'
                f'<div class="v">{esc(v)}</div></div>' for k, v in (meta or []))
    st.markdown(
        f'<div class="tb"><div class="brand"><div class="mark">GRN</div>'
        f'<div><div class="name">{esc(title)}</div>'
        f'<div class="sub">{esc(subtitle)}</div></div></div>'
        f'<div class="meta">{m}</div></div>', unsafe_allow_html=True)


def page_header(icon: str, title: str, subtitle: str = "", badges: list = None):
    b = " ".join(badges or [])
    st.markdown(
        f'<div class="ph"><div class="row"><div class="ic">{icon}</div>'
        f'<div><div class="t">{esc(title)} {b}</div>'
        f'<div class="s">{esc(subtitle)}</div></div></div>'
        f'<div class="rule"></div></div>', unsafe_allow_html=True)


def section(title: str, desc: str = "", number: str | int | None = None):
    n = f'<span class="n">{esc(number)}</span>' if number is not None else ""
    st.markdown(f'<div class="sec"><div class="t">{n}{esc(title)}</div>'
                + (f'<div class="d">{esc(desc)}</div>' if desc else "")
                + '</div>', unsafe_allow_html=True)


def badge(text: str, tone: str = "muted") -> str:
    c = TONES.get(tone, MUTED)
    return (f'<span class="bdg" style="color:{c};border-color:{c}44;'
            f'background:{c}12">{esc(text)}</span>')


def stats(items: list[dict], cols: int | None = None):
    """items: [{value, label, note?, tone?}]"""
    n = cols or min(max(len(items), 2), 5)
    cells = ""
    for it in items:
        c = TONES.get(it.get("tone", ""), "")
        edge = f'<div class="edge" style="background:{c}"></div>' if c else ""
        vcol = f'color:{c}' if c else ""
        cells += (f'<div class="stat">{edge}'
                  f'<div class="v" style="{vcol}">{esc(it["value"])}</div>'
                  f'<div class="l">{esc(it["label"])}</div>'
                  + (f'<div class="n">{esc(it["note"])}</div>' if it.get("note") else "")
                  + '</div>')
    st.markdown(f'<div class="grid g{n}">{cells}</div>', unsafe_allow_html=True)


def pipeline(stages: list[tuple[str, int, str]]):
    """[(label, value, colour)] with a fill bar relative to the first stage."""
    top = max((v for _, v, _ in stages), default=0) or 1
    parts = []
    for i, (lab, v, c) in enumerate(stages):
        pct = min(int(v / top * 100), 100)
        parts.append(
            f'<div class="seg"><div class="n">{v}</div>'
            f'<div class="c">{esc(lab)}</div>'
            f'<div class="bar"><i style="width:{pct}%;background:{c}"></i></div></div>')
        if i < len(stages) - 1:
            parts.append('<div class="arrow">&rsaquo;</div>')
    st.markdown(f'<div class="pipe">{"".join(parts)}</div>', unsafe_allow_html=True)


def steps(labels: list[str], current: int):
    """current is a 1-based index; earlier steps render as done."""
    out = ""
    for i, lab in enumerate(labels, start=1):
        cls = "done" if i < current else ("on" if i == current else "")
        mark = "✓" if i < current else str(i)
        out += (f'<div class="s {cls}"><div class="k"><b>{mark}</b>'
                f'{esc(lab)}</div></div>')
    st.markdown(f'<div class="steps">{out}</div>', unsafe_allow_html=True)


def note(body: str, title: str = "", tone: str = "info"):
    c = TONES.get(tone, INFO)
    t = f'<div class="t">{esc(title)}</div>' if title else ""
    st.markdown(f'<div class="note" style="color:{c};border-color:{c}3d;'
                f'background:{c}0d">{t}<div class="b">{esc(body)}</div></div>',
                unsafe_allow_html=True)


def empty(icon: str, title: str, subtitle: str = ""):
    st.markdown(f'<div class="empty"><div class="ic">{icon}</div>'
                f'<div class="t">{esc(title)}</div>'
                + (f'<div class="s">{esc(subtitle)}</div>' if subtitle else "")
                + '</div>', unsafe_allow_html=True)


def file_tile(name: str, kind: str, meta: str = ""):
    is_pdf = str(kind).upper() == "PDF"
    col = DANGER if is_pdf else INFO
    st.markdown(f'<div class="file"><div class="h">'
                f'<div class="ic" style="background:{col}">'
                f'{"PDF" if is_pdf else "IMG"}</div>'
                f'<div style="min-width:0"><div class="nm">{esc(name)}</div>'
                f'<div class="mt">{esc(meta)}</div></div></div></div>',
                unsafe_allow_html=True)


def kv(rows: list[tuple[str, str]]):
    body = "".join(f'<div class="r"><span class="k">{esc(k)}</span>'
                   f'<span class="v">{esc(v)}</span></div>' for k, v in rows)
    st.markdown(f'<div class="kv">{body}</div>', unsafe_allow_html=True)


def nav_brand(name: str, sub: str):
    st.sidebar.markdown(
        f'<div class="navbrand"><div class="mark">GRN</div>'
        f'<div><div class="n">{esc(name)}</div>'
        f'<div class="s">{esc(sub)}</div></div></div>', unsafe_allow_html=True)


def nav_label(text: str):
    st.sidebar.markdown(f'<div class="navlabel">{esc(text)}</div>',
                        unsafe_allow_html=True)


def nav_footer(rows: list[tuple[str, str]]):
    body = "".join(f'<div class="row"><span>{esc(k)}</span><b>{v}</b></div>'
                   for k, v in rows)
    st.sidebar.markdown(f'<div class="navfoot">{body}</div>',
                        unsafe_allow_html=True)


def chart(fig, height=300, legend=False):
    """Apply the house chart style. bargap keeps bars from filling the plot
    when a chart only has two or three categories."""
    fig.update_layout(
        template="simple_white", height=height, bargap=0.45,
        margin=dict(t=8, b=8, l=8, r=8),
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif",
                  size=12, color=INK),
        showlegend=legend,
        legend=dict(orientation="h", y=1.13, x=0, title_text=""),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor=LINE_2, zeroline=False),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    return fig
