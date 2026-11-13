"""API /v1 para o Fórum Hub: conversão de PDF em Markdown.

Contrato em /v1/openapi.json e no manual API.md. Configuração só por
variáveis de ambiente (ver API.md). Nenhum conteúdo de documento vai para
o log: só id (sha256), tamanho, páginas, tempo e tipo de erro.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import traceback
import urllib.request
import uuid
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, Field
from fastapi.exception_handlers import (http_exception_handler,
                                        request_validation_exception_handler)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from .conversor import (MODOS_OCR, ErroPDF, abrir, contar_paginas, converter,
                        montar_markdown)
from .pipeline import processar
from .pipeline.ocr import TESSDATA, _TEM_OCR2 as OCR_NEURAL
from .pipeline.prompts import montar_lotes

FORMATOS = ("paginas", "juridico")

VERSAO = "1.0.0"
MB = 1024 * 1024

log = logging.getLogger("forumhub")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False


# --- configuração (variáveis de ambiente) ---------------------------------
class RedePrivadaMiddleware:
    """Página pública (Vercel) chamando localhost: o Chrome exige, no preflight, a resposta
    Access-Control-Allow-Private-Network: true (Private Network Access)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "OPTIONS":
            return await self.app(scope, receive, send)
        pediu = any(k == b"access-control-request-private-network" and v.strip().lower() == b"true"
                    for k, v in scope["headers"])

        async def enviar(mensagem):
            if pediu and mensagem["type"] == "http.response.start":
                mensagem["headers"] = [*mensagem.get("headers", []),
                                       (b"access-control-allow-private-network", b"true")]
            await send(mensagem)

        return await self.app(scope, receive, enviar)


def _env(nome: str, padrao) -> str:
    return os.environ.get(nome) or str(padrao)


# API_CHAVES="forumhub:chave1,outra-app:chave2" -> {chave: app}
CHAVES: dict[str, str] = {}
for _par in _env("API_CHAVES", "").split(","):
    _app, _, _chave = _par.strip().rpartition(":")
    if _chave:
        CHAVES[_chave] = _app or "padrao"

# Padrão: os dois endereços do Fórum Hub (local e publicado), para o navegador chamar esta
# instância direto, mesmo com o Hub na Vercel.
CORS_ORIGENS = [o.strip() for o in _env(
    "API_CORS_ORIGENS", "http://localhost:3000,https://pje-services-web-frontend.vercel.app",
).split(",") if o.strip()]
# tudo fica numa subpasta própria: a limpeza nunca toca em outra coisa
BASE = Path(_env("API_DIR_TEMP", tempfile.gettempdir())) / "forumhub-api"
LIMITE_SYNC = int(float(_env("API_LIMITE_SYNC_MB", 25)) * MB)
LIMITE_SYNC_PAGINAS = int(_env("API_LIMITE_SYNC_PAGINAS", 200))
LIMITE_LOTE = int(float(_env("API_LIMITE_LOTE_MB", 2048)) * MB)
LIMITE_LOTE_ARQUIVOS = int(_env("API_LIMITE_LOTE_ARQUIVOS", 500))
CONCORRENCIA = max(1, int(_env("API_CONCORRENCIA", 2)))
RETENCAO = float(_env("API_RETENCAO_HORAS", 24)) * 3600
CACHE_MAX = int(float(_env("API_CACHE_MAX_MB", 1024)) * MB)
LIMITE_REQ_MIN = int(_env("API_LIMITE_REQ_MIN", 300))
OCR_DISPONIVEL = (Path(TESSDATA) / "por.traineddata").exists()


# --- erros ------------------------------------------------------------------
class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str,
                 detalhes: dict | None = None, headers: dict | None = None):
        super().__init__(codigo)
        self.status, self.codigo, self.mensagem = status, codigo, mensagem
        self.detalhes, self.headers = detalhes or {}, headers


MENSAGENS_PDF = {
    "PDF_PROTEGIDO": "O PDF está protegido por senha. Remova a senha e envie novamente.",
    "PDF_CORROMPIDO": "O PDF está corrompido ou não pôde ser aberto.",
}
MSG_SYNC = (f"O arquivo passa do limite da conversão direta ({LIMITE_SYNC // MB} MB "
            f"ou {LIMITE_SYNC_PAGINAS} páginas). Envie-o pelo lote: POST /v1/batches.")
MSG_LOTE = (f"O lote passa do limite ({LIMITE_LOTE_ARQUIVOS} arquivos ou "
            f"{LIMITE_LOTE // MB} MB no total). Divida em lotes menores.")


def _erro_pdf(e: ErroPDF) -> ErroAPI:
    return ErroAPI(422, e.codigo, MENSAGENS_PDF[e.codigo])


def _json_erro(status: int, codigo: str, mensagem: str, detalhes: dict | None = None,
               headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"erro": {"codigo": codigo, "mensagem": mensagem,
                                  "detalhes": detalhes or {}}}, status, headers=headers)


# --- cache por hash, com teto de tamanho ------------------------------------
class Cache:
    """Resultado da conversão por sha256 + modo de OCR, em disco.

    Ao atingir `maximo` bytes, para de gravar: as conversões continuam
    funcionando, só não entram no cache, até a limpeza por retenção
    liberar espaço. Falha de disco também só desliga a gravação."""

    def __init__(self, pasta: Path, maximo: int):
        self.pasta, self.maximo = pasta, maximo
        self._trava = threading.Lock()
        self.cheio = False

    def _arq(self, chave: str) -> Path:
        return self.pasta / f"{chave}.json"

    def obter(self, chave: str) -> dict | None:
        if self.maximo <= 0:
            return None
        f = self._arq(chave)
        try:
            if time.time() - f.stat().st_mtime > RETENCAO:
                return None
            return json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def uso(self) -> int:
        # ponytail: soma os arquivos a cada gravação (O(n)); trocar por
        # contador em memória se o cache passar de dezenas de milhares
        total = 0
        for f in self.pasta.glob("*.json"):
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return total

    def guardar(self, chave: str, dados: dict) -> bool:
        if self.maximo <= 0:
            return False
        bruto = json.dumps(dados, ensure_ascii=False).encode()
        with self._trava:
            if self.uso() + len(bruto) > self.maximo:
                if not self.cheio:
                    log.warning("cache cheio (limite %d MB): novos resultados "
                                "não serão guardados", self.maximo // MB)
                self.cheio = True
                return False
            try:
                tmp = self.pasta / f"{chave}.{uuid.uuid4().hex}.tmp"
                tmp.write_bytes(bruto)
                os.replace(tmp, self._arq(chave))
            except OSError as e:
                log.warning("cache: falha ao gravar (%s), seguindo sem cache",
                            type(e).__name__)
                return False
            if self.cheio:
                log.info("cache voltou a gravar")
            self.cheio = False
            return True

    def limpar(self, agora: float) -> None:
        for f in self.pasta.glob("*"):
            try:
                if agora - f.stat().st_mtime > RETENCAO:
                    f.unlink()
            except OSError:
                pass


cache = Cache(BASE / "cache", CACHE_MAX)

# --- conversão com concorrência limitada -----------------------------------
_sem: asyncio.Semaphore | None = None
_aguardando = 0  # conversões esperando vaga (entra no "fila" do health)


def _juridico(pdf: Path) -> dict:
    """Pipeline do Extrator (capa, peças por Id, linha do tempo): o mesmo
    Markdown da interface web, mais `autos` (sem envelope) para montar lotes."""
    abrir(str(pdf)).close()  # protegido/corrompido viram ErroPDF, como no outro formato
    r = processar(pdf.read_bytes(), pdf.name)
    return {"paginas": r.total_paginas, "paginasOcr": [], "totalPaginasOcr": r.paginas_ocr,
            "avisos": r.avisos, "markdown": r.markdown, "autos": r.autos}


async def _converter(pdf: Path, sha: str, modo: str,
                     formato: str = "paginas") -> tuple[dict, bool]:
    """(resultado, veio_do_cache). Nunca rejeita por ocupado: espera vaga."""
    global _aguardando
    chave = f"{sha}_{modo}" if formato == "paginas" else f"{sha}_{formato}"
    if (r := await run_in_threadpool(cache.obter, chave)) is not None:
        return r, True
    _aguardando += 1
    try:
        await _sem.acquire()
    finally:
        _aguardando -= 1
    try:
        if formato == "juridico":
            r = await run_in_threadpool(_juridico, pdf)
        else:
            r = await run_in_threadpool(converter, str(pdf), modo)
    finally:
        _sem.release()
    await run_in_threadpool(cache.guardar, chave, r)
    return r, False


_RE_NOME = re.compile(r"^(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})(?:_(.+?))?\.pdf$", re.I)


def _meta_nome(nome: str, processo: str | None, documento: str | None):
    """NUMERO_CNJ.pdf ou NUMERO_CNJ_tipo-documento.pdf; campos explícitos ganham."""
    m = _RE_NOME.match(PurePosixPath(nome).name)
    return (processo or (m[1] if m else None),
            documento or (m[2] if m and m[2] else None))


def _item(sha: str, arquivo: str, processo, documento, r: dict, t0: float,
          do_cache: bool) -> dict:
    total_ocr = r.get("totalPaginasOcr", len(r["paginasOcr"]))
    item = {
        "id": sha, "arquivo": arquivo, "processo": processo, "documento": documento,
        "paginas": r["paginas"], "ocr": total_ocr > 0,
        "totalPaginasOcr": total_ocr, "paginasOcr": r["paginasOcr"],
        "avisos": r["avisos"], "tempoMs": int((time.perf_counter() - t0) * 1000),
        "cache": do_cache,
    }
    if "autos" in r:
        item["autos"] = r["autos"]
    return item


def _markdown(formato: str, nome: str, processo, documento, r: dict) -> str:
    return r["markdown"] if formato == "juridico" else montar_markdown(nome, processo, documento, r)


def _resposta(request: Request, item: dict, markdown: str) -> Response:
    headers = {"X-Paginas": str(item["paginas"]),
               "X-Ocr": "true" if item["ocr"] else "false", "X-Id": item["id"]}
    if "text/markdown" in request.headers.get("accept", ""):
        return Response(markdown, media_type="text/markdown; charset=utf-8",
                        headers=headers)
    corpo = {k: item[k] for k in ("id", "arquivo", "processo", "documento",
                                  "paginas", "ocr", "totalPaginasOcr", "paginasOcr")}
    corpo |= {"markdown": markdown, "avisos": item["avisos"],
              "tempoMs": item["tempoMs"], "cache": item["cache"]}
    if "autos" in item:
        corpo["autos"] = item["autos"]
    return JSONResponse(corpo, headers=headers)


# --- recebimento de arquivos (streaming para disco) -------------------------
def _checar_tamanho(request: Request, limite: int, msg: str) -> None:
    cl = request.headers.get("content-length", "")
    if cl.isdigit() and int(cl) > limite:
        raise ErroAPI(413, "MUITO_GRANDE", msg, {"limiteBytes": limite})


async def _form(request: Request, max_files: int, msg_limite: str):
    try:
        return await request.form(max_files=max_files, max_fields=20)
    except StarletteHTTPException as e:
        if "Too many files" in str(e.detail):
            raise ErroAPI(413, "MUITO_GRANDE", msg_limite) from None
        raise ErroAPI(400, "ARQUIVO_INVALIDO", "Corpo multipart inválido.") from None


async def _pedacos_upload(f: UploadFile):
    while pedaco := await f.read(MB):
        yield pedaco


async def _gravar(pedacos, destino: Path, limite: int, msg: str) -> tuple[str, int]:
    """Grava em disco calculando o sha256; nunca segura o arquivo em memória."""
    h, total = hashlib.sha256(), 0
    with open(destino, "wb") as out:
        async for p in pedacos:
            total += len(p)
            if total > limite:
                raise ErroAPI(413, "MUITO_GRANDE", msg, {"limiteBytes": limite})
            h.update(p)
            out.write(p)
    return h.hexdigest(), total


def _parece_pdf(caminho: Path) -> bool:
    with open(caminho, "rb") as f:
        return b"%PDF-" in f.read(1024)


def _sha_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        while p := f.read(MB):
            h.update(p)
    return h.hexdigest()


def _texto(v) -> str | None:
    return v.strip() or None if isinstance(v, str) else None


def _modo(v) -> str:
    v = _texto(v) or "auto"
    if v not in MODOS_OCR:
        raise ErroAPI(400, "PARAMETRO_INVALIDO",
                      "O campo ocr deve ser auto, always ou never.", {"ocr": v})
    return v


def _formato(v) -> str:
    v = _texto(v) or "paginas"
    if v not in FORMATOS:
        raise ErroAPI(400, "PARAMETRO_INVALIDO",
                      "O campo formato deve ser paginas ou juridico.", {"formato": v})
    return v


def _url_callback(v) -> str | None:
    v = _texto(v)
    if v is None:
        return None
    u = urlsplit(v)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ErroAPI(400, "PARAMETRO_INVALIDO",
                      "callbackUrl deve ser uma URL http ou https.")
    return v


# --- autenticação e limite de requisições -----------------------------------
_bearer = HTTPBearer(auto_error=False, description="Chave da aplicação consumidora")
_janelas: dict[str, tuple[int, int]] = {}


async def autenticar(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    chave = ""
    if CHAVES:
        enviada = (cred.credentials if cred else "").encode()
        chave = next((k for k in CHAVES if hmac.compare_digest(k.encode(), enviada)), "")
        if not chave:
            raise ErroAPI(401, "NAO_AUTORIZADO", "Chave de acesso ausente ou inválida.",
                          headers={"WWW-Authenticate": "Bearer"})
    if LIMITE_REQ_MIN > 0:
        # ponytail: janela fixa por minuto, em memória (um processo só)
        minuto = int(time.time() // 60)
        inicio, n = _janelas.get(chave, (minuto, 0))
        n = n + 1 if inicio == minuto else 1
        _janelas[chave] = (minuto, n)
        if n > LIMITE_REQ_MIN:
            espera = 60 - int(time.time()) % 60
            raise ErroAPI(429, "LIMITE_EXCEDIDO",
                          "Muitas requisições em pouco tempo. Aguarde alguns "
                          "segundos e tente novamente.", {"retryAfter": espera},
                          headers={"Retry-After": str(espera)})
    return chave


# --- lotes -----------------------------------------------------------------
@dataclass
class Lote:
    id: str
    chave: str
    pasta: Path
    ocr: str
    callback: str | None
    itens: list[dict]
    formato: str = "paginas"
    criado: float = field(default_factory=time.time)
    status: str = "queued"
    terminado: float | None = None
    atual: str | None = None
    trava: asyncio.Lock = field(default_factory=asyncio.Lock)

    def publico(self) -> dict:
        return {
            "batchId": self.id, "status": self.status, "total": len(self.itens),
            "feitos": sum(i["status"] in ("done", "failed") for i in self.itens),
            "falhas": sum(i["status"] == "failed" for i in self.itens),
            "atual": self.atual,
            "itens": [{k: i[k] for k in ("arquivo", "status", "paginas", "ocr", "erro")}
                      for i in self.itens],
        }


# ponytail: estado dos lotes em memória; exige um único processo uvicorn
# (o OCR já usa todos os núcleos). Persistir em disco se precisar de réplicas.
_lotes: dict[str, Lote] = {}
_tarefas: set[asyncio.Task] = set()


def _md(rel: str) -> str:
    return str(PurePosixPath(rel).with_suffix(".md"))


def _caminho_seguro(nome: str) -> str | None:
    """Caminho relativo dentro do lote; nada de '..', absoluto ou oculto."""
    partes = [p for p in nome.replace("\\", "/").split("/") if p not in ("", ".")]
    if (not partes or partes[0] == "__MACOSX"
            or any(p == ".." or ":" in p or p.startswith(".") for p in partes)):
        return None
    return "/".join(partes)


def _extrair_zip(arquivo, destino: Path) -> list[str]:
    try:
        zf = zipfile.ZipFile(arquivo)
    except zipfile.BadZipFile:
        raise ErroAPI(400, "ARQUIVO_INVALIDO", "O zip enviado está corrompido.") from None
    with zf:
        infos, vistos = [], set()
        for info in zf.infolist():
            rel = None if info.is_dir() else _caminho_seguro(info.filename)
            if rel and rel.lower().endswith(".pdf") and rel.lower() not in vistos:
                vistos.add(rel.lower())
                infos.append((info, rel))
        if not infos:
            raise ErroAPI(400, "ARQUIVO_INVALIDO", "O zip enviado não contém PDFs.")
        if (len(infos) > LIMITE_LOTE_ARQUIVOS
                or sum(i.file_size for i, _ in infos) > LIMITE_LOTE):
            raise ErroAPI(413, "MUITO_GRANDE", MSG_LOTE)
        for info, rel in infos:
            alvo = destino / rel
            alvo.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(alvo, "wb") as dst:
                    shutil.copyfileobj(src, dst, MB)
            except RuntimeError:  # entrada criptografada
                raise ErroAPI(400, "ARQUIVO_INVALIDO",
                              "O zip está protegido por senha.") from None
        return [rel for _, rel in infos]


def _salvar_arquivos(arquivos: list[UploadFile], destino: Path) -> list[str]:
    if len(arquivos) > LIMITE_LOTE_ARQUIVOS:
        raise ErroAPI(413, "MUITO_GRANDE", MSG_LOTE)
    nomes, total = [], 0
    for n, f in enumerate(arquivos, 1):
        nome = _caminho_seguro(PurePosixPath((f.filename or "").replace("\\", "/")).name)
        nome = nome or f"arquivo{n}.pdf"
        base, k = PurePosixPath(nome), 2
        while nome.lower() in {x.lower() for x in nomes}:  # nomes repetidos
            nome, k = f"{base.stem} ({k}){base.suffix}", k + 1
        with open(destino / nome, "wb") as out:
            shutil.copyfileobj(f.file, out, MB)
            total += out.tell()
        if total > LIMITE_LOTE:
            raise ErroAPI(413, "MUITO_GRANDE", MSG_LOTE)
        nomes.append(nome)
    return nomes


def _disparar(coro) -> None:
    t = asyncio.create_task(coro)
    _tarefas.add(t)
    t.add_done_callback(_tarefas.discard)


async def _processar_item(lote: Lote, item: dict) -> None:
    t0 = time.perf_counter()
    lote.status = "running" if lote.status == "queued" else lote.status
    lote.atual = item["arquivo"]
    pdf = lote.pasta / "entrada" / item["arquivo"]
    try:
        if not await run_in_threadpool(_parece_pdf, pdf):
            raise ErroAPI(400, "ARQUIVO_INVALIDO", "O arquivo não é um PDF.")
        sha = await run_in_threadpool(_sha_arquivo, pdf)
        r, do_cache = await _converter(pdf, sha, lote.ocr, lote.formato)
        if lote.status == "cancelled":
            return
        nome = PurePosixPath(item["arquivo"]).name
        processo, documento = _meta_nome(nome, None, None)
        md = _markdown(lote.formato, nome, processo, documento, r)
        saida = lote.pasta / "saida" / _md(item["arquivo"])
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_text(md, "utf-8")
        item.update(status="done", paginas=r["paginas"], ocr=bool(r["paginasOcr"]),
                    detalhe=_item(sha, item["arquivo"], processo, documento, r,
                                  t0, do_cache))
        log.info("lote=%s item id=%s bytes=%d paginas=%d ms=%d cache=%s", lote.id,
                 sha[:16], pdf.stat().st_size, r["paginas"],
                 item["detalhe"]["tempoMs"], do_cache)
    except ErroPDF as e:
        item.update(status="failed", erro={"codigo": e.codigo,
                                           "mensagem": MENSAGENS_PDF[e.codigo]})
    except ErroAPI as e:
        item.update(status="failed", erro={"codigo": e.codigo, "mensagem": e.mensagem})
    except Exception as e:
        cid = uuid.uuid4().hex[:12]
        log.error("lote=%s erro interno id=%s %s\n%s", lote.id, cid, type(e).__name__,
                  "".join(traceback.format_tb(e.__traceback__)))
        item.update(status="failed", erro={
            "codigo": "ERRO_INTERNO", "correlacao": cid,
            "mensagem": f"Falha inesperada ao converter este arquivo (código {cid})."})
    finally:
        try:
            pdf.unlink(missing_ok=True)  # o PDF não fica guardado
        except OSError as e:  # sobra para a faxina por retenção
            log.warning("lote=%s não apagou o PDF: %s", lote.id, type(e).__name__)


async def _processar_lote(lote: Lote) -> None:
    fila = deque(lote.itens)

    async def trabalhador():
        while fila and lote.status != "cancelled":
            await _processar_item(lote, fila.popleft())

    try:
        await asyncio.gather(*(trabalhador() for _ in range(min(CONCORRENCIA, len(fila)))))
        if lote.status != "cancelled":
            ok = any(i["status"] == "done" for i in lote.itens)
            lote.status = "done" if ok else "failed"
    except Exception as e:
        log.error("lote=%s falhou: %s", lote.id, type(e).__name__)
        lote.status = "failed"
    finally:
        lote.atual = None
        lote.terminado = time.time()
    if lote.callback and lote.status != "cancelled":
        await _avisar(lote)


def _post(url: str, corpo: bytes, assinatura: str) -> int:
    req = urllib.request.Request(url, corpo, method="POST", headers={
        "Content-Type": "application/json", "X-Signature": assinatura})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


ESPERAS_CALLBACK = (2, 8)  # segundos antes da 2ª e da 3ª tentativa


async def _avisar(lote: Lote) -> None:
    """POST do mesmo JSON do GET /v1/batches/{id}, assinado com HMAC-SHA256
    (chave da aplicação) no header X-Signature. 3 tentativas."""
    corpo = json.dumps(lote.publico(), ensure_ascii=False).encode()
    assinatura = hmac.new(lote.chave.encode(), corpo, hashlib.sha256).hexdigest()
    for tentativa in range(3):
        if tentativa:
            await asyncio.sleep(ESPERAS_CALLBACK[tentativa - 1])
        try:
            status = await run_in_threadpool(_post, lote.callback, corpo, assinatura)
            log.info("lote=%s callback status=%d", lote.id, status)
            return
        except Exception as e:
            log.warning("lote=%s callback tentativa %d falhou: %s", lote.id,
                        tentativa + 1, type(e).__name__)


def _lote(lid: str, chave: str, ativo: bool = False) -> Lote:
    lote = _lotes.get(lid)
    if not lote or lote.chave != chave or (ativo and lote.status == "cancelled"):
        raise ErroAPI(404, "LOTE_NAO_ENCONTRADO", "Lote não encontrado ou já expirado.",
                      {"batchId": lid})
    return lote


def _manifesto(lote: Lote) -> dict:
    itens = []
    for i in lote.itens:
        d = i.get("detalhe") or {}
        itens.append({
            "arquivo": i["arquivo"],
            "markdown": _md(i["arquivo"]) if i["status"] == "done" else None,
            "status": i["status"], "id": d.get("id"), "paginas": i["paginas"],
            "ocr": i["ocr"], "totalPaginasOcr": d.get("totalPaginasOcr"),
            "paginasOcr": d.get("paginasOcr"), "avisos": d.get("avisos", []),
            "erro": i["erro"]})
    return {"batchId": lote.id, "status": lote.status, "total": len(lote.itens),
            "falhas": sum(i["status"] == "failed" for i in lote.itens),
            "itens": itens}


def _montar_resultado(lote: Lote) -> Path:
    destino = lote.pasta / "resultado.zip"
    if destino.exists():
        return destino
    tmp = lote.pasta / "resultado.zip.tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for i in lote.itens:
            if i["status"] == "done":
                z.write(lote.pasta / "saida" / _md(i["arquivo"]), _md(i["arquivo"]))
        z.writestr("manifest.json",
                   json.dumps(_manifesto(lote), ensure_ascii=False, indent=2))
    os.replace(tmp, destino)
    return destino


# --- retenção ---------------------------------------------------------------
async def limpar(agora: float | None = None) -> None:
    """Apaga cache e lotes com mais de API_RETENCAO_HORAS."""
    agora = agora or time.time()
    expirados = [l for l in _lotes.values()
                 if l.terminado and agora - l.terminado > RETENCAO]
    for l in expirados:
        del _lotes[l.id]
    vivos = set(_lotes)

    def disco():
        cache.limpar(agora)
        for l in expirados:
            shutil.rmtree(l.pasta, ignore_errors=True)
        for d in (BASE / "lotes").iterdir():  # pastas órfãs (lote cancelado etc.)
            try:
                if d.name not in vivos and agora - d.stat().st_mtime > RETENCAO:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
        for d in (BASE / "tmp").iterdir():  # sobras de uploads interrompidos
            try:
                if agora - d.stat().st_mtime > 3600:
                    shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink()
            except OSError:
                pass

    await run_in_threadpool(disco)


async def _faxina() -> None:
    while True:
        await asyncio.sleep(600)
        try:
            await limpar()
        except Exception as e:
            log.error("faxina falhou: %s", type(e).__name__)


@asynccontextmanager
async def ciclo(app: FastAPI):
    global _sem
    _sem = asyncio.Semaphore(CONCORRENCIA)
    # estado de lotes de uma execução anterior se perdeu com o processo
    for d in ("lotes", "tmp"):
        shutil.rmtree(BASE / d, ignore_errors=True)
    for d in ("lotes", "tmp", "cache"):
        (BASE / d).mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(BASE / "tmp")  # uploads multipart caem aqui
    if not CHAVES:
        log.warning("API_CHAVES vazia: /v1 sem autenticação (só para uso local)")
    tarefa = asyncio.create_task(_faxina())
    yield
    tarefa.cancel()


# --- documentação OpenAPI ---------------------------------------------------
_EX_ID = "9f2c4e0d1b7a6c58e3f1d2a4b6c8e0f1a3b5c7d9e1f2a4b6c8d0e2f4a6b8c0d2"
_EX_MD = ("---\narquivo: 8000123-45.2023.8.05.0001_sentenca.pdf\n"
          "processo: 8000123-45.2023.8.05.0001\ndocumento: sentenca\npaginas: 3\n"
          "ocr: false\ngeradoEm: 2026-10-30T14:03:00-03:00\n---\n\n## Página 1\n\n"
          "### SENTENÇA\n\nProcesso nº 8000123-45.2023.8.05.0001\n\n### RELATÓRIO\n\n"
          "Trata-se de ação de indenização proposta por JOÃO DA SILVA em face do "
          "BANCO EXEMPLO S.A., na qual o autor alega falha na prestação do serviço "
          "bancário e requer a reparação dos danos sofridos.\n\n## Página 2\n\n...")
_EX_CONVERT = {
    "id": _EX_ID, "arquivo": "8000123-45.2023.8.05.0001_sentenca.pdf",
    "processo": "8000123-45.2023.8.05.0001", "documento": "sentenca",
    "paginas": 3, "ocr": False, "totalPaginasOcr": 0, "paginasOcr": [],
    "markdown": _EX_MD,
    "avisos": ["Assinatura: Documento assinado eletronicamente por MARIA DE SOUZA, "
               "em 10/03/2024 14:00:00."],
    "tempoMs": 84, "cache": False}
_EX_LOTE = {
    "batchId": "3f6d2c1ab8e94e0f9d7c5b3a1e2f4d6c", "status": "running", "total": 3,
    "feitos": 2, "falhas": 1, "atual": "pasta/8000999-11.2022.8.05.0001.pdf",
    "itens": [
        {"arquivo": "8000123-45.2023.8.05.0001_sentenca.pdf", "status": "done",
         "paginas": 3, "ocr": False, "erro": None},
        {"arquivo": "pasta/8000555-00.2021.8.05.0001_decisao.pdf", "status": "failed",
         "paginas": None, "ocr": None,
         "erro": {"codigo": "PDF_PROTEGIDO", "mensagem": MENSAGENS_PDF["PDF_PROTEGIDO"]}},
        {"arquivo": "pasta/8000999-11.2022.8.05.0001.pdf", "status": "pending",
         "paginas": None, "ocr": None, "erro": None}]}


def _ex_erro(codigo: str, mensagem: str, detalhes: dict | None = None) -> dict:
    return {"content": {"application/json": {"example": {"erro": {
        "codigo": codigo, "mensagem": mensagem, "detalhes": detalhes or {}}}}}}


_ERROS_COMUNS = {
    401: {"description": "Chave ausente ou inválida", **_ex_erro(
        "NAO_AUTORIZADO", "Chave de acesso ausente ou inválida.")},
    429: {"description": "Limite de requisições por chave (header Retry-After)", **_ex_erro(
        "LIMITE_EXCEDIDO", "Muitas requisições em pouco tempo. Aguarde alguns "
        "segundos e tente novamente.", {"retryAfter": 12})},
    500: {"description": "Falha não prevista, com id de correlação", **_ex_erro(
        "ERRO_INTERNO", "Erro interno. Informe o código 4b1e9c0a7d2f ao suporte.",
        {"correlacao": "4b1e9c0a7d2f"})},
}
_ERRO_404 = {404: {"description": "Lote desconhecido ou expirado", **_ex_erro(
    "LOTE_NAO_ENCONTRADO", "Lote não encontrado ou já expirado.",
    {"batchId": "3f6d2c1ab8e94e0f9d7c5b3a1e2f4d6c"})}}
_BINARIO = {"type": "string", "format": "binary"}
_CAMPO_OCR = {"type": "string", "enum": list(MODOS_OCR), "default": "auto",
              "description": "auto: OCR só onde a camada de texto falta ou é lixo"}
_CAMPO_FORMATO = {"type": "string", "enum": list(FORMATOS), "default": "paginas",
                  "description": "paginas: texto página a página; juridico: autos do PJe "
                                 "segmentados por peça (capa, linha do tempo, Id), com o "
                                 "bloco de proteção e o campo autos para montar lotes"}


def _param(nome: str, schema: dict, descricao: str) -> dict:
    return {"name": nome, "in": "query", "required": False, "schema": schema,
            "description": descricao}


# --- rotas ------------------------------------------------------------------
publico = APIRouter(prefix="/v1", tags=["apoio"])
protegido = APIRouter(prefix="/v1", dependencies=[Depends(autenticar)])


@publico.get("/health", summary="Saúde do serviço (sem autenticação)", responses={
    200: {"content": {"application/json": {"example": {
        "ok": True, "versao": VERSAO, "fila": 3, "ocrDisponivel": True,
        "ocrNeural": True}}}}})
async def health() -> dict:
    fila = _aguardando + sum(1 for l in _lotes.values() if l.status in ("queued", "running")
                             for i in l.itens if i["status"] == "pending")
    return {"ok": True, "versao": VERSAO, "fila": fila, "ocrDisponivel": OCR_DISPONIVEL,
            "ocrNeural": OCR_NEURAL}


@protegido.post(
    "/convert", tags=["conversão"], summary="Converte um PDF (síncrono)",
    description=(f"Até {LIMITE_SYNC // MB} MB ou {LIMITE_SYNC_PAGINAS} páginas; acima "
                 "disso use POST /v1/batches. Com `Accept: text/markdown` a resposta é "
                 "só o Markdown, com os metadados nos headers X-Paginas, X-Ocr e X-Id."),
    openapi_extra={
        "parameters": [
            _param("processo", {"type": "string"}, "Só para application/pdf: número CNJ"),
            _param("documento", {"type": "string"}, "Só para application/pdf: tipo do documento"),
            _param("ocr", _CAMPO_OCR, "Só para application/pdf"),
            _param("formato", _CAMPO_FORMATO, "Só para application/pdf"),
            _param("arquivo", {"type": "string"}, "Só para application/pdf: nome do arquivo de origem"),
        ],
        "requestBody": {"required": True, "content": {
            "multipart/form-data": {"schema": {
                "type": "object", "required": ["file"], "properties": {
                    "file": _BINARIO,
                    "processo": {"type": "string", "example": "8000123-45.2023.8.05.0001"},
                    "documento": {"type": "string", "example": "sentenca"},
                    "ocr": _CAMPO_OCR, "formato": _CAMPO_FORMATO}}},
            "application/pdf": {"schema": _BINARIO}}}},
    responses={
        200: {"description": "Conversão concluída", "content": {
            "application/json": {"example": _EX_CONVERT},
            "text/markdown": {"schema": {"type": "string"}, "example": _EX_MD}},
            "headers": {h: {"schema": {"type": "string"}} for h in ("X-Paginas", "X-Ocr", "X-Id")}},
        400: {"description": "Não é PDF ou campo ausente", **_ex_erro(
            "ARQUIVO_INVALIDO", "O arquivo enviado não é um PDF.")},
        413: {"description": "Acima do limite síncrono", **_ex_erro(
            "MUITO_GRANDE", MSG_SYNC, {"limiteBytes": LIMITE_SYNC})},
        422: {"description": "PDF_PROTEGIDO ou PDF_CORROMPIDO", **_ex_erro(
            "PDF_PROTEGIDO", MENSAGENS_PDF["PDF_PROTEGIDO"])},
        **_ERROS_COMUNS})
async def convert(request: Request):
    t0 = time.perf_counter()
    _checar_tamanho(request, LIMITE_SYNC + MB, MSG_SYNC)  # folga do multipart
    ctype = request.headers.get("content-type", "")
    with tempfile.TemporaryDirectory(dir=BASE / "tmp", ignore_cleanup_errors=True) as d:
        destino = Path(d) / "entrada.pdf"
        if ctype.startswith("multipart/form-data"):
            form = await _form(request, 1, MSG_SYNC)
            try:
                f = form.get("file")
                if not isinstance(f, UploadFile):
                    raise ErroAPI(400, "ARQUIVO_INVALIDO", "Envie o PDF no campo file.")
                campos = {k: _texto(form.get(k))
                          for k in ("processo", "documento", "ocr", "formato")}
                nome = f.filename
                sha, tam = await _gravar(_pedacos_upload(f), destino, LIMITE_SYNC, MSG_SYNC)
            finally:
                await form.close()
        elif ctype.startswith("application/pdf"):
            q = request.query_params
            campos = {k: _texto(q.get(k)) for k in ("processo", "documento", "ocr", "formato")}
            nome = _texto(q.get("arquivo"))
            sha, tam = await _gravar(request.stream(), destino, LIMITE_SYNC, MSG_SYNC)
        else:
            raise ErroAPI(400, "ARQUIVO_INVALIDO", "Envie multipart/form-data com o "
                          "campo file ou o PDF puro com Content-Type: application/pdf.")
        if not tam or not _parece_pdf(destino):
            raise ErroAPI(400, "ARQUIVO_INVALIDO", "O arquivo enviado não é um PDF.")
        modo, formato = _modo(campos["ocr"]), _formato(campos["formato"])
        nome = PurePosixPath((nome or "documento.pdf").replace("\\", "/")).name
        processo, documento = _meta_nome(nome, campos["processo"], campos["documento"])
        try:
            paginas = await run_in_threadpool(contar_paginas, str(destino))
            if paginas > LIMITE_SYNC_PAGINAS:
                raise ErroAPI(413, "MUITO_GRANDE", MSG_SYNC,
                              {"paginas": paginas, "limitePaginas": LIMITE_SYNC_PAGINAS})
            r, do_cache = await _converter(destino, sha, modo, formato)
        except ErroPDF as e:
            log.info("convert id=%s bytes=%d erro=%s", sha[:16], tam, e.codigo)
            raise _erro_pdf(e) from None
    item = _item(sha, nome, processo, documento, r, t0, do_cache)
    log.info("convert id=%s bytes=%d paginas=%d ms=%d cache=%s", sha[:16], tam,
             r["paginas"], item["tempoMs"], do_cache)
    return _resposta(request, item, _markdown(formato, nome, processo, documento, r))


@protegido.post(
    "/batches", status_code=202, tags=["lotes"], summary="Cria um lote (assíncrono)",
    description=(f"Até {LIMITE_LOTE_ARQUIVOS} arquivos e {LIMITE_LOTE // MB} MB. Envie "
                 "vários campos files[] OU um campo zip (subpastas são mantidas no "
                 "resultado). Nunca é rejeitado por ocupação: entra na fila."),
    openapi_extra={"requestBody": {"required": True, "content": {
        "multipart/form-data": {"schema": {"type": "object", "properties": {
            "files[]": {"type": "array", "items": _BINARIO},
            "zip": _BINARIO,
            "ocr": _CAMPO_OCR,
            "formato": _CAMPO_FORMATO,
            "callbackUrl": {"type": "string", "format": "uri",
                            "example": "https://forumhub.exemplo/api/conversao/callback"}}}}}}},
    responses={
        202: {"description": "Lote na fila", "content": {"application/json": {"example": {
            "batchId": "3f6d2c1ab8e94e0f9d7c5b3a1e2f4d6c", "total": 37, "status": "queued"}}}},
        400: {"description": "Sem arquivos, zip inválido ou parâmetro inválido", **_ex_erro(
            "ARQUIVO_INVALIDO", "Envie os PDFs no campo files[] ou um zip no campo zip.")},
        413: {"description": "Acima do limite do lote", **_ex_erro("MUITO_GRANDE", MSG_LOTE)},
        **_ERROS_COMUNS})
async def criar_lote(request: Request, chave: str = Depends(autenticar)):
    _checar_tamanho(request, LIMITE_LOTE + 16 * MB, MSG_LOTE)
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise ErroAPI(400, "ARQUIVO_INVALIDO", "Envie multipart/form-data com files[] ou zip.")
    lid = uuid.uuid4().hex
    pasta = BASE / "lotes" / lid
    (pasta / "entrada").mkdir(parents=True)
    try:
        form = await _form(request, LIMITE_LOTE_ARQUIVOS + 1, MSG_LOTE)
        try:
            ocr, formato = _modo(form.get("ocr")), _formato(form.get("formato"))
            callback = _url_callback(form.get("callbackUrl"))
            z = form.get("zip")
            arquivos = [f for k in ("files[]", "files") for f in form.getlist(k)
                        if isinstance(f, UploadFile)]
            if isinstance(z, UploadFile):
                nomes = await run_in_threadpool(_extrair_zip, z.file, pasta / "entrada")
            elif arquivos:
                nomes = await run_in_threadpool(_salvar_arquivos, arquivos, pasta / "entrada")
            else:
                raise ErroAPI(400, "ARQUIVO_INVALIDO",
                              "Envie os PDFs no campo files[] ou um zip no campo zip.")
        finally:
            await form.close()
    except BaseException:
        shutil.rmtree(pasta, ignore_errors=True)
        raise
    lote = Lote(lid, chave, pasta, ocr, callback,
                [{"arquivo": n, "status": "pending", "paginas": None, "ocr": None,
                  "erro": None} for n in nomes], formato)
    _lotes[lid] = lote
    _disparar(_processar_lote(lote))
    log.info("lote=%s criado arquivos=%d", lid, len(nomes))
    return JSONResponse({"batchId": lid, "total": len(nomes), "status": "queued"}, 202)


@protegido.get("/batches/{batchId}", tags=["lotes"], summary="Progresso do lote",
               description="Feito para polling a cada 2 a 3 s. `feitos` conta os itens "
                           "já processados (done + failed); `falhas`, só os failed.",
               responses={200: {"content": {"application/json": {"example": _EX_LOTE}}},
                          **_ERRO_404, **_ERROS_COMUNS})
async def status_lote(batchId: str, chave: str = Depends(autenticar)) -> dict:
    return _lote(batchId, chave).publico()


@protegido.get(
    "/batches/{batchId}/result", tags=["lotes"], summary="Zip com os Markdowns",
    description="Disponível com status done (ou failed com itens prontos). Um .md por "
                "PDF, mesmo nome e mesmas subpastas, mais manifest.json.",
    response_class=FileResponse,
    responses={200: {"content": {"application/zip": {"schema": _BINARIO}}},
               409: {"description": "Lote ainda em processamento ou sem itens prontos",
                     **_ex_erro("RESULTADO_INDISPONIVEL", "O lote ainda está em processamento.")},
               **_ERRO_404, **_ERROS_COMUNS})
async def resultado_lote(batchId: str, chave: str = Depends(autenticar)):
    lote = _lote(batchId, chave, ativo=True)
    if lote.status not in ("done", "failed"):
        raise ErroAPI(409, "RESULTADO_INDISPONIVEL", "O lote ainda está em processamento.")
    if not any(i["status"] == "done" for i in lote.itens):
        raise ErroAPI(409, "RESULTADO_INDISPONIVEL", "Nenhum arquivo do lote foi convertido.")
    async with lote.trava:
        caminho = await run_in_threadpool(_montar_resultado, lote)
    return FileResponse(caminho, media_type="application/zip",
                        filename=f"lote-{batchId}.zip")


@protegido.get(
    "/batches/{batchId}/items/{arquivo:path}", tags=["lotes"],
    summary="Markdown de um item do lote",
    description="`arquivo` é o caminho do PDF no lote (com subpastas, URL-encoded). "
                "JSON como em /v1/convert, ou só o Markdown com Accept: text/markdown.",
    responses={200: {"content": {"application/json": {"example": _EX_CONVERT},
                                 "text/markdown": {"schema": {"type": "string"},
                                                   "example": _EX_MD}}},
               404: {"description": "Lote ou item inexistente", **_ex_erro(
                   "ITEM_NAO_ENCONTRADO", "Arquivo não encontrado neste lote.")},
               409: {"description": "Item ainda não convertido", **_ex_erro(
                   "ITEM_INDISPONIVEL", "Este arquivo ainda não foi convertido.")},
               422: {"description": "O item falhou (erro do próprio item)", **_ex_erro(
                   "PDF_CORROMPIDO", MENSAGENS_PDF["PDF_CORROMPIDO"])},
               **_ERROS_COMUNS})
async def item_lote(batchId: str, arquivo: str, request: Request,
                    chave: str = Depends(autenticar)):
    lote = _lote(batchId, chave, ativo=True)
    it = next((i for i in lote.itens if i["arquivo"] == arquivo), None)
    if it is None:
        raise ErroAPI(404, "ITEM_NAO_ENCONTRADO", "Arquivo não encontrado neste lote.",
                      {"arquivo": arquivo})
    if it["status"] == "failed":
        raise ErroAPI(422, it["erro"]["codigo"], it["erro"]["mensagem"])
    if it["status"] != "done":
        raise ErroAPI(409, "ITEM_INDISPONIVEL", "Este arquivo ainda não foi convertido.")
    md = (lote.pasta / "saida" / _md(arquivo)).read_text("utf-8")
    return _resposta(request, it["detalhe"], md)


class _ProcessoLote(BaseModel):
    autos: str = Field(min_length=1, description="Campo autos de um item em formato juridico")


class _PedidoLotesMd(BaseModel):
    processos: list[_ProcessoLote] = Field(min_length=1, max_length=LIMITE_LOTE_ARQUIVOS)
    limiteKb: int = Field(2048, ge=100, le=10240,
                          description="Tamanho máximo de cada MDemloteN.md")


@protegido.post(
    "/lotes-md", tags=["lotes"], summary="Monta os MDemloteN.md para análise por IA",
    description="Recebe o campo `autos` de itens convertidos com formato=juridico e "
                "devolve um zip com MDemlote1.md, MDemlote2.md… de até `limiteKb` cada, "
                "com o bloco de proteção uma vez por arquivo e um processo por envelope.",
    responses={200: {"content": {"application/zip": {"schema": _BINARIO}}},
               400: {"description": "Lista vazia ou limite fora de 100–10240 KB",
                     **_ex_erro("PARAMETRO_INVALIDO", "Requisição inválida.")},
               **_ERROS_COMUNS})
async def lotes_md(pedido: _PedidoLotesMd, chave: str = Depends(autenticar)) -> Response:
    docs = montar_lotes([(p.autos, None) for p in pedido.processos], pedido.limiteKb * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, doc in enumerate(docs, 1):
            z.writestr(f"MDemlote{i}.md", doc)
    log.info("lotes-md processos=%d arquivos=%d", len(pedido.processos), len(docs))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="MDemlote.zip"'})


@protegido.delete("/batches/{batchId}", status_code=204, tags=["lotes"],
                  summary="Cancela o lote e apaga os resultados (idempotente)",
                  responses={204: {"description": "Cancelado (ou já não existia)"},
                             **_ERROS_COMUNS})
async def cancelar_lote(batchId: str, chave: str = Depends(autenticar)):
    lote = _lotes.get(batchId)
    if lote and lote.chave == chave and lote.status != "cancelled":
        lote.status, lote.atual = "cancelled", None
        lote.terminado = time.time()
        await run_in_threadpool(shutil.rmtree, lote.pasta, True)
        log.info("lote=%s cancelado", batchId)
    return Response(status_code=204)


# --- instalação no app ------------------------------------------------------
def _eh_v1(request: Request) -> bool:
    return request.url.path.startswith("/v1/")


def instalar(app: FastAPI) -> None:
    app.include_router(publico)
    app.include_router(protegido)

    @app.exception_handler(ErroAPI)
    async def _tratar(request: Request, e: ErroAPI):
        return _json_erro(e.status, e.codigo, e.mensagem, e.detalhes, e.headers)

    @app.exception_handler(RequestValidationError)
    async def _validacao(request: Request, e: RequestValidationError):
        if not _eh_v1(request):
            return await request_validation_exception_handler(request, e)
        return _json_erro(400, "PARAMETRO_INVALIDO", "Requisição inválida.",
                          {"campos": [".".join(map(str, x["loc"])) for x in e.errors()]})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        if not _eh_v1(request):
            return await http_exception_handler(request, e)
        codigo = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}.get(e.status_code, "ERRO_HTTP")
        return _json_erro(e.status_code, codigo, "Rota ou método inexistente na API.")

    @app.middleware("http")
    async def _erro_interno(request: Request, call_next):
        if not _eh_v1(request):
            return await call_next(request)
        try:
            return await call_next(request)
        except Exception as e:
            cid = uuid.uuid4().hex[:12]
            rota = getattr(request.scope.get("route"), "path", "?")
            log.error("erro interno id=%s %s %s %s\n%s", cid, request.method, rota,
                      type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))
            return _json_erro(500, "ERRO_INTERNO",
                              f"Erro interno. Informe o código {cid} ao suporte.",
                              {"correlacao": cid})

    if CORS_ORIGENS:  # por último = mais externo: responde o preflight antes de tudo
        app.add_middleware(
            CORSMiddleware, allow_origins=CORS_ORIGENS,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Accept"],
            expose_headers=["X-Paginas", "X-Ocr", "X-Id", "Retry-After"], max_age=600)
        app.add_middleware(RedePrivadaMiddleware)
