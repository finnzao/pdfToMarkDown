/**
 * Cliente de exemplo da API /v1 (fetch nativo, Node 22+), para adaptar no
 * backend do Fórum Hub. No navegador as chamadas são as mesmas: troque
 * openAsBlob pelo File do File System Access API e monte o zip com uma
 * biblioteca (fflate, JSZip).
 *
 *   API_URL=http://localhost:8077 API_CHAVE=sua-chave \
 *     node exemplos/cliente-forumhub.ts exemplos/texto.pdf          # 1 PDF
 *   ... node exemplos/cliente-forumhub.ts a.pdf b.pdf c.pdf          # lote
 *
 * Node 22: acrescente --experimental-strip-types (Node 23.6+ já roda .ts).
 */
import { openAsBlob } from "node:fs";
import { writeFile } from "node:fs/promises";
import { basename } from "node:path";

const API = process.env.API_URL ?? "http://localhost:8077";
const auth = { Authorization: `Bearer ${process.env.API_CHAVE ?? ""}` };

type ErroApi = {
  erro: { codigo: string; mensagem: string; detalhes: Record<string, unknown> };
};
type Item = {
  arquivo: string;
  status: "done" | "failed" | "pending";
  paginas: number | null;
  ocr: boolean | null;
  erro: { codigo: string; mensagem: string } | null;
};
type Lote = {
  batchId: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  total: number;
  feitos: number;
  falhas: number;
  atual: string | null;
  itens: Item[];
};

const esperar = (ms: number) => new Promise((ok) => setTimeout(ok, ms));

/** Erros vêm sempre como { erro: { codigo, mensagem } }; a mensagem é para o usuário. */
async function falhar(r: Response): Promise<never> {
  const corpo = (await r.json().catch(() => null)) as ErroApi | null;
  throw new Error(corpo?.erro?.mensagem ?? `HTTP ${r.status}`);
}

/** N = 1: converte um PDF e grava o .md ao lado. */
export async function converterUm(caminho: string): Promise<string> {
  const form = new FormData();
  form.append("file", await openAsBlob(caminho), basename(caminho));
  form.append("ocr", "auto");
  const r = await fetch(`${API}/v1/convert`, {
    method: "POST",
    headers: { ...auth, Accept: "text/markdown" },
    body: form,
  });
  if (!r.ok) await falhar(r); // 413 MUITO_GRANDE: mande pelo lote
  const destino = caminho.replace(/\.pdf$/i, "") + ".md";
  await writeFile(destino, await r.text(), "utf8");
  console.log(
    `${basename(caminho)}: ${r.headers.get("X-Paginas")} página(s), ` +
      `OCR ${r.headers.get("X-Ocr")}, id ${r.headers.get("X-Id")?.slice(0, 12)} -> ${destino}`,
  );
  return destino;
}

/** N > 1: cria o lote, acompanha a cada 2,5 s e baixa o zip com os .md. */
export async function converterLote(caminhos: string[], destinoZip: string): Promise<Lote> {
  const form = new FormData();
  for (const c of caminhos) form.append("files[]", await openAsBlob(c), basename(c));
  // (ou um zip: form.append("zip", blobDoZip, "lote.zip") — mantém subpastas)
  const r = await fetch(`${API}/v1/batches`, { method: "POST", headers: auth, body: form });
  if (r.status !== 202) await falhar(r);
  const { batchId } = (await r.json()) as { batchId: string };

  let lote: Lote;
  while (true) {
    await esperar(2500);
    const s = await fetch(`${API}/v1/batches/${batchId}`, { headers: auth });
    if (s.status === 429) {
      await esperar(Number(s.headers.get("Retry-After") ?? 5) * 1000);
      continue;
    }
    if (!s.ok) await falhar(s);
    lote = (await s.json()) as Lote;
    console.log(`${lote.status} ${lote.feitos}/${lote.total} ${lote.atual ?? ""}`);
    if (lote.status !== "queued" && lote.status !== "running") break;
  }

  for (const i of lote.itens) {
    if (i.erro) console.warn(`  falhou ${i.arquivo}: ${i.erro.mensagem}`);
  }
  if (lote.feitos > lote.falhas) {
    const z = await fetch(`${API}/v1/batches/${batchId}/result`, { headers: auth });
    if (!z.ok) await falhar(z);
    await writeFile(destinoZip, Buffer.from(await z.arrayBuffer()));
    console.log(`zip com os .md e manifest.json -> ${destinoZip}`);
  }
  // opcional: libera o servidor antes das 24 h de retenção
  await fetch(`${API}/v1/batches/${batchId}`, { method: "DELETE", headers: auth });
  return lote;
}

async function main() {
  const arquivos = process.argv.slice(2);
  if (arquivos.length === 0) {
    console.log("uso: node exemplos/cliente-forumhub.ts arquivo.pdf [mais.pdf ...]");
  } else if (arquivos.length === 1) {
    await converterUm(arquivos[0]);
  } else {
    await converterLote(arquivos, "resultado-lote.zip");
  }
}

main().catch((e: Error) => {
  console.error("erro:", e.message);
  process.exit(1);
});
