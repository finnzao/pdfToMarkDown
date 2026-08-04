"""Remoção de ruído de PDFs do PJe/TJBA e Sinesp/PPe.

Todo padrão aqui foi observado em processos reais. A regra de ouro:
remover apenas boilerplate mecânico — nunca conteúdo jurídico.
"""
from __future__ import annotations

import re

# Rodapés de autenticação, marcadores de folha e avisos padronizados.
# Removidos de TODAS as páginas.
_LIXO = [
    re.compile(r"Este documento foi gerado pelo usu[áa]rio.*?(?=\n|$)", re.I),
    re.compile(r"N[úu]mero do documento:\s*\d+.*?(?=\n|$)", re.I),
    re.compile(r"https?://\S+", re.I),
    re.compile(r"Assinado eletronicamente(?: por)?:?.*?(?=\n|$)", re.I),
    re.compile(r"Num[.\s]+\d{5,}\s*[-–]\s*P[áa]g[.\s]+\d+", re.I),
    re.compile(r"C[óo]digo Verificador \(MAC\).*?(?=\n|$)", re.I),
    re.compile(r"Pg\.\s*\d+/\d+", re.I),
    # "Fls: 1 / Visto:" — inclusive com caracteres duplicados pela extração
    # ("FFllss:: 1" / "VViissttoo::")
    re.compile(r"F{1,2}l{1,4}s{1,2}\s*:{1,2}\s*\d*", re.I),
    re.compile(r"V{1,2}i{1,2}s{1,4}t{1,2}o{1,2}\s*:{1,2}", re.I),
    re.compile(r"Impresso por:.*?(?=\n|$)", re.I),
    re.compile(r"Data de Impress[ãa]o:.*?(?=\n|$)", re.I),
    re.compile(r"IP de Registro:.*?(?=\n|$)", re.I),
    re.compile(r"PPe\s*[-–]\s*Procedimentos Policiais.*?(?=\n|$)", re.I),
    re.compile(r"P[áa]gina\s+\d+\s+de\s+\d+", re.I),
    re.compile(r"Gerado por Sinesp Seguran[çc]a", re.I),
    re.compile(r"Documento assinado eletronicamente[\s\S]*?Bras[íi]lia\.?", re.I),
    re.compile(r"O sigilo deste documento[\s\S]*?administrativas\.?", re.I),
    re.compile(r"A autenticidade do documento[\s\S]*?verificar\.jsf", re.I),
    re.compile(r"A autenticidade do documento pode ser conferida.*?(?=\n|$)", re.I),
    re.compile(r"Informe o c[óo]digo verificador.*?(?=\n|$)", re.I),
    re.compile(r"Este documento ainda poder[áa].*?(?=\n|$)", re.I),
    re.compile(
        r"Secretaria Nacional de\s*\n?\s*(?:Minist[ée]rio da\s*\n?\s*)?"
        r"(?:Justi[çc]a e )?Seguran[çc]a P[úu]blica",
        re.I,
    ),
    re.compile(
        r"Minist[ée]rio da\s*\n?\s*Justi[çc]a e Seguran[çc]a P[úu]blica", re.I
    ),
    re.compile(r"TJBA\s*\n?\s*PJe\s*[-–]\s*Processo Judicial.*", re.I),
    re.compile(r"^\s*\d{10,}\s*$", re.M),
    re.compile(r"\(documento gerado e assinado automaticamente pelo PJe\)", re.I),
    # --- fragmentos da folha de assinatura Sinesp que a reordenação por
    # posição (sort=True) embaralha ou cola sem espaços ------------------
    re.compile(
        r"Este\s*documento\s*ainda\s*poder[áa]\s*receber\s*assinaturas\.?", re.I
    ),
    re.compile(
        r"Documento\s*assinado\s*eletronicamente[,\s]*(?:via\s*Sinesp[^\n]*)?", re.I
    ),
    re.compile(
        r"^[^\n]{0,60}Delegad[oa]\(?a?\)? de Pol[íi]cia, em \d{2}/\d{2}/\d{4}"
        r"[^\n]*Bras[íi]lia\.?\s*$",
        re.I | re.M,
    ),
    re.compile(r"^\s*Informe o\s*$", re.I | re.M),
    re.compile(r"^\s*Documento\s*$", re.M),
    re.compile(
        r"^\s*(?:Minist[ée]rio da\s*)?Justi[çc]a e Seguran[çc]a P[úu]blica\s*$",
        re.I | re.M,
    ),
    re.compile(r"^\s*Secretaria Nacional de\s*$", re.I | re.M),
    re.compile(r"^\s*Seguran[çc]a P[úu]blica\s*$", re.I | re.M),
]

# Cabeçalhos institucionais repetidos em toda página. Removidos apenas
# das páginas de CONTINUAÇÃO de uma peça — a primeira página mantém o
# cabeçalho, que faz parte da formalidade do documento.
_CABECALHOS_INSTITUCIONAIS = [
    re.compile(
        r"PODER JUDICI[ÁA]RIO\s*\n\s*TRIBUNAL DE JUSTI[ÇC]A DO ESTADO D[AEO]\s+\w[^\n]*\n?",
        re.I,
    ),
    re.compile(
        r"GOVERNO DO ESTADO D[AEO]\s+[^\n]+\n\s*POL[ÍI]CIA CIVIL\s*\n"
        r"(?:\s*DELEGACIA TERRITORIAL[^\n]*\n)?",
        re.I,
    ),
    re.compile(r"ESTADO DA BAHIA\s*\n\s*SECRETARIA DA SEGURAN[ÇC]A P[ÚU]BLICA[^\n]*\n?", re.I),
]

# Hifenização de quebra de linha: "investi-\ngado" -> "investigado"
_HIFEN_QUEBRA = re.compile(r"([a-záéíóúâêôãõç])-\n([a-záéíóúâêôãõç])", re.I)


def limpar_pagina(texto: str, primeira_da_peca: bool = False) -> str:
    """Limpa o texto de uma página. Cabeçalhos institucionais são
    preservados apenas na primeira página de cada peça."""
    t = texto
    if not primeira_da_peca:
        for p in _CABECALHOS_INSTITUCIONAIS:
            t = p.sub("", t)
    for p in _LIXO:
        t = p.sub("", t)
    t = _HIFEN_QUEBRA.sub(r"\1\2", t)
    # normalização de espaço
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def texto_substantivo(texto: str) -> str:
    """Retorna apenas os caracteres 'de conteúdo' (letras/dígitos) — usado
    para decidir se uma página é puro boilerplate."""
    return re.sub(r"[^0-9a-zA-Záéíóúâêôãõàçñü]", "", texto, flags=re.I)
