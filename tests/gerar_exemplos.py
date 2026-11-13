"""Gera os PDFs de exemplo em exemplos/ (dados fictícios).

    python tests/gerar_exemplos.py

texto.pdf        3 páginas com camada de texto: cabeçalho/rodapé repetidos,
                 assinatura, URL de validação, hifenização, duas colunas,
                 tabela e títulos
digitalizado.pdf 1 página só imagem (scan a 150 dpi, inclinado 3°)
misto.pdf        página com texto + página digitalizada + página em branco
protegido.pdf    texto.pdf com senha de abertura
"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

EXEMPLOS = Path(__file__).resolve().parent.parent / "exemplos"
FONTE = 10


def _moldura(page: fitz.Page, n: int) -> None:
    page.insert_text((50, 40), "PODER JUDICIÁRIO", fontsize=9)
    page.insert_text((50, 52), "TRIBUNAL DE JUSTIÇA DO ESTADO DA BAHIA", fontsize=9)
    page.insert_text((50, 790), "Documento assinado eletronicamente por MARIA DE "
                     "SOUZA, em 10/03/2024 14:00:00.", fontsize=7)
    page.insert_text((50, 802), "https://pje.tjba.jus.br/pje/Processo/Consulta"
                     "Documento/listView.seam?x=24031012", fontsize=7)
    page.insert_text((50, 814), f"Num. 12345678 - Pág. {n}", fontsize=7)


def _pagina_sentenca(doc: fitz.Document, n: int = 1) -> fitz.Page:
    p = doc.new_page(width=595, height=842)
    _moldura(p, n)
    p.insert_text((50, 100), "SENTENÇA", fontsize=12)
    p.insert_text((50, 125), "Processo nº 8000123-45.2023.8.05.0001", fontsize=FONTE)
    p.insert_text((50, 155), "RELATÓRIO", fontsize=12)
    p.insert_text((50, 180), (
        "Trata-se de ação de indenização proposta por JOÃO DA SILVA em face\n"
        "do BANCO EXEMPLO S.A., na qual o autor alega falha na presta-\n"
        "ção do serviço bancário e requer a reparação dos danos sofridos."),
        fontsize=FONTE)
    p.insert_text((50, 240), (
        "Citado, o réu apresentou contestação, sustentando a regularidade\n"
        "das cobranças. Houve réplica. É o relatório."), fontsize=FONTE)
    return p


def texto() -> fitz.Document:
    doc = fitz.open()
    _pagina_sentenca(doc, 1)

    p = doc.new_page(width=595, height=842)
    _moldura(p, 2)
    p.insert_text((50, 100), "FUNDAMENTAÇÃO", fontsize=12)
    esq = ["A relação entre as partes é de consumo, aplicando-se o Código de "
           "Defesa do Consumidor, inclusive a inversão do ônus da prova.",
           "O réu não comprovou a contratação dos serviços cobrados, ônus que "
           "lhe competia nos termos do art. 373, II, do CPC."]
    dir_ = ["Os extratos juntados demonstram descontos mensais indevidos na "
            "conta do autor desde janeiro de 2022.",
            "O dano moral decorre da privação de verba alimentar, situação que "
            "ultrapassa o mero aborrecimento."]
    for col, x in ((esq, 50), (dir_, 310)):
        y = 130
        for par in col:
            p.insert_textbox(fitz.Rect(x, y, x + 235, y + 90), par, fontsize=FONTE)
            y += 100

    p = doc.new_page(width=595, height=842)
    _moldura(p, 3)
    linhas = [("Parte", "Papel", "Valor"),
              ("JOÃO DA SILVA", "Autor", "R$ 4.350,00"),
              ("BANCO EXEMPLO S.A.", "Réu", "R$ 0,00")]
    x0, y0, w, h = 50, 100, 160, 22
    for r, linha in enumerate(linhas):
        for c, cel in enumerate(linha):
            ret = fitz.Rect(x0 + c * w, y0 + r * h, x0 + (c + 1) * w, y0 + (r + 1) * h)
            p.draw_rect(ret, color=(0, 0, 0), width=0.8)
            p.insert_text((ret.x0 + 4, ret.y1 - 7), cel, fontsize=FONTE)
    p.insert_text((50, 210), "DISPOSITIVO", fontsize=12)
    p.insert_text((50, 235), (
        "Ante o exposto, JULGO PROCEDENTE o pedido para condenar o réu ao\n"
        "pagamento de R$ 4.350,00 a título de danos materiais."), fontsize=FONTE)
    p.insert_text((50, 280), "Salvador, 10 de março de 2024.", fontsize=FONTE)
    return doc


def _escanear(origem: fitz.Page, destino: fitz.Document, dpi: int = 150,
              angulo: float = 3) -> None:
    m = fitz.Matrix(dpi / 72, dpi / 72).prerotate(angulo)
    pix = origem.get_pixmap(matrix=m, colorspace=fitz.csGRAY)
    p = destino.new_page(width=595, height=842)
    p.insert_image(p.rect, pixmap=pix)


def digitalizado() -> fitz.Document:
    base = fitz.open()
    _pagina_sentenca(base)
    doc = fitz.open()
    _escanear(base[0], doc)
    return doc


def misto() -> fitz.Document:
    doc = fitz.open()
    _pagina_sentenca(doc, 1)
    base = fitz.open()
    _pagina_sentenca(base, 2)
    _escanear(base[0], doc)
    doc.new_page(width=595, height=842)  # página em branco
    return doc


def protegido() -> bytes:
    return texto().tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                           user_pw="segredo", owner_pw="dono")


GERADO_EM = "geradoEm: 2026-10-30T14:03:00-03:00"


def fixar_data(md: str) -> str:
    """geradoEm muda a cada conversão; os .md esperados usam data fixa."""
    import re
    return re.sub(r"^geradoEm: .*$", GERADO_EM, md, count=1, flags=re.M)


def gerar() -> None:
    from app.conversor import converter, montar_markdown

    EXEMPLOS.mkdir(exist_ok=True)
    for nome, doc in (("texto", texto()), ("digitalizado", digitalizado()),
                      ("misto", misto())):
        pdf = EXEMPLOS / f"{nome}.pdf"
        doc.save(pdf, garbage=3, deflate=True)
        # Markdown esperado: revisado à mão na criação; regerar só se a
        # mudança na saída for intencional
        md = montar_markdown(pdf.name, None, None, converter(str(pdf)))
        (EXEMPLOS / f"{nome}.md").write_text(fixar_data(md), "utf-8")
    (EXEMPLOS / "protegido.pdf").write_bytes(protegido())


if __name__ == "__main__":
    gerar()
    print("ok:", ", ".join(sorted(p.name for p in EXEMPLOS.iterdir())))
