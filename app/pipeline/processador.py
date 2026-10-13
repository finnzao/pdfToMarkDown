"""Orquestrador: PDF (bytes) -> Markdown estruturado + estatísticas.

Fluxo: extração de texto (PyMuPDF) -> detecção da capa -> parse da capa
-> segmentação por Id de rodapé -> limpeza -> classificação -> Markdown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf as fitz

from . import limpeza
from .capa import (MetaProcesso, casar_indice_com_rodapes, eh_capa,
                   eh_continuacao_capa, parsear_capa)
from .classificacao import (categoria_do_tipo_pje, classificar_conteudo, grupo)
from .markdown_gen import gerar_markdown
from .ocr import Progresso, ocr_paginas, precisa_ocr
from .prompts import montar_documento
from .segmentacao import segmentar

# Menos que isso de conteúdo substantivo após a limpeza = página/peça de
# puro boilerplate (rodapés de assinatura, folhas Sinesp).
_MIN_CHARS_SUBSTANTIVOS = 30
# Abaixo disso, o OCR provavelmente leu lixo (ver ocr.qualidade)
_MIN_QUALIDADE_OCR = 0.7

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
    paginas_ocr: int = 0


def extrair_paginas(doc: fitz.Document) -> list[str]:
    """Texto de todas as páginas, em ordem de leitura (sort=True ordena
    blocos por posição — essencial em layouts de duas colunas do PJe)."""
    return [page.get_text("text", sort=True) for page in doc]


def extrair_com_ocr(pdf_bytes: bytes, progresso: Progresso | None = None
                    ) -> tuple[list[str], dict[int, tuple[str, float, float]]]:
    """Camada de texto de todas as páginas; as escaneadas (sem camada de
    texto, imagem grande) passam pelo OCR. Retorna (textos, info_ocr) com
    info_ocr = {índice: (texto, ângulo, qualidade)}."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        paginas = extrair_paginas(doc)
        indices = [i for i, t in enumerate(paginas) if precisa_ocr(doc[i], t)]
    finally:
        doc.close()
    if indices and progresso:
        progresso(0, len(indices))
    info = ocr_paginas(pdf_bytes, indices, progresso)
    for i, (texto, _, _) in info.items():
        paginas[i] = texto
    return paginas, info


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
              perfil: str | None = None,
              progresso: Progresso | None = None) -> Resultado:
    """`progresso(feitas, total)` é chamado durante o OCR (única etapa
    lenta) para a interface mostrar o andamento."""
    avisos: list[str] = []
    paginas, info_ocr = extrair_com_ocr(pdf_bytes, progresso)

    total = len(paginas)
    chars_bruto = sum(len(p) for p in paginas)

    if info_ocr:
        ruins = sorted(i + 1 for i, (_, _, q) in info_ocr.items()
                       if q < _MIN_QUALIDADE_OCR)
        if ruins:
            avisos.append(
                f"OCR com qualidade baixa nas págs. "
                f"{', '.join(map(str, ruins))} — o texto pode conter erros "
                f"de leitura; confira no PDF original."
            )

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
        ocr = any(i in info_ocr
                  for i in range(peca.pag_ini - 1, peca.pag_fim))

        if len(limpeza.texto_substantivo(texto)) < _MIN_CHARS_SUBSTANTIVOS:
            rotulo = f" (Id {peca.id_pje})" if peca.id_pje else ""
            if ocr:
                # Nunca rotular imagem como boilerplate: o OCR rodou e não
                # achou texto — pode ser foto, planta, página ilegível.
                motivo = "imagem sem texto legível (OCR não reconheceu texto)"
                avisos.append(f"{pags}: {motivo} — conferir no PDF.")
            elif not any(limpeza.texto_substantivo(t) for t in peca.paginas):
                motivo = "página sem texto nem imagem (em branco)"
            else:
                motivo = "apenas rodapés de assinatura/boilerplate"
            descartes.append(f"{pags}: {motivo}{rotulo}.")
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
            "ocr": ocr,
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
        meta, nome_arquivo, total, pecas_a, pecas_b, descartes,
        paginas_ocr=len(info_ocr),
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
        paginas_ocr=len(info_ocr),
    )
