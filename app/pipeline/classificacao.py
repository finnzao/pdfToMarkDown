"""Classificação de peças processuais.

Três vias, em ordem de confiança:
1. Tipo oficial da capa do PJe (coluna "Tipo");
2. Título do documento (coluna "Documento"), quando o tipo é genérico;
3. Scoring ponderado sobre o conteúdo.

Os prefixos de PREFIXOS_TIPO vêm da tabela "Nova nomenclatura de tipos de
documento do PJe" (TJPE, nomes antigos e nomes no padrão CNJ), do catálogo
de tipos do PJe/TJBA usado pelo Fórum Hub, dos 60 tipos de modelo de
documento da unidade no PJe/TJBA (conferidos um a um em
tests/test_juridico.py) e das capas de processos reais.
Cada categoria tem uma natureza — quem produz a peça: juiz (o juízo do
processo), tribunal (instância superior), parte, cartório, prova ou anexo —,
que é o que permite achar o último pronunciamento judicial sem ler o texto.
"""
from __future__ import annotations

import re
import unicodedata

JUIZ, TRIBUNAL, PARTE = "juiz", "tribunal", "parte"
SECRETARIA, PROVA, ANEXO = "cartório", "prova", "anexo"

NATUREZA = {
    "DESPACHO": JUIZ, "DECISÃO": JUIZ, "SENTENÇA": JUIZ,
    "PRONÚNCIA": JUIZ, "ATA DE AUDIÊNCIA": JUIZ,

    "ACÓRDÃO": TRIBUNAL, "INFORMAÇÃO DO 2º GRAU": TRIBUNAL,

    "PETIÇÃO INICIAL": PARTE, "PETIÇÃO": PARTE, "CONTESTAÇÃO": PARTE,
    "RÉPLICA": PARTE, "RECONVENÇÃO": PARTE, "IMPUGNAÇÃO": PARTE,
    "EXCEÇÃO": PARTE, "EMBARGOS DE DECLARAÇÃO": PARTE, "EMBARGOS": PARTE,
    "RECURSO": PARTE, "CONTRARRAZÕES": PARTE, "CUMPRIMENTO DE SENTENÇA": PARTE,
    "ALEGAÇÕES": PARTE, "MANIFESTAÇÃO": PARTE, "PARECER": PARTE,
    "COTA MINISTERIAL": PARTE, "DENÚNCIA": PARTE, "RESPOSTA": PARTE,
    "LIBELO": PARTE, "ACORDO": PARTE,

    "CERTIDÃO": SECRETARIA, "INTIMAÇÃO": SECRETARIA, "CITAÇÃO": SECRETARIA,
    "MANDADO": SECRETARIA, "OFÍCIO": SECRETARIA, "CARTA": SECRETARIA,
    "CARTA PRECATÓRIA": SECRETARIA, "ALVARÁ": SECRETARIA, "EDITAL": SECRETARIA,
    "ATO ORDINATÓRIO": SECRETARIA, "CONCLUSÃO": SECRETARIA,
    "REMESSA": SECRETARIA, "JUNTADA": SECRETARIA, "TERMO": SECRETARIA,
    "TERMO DE VISTA": SECRETARIA, "AVISO DE RECEBIMENTO": SECRETARIA,
    "EXTRATO DE ATA": SECRETARIA, "EXPEDIENTE": SECRETARIA,
    "CONSTRIÇÃO": SECRETARIA, "AUTUAÇÃO": SECRETARIA, "RECIBO": SECRETARIA,
    "FORMAL DE PARTILHA": SECRETARIA,

    "LAUDO": PROVA, "CÁLCULO": PROVA, "RELATÓRIO": PROVA,
    "BOLETIM DE OCORRÊNCIA": PROVA, "DECLARAÇÃO": PROVA,
    "INTERROGATÓRIO": PROVA, "INQUÉRITO": PROVA, "PORTARIA": PROVA,
    "MEDIDA PROTETIVA": PROVA, "AVALIAÇÃO DE RISCO": PROVA,
    "DOCUMENTAÇÃO": PROVA,

    "PROCURAÇÃO": ANEXO, "DOCUMENTOS": ANEXO, "GUIA": ANEXO, "BIC": ANEXO,
}

# Expediente e anexos vão para o contador "expediente" da interface.
_NATUREZAS_GRUPO_B = {SECRETARIA, ANEXO}
_AUTORES_DE_PECA = {JUIZ, PARTE, SECRETARIA}

# Prefixo normalizado do tipo (ou do título) -> categoria. Vence o prefixo
# mais longo. None = tipo genérico: decide pelo título ou pelo conteúdo.
PREFIXOS_TIPO: dict[str, str | None] = {
    # --- atos do juiz ---
    "despacho": "DESPACHO",
    "decisao": "DECISÃO",
    "juizo de admissibilidade": "DECISÃO",
    "sentenca": "SENTENÇA",
    "acordao": "ACÓRDÃO",
    "ementa": "ACÓRDÃO",
    "voto": "ACÓRDÃO",
    "inteiro teor": "ACÓRDÃO",
    "pronuncia": "PRONÚNCIA",
    "ata da audiencia": "ATA DE AUDIÊNCIA",
    "ata de audiencia": "ATA DE AUDIÊNCIA",
    "ata de julgamento": "ATA DE AUDIÊNCIA",
    "termo de audiencia": "ATA DE AUDIÊNCIA",
    "assentada": "ATA DE AUDIÊNCIA",
    "ata": "ATA DE AUDIÊNCIA",

    # --- petições e manifestações das partes e do Ministério Público ---
    "peticao inicial": "PETIÇÃO INICIAL",
    "inicial": "PETIÇÃO INICIAL",
    "queixa": "PETIÇÃO INICIAL",
    "peticao": "PETIÇÃO",
    "acesso aos autos": "PETIÇÃO",
    "requerimento": "PETIÇÃO",
    "pedido": "PETIÇÃO",
    "comunicacoes": "PETIÇÃO",
    "desistencia": "PETIÇÃO",
    "renuncia de mandato": "PETIÇÃO",
    "liberdade provisoria": "PETIÇÃO",
    "relaxamento de prisao": "PETIÇÃO",
    "contestacao": "CONTESTAÇÃO",
    "resposta do reu": "CONTESTAÇÃO",
    "replica": "RÉPLICA",
    "reconvencao": "RECONVENÇÃO",
    "impugnacao": "IMPUGNAÇÃO",
    "excecao": "EXCEÇÃO",
    "incidente": "EXCEÇÃO",
    "embargos de declaracao": "EMBARGOS DE DECLARAÇÃO",
    "embargos": "EMBARGOS",
    "apelacao": "RECURSO",
    "agravo": "RECURSO",
    "recurso": "RECURSO",
    "razoes de apelacao": "RECURSO",
    "razoes de recurso": "RECURSO",
    "razoes recursais": "RECURSO",
    "correicao parcial": "RECURSO",
    "reclamacao": "RECURSO",
    "contrarrazoes": "CONTRARRAZÕES",
    "contra-razoes": "CONTRARRAZÕES",
    "contra razoes": "CONTRARRAZÕES",
    "contraminuta": "CONTRARRAZÕES",
    "execucao/cumprimento de sentenca": "CUMPRIMENTO DE SENTENÇA",
    "execucao / cumprimento de sentenca": "CUMPRIMENTO DE SENTENÇA",
    "cumprimento de sentenca": "CUMPRIMENTO DE SENTENÇA",
    "execucao": "CUMPRIMENTO DE SENTENÇA",
    "alegacoes finais": "ALEGAÇÕES",
    "razoes finais": "ALEGAÇÕES",
    "razoes": "ALEGAÇÕES",
    "memoriais": "ALEGAÇÕES",
    "memorial": "ALEGAÇÕES",
    "manifestacao": "MANIFESTAÇÃO",
    "ciencia": "MANIFESTAÇÃO",
    "informacoes prestadas": "MANIFESTAÇÃO",
    "parecer": "PARECER",
    "cota ministerial": "COTA MINISTERIAL",
    "remissao ministerial": "COTA MINISTERIAL",
    "denuncia": "DENÚNCIA",
    "representacao": "DENÚNCIA",
    "resposta a acusacao": "RESPOSTA",
    "resposta preliminar": "RESPOSTA",
    "resposta": "RESPOSTA",
    "defesa previa": "RESPOSTA",
    "acordo": "ACORDO",
    "termo de acordo": "ACORDO",
    "transacao": "ACORDO",

    # --- cartório: certidões, comunicações e atos de secretaria ---
    "certidao": "CERTIDÃO",
    "decurso de prazo": "CERTIDÃO",
    "transito em julgado": "CERTIDÃO",
    "publicacao": "CERTIDÃO",
    "apensamento": "CERTIDÃO",
    "intimacao": "INTIMAÇÃO",
    "int": "INTIMAÇÃO",
    "carta de intimacao": "INTIMAÇÃO",
    "notificacao": "INTIMAÇÃO",
    "citacao": "CITAÇÃO",
    "carta de citacao": "CITAÇÃO",
    "despacho/citacao": "CITAÇÃO",
    "mandado": "MANDADO",
    "devolucao de mandado": "MANDADO",
    "recebimento de mandado": "MANDADO",
    "oficio": "OFÍCIO",
    "devolucao de oficio": "OFÍCIO",
    "recebimento de oficio": "OFÍCIO",
    "resposta ao oficio": "OFÍCIO",
    "precatorio": "OFÍCIO",
    "carta precatoria": "CARTA PRECATÓRIA",
    "devolucao de carta precatoria": "CARTA PRECATÓRIA",
    "carta": "CARTA",
    "devolucao de carta": "CARTA",
    "alvara": "ALVARÁ",
    "liberacao de alvara": "ALVARÁ",
    "ordem de": "ALVARÁ",
    "formal de partilha": "FORMAL DE PARTILHA",
    "edital": "EDITAL",
    "leilao": "EDITAL",
    "ato ordinatorio": "ATO ORDINATÓRIO",
    "despacho ordinatorio": "ATO ORDINATÓRIO",
    "conclusao": "CONCLUSÃO",
    "termo de remessa": "REMESSA",
    "remessa": "REMESSA",
    "juntada": "JUNTADA",
    "termo de vista": "TERMO DE VISTA",
    "vista": "TERMO DE VISTA",
    "termo": "TERMO",
    "aviso de recebimento": "AVISO DE RECEBIMENTO",
    "entregue": "AVISO DE RECEBIMENTO",
    "nao entregue": "AVISO DE RECEBIMENTO",
    "telegrama": "AVISO DE RECEBIMENTO",
    "extrato de ata": "EXTRATO DE ATA",
    "extrato da ata": "EXTRATO DE ATA",
    "expediente": "EXPEDIENTE",
    "diligencia": "EXPEDIENTE",
    "informacao": "EXPEDIENTE",
    "informacao 2º grau": "INFORMAÇÃO DO 2º GRAU",
    "informacao 2o grau": "INFORMAÇÃO DO 2º GRAU",
    "movimento processual": "EXPEDIENTE",
    "integra da movimentacao": "EXPEDIENTE",
    "protocolo de distribuicao": "EXPEDIENTE",
    "sisbajud": "CONSTRIÇÃO",
    "bacenjud": "CONSTRIÇÃO",
    "renajud": "CONSTRIÇÃO",
    "infojud": "CONSTRIÇÃO",
    "protocolo sisbajud": "CONSTRIÇÃO",
    "bloqueio": "CONSTRIÇÃO",
    "desbloqueio": "CONSTRIÇÃO",
    "gravame": "CONSTRIÇÃO",
    "penhora": "CONSTRIÇÃO",
    "auto de penhora": "CONSTRIÇÃO",
    "termo de penhora": "CONSTRIÇÃO",
    "lavrar termo de penhora": "CONSTRIÇÃO",
    "autuacao": "AUTUAÇÃO",
    "termo de autuacao": "AUTUAÇÃO",
    "recibo": "RECIBO",

    # --- prova ---
    "laudo": "LAUDO",
    "pericia": "LAUDO",
    "parecer tecnico": "LAUDO",
    "exame": "LAUDO",
    "estudo social": "LAUDO",
    "estudo psicossocial": "LAUDO",
    "calculo": "CÁLCULO",
    "demonstrativo": "CÁLCULO",
    "planilha": "CÁLCULO",
    "memoria de calculo": "CÁLCULO",
    "documento da contadoria": "CÁLCULO",
    "relatorio": "RELATÓRIO",
    "boletim de ocorrencia": "BOLETIM DE OCORRÊNCIA",
    "termo de declaracoes": "DECLARAÇÃO",
    "declaracao": "DECLARAÇÃO",
    "termo de transcricao de depoimento": "DECLARAÇÃO",
    "notas taquigraficas": "DECLARAÇÃO",
    "interrogatorio": "INTERROGATÓRIO",
    "inquerito": "INQUÉRITO",
    "procedimento policial": "INQUÉRITO",
    "auto de prisao": "INQUÉRITO",
    "termo circunstanciado": "INQUÉRITO",
    "portaria": "PORTARIA",
    "medida protetiva": "MEDIDA PROTETIVA",
    "formulario": "AVALIAÇÃO DE RISCO",
    "documentacao": "DOCUMENTAÇÃO",

    # --- anexos ---
    "procuracao": "PROCURAÇÃO",
    "instrumento de procuracao": "PROCURAÇÃO",
    "substabelecimento": "PROCURAÇÃO",
    "carta de preposto": "PROCURAÇÃO",
    "carta de preposicao": "PROCURAÇÃO",
    "documento de comprovacao": "DOCUMENTOS",
    "documento comprobatorio": "DOCUMENTOS",
    "documento de identificacao": "DOCUMENTOS",
    "documentos": "DOCUMENTOS",
    "comprovante": "DOCUMENTOS",
    "titulo": "DOCUMENTOS",
    "cda": "DOCUMENTOS",
    "guia": "GUIA",
    "boleto": "GUIA",

    # --- genéricos ---
    "outros documentos": None,
    "outras pecas": None,
    "outros": None,
    "documento diverso": None,
    "diversos": None,
    "variaveis": None,
    "documento": None,
    "anexo": None,
    "copia": None,
    "capa": None,
    "volume": None,
    "apenso": None,
}

# Raízes de hierarquia dos nomes no padrão CNJ ("Decisão\Acórdão",
# "Elementos de Prova/Ofício (Outros)"): só o último nível classifica.
_RAIZES_CNJ = ("acoes processuais", "elementos de prova", "peca informativa",
               "decisao")

# (CATEGORIA, [(regex, peso), ...], pontuação mínima). Sinal específico
# (cabeçalho literal, expressão decisória) pesa 10; palavra ambígua pesa 1.
_SINAIS_CONTEUDO: list[tuple[str, list[tuple[re.Pattern, int]], int]] = [
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
    ("ACÓRDÃO", [
        (re.compile(r"^[\s#]*AC[ÓO]RD[ÃA]O\b", re.M), 10),
        (re.compile(r"\bacordam os (?:desembargadores|ju[íi]zes|membros)\b", re.I), 10),
    ], 8),
    ("SENTENÇA", [
        (re.compile(r"^[\s#]*SENTEN[ÇC]A\b", re.M), 10),
        (re.compile(r"\bjulgo (?:totalmente |parcialmente )?(?:im)?procedentes?\b", re.I), 8),
        (re.compile(r"\b(?:condeno|absolvo) o r[ée]u\b", re.I), 8),
        (re.compile(r"\bjulgo extint[ao]\b", re.I), 6),
        (re.compile(r"\bhomologo\b.{0,80}\bacordo\b", re.I | re.S), 6),
    ], 8),
    ("PRONÚNCIA", [
        (re.compile(r"\b(?:im)?pronuncio o r[ée]u\b", re.I), 10),
    ], 8),
    ("CONTESTAÇÃO", [
        (re.compile(r"^[\s#]*CONTESTA[ÇC][ÃA]O\b", re.M), 10),
        (re.compile(r"\bapresentar (?:a sua |sua |a presente )?contesta[çc][ãa]o\b", re.I), 8),
    ], 8),
    ("RÉPLICA", [
        (re.compile(r"^[\s#]*R[ÉE]PLICA\b", re.M), 10),
        (re.compile(r"\bimpugna[çc][ãa]o [àa] contesta[çc][ãa]o\b", re.I), 8),
    ], 8),
    ("EMBARGOS DE DECLARAÇÃO", [
        (re.compile(r"^[\s#]*EMBARGOS DE DECLARA[ÇC][ÃA]O\b", re.M), 10),
        (re.compile(r"\bop[õo]e[mr]?\b.{0,60}\bembargos de declara[çc][ãa]o\b", re.I | re.S), 10),
    ], 8),
    ("CUMPRIMENTO DE SENTENÇA", [
        (re.compile(r"^[\s#]*(?:PEDIDO DE )?CUMPRIMENTO DE SENTEN[ÇC]A\b", re.M), 10),
        (re.compile(r"\brequer(?:er)?\b.{0,60}\bcumprimento d[ae] senten[çc]a\b", re.I | re.S), 8),
    ], 8),
    ("IMPUGNAÇÃO", [
        (re.compile(r"^[\s#]*IMPUGNA[ÇC][ÃA]O AO CUMPRIMENTO\b", re.M), 10),
        (re.compile(r"\bimpugna[çc][ãa]o ao cumprimento de senten[çc]a\b", re.I), 8),
    ], 8),
    ("ALEGAÇÕES", [
        (re.compile(r"\balega[çc][õo]es finais\b", re.I), 10),
        (re.compile(r"\bmemoriais finais\b", re.I), 8),
    ], 6),
    ("RESPOSTA", [
        (re.compile(r"\bresposta [àa] acusa[çc][ãa]o\b", re.I), 10),
        (re.compile(r"\bdefesa pr[ée]via\b", re.I), 8),
    ], 6),
    ("CONTRARRAZÕES", [
        (re.compile(r"^[\s#]*CONTRARRAZ[ÕO]ES\b", re.M), 10),
        (re.compile(r"\bapresentar (?:as suas |suas )?contrarraz[õo]es\b", re.I), 8),
    ], 8),
    ("RECURSO", [
        (re.compile(r"^[\s#]*(?:RAZ[ÕO]ES DE )?APELA[ÇC][ÃA]O\s*$", re.M), 10),
        (re.compile(r"^[\s#]*RECURSO INOMINADO\b", re.M), 10),
        (re.compile(r"\binterp[õo]e[mr]? o presente recurso\b", re.I), 10),
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
        (re.compile(r"^[\s#]*LAUDO PERICIAL\b", re.M), 10),
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
        (re.compile(r"\b(?:ata|termo) d[ae] audi[êe]ncia\b", re.I), 10),
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
        (re.compile(r"\balvar[áa] de (?:soltura|levantamento)\b", re.I), 10),
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
    ("PROCURAÇÃO", [
        (re.compile(r"^[\s#]*(?:PROCURA[ÇC][ÃA]O|SUBSTABELECIMENTO)\b", re.M), 10),
        (re.compile(r"\bnomeia e constitui\b", re.I), 8),
    ], 8),
    ("PETIÇÃO INICIAL", [
        (re.compile(r"^[\s#]*PETI[ÇC][ÃA]O INICIAL\b", re.M), 10),
        (re.compile(r"\bvem\b.{0,120}\bpropor a presente\b", re.I | re.S), 8),
    ], 8),
    ("PETIÇÃO", [
        (re.compile(r"\bexcelent[íi]ssim[oa]\s+senhora?\s+(?:doutora?|dra?\.?|ju[íi]z)", re.I), 6),
        (re.compile(r"\bpede(?:\s+e\s+espera)?\s+deferimento\b", re.I), 3),
        (re.compile(r"\bnestes\s+termos,?\s*pede\s+deferimento\b", re.I), 6),
    ], 6),
    ("PARECER", [
        (re.compile(r"^[\s#]*PARECER\b", re.M), 10),
        (re.compile(r"\bo minist[ée]rio p[úu]blico.{0,80}manifesta-se\b", re.I | re.S), 6),
    ], 8),
]


class ClassificadorPecas:
    SEM_CATEGORIA = "DOCUMENTO"

    _RE_PARENTESES = re.compile(r"\s*\([^)]*\)")
    _RE_NUMERACAO = re.compile(r"^[\d.\-–)\s]+")
    _RE_ESPACOS = re.compile(r"\s+")

    def __init__(self, prefixos: dict[str, str | None] = PREFIXOS_TIPO):
        self._prefixos = sorted(prefixos.items(), key=lambda kv: len(kv[0]), reverse=True)

    def classificar(self, tipo: str | None, titulo: str | None, texto: str) -> str:
        """Categoria da peça: tipo do PJe, senão título, senão conteúdo. Tipo
        específico fora do mapa vira a própria categoria (a capa é a fonte
        de verdade); sem nenhum sinal, SEM_CATEGORIA."""
        categoria = self.pelo_cadastro(tipo, titulo) or self.pelo_conteudo(texto)
        if categoria:
            return categoria
        chave = self._normalizar(tipo)
        if chave and not self._generico(chave):
            return (tipo or "").strip().upper()
        return self.SEM_CATEGORIA

    def pelo_cadastro(self, tipo: str | None, titulo: str | None) -> str | None:
        return self._por_prefixo(tipo) or self._por_prefixo(titulo)

    def pelo_conteudo(self, texto: str) -> str | None:
        amostra = texto[:3000]
        inicio = amostra[:400]
        melhor, melhor_score = None, 0
        for categoria, sinais, minimo in _SINAIS_CONTEUDO:
            score = 0
            for padrao, peso in sinais:
                if padrao.search(amostra):
                    score += peso * (2 if padrao.search(inicio) else 1)
            if score >= minimo and score > melhor_score:
                melhor, melhor_score = categoria, score
        return melhor

    def natureza(self, categoria: str) -> str:
        return NATUREZA.get(categoria, "")

    def divergencia(self, tipo: str | None, titulo: str | None) -> tuple[str, str] | None:
        """(categoria pelo tipo, categoria pelo título) quando o cadastro se
        contradiz sobre quem produziu a peça. Anexo e prova ficam de fora: é
        normal um comprovante se chamar "Sentença do processo X"."""
        pelo_tipo, pelo_titulo = self._por_prefixo(tipo), self._por_prefixo(titulo)
        if not pelo_tipo or not pelo_titulo:
            return None
        de, para = self.natureza(pelo_tipo), self.natureza(pelo_titulo)
        if de == para or de not in _AUTORES_DE_PECA or para not in _AUTORES_DE_PECA | {TRIBUNAL}:
            return None
        return pelo_tipo, pelo_titulo

    def grupo(self, categoria: str) -> str:
        """A: peça principal. B: expediente ou anexo. Em dúvida, A."""
        return "B" if self.natureza(categoria) in _NATUREZAS_GRUPO_B else "A"

    def _por_prefixo(self, nome: str | None) -> str | None:
        chave = self._normalizar(nome)
        if not chave:
            return None
        for prefixo, categoria in self._prefixos:
            if self._casa(chave, prefixo):
                return categoria
        return None

    def _generico(self, chave: str) -> bool:
        return any(c is None and self._casa(chave, p) for p, c in self._prefixos)

    @staticmethod
    def _casa(chave: str, prefixo: str) -> bool:
        """Prefixo em fronteira de palavra, aceitando o plural ('mandados',
        'citações')."""
        formas = [prefixo + "s", prefixo]
        if prefixo.endswith("ao"):
            formas.append(prefixo[:-2] + "oes")
        for forma in formas:
            if chave.startswith(forma):
                resto = chave[len(forma):]
                if not resto or not resto[0].isalnum():
                    return True
        return False

    def _normalizar(self, nome: str | None) -> str:
        s = unicodedata.normalize("NFD", (nome or "").lower())
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        s = self._RE_PARENTESES.sub("", s).replace("\\", "/")
        s = self._RE_ESPACOS.sub(" ", s).strip()
        raiz, _, resto = s.partition("/")
        if resto and raiz.strip() in _RAIZES_CNJ:
            s = resto.strip()
        return self._RE_NUMERACAO.sub("", s)


classificador = ClassificadorPecas()
