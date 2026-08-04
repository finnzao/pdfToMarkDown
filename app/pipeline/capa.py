"""Parse da capa do PJe — a fonte de verdade do processo.

A capa traz os metadados (número, classe, órgão, partes...) e a tabela
"Documentos" (Id / Data da Assinatura / Documento / Tipo). Na extração
linear o Id frequentemente vem QUEBRADO em duas linhas:

    46439 17/09/2024 15:02 IP 55040/2024 - 1a Remessa    Petição Inicial
    7941 Final_30687237215050841

O Id completo é a concatenação (464397941) e corresponde exatamente ao
rodapé "Num. 464397941 - Pág. N" das páginas do corpo — a âncora que
permite segmentação exata das peças.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class EntradaIndice:
    id_pje: str          # id completo (pode ser reconstituído depois, via rodapés)
    data: str            # DD/MM/AAAA
    hora: str            # HH:MM
    titulo: str          # coluna "Documento" (+ "Tipo" colada, na extração linear)
    tipo: str = ""       # coluna "Tipo", quando separável


@dataclass
class MetaProcesso:
    numero: str = ""
    classe: str = ""
    orgao_julgador: str = ""
    assuntos: str = ""
    distribuicao: str = ""
    segredo_justica: bool = False
    justica_gratuita: bool = False
    valor_causa: str = ""
    partes: list[tuple[str, str]] = field(default_factory=list)      # (nome, papel)
    outros: list[str] = field(default_factory=list)
    indice: list[EntradaIndice] = field(default_factory=list)


_RE_LINHA_DOC = re.compile(
    r"^(\d{4,6})\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s+(.+)$"
)
_RE_COMPLEMENTO_ID = re.compile(r"^(\d{3,6})(?:\s+(\S.*))?$")
_RE_PARTE = re.compile(
    r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ\s.\-&']{2,80}?)\s*"
    r"\((AUTOR|AUTORA|INVESTIGADO|INVESTIGADA|R[ÉE]U|R[ÉE]"
    r"|REQUERENTE|REQUERIDO|EXEQUENTE|EXECUTADO|RECLAMANTE|RECLAMADO"
    r"|IMPETRANTE|IMPETRADO|V[ÍI]TIMA|INDICIADO|ACUSADO|APELANTE|APELADO"
    r"|TERCEIRO\s+INTERESSADO|FISCAL\s+DA\s+LEI|CUSTOS\s+LEGIS)\)"
)

# Tipos oficiais do PJe que aparecem colados no fim da coluna "Documento".
# Ordenados do mais longo para o mais curto para o sufixo mais específico
# vencer ("Certidão de publicação no DJEN" antes de "Certidão").
_TIPOS_PJE_CONHECIDOS = sorted(
    [
        "Petição Inicial", "Petição", "Manifestação PC para MP", "Manifestação",
        "Despacho", "Decisão", "Sentença", "Intimação",
        "Certidão de publicação no DJEN", "Certidão de publicação", "Certidão",
        "Ata da Audiência", "Ata de audiência", "Carta Precatória",
        "Outros documentos", "Documento de Comprovação", "Ofício", "Mandado",
        "Alvará", "Parecer", "Denúncia", "Laudo", "Ato Ordinatório", "Juntada",
        "Alegações Finais", "Resposta à Acusação",
    ],
    key=len,
    reverse=True,
)


def _deduplicar_cauda(texto: str) -> str:
    """A coluna 'Tipo' costuma repetir o começo da coluna 'Documento'
    ('Certidão de Devolução de Mandado  Certidão de Devolução de').
    Remove a cauda que repete o início (mesmo truncada)."""
    palavras = texto.split()
    for i in range(1, len(palavras)):
        cauda = palavras[i:]
        if cauda == palavras[: len(cauda)]:
            return " ".join(palavras[:i])
    return texto


def _separar_titulo_tipo(resto: str) -> tuple[str, str]:
    """Na extração linear, 'Documento' e 'Tipo' vêm colados. Detecta o tipo
    conhecido no sufixo. Trata também a duplicação ('Despacho Despacho')."""
    resto = re.sub(r"\s+", " ", resto).strip()
    # 1) duplicação Documento==Tipo ("Certidão de Devolução de Mandado
    #    Certidão de Devolução de Mandado") — resolve antes do sufixo,
    #    senão um tipo curto ("Mandado") captura o fim da duplicata.
    deduplicado = _deduplicar_cauda(resto)
    if deduplicado != resto:
        return deduplicado, deduplicado
    # 2) tipo conhecido colado no sufixo
    for tipo in _TIPOS_PJE_CONHECIDOS:
        if resto.lower().endswith(tipo.lower()) and len(resto) > len(tipo):
            titulo = resto[: len(resto) - len(tipo)].strip()
            return (titulo or tipo), tipo
        if resto.lower() == tipo.lower():
            return resto, tipo
    return resto, ""


def _grab(texto: str, padrao: str) -> str:
    m = re.search(padrao, texto, re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def eh_capa(texto_pagina: str) -> bool:
    """Uma página é capa quando exibe os sinais do cabeçalho do PJe e não
    tem rodapé 'Num. X - Pág. Y' (o corpo sempre tem)."""
    sinais = 0
    if re.search(r"PJe\s*[-–]\s*Processo Judicial", texto_pagina, re.I):
        sinais += 2
    if re.search(r"[ÓO]rg[ãa]o julgador:", texto_pagina, re.I):
        sinais += 1
    if re.search(r"Classe:", texto_pagina, re.I):
        sinais += 1
    if re.search(r"^Documentos$", texto_pagina, re.I | re.M):
        sinais += 1
    tem_rodape = re.search(r"Num[.\s]+\d{5,}\s*[-–]\s*P[áa]g", texto_pagina)
    return sinais >= 2 and not tem_rodape


def eh_continuacao_capa(texto_pagina: str) -> bool:
    """Página 2+ da capa: só linhas da tabela de documentos, sem rodapé."""
    if re.search(r"Num[.\s]+\d{5,}\s*[-–]\s*P[áa]g", texto_pagina):
        return False
    linhas = [l for l in texto_pagina.splitlines() if l.strip()]
    if not linhas:
        return False
    relevantes = sum(
        1 for l in linhas
        if _RE_LINHA_DOC.match(l.strip()) or _RE_COMPLEMENTO_ID.match(l.strip())
    )
    return relevantes / len(linhas) > 0.6


def parsear_capa(paginas_capa: list[str]) -> MetaProcesso:
    texto = "\n".join(paginas_capa)
    meta = MetaProcesso()

    meta.numero = _grab(texto, r"N\s*[úu]?\s*mero:\s*([\d.\-]+)")
    meta.classe = _grab(texto, r"Classe:\s*([^\n]+)")
    meta.orgao_julgador = _grab(texto, r"[ÓO]rg[ãa]o julgador:\s*([^\n]+)")
    meta.assuntos = _grab(texto, r"Assuntos?:\s*([^\n]+)")
    meta.distribuicao = _grab(texto, r"[ÚU]ltima distribui[çc][ãa]o\s*:?\s*([^\n]+)")
    meta.valor_causa = _grab(texto, r"Valor da causa:\s*R?\$?\s*([^\n]+)")
    meta.segredo_justica = bool(re.search(r"Segredo de justi[çc]a\?\s*SIM", texto, re.I))
    meta.justica_gratuita = bool(re.search(r"Justi[çc]a gratuita\?\s*SIM", texto, re.I))

    for m in _RE_PARTE.finditer(texto):
        nome = re.sub(r"\s+", " ", m.group(1)).strip()
        papel = re.sub(r"\s+", " ", m.group(2)).strip()
        if papel.upper() in ("TERCEIRO INTERESSADO", "FISCAL DA LEI", "CUSTOS LEGIS"):
            meta.outros.append(f"{nome} ({papel})")
        else:
            meta.partes.append((nome, papel))

    # --- tabela "Documentos": reconstituição dos Ids quebrados -------------
    # Só considera a partir da linha "Documentos" para não confundir com
    # outras áreas da capa.
    pos = re.search(r"^Documentos\s*$", texto, re.I | re.M)
    area = texto[pos.end():] if pos else texto
    linhas = area.splitlines()
    i = 0
    while i < len(linhas):
        linha = linhas[i].strip()
        m = _RE_LINHA_DOC.match(linha)
        if m:
            frag_id, data, hora, resto = m.groups()
            id_completo = frag_id
            # a linha seguinte pode conter o complemento do Id (e sobra de título)
            if i + 1 < len(linhas):
                m2 = _RE_COMPLEMENTO_ID.match(linhas[i + 1].strip())
                if m2 and not _RE_LINHA_DOC.match(linhas[i + 1].strip()):
                    id_completo = frag_id + m2.group(1)
                    i += 1  # consome a linha de complemento
            titulo, tipo = _separar_titulo_tipo(resto)
            meta.indice.append(EntradaIndice(id_completo, data, hora, titulo, tipo))
        i += 1
    return meta


def casar_indice_com_rodapes(meta: MetaProcesso, ids_rodape: list[str]) -> None:
    """Confirma/corrige os Ids do índice contra os Ids reais dos rodapés.
    Um Id de capa 'casa' com um rodapé quando é prefixo dele (Id quebrado
    cuja segunda parte não foi capturada) ou igual."""
    restantes = list(ids_rodape)
    for entrada in meta.indice:
        if entrada.id_pje in restantes:
            restantes.remove(entrada.id_pje)
            continue
        candidatos = [r for r in restantes if r.startswith(entrada.id_pje)]
        if len(candidatos) == 1:
            entrada.id_pje = candidatos[0]
            restantes.remove(candidatos[0])
