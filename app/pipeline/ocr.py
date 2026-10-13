"""OCR local de páginas escaneadas (Tesseract embutido no PyMuPDF).

Só o arquivo de idioma `tessdata/por.traineddata` é externo — o motor
Tesseract vem compilado dentro da wheel do PyMuPDF. Nada sai da máquina.

Decisões (medidas em páginas sintéticas degradadas, ver histórico):
- tessdata_fast: mesma fidelidade que tessdata_best e 4x mais rápido;
- 300 dpi de renderização: ganho nulo acima disso;
- deskew por perfil de projeção: o Tesseract tolera ~2° sozinho; a 4°
  perde 3% e a 6° perde 15% — com deskew volta a 99%, por 0,2 s/página.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import pymupdf as fitz
import numpy as np

TESSDATA = str(Path(__file__).resolve().parent / "tessdata")
IDIOMA = "por"
DPI = 300

# Página "sem texto": camada de texto menor que isso (o rodapé do PJe
# "Num. X - Pág. Y" sozinho tem ~40 caracteres) e imagem cobrindo a página.
_MAX_CHARS_SEM_TEXTO = 200
_MIN_COBERTURA_IMAGEM = 0.2
_ANGULO_MINIMO = 1.0  # abaixo disso o Tesseract corrige sozinho

Progresso = Callable[[int, int], None]  # (páginas OCRizadas, total a OCRizar)


def precisa_ocr(page: fitz.Page, texto: str) -> bool:
    """Camada de texto ínfima + imagem grande = página escaneada."""
    if len(texto.strip()) > _MAX_CHARS_SEM_TEXTO:
        return False
    area_pagina = abs(page.rect) or 1.0
    area_imgs = sum(abs(fitz.Rect(i["bbox"])) for i in page.get_image_info())
    return area_imgs / area_pagina >= _MIN_COBERTURA_IMAGEM


def estimar_angulo(page: fitz.Page, dpi: int = 72) -> float:
    """Ângulo (graus) que, aplicado à página, alinha as linhas de texto na
    horizontal: maximiza a variação do perfil de projeção horizontal.
    Busca grossa de 0,5° em ±6° e refino de 0,1°."""
    def score(ang: float) -> float:
        m = fitz.Matrix(dpi / 72, dpi / 72).prerotate(ang)
        pix = page.get_pixmap(matrix=m, colorspace=fitz.csGRAY)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
        linhas = (a < 128).sum(axis=1).astype(float)
        return float((np.diff(linhas) ** 2).sum())

    grosso = max(np.arange(-6, 6.01, 0.5), key=score)
    return float(max(np.arange(grosso - 0.5, grosso + 0.51, 0.1), key=score))


def ocr_pagina(page: fitz.Page) -> tuple[str, float]:
    """Retorna (texto, ângulo corrigido). Mantém a camada de texto original
    (rodapé do PJe) para a segmentação por Id continuar funcionando."""
    ang = estimar_angulo(page)
    if abs(ang) < _ANGULO_MINIMO:
        # full=False: OCR só nas imagens, camada de texto preservada e
        # mesclada em ordem de leitura
        tp = page.get_textpage_ocr(language=IDIOMA, dpi=DPI, full=False,
                                   tessdata=TESSDATA)
        return page.get_text("text", textpage=tp, sort=True), 0.0

    m = fitz.Matrix(DPI / 72, DPI / 72).prerotate(ang)
    pix = page.get_pixmap(matrix=m, colorspace=fitz.csGRAY)
    tmp = fitz.open()
    p = tmp.new_page(width=pix.w * 72 / DPI, height=pix.h * 72 / DPI)
    p.insert_image(p.rect, pixmap=pix)
    tp = p.get_textpage_ocr(language=IDIOMA, dpi=DPI, full=True,
                            tessdata=TESSDATA)
    texto = p.get_text("text", textpage=tp, sort=True)
    # rodapé original por último: `id_da_pagina` usa a última ocorrência
    return texto + "\n" + page.get_text("text"), ang


# palavra (com pontos internos: "S.A."), número ("8.05.0001", "4.350,00")
# ou token curto; letras misturadas com dígitos ("c0ntrato") não contam
_RE_TOKEN_BOM = re.compile(
    r"^[(\"'«\[]?(?:[A-Za-zÀ-ÿºª]+(?:\.[A-Za-zÀ-ÿ]+)*|\d[\d.,/:-]*|.{1,2})"
    r"[.,;:!?)\"'»\]]*$"
)


def qualidade(texto: str) -> float:
    """Proporção de tokens com cara de palavra/número (0–1). O Tesseract
    do PyMuPDF não expõe confiança; isto é um proxy: OCR ruim produz
    tokens como 'c0ntr@t0' ou '|-.,~'."""
    # ponytail: heurística; trocar por confiança real se o PyMuPDF expor
    tokens = texto.split()
    if len(tokens) < 10:
        return 1.0
    bons = sum(1 for t in tokens if _RE_TOKEN_BOM.match(t))
    return bons / len(tokens)


# --- paralelismo por página -------------------------------------------------
_doc: fitz.Document | None = None


def _iniciar(pdf_bytes: bytes) -> None:
    global _doc
    os.environ["OMP_THREAD_LIMIT"] = "1"  # 1 thread por processo
    _doc = fitz.open(stream=pdf_bytes, filetype="pdf")


def _ocr_indice(i: int) -> tuple[int, str, float, float]:
    texto, ang = ocr_pagina(_doc[i])
    return i, texto, ang, qualidade(texto)


def ocr_paginas(pdf_bytes: bytes, indices: list[int],
                progresso: Progresso | None = None) -> dict[int, tuple[str, float, float]]:
    """OCR das páginas `indices` (0-based) em paralelo.
    Retorna {índice: (texto, ângulo, qualidade)}."""
    out: dict[int, tuple[str, float, float]] = {}
    if not indices:
        return out
    workers = min(len(indices), max(1, (os.cpu_count() or 2) - 1), 8)
    feitas = 0
    if workers == 1:
        _iniciar(pdf_bytes)
        for i in indices:
            _, texto, ang, q = _ocr_indice(i)
            out[i] = (texto, ang, q)
            feitas += 1
            if progresso:
                progresso(feitas, len(indices))
        return out
    # ponytail: o PDF inteiro é enviado a cada worker; se a memória pesar,
    # gravar em arquivo temporário e passar o caminho
    with ProcessPoolExecutor(workers, initializer=_iniciar,
                             initargs=(pdf_bytes,)) as ex:
        for fut in as_completed(ex.submit(_ocr_indice, i) for i in indices):
            i, texto, ang, q = fut.result()
            out[i] = (texto, ang, q)
            feitas += 1
            if progresso:
                progresso(feitas, len(indices))
    return out


if __name__ == "__main__":
    import difflib
    import time

    ref = ("EXCELENTÍSSIMO SENHOR DOUTOR JUIZ DE DIREITO. Processo nº "
           "8012345-67.1998.8.05.0001. JOÃO DA SILVA, brasileiro, casado, "
           "propõe AÇÃO DE INDENIZAÇÃO em face de BANCO EXEMPLO S.A., pelos "
           "fatos e fundamentos a seguir expostos. ") * 6
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_textbox(fitz.Rect(60, 60, 535, 800), ref, fontsize=10)
    # página 1: texto nativo; página 2: scan a 150 dpi, 5° torto, com rodapé
    pix = pg.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72).prerotate(5),
                        colorspace=fitz.csGRAY)
    scan = doc.new_page(width=595, height=842)
    scan.insert_image(scan.rect, pixmap=pix)
    scan.insert_text((60, 830), "Num. 12345678 - Pág. 1", fontsize=8)
    assert not precisa_ocr(doc[0], doc[0].get_text())
    assert precisa_ocr(doc[1], doc[1].get_text())
    t = time.time()
    r = ocr_paginas(doc.tobytes(), [1])
    texto, ang, q = r[1]
    sim = difflib.SequenceMatcher(None, " ".join(ref.split()),
                                  " ".join(texto.split())).ratio()
    print(f"ângulo {ang:+.1f}° qualidade {q:.2f} similaridade {sim:.3f} "
          f"{time.time() - t:.1f}s")
    assert abs(ang + 5) < 0.6 and sim > 0.95 and q > 0.9
    assert "Num. 12345678 - Pág. 1" in texto
    assert qualidade("c0ntr@t0 |-.,~ x9z!! a1b2 ..-- ;;; ### %% && p3t1ção "
                     "j|u1z ~~~ r3u") < 0.5
    assert qualidade("Processo nº 8012345-67.1998.8.05.0001 em face de BANCO "
                     "EXEMPLO S.A., R$ 4.350,00 (quatro mil).") > 0.9
    print("ok")
