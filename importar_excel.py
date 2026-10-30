from __future__ import annotations

import gzip
import io
import re
import unicodedata
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from drive_upload import enviar_bytes

load_dotenv()
PASTA = Path(__file__).resolve().parent.parent / "dados"


def limpar_nome(s):
    s = s.astype("string").str.strip()
    return s.mask(s.isin(["", "0", "0.0", "nan", "NaN", "None", "<NA>"]))


def normalizar_codigo(s):
    return s.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def normalizar_texto(valor):
    if pd.isna(valor):
        return None
    texto = str(valor).strip().upper()
    texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto or None


def normalizar_base(valor):
    texto = normalizar_texto(valor)
    if not texto:
        return None
    texto = re.sub(r"^F\s+", "", texto)
    texto = re.sub(r"\s*-\s*", "-", texto)
    return re.sub(r"\s+", " ", texto).strip()


def localizar_arquivo(nome_exato, padrao):
    exato = PASTA / nome_exato
    if exato.exists():
        return exato
    candidatos = [p for p in PASTA.glob(padrao) if not p.name.startswith("~$")]
    if not candidatos:
        raise FileNotFoundError(f"Nenhum arquivo encontrado para: {padrao}")
    return max(candidatos, key=lambda p: p.stat().st_mtime)


def localizar_gestao():
    return localizar_arquivo("Gestão de colaboradores.xlsx", "Gestão de colaboradores*.xlsx")


def localizar_prazos():
    return localizar_arquivo("Ajuste de Prazos (Setembro).xlsx", "Ajuste de Prazos*.xlsx")


def localizar_entregas():
    arquivos = [p for p in PASTA.glob("Entrega realizada*.xlsx") if not p.name.startswith("~$")]
    if not arquivos:
        raise FileNotFoundError("Nenhum arquivo 'Entrega realizada*.xlsx' em dados/")
    return sorted(arquivos, key=lambda p: p.stat().st_mtime)


def extrair_turno_num(v):
    m = re.search(r"(\d+)", str(v))
    return int(m.group(1)) if m else None


def carregar_metas():
    arquivo = localizar_prazos()
    prazos = pd.read_excel(arquivo, sheet_name="Prazos Ideais", engine="openpyxl")
    obrigatorias = {"Base de entrega", "Nome da cidade", "Novo Transit", "Novo Prazo"}
    faltando = obrigatorias - set(prazos.columns)
    if faltando:
        raise KeyError("Colunas ausentes no Ajuste de Prazos: " + ", ".join(sorted(faltando)))
    prazos["_base_chave"] = prazos["Base de entrega"].map(normalizar_base)
    prazos["cidade_base"] = prazos["Nome da cidade"].astype("string").str.strip()
    prazos["meta_transit"] = pd.to_numeric(prazos["Novo Transit"], errors="coerce")
    prazos["meta_entrega"] = pd.to_numeric(prazos["Novo Prazo"], errors="coerce")
    prazos["meta_total"] = prazos["meta_transit"] + prazos["meta_entrega"]
    metas = (
        prazos.dropna(subset=["_base_chave", "meta_total"])
        .sort_values(["_base_chave", "meta_total"], kind="stable")
        .drop_duplicates("_base_chave", keep="first")
        [["_base_chave", "cidade_base", "meta_transit", "meta_entrega", "meta_total"]]
    )
    print(f"Metas por Base carregadas: {len(metas):,} ({arquivo.name})")
    return metas


def carregar():
    entregas = localizar_entregas()
    print(f"Arquivos de entrega encontrados: {len(entregas)}")
    for arquivo in entregas:
        print(f" - {arquivo.name}")

    partes = []
    for entrega in entregas:
        xls = pd.ExcelFile(entrega, engine="openpyxl")
        for aba in xls.sheet_names:
            df = pd.read_excel(entrega, sheet_name=aba, engine="openpyxl")
            if "Número da Remessa" not in df.columns:
                continue
            df["_mtime_origem"] = entrega.stat().st_mtime
            partes.append(df)
    if not partes:
        raise ValueError("Nenhuma aba com 'Número da Remessa' foi encontrada.")

    base = (
        pd.concat(partes, ignore_index=True)
        .sort_values("_mtime_origem")
        .drop_duplicates(subset=["Número da Remessa"], keep="last")
        .reset_index(drop=True)
    )

    gestao = pd.read_excel(localizar_gestao(), engine="openpyxl").rename(
        columns={"Estação No.": "Código Base", "Responsável da Região": "R/M"}
    )
    base["Código Base"] = normalizar_codigo(base["Código da base de entrega"])
    gestao["Código Base"] = normalizar_codigo(gestao["Código Base"])
    for c in ["R/M", "Coordenador", "Supervisor"]:
        gestao[c] = limpar_nome(gestao[c])
    gestao = gestao[
        gestao["R/M"].astype("string").str.contains("ORLEAN", case=False, na=False)
    ].copy()
    if gestao.empty:
        raise ValueError("Nenhuma base do R/M ORLEAN encontrada na Gestão.")

    codigos = set(gestao["Código Base"].dropna().astype("string").str.strip())
    print(f"Bases pertencentes ao R/M Orlean: {len(codigos):,}")
    antes = len(base)
    base = base[base["Código Base"].isin(codigos)].copy()
    print(f"Registros após filtro Orlean: {len(base):,} de {antes:,}")

    col_turno = next((c for c in base.columns if "turno de linha" in str(c).lower()), None)
    if col_turno is None:
        raise KeyError("Coluna de Turno de linha não encontrada.")
    base["turno_rit"] = base[col_turno].map(extrair_turno_num)
    antes = len(base)
    base = base[base["turno_rit"].fillna(0) >= 1].copy()
    print(f"Registros após filtro 1Rit+: {len(base):,} de {antes:,}")

    base = base.merge(
        gestao[["Código Base", "R/M", "Coordenador", "Supervisor"]].drop_duplicates("Código Base"),
        on="Código Base",
        how="left",
        validate="m:1",
    )

    status_col = next((c for c in base.columns if "entregue no prazo" in str(c).lower()), None)
    if status_col is None:
        raise KeyError("Coluna 'Entregue no prazo' não encontrada.")
    status = base[status_col].astype("string").str.strip().str.upper()
    base["no_prazo"] = status.eq("Y").fillna(False).astype(bool)
    base["status_sla"] = status.map({"Y": "No prazo", "N": "Entregue fora do prazo"}).fillna("Não entregue")

    col_entregador = next((c for c in base.columns if str(c).strip().lower() == "entregador"), None)
    if col_entregador is None:
        raise KeyError("Coluna 'Entregador' não encontrada.")
    base["entregador"] = (
        base[col_entregador].astype("string").str.split(" - ", n=1).str[-1]
        .str.split("|", n=1).str[0].str.strip()
    )
    base["entregador"] = limpar_nome(base["entregador"])
    base["data_sla"] = pd.to_datetime(base["Data prevista de entrega"], errors="coerce", dayfirst=True).dt.date

    base["prazo_atual_transferencia"] = pd.to_numeric(base.get("Prazo SC Destino-> Base de Entrega"), errors="coerce")
    base["prazo_atual_entrega"] = pd.to_numeric(base.get("Prazo PDD Destino x Destinatário"), errors="coerce")
    base["data_expedicao_sc"] = pd.to_datetime(base.get("Horário de Expedição SC/ HUB Destino"), errors="coerce")
    base["data_hora_entrega"] = pd.to_datetime(base.get("Horário da entrega"), errors="coerce")
    base["_base_chave"] = base["Base de entrega"].map(normalizar_base)
    base = base.merge(carregar_metas(), on="_base_chave", how="left", validate="m:1")

    cobertura = int(base["meta_total"].notna().sum())
    print(f"Cobertura das metas: {cobertura:,}/{len(base):,} ({(cobertura / len(base) * 100 if len(base) else 0):.1f}%)")
    data_exp = base["data_expedicao_sc"].dt.normalize()
    meta = pd.to_numeric(base["meta_total"], errors="coerce")
    base["data_limite_prazo"] = (data_exp + pd.to_timedelta(meta, unit="D")).dt.date
    data_entrega = base["data_hora_entrega"].dt.normalize()
    base["prazo_real_dias"] = (data_entrega - data_exp).dt.total_seconds() / 86400
    return base


def preparar_saida(base):
    return pd.DataFrame({
        "numero_remessa": base["Número da Remessa"].astype("string"),
        "codigo_base": base["Código Base"].astype("string"),
        "base_entrega": base["Base de entrega"].astype("string"),
        "data_sla": base["data_sla"],
        "no_prazo": base["no_prazo"].fillna(False).astype(bool),
        "status_sla": base["status_sla"].astype("string"),
        "rm": base["R/M"].astype("string"),
        "coordenador": base["Coordenador"].astype("string"),
        "supervisor": base["Supervisor"].astype("string"),
        "entregador": base["entregador"].astype("string"),
        "turno_rit": base["turno_rit"].astype("Int64"),
        "cidade_base": base["cidade_base"].astype("string"),
        "prazo_atual_transferencia": base["prazo_atual_transferencia"],
        "prazo_atual_entrega": base["prazo_atual_entrega"],
        "meta_transit": base["meta_transit"],
        "meta_entrega": base["meta_entrega"],
        "meta_total": base["meta_total"],
        "data_expedicao_sc": base["data_expedicao_sc"],
        "data_hora_entrega": base["data_hora_entrega"],
        "data_limite_prazo": base["data_limite_prazo"],
        "prazo_real_dias": base["prazo_real_dias"],
    })


def csv_gzip(df):
    texto = io.StringIO()
    df.to_csv(texto, index=False, date_format="%Y-%m-%dT%H:%M:%S")
    return gzip.compress(texto.getvalue().encode("utf-8"), compresslevel=6, mtime=0)


def publicar(base):
    out = preparar_saida(base).dropna(subset=["data_sla"]).copy()
    if out.empty:
        raise ValueError("Nenhum registro com data_sla válida para publicar.")
    enviados = 0
    for dt, grupo in out.groupby("data_sla", sort=True):
        grupo = grupo.drop_duplicates("numero_remessa", keep="last").sort_values("numero_remessa")
        nome = f"sla_{dt}.csv.gz"
        enviar_bytes(nome, csv_gzip(grupo))
        print(f"{dt}: {len(grupo):,} registros publicados.")
        enviados += 1
    return enviados


if __name__ == "__main__":
    df = carregar()
    print(f"Registros válidos após filtros e deduplicação: {len(df):,}")
    qtd = publicar(df)
    print(f"Carga concluída: {qtd} arquivo(s) diário(s) enviado(s) ao Google Drive.")
