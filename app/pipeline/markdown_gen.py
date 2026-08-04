"""Geração do Markdown final.

Estrutura: frontmatter YAML -> linha do tempo -> peças principais
(transcrição integral) -> peças de expediente (texto integral, seção
própria) -> inventário de descarte. A soma dos intervalos de páginas
declarados cobre 100% do PDF — perda silenciosa é bug, não opção.
"""
from __future__ import annotations

import datetime as _dt

from .capa import MetaProcesso


def _yaml_escape(v: str) -> str:
    return '"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"'


def gerar_markdown(
    meta: MetaProcesso,
    nome_arquivo: str,
    total_paginas: int,
    pecas_a: list[dict],
    pecas_b: list[dict],
    descartes: list[str],
) -> str:
    """Cada peça (dict): categoria, titulo, data, id_pje, pag_ini, pag_fim, texto."""
    md: list[str] = []

    # --- frontmatter --------------------------------------------------------
    md.append("---")
    if meta.numero:
        md.append(f"processo: {_yaml_escape(meta.numero)}")
    if meta.classe:
        md.append(f"classe: {_yaml_escape(meta.classe)}")
    if meta.orgao_julgador:
        md.append(f"orgao_julgador: {_yaml_escape(meta.orgao_julgador)}")
    if meta.assuntos:
        md.append(f"assuntos: {_yaml_escape(meta.assuntos)}")
    if meta.partes or meta.outros:
        md.append("partes:")
        for nome, papel in meta.partes:
            md.append(f"  - nome: {_yaml_escape(nome)}")
            md.append(f"    papel: {_yaml_escape(papel)}")
        if meta.outros:
            md.append("  outros:")
            for o in meta.outros:
                md.append(f"    - {_yaml_escape(o)}")
    if meta.distribuicao:
        md.append(f"distribuicao: {_yaml_escape(meta.distribuicao)}")
    md.append(f"segredo_justica: {'true' if meta.segredo_justica else 'false'}")
    if meta.valor_causa:
        md.append(f"valor_causa: {_yaml_escape(meta.valor_causa)}")
    md.append(f"total_paginas_pdf: {total_paginas}")
    md.append(f"pecas_transcritas: {len(pecas_a)}")
    md.append(f"gerado_em: \"{_dt.date.today().isoformat()}\"")
    md.append(f"fonte: {_yaml_escape(nome_arquivo)}")
    md.append("---")
    md.append("")

    titulo_doc = meta.numero or nome_arquivo
    md.append(f"# Processo {titulo_doc}")
    md.append("")

    # --- linha do tempo -----------------------------------------------------
    todas = sorted(pecas_a + pecas_b, key=lambda p: p["pag_ini"])
    if todas:
        md.append("## Linha do Tempo")
        md.append("")
        md.append("| Data | Peça | Id (PJe) | Págs. PDF |")
        md.append("|------|------|----------|-----------|")
        for p in todas:
            pags = (str(p["pag_ini"]) if p["pag_ini"] == p["pag_fim"]
                    else f"{p['pag_ini']}–{p['pag_fim']}")
            titulo = p["titulo"] or p["categoria"]
            md.append(f"| {p['data'] or '—'} | {titulo} | {p['id_pje'] or '—'} | {pags} |")
        md.append("")

    md.append("---")
    md.append("")

    # --- peças principais (Grupo A): transcrição integral --------------------
    for n, p in enumerate((x for x in todas if x["grupo"] == "A"), start=1):
        md.append(_cabecalho_peca(n, p))
        md.append("")
        md.append(p["texto"])
        md.append("")
        md.append("---")
        md.append("")

    # --- peças de expediente (Grupo B): texto integral em seção própria ------
    grupo_b = [p for p in todas if p["grupo"] == "B"]
    if grupo_b:
        md.append("## Peças de Expediente")
        md.append("")
        for p in grupo_b:
            pags = (f"pág. {p['pag_ini']}" if p["pag_ini"] == p["pag_fim"]
                    else f"págs. {p['pag_ini']}–{p['pag_fim']}")
            ident = f"Id {p['id_pje']} · {pags}" if p["id_pje"] else pags
            titulo = p["titulo"] or p["categoria"]
            data = f" — {p['data']}" if p["data"] else ""
            md.append(f"### {titulo}{data} `[{ident}]`")
            md.append("")
            md.append(p["texto"])
            md.append("")
        md.append("---")
        md.append("")

    # --- inventário ----------------------------------------------------------
    if descartes:
        md.append("## Inventário de Descarte")
        md.append("")
        for d in descartes:
            md.append(f"- {d}")
        md.append("")

    out = "\n".join(md)
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out


def _cabecalho_peca(n: int, p: dict) -> str:
    pags = (f"pág. {p['pag_ini']}" if p["pag_ini"] == p["pag_fim"]
            else f"págs. {p['pag_ini']}–{p['pag_fim']}")
    ident = f"Id {p['id_pje']} · {pags}" if p["id_pje"] else pags
    titulo = p["titulo"] or p["categoria"]
    # evita "DESPACHO — Despacho": título igual à categoria não repete
    if titulo.strip().upper() != p["categoria"]:
        cab = f"## {n}. {p['categoria']} — {titulo}"
    else:
        cab = f"## {n}. {p['categoria']}"
    if p["data"]:
        cab += f" — {p['data']}"
    return f"{cab} `[{ident}]`"
