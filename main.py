# -*- coding: utf-8 -*-
"""
Backend condiviso per il cruscotto REPLACE.

Sostituisce sql.js (che leggeva il database intero nel browser) con un
piccolo server che legge/scrive direttamente il file SQLite sul server,
rispondendo solo con i dati richiesti - cosi' il file puo' crescere senza
che il cruscotto rallenti o vada scaricato/caricato ogni volta.

Due endpoint:
- POST /query   -> esegue una SELECT, ritorna le righe come lista di oggetti
- POST /execute -> esegue INSERT/UPDATE/DELETE, ritorna quante righe toccate

Protetti da una password semplice (header X-API-Key), adatta per un solo
utente - non e' un sistema multi-utente con permessi differenziati.
"""
import os
import sqlite3
from contextlib import contextmanager

from fastapi import FastAPI, Header, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/data/dashboard.db")
API_KEY = os.environ.get("API_KEY")  # da impostare come variabile d'ambiente sul server

# diagnosi temporanea: se API_KEY non arriva, elenca quali variabili
# SONO effettivamente visibili al processo, per capire cosa succede
if not API_KEY:
    visible_vars = sorted(k for k in os.environ.keys() if not k.startswith("RAILWAY_"))
    raise RuntimeError(
        "Variabile d'ambiente API_KEY non impostata. "
        f"Variabili visibili al processo (nomi soltanto): {visible_vars}"
    )

app = FastAPI(title="REPLACE backend")

# indicazione utente: il cruscotto e' un artifact pubblicato su claude.ai,
# che e' un dominio DIVERSO da questo backend - senza CORS il browser
# blocca la richiesta anche se le credenziali sono giuste
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)


def check_key(x_api_key: str | None):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Chiave API mancante o errata")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class SqlRequest(BaseModel):
    sql: str
    params: list = []


@app.post("/query")
def query(req: SqlRequest, x_api_key: str | None = Header(default=None)):
    """SELECT - ritorna le righe come lista di oggetti (stessa forma che
    il cruscotto si aspettava da sql.js)."""
    check_key(x_api_key)
    with get_conn() as conn:
        try:
            cur = conn.execute(req.sql, req.params)
            rows = [dict(r) for r in cur.fetchall()]
            return {"rows": rows}
        except sqlite3.Error as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/execute")
def execute(req: SqlRequest, x_api_key: str | None = Header(default=None)):
    """INSERT/UPDATE/DELETE - scrive davvero sul file, subito visibile a
    chiunque interroghi lo stesso backend dopo (niente piu' 'salva e
    rimanda il file')."""
    check_key(x_api_key)
    with get_conn() as conn:
        try:
            cur = conn.execute(req.sql, req.params)
            conn.commit()
            return {"rowcount": cur.rowcount, "lastrowid": cur.lastrowid}
        except sqlite3.Error as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.post("/upload")
async def upload(file: UploadFile = File(...), x_api_key: str | None = Header(default=None)):
    """Carica (o sostituisce) il file dashboard.db sul server - serve solo
    la prima volta per popolare il volume vuoto, o per un ripristino
    manuale in caso di emergenza. Protetto dalla stessa password."""
    check_key(x_api_key)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "wb") as f:
        content = await file.read()
        f.write(content)
    return {"status": "ok", "bytes_written": len(content)}


@app.get("/health")
def health():
    """Verifica rapida che il server sia su e il database raggiungibile,
    senza serve la password - utile per un primo controllo veloce."""
    exists = os.path.exists(DB_PATH)
    return {"status": "ok", "db_path": DB_PATH, "db_exists": exists}
