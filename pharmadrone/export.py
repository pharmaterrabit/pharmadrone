"""Write reports and datasets to /reports, plus a static searchable site."""
from __future__ import annotations
import json
import re
import html
import csv
from pathlib import Path

try:
    import markdown as md_lib
    _HAS_MD = True
except ImportError:
    _HAS_MD = False


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "report").lower()).strip("-")[:60]


def _md_to_html(md_text: str) -> str:
    if _HAS_MD:
        return md_lib.markdown(md_text, extensions=["tables", "fenced_code"])
    # tiny fallback: escape + preserve line breaks
    return "<pre>" + html.escape(md_text) + "</pre>"


def write_reports(opps: list[dict], reports_dir: Path) -> list[dict]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for i, opp in enumerate(opps):
        name = f"{i+1:03d}-{_slug(opp.get('company'))}-{_slug(opp.get('product'))}"
        md_text = opp.get("report_md", "")
        (reports_dir / f"{name}.md").write_text(md_text, encoding="utf-8")
        page = _wrap_html(opp, _md_to_html(md_text))
        (reports_dir / f"{name}.html").write_text(page, encoding="utf-8")
        index.append({
            "file": f"{name}.html",
            "company": opp.get("company"), "product": opp.get("product"),
            "region": opp.get("region"), "grade": opp.get("grade"),
            "score": opp.get("score"), "confidence": opp.get("confidence"),
            "report_type": opp.get("report_type"),
            "problem_signal": opp.get("problem_signal"),
            "red_flags": "; ".join(opp.get("red_flags", []) or []),
            "evidence_urls": [e.get("url") for e in opp.get("evidence", [])],
        })
    return index


def write_datasets(opps, rejected, reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    # opportunities.csv
    with open(reports_dir / "opportunities.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company", "product", "generic_name", "region", "stage",
                    "problem_signal", "grade", "score", "confidence",
                    "evidence_count", "report_type"])
        for o in opps:
            w.writerow([o.get("company"), o.get("product"), o.get("generic_name"),
                        o.get("region"), o.get("stage"), o.get("problem_signal"),
                        o.get("grade"), o.get("score"), o.get("confidence"),
                        len(o.get("evidence", [])), o.get("report_type")])
    # evidence.json
    ev = [{"company": o.get("company"), "product": o.get("product"),
           "evidence": o.get("evidence", [])} for o in opps]
    (reports_dir / "evidence.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    # rejected_leads.csv
    with open(reports_dir / "rejected_leads.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company", "product", "reason", "evidence_count"])
        for r in rejected:
            w.writerow([r.get("company"), r.get("product"),
                        r.get("reject_reason"), len(r.get("evidence", []))])


def write_static_site(index: list[dict], reports_dir: Path) -> None:
    """Searchable index.html for pharmadrone.com/case-studies."""
    rows = json.dumps(index, ensure_ascii=False)
    site = INDEX_TEMPLATE.replace("__DATA__", rows)
    (reports_dir / "index.html").write_text(site, encoding="utf-8")


def _wrap_html(opp: dict, body_html: str) -> str:
    title = html.escape(f"{opp.get('company','')} — {opp.get('product','')}")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;
margin:2rem auto;padding:0 1rem;line-height:1.55;color:#1a1a1a}}
h1{{border-bottom:2px solid #eee;padding-bottom:.4rem}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;margin:1rem 0}}
th,td{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;vertical-align:top}}
th{{background:#fafafa}} a{{color:#2563eb}}
.back{{display:inline-block;margin-bottom:1rem;color:#666}}
</style></head><body>
<a class="back" href="index.html">← All case studies</a>
{body_html}</body></html>"""


INDEX_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PharmaDrone — BD Opportunity Case Studies</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;
margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{margin-bottom:.2rem} .sub{color:#666;margin-bottom:1.2rem}
input,select{padding:.5rem;border:1px solid #ccc;border-radius:6px;font-size:.95rem}
table{border-collapse:collapse;width:100%;margin-top:1rem;font-size:.9rem}
th,td{border-bottom:1px solid #eee;padding:.55rem .5rem;text-align:left}
th{cursor:pointer;background:#fafafa;position:sticky;top:0}
.grade{font-weight:700;padding:.1rem .5rem;border-radius:4px;color:#fff}
.A{background:#16a34a}.B{background:#2563eb}.C{background:#d97706}.D{background:#999}
a{color:#2563eb;text-decoration:none} .flags{color:#b91c1c;font-size:.8rem}
.note{background:#fff7ed;border:1px solid #fed7aa;padding:.6rem .8rem;border-radius:8px;
font-size:.85rem;color:#9a3412;margin:1rem 0}
</style></head><body>
<h1>PharmaDrone — BD Opportunity Case Studies</h1>
<div class="sub">Global public-source scan · possible opportunity signals only</div>
<div class="note">Automated multilingual public-source scouting. Signals require
human validation before any commercial decision-making. No freedom-to-operate,
patent validity, or infringement claims are made.</div>
<div>
<input id="q" placeholder="Search company / product / signal…" style="width:340px">
<select id="region"><option value="">All regions</option></select>
<select id="grade"><option value="">All grades</option>
<option>A</option><option>B</option><option>C</option></select>
</div>
<table id="tbl"><thead><tr>
<th data-k="company">Company</th><th data-k="product">Product/Asset</th>
<th data-k="region">Region</th><th data-k="problem_signal">Opportunity</th>
<th data-k="grade">Grade</th><th data-k="score">Score</th>
<th data-k="confidence">Confidence</th><th>Report</th>
</tr></thead><tbody></tbody></table>
<script>
const DATA = __DATA__;
const tb = document.querySelector('#tbl tbody');
const regSel = document.getElementById('region');
[...new Set(DATA.map(d=>d.region).filter(Boolean))].sort().forEach(r=>{
  const o=document.createElement('option');o.textContent=r;regSel.appendChild(o);});
let sortK='score', asc=false;
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const reg=regSel.value, g=document.getElementById('grade').value;
  let rows=DATA.filter(d=>{
    const hay=(d.company+' '+d.product+' '+d.problem_signal).toLowerCase();
    return (!q||hay.includes(q))&&(!reg||d.region===reg)&&(!g||d.grade===g);});
  rows.sort((a,b)=>{const x=a[sortK]??'',y=b[sortK]??'';
    return (x>y?1:x<y?-1:0)*(asc?1:-1);});
  tb.innerHTML=rows.map(d=>`<tr>
    <td>${d.company||''}</td><td>${d.product||''}</td><td>${d.region||''}</td>
    <td>${d.problem_signal||''}${d.red_flags?`<div class="flags">⚑ ${d.red_flags}</div>`:''}</td>
    <td><span class="grade ${d.grade}">${d.grade||''}</span></td>
    <td>${d.score??''}</td><td>${d.confidence||''}</td>
    <td><a href="${d.file}">Open →</a></td></tr>`).join('');
}
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; asc = (sortK===k)?!asc:false; sortK=k; render();});
['q','region','grade'].forEach(id=>document.getElementById(id).oninput=render);
render();
</script></body></html>"""
