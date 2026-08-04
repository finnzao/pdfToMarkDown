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
"""
from __future__ import annotations

import base64
import os
import secrets
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .pipeline import processar
from .pipeline.prompts import catalogo_api, resolver

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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
