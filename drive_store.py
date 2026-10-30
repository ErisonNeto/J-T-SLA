"""Leitura dos arquivos diarios do SLA no Google Drive, com cache em memoria."""
from __future__ import annotations

import io
import os
import re
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

API = "https://www.googleapis.com/drive/v3/files"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
PATTERN = re.compile(r"^sla_(\d{4}-\d{2}-\d{2})\.csv\.gz$")
COLUNAS = [
    "numero_remessa", "codigo_base", "base_entrega", "data_sla", "no_prazo",
    "status_sla", "rm", "coordenador", "supervisor", "entregador", "turno_rit",
    "cidade_base", "prazo_atual_transferencia", "prazo_atual_entrega", "meta_transit",
    "meta_entrega", "meta_total", "data_expedicao_sc", "data_hora_entrega",
    "data_limite_prazo", "prazo_real_dias",
]

_lock = threading.Lock()
_list_cache = {"until": 0.0, "files": None}
_frame_cache = {}
_status = {"fonte": "Google Drive", "sincronizado_em": None, "aviso": None, "arquivos": 0}


def _tz():
    return ZoneInfo(os.getenv("JT_TIMEZONE", "America/Belem"))


def status_fonte():
    return dict(_status)


def _credentials_path():
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not path:
        local = os.path.join(os.path.dirname(__file__), "google-drive.json")
        if os.path.exists(local):
            path = local
    if not path:
        raise ValueError("Credencial do Google Drive nao configurada.")
    return path


def _session():
    cred = service_account.Credentials.from_service_account_file(_credentials_path(), scopes=SCOPES)
    return AuthorizedSession(cred, refresh_timeout=20)


def _folder_id():
    folder = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1pIXZ5unObZaSJPHlt65hwTMHXI-UOs8U").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder):
        raise ValueError("GOOGLE_DRIVE_FOLDER_ID invalido.")
    return folder


def _listar_arquivos(force=False):
    now = time.monotonic()
    ttl = max(30, int(os.getenv("DRIVE_CACHE_SECONDS", "300")))
    with _lock:
        if not force and _list_cache["files"] is not None and now < _list_cache["until"]:
            return list(_list_cache["files"])
        folder = _folder_id()
        with _session() as s:
            check = s.get(f"{API}/{folder}", params={"fields": "id,mimeType"}, timeout=30)
            check.raise_for_status()
            if check.json().get("mimeType") != "application/vnd.google-apps.folder":
                raise ValueError("O ID configurado nao corresponde a uma pasta do Drive.")
            files, token = [], None
            while True:
                params = {
                    "q": f"'{folder}' in parents and trashed = false",
                    "pageSize": 1000,
                    "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,size,md5Checksum)",
                }
                if token:
                    params["pageToken"] = token
                r = s.get(API, params=params, timeout=30)
                r.raise_for_status()
                body = r.json()
                files.extend(body.get("files", []))
                token = body.get("nextPageToken")
                if not token:
                    break
        reconhecidos = [f for f in files if PATTERN.fullmatch(f.get("name", ""))]
        nomes = [f["name"] for f in reconhecidos]
        if len(nomes) != len(set(nomes)):
            raise ValueError("Ha arquivos SLA duplicados com o mesmo nome no Drive.")
        _list_cache.update(until=now + ttl, files=reconhecidos)
        _status.update(sincronizado_em=datetime.now(_tz()).isoformat(), aviso=None, arquivos=len(reconhecidos))
        return list(reconhecidos)


def datas_disponiveis():
    return sorted(date.fromisoformat(PATTERN.fullmatch(f["name"]).group(1)) for f in _listar_arquivos())


def _download(file):
    max_mb = max(1, int(os.getenv("DRIVE_MAX_FILE_MB", "25")))
    limit = max_mb * 1024 * 1024
    if int(file.get("size") or 0) > limit:
        raise ValueError(f"{file['name']} excede o limite de {max_mb} MB compactado.")
    with _session() as s:
        r = s.get(f"{API}/{file['id']}", params={"alt": "media"}, stream=True, timeout=45)
        r.raise_for_status()
        buf = io.BytesIO()
        for chunk in r.iter_content(65536):
            if buf.tell() + len(chunk) > limit:
                raise ValueError(f"{file['name']} excede o limite configurado.")
            buf.write(chunk)
        return buf.getvalue()


def _normalizar(df):
    faltando = [c for c in COLUNAS if c not in df.columns]
    if faltando:
        raise ValueError("CSV SLA sem colunas obrigatorias: " + ", ".join(faltando))
    df = df[COLUNAS].copy()
    for c in ["numero_remessa", "codigo_base", "base_entrega", "data_sla", "status_sla", "rm", "coordenador", "supervisor", "entregador", "cidade_base"]:
        df[c] = df[c].fillna("").astype(str).replace({"<NA>": "", "nan": "", "None": ""})
    df["no_prazo"] = df["no_prazo"].astype(str).str.lower().isin(["true", "1", "t", "yes", "y"])
    for c in ["turno_rit", "prazo_atual_transferencia", "prazo_atual_entrega", "meta_transit", "meta_entrega", "meta_total", "prazo_real_dias"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["data_expedicao_sc", "data_hora_entrega", "data_limite_prazo"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _carregar_arquivo(file):
    key = (file["id"], file.get("modifiedTime"), file.get("md5Checksum"))
    with _lock:
        cached = _frame_cache.get(key)
    if cached is not None:
        return cached.copy()
    raw = _download(file)
    df = pd.read_csv(io.BytesIO(raw), compression="gzip", low_memory=False)
    df = _normalizar(df)
    esperado = PATTERN.fullmatch(file["name"]).group(1)
    if not df.empty and not (df["data_sla"] == esperado).all():
        raise ValueError(f"Data interna de {file['name']} nao corresponde ao nome do arquivo.")
    with _lock:
        _frame_cache[key] = df
        if len(_frame_cache) > 40:
            for old in list(_frame_cache)[:-30]:
                _frame_cache.pop(old, None)
    return df.copy()


def carregar_periodo(data_inicio=None, data_fim=None):
    try:
        files = _listar_arquivos()
        if not files:
            return pd.DataFrame(columns=COLUNAS)
        datas = [date.fromisoformat(PATTERN.fullmatch(f["name"]).group(1)) for f in files]
        if data_inicio is None:
            data_inicio = min(datas)
        if data_fim is None:
            data_fim = max(datas)
        if data_inicio > data_fim:
            data_inicio, data_fim = data_fim, data_inicio
        escolhidos, total = [], 0
        total_limit = max(10, int(os.getenv("DRIVE_MAX_TOTAL_MB", "150"))) * 1024 * 1024
        for f in files:
            dt = date.fromisoformat(PATTERN.fullmatch(f["name"]).group(1))
            if data_inicio <= dt <= data_fim:
                total += int(f.get("size") or 0)
                if total > total_limit:
                    raise ValueError("Periodo selecionado excede o limite de leitura; reduza o intervalo.")
                escolhidos.append(f)
        frames = [_carregar_arquivo(f) for f in sorted(escolhidos, key=lambda x: x["name"])]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUNAS)
    except Exception as exc:
        _status["aviso"] = "Falha ao ler o Drive. " + (str(exc) if isinstance(exc, ValueError) else "Confira credencial, API e compartilhamento da pasta.")
        raise


def carregar_ultima_data():
    files = _listar_arquivos()
    if not files:
        return pd.DataFrame(columns=COLUNAS)
    ultimo = max(files, key=lambda f: PATTERN.fullmatch(f["name"]).group(1))
    return _carregar_arquivo(ultimo)
