from pathlib import Path
from datetime import date

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import get_conn

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="J&T SLA Regional — Resumo Oficial")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Fonte UNICA da Visao Geral: Resumo consolidado por data e codigo da base.
# A tabela 'entregas' (remessas) nao participa de nenhuma consulta deste painel.
FONTE = "public.sla_resumo_diario"
META_DIARIA = 0.95
META_MENSAL = 0.97


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/health")
def health():
    return {"status": "ok", "fonte": FONTE}


def consultar(sql: str, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchall()


def tabela_pronta() -> bool:
    """Nunca utilizar a base antiga como fallback silencioso."""
    rows = consultar(
        "SELECT to_regclass('public.sla_resumo_diario') IS NOT NULL"
    )
    return bool(rows and rows[0][0])


def filtros_sql(data_inicio=None, data_fim=None, responsavel=None, rm=None, base=None):
    filtros = ["1=1"]
    params = []
    if data_inicio:
        filtros.append("data_sla >= %s")
        params.append(data_inicio)
    if data_fim:
        filtros.append("data_sla <= %s")
        params.append(data_fim)
    if responsavel:
        filtros.append("sc = %s")
        params.append(responsavel)
    if rm:
        filtros.append("rm = %s")
        params.append(rm)
    if base:
        filtros.append("base_entrega = %s")
        params.append(base)
    return " AND ".join(filtros), params


def taxa(no_prazo, a_entregar):
    """Razao de somas (N/K), jamais media simples de percentuais."""
    total = int(a_entregar or 0)
    if total <= 0:
        return None
    return float(no_prazo or 0) / total


def data_iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)


def sem_resumo():
    return {
        "data_min": None, "data_max": None, "responsaveis": [],
        "rms": [], "bases": [], "fonte": "sla_resumo_diario",
        "disponivel": False
    }


@app.get("/api/filtros")
def filtros():
    if not tabela_pronta():
        return sem_resumo()
    datas = consultar(f"SELECT MIN(data_sla), MAX(data_sla) FROM {FONTE}")[0]
    focais = consultar(f"""
        SELECT DISTINCT sc FROM {FONTE}
        WHERE sc IS NOT NULL AND BTRIM(sc) <> ''
        ORDER BY sc
    """)
    rms = consultar(f"""
        SELECT DISTINCT rm FROM {FONTE}
        WHERE rm IS NOT NULL AND BTRIM(rm) <> ''
        ORDER BY rm
    """)
    bases = consultar(f"""
        SELECT DISTINCT base_entrega FROM {FONTE}
        WHERE base_entrega IS NOT NULL AND BTRIM(base_entrega) <> ''
        ORDER BY base_entrega
    """)
    return {
        "data_min": data_iso(datas[0]),
        "data_max": data_iso(datas[1]),
        "responsaveis": [r[0] for r in focais],
        "rms": [r[0] for r in rms],
        "bases": [r[0] for r in bases],
        "fonte": "sla_resumo_diario",
        "disponivel": datas[1] is not None,
    }


@app.get("/api/kpis")
def kpis(
    data_inicio: date | None = None, data_fim: date | None = None,
    responsavel: str | None = None, rm: str | None = None, base: str | None = None
):
    vazio = {
        "total_pedidos": 0, "no_prazo": 0, "fora_prazo": 0,
        "entregue_fora_prazo": 0, "nao_entregue": 0,
        "qtd_outros": 0, "sla": None, "meta_diaria": META_DIARIA,
        "meta_mensal": META_MENSAL, "fonte": "sla_resumo_diario"
    }
    if not tabela_pronta():
        return vazio
    where, params = filtros_sql(data_inicio, data_fim, responsavel, rm, base)
    row = consultar(f"""
        SELECT
            COALESCE(SUM(qtd_a_entregar), 0)::bigint,
            COALESCE(SUM(qtd_no_prazo), 0)::bigint,
            COALESCE(SUM(qtd_atraso), 0)::bigint,
            COALESCE(SUM(qtd_outros), 0)::bigint
        FROM {FONTE} WHERE {where}
    """, params)[0]
    k,n,p,outros = [int(v or 0) for v in row]
    return {
        "total_pedidos": k, "no_prazo": n,
        "fora_prazo": p, "entregue_fora_prazo": p,
        "nao_entregue": outros, "qtd_outros": outros,
        "sla": taxa(n,k), "meta_diaria": META_DIARIA,
        "meta_mensal": META_MENSAL, "fonte": "sla_resumo_diario"
    }


@app.get("/api/sla-diario")
def sla_diario(
    data_inicio: date | None = None, data_fim: date | None = None,
    responsavel: str | None = None, rm: str | None = None, base: str | None = None
):
    if not tabela_pronta():
        return []
    where, params = filtros_sql(data_inicio, data_fim, responsavel, rm, base)
    rows = consultar(f"""
        SELECT data_sla,
               SUM(qtd_a_entregar)::bigint,
               SUM(qtd_no_prazo)::bigint,
               SUM(qtd_atraso)::bigint
        FROM {FONTE}
        WHERE {where}
        GROUP BY data_sla
        HAVING SUM(qtd_a_entregar) > 0
        ORDER BY data_sla
    """, params)
    return [
        {"data_sla": data_iso(d), "pedidos": int(k), "no_prazo": int(n),
         "fora_prazo": int(p), "sla": taxa(n,k)}
        for d,k,n,p in rows
    ]


@app.get("/api/sla-rm")
def sla_rm(
    data_inicio: date | None = None, data_fim: date | None = None,
    responsavel: str | None = None, rm: str | None = None, base: str | None = None
):
    if not tabela_pronta():
        return []
    where, params = filtros_sql(data_inicio, data_fim, responsavel, rm, base)
    rows = consultar(f"""
        SELECT rm,
               SUM(qtd_a_entregar)::bigint,
               SUM(qtd_no_prazo)::bigint,
               SUM(qtd_atraso)::bigint
        FROM {FONTE}
        WHERE {where} AND rm IS NOT NULL AND BTRIM(rm) <> ''
        GROUP BY rm
        HAVING SUM(qtd_a_entregar) > 0
        ORDER BY (SUM(qtd_no_prazo)::numeric/NULLIF(SUM(qtd_a_entregar),0)) ASC
    """, params)
    return [
        {"rm": nome, "pedidos": int(k), "no_prazo": int(n),
         "fora_prazo": int(p), "sla": taxa(n,k)}
        for nome,k,n,p in rows
    ]


@app.get("/api/piores-responsaveis")
def piores_responsaveis(
    limit: int = Query(15, ge=1, le=100),
    data_inicio: date | None = None, data_fim: date | None = None,
    responsavel: str | None = None, rm: str | None = None, base: str | None = None
):
    if not tabela_pronta():
        return []
    where, params = filtros_sql(data_inicio, data_fim, responsavel, rm, base)
    rows = consultar(f"""
        SELECT sc,
               SUM(qtd_a_entregar)::bigint,
               SUM(qtd_no_prazo)::bigint,
               SUM(qtd_atraso)::bigint
        FROM {FONTE}
        WHERE {where} AND sc IS NOT NULL AND BTRIM(sc) <> ''
        GROUP BY sc
        HAVING SUM(qtd_a_entregar) > 0
        ORDER BY (SUM(qtd_no_prazo)::numeric/NULLIF(SUM(qtd_a_entregar),0)) ASC,
                 SUM(qtd_atraso) DESC, SUM(qtd_a_entregar) DESC
        LIMIT %s
    """, params + [limit])
    return [
        {"responsavel": sc, "funcao": "Focal", "pedidos": int(k),
         "fora_prazo": int(p), "no_prazo": int(n), "sla": taxa(n,k)}
        for sc,k,n,p in rows
    ]


def consulta_bases(where, limit=None):
    limite_sql = " LIMIT %s" if limit is not None else ""
    return f"""
        SELECT
            codigo_base,
            (ARRAY_AGG(base_entrega ORDER BY data_sla DESC))[1] AS base_entrega,
            (ARRAY_AGG(rm ORDER BY data_sla DESC))[1] AS rm,
            (ARRAY_AGG(sc ORDER BY data_sla DESC))[1] AS focal,
            SUM(qtd_a_entregar)::bigint AS pedidos,
            SUM(qtd_no_prazo)::bigint AS no_prazo,
            SUM(qtd_atraso)::bigint AS fora_prazo
        FROM {FONTE}
        WHERE {where}
        GROUP BY codigo_base
        HAVING SUM(qtd_a_entregar) > 0
        ORDER BY
            (SUM(qtd_no_prazo)::numeric/NULLIF(SUM(qtd_a_entregar),0)) ASC,
            SUM(qtd_atraso) DESC, SUM(qtd_a_entregar) DESC
        {limite_sql}
    """


def organizar_bases(rows):
    return [
        {"codigo_base": codigo, "base_entrega": base, "rm": rm,
         "focal": focal, "pedidos": int(k), "no_prazo": int(n),
         "fora_prazo": int(p), "sla": taxa(n,k)}
        for codigo,base,rm,focal,k,n,p in rows
    ]


@app.get("/api/piores-bases")
def piores_bases(
    limit: int = Query(20, ge=1, le=200),
    data_inicio: date | None = None, data_fim: date | None = None,
    responsavel: str | None = None, rm: str | None = None, base: str | None = None
):
    if not tabela_pronta():
        return []
    where, params = filtros_sql(data_inicio, data_fim, responsavel, rm, base)
    return organizar_bases(
        consultar(consulta_bases(where, limit), params + [limit])
    )


@app.get("/api/ranking-bases")
def ranking_bases(
    data_inicio: date | None = None, data_fim: date | None = None,
    responsavel: str | None = None, rm: str | None = None, base: str | None = None
):
    if not tabela_pronta():
        return []
    where, params = filtros_sql(data_inicio, data_fim, responsavel, rm, base)
    return organizar_bases(consultar(consulta_bases(where), params))
