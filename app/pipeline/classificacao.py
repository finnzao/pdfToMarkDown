"""Classificação de peças processuais.

Duas vias, em ordem de confiança:
1. Tipo oficial vindo da capa do PJe (mapeado por MAPA_TIPO_PJE);
2. Classificador por scoring ponderado sobre o conteúdo (fallback).

O scoring evita os falsos positivos do matching por substring simples:
uma petição que cita "apelação" numa ementa não pode virar RECURSO.
Sinais muito específicos (cabeçalho literal, expressão decisória) pesam
10; palavras ambíguas pesam 1. Sinal encontrado nos primeiros 400
caracteres ganha bônus (dobra o peso) — cabeçalho vale mais que citação.
"""
from __future__ import annotations

import re
import unicodedata

# --- Grupos de tratamento -------------------------------------------------
# A: transcrição integral. B: expediente (texto integral, mas em seção
# separada). C: descarte registrado no inventário.
GRUPO_A = {
    "AUTUAÇÃO", "PORTARIA", "DENÚNCIA", "SENTENÇA", "PRONÚNCIA", "ALEGAÇÕES",
    "RESPOSTA", "RECURSO", "BOLETIM DE OCORRÊNCIA", "DECLARAÇÃO",
    "INTERROGATÓRIO", "RELATÓRIO", "MEDIDA PROTETIVA", "AVALIAÇÃO DE RISCO",
    "LAUDO", "DECISÃO", "DESPACHO", "ATA DE AUDIÊNCIA", "CARTA PRECATÓRIA",
    "PETIÇÃO", "PARECER", "MANIFESTAÇÃO", "DOCUMENTAÇÃO", "ACÓRDÃO",
    "LIBELO", "COTA MINISTERIAL",
}
GRUPO_B = {
    "OFÍCIO", "CERTIDÃO", "INTIMAÇÃO", "MANDADO", "ALVARÁ", "CONCLUSÃO",
    "REMESSA", "RECIBO", "ATO ORDINATÓRIO", "JUNTADA", "BIC", "DOCUMENTOS",
    "AVISO DE RECEBIMENTO", "EDITAL", "EXTRATO DE ATA", "TERMO DE VISTA",
}
GRUPO_C = {"ASSINATURA"}

# --- Mapa: tipo oficial do PJe (coluna "Tipo" da capa) -> categoria --------
MAPA_TIPO_PJE = {
    "peticao inicial": "PETIÇÃO",
    "peticao": "PETIÇÃO",
    "manifestacao": "MANIFESTAÇÃO",
    "parecer": "PARECER",
    "denuncia": "DENÚNCIA",
    "despacho": "DESPACHO",
    "decisao": "DECISÃO",
    "sentenca": "SENTENÇA",
    "ata da audiencia": "ATA DE AUDIÊNCIA",
    "ata de audiencia": "ATA DE AUDIÊNCIA",
    "carta precatoria": "CARTA PRECATÓRIA",
    "intimacao": "INTIMAÇÃO",
    "certidao": "CERTIDÃO",
    "certidao de publicacao no djen": "CERTIDÃO",
    "certidao de publicacao": "CERTIDÃO",
    "oficio": "OFÍCIO",
    "mandado": "MANDADO",
    "alvara": "ALVARÁ",
    "ato ordinatorio": "ATO ORDINATÓRIO",
    "laudo": "LAUDO",
    "laudo pericial": "LAUDO",
    "alegacoes finais": "ALEGAÇÕES",
    "resposta a acusacao": "RESPOSTA",
    "portaria": "PORTARIA",
    "boletim de ocorrencia": "BOLETIM DE OCORRÊNCIA",
    "termo de declaracoes": "DECLARAÇÃO",
    "interrogatorio": "INTERROGATÓRIO",
    "relatorio": "RELATÓRIO",
    "medida protetiva": "MEDIDA PROTETIVA",
    "juntada": "JUNTADA",
    "documento de comprovacao": "DOCUMENTOS",
    # tipos comuns em processos antigos digitalizados para o PJe
    "documentacao": "DOCUMENTAÇÃO",
    "conclusao": "CONCLUSÃO",
    "aviso de recebimento": "AVISO DE RECEBIMENTO",
    "termo de audiencia": "ATA DE AUDIÊNCIA",
    "assentada": "ATA DE AUDIÊNCIA",
    "acordao": "ACÓRDÃO",
    "pronuncia": "PRONÚNCIA",
    "edital": "EDITAL",
    "extrato de ata": "EXTRATO DE ATA",
    "termo de vista": "TERMO DE VISTA",
    "cota ministerial": "COTA MINISTERIAL",
    "contrarrazoes": "RECURSO",
    "razoes recursais": "RECURSO",
    "apelacao": "RECURSO",
    "outros documentos": None,  # genérico -> classificar pelo conteúdo
}


def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def categoria_do_tipo_pje(tipo: str | None) -> str | None:
    """Mapeia a coluna 'Tipo' da capa do PJe para uma categoria interna.
    Retorna None quando o tipo é genérico/desconhecido (usar conteúdo)."""
    if not tipo:
        return None
    chave = _normalizar(tipo)
    if chave in MAPA_TIPO_PJE:
        return MAPA_TIPO_PJE[chave]
    # tentativa por prefixo ("Certidão de publicação no DJe" etc.)
    for k, v in MAPA_TIPO_PJE.items():
        if chave.startswith(k):
            return v
    return None


# --- Classificador por conteúdo (fallback) ---------------------------------
# Formato: (CATEGORIA, [(regex, peso), ...], pontuacao_minima)
_TIPOS: list[tuple[str, list[tuple[re.Pattern, int]], int]] = [
    ("AUTUAÇÃO", [
        (re.compile(r"\bA\s*U\s*T\s*U\s*A\s*[ÇC]\s*[ÃA]\s*O\b", re.I), 10),
        (re.compile(r"\bautuo o\(?a?\)? presente\b", re.I), 10),
    ], 6),
    ("PORTARIA", [
        (re.compile(r"^[\s#]*PORTARIA\b", re.I | re.M), 10),
        (re.compile(r"\bportaria n[°º]\s*\d", re.I), 10),
        (re.compile(r"\binstaurar? inqu[ée]rito policial\b", re.I), 8),
    ], 8),
    ("DENÚNCIA", [
        (re.compile(r"\boferece a presente den[úu]ncia\b", re.I), 10),
        (re.compile(r"\bdenuncia como incurso\b", re.I), 10),
    ], 8),
    ("SENTENÇA", [
        (re.compile(r"^[\s#]*SENTEN[ÇC]A\b", re.M), 10),
        (re.compile(r"\bjulgo (?:totalmente )?(?:im)?procedente\b", re.I), 8),
        (re.compile(r"\b(?:condeno|absolvo) o r[ée]u\b", re.I), 8),
        (re.compile(r"\bjulgo extinta?o?\b", re.I), 6),
    ], 8),
    ("PRONÚNCIA", [
        (re.compile(r"\b(?:im)?pronuncio o r[ée]u\b", re.I), 10),
    ], 8),
    ("ALEGAÇÕES", [
        (re.compile(r"\balega[çc][õo]es finais\b", re.I), 10),
        (re.compile(r"\bmemoriais finais\b", re.I), 8),
    ], 6),
    ("RESPOSTA", [
        (re.compile(r"\bresposta [àa] acusa[çc][ãa]o\b", re.I), 10),
        (re.compile(r"\bdefesa pr[ée]via\b", re.I), 8),
    ], 6),
    ("RECURSO", [
        (re.compile(r"^[\s#]*(?:RAZ[ÕO]ES DE )?APELA[ÇC][ÃA]O\s*$", re.M), 10),
        (re.compile(r"\binterp[õo]e o presente recurso\b", re.I), 10),
        (re.compile(r"\bapela[çc][ãa]o\b", re.I), 1),
    ], 8),
    ("BOLETIM DE OCORRÊNCIA", [
        (re.compile(r"\bboletim de ocorr[êe]ncia\b", re.I), 10),
        (re.compile(r"\bdados do registro\b", re.I), 3),
    ], 8),
    ("DECLARAÇÃO", [
        (re.compile(r"\btermo de declara[çc][õo]es\b", re.I), 10),
        (re.compile(r"\b[àa]s perguntas do\(?a?\)? delegado\b", re.I), 8),
    ], 8),
    ("INTERROGATÓRIO", [
        (re.compile(r"\btermo de (?:qualifica[çc][ãa]o e )?interrogat[óo]rio\b", re.I), 10),
    ], 8),
    ("RELATÓRIO", [
        (re.compile(r"\brelat[óo]rio final\b", re.I), 10),
    ], 8),
    ("MEDIDA PROTETIVA", [
        (re.compile(r"\bmedidas? protetivas? de urg[êe]ncia\b", re.I), 10),
    ], 8),
    ("AVALIAÇÃO DE RISCO", [
        (re.compile(r"\bformul[áa]rio nacional de avalia[çc][ãa]o de risco\b", re.I), 10),
    ], 8),
    ("LAUDO", [
        (re.compile(r"\blaudo de exame\b", re.I), 10),
        (re.compile(r"\bexame m[ée]dico pericial\b", re.I), 10),
    ], 8),
    ("DECISÃO", [
        (re.compile(r"^[\s#]*DECIS[ÃA]O\b", re.M), 10),
        (re.compile(r"\b(?:de|in)firo o pedido\b", re.I), 8),
    ], 8),
    ("DESPACHO", [
        (re.compile(r"^[\s#]*DESPACHO\b", re.M), 10),
        (re.compile(r"\bcite-se\b", re.I), 4),
        (re.compile(r"\bintime(?:m)?-se\b", re.I), 3),
        (re.compile(r"\bcumpra-se\b", re.I), 3),
    ], 8),
    ("ATA DE AUDIÊNCIA", [
        (re.compile(r"\bata d[ae] audi[êe]ncia\b", re.I), 10),
        (re.compile(r"\baberta a audi[êe]ncia\b", re.I), 8),
    ], 8),
    ("OFÍCIO", [
        (re.compile(r"^[\s#]*OF[ÍI]CIO\s+n[°º]", re.I | re.M), 10),
        (re.compile(r"\bof[íi]cio n[°º]\s*[\d.\-/]+", re.I), 8),
    ], 6),
    ("CARTA PRECATÓRIA", [
        (re.compile(r"\bcarta precat[óo]ria\b", re.I), 10),
    ], 8),
    ("CERTIDÃO", [
        (re.compile(r"^[\s#]*CERTID[ÃA]O(?:\s+DE\s+\w+)?\s*$", re.M), 10),
        (re.compile(r"\bcertifico,?\s+para os devidos fins\b", re.I), 8),
        (re.compile(r"\bcertifico que\b", re.I), 6),
        (re.compile(r"\bcertid[ãa]o de publica[çc][ãa]o\b", re.I), 8),
    ], 6),
    ("INTIMAÇÃO", [
        (re.compile(r"^[\s#]*INTIMA[ÇC][ÃA]O\b", re.M), 8),
        (re.compile(r"\bfica(?:m)? (?:a parte |as partes )?intimad[oa]s?\b", re.I), 6),
    ], 6),
    ("MANDADO", [
        (re.compile(r"^[\s#]*MANDADO\s+DE\b", re.M), 10),
        (re.compile(r"\bmandado de (?:cita|intima|penhora|bus|pris|condu)", re.I), 8),
    ], 8),
    ("ALVARÁ", [
        (re.compile(r"\balvar[áa] de soltura\b", re.I), 10),
    ], 8),
    ("CONCLUSÃO", [
        (re.compile(r"\b(?:torno os )?autos conclusos\b", re.I), 8),
    ], 8),
    ("REMESSA", [
        (re.compile(r"\b(?:fa[çc]o a|termo de) remessa\b", re.I), 10),
    ], 8),
    ("ATO ORDINATÓRIO", [
        (re.compile(r"^[\s#]*ATO ORDINAT[ÓO]RIO\b", re.M), 10),
    ], 8),
    ("PETIÇÃO", [
        (re.compile(r"^[\s#]*PETI[ÇC][ÃA]O INICIAL\b", re.M), 10),
        (re.compile(r"\bexcelent[íi]ssim[oa]\s+senhora?\s+(?:doutora?|dra?\.?|ju[íi]z)", re.I), 6),
        (re.compile(r"\bpede(?:\s+e\s+espera)?\s+deferimento\b", re.I), 3),
        (re.compile(r"\bnestes\s+termos,?\s*pede\s+deferimento\b", re.I), 6),
    ], 6),
    ("PARECER", [
        (re.compile(r"^[\s#]*PARECER\b", re.M), 10),
        (re.compile(r"\bo minist[ée]rio p[úu]blico.{0,80}manifesta-se\b", re.I | re.S), 6),
    ], 8),
]


def classificar_conteudo(texto: str) -> str:
    """Classifica pelo conteúdo. Retorna 'DOC' quando nenhum tipo atinge a
    pontuação mínima — o chamador decide o que fazer com peças genéricas."""
    amostra = texto[:3000]
    inicio = amostra[:400]

    melhor_tipo, melhor_score = "DOC", 0
    for tipo, sinais, minimo in _TIPOS:
        score = 0
        for padrao, peso in sinais:
            if padrao.search(amostra):
                score += peso
                if padrao.search(inicio):
                    score += peso  # bônus: sinal no cabeçalho da peça
        if score >= minimo and score > melhor_score:
            melhor_tipo, melhor_score = tipo, score
    return melhor_tipo


def grupo(categoria: str) -> str:
    if categoria in GRUPO_A:
        return "A"
    if categoria in GRUPO_B:
        return "B"
    if categoria in GRUPO_C:
        return "C"
    return "A"  # em dúvida, preserva integral — nunca perder conteúdo
