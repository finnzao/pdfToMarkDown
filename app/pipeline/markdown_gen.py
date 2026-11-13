"""Geração do Markdown final.

Estrutura: frontmatter YAML -> alertas -> índice completo da capa (mais
recente primeiro) -> peças em ordem cronológica única, texto integral ->
inventário de descarte. A soma dos intervalos de páginas declarados cobre
100% do PDF — perda silenciosa é bug, não opção.
"""
from __future__ import annotations

import datetime as _dt

from .capa import EntradaIndice, MetaProcesso
from .classificacao import classificador

_NOTA_OCR = ("> ⚠ **[peça reconhecida por OCR]** — páginas escaneadas; o texto "
             "pode conter erros de leitura. Em caso de dúvida, confira no PDF.")
_SEM_DATA = _dt.datetime.min
_VAZIO = "—"

Intervalo = tuple[int, int]


class GeradorMarkdown:
    """`pecas`: dicts com categoria, titulo, tipo, data, hora, id_pje,
    pag_ini, pag_fim, texto e ocr. `paginas_por_id`: intervalos de todo Id
    achado no corpo, inclusive os descartados por não terem texto. No
    índice, Natureza diz quem produziu a peça: juiz, parte, cartório, prova
    ou anexo."""

    def __init__(self, meta: MetaProcesso, nome_arquivo: str, total_paginas: int,
                 pecas: list[dict], paginas_por_id: dict[str, list[Intervalo]],
                 descartes: list[str], avisos: list[str], paginas_ocr: int = 0):
        self.meta = meta
        self.nome_arquivo = nome_arquivo
        self.total_paginas = total_paginas
        self.pecas = sorted(pecas, key=self._ordem_peca, reverse=True)
        self.paginas_por_id = paginas_por_id
        self.descartes = descartes
        self.avisos = avisos
        self.paginas_ocr = paginas_ocr

    def gerar(self) -> str:
        md = [*self._frontmatter(), "",
              f"# Processo {self.meta.numero or self.nome_arquivo}", "",
              *self._alertas(), *self._indice(), "---", "",
              *self._pecas(), *self._inventario()]
        out = "\n".join(md)
        while "\n\n\n" in out:
            out = out.replace("\n\n\n", "\n\n")
        return out

    @staticmethod
    def _quando(data: str, hora: str) -> _dt.datetime:
        try:
            return _dt.datetime.strptime(f"{data} {hora or '00:00'}", "%d/%m/%Y %H:%M")
        except ValueError:
            return _SEM_DATA

    @staticmethod
    def _numero(id_pje: str) -> int:
        return int(id_pje) if id_pje.isdigit() else 0

    def _ordem_peca(self, p: dict) -> tuple:
        return (self._quando(p["data"], p["hora"]), self._numero(p["id_pje"]), -p["pag_ini"])

    def _ordem_entrada(self, e: EntradaIndice) -> tuple:
        return (self._quando(e.data, e.hora), self._numero(e.id_pje))

    @staticmethod
    def _faixa(intervalos: list[Intervalo]) -> str:
        return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in intervalos)

    @staticmethod
    def _celula(v: str) -> str:
        return v.replace("|", "/").strip() or _VAZIO

    @staticmethod
    def _yaml(v: str) -> str:
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _frontmatter(self) -> list[str]:
        m = self.meta
        md = ["---"]
        for chave, valor in (("processo", m.numero), ("classe", m.classe),
                             ("orgao_julgador", m.orgao_julgador), ("assuntos", m.assuntos)):
            if valor:
                md.append(f"{chave}: {self._yaml(valor)}")
        if m.partes or m.outros:
            md.append("partes:")
            for nome, papel in m.partes:
                md.append(f"  - nome: {self._yaml(nome)}")
                md.append(f"    papel: {self._yaml(papel)}")
            if m.outros:
                md.append("  outros:")
                md.extend(f"    - {self._yaml(o)}" for o in m.outros)
        if m.distribuicao:
            md.append(f"distribuicao: {self._yaml(m.distribuicao)}")
        md.append(f"segredo_justica: {'true' if m.segredo_justica else 'false'}")
        md.append(f"justica_gratuita: {'true' if m.justica_gratuita else 'false'}")
        if m.valor_causa:
            md.append(f"valor_causa: {self._yaml(m.valor_causa)}")
        sigilosos = sum(1 for e in m.indice if e.sigiloso)
        if sigilosos:
            md.append(f"documentos_sigilosos: {sigilosos}")
        md += [f"total_paginas_pdf: {self.total_paginas}",
               f"documentos_no_indice: {len(m.indice)}",
               f"pecas_transcritas: {len(self.pecas)}",
               f"paginas_ocr: {self.paginas_ocr}",
               f'gerado_em: "{_dt.date.today().isoformat()}"',
               f"fonte: {self._yaml(self.nome_arquivo)}",
               "---"]
        return md

    def _alertas(self) -> list[str]:
        if not self.avisos:
            return []
        return ["## Alertas", "", *(f"- {a}" for a in self.avisos), ""]

    def _indice(self) -> list[str]:
        categorias = {p["id_pje"]: p["categoria"] for p in self.pecas if p["id_pje"]}
        linhas = []
        for e in sorted(self.meta.indice, key=self._ordem_entrada, reverse=True):
            intervalos = self.paginas_por_id.get(e.id_pje)
            if not intervalos:
                pags = "não localizadas"
            elif e.id_pje in categorias:
                pags = self._faixa(intervalos)
            else:
                pags = f"{self._faixa(intervalos)} (sem texto)"
            categoria = (categorias.get(e.id_pje)
                         or classificador.pelo_cadastro(e.tipo, e.titulo) or "")
            titulo = f"{e.titulo} [sigiloso]" if e.sigiloso else e.titulo
            linhas.append((e.id_pje, e.data, e.hora, titulo, e.tipo, categoria,
                           classificador.natureza(categoria), pags))
        no_indice = {e.id_pje for e in self.meta.indice}
        for p in self.pecas:
            if p["id_pje"] not in no_indice:
                linhas.append((p["id_pje"], p["data"], p["hora"], p["titulo"] or p["categoria"],
                               p["tipo"], p["categoria"], classificador.natureza(p["categoria"]),
                               self._faixa([(p["pag_ini"], p["pag_fim"])])))
        if not linhas:
            return []
        md = ["## Índice (mais recente primeiro)", "",
              "| Id | Data | Hora | Documento | Tipo | Categoria | Natureza | Págs. PDF |",
              "|----|------|------|-----------|------|-----------|----------|-----------|"]
        md += ["| " + " | ".join(self._celula(c) for c in linha) + " |" for linha in linhas]
        return [*md, ""]

    def _pecas(self) -> list[str]:
        if not self.pecas:
            return []
        md = ["## Peças (mais recente primeiro)", ""]
        for p in self.pecas:
            md += [self._cabecalho(p), ""]
            if p.get("ocr"):
                md += [_NOTA_OCR, ""]
            md += [p["texto"], ""]
        return [*md, "---", ""]

    def _cabecalho(self, p: dict) -> str:
        quando = f"{p['data']} {p['hora']}".strip() or _VAZIO
        pags = self._faixa([(p["pag_ini"], p["pag_fim"])])
        rotulo = "pág." if p["pag_ini"] == p["pag_fim"] else "págs."
        return (f"### {p['id_pje'] or _VAZIO} · {quando} · {p['titulo'] or p['categoria']} "
                f"[{p['tipo'] or _VAZIO}] · {rotulo} {pags}"
                f"{' · sigiloso' if p.get('sigiloso') else ''}")

    def _inventario(self) -> list[str]:
        if not self.descartes:
            return []
        return ["## Inventário de Descarte", "", *(f"- {d}" for d in self.descartes), ""]
