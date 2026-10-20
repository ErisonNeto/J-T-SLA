from pathlib import Path
from datetime import date

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import get_conn

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="J&T SLA Dashboard")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/health")
def health():
    return {"status": "ok"}


def montar_filtros(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where = ["1=1"]
    params = []

    if data_inicio:
        where.append("data_sla >= %s")
        params.append(data_inicio)

    if data_fim:
        where.append("data_sla <= %s")
        params.append(data_fim)

    if responsavel:
        where.append("(coordenador = %s OR supervisor = %s)")
        params.extend([responsavel, responsavel])

    if rm:
        where.append("rm = %s")
        params.append(rm)

    return " AND ".join(where), params


@app.get("/api/filtros")
def filtros():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(data_sla), MAX(data_sla)
                FROM entregas
                WHERE data_sla IS NOT NULL;
            """)
            datas = cur.fetchone()

            cur.execute("""
                WITH nomes AS (
                    SELECT coordenador AS responsavel
                    FROM entregas
                    WHERE coordenador IS NOT NULL
                      AND TRIM(coordenador) <> ''

                    UNION

                    SELECT supervisor AS responsavel
                    FROM entregas
                    WHERE supervisor IS NOT NULL
                      AND TRIM(supervisor) <> ''
                )
                SELECT responsavel
                FROM nomes
                ORDER BY responsavel;
            """)
            responsaveis = [r[0] for r in cur.fetchall()]

            cur.execute("""
                SELECT DISTINCT rm
                FROM entregas
                WHERE rm IS NOT NULL
                  AND TRIM(rm) <> ''
                ORDER BY rm;
            """)
            rms = [r[0] for r in cur.fetchall()]

    return {
        "data_min": datas[0].isoformat() if datas and datas[0] else None,
        "data_max": datas[1].isoformat() if datas and datas[1] else None,
        "responsaveis": responsaveis,
        "rms": rms,
    }


@app.get("/api/kpis")
def kpis(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(data_inicio, data_fim, responsavel, rm)

    sql = f"""
        SELECT
            COUNT(*)::bigint,
            COUNT(*) FILTER (WHERE no_prazo)::bigint,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint,
            COUNT(*) FILTER (WHERE status_sla = 'Entregue fora do prazo')::bigint,
            COUNT(*) FILTER (WHERE status_sla = 'Não entregue')::bigint,
            COALESCE(AVG(CASE WHEN no_prazo THEN 1.0 ELSE 0.0 END), 0)
        FROM entregas
        WHERE {where};
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    return {
        "total_pedidos": row[0],
        "no_prazo": row[1],
        "fora_prazo": row[2],
        "entregue_fora_prazo": row[3],
        "nao_entregue": row[4],
        "sla": float(row[5]),
        "meta": 1.0,
    }


@app.get("/api/sla-diario")
def sla_diario(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(data_inicio, data_fim, responsavel, rm)

    sql = f"""
        SELECT
            data_sla,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE no_prazo)::bigint AS no_prazo,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(CASE WHEN no_prazo THEN 1.0 ELSE 0.0 END) AS sla
        FROM entregas
        WHERE {where}
          AND data_sla IS NOT NULL
        GROUP BY data_sla
        ORDER BY data_sla;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "data_sla": row[0].isoformat() if row[0] else None,
            "pedidos": row[1],
            "no_prazo": row[2],
            "fora_prazo": row[3],
            "sla": float(row[4] or 0),
        }
        for row in rows
    ]


@app.get("/api/sla-rm")
def sla_rm(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(data_inicio, data_fim, responsavel, rm)

    sql = f"""
        SELECT
            rm,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE no_prazo)::bigint AS no_prazo,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(CASE WHEN no_prazo THEN 1.0 ELSE 0.0 END) AS sla
        FROM entregas
        WHERE {where}
          AND rm IS NOT NULL
          AND TRIM(rm) <> ''
        GROUP BY rm
        ORDER BY sla ASC, pedidos DESC;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "rm": row[0],
            "pedidos": row[1],
            "no_prazo": row[2],
            "fora_prazo": row[3],
            "sla": float(row[4] or 0),
        }
        for row in rows
    ]


@app.get("/api/piores-responsaveis")
def piores_responsaveis(
    limit: int = Query(15, ge=1, le=100),
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(data_inicio, data_fim, responsavel, rm)

    sql = f"""
        WITH responsaveis AS (
            SELECT
                coordenador AS responsavel,
                'Coordenador'::text AS funcao,
                COUNT(*)::bigint AS pedidos,
                COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
                AVG(CASE WHEN no_prazo THEN 1.0 ELSE 0.0 END) AS sla
            FROM entregas
            WHERE {where}
              AND coordenador IS NOT NULL
              AND TRIM(coordenador) <> ''
            GROUP BY coordenador

            UNION ALL

            SELECT
                supervisor AS responsavel,
                'Supervisor'::text AS funcao,
                COUNT(*)::bigint AS pedidos,
                COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
                AVG(CASE WHEN no_prazo THEN 1.0 ELSE 0.0 END) AS sla
            FROM entregas
            WHERE {where}
              AND supervisor IS NOT NULL
              AND TRIM(supervisor) <> ''
            GROUP BY supervisor
        )
        SELECT responsavel, funcao, pedidos, fora_prazo, sla
        FROM responsaveis
        ORDER BY sla ASC, fora_prazo DESC, pedidos DESC
        LIMIT %s;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params + params + [limit])
            rows = cur.fetchall()

    return [
        {
            "responsavel": row[0],
            "funcao": row[1],
            "pedidos": row[2],
            "fora_prazo": row[3],
            "sla": float(row[4] or 0),
        }
        for row in rows
    ]


@app.get("/api/piores-bases")
def piores_bases(
    limit: int = Query(20, ge=1, le=200),
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(data_inicio, data_fim, responsavel, rm)

    sql = f"""
        SELECT
            codigo_base,
            base_entrega,
            coordenador,
            supervisor,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE no_prazo)::bigint AS no_prazo,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(CASE WHEN no_prazo THEN 1.0 ELSE 0.0 END) AS sla
        FROM entregas
        WHERE {where}
        GROUP BY codigo_base, base_entrega, coordenador, supervisor
        ORDER BY sla ASC, fora_prazo DESC, pedidos DESC
        LIMIT %s;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params + [limit])
            rows = cur.fetchall()

    return [
        {
            "codigo_base": row[0],
            "base_entrega": row[1],
            "coordenador": row[2],
            "supervisor": row[3],
            "pedidos": row[4],
            "no_prazo": row[5],
            "fora_prazo": row[6],
            "sla": float(row[7] or 0),
        }
        for row in rows
    ]


@app.get("/api/ranking-bases")
def ranking_bases(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(data_inicio, data_fim, responsavel, rm)

    sql = f"""
        SELECT
            codigo_base,
            base_entrega,
            rm,
            coordenador,
            supervisor,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE no_prazo)::bigint AS no_prazo,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(CASE WHEN no_prazo THEN 1.0 ELSE 0.0 END) AS sla
        FROM entregas
        WHERE {where}
        GROUP BY
            codigo_base,
            base_entrega,
            rm,
            coordenador,
            supervisor
        ORDER BY sla ASC, fora_prazo DESC, pedidos DESC;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "codigo_base": row[0],
            "base_entrega": row[1],
            "rm": row[2],
            "coordenador": row[3],
            "supervisor": row[4],
            "pedidos": row[5],
            "no_prazo": row[6],
            "fora_prazo": row[7],
            "sla": float(row[8] or 0),
        }
        for row in rows
    ]
