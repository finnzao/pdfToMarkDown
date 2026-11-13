"""Formato jurídico: índice da capa, alertas e ordem das peças.

    python -m unittest tests.test_juridico -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf as fitz  # noqa: E402

from app.pipeline.capa import (EntradaIndice, MetaProcesso,  # noqa: E402
                               casar_indice_com_rodapes, parsear_capa)
from app.pipeline.classificacao import classificador  # noqa: E402
from app.pipeline.processador import processar  # noqa: E402

NUMERO = "8000123-45.2023.8.05.0001"
VAO = " " * 30
CAPA = [
    "TJBA",
    "PJe - Processo Judicial Eletrônico",
    f"Número: {NUMERO}",
    "Classe: PROCEDIMENTO COMUM CÍVEL",
    " Órgão julgador: V DOS FEITOS DE REL DE CONS CIV E COMERCIAIS DE RIO REAL",
    " Assuntos: Inclusão Indevida em Cadastro de Inadimplentes",
    " Segredo de justiça? NÃO",
    " Justiça gratuita? SIM",
    "                Partes                                Advogados",
    "MARIA EXEMPLO DA SILVA (AUTOR)",
    "BANCO EXEMPLO (EMBARGADO)",
    "                         Documentos",
    "   Id.      Data da    Documento" + VAO + "Tipo",
    "         Assinatura",
    "46742 07/10/2024 08:35 Certidão de trânsito em julgado" + VAO + "Certidão",
    " 7478",
    "46657 02/10/2024 08:37 CONTESTAÇÃO BANCO EXEMPLO" + VAO + "Contestação",
    " 6836            E DOCUMENTOS",
    "46577 01/10/2024 04:56 Sentença" + VAO + "Sentença",
    " 2467",
    "46000 30/09/2024 10:00 Comprovante" + VAO + "Outros documentos",
    " 0001",
    "20315 01/06/2022 05:20 Petição Inicial" + VAO + "Petição Inicial",
    " 9932SIGILOSO",
]
CORPO = [  # (Id do rodapé, texto) na ordem das páginas, que não é a cronológica
    ("465772467", "SENTENÇA. Julgo procedente o pedido formulado na inicial."),
    ("467427478", "CERTIDÃO. Certifico que a sentença transitou em julgado."),
    ("466576836", "CONTESTAÇÃO. O réu sustenta a regularidade da inscrição."),
    ("466576836", "Documentos que acompanham a contestação do banco réu."),
    ("203159932", "PETIÇÃO INICIAL. A autora requer a exclusão do cadastro."),
    ("999999999", "Documento juntado depois da geração do índice da capa."),
]


class TesteFormatoJuridico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        doc = fitz.open()
        capa = doc.new_page(width=842, height=595)
        for i, linha in enumerate(CAPA):
            capa.insert_text((30, 40 + 14 * i), linha, fontsize=8, fontname="cour")
        contagem: dict[str, int] = {}
        for id_pje, texto in CORPO:
            contagem[id_pje] = contagem.get(id_pje, 0) + 1
            p = doc.new_page(width=595, height=842)
            p.insert_textbox(fitz.Rect(50, 60, 545, 700), (texto + " ") * 8, fontsize=10)
            p.insert_text((50, 814), f"Num. {id_pje} - Pág. {contagem[id_pje]}", fontsize=7)
        cls.resultado = processar(doc.tobytes(), f"{NUMERO}.pdf")
        cls.md = cls.resultado.autos

    def linhas_do_indice(self) -> list[list[str]]:
        bloco = self.md.split("## Índice (mais recente primeiro)")[1].split("\n---\n")[0]
        linhas = [l for l in bloco.splitlines() if l.startswith("| ") and l[2].isdigit()]
        return [[c.strip() for c in l.strip("|").split("|")] for l in linhas]

    def test_indice_completo_e_ordenado(self):
        linhas = self.linhas_do_indice()
        self.assertEqual([l[0] for l in linhas], [
            "467427478", "466576836", "465772467", "460000001", "203159932", "999999999"])
        self.assertEqual(linhas[0][1:5], [
            "07/10/2024", "08:35", "Certidão de trânsito em julgado", "Certidão"])
        self.assertEqual(linhas[1][3:8], [
            "CONTESTAÇÃO BANCO EXEMPLO E DOCUMENTOS", "Contestação", "CONTESTAÇÃO",
            "parte", "4–5"])
        self.assertEqual(linhas[2][5:7], ["SENTENÇA", "juiz"])
        self.assertEqual(linhas[3][5:8], ["DOCUMENTOS", "anexo", "não localizadas"])
        self.assertEqual(linhas[4][3], "Petição Inicial [sigiloso]")
        self.assertEqual(linhas[5][5:7], ["DOCUMENTO", "—"])
        self.assertIn("documentos_sigilosos: 1", self.md)

    def test_alertas_dentro_do_markdown(self):
        alertas = self.md.split("## Alertas")[1].split("## Índice")[0]
        self.assertIn("sem conteúdo no corpo: Id 460000001", alertas)
        self.assertIn("possivelmente incompleto", alertas)
        self.assertIn("999999999", alertas)
        self.assertEqual(self.resultado.avisos, [
            a[2:] for a in alertas.splitlines() if a.startswith("- ")])

    def test_pecas_em_ordem_cronologica_unica(self):
        self.assertNotIn("Peças de Expediente", self.md)
        cabecalhos = [l for l in self.md.splitlines() if l.startswith("### ")]
        self.assertEqual([c.split(" · ")[0][4:] for c in cabecalhos], [
            "467427478", "466576836", "465772467", "203159932", "999999999"])
        self.assertEqual(cabecalhos[0],
                         "### 467427478 · 07/10/2024 08:35 · Certidão de trânsito em julgado "
                         "[Certidão] · pág. 3")
        self.assertEqual(cabecalhos[3],
                         "### 203159932 · 01/06/2022 05:20 · Petição Inicial "
                         "[Petição Inicial] · pág. 6 · sigiloso")
        self.assertIn("transitou em julgado", self.md)

    def test_partes_com_qualquer_papel(self):
        self.assertIn('papel: "AUTOR"', self.md)
        self.assertIn('nome: "BANCO EXEMPLO"', self.md)
        self.assertIn('papel: "EMBARGADO"', self.md)
        self.assertIn("justica_gratuita: true", self.md)

    def test_id_inteiro_na_linha(self):
        meta = parsear_capa([
            "Documentos\n"
            "467427478 07/10/2024 08:35 Certidão" + VAO + "Certidão\n"
            "466576836 02/10/2024 08:37 Réplica" + VAO + "Réplica\n"
            " 123 texto que não é complemento de Id"])
        self.assertEqual([(e.id_pje, e.titulo, e.tipo) for e in meta.indice], [
            ("467427478", "Certidão", "Certidão"), ("466576836", "Réplica", "Réplica")])


class TesteCapa(unittest.TestCase):
    def test_marca_sigiloso(self):
        meta = parsear_capa([
            "Documentos\n"
            "15213 12/09/2018 13:15 11 CERTIDÃO" + VAO + "CertidãoSIGILOSO662\n"
            "15213 12/09/2018 13:15 12 PARECER MP" + VAO + "Outros documentos\n"
            " 671\n"
            "50626 25/06/2025 08:31 Intimação" + VAO + "Intimação\n"
            " 9588SIGILOSO"])
        self.assertEqual([(e.id_pje, e.titulo, e.tipo, e.sigiloso) for e in meta.indice], [
            ("15213662", "11 CERTIDÃO", "Certidão", True),
            ("15213671", "12 PARECER MP", "Outros documentos", False),
            ("506269588", "Intimação", "Intimação", True)])

    def test_id_incompleto_casa_depois_dos_exatos(self):
        meta = MetaProcesso(indice=[
            EntradaIndice("50626", "25/06/2025", "08:31", "Intimação"),
            EntradaIndice("506261111", "25/06/2025", "08:30", "Despacho")])
        casar_indice_com_rodapes(meta, ["506269588", "506261111"])
        self.assertEqual([e.id_pje for e in meta.indice], ["506269588", "506261111"])


class TesteClassificador(unittest.TestCase):
    CADASTRO = [  # (tipo, título, categoria, natureza)
        ("Despacho", "Despacho", "DESPACHO", "juiz"),
        ("Despacho de Mero Expediente", "", "DESPACHO", "juiz"),
        ("Decisão\\Acórdão", "", "ACÓRDÃO", "tribunal"),
        ("Informação 2º Grau", "", "INFORMAÇÃO DO 2º GRAU", "tribunal"),
        ("Decisão Monocrática Terminativa", "", "DECISÃO", "juiz"),
        ("Sentença (Outras)", "", "SENTENÇA", "juiz"),
        ("Termo de Audiência (Outros)", "", "ATA DE AUDIÊNCIA", "juiz"),
        ("Petição Inicial (Outras)", "", "PETIÇÃO INICIAL", "parte"),
        ("Petição (Outras)", "", "PETIÇÃO", "parte"),
        ("Contestação", "", "CONTESTAÇÃO", "parte"),
        ("Réplica", "", "RÉPLICA", "parte"),
        ("Impugnação ao Cumprimento de Sentença", "", "IMPUGNAÇÃO", "parte"),
        ("Embargos de Declaração", "", "EMBARGOS DE DECLARAÇÃO", "parte"),
        ("Embargos (outros)", "", "EMBARGOS", "parte"),
        ("Recurso Inominado", "", "RECURSO", "parte"),
        ("Recurso\\Adesivo", "", "RECURSO", "parte"),
        ("Contrarrazões da Apelação", "", "CONTRARRAZÕES", "parte"),
        ("Contra-razões", "", "CONTRARRAZÕES", "parte"),
        ("Outros documentos", "14 CONTRA RAZÕES", "CONTRARRAZÕES", "parte"),
        ("Acesso aos autos", "Autos", "PETIÇÃO", "parte"),
        ("Execução/Cumprimento de Sentença", "", "CUMPRIMENTO DE SENTENÇA", "parte"),
        ("Ações Processuais\\Manifestação (Outras)", "", "MANIFESTAÇÃO", "parte"),
        ("Manifestação do Ministério Público", "", "MANIFESTAÇÃO", "parte"),
        ("Certidão de Trânsito em Julgado", "", "CERTIDÃO", "cartório"),
        ("Certidão de publicação no DJEN", "", "CERTIDÃO", "cartório"),
        ("Certidão de decurso de prazo", "", "CERTIDÃO", "cartório"),
        ("Intimação (Outros)", "", "INTIMAÇÃO", "cartório"),
        ("Despacho/Citação (Outros)", "", "CITAÇÃO", "cartório"),
        ("Ato Ordinatório", "", "ATO ORDINATÓRIO", "cartório"),
        ("Devolução de Mandado", "", "MANDADO", "cartório"),
        ("Elementos de Prova/Ofício (Outros)", "", "OFÍCIO", "cartório"),
        ("Aviso de Recebimento - AR", "", "AVISO DE RECEBIMENTO", "cartório"),
        ("Protocolo Sisbajud Positivo", "", "CONSTRIÇÃO", "cartório"),
        ("Ações Processuais\\Alvará", "", "ALVARÁ", "cartório"),
        ("Laudo (Outros)", "", "LAUDO", "prova"),
        ("Demonstrativo de Cálculo", "", "CÁLCULO", "prova"),
        ("Cálculos", "", "CÁLCULO", "prova"),
        ("Instrumento de Procuração", "", "PROCURAÇÃO", "anexo"),
        ("Documento de Comprovação", "", "DOCUMENTOS", "anexo"),
        ("Guia de Custas", "", "GUIA", "anexo"),
        ("Outros documentos", "07-DESPACHO", "DESPACHO", "juiz"),
        ("Outros documentos", "02-PETIÇÃO INICIAL", "PETIÇÃO INICIAL", "parte"),
        ("Outros documentos", "05-MANDADO DE BUSCA E APREENSÃO", "MANDADO", "cartório"),
        ("Certidão", "04-DECISÃO", "CERTIDÃO", "cartório"),
    ]

    # Combo "Tipo de modelo de documento" do PJe/TJBA da unidade (60 opções).
    MODELOS_TJBA = {
        ("DESPACHO", "juiz"): [
            "DESPACHO MANDADO", "Despacho", "Despacho Básico", "Despacho Inicial",
            "Despacho Inicial - Execucao fiscal", "Despacho Mero Expediente"],
        ("DECISÃO", "juiz"): ["Decisão"],
        ("SENTENÇA", "juiz"): ["Sentença"],
        ("ATA DE AUDIÊNCIA", "juiz"): ["TERMO DE AUDIÊNCIA"],
        ("PETIÇÃO", "parte"): ["Petição"],
        ("ALVARÁ", "cartório"): ["Alvará", "Alvará judicial"],
        ("ATO ORDINATÓRIO", "cartório"): ["Ato Ordinatório"],
        ("CARTA", "cartório"): ["CARTA", "Carta de Sentença"],
        ("CARTA PRECATÓRIA", "cartório"): [
            "CARTA PRECATÓRIA - AUDIÊNCIA", "Carta precatória",
            "Devolução de Carta Precatória - Cumprida"],
        ("CERTIDÃO", "cartório"): ["CERTIDÃO DE EDITAL", "Certidão", "Certidão Interna"],
        ("CITAÇÃO", "cartório"): [
            "Carta de Citação", "Citação", "Citação do réu", "Citação e Intimação",
            "Citação e Intimação Aud. Alimentos", "Citações"],
        ("EDITAL", "cartório"): ["Edital"],
        ("FORMAL DE PARTILHA", "cartório"): ["Formal de Partilha"],
        ("INTIMAÇÃO", "cartório"): [
            "INT", "Intimação", "Intimação - Oficial - Alimentos",
            "Intimação - Oficial - Audiência - alimentos", "Intimação via postal",
            "NOTIFICAÇÃO"],
        ("MANDADO", "cartório"): [
            "MANDADO CITAÇÃO - PRAZO 15 DIAS - PADRÃO", "MANDADO DE CITAÇÃO AUD. ALIMENTOS",
            "MANDADO DE INTIMAÇÃO -", "MANDADOS DIVERSOS", "Mandado", "Mandado Intimação",
            "Mandado de Averbação", "Mandado de Busca e Apreensão", "Mandado de Citação",
            "Mandado de Citação e Intimação", "Mandado de Intimação"],
        ("OFÍCIO", "cartório"): [
            "OFÍCIO - INFORMAÇÃO DE CRÉDITOS", "OFÍCIO - INSS - dependentes", "OFÍCIOS",
            "Oficio abertura de conta", "Ofício"],
        ("TERMO", "cartório"): [
            "TERMO", "TERMO DE GUARDA", "TERMO DE GUARDA (PROVISÓRIO)",
            "Termo de Inventariante", "Termos"],
        ("TERMO DE VISTA", "cartório"): ["Vista ao MP"],
        ("DOCUMENTOS", "anexo"): ["Comprovante Cadastro de Advogado"],
        (None, ""): ["DIVERSOS", "Variaveis"],
    }

    def test_modelos_do_pje_tjba(self):
        self.assertEqual(sum(len(v) for v in self.MODELOS_TJBA.values()), 60)
        for (categoria, natureza), tipos in self.MODELOS_TJBA.items():
            for tipo in tipos:
                with self.subTest(tipo=tipo):
                    obtida = classificador.pelo_cadastro(tipo, "")
                    self.assertEqual((obtida, classificador.natureza(obtida or "")),
                                     (categoria, natureza))
        self.assertEqual(classificador.classificar("DIVERSOS", "", "texto solto"), "DOCUMENTO")

    def test_tipo_e_titulo(self):
        for tipo, titulo, categoria, natureza in self.CADASTRO:
            with self.subTest(tipo=tipo, titulo=titulo):
                obtida = classificador.pelo_cadastro(tipo, titulo)
                self.assertEqual((obtida, classificador.natureza(obtida)),
                                 (categoria, natureza))

    def test_generico_cai_no_conteudo(self):
        self.assertIsNone(classificador.pelo_cadastro("Outros documentos", "espelho saipro"))
        self.assertIsNone(classificador.pelo_cadastro("Documento", "Capa"))
        self.assertEqual(classificador.classificar(
            "Outros documentos", "anexo 3", "CONTESTAÇÃO\nO réu vem apresentar contestação."),
            "CONTESTAÇÃO")
        self.assertEqual(classificador.classificar("Outros documentos", "", "texto solto"),
                         "DOCUMENTO")
        self.assertEqual(classificador.classificar("Nota Explicativa", "", "texto solto"),
                         "NOTA EXPLICATIVA")

    def test_cadastro_divergente(self):
        self.assertEqual(classificador.divergencia("Certidão", "04-DECISÃO"),
                         ("CERTIDÃO", "DECISÃO"))
        self.assertEqual(classificador.divergencia("CERTIDÃO", "Petição Inicial"),
                         ("CERTIDÃO", "PETIÇÃO INICIAL"))
        for tipo, titulo in (("Documento de Comprovação", "DECISÃO - AGRAVO DE INSTRUMENTO"),
                             ("Informação 2º Grau", "8071916-69.2025.8.05.0000-Decisão"),
                             ("Petição", "CIÊNCIA DE DECISÃO"),
                             ("Outros documentos", "07-DESPACHO")):
            with self.subTest(tipo=tipo):
                self.assertIsNone(classificador.divergencia(tipo, titulo))

    def test_grupo(self):
        self.assertEqual([classificador.grupo(c) for c in (
            "SENTENÇA", "CONTESTAÇÃO", "LAUDO", "CERTIDÃO", "PROCURAÇÃO", "DOCUMENTO")],
            ["A", "A", "A", "B", "B", "A"])


if __name__ == "__main__":
    unittest.main()
