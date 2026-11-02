import os
from contextlib import contextmanager
from datetime import date, datetime

import requests
from dotenv import load_dotenv

load_dotenv()

JT_API_URL = os.getenv(
    "JT_API_URL",
    "https://jt-api.rockettechbr.com.br"
).rstrip("/")

JT_API_KEY = os.getenv("JT_API_KEY")

if not JT_API_KEY:
    raise RuntimeError("JT_API_KEY não configurada.")


# OIDs principais do PostgreSQL que precisam voltar como objetos Python
# porque o main.py atual chama .isoformat() em datas.
OID_DATE = 1082
OID_TIMESTAMP = 1114
OID_TIMESTAMPTZ = 1184


def _serializar_param(valor):
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def _converter_valor(valor, oid):
    if valor is None:
        return None

    if oid == OID_DATE and isinstance(valor, str):
        try:
            return date.fromisoformat(valor)
        except ValueError:
            return valor

    if oid in (OID_TIMESTAMP, OID_TIMESTAMPTZ) and isinstance(valor, str):
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return valor

    return valor


class APICursor:
    def __init__(self, session):
        self.session = session
        self._rows = []
        self._index = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, comando, params=None):
        payload = {
            "sql": comando,
            "params": [
                _serializar_param(v)
                for v in (params or [])
            ],
        }

        resposta = self.session.post(
            f"{JT_API_URL}/query",
            json=payload,
            timeout=(15, 120),
        )

        if not resposta.ok:
            raise RuntimeError(
                f"JT API /query retornou HTTP {resposta.status_code}: "
                f"{resposta.text}"
            )

        dados = resposta.json()
        tipos = dados.get("tipos") or []
        brutas = dados.get("rows") or []

        self._rows = [
            tuple(
                _converter_valor(
                    valor,
                    tipos[i] if i < len(tipos) else None
                )
                for i, valor in enumerate(row)
            )
            for row in brutas
        ]

        self._index = 0
        return self

    def fetchone(self):
        if self._index >= len(self._rows):
            return None

        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        if self._index >= len(self._rows):
            return []

        rows = self._rows[self._index:]
        self._index = len(self._rows)
        return rows


class APIConnection:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": JT_API_KEY,
            "Content-Type": "application/json",
        })

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.session.close()
        return False

    def cursor(self):
        return APICursor(self.session)


@contextmanager
def get_conn():
    with APIConnection() as conn:
        yield conn
