"""Orquestrador: PDF (bytes) -> Markdown estruturado + estatísticas.

Fluxo: extração de texto (PyMuPDF) -> detecção da capa -> parse da capa
-> segmentação por Id de rodapé -> limpeza -> classificação -> Markdown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import fitz  # PyMuPDF

from . import limpeza
from .capa import (MetaProcesso, casar_indice_com_rodapes, eh_capa,
                   eh_continuacao_capa, parsear_capa)
from .classificacao import (categoria_do_tipo_pje, classificar_conteudo, grupo)
from .markdown_gen import gerar_markdown
from .prompts import montar_documento
from .segmentacao import segmentar

# Menos que isso de conteúdo substantivo após a limpeza = página/peça de
# puro boilerplate (rodapés de assinatura, folhas Sinesp).
_MIN_CHARS_SUBSTANTIVOS = 30

_RE_NUMERO_CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


@dataclass
class Resultado:
    nome: str
    numero: str
    markdown: str
    total_paginas: int
    pecas_principais: int
    pecas_expediente: int
    paginas_descartadas: int
    chars_bruto: int
    chars_limpo: int
    avisos: list[str]
    perfil: dict | None = None  # prompt especializado aplicado (ou None)
    autos: str = ""  # markdown sem envelope/prompts (para montar lotes)


def extrair_paginas(doc: fitz.Document) -> list[str]:
    """Texto de todas as páginas, em ordem de leitura (sort=True ordena
    blocos por posição — essencial em layouts de duas colunas do PJe)."""
    return [page.get_text("text", sort=True) for page in doc]


def _detectar_capa(paginas: list[str]) -> int:
    """Retorna o número (1-based) da primeira página do corpo.
    A capa são as primeiras páginas com cara de índice do PJe e sem
    rodapé 'Num. - Pág.'. Processos volumosos têm índices longos
    (8+ páginas em autos de 500+ páginas) — teto generoso de 30."""
    if not paginas or not eh_capa(paginas[0]):
        return 1
    fim_capa = 1
    for i in range(1, min(30, len(paginas))):
        if eh_continuacao_capa(paginas[i]):
            fim_capa = i + 1
        else:
            break
    return fim_capa + 1


def processar(pdf_bytes: bytes, nome_arquivo: str,
              perfil: str | None = None) -> Resultado:
    avisos: list[str] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        paginas = extrair_paginas(doc)
    finally:
        doc.close()

    total = len(paginas)
    chars_bruto = sum(len(p) for p in paginas)

    # --- capa ---------------------------------------------------------------
    primeira_corpo = _detectar_capa(paginas)
    if primeira_corpo > 1:
        meta = parsear_capa(paginas[: primeira_corpo - 1])
    else:
        meta = MetaProcesso()
        avisos.append("Capa do PJe não detectada — metadados podem estar incompletos.")

    if not meta.numero:
        m = _RE_NUMERO_CNJ.search(nome_arquivo) or _RE_NUMERO_CNJ.search(
            "\n".join(paginas[:2])
        )
        if m:
            meta.numero = m.group(0)

    # --- segmentação por Id de rodapé ----------------------------------------
    pecas_seg = segmentar(paginas, primeira_corpo)
    ids_rodape = [p.id_pje for p in pecas_seg if p.id_pje]
    casar_indice_com_rodapes(meta, ids_rodape)
    indice_por_id = {e.id_pje: e for e in meta.indice}

    # --- limpeza + classificação ---------------------------------------------
    pecas_a: list[dict] = []
    pecas_b: list[dict] = []
    descartes: list[str] = []
    if primeira_corpo > 1:
        descartes.append(
            f"Págs. 1–{primeira_corpo - 1}: capa do PJe (metadados e índice "
            f"convertidos em frontmatter e Linha do Tempo)."
        )

    for peca in pecas_seg:
        paginas_limpas = [
            limpeza.limpar_pagina(t, primeira_da_peca=(i == 0))
            for i, t in enumerate(peca.paginas)
        ]
        texto = "\n\n".join(t for t in paginas_limpas if t).strip()
        pags = (f"Pág. {peca.pag_ini}" if peca.pag_ini == peca.pag_fim
                else f"Págs. {peca.pag_ini}–{peca.pag_fim}")

        if len(limpeza.texto_substantivo(texto)) < _MIN_CHARS_SUBSTANTIVOS:
            rotulo = f" (Id {peca.id_pje})" if peca.id_pje else ""
            descartes.append(
                f"{pags}: apenas rodapés de assinatura/boilerplate{rotulo}."
            )
            continue

        entrada = indice_por_id.get(peca.id_pje)
        categoria = categoria_do_tipo_pje(entrada.tipo if entrada else None)
        if categoria is None:
            categoria = classificar_conteudo(texto)
        if categoria == "DOC":
            if entrada and entrada.tipo:
                # Tipo oficial do PJe fora do mapa: a capa é a fonte de
                # verdade — usa o tipo como categoria, sem aviso.
                categoria = entrada.tipo.upper()
            else:
                # Sem entrada na capa e sem sinal no conteúdo: preserva
                # integral como peça genérica e avisa.
                categoria = "DOCUMENTO"
                avisos.append(
                    f"{pags}: tipo não identificado — transcrito "
                    f"integralmente como DOCUMENTO."
                )

        g = grupo(categoria) if categoria != "DOCUMENTO" else "A"
        item = {
            "categoria": categoria,
            "grupo": g,
            "titulo": (entrada.titulo if entrada else ""),
            "data": (entrada.data if entrada else ""),
            "id_pje": peca.id_pje,
            "pag_ini": peca.pag_ini,
            "pag_fim": peca.pag_fim,
            "texto": texto,
        }
        (pecas_a if g == "A" else pecas_b).append(item)

    # --- verificação de cobertura ---------------------------------------------
    cobertas = set()
    if primeira_corpo > 1:
        cobertas.update(range(1, primeira_corpo))
    for p in pecas_seg:
        cobertas.update(range(p.pag_ini, p.pag_fim + 1))
    faltantes = sorted(set(range(1, total + 1)) - cobertas)
    if faltantes:
        avisos.append(f"Páginas fora de qualquer peça (verificar): {faltantes}")

    # peças do índice da capa sem página correspondente no corpo
    ids_corpo = {p.id_pje for p in pecas_seg if p.id_pje}
    for e in meta.indice:
        if e.id_pje not in ids_corpo and not any(
            r.startswith(e.id_pje) for r in ids_corpo
        ):
            avisos.append(
                f"Documento do índice da capa sem conteúdo no corpo: "
                f"Id {e.id_pje} ({e.titulo})."
            )

    markdown = gerar_markdown(
        meta, nome_arquivo, total, pecas_a, pecas_b, descartes
    )
    # Envelopa com o bloco de proteção contra prompt injection (sempre)
    # e o prompt especializado do catálogo (quando escolhido).
    autos = markdown
    markdown, info_perfil = montar_documento(markdown, perfil)

    n_desc = sum(
        1 for d in descartes if d.startswith("Pág")
    )
    return Resultado(
        nome=nome_arquivo,
        numero=meta.numero,
        markdown=markdown,
        total_paginas=total,
        pecas_principais=len(pecas_a),
        pecas_expediente=len(pecas_b),
        paginas_descartadas=n_desc,
        chars_bruto=chars_bruto,
        chars_limpo=len(markdown),
        avisos=avisos,
        perfil=info_perfil,
        autos=autos,
    )
