#!/usr/bin/env python3
"""로컬 웹 UI — 목록에서 버튼으로 논문을 요약하고, 결과를 게시(push)한다.

요약 작업은 *내 PC에서* 실행됩니다(PDF 다운로드 + 로컬 Ollama/추출 요약). 끝나면
[게시] 버튼으로 GitHub에 push → GitHub Pages가 정적 사이트를 공개 URL로 서빙하므로
폰·다른 PC 등 **어디서나 결과를 볼 수 있습니다**.

실행:
    pip install -r requirements.txt
    python3 app.py            # http://localhost:8000 접속

Ollama 생성형 요약을 쓰려면 먼저 `ollama serve` 가 떠 있어야 합니다(기본 엔진).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from types import SimpleNamespace

from flask import Flask, jsonify, request, send_file

import build_site as B
import summarize as S
from sources.base import make_session

app = Flask(__name__)
logger = logging.getLogger("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _rows() -> list[dict]:
    return S._flatten(B.load_issues(B.OUTPUT_DIR))


def _summarized(row: dict) -> bool:
    return (S.SUMMARY_DIR / f"{S._safe_name(row['doi'], row)}.json").exists()


_PAGE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>요약 콘솔</title>
<style>
 body{font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;margin:0;background:#f6f7f9;color:#1f2328}
 header{background:#2f6f4f;color:#fff;padding:18px 20px}
 header h1{margin:0;font-size:1.2rem} header p{margin:4px 0 0;opacity:.9;font-size:.85rem}
 .wrap{max-width:920px;margin:0 auto;padding:16px 20px}
 .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;margin-bottom:14px}
 select,input{padding:7px 9px;border:1px solid #e5e7eb;border-radius:7px}
 .btn{background:#2f6f4f;color:#fff;border:none;border-radius:7px;padding:8px 14px;cursor:pointer;font-size:.9rem}
 .btn.sec{background:#fff;color:#2f6f4f;border:1px solid #2f6f4f}
 .btn:disabled{opacity:.5;cursor:default}
 .row{display:flex;gap:10px;align-items:center;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px;margin-bottom:8px}
 .row .meta{flex:1;min-width:0}
 .row .t{font-size:.95rem;margin:0 0 2px} .row .s{font-size:.78rem;color:#6b7280}
 .badge{font-size:.72rem;border-radius:6px;padding:2px 7px;background:#eef2f7;color:#6b7280;white-space:nowrap}
 .badge.done{background:#e7f1ec;color:#2f6f4f;font-weight:600}
 #log{font-size:.82rem;color:#374151;background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:8px 10px;margin-bottom:12px;min-height:1.2em}
 a.view{color:#2f6f4f}
</style></head><body>
<header><div class="wrap" style="padding:0"><h1>📝 논문 요약 콘솔 (로컬)</h1>
<p>버튼으로 요약 → [게시]하면 공개 URL(GitHub Pages)에 반영됩니다 · 요약은 내 PC에서 실행</p></div></header>
<div class="wrap">
 <div class="bar">
   <label>방식
     <select id="engine">
       <option value="ollama">생성형 (Ollama, 로컬 LLM)</option>
       <option value="extractive">추출 (무료·즉시)</option>
     </select></label>
   <label>모델 <input id="model" value="qwen2.5:7b" size="12"></label>
   <button class="btn sec" id="publish">변경사항 게시(push)</button>
   <a class="view" href="/preview" target="_blank">현재 사이트 미리보기 →</a>
 </div>
 <div id="log">준비됨.</div>
 <div id="list"></div>
</div>
<script>
 const log=document.getElementById("log");
 function setLog(m){log.textContent=m;}
 async function load(){
   const r=await fetch("/api/list"); const rows=await r.json();
   const list=document.getElementById("list"); list.innerHTML="";
   rows.forEach(row=>{
     const el=document.createElement("div"); el.className="row";
     el.innerHTML=`<div class="meta"><p class="t">${row.n}. ${escapeHtml(row.title)}</p>
       <p class="s">${escapeHtml(row.journal_name)} · ${escapeHtml(row.issue_label)} · ${escapeHtml(row.authors)}</p></div>
       <span class="badge ${row.summarized?'done':''}">${row.summarized?'요약됨':'미요약'}</span>
       <button class="btn sum" data-n="${row.n}">${row.summarized?'다시 요약':'요약'}</button>
       <button class="btn sec pdf" title="내 PDF 파일로 요약 (페이월 해외 논문 등)">📎 PDF로 요약</button>
       <input type="file" accept="application/pdf,.pdf" style="display:none">`;
     const sumBtn=el.querySelector("button.sum"), pdfBtn=el.querySelector("button.pdf"), file=el.querySelector("input[type=file]");
     sumBtn.onclick=()=>summarize(row.n, sumBtn);
     pdfBtn.onclick=()=>file.click();
     file.onchange=()=>{ if(file.files[0]) summarizePdf(row.n, file.files[0], pdfBtn); };
     list.appendChild(el);
   });
 }
 function escapeHtml(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
 async function summarize(n, btn){
   const engine=document.getElementById("engine").value, model=document.getElementById("model").value;
   btn.disabled=true; setLog(`#${n} 요약 중… (생성형은 1~3분 걸릴 수 있어요)`);
   try{
     const r=await fetch("/api/summarize",{method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({num:n,engine,model,force:true})});
     const d=await r.json();
     setLog((d.ok?"✅ ":"⚠️ ")+d.message);
   }catch(e){ setLog("⚠️ 오류: "+e); }
   btn.disabled=false; load();
 }
 async function summarizePdf(n, file, btn){
   const engine=document.getElementById("engine").value, model=document.getElementById("model").value;
   btn.disabled=true; setLog(`#${n} PDF로 요약 중… (${file.name}, 생성형은 1~3분 걸릴 수 있어요)`);
   try{
     const fd=new FormData();
     fd.append("num",n); fd.append("engine",engine); fd.append("model",model); fd.append("pdf",file);
     const r=await fetch("/api/summarize_pdf",{method:"POST",body:fd});
     const d=await r.json();
     setLog((d.ok?"✅ ":"⚠️ ")+d.message);
   }catch(e){ setLog("⚠️ 오류: "+e); }
   btn.disabled=false; load();
 }
 document.getElementById("publish").onclick=async()=>{
   setLog("게시 중…");
   const r=await fetch("/api/publish",{method:"POST"}); const d=await r.json();
   setLog((d.ok?"✅ ":"⚠️ ")+d.message);
 };
 load();
</script></body></html>"""


@app.get("/")
def index():
    return _PAGE


@app.get("/api/list")
def api_list():
    out = []
    for i, row in enumerate(_rows(), 1):
        authors = ", ".join(row["authors"][:2]) + ("…" if len(row["authors"]) > 2 else "")
        out.append({
            "n": i, "title": row["title"], "journal_name": row["journal_name"],
            "issue_label": row["issue_label"], "authors": authors,
            "summarized": _summarized(row),
        })
    return jsonify(out)


def _run_summary(num, engine, model, local_pdf=None):
    """Shared summary runner for both endpoints. Returns a (json, status) tuple."""
    rows = _rows()
    if not (isinstance(num, int) and 1 <= num <= len(rows)):
        return jsonify(ok=False, message=f"번호 범위(1~{len(rows)}) 밖"), 400
    row = rows[num - 1]

    model = model or S.DEFAULT_OLLAMA_MODEL
    if engine == "ollama" and not S.ollama_available(S.DEFAULT_OLLAMA_HOST, logger):
        return jsonify(ok=False, message="Ollama 서버가 꺼져 있어요. 터미널에서 'ollama serve' 실행 후 다시 시도하세요."), 400

    opts = SimpleNamespace(engine=engine, model=model,
                           ollama_host=S.DEFAULT_OLLAMA_HOST, sentences=S.DEFAULT_SENTENCES)
    problems: list[str] = []
    summary = S.summarize_row(make_session(), row, opts, logger, problems, local_pdf=local_pdf)
    if not summary:
        reason = problems[-1] if problems else "PDF 접근 불가 또는 추출 실패"
        return jsonify(ok=False, message=f"요약 실패 — {reason}"), 500
    S.save_summary(summary, row, logger)
    B.build_site(B.OUTPUT_DIR, B.SITE_DIR, logger)

    method = summary.get("method", "")
    nsec = len(summary.get("sections", []))
    kind = "생성형" if method.startswith("ollama") else "추출"
    src = "PDF 파일로 " if local_pdf else ""
    return jsonify(ok=True, message=f"'{row['title'][:30]}' {src}{kind} 요약 완료 ({nsec}개 장). [게시]로 공개하세요."), 200


@app.post("/api/summarize")
def api_summarize():
    data = request.get_json(force=True)
    try:
        num = int(data.get("num"))
    except (TypeError, ValueError):
        return jsonify(ok=False, message="잘못된 번호"), 400
    return _run_summary(num, data.get("engine", "ollama"), data.get("model"))


@app.post("/api/summarize_pdf")
def api_summarize_pdf():
    try:
        num = int(request.form.get("num"))
    except (TypeError, ValueError):
        return jsonify(ok=False, message="잘못된 번호"), 400
    up = request.files.get("pdf")
    if up is None or not up.filename:
        return jsonify(ok=False, message="PDF 파일이 첨부되지 않았어요."), 400

    # Save the upload to a temp file, summarise from it, then delete it.
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        up.save(tmp)
        return _run_summary(num, request.form.get("engine", "ollama"),
                            request.form.get("model"), local_pdf=tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@app.post("/api/publish")
def api_publish():
    try:
        subprocess.run(["git", "add", "output/summaries", "site"], check=True)
        st = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not st.stdout.strip():
            return jsonify(ok=True, message="게시할 변경사항이 없습니다.")
        subprocess.run(["git", "commit", "-m", "요약 추가/갱신"], check=True, capture_output=True)
        push = subprocess.run(["git", "push"], capture_output=True, text=True)
        if push.returncode != 0:
            return jsonify(ok=False, message="push 실패: " + (push.stderr.strip() or push.stdout.strip()))
        return jsonify(ok=True, message="게시 완료! 1~2분 뒤 공개 URL에 반영됩니다.")
    except subprocess.CalledProcessError as exc:
        return jsonify(ok=False, message="git 오류: " + str(exc))


@app.get("/preview")
def preview():
    index_html = B.SITE_DIR / "index.html"
    if not index_html.exists():
        B.build_site(B.OUTPUT_DIR, B.SITE_DIR, logger)
    return send_file(index_html)


if __name__ == "__main__":
    print("\n  ▶ 요약 콘솔: http://localhost:8000  (종료: Ctrl+C)\n")
    app.run(host="127.0.0.1", port=8000, debug=False)
