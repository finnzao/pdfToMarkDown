"""Segundo motor de OCR, opcional: RapidOCR (modelos PP-OCR em ONNX, CPU).

Só é usado nas páginas em que o Tesseract vai mal (ver `ocr._ocr_indice`).
Roda local: os modelos vêm dentro do pacote `rapidocr`, nada é baixado nem
enviado. Sem o pacote instalado (`pip install -r requirements-ocr2.txt`),
`disponivel()` é False e o pipeline segue só com o Tesseract.
"""
from __future__ import annotations

import importlib.util
import logging

import numpy as np
import pymupdf as fitz

# PP-OCR reduz a imagem para no máximo 2000 px de lado; A4 a 200 dpi dá 2339
DPI = 200

_motor = None


def disponivel() -> bool:
    return (importlib.util.find_spec("rapidocr") is not None
            and importlib.util.find_spec("onnxruntime") is not None)


def _obter_motor():
    global _motor
    if _motor is None:
        from rapidocr import RapidOCR
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        # 1 thread: o paralelismo já é por página, um processo por núcleo
        _motor = RapidOCR(params={
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": 1,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        })
    return _motor


def _linhas(caixas, textos) -> list[tuple[float, float, float, float, str]]:
    """Junta as caixas de texto em linhas: (x0, y0, x1, y1, texto) em pontos
    de PDF, de cima para baixo. Caixas na mesma altura e próximas viram uma
    linha; as distantes (outra coluna, outra célula) ficam separadas."""
    k = 72 / DPI
    itens = sorted(
        ((float(c[:, 0].min()) * k, float(c[:, 1].min()) * k,
          float(c[:, 0].max()) * k, float(c[:, 1].max()) * k, t)
         for c, t in zip(caixas, textos)),
        key=lambda b: ((b[1] + b[3]) / 2, b[0]))
    linhas: list[list] = []
    for x0, y0, x1, y1, t in itens:
        meio, alt = (y0 + y1) / 2, y1 - y0
        for u in reversed(linhas[-6:]):
            if u[1] <= meio <= u[3] and max(x0 - u[2], u[0] - x1) < 2 * alt:
                u[4].append((x0, t))
                u[:4] = min(u[0], x0), min(u[1], y0), max(u[2], x1), max(u[3], y1)
                break
        else:
            linhas.append([x0, y0, x1, y1, [(x0, t)]])
    return [(x0, y0, x1, y1, " ".join(t for _, t in sorted(ps)))
            for x0, y0, x1, y1, ps in linhas]


def _paragrafos(linhas: list) -> list[tuple[float, float, float, float, str]]:
    """Linhas uma embaixo da outra, coladas e sobrepostas na horizontal,
    formam um bloco (texto com as linhas separadas por \\n)."""
    blocos: list[list] = []
    for x0, y0, x1, y1, t in linhas:
        alt = y1 - y0
        for b in reversed(blocos[-6:]):
            sobre = min(x1, b[2]) - max(x0, b[0])
            if 0 <= y0 - b[3] < 0.7 * alt and sobre > 0.5 * min(x1 - x0, b[2] - b[0]):
                b[:4] = min(b[0], x0), b[1], max(b[2], x1), y1
                b[4] += "\n" + t
                break
        else:
            blocos.append([x0, y0, x1, y1, t])
    return [tuple(b) for b in blocos]


def ocr_pagina(page: fitz.Page, giro: float = 0, blocos: bool = False) -> str | list:
    """Texto (ou blocos, como em `ocr.ocr_pagina`) da página renderizada e
    girada de `giro` graus."""
    pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72).prerotate(giro),
                          colorspace=fitz.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
    r = _obter_motor()(img[:, :, ::-1])  # BGR, como o OpenCV
    linhas = _linhas(r.boxes, r.txts) if r.txts else []
    return _paragrafos(linhas) if blocos else "\n".join(l[4] for l in linhas)


if __name__ == "__main__":
    c = lambda x0, y0, x1, y1: np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
    ls = _linhas([c(520, 100, 900, 140), c(100, 105, 500, 138), c(100, 200, 400, 240)],
                 ["de Direito", "Juiz", "Processo"])
    assert [l[4] for l in ls] == ["Juiz de Direito", "Processo"], ls
    # duas colunas: caixas distantes na mesma altura não se juntam; linhas
    # coladas na vertical viram um bloco por coluna
    ls = _linhas([c(100, 100, 500, 140), c(1200, 100, 1600, 140),
                  c(100, 145, 500, 185), c(1200, 145, 1600, 185)],
                 ["esq 1", "dir 1", "esq 2", "dir 2"])
    assert [b[4] for b in _paragrafos(ls)] == ["esq 1\nesq 2", "dir 1\ndir 2"], ls
    if not disponivel():
        print("rapidocr não instalado: só a junção de linhas foi testada. ok")
    else:
        import difflib
        ref = ("EXCELENTÍSSIMO SENHOR DOUTOR JUIZ DE DIREITO. JOÃO DA SILVA, "
               "brasileiro, casado, propõe AÇÃO DE INDENIZAÇÃO em face de BANCO "
               "EXEMPLO S.A., pelos fatos e fundamentos a seguir expostos. ") * 4
        doc = fitz.open()
        pg = doc.new_page(width=595, height=842)
        pg.insert_textbox(fitz.Rect(60, 60, 535, 800), ref, fontsize=11)
        texto = ocr_pagina(pg)
        sim = difflib.SequenceMatcher(None, " ".join(ref.split()),
                                      " ".join(texto.split())).ratio()
        print(f"similaridade {sim:.3f}")
        assert sim > 0.95
        print("ok")
