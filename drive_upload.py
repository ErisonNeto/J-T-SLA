"""Upload/substituicao de CSV.GZ diarios do SLA na pasta privada do Google Drive."""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
DEFAULT_FOLDER = "1pIXZ5unObZaSJPHlt65hwTMHXI-UOs8U"


def _cred_path() -> str:
    env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if env and Path(env).exists():
        return env
    local = Path(__file__).resolve().parent / "google-drive.json"
    if local.exists():
        return str(local)
    raise RuntimeError(
        "Credencial Google nao encontrada. Coloque google-drive.json ao lado dos scripts "
        "ou configure GOOGLE_APPLICATION_CREDENTIALS."
    )


def _folder() -> str:
    folder = os.getenv("GOOGLE_DRIVE_FOLDER_ID", DEFAULT_FOLDER).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID invalido.")
    return folder


def _service():
    creds = service_account.Credentials.from_service_account_file(_cred_path(), scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def enviar_bytes(nome: str, conteudo: bytes, mime: str = "application/gzip") -> str:
    folder = _folder()
    service = _service()
    safe_name = nome.replace("'", "\\'")
    q = f"'{folder}' in parents and name = '{safe_name}' and trashed = false"
    existentes = service.files().list(q=q, fields="files(id,name)", pageSize=10).execute().get("files", [])
    if len(existentes) > 1:
        raise RuntimeError(f"Ha mais de um arquivo chamado {nome} no Drive. Remova a duplicata.")
    media = MediaIoBaseUpload(io.BytesIO(conteudo), mimetype=mime, resumable=False)
    if existentes:
        file_id = existentes[0]["id"]
        service.files().update(fileId=file_id, media_body=media, fields="id").execute()
        acao = "atualizado"
    else:
        body = {"name": nome, "parents": [folder]}
        file_id = service.files().create(body=body, media_body=media, fields="id").execute()["id"]
        acao = "criado"
    print(f"Drive: {nome} {acao} ({file_id}).")
    return file_id
