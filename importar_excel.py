from pathlib import Path
import re
import unicodedata

import pandas as pd

from db import get_conn


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
    texto = "".join(
        c for c in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(c)
    )
    texto = re.sub(r"\s+", " ", texto)
    return texto or None


def normalizar_base(valor):
    texto = normalizar_texto(valor)
    if not texto:
        return None

    # SLA costuma vir como "F BRV-PA"; tabela de metas como "BRV -PA".
    texto = re.sub(r"^F\s+", "", texto)
    texto = re.sub(r"\s*-\s*", "-", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def normalizar_cidade(valor):
    texto = normalizar_texto(valor)
    if not texto:
        return None

    texto = re.sub(r"[^A-Z0-9 ]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def localizar_arquivo(nome_exato, padrao):
    exato = PASTA / nome_exato
    if exato.exists():
        return exato

    candidatos = [
        p for p in PASTA.glob(padrao)
        if not p.name.startswith("~$")
    ]
    if not candidatos:
        raise FileNotFoundError(
            f"Nenhum arquivo encontrado para: {padrao}"
        )

    return max(candidatos, key=lambda p: p.stat().st_mtime)


def localizar_gestao():
    return localizar_arquivo(
        "Gestão de colaboradores.xlsx",
        "Gestão de colaboradores*.xlsx",
    )


def localizar_prazos():
    return localizar_arquivo(
        "Ajuste de Prazos (Setembro).xlsx",
        "Ajuste de Prazos*.xlsx",
    )


def localizar_entregas():
    arquivos = [
        p for p in PASTA.glob("Entrega realizada*.xlsx")
        if not p.name.startswith("~$")
    ]

    if not arquivos:
        raise FileNotFoundError(
            "Nenhum arquivo 'Entrega realizada*.xlsx' em dados/"
        )

    return sorted(arquivos, key=lambda p: p.stat().st_mtime)


def extrair_turno_num(v):
    m = re.search(r"(\d+)", str(v))
    return int(m.group(1)) if m else None


def carregar_metas():
    """
    Define uma única Cidade da Base e uma única meta por Base.

    Regra:
    - usa o arquivo Ajuste de Prazos somente como referência;
    - para cada Base, escolhe a rota com menor Meta Total
      (Novo Transit + Novo Prazo);
    - em empate, mantém a primeira ocorrência do arquivo.

    Isso evita depender de Cidade Destino do pacote.
    """
    arquivo = localizar_prazos()

    prazos = pd.read_excel(
        arquivo,
        sheet_name="Prazos Ideais",
        engine="openpyxl",
    )

    obrigatorias = {
        "Base de entrega",
        "Nome da cidade",
        "Novo Transit",
        "Novo Prazo",
    }

    faltando = obrigatorias - set(prazos.columns)
    if faltando:
        raise KeyError(
            "Colunas ausentes no arquivo Ajuste de Prazos: "
            + ", ".join(sorted(faltando))
        )

    prazos["_base_chave"] = prazos["Base de entrega"].map(normalizar_base)
    prazos["cidade_base"] = prazos["Nome da cidade"].astype("string").str.strip()

    prazos["meta_transit"] = pd.to_numeric(
        prazos["Novo Transit"], errors="coerce"
    )
    prazos["meta_entrega"] = pd.to_numeric(
        prazos["Novo Prazo"], errors="coerce"
    )
    prazos["meta_total"] = (
        prazos["meta_transit"] + prazos["meta_entrega"]
    )

    prazos = prazos.dropna(
        subset=["_base_chave", "meta_total"]
    ).copy()

    # menor prazo da base = referência da própria cidade/base
    metas = (
        prazos.sort_values(
            ["_base_chave", "meta_total"],
            kind="stable",
        )
        .drop_duplicates("_base_chave", keep="first")
        [[
            "_base_chave",
            "cidade_base",
            "meta_transit",
            "meta_entrega",
            "meta_total",
        ]]
    )

    print(
        f"Metas por Base carregadas: {len(metas):,} "
        f"({arquivo.name})"
    )

    return metas

def garantir_colunas_banco():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS cidade_base text;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS prazo_atual_transferencia numeric;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS prazo_atual_entrega numeric;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS meta_transit numeric;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS meta_entrega numeric;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS meta_total numeric;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS data_expedicao_sc timestamp;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS data_hora_entrega timestamp;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS data_limite_prazo date;
            """)
            cur.execute("""
                ALTER TABLE entregas
                ADD COLUMN IF NOT EXISTS prazo_real_dias numeric;
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_entregas_cidade_base
                ON entregas(cidade_base);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_entregas_base_cidadebase
                ON entregas(base_entrega, cidade_base);
            """)

        conn.commit()


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

            df["_mtime_origem"] = entrega.stat().st_mtime
            partes.append(df)

    if not partes:
        raise ValueError(
            "Nenhuma aba com a coluna 'Número da Remessa' foi encontrada."
        )

    base = pd.concat(partes, ignore_index=True)

    # Mesmo universo do SLA: deduplicação por remessa antes dos filtros.
    base = (
        base.sort_values("_mtime_origem")
        .drop_duplicates(subset=["Número da Remessa"], keep="last")
        .reset_index(drop=True)
    )

    gestao = pd.read_excel(localizar_gestao(), engine="openpyxl")
    gestao = gestao.rename(columns={
        "Estação No.": "Código Base",
        "Responsável da Região": "R/M",
    })

    base["Código Base"] = normalizar_codigo(
        base["Código da base de entrega"]
    )
    gestao["Código Base"] = normalizar_codigo(gestao["Código Base"])

    gestao["R/M"] = limpar_nome(gestao["R/M"])
    gestao["Coordenador"] = limpar_nome(gestao["Coordenador"])
    gestao["Supervisor"] = limpar_nome(gestao["Supervisor"])

    # Regra original do SLA: somente as bases do R/M Orlean.
    gestao = gestao[
        gestao["R/M"]
        .astype("string")
        .str.contains("ORLEAN", case=False, na=False)
    ].copy()

    if gestao.empty:
        raise ValueError(
            "Nenhuma base do R/M ORLEAN encontrada na Gestão. "
            "Carga cancelada."
        )

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

    col_turno = next(
        (c for c in base.columns if "turno de linha" in str(c).lower()),
        None,
    )
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
        gestao[
            ["Código Base", "R/M", "Coordenador", "Supervisor"]
        ].drop_duplicates("Código Base"),
        on="Código Base",
        how="left",
        validate="m:1",
    )

    # ==========================
    # Regras originais do SLA
    # ==========================
    status_col = next(
        (c for c in base.columns if "entregue no prazo" in str(c).lower()),
        None,
    )
    if status_col is None:
        raise KeyError("Coluna 'Entregue no prazo' não encontrada.")

    status_sla = (
        base[status_col]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    base["no_prazo"] = (
        status_sla.eq("Y").fillna(False).astype(bool)
    )

    base["status_sla"] = (
        status_sla
        .map({
            "Y": "No prazo",
            "N": "Entregue fora do prazo",
        })
        .fillna("Não entregue")
    )

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

    # ==========================
    # Dados da ABA PRAZO
    # A fonte operacional continua sendo o próprio SLA.
    # ==========================
    # ==========================
    # Dados da ABA PRAZO
    # Não usa Cidade Destino.
    # A meta é definida pela própria Base.
    # ==========================
    base["prazo_atual_transferencia"] = pd.to_numeric(
        base.get("Prazo SC Destino-> Base de Entrega"),
        errors="coerce",
    )
    base["prazo_atual_entrega"] = pd.to_numeric(
        base.get("Prazo PDD Destino x Destinatário"),
        errors="coerce",
    )

    base["data_expedicao_sc"] = pd.to_datetime(
        base.get("Horário de Expedição SC/ HUB Destino"),
        errors="coerce",
    )
    base["data_hora_entrega"] = pd.to_datetime(
        base.get("Horário da entrega"),
        errors="coerce",
    )

    base["_base_chave"] = base["Base de entrega"].map(normalizar_base)

    metas = carregar_metas()

    base = base.merge(
        metas,
        on="_base_chave",
        how="left",
        validate="m:1",
    )

    cobertura = int(base["meta_total"].notna().sum())
    print(
        "Cobertura das metas por Base: "
        f"{cobertura:,}/{len(base):,} "
        f"({(cobertura / len(base) * 100 if len(base) else 0):.1f}%)"
    )

    meta_dias = pd.to_numeric(base["meta_total"], errors="coerce")

    data_exp = base["data_expedicao_sc"].dt.normalize()
    limite = data_exp + pd.to_timedelta(meta_dias, unit="D")
    base["data_limite_prazo"] = limite.dt.date

    data_entrega = base["data_hora_entrega"].dt.normalize()
    base["prazo_real_dias"] = (
        (data_entrega - data_exp).dt.total_seconds() / 86400
    )

    return base


def upsert(base):
    garantir_colunas_banco()

    out = pd.DataFrame({
        # Campos originais SLA
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

        # Campos adicionais para a aba Prazo
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

    out = out.astype(object).where(pd.notna(out), None)
    registros = list(out.itertuples(index=False, name=None))

    sql = """
        INSERT INTO entregas (
            numero_remessa,
            codigo_base,
            base_entrega,
            data_sla,
            no_prazo,
            status_sla,
            rm,
            coordenador,
            supervisor,
            entregador,
            turno_rit,
            cidade_base,
            prazo_atual_transferencia,
            prazo_atual_entrega,
            meta_transit,
            meta_entrega,
            meta_total,
            data_expedicao_sc,
            data_hora_entrega,
            data_limite_prazo,
            prazo_real_dias
        )
        VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
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
            turno_rit = EXCLUDED.turno_rit,
            cidade_base = EXCLUDED.cidade_base,
            prazo_atual_transferencia = EXCLUDED.prazo_atual_transferencia,
            prazo_atual_entrega = EXCLUDED.prazo_atual_entrega,
            meta_transit = EXCLUDED.meta_transit,
            meta_entrega = EXCLUDED.meta_entrega,
            meta_total = EXCLUDED.meta_total,
            data_expedicao_sc = EXCLUDED.data_expedicao_sc,
            data_hora_entrega = EXCLUDED.data_hora_entrega,
            data_limite_prazo = EXCLUDED.data_limite_prazo,
            prazo_real_dias = EXCLUDED.prazo_real_dias;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, registros)
        conn.commit()


if __name__ == "__main__":
    df = carregar()
    print(
        f"Registros válidos após filtros e deduplicação: {len(df):,}"
    )
    upsert(df)
    print("Carga concluída.")
