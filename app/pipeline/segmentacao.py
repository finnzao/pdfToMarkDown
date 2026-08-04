"""Segmentação do corpo do processo em peças.

Âncora primária: o rodapé "Num. XXXXXXXXX - Pág. Y" presente em cada
página do corpo — o número É o Id da tabela "Documentos" da capa.
Páginas consecutivas com o mesmo Id pertencem à mesma peça.

Página sem rodapé identificável herda a peça da página anterior
(continuação) — nunca se descarta página por falta de âncora.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_RE_RODAPE = re.compile(r"Num[.\s]+(\d{5,})\s*[-–]\s*P[áa]g[.\s]+(\d+)")


@dataclass
class Peca:
    id_pje: str                      # "" quando não identificável
    pag_ini: int                     # 1-based, no PDF
    pag_fim: int
    paginas: list[str] = field(default_factory=list)  # textos brutos


def id_da_pagina(texto: str) -> str | None:
    """Extrai o Id do rodapé. Usa a ÚLTIMA ocorrência da página — citações
    a outros documentos no corpo do texto aparecem antes do rodapé."""
    matches = list(_RE_RODAPE.finditer(texto))
    return matches[-1].group(1) if matches else None


def segmentar(paginas: list[str], primeira_pagina_corpo: int) -> list[Peca]:
    """`paginas` é a lista completa de textos (índice 0 = página 1 do PDF).
    `primeira_pagina_corpo` é 1-based (primeira página após a capa)."""
    pecas: list[Peca] = []
    atual: Peca | None = None

    for i in range(primeira_pagina_corpo - 1, len(paginas)):
        num_pdf = i + 1
        texto = paginas[i]
        id_pje = id_da_pagina(texto)

        if atual is not None and (id_pje is None or id_pje == atual.id_pje):
            # continuação da peça corrente (mesmo Id, ou página sem âncora)
            atual.pag_fim = num_pdf
            atual.paginas.append(texto)
            continue

        atual = Peca(id_pje=id_pje or "", pag_ini=num_pdf, pag_fim=num_pdf,
                     paginas=[texto])
        pecas.append(atual)

    return pecas
