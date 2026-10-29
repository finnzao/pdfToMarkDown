"""Inspeciona o ZIP de autos que o PJe entrega quando o processo é grande:
quantos volumes (PDFs) há, se cada volume tem a própria capa do PJe, onde
começam e terminam as peças (rodapé "Num. X - Pág. Y") e se alguma peça
fica partida entre dois volumes. Só lê a camada de texto — não roda OCR.

    python ferramentas/inspecionar_zip.py autos.zip [--json saida.json]

Aceita também uma pasta com PDFs ou um PDF solto. Não imprime conteúdo das
peças: só Ids, contagens, tamanhos e o número do processo lido da capa.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.pipeline.capa import eh_capa, parsear_capa  # noqa: E402
from app.pipeline.ocr import precisa_ocr  # noqa: E402
from app.pipeline.processador import _detectar_capa  # noqa: E402
from app.pipeline.segmentacao import _RE_RODAPE, id_da_pagina  # noqa: E402


@dataclass
class Volume:
    nome: str
    bytes: int
    paginas: int
    capa: bool
    paginas_capa: int
    numero_processo: str
    entradas_indice: int        # documentos listados no índice da capa
    ids: list[str]              # Ids de rodapé em ordem de aparição
    primeiro_rodape: str        # "Id · Pág." da 1ª página com rodapé
    ultimo_rodape: str
    paginas_sem_rodape: int     # fora da capa
    paginas_ocr: int            # sem camada de texto, imagem grande
    mb_por_pagina: float
    avisos: list[str] = field(default_factory=list)


def _rodape(texto: str) -> str:
    m = None
    for m in _RE_RODAPE.finditer(texto):
        pass
    return f"{m.group(1)} · pág. {m.group(2)}" if m else ""


def inspecionar_pdf(nome: str, dados: bytes) -> Volume:
    doc = fitz.open(stream=dados, filetype="pdf")
    try:
        textos = [p.get_text("text", sort=True) for p in doc]
        ocr = sum(1 for i, t in enumerate(textos) if precisa_ocr(doc[i], t))
    finally:
        doc.close()
    n = len(textos)
    capa = bool(textos) and eh_capa(textos[0])
    primeira_corpo = _detectar_capa(textos)
    meta = parsear_capa(textos[: primeira_corpo - 1]) if primeira_corpo > 1 else None
    corpo = textos[primeira_corpo - 1:]
    ids: list[str] = []
    sem_rodape = 0
    for t in corpo:
        i = id_da_pagina(t)
        if i is None:
            sem_rodape += 1
        elif not ids or ids[-1] != i:
            ids.append(i)
    com_rodape = [_rodape(t) for t in corpo if _rodape(t)]
    avisos = []
    if not capa:
        avisos.append("sem capa do PJe na 1ª página")
    if len(ids) != len(set(ids)):
        avisos.append("Id de peça aparece em trechos não contíguos")
    if sem_rodape:
        avisos.append(f"{sem_rodape} página(s) do corpo sem rodapé Num./Pág.")
    return Volume(
        nome=nome, bytes=len(dados), paginas=n, capa=capa,
        paginas_capa=primeira_corpo - 1,
        numero_processo=(meta.numero if meta else "") or "",
        entradas_indice=len(meta.indice) if meta else 0,
        ids=ids,
        primeiro_rodape=com_rodape[0] if com_rodape else "",
        ultimo_rodape=com_rodape[-1] if com_rodape else "",
        paginas_sem_rodape=sem_rodape, paginas_ocr=ocr,
        mb_por_pagina=round(len(dados) / 1024 / 1024 / max(n, 1), 3),
        avisos=avisos,
    )


def _ordem_natural(nome: str) -> list:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", nome)]


def carregar(caminho: Path) -> list[tuple[str, bytes]]:
    if caminho.is_dir():
        pdfs = sorted(caminho.glob("*.pdf"), key=lambda p: _ordem_natural(p.name))
        return [(p.name, p.read_bytes()) for p in pdfs]
    if caminho.suffix.lower() == ".pdf":
        return [(caminho.name, caminho.read_bytes())]
    with zipfile.ZipFile(caminho) as z:
        nomes = sorted((i.filename for i in z.infolist()
                        if not i.is_dir() and i.filename.lower().endswith(".pdf")),
                       key=_ordem_natural)
        outros = [i.filename for i in z.infolist()
                  if not i.is_dir() and not i.filename.lower().endswith(".pdf")]
        if outros:
            print(f"(ignorados no ZIP, não são PDF: {outros})")
        return [(n, z.read(n)) for n in nomes]


def resumo_conjunto(vols: list[Volume]) -> dict:
    """O que o fatiador do cliente precisa saber do conjunto."""
    idx_cap = [v for v in vols if v.capa]
    partidas = [
        f"{a.nome} -> {b.nome} (Id {a.ids[-1]})"
        for a, b in zip(vols, vols[1:])
        if a.ids and b.ids and a.ids[-1] == b.ids[0]
    ]
    ids_todos = [i for v in vols for i in v.ids]
    return {
        "volumes": len(vols),
        "paginas_total": sum(v.paginas for v in vols),
        "mb_total": round(sum(v.bytes for v in vols) / 1024 / 1024, 1),
        "volumes_com_capa": [v.nome for v in idx_cap],
        "cada_volume_tem_capa": len(idx_cap) == len(vols),
        "indice_so_no_primeiro": bool(vols) and vols[0].entradas_indice > 0
        and all(v.entradas_indice == 0 for v in vols[1:]),
        "pecas_distintas": len(set(ids_todos)),
        "pecas_partidas_entre_volumes": partidas,
        "numeros_processo": sorted({v.numero_processo for v in vols if v.numero_processo}),
        "paginas_ocr_total": sum(v.paginas_ocr for v in vols),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("caminho", type=Path, help="ZIP do PJe, pasta com PDFs ou um PDF")
    ap.add_argument("--json", type=Path, help="grava o relatório completo em JSON")
    args = ap.parse_args()

    vols = [inspecionar_pdf(n, d) for n, d in carregar(args.caminho)]
    if not vols:
        sys.exit("nenhum PDF encontrado")

    print(f"{'volume':<40} {'MB':>7} {'págs':>5} {'capa':>5} {'índice':>6} "
          f"{'peças':>5} {'s/rod':>5} {'OCR':>5}  primeiro rodapé -> último")
    for v in vols:
        print(f"{v.nome[:40]:<40} {v.bytes / 1024 / 1024:>7.1f} {v.paginas:>5} "
              f"{'sim' if v.capa else 'não':>5} {v.entradas_indice:>6} {len(v.ids):>5} "
              f"{v.paginas_sem_rodape:>5} {v.paginas_ocr:>5}  "
              f"{v.primeiro_rodape or '-'} -> {v.ultimo_rodape or '-'}")
        for a in v.avisos:
            print(f"    ! {a}")
    print()
    for k, val in resumo_conjunto(vols).items():
        print(f"{k}: {val}")

    if args.json:
        args.json.write_text(json.dumps(
            {"volumes": [asdict(v) for v in vols], "conjunto": resumo_conjunto(vols)},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nrelatório em {args.json}")


if __name__ == "__main__":
    main()
