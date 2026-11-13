"""Testes da API /v1 (Fórum Hub).

    python -m unittest discover -s tests -v

Os processos reais (dados sigilosos, fora do repositório) são testados se
a pasta em PROCESSOS_REAIS existir (padrão: ~/Downloads/processos).
"""
from __future__ import annotations

import difflib
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

CHAVE = "chave-de-teste"
ORIGEM = "http://localhost:3000"
os.environ.update(API_CHAVES=f"forumhub:{CHAVE}", API_CORS_ORIGENS=ORIGEM,
                  API_DIR_TEMP=tempfile.mkdtemp(prefix="forumhub-teste-"))

import pymupdf as fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import gerar_exemplos  # noqa: E402
from app import api_v1  # noqa: E402
from app.main import app  # noqa: E402

EXEMPLOS = RAIZ / "exemplos"
AUTH = {"Authorization": f"Bearer {CHAVE}"}
REAIS = Path(os.environ.get("PROCESSOS_REAIS", Path.home() / "Downloads" / "processos"))
NOMES_REAIS = [  # processos de 2008 digitalizados, enviados pelo usuário
    "0001432-95.2008.8.05.0216-1793391577612-14552753-processo.pdf",
    "0001244-05.2008.8.05.0216-1793391576026-14552753-processo.pdf",
    "0001447-64.2008.8.05.0216-1793391618318-14552753-processo.pdf",
]


def pdf(nome: str) -> bytes:
    return (EXEMPLOS / nome).read_bytes()


def esperado(nome: str) -> str:
    return (EXEMPLOS / nome).read_text("utf-8")


def pdf_texto(paginas: int) -> bytes:
    doc = fitz.open()
    for i in range(paginas):
        p = doc.new_page(width=595, height=842)
        p.insert_textbox(fitz.Rect(50, 60, 545, 780),
                         f"Página {i + 1}. " + "Texto jurídico de teste. " * 120,
                         fontsize=10)
    return doc.tobytes()


class TesteAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(app)
        cls.c = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def setUp(self):
        for f in api_v1.cache.pasta.glob("*"):
            f.unlink()
        api_v1._janelas.clear()

    def converter(self, conteudo: bytes, nome: str = "texto.pdf", **kw):
        return self.c.post("/v1/convert", headers={**AUTH, **kw.pop("headers", {})},
                           files={"file": (nome, conteudo, "application/pdf")},
                           data=kw)

    def esperar_lote(self, lid: str, limite: float = 300) -> dict:
        fim = time.time() + limite
        while time.time() < fim:
            r = self.c.get(f"/v1/batches/{lid}", headers=AUTH)
            self.assertEqual(r.status_code, 200)
            if r.json()["status"] in ("done", "failed", "cancelled"):
                return r.json()
            time.sleep(0.2)
        self.fail("lote não terminou")

    # --- conversão síncrona ------------------------------------------------
    def test_pdf_com_texto(self):
        r = self.converter(pdf("texto.pdf"))
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["id"], hashlib.sha256(pdf("texto.pdf")).hexdigest())
        self.assertEqual((j["paginas"], j["ocr"], j["paginasOcr"]), (3, False, []))
        self.assertEqual(gerar_exemplos.fixar_data(j["markdown"]), esperado("texto.md"))
        self.assertTrue(any(a.startswith("Assinatura:") for a in j["avisos"]))
        md = j["markdown"]
        for proibido in ("Num. 12345678", "https://pje", "PODER JUDICIÁRIO",
                         "fa-\nlha", "presta- ção"):
            self.assertNotIn(proibido, md)
        self.assertIn("|JOÃO DA SILVA|Autor|R$ 4.350,00|", md)  # tabela
        self.assertLess(md.index("inversão do ônus"), md.index("Os extratos"))  # colunas

    def test_digitalizado(self):
        r = self.converter(pdf("digitalizado.pdf"), "digitalizado.pdf")
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual((j["ocr"], j["paginasOcr"]), (True, [1]))
        sim = difflib.SequenceMatcher(None, gerar_exemplos.fixar_data(j["markdown"]),
                                      esperado("digitalizado.md")).ratio()
        self.assertGreater(sim, 0.95)

    def test_misto(self):
        r = self.converter(pdf("misto.pdf"), "misto.pdf")
        j = r.json()
        self.assertEqual((j["paginas"], j["paginasOcr"]), (3, [2]))
        self.assertIn("## Página 3\n\n_[sem texto reconhecível]_", j["markdown"])
        self.assertIn("página 3 sem texto reconhecível", j["avisos"])
        sim = difflib.SequenceMatcher(None, gerar_exemplos.fixar_data(j["markdown"]),
                                      esperado("misto.md")).ratio()
        self.assertGreater(sim, 0.95)

    def test_modos_ocr(self):
        j = self.converter(pdf("digitalizado.pdf"), ocr="never").json()
        self.assertFalse(j["ocr"])
        self.assertIn("_[sem texto reconhecível]_", j["markdown"])
        self.assertEqual(self.converter(pdf("texto.pdf"), ocr="always").json()["paginasOcr"],
                         [1, 2, 3])
        r = self.converter(pdf("texto.pdf"), ocr="talvez")
        self.assertEqual((r.status_code, r.json()["erro"]["codigo"]), (400, "PARAMETRO_INVALIDO"))

    def test_accept_markdown_e_metadados_do_nome(self):
        nome = "8000123-45.2023.8.05.0001_sentenca.pdf"
        r = self.converter(pdf("texto.pdf"), nome, headers={"Accept": "text/markdown"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/markdown"))
        self.assertEqual((r.headers["x-paginas"], r.headers["x-ocr"]), ("3", "false"))
        self.assertEqual(len(r.headers["x-id"]), 64)
        self.assertTrue(r.text.startswith(f"---\narquivo: {nome}\n"
                                          "processo: 8000123-45.2023.8.05.0001\n"
                                          "documento: sentenca\npaginas: 3\nocr: false\n"))

    def test_pdf_puro_no_corpo(self):
        r = self.c.post("/v1/convert?processo=8000123-45.2023.8.05.0001&documento=decisao",
                        content=pdf("texto.pdf"),
                        headers={**AUTH, "Content-Type": "application/pdf"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["processo"], r.json()["documento"]),
                         ("8000123-45.2023.8.05.0001", "decisao"))

    def test_erros(self):
        casos = [
            (self.converter(pdf("protegido.pdf")), 422, "PDF_PROTEGIDO"),
            (self.converter(b"%PDF-1.4\nlixo sem estrutura"), 422, "PDF_CORROMPIDO"),
            (self.converter(b"isto nao e pdf"), 400, "ARQUIVO_INVALIDO"),
            (self.c.post("/v1/convert", headers=AUTH, data={"x": "1"}), 400, "ARQUIVO_INVALIDO"),
            (self.c.post("/v1/convert", files={"file": ("a.pdf", pdf("texto.pdf"))}),
             401, "NAO_AUTORIZADO"),
            (self.c.get("/v1/batches/naoexiste", headers=AUTH), 404, "LOTE_NAO_ENCONTRADO"),
        ]
        for r, status, codigo in casos:
            self.assertEqual(r.status_code, status, r.text)
            erro = r.json()["erro"]
            self.assertEqual(erro["codigo"], codigo)
            self.assertTrue(erro["mensagem"])
            self.assertIn("detalhes", erro)

    def test_limite_sincrono(self):
        antigo = api_v1.LIMITE_SYNC_PAGINAS
        api_v1.LIMITE_SYNC_PAGINAS = 2
        try:
            r = self.converter(pdf("texto.pdf"))
        finally:
            api_v1.LIMITE_SYNC_PAGINAS = antigo
        self.assertEqual((r.status_code, r.json()["erro"]["codigo"]), (413, "MUITO_GRANDE"))
        self.assertIn("/v1/batches", r.json()["erro"]["mensagem"])

    def test_limite_requisicoes(self):
        antigo = api_v1.LIMITE_REQ_MIN
        api_v1.LIMITE_REQ_MIN = 2
        try:
            codigos = [self.c.get("/v1/batches/x", headers=AUTH).status_code for _ in range(3)]
            r = self.c.get("/v1/batches/x", headers=AUTH)
        finally:
            api_v1.LIMITE_REQ_MIN = antigo
        self.assertEqual(codigos, [404, 404, 429])
        self.assertEqual(r.json()["erro"]["codigo"], "LIMITE_EXCEDIDO")
        self.assertTrue(r.headers["retry-after"].isdigit())

    def test_desempenho_texto(self):
        t = time.perf_counter()
        r = self.converter(pdf_texto(20), "vinte.pdf")
        self.assertEqual(r.json()["paginas"], 20)
        self.assertLess(time.perf_counter() - t, 3.0)

    # --- cache --------------------------------------------------------------
    def test_cache_por_hash(self):
        chamadas = []
        original = api_v1.converter

        def contar(*a):
            chamadas.append(a)
            return original(*a)

        api_v1.converter = contar
        try:
            a = self.converter(pdf("misto.pdf"), "a.pdf").json()
            b = self.converter(pdf("misto.pdf"), "outro-nome.pdf").json()
        finally:
            api_v1.converter = original
        self.assertEqual(len(chamadas), 1)
        self.assertEqual((a["cache"], b["cache"]), (False, True))
        self.assertEqual(a["id"], b["id"])
        self.assertIn("arquivo: outro-nome.pdf", b["markdown"])  # front-matter é por pedido

    def test_cache_cheio_continua_sem_salvar(self):
        antigo = api_v1.cache.maximo
        api_v1.cache.maximo = 10  # bytes: nada cabe
        try:
            for _ in range(2):
                r = self.converter(pdf("texto.pdf"))
                self.assertEqual(r.status_code, 200)
                self.assertFalse(r.json()["cache"])
            self.assertTrue(api_v1.cache.cheio)
            self.assertEqual(list(api_v1.cache.pasta.glob("*.json")), [])
        finally:
            api_v1.cache.maximo = antigo
        self.converter(pdf("texto.pdf"))  # com espaço, volta a gravar
        self.assertFalse(api_v1.cache.cheio)
        self.assertEqual(len(list(api_v1.cache.pasta.glob("*.json"))), 1)

    def test_expiracao(self):
        self.converter(pdf("texto.pdf"))
        lid = self.c.post("/v1/batches", headers=AUTH,
                          files=[("files[]", ("a.pdf", pdf("texto.pdf")))]).json()["batchId"]
        self.esperar_lote(lid)
        self.c.portal.call(api_v1.limpar, time.time() + api_v1.RETENCAO + 60)
        self.assertEqual(self.c.get(f"/v1/batches/{lid}", headers=AUTH).status_code, 404)
        self.assertFalse((api_v1.BASE / "lotes" / lid).exists())
        self.assertEqual(list(api_v1.cache.pasta.glob("*.json")), [])

    # --- lotes ----------------------------------------------------------------
    def test_lote_zip_com_subpastas(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("8000123-45.2023.8.05.0001_sentenca.pdf", pdf("texto.pdf"))
            z.writestr("pasta/sub/misto.pdf", pdf("misto.pdf"))
            z.writestr("pasta/protegido.pdf", pdf("protegido.pdf"))
            z.writestr("pasta/quebrado.pdf", b"%PDF-1.4 lixo")
            z.writestr("pasta/leia-me.txt", b"ignorado")
            z.writestr("../fora.pdf", pdf("texto.pdf"))  # tentativa de sair da pasta
        r = self.c.post("/v1/batches", headers=AUTH,
                        files={"zip": ("lote.zip", buf.getvalue(), "application/zip")})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual((r.json()["total"], r.json()["status"]), (4, "queued"))
        lid = r.json()["batchId"]
        s = self.esperar_lote(lid)
        self.assertEqual((s["status"], s["feitos"], s["falhas"]), ("done", 4, 2))
        erros = {i["arquivo"]: i["erro"]["codigo"] for i in s["itens"] if i["erro"]}
        self.assertEqual(erros, {"pasta/protegido.pdf": "PDF_PROTEGIDO",
                                 "pasta/quebrado.pdf": "PDF_CORROMPIDO"})
        self.assertFalse(any("fora" in i["arquivo"] for i in s["itens"]))  # recusado

        r = self.c.get(f"/v1/batches/{lid}/result", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertEqual(sorted(z.namelist()), [
            "8000123-45.2023.8.05.0001_sentenca.md", "manifest.json",
            "pasta/sub/misto.md"])
        man = json.loads(z.read("manifest.json"))
        self.assertEqual(len(man["itens"]), 4)
        self.assertIn("processo: 8000123-45.2023.8.05.0001",
                      z.read("8000123-45.2023.8.05.0001_sentenca.md").decode())

        r = self.c.get(f"/v1/batches/{lid}/items/pasta/sub/misto.pdf",
                       headers={**AUTH, "Accept": "text/markdown"})
        self.assertEqual((r.status_code, r.headers["x-paginas"]), (200, "3"))
        r = self.c.get(f"/v1/batches/{lid}/items/pasta/protegido.pdf", headers=AUTH)
        self.assertEqual(r.json()["erro"]["codigo"], "PDF_PROTEGIDO")

        for _ in range(2):  # idempotente
            self.assertEqual(self.c.delete(f"/v1/batches/{lid}", headers=AUTH).status_code, 204)
        self.assertEqual(self.c.get(f"/v1/batches/{lid}/result", headers=AUTH).status_code, 404)
        self.assertFalse((api_v1.BASE / "lotes" / lid).exists())

    def test_formato_juridico_e_lotes_md(self):
        r = self.converter(pdf("misto.pdf"), "8000123-45.2023.8.05.0001.pdf", formato="juridico")
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertIn("Instruções de Segurança", j["markdown"])
        self.assertIn("8000123-45.2023.8.05.0001", j["autos"])
        self.assertNotIn("Instruções de Segurança", j["autos"])
        self.assertEqual((j["paginas"], j["ocr"]), (3, True))
        self.assertEqual(self.converter(pdf("texto.pdf"), formato="pdf").status_code, 400)
        self.assertEqual(self.converter(pdf("protegido.pdf"), formato="juridico")
                         .json()["erro"]["codigo"], "PDF_PROTEGIDO")

        r = self.c.post("/v1/batches", headers=AUTH, data={"formato": "juridico"},
                        files=[("files[]", ("a.pdf", pdf("texto.pdf"), "application/pdf"))])
        self.assertEqual(r.status_code, 202, r.text)
        lid = r.json()["batchId"]
        self.assertEqual(self.esperar_lote(lid)["status"], "done")
        item = self.c.get(f"/v1/batches/{lid}/items/a.pdf", headers=AUTH).json()
        self.assertIn("<<AUTOS_", item["markdown"])
        self.assertTrue(item["autos"])

        r = self.c.post("/v1/lotes-md", headers=AUTH, json={
            "processos": [{"autos": j["autos"]}, {"autos": item["autos"]}], "limiteKb": 100})
        self.assertEqual(r.status_code, 200, r.text)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertEqual(z.namelist(), ["MDemlote1.md"])
        doc = z.read("MDemlote1.md").decode()
        self.assertIn("PROCESSO 2 de 2", doc)
        self.assertEqual(doc.count("<!-- PROCESSO "), 2)
        self.assertEqual(self.c.post("/v1/lotes-md", headers=AUTH,
                                     json={"processos": []}).status_code, 400)

    def test_lote_files_e_callback_assinado(self):
        recebidos = []

        class Receptor(BaseHTTPRequestHandler):
            def do_POST(self):
                corpo = self.rfile.read(int(self.headers["Content-Length"]))
                recebidos.append((self.headers["X-Signature"], corpo))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Receptor)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = self.c.post("/v1/batches", headers=AUTH, data={
                "callbackUrl": f"http://127.0.0.1:{srv.server_port}/cb"}, files=[
                ("files[]", ("a.pdf", pdf("texto.pdf"))),
                ("files[]", ("a.pdf", pdf("misto.pdf")))])  # nome repetido
            lid = r.json()["batchId"]
            s = self.esperar_lote(lid)
            fim = time.time() + 10
            while not recebidos and time.time() < fim:
                time.sleep(0.1)
        finally:
            srv.shutdown()
        self.assertEqual([i["arquivo"] for i in s["itens"]], ["a.pdf", "a (2).pdf"])
        assinatura, corpo = recebidos[0]
        self.assertEqual(assinatura, hmac.new(CHAVE.encode(), corpo, hashlib.sha256).hexdigest())
        self.assertEqual(json.loads(corpo)["status"], "done")

    # --- apoio ------------------------------------------------------------------
    def test_health_openapi_cors(self):
        h = self.c.get("/v1/health").json()  # sem autenticação
        self.assertEqual((h["ok"], h["versao"], h["ocrDisponivel"]), (True, api_v1.VERSAO, True))
        spec = self.c.get("/v1/openapi.json").json()
        self.assertTrue(spec["openapi"].startswith("3.1"))
        self.assertEqual(sorted(spec["paths"]), [
            "/v1/batches", "/v1/batches/{batchId}", "/v1/batches/{batchId}/items/{arquivo}",
            "/v1/batches/{batchId}/result", "/v1/convert", "/v1/health", "/v1/lotes-md"])
        r = self.c.options("/v1/convert", headers={
            "Origin": ORIGEM, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["access-control-allow-origin"], ORIGEM)
        self.assertNotIn("access-control-allow-private-network", r.headers)
        r = self.c.options("/v1/convert", headers={
            "Origin": ORIGEM, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(r.headers["access-control-allow-private-network"], "true")
        r = self.converter(pdf("texto.pdf"), headers={"Origin": ORIGEM})
        self.assertIn("X-Paginas", r.headers["access-control-expose-headers"])

    # --- processos reais (fora do repositório) -----------------------------
    @unittest.skipUnless(all((REAIS / n).exists() for n in NOMES_REAIS),
                         f"processos reais não encontrados em {REAIS}")
    def test_processos_reais(self):
        for nome in NOMES_REAIS:
            with self.subTest(nome=nome[:25]):
                t = time.perf_counter()
                r = self.converter((REAIS / nome).read_bytes(), nome)
                dt = time.perf_counter() - t
                self.assertEqual(r.status_code, 200, r.text[:300])
                j = r.json()
                md = j["markdown"]
                for n in range(1, j["paginas"] + 1):
                    self.assertIn(f"\n## Página {n}\n", md)
                self.assertNotIn("Assinado eletronicamente por", md)
                self.assertNotIn("Num. ", md)
                sem_texto = sum("sem texto reconhecível" in a for a in j["avisos"])
                self.assertLessEqual(sem_texto, 2)  # só páginas que são só carimbo
                if j["totalPaginasOcr"]:
                    self.assertLess(dt / j["totalPaginasOcr"], 2.0)
        # o de 55 páginas é quase todo digitalizado
        self.assertGreaterEqual(j["totalPaginasOcr"], 40)


if __name__ == "__main__":
    unittest.main()
