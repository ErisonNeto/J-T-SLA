from __future__ import annotations

import gzip
import io

import pandas as pd
from dotenv import load_dotenv

from db import get_conn
from drive_upload import enviar_bytes

load_dotenv()

COLUNAS = [
    "numero_remessa", "codigo_base", "base_entrega", "data_sla", "no_prazo",
    "status_sla", "rm", "coordenador", "supervisor", "entregador", "turno_rit",
    "cidade_base", "prazo_atual_transferencia", "prazo_atual_entrega", "meta_transit",
    "meta_entrega", "meta_total", "data_expedicao_sc", "data_hora_entrega",
    "data_limite_prazo", "prazo_real_dias",
]


def csv_gzip(df):
    texto = io.StringIO()
    df.to_csv(texto, index=False, date_format="%Y-%m-%dT%H:%M:%S")
    return gzip.compress(texto.getvalue().encode("utf-8"), compresslevel=6, mtime=0)


def datas_existentes(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT data_sla FROM entregas WHERE data_sla IS NOT NULL ORDER BY data_sla")
        return [r[0] for r in cur.fetchall()]


def carregar_dia(conn, dia):
    sql = f"SELECT {', '.join(COLUNAS)} FROM entregas WHERE data_sla = %s ORDER BY numero_remessa"
    with conn.cursor() as cur:
        cur.execute(sql, (dia,))
        rows = cur.fetchall()
        nomes = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=nomes)


def main():
    print("=== MIGRACAO SLA: SUPABASE -> GOOGLE DRIVE ===")
    with get_conn() as conn:
        datas = datas_existentes(conn)
        if not datas:
            raise RuntimeError("A tabela entregas nao possui datas para exportar.")
        print(f"Datas encontradas: {len(datas)} | {datas[0]} ate {datas[-1]}")
        total = 0
        for dia in datas:
            df = carregar_dia(conn, dia)
            if df.empty:
                continue
            df = df.drop_duplicates("numero_remessa", keep="last")
            nome = f"sla_{dia}.csv.gz"
            enviar_bytes(nome, csv_gzip(df))
            total += len(df)
            print(f"OK {dia}: {len(df):,} registros")
    print(f"Migracao concluida: {total:,} registros historicos enviados.")
    print("Depois de validar o dashboard no Drive, o Supabase pode deixar de ser a fonte do SLA.")


if __name__ == "__main__":
    main()
