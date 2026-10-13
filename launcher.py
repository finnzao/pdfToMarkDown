"""Ponto de entrada do Extrator.exe (gerado pelo build.bat)."""
import multiprocessing
import threading
import webbrowser

import uvicorn

from app.main import app

URL = "http://127.0.0.1:8077"

if __name__ == "__main__":
    multiprocessing.freeze_support()  # workers de OCR no .exe (spawn)
    print(f"Extrator PDF Juridico rodando em {URL}")
    print("Para encerrar, feche esta janela.")
    # Se a porta ja estiver em uso (programa ja aberto), o servidor sai e o
    # navegador abre na instancia que ja esta rodando.
    threading.Timer(1.5, webbrowser.open, [URL]).start()
    uvicorn.run(app, host="127.0.0.1", port=8077, log_level="warning")
