"""API web do Extrator PDF Jurídico -> Markdown.

Execução local:

    uvicorn app.main:app --port 8077

Em produção, defina EXTRATOR_USUARIO e EXTRATOR_SENHA para exigir
autenticação HTTP Basic em todas as rotas (obrigatório para expor a
aplicação na web — os documentos processados são sigilosos).

Endpoints:
    GET  /               interface web
    GET  /api/perfis     catálogo de prompts especializados
    POST /api/converter  multipart com um PDF; retorna JSON com o Markdown
    POST /api/zip        lista [{nome, markdown}]; retorna um .zip
    POST /api/lotes      {limite_kb, processos}; .zip com MDemloteN.md
"""
from __future__ import annotations

import base64
import io
import os
import secrets
import traceback
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .pipeline import processar
from .pipeline.prompts import catalogo_api, montar_lotes, resolver

MAX_BYTES = 200 * 1024 * 1024  # 200 MB por arquivo

_RAIZ = Path(__file__).resolve().parent.parent
_STATIC = _RAIZ / "static"

app = FastAPI(title="Extrator PDF Jurídico → Markdown", docs_url=None,
              redoc_url=None)

# --- autenticação HTTP Basic (ativada por variáveis de ambiente) -----------
_USUARIO = os.environ.get("EXTRATOR_USUARIO", "")
_SENHA = os.environ.get("EXTRATOR_SENHA", "")


@app.middleware("http")
async def _autenticacao(request: Request, call_next):
    if _SENHA:  # sem senha configurada, roda aberto (uso local)
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.startswith("Basic "):
            try:
                usuario, _, senha = (
                    base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                )
                ok = (secrets.compare_digest(usuario, _USUARIO)
                      and secrets.compare_digest(senha, _SENHA))
            except Exception:
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Extrator"'},
            )
    return await call_next(request)


@app.get("/api/perfis")
async def perfis() -> JSONResponse:
    return JSONResponse(catalogo_api())


@app.post("/api/converter")
async def converter(arquivo: UploadFile = File(...),
                    perfil: str = Form("")) -> JSONResponse:
    if not (arquivo.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Envie um arquivo .pdf")
    if perfil and resolver(perfil) is None:
        raise HTTPException(400, "Prompt de análise desconhecido")
    dados = await arquivo.read()
    if len(dados) > MAX_BYTES:
        raise HTTPException(413, "Arquivo maior que 200 MB")
    if not dados.startswith(b"%PDF"):
        raise HTTPException(400, "Conteúdo não é um PDF válido")

    try:
        # processamento é CPU-bound: roda no threadpool para não bloquear
        # o event loop enquanto outros usuários usam a aplicação
        r = await run_in_threadpool(
            processar, dados, arquivo.filename, perfil or None
        )
    except Exception:
        traceback.print_exc()
        raise HTTPException(422, "Falha ao processar o PDF — arquivo "
                                 "corrompido ou protegido?")

    return JSONResponse({
        "nome": r.nome,
        "numero": r.numero,
        "markdown": r.markdown,
        "autos": r.autos,
        "stats": {
            "paginas": r.total_paginas,
            "pecas_principais": r.pecas_principais,
            "pecas_expediente": r.pecas_expediente,
            "paginas_descartadas": r.paginas_descartadas,
            "chars_bruto": r.chars_bruto,
            "chars_limpo": r.chars_limpo,
        },
        "avisos": r.avisos,
        "perfil": r.perfil,  # {area, categoria, prompt, ...} ou null
    })


class _Arquivo(BaseModel):
    nome: str
    markdown: str


@app.post("/api/zip")
async def zipar(arquivos: list[_Arquivo]) -> Response:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in arquivos:
            z.writestr(Path(a.nome).name, a.markdown)
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="extrator.zip"'},
    )


class _Processo(BaseModel):
    autos: str
    perfil: str = ""


class _PedidoLotes(BaseModel):
    limite_kb: int = Field(2048, ge=100, le=10240)
    processos: list[_Processo]


@app.post("/api/lotes")
async def lotes(pedido: _PedidoLotes) -> Response:
    """Junta os processos em MDemlote1.md, MDemlote2.md… de até limite_kb."""
    docs = montar_lotes([(p.autos, p.perfil or None) for p in pedido.processos],
                        pedido.limite_kb * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, doc in enumerate(docs, 1):
            z.writestr(f"MDemlote{i}.md", doc)
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="lotes.zip"'},
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html",
                        headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
