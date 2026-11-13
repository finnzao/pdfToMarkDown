"""PDF -> Markdown página a página (contrato da API /v1 do Fórum Hub).

Diferente do pipeline da interface web (organizado por peça do PJe), aqui
a saída segue a paginação do PDF: `## Página N` em cada página, na ordem,
sem resumir, corrigir ou reorganizar. Só sai o que é boilerplate mecânico
(cabeçalhos/rodapés repetidos, número de página, carimbo de assinatura,
URL de validação).
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime

import pymupdf as fitz

from .pipeline import limpeza
from .pipeline.ocr import (QUALIDADE_MINIMA, ocr_paginas, precisa_ocr,
                           qualidade)

MODOS_OCR = ("auto", "always", "never")
SEM_TEXTO = "_[sem texto reconhecível]_"


class ErroPDF(Exception):
    """PDF_PROTEGIDO ou PDF_CORROMPIDO."""

    def __init__(self, codigo: str):
        super().__init__(codigo)
        self.codigo = codigo


def abrir(caminho: str) -> fitz.Document:
    try:
        doc = fitz.open(caminho, filetype="pdf")
    except Exception:
        doc = None
    if doc is None:
        # lançar fora do except: a exceção do MuPDF encadeada seguraria o
        # arquivo aberto (no Windows, impede apagar o temporário)
        raise ErroPDF("PDF_CORROMPIDO")
    if doc.needs_pass:
        doc.close()
        raise ErroPDF("PDF_PROTEGIDO")
    if doc.page_count == 0:
        doc.close()
        raise ErroPDF("PDF_CORROMPIDO")
    return doc


def contar_paginas(caminho: str) -> int:
    with abrir(caminho) as doc:
        return doc.page_count


# --- ruído --------------------------------------------------------------
_ASSINATURA = re.compile(
    r"^\s*(?:Documento\s+)?assinad[oa]\s+(?:digital|eletronica)mente\b.*$",
    re.I | re.M)
_URL_FRASE = re.compile(  # "A autenticidade ... pode ser conferida em https://..."
    r"^[^\n]*(?:autenticidade|validar|valida[çc][ãa]o|verificador|conferid|"
    r"confer[êe]ncia)[^\n]*https?://[^\n]*$", re.I | re.M)
_URL_SOLTA = re.compile(  # linha só com URL de validação
    r"^\s*https?://\S*(?:valid|autentic|verific|confer|consulta|documento)\S*\s*$",
    re.I | re.M)
# padrões do PJe/Sinesp já usados pela interface, menos o que apaga
# qualquer URL (aqui só as de validação saem)
_RUIDO = [_ASSINATURA, _URL_FRASE, _URL_SOLTA] + [
    p for p in limpeza._LIXO if p.pattern != r"https?://\S+"]
_NUM_PAGINA = re.compile(
    r"\s*(?:P[áa]g(?:ina)?\.?\s*)?\d{1,4}(?:\s*(?:de|/)\s*\d{1,4})?\s*", re.I)
_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f­​﻿]")

# Primeira palavra (sem acento, sem artigo) de títulos reais de peças.
# Só linha inteira em maiúsculas com uma destas vira `###`: nada inventado.
_TITULOS = set("""
SENTENCA RELATORIO FUNDAMENTACAO FUNDAMENTOS DISPOSITIVO DECISAO DESPACHO
ACORDAO EMENTA VOTO CONCLUSAO CONCLUSOES PARECER CERTIDAO FATOS FATO DIREITO
PEDIDOS PEDIDO MERITO PRELIMINAR PRELIMINARES TERMO ATA AUDIENCIA OFICIO
MANDADO INTIMACAO CITACAO DENUNCIA PETICAO CONTESTACAO REPLICA RECURSO
APELACAO AGRAVO EMBARGOS SINTESE PROVAS PROVA TUTELA LIMINAR ALEGACOES RAZOES
CONTRARRAZOES QUESITOS LAUDO JULGAMENTO DOSIMETRIA REQUERIMENTOS
""".split())
_ARTIGOS = {"DO", "DA", "DOS", "DAS", "DE", "E"}


def _sem_ruido(t: str) -> str:
    t = _CONTROLE.sub("", t)
    for p in _RUIDO:
        t = p.sub("", t)
    return t


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _eh_titulo(linha: str) -> bool:
    if len(linha) > 80 or linha != linha.upper() or not re.search(r"[A-Z]", _sem_acento(linha)):
        return False
    # tira numeração "I -", "2.1", "III."
    s = re.sub(r"^(?:[IVXLC]+|\d+(?:\.\d+)*)\s*[-–.)]?\s+", "", _sem_acento(linha))
    palavras = re.findall(r"[A-Z]+", s)
    while palavras and palavras[0] in _ARTIGOS:
        palavras.pop(0)
    return bool(palavras) and palavras[0] in _TITULOS and len(palavras) <= 8


def _texto_lixo(t: str) -> bool:
    """Camada de texto quebrada (fonte sem mapa de caracteres etc.)."""
    s = "".join(t.split())
    if len(s) < 20:
        return False
    ruins = sum(1 for c in s if c == "�"
                or unicodedata.category(c) in ("Co", "Cc", "Cn"))
    return ruins / len(s) > 0.1 or qualidade(t) < 0.5


def _texto_vetorial(page: fitz.Page, texto: str) -> bool:
    """Pouco texto extraível, mas a página é cheia de traços: texto
    convertido em curvas (ou manuscrito vetorizado). Só o OCR lê."""
    return len(texto.strip()) < 200 and len(page.get_drawings()) >= 100


def _linha_ruim(s: str) -> bool:
    """Linha de OCR com cara de carimbo/assinatura lida como lixo."""
    alnum = sum(c.isalnum() for c in s)
    if alnum == 0 or (len(s) >= 3 and alnum / len(s) < 0.5):
        return True
    if len(s) <= 2 and not s.isdigit():  # letra solta: risco de carimbo
        return True
    return qualidade(s, min_tokens=3) < 0.5


def _filtrar_ocr(texto: str) -> tuple[str, int]:
    manter, descartadas = [], 0
    for linha in texto.split("\n"):
        if linha.strip() and _linha_ruim(linha.strip()):
            descartadas += 1
        else:
            manter.append(linha)
    return "\n".join(manter), descartadas


def _chave(t: str) -> str:
    return re.sub(r"\d+", "#", " ".join(t.split())).lower()


_MARGEM = 0.12  # faixa de cabeçalho/rodapé: 12% da altura em cima e embaixo


def _repetidos(paginas: list[list | None], alturas: list[float]) -> set[str]:
    """Blocos inteiros dentro da faixa de cabeçalho/rodapé que se repetem
    em >= 60% das páginas. Fora da faixa nada é removido: duas certidões
    iguais em páginas seguidas são conteúdo, não cabeçalho."""
    validas = [(p, h) for p, h in zip(paginas, alturas) if p]
    if len(validas) < 2:
        return set()
    cont: Counter = Counter()
    for blocos, h in validas:
        cont.update({_chave(b[4]) for b in blocos if b[4].strip() and (
            b[3] <= h * _MARGEM or b[1] >= h * (1 - _MARGEM))})
    minimo = max(2, math.ceil(0.6 * len(validas)))
    return {k for k, c in cont.items() if c >= minimo}


def ordenar_colunas(blocos: list, largura: float) -> list:
    """Ordem de leitura coluna por coluna: blocos que cabem numa metade da
    página formam colunas (esquerda inteira, depois direita); um bloco
    largo encerra o trecho de colunas."""
    meio, folga = largura / 2, 5
    saida, esq, dir_ = [], [], []

    def despejar():
        if len(esq) >= 2 and len(dir_) >= 2:  # duas colunas de verdade
            saida.extend(sorted(esq, key=lambda b: b[1]))
            saida.extend(sorted(dir_, key=lambda b: b[1]))
        else:
            saida.extend(sorted(esq + dir_, key=lambda b: (round(b[1] / 3), b[0])))
        esq.clear()
        dir_.clear()

    for b in sorted(blocos, key=lambda b: (round(b[1] / 3), b[0])):
        if b[2] <= meio + folga:
            esq.append(b)
        elif b[0] >= meio - folga:
            dir_.append(b)
        else:
            despejar()
            saida.append(b)
    despejar()
    return saida


def _markdown_bloco(texto: str) -> list[str]:
    t = limpeza._HIFEN_QUEBRA.sub(r"\1\2", texto)
    linhas = [" ".join(l.split()) for l in t.split("\n")]
    linhas = [l for l in linhas if l]
    out = []
    while linhas and _eh_titulo(linhas[0]):
        out.append("### " + linhas.pop(0))
    if linhas:
        out.append(" ".join(linhas))
    return out


def _tabelas(page: fitz.Page) -> list[tuple[fitz.Rect, str]]:
    try:
        achadas = page.find_tables().tables
    except Exception:
        return []
    return [(fitz.Rect(t.bbox), t.to_markdown(clean=False).strip())
            for t in achadas if t.row_count >= 2 and t.col_count >= 2]


def _dentro(b, r: fitz.Rect) -> bool:
    return fitz.Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) in r


def converter(caminho: str, modo_ocr: str = "auto") -> dict:
    """Retorna {paginas, paginasOcr, corpo, avisos}. `corpo` são as
    páginas com `## Página N`; o front-matter fica com quem chama
    (depende do nome do arquivo e da hora)."""
    blocos: list[list | None] = []
    tabelas: list[list] = []
    larguras: list[float] = []
    alturas: list[float] = []
    ocr_idx: list[int] = []
    carimbos: dict[int, list] = {}
    avisos: list[str] = []
    with abrir(caminho) as doc:
        n = doc.page_count
        for i, page in enumerate(doc):
            larguras.append(page.rect.width)
            alturas.append(page.rect.height)
            try:
                bs = [b[:5] for b in page.get_text("blocks", sort=True) if b[6] == 0]
                # decide pelo texto sem o carimbo do PJe: scans antigos
                # recebem por cima um rodapé de ~350 caracteres
                texto = _sem_ruido("\n".join(b[4] for b in bs))
                if modo_ocr == "always" or (modo_ocr == "auto" and (
                        precisa_ocr(page, texto) or _texto_lixo(texto)
                        or _texto_vetorial(page, texto))):
                    ocr_idx.append(i)
                    # blocos nativos que são só carimbo saem antes do OCR
                    carimbos[i] = [b[:4] for b in bs
                                   if not re.search(r"\w", _sem_ruido(b[4]))]
                    bs, tabs = [], []
                else:
                    tabs = _tabelas(page) if bs else []
            except Exception:
                bs, tabs = None, []
            blocos.append(bs)
            tabelas.append(tabs)

    if ocr_idx:
        try:
            # completo=True: a página inteira é reconhecida (inclusive
            # quando a camada de texto existe mas é lixo)
            res = ocr_paginas(caminho, ocr_idx, completo=True, blocos=True,
                              apagar=carimbos)
        except Exception:
            res = {}
        for i in ocr_idx:
            if i not in res:
                blocos[i] = None
                continue
            bs, _, q = res[i]
            limpos, total_desc = [], 0
            for b in bs:
                t, d = _filtrar_ocr(b[4])
                total_desc += d
                limpos.append((*b[:4], t))
            blocos[i] = limpos
            if total_desc:
                avisos.append(f"página {i + 1}: {total_desc} linha(s) de baixa "
                              "confiança do OCR descartada(s)")
            if q < QUALIDADE_MINIMA:
                avisos.append(f"página {i + 1}: OCR de baixa qualidade "
                              f"({q:.0%}), conferir com o original")

    if all(b is None for b in blocos):
        raise ErroPDF("PDF_CORROMPIDO")

    # uma única cópia do texto de assinatura vai para os avisos
    for bs in blocos:
        m = next((m for b in bs or [] if (m := _ASSINATURA.search(b[4]))), None)
        if m:
            avisos.insert(0, "Assinatura: " + " ".join(m.group(0).split())[:300])
            break

    repetidos = _repetidos(blocos, alturas)
    partes = []
    for i, bs in enumerate(blocos):
        if bs is None:
            partes.append(f"## Página {i + 1}\n\n_[falha ao ler a página]_")
            avisos.append(f"página {i + 1}: falha ao ler a página")
            continue
        tabs = tabelas[i]
        itens = []
        for b in bs:
            if _chave(b[4]) in repetidos or any(_dentro(b, r) for r, _ in tabs):
                continue
            t = _sem_ruido(b[4])
            if re.search(r"\w", t) and not _NUM_PAGINA.fullmatch(t):
                itens.append((*b[:4], t))
        itens += [(*r, md, "tabela") for r, md in tabs]
        md = []
        for b in ordenar_colunas(itens, larguras[i]):
            md += [b[4]] if len(b) == 6 else _markdown_bloco(b[4])
        conteudo = "\n\n".join(md).strip()
        if not conteudo:
            conteudo = SEM_TEXTO
            avisos.append(f"página {i + 1} sem texto reconhecível")
        partes.append(f"## Página {i + 1}\n\n{conteudo}")

    return {"paginas": n, "paginasOcr": [i + 1 for i in ocr_idx],
            "corpo": "\n\n".join(partes), "avisos": avisos}


def _yaml(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    simples = (re.fullmatch(r"[\w.\-+:/ ]+", v) and ": " not in v
               and v == v.strip() and not v.endswith(":")
               and not re.fullmatch(r"true|false|null|yes|no|on|off|~|"
                                    r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", v, re.I))
    return v if simples else json.dumps(v, ensure_ascii=False)


def montar_markdown(arquivo: str, processo: str | None, documento: str | None,
                    resultado: dict) -> str:
    meta = {
        "arquivo": arquivo,
        "processo": processo,
        "documento": documento,
        "paginas": resultado["paginas"],
        "ocr": bool(resultado["paginasOcr"]),
        "geradoEm": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    cabeca = "\n".join(f"{k}: {_yaml(v)}" for k, v in meta.items())
    return f"---\n{cabeca}\n---\n\n{resultado['corpo']}\n"
