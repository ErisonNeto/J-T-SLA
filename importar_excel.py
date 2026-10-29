from pathlib import Path
import re
import pandas as pd
from db import get_conn

PASTA = Path(__file__).resolve().parent.parent / "dados"
GESTAO = PASTA / "Gestão de colaboradores.xlsx"

def limpar_nome(s):
    s = s.astype("string").str.strip()
    return s.mask(s.isin(["", "0", "0.0", "nan", "NaN", "None", "<NA>"]))

def normalizar_codigo(s):
    return s.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)

def localizar_entregas():
    arquivos = [
        p for p in PASTA.glob("Entrega realizada*.xlsx")
        if not p.name.startswith("~$")
    ]
    if not arquivos:
        raise FileNotFoundError("Nenhum arquivo 'Entrega realizada*.xlsx' em dados/")

    # Processa do mais antigo para o mais novo.
    # Se uma mesma remessa existir em mais de um arquivo,
    # a versão do arquivo mais recente será mantida.
    return sorted(arquivos, key=lambda p: p.stat().st_mtime)

def extrair_turno_num(v):
    m = re.search(r"(\d+)", str(v))
    return int(m.group(1)) if m else None

def carregar():
    entregas = localizar_entregas()

    print(f"Arquivos de entrega encontrados: {len(entregas)}")
    for arquivo in entregas:
        print(f" - {arquivo.name}")

    partes = []

    for entrega in entregas:
        xls = pd.ExcelFile(entrega, engine="openpyxl")

        for aba in xls.sheet_names:
            df = pd.read_excel(
                entrega,
                sheet_name=aba,
                engine="openpyxl",
            )

            if "Número da Remessa" not in df.columns:
                continue

            # Guarda a origem apenas para controlar duplicidades.
            df["_arquivo_origem"] = entrega.name
            df["_mtime_origem"] = entrega.stat().st_mtime
            partes.append(df)

    if not partes:
        raise ValueError(
            "Nenhuma aba com a coluna 'Número da Remessa' foi encontrada "
            "nos arquivos de entrega."
        )

    base = pd.concat(partes, ignore_index=True)

    # Garante uma única linha por remessa.
    # Como os arquivos foram carregados do mais antigo para o mais novo,
    # keep='last' preserva a versão mais recente da remessa.
    base = (
        base.sort_values("_mtime_origem")
        .drop_duplicates(subset=["Número da Remessa"], keep="last")
        .reset_index(drop=True)
    )

    gestao = pd.read_excel(GESTAO, engine="openpyxl")
    gestao = gestao.rename(columns={
        "Estação No.": "Código Base",
        "Responsável da Região": "R/M",
    })

    base["Código Base"] = normalizar_codigo(base["Código da base de entrega"])
    gestao["Código Base"] = normalizar_codigo(gestao["Código Base"])
    gestao["R/M"] = limpar_nome(gestao["R/M"])
    gestao["Coordenador"] = limpar_nome(gestao["Coordenador"])
    gestao["Supervisor"] = limpar_nome(gestao["Supervisor"])

    # REGRA DO DASHBOARD:
    # considerar somente o R/M ORLEAN e apenas as bases que pertencem a ele.
    # Coordenadores e Supervisores entram apenas por meio dessas bases.
    gestao_orlean = gestao[
        gestao["R/M"]
        .astype("string")
        .str.contains("ORLEAN", case=False, na=False)
    ].copy()

    if gestao_orlean.empty:
        rms_encontrados = sorted(
            gestao["R/M"]
            .dropna()
            .astype("string")
            .str.strip()
            .loc[lambda s: s.ne("")]
            .unique()
            .tolist()
        )

        raise ValueError(
            "Nenhuma base do R/M ORLEAN foi encontrada no arquivo de Gestão. "
            f"R/M encontrados no arquivo: {rms_encontrados}. "
            "Carga cancelada para evitar importar bases de outro R/M."
        )

    gestao = gestao_orlean

    codigos = set(
        gestao["Código Base"]
        .dropna()
        .astype("string")
        .str.strip()
    )

    print(f"Bases pertencentes ao R/M Orlean: {len(codigos):,}")

    antes_orlean = len(base)
    base = base[base["Código Base"].isin(codigos)].copy()

    print(
        f"Registros após filtro das bases do Orlean: "
        f"{len(base):,} de {antes_orlean:,}"
    )

    # Exclui 0Rit e mantém 1Rit, 2Rit, 3Rit...
    col_turno = next((c for c in base.columns if "turno de linha" in str(c).lower()), None)
    if col_turno is None:
        raise KeyError("Coluna de Turno de linha não encontrada.")
    base["turno_rit"] = base[col_turno].map(extrair_turno_num)

    antes_rit = len(base)
    base = base[base["turno_rit"].fillna(0) >= 1].copy()

    print(
        f"Registros após filtro 1Rit+: "
        f"{len(base):,} de {antes_rit:,}"
    )

    base = base.merge(
        gestao[["Código Base", "R/M", "Coordenador", "Supervisor"]]
        .drop_duplicates("Código Base"),
        on="Código Base",
        how="left",
        validate="m:1",
    )

    status_col = next((c for c in base.columns if "entregue no prazo" in str(c).lower()), None)
    if status_col is None:
        raise KeyError("Coluna 'Entregue no prazo' não encontrada.")

    status_sla = (
        base[status_col]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # Regra operacional:
    # Y     -> entregue no prazo
    # N     -> entregue fora do prazo
    # vazio -> não entregue
    base["no_prazo"] = (
        status_sla
        .eq("Y")
        .fillna(False)
        .astype(bool)
    )

    base["status_sla"] = (
        status_sla
        .map({
            "Y": "No prazo",
            "N": "Entregue fora do prazo",
        })
        .fillna("Não entregue")
    )

    # Entregador: mantém somente o nome.
    # Exemplo:
    # "F SBS - ANDERSON FELIPE MOURAO DE CASTRO|06144289127"
    # -> "ANDERSON FELIPE MOURAO DE CASTRO"
    col_entregador = next(
        (c for c in base.columns if str(c).strip().lower() == "entregador"),
        None,
    )
    if col_entregador is None:
        raise KeyError("Coluna 'Entregador' não encontrada.")

    base["entregador"] = (
        base[col_entregador]
        .astype("string")
        .str.split(" - ", n=1)
        .str[-1]
        .str.split("|", n=1)
        .str[0]
        .str.strip()
    )
    base["entregador"] = limpar_nome(base["entregador"])

    base["data_sla"] = pd.to_datetime(
        base["Data prevista de entrega"],
        errors="coerce",
        dayfirst=True,
    ).dt.date

    return base

def upsert(base):
    out = pd.DataFrame({
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
        "turno_rit": base["turno_rit"].astype(int),
    })

    # Converte valores ausentes do pandas para None,
    # que o PostgreSQL/Psycopg entende como NULL.
    out = out.astype(object).where(pd.notna(out), None)

    registros = list(out.itertuples(index=False, name=None))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO entregas (
                    numero_remessa, codigo_base, base_entrega, data_sla,
                    no_prazo, status_sla, rm, coordenador, supervisor,
                    entregador, turno_rit
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (numero_remessa) DO UPDATE SET
                    codigo_base = EXCLUDED.codigo_base,
                    base_entrega = EXCLUDED.base_entrega,
                    data_sla = EXCLUDED.data_sla,
                    no_prazo = EXCLUDED.no_prazo,
                    status_sla = EXCLUDED.status_sla,
                    rm = EXCLUDED.rm,
                    coordenador = EXCLUDED.coordenador,
                    supervisor = EXCLUDED.supervisor,
                    entregador = EXCLUDED.entregador,
                    turno_rit = EXCLUDED.turno_rit
            """, registros)
        conn.commit()

if __name__ == "__main__":
    df = carregar()
    print(f"Registros válidos após filtros e deduplicação: {len(df):,}")
    upsert(df)
    print("Carga concluída.")
