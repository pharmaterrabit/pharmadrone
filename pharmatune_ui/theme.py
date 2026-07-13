"""Approved PharmaTune enterprise design tokens and reusable visual primitives."""
from __future__ import annotations

import html
import streamlit as st

TOKENS = {
    "bg": "#070D18", "panel": "#0C1526", "raise": "#111D33",
    "ink": "#E9EEF8", "ink2": "#A9B6D1", "ink3": "#7787A6",
    "acc": "#4D8DFF", "cyan": "#3AC8E6", "violet": "#9180F4",
    "green": "#3DBE8B", "amber": "#E0A83E", "red": "#E36A6A",
}


def inject() -> None:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&display=swap');
    :root{--bg:#070D18;--panel:#0C1526;--raise:#111D33;--line:rgba(151,168,205,.13);--line2:rgba(151,168,205,.28);--ink:#E9EEF8;--ink2:#A9B6D1;--ink3:#7787A6;--acc:#4D8DFF;--cyan:#3AC8E6;--violet:#9180F4;--green:#3DBE8B;--amber:#E0A83E;--red:#E36A6A}
    html,body,[class*="css"],.stApp{font-family:'Instrument Sans',sans-serif;background:var(--bg);color:var(--ink)}
    .stApp{background:radial-gradient(circle at 75% -10%,rgba(77,141,255,.12),transparent 34%),var(--bg)}
    [data-testid="stHeader"]{background:rgba(7,13,24,.78);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
    [data-testid="stSidebar"]{background:#08111f;border-right:1px solid var(--line)}
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p{color:var(--ink2)}
    .block-container{max-width:1440px;padding:1.6rem 2.2rem 4rem}
    h1,h2,h3{letter-spacing:-.025em;color:var(--ink)} h1{font-size:2rem!important} h2{font-size:1.35rem!important}
    p,.stCaption{color:var(--ink2)}
    div[data-testid="stMetric"]{background:linear-gradient(145deg,rgba(17,29,51,.96),rgba(12,21,38,.96));border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:0 10px 28px rgba(2,6,16,.22)}
    div[data-testid="stMetricLabel"]{color:var(--ink3);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}
    div[data-testid="stMetricValue"]{font-family:'JetBrains Mono',monospace;color:var(--ink);font-size:1.55rem}
    div[data-testid="stDataFrame"],div[data-testid="stTable"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}
    .stButton>button,.stDownloadButton>button{border-radius:9px;border:1px solid var(--line2);background:var(--raise);color:var(--ink);font-weight:600}
    .stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--acc);color:white}
    .stTextInput input,.stTextArea textarea,.stSelectbox>div>div,.stMultiSelect>div>div{background:var(--panel);border-color:var(--line2);color:var(--ink)}
    div[data-testid="stExpander"]{background:rgba(12,21,38,.72);border:1px solid var(--line);border-radius:12px}
    .pt-brand{display:flex;align-items:center;gap:10px;margin:4px 0 18px}.pt-mark{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,var(--acc),var(--cyan));display:grid;place-items:center;color:white;font-weight:800}.pt-brand b{font-size:18px}.pt-brand small{display:block;color:var(--ink3);font-size:10px;letter-spacing:.11em;text-transform:uppercase}
    .pt-eyebrow{font:600 11px 'JetBrains Mono',monospace;color:var(--acc);text-transform:uppercase;letter-spacing:.12em;margin-bottom:6px}.pt-title{font-size:28px;font-weight:700;line-height:1.15;color:var(--ink);letter-spacing:-.03em}.pt-sub{color:var(--ink2);margin:7px 0 20px;max-width:850px}
    .pt-card{background:linear-gradient(145deg,rgba(17,29,51,.92),rgba(12,21,38,.92));border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:12px;box-shadow:0 10px 28px rgba(2,6,16,.18)}
    .pt-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:999px;font:600 10px 'JetBrains Mono',monospace;letter-spacing:.03em;border:1px solid currentColor;margin-right:5px}.green{color:var(--green);background:rgba(61,190,139,.08)}.blue{color:var(--acc);background:rgba(77,141,255,.08)}.violet{color:var(--violet);background:rgba(145,128,244,.08)}.amber{color:var(--amber);background:rgba(224,168,62,.08)}.red{color:var(--red);background:rgba(227,106,106,.08)}.muted{color:var(--ink3);background:rgba(119,135,166,.08)}
    .pt-rule{height:1px;background:var(--line);margin:14px 0}.pt-mono{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--ink3)}
    .pt-empty{text-align:center;padding:48px 24px;border:1px dashed var(--line2);border-radius:14px;background:rgba(12,21,38,.45)}
    .pt-evidence{min-height:180px}.pt-evidence h4{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.07em}.pt-evidence p{font-size:13px}
    #MainMenu,footer{visibility:hidden}
    @media(max-width:1280px){.block-container{padding:1.2rem 1rem 3rem}}
    </style>
    """, unsafe_allow_html=True)


def page_header(title: str, subtitle: str, eyebrow: str = "PharmaTune Intelligence") -> None:
    st.markdown(f'<div class="pt-eyebrow">{html.escape(eyebrow)}</div><div class="pt-title">{html.escape(title)}</div><div class="pt-sub">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def badge(text: str, colour: str = "blue") -> str:
    return f'<span class="pt-badge {colour}">{html.escape(str(text))}</span>'


def card(title: str, body: str, badges: list[tuple[str, str]] | None = None, meta: str = "") -> None:
    chips = "".join(badge(a, b) for a, b in (badges or []))
    st.markdown(f'<div class="pt-card"><div>{chips}</div><h3 style="margin:10px 0 6px;font-size:16px">{html.escape(title)}</h3><p style="margin:0">{html.escape(body)}</p><div class="pt-mono" style="margin-top:12px">{html.escape(meta)}</div></div>', unsafe_allow_html=True)


def empty(title: str, detail: str, label: str = "Coming soon") -> None:
    st.markdown(f'<div class="pt-empty">{badge(label,"muted")}<h3>{html.escape(title)}</h3><p>{html.escape(detail)}</p></div>', unsafe_allow_html=True)
