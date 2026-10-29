from pathlib import Path
from datetime import date

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import get_conn


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="J&T SLA Dashboard")

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static",
)

templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
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
            cur.execute(
                """
                SELECT MIN(data_sla), MAX(data_sla)
                FROM entregas
                WHERE data_sla IS NOT NULL;
                """
            )
            datas = cur.fetchone()

            cur.execute(
                """
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
                """
            )
            responsaveis = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                SELECT DISTINCT rm
                FROM entregas
                WHERE rm IS NOT NULL
                  AND TRIM(rm) <> ''
                ORDER BY rm;
                """
            )
            rms = [row[0] for row in cur.fetchall()]

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
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql = f"""
        SELECT
            COUNT(*)::bigint AS total_pedidos,
            COUNT(*) FILTER (WHERE no_prazo)::bigint AS no_prazo,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            COUNT(*) FILTER (
                WHERE status_sla = 'Entregue fora do prazo'
            )::bigint AS entregue_fora_prazo,
            COUNT(*) FILTER (
                WHERE status_sla = 'Não entregue'
            )::bigint AS nao_entregue,
            COALESCE(
                AVG(
                    CASE
                        WHEN no_prazo THEN 1.0
                        ELSE 0.0
                    END
                ),
                0
            ) AS sla
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
        "sla": float(row[5] or 0),
        "meta_diaria": 0.95,
        "meta_mensal": 0.97,
    }


@app.get("/api/sla-diario")
def sla_diario(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql = f"""
        SELECT
            data_sla,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE no_prazo)::bigint AS no_prazo,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(
                CASE
                    WHEN no_prazo THEN 1.0
                    ELSE 0.0
                END
            ) AS sla
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
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql = f"""
        SELECT
            rm,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE no_prazo)::bigint AS no_prazo,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(
                CASE
                    WHEN no_prazo THEN 1.0
                    ELSE 0.0
                END
            ) AS sla
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
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql = f"""
        WITH responsaveis AS (
            SELECT
                coordenador AS responsavel,
                'Coordenador'::text AS funcao,
                COUNT(*)::bigint AS pedidos,
                COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
                AVG(
                    CASE
                        WHEN no_prazo THEN 1.0
                        ELSE 0.0
                    END
                ) AS sla
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
                AVG(
                    CASE
                        WHEN no_prazo THEN 1.0
                        ELSE 0.0
                    END
                ) AS sla
            FROM entregas
            WHERE {where}
              AND supervisor IS NOT NULL
              AND TRIM(supervisor) <> ''
            GROUP BY supervisor
        )
        SELECT
            responsavel,
            funcao,
            pedidos,
            fora_prazo,
            sla
        FROM responsaveis
        ORDER BY sla ASC, fora_prazo DESC, pedidos DESC
        LIMIT %s;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                params + params + [limit],
            )
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
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql = f"""
        SELECT
            codigo_base,
            base_entrega,
            coordenador,
            supervisor,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(
                CASE
                    WHEN no_prazo THEN 1.0
                    ELSE 0.0
                END
            ) AS sla
        FROM entregas
        WHERE {where}
        GROUP BY
            codigo_base,
            base_entrega,
            coordenador,
            supervisor
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
            "fora_prazo": row[5],
            "sla": float(row[6] or 0),
        }
        for row in rows
    ]


@app.get("/api/piores-entregadores")
def piores_entregadores(
    limit: int = Query(50, ge=1, le=5000),
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql_total = f"""
        SELECT COUNT(*)
        FROM (
            SELECT entregador
            FROM entregas
            WHERE {where}
              AND entregador IS NOT NULL
              AND TRIM(entregador) <> ''
            GROUP BY entregador
        ) t;
    """

    sql = f"""
        SELECT
            codigo_base,
            base_entrega,
            entregador,

            COUNT(*) FILTER (
                WHERE status_sla = 'No prazo'
            )::bigint AS no_prazo,

            COUNT(*) FILTER (
                WHERE status_sla = 'Não entregue'
            )::bigint AS nao_entregue,

            COUNT(*) FILTER (
                WHERE status_sla = 'Entregue fora do prazo'
            )::bigint AS fora_prazo,

            COUNT(*)::bigint AS total,

            AVG(
                CASE
                    WHEN no_prazo THEN 1.0
                    ELSE 0.0
                END
            ) AS sla

        FROM entregas

        WHERE {where}
          AND entregador IS NOT NULL
          AND TRIM(entregador) <> ''

        GROUP BY
            codigo_base,
            base_entrega,
            entregador

        ORDER BY
            sla ASC,
            nao_entregue DESC,
            fora_prazo DESC,
            total DESC,
            entregador ASC

        LIMIT %s;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_total, params)
            total_entregadores = cur.fetchone()[0]

            cur.execute(sql, params + [limit])
            rows = cur.fetchall()

    return {
        "total_entregadores": total_entregadores,
        "limite": limit,
        "itens": [
            {
                "codigo_base": row[0],
                "base_entrega": row[1],
                "entregador": row[2],
                "no_prazo": row[3],
                "nao_entregue": row[4],
                "fora_prazo": row[5],
                "total": row[6],
                "sla": float(row[7] or 0),
            }
            for row in rows
        ],
    }



@app.get("/api/entregadores-detalhes")
def entregadores_detalhes(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql = f"""
        SELECT
            codigo_base,
            base_entrega,
            entregador,
            numero_remessa,
            status_sla,
            no_prazo,
            data_sla
        FROM entregas
        WHERE {where}
          AND entregador IS NOT NULL
          AND TRIM(entregador) <> ''
        ORDER BY
            entregador ASC,
            data_sla ASC,
            numero_remessa ASC;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "codigo_base": row[0],
            "base_entrega": row[1],
            "entregador": row[2],
            "numero_remessa": row[3],
            "status_sla": row[4],
            "no_prazo": bool(row[5]),
            "data_sla": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]



@app.get("/api/entregadores-csv")
def entregadores_csv(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    # numero_remessa é o Número da Remessa da coluna A do Excel,
    # salvo individualmente no banco, uma linha por pacote.
    sql = f"""
        SELECT
            numero_remessa,
            base_entrega,
            entregador,
            data_sla,
            status_sla
        FROM entregas
        WHERE {where}
          AND numero_remessa IS NOT NULL
          AND TRIM(numero_remessa) <> ''
          AND entregador IS NOT NULL
          AND TRIM(entregador) <> ''
        ORDER BY
            entregador ASC,
            data_sla ASC,
            numero_remessa ASC;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    def csv_field(value):
        text = "" if value is None else str(value)
        return '"' + text.replace('"', '""') + '"'

    linhas = [
        ";".join([
            csv_field("Número da Remessa"),
            csv_field("Base"),
            csv_field("Entregador"),
            csv_field("Data SLA"),
            csv_field("Status SLA"),
        ])
    ]

    for numero_remessa, base_entrega, entregador, data_sla, status_sla in rows:
        linhas.append(
            ";".join([
                csv_field(numero_remessa),
                csv_field(base_entrega),
                csv_field(entregador),
                csv_field(data_sla.isoformat() if data_sla else ""),
                csv_field(status_sla),
            ])
        )

    conteudo = "\ufeff" + "\r\n".join(linhas)

    if data_inicio and data_fim and data_inicio == data_fim:
        nome = f"entregadores_remessas_{data_inicio.isoformat()}.csv"
    elif data_inicio and data_fim:
        nome = (
            f"entregadores_remessas_"
            f"{data_inicio.isoformat()}_a_{data_fim.isoformat()}.csv"
        )
    else:
        nome = "entregadores_remessas.csv"

    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"'
        },
    )


@app.get("/api/ranking-bases")
def ranking_bases(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    rm: str | None = None,
):
    where, params = montar_filtros(
        data_inicio,
        data_fim,
        responsavel,
        rm,
    )

    sql = f"""
        SELECT
            codigo_base,
            base_entrega,
            rm,
            coordenador,
            supervisor,
            COUNT(*)::bigint AS pedidos,
            COUNT(*) FILTER (WHERE NOT no_prazo)::bigint AS fora_prazo,
            AVG(
                CASE
                    WHEN no_prazo THEN 1.0
                    ELSE 0.0
                END
            ) AS sla
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
            "fora_prazo": row[6],
            "sla": float(row[7] or 0),
        }
        for row in rows
    ]


# ============================================================
# MÓDULO PRAZO
# Totalmente isolado dos filtros/endpoints da Visão Geral SLA.
# O universo de pacotes é a própria tabela entregas (mesma base SLA).
# ============================================================

def montar_filtros_prazo(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    base: str | None = None,
):
    where = ["1=1"]
    params = []

    # A data do módulo Prazo usa a MESMA data_sla da Visão Geral.
    # Isso garante que o volume do filtro bata com a base SLA.
    if data_inicio:
        where.append("data_sla >= %s")
        params.append(data_inicio)

    if data_fim:
        where.append("data_sla <= %s")
        params.append(data_fim)

    if responsavel:
        where.append("(coordenador = %s OR supervisor = %s)")
        params.extend([responsavel, responsavel])

    if base:
        where.append("base_entrega = %s")
        params.append(base)

    return " AND ".join(where), params


PRAZO_STATUS_SQL = """
CASE
    WHEN meta_total IS NULL
      OR data_limite_prazo IS NULL
      OR data_expedicao_sc IS NULL
        THEN 'Sem meta cadastrada'
    WHEN data_hora_entrega IS NOT NULL
      AND data_hora_entrega::date <= data_limite_prazo
        THEN 'Dentro da meta'
    WHEN data_hora_entrega IS NOT NULL
      AND data_hora_entrega::date > data_limite_prazo
        THEN 'Fora da meta'
    WHEN data_hora_entrega IS NULL
      AND CURRENT_DATE > data_limite_prazo
        THEN 'Fora da meta'
    ELSE 'Aguardando prazo'
END
"""


@app.get("/api/prazo/filtros")
def prazo_filtros(
    responsavel: str | None = None,
):
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
            responsaveis = [row[0] for row in cur.fetchall()]

            params = []
            filtro = "1=1"
            if responsavel:
                filtro += " AND (coordenador = %s OR supervisor = %s)"
                params.extend([responsavel, responsavel])

            cur.execute(
                f"""
                SELECT DISTINCT base_entrega
                FROM entregas
                WHERE {filtro}
                  AND base_entrega IS NOT NULL
                  AND TRIM(base_entrega) <> ''
                ORDER BY base_entrega;
                """,
                params,
            )
            bases = [row[0] for row in cur.fetchall()]

    return {
        "data_min": datas[0].isoformat() if datas and datas[0] else None,
        "data_max": datas[1].isoformat() if datas and datas[1] else None,
        "responsaveis": responsaveis,
        "bases": bases,
    }


@app.get("/api/prazo/kpis")
def prazo_kpis(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    base: str | None = None,
):
    where, params = montar_filtros_prazo(
        data_inicio, data_fim, responsavel, base
    )

    sql = f"""
        WITH dados AS (
            SELECT
                *,
                {PRAZO_STATUS_SQL} AS resultado_prazo
            FROM entregas
            WHERE {where}
        )
        SELECT
            COUNT(*)::bigint AS total_sla,
            COUNT(*) FILTER (
                WHERE resultado_prazo IN ('Dentro da meta', 'Fora da meta')
            )::bigint AS avaliados,
            COUNT(*) FILTER (
                WHERE resultado_prazo = 'Dentro da meta'
            )::bigint AS dentro_meta,
            COUNT(*) FILTER (
                WHERE resultado_prazo = 'Fora da meta'
            )::bigint AS fora_meta,
            COUNT(*) FILTER (
                WHERE resultado_prazo = 'Aguardando prazo'
            )::bigint AS aguardando,
            COUNT(*) FILTER (
                WHERE resultado_prazo = 'Sem meta cadastrada'
            )::bigint AS sem_meta,
            COALESCE(
                AVG(
                    CASE
                        WHEN resultado_prazo = 'Dentro da meta' THEN 1.0
                        WHEN resultado_prazo = 'Fora da meta' THEN 0.0
                        ELSE NULL
                    END
                ),
                0
            ) AS cumprimento,
            MIN(meta_total) FILTER (WHERE meta_total IS NOT NULL) AS meta_min,
            MAX(meta_total) FILTER (WHERE meta_total IS NOT NULL) AS meta_max,
            COUNT(DISTINCT meta_total) FILTER (
                WHERE meta_total IS NOT NULL
            )::bigint AS qtd_metas
        FROM dados;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    return {
        "total_sla": row[0],
        "avaliados": row[1],
        "dentro_meta": row[2],
        "fora_meta": row[3],
        "aguardando": row[4],
        "sem_meta": row[5],
        "cumprimento": float(row[6]) if row[1] and row[6] is not None else None,
        "meta_min": float(row[7]) if row[7] is not None else None,
        "meta_max": float(row[8]) if row[8] is not None else None,
        "qtd_metas": row[9],
    }


@app.get("/api/prazo/diario")
def prazo_diario(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    base: str | None = None,
):
    where, params = montar_filtros_prazo(
        data_inicio, data_fim, responsavel, base
    )

    sql = f"""
        WITH dados AS (
            SELECT
                data_sla,
                {PRAZO_STATUS_SQL} AS resultado_prazo
            FROM entregas
            WHERE {where}
              AND data_sla IS NOT NULL
        )
        SELECT
            data_sla,
            COUNT(*)::bigint AS total_sla,
            COUNT(*) FILTER (
                WHERE resultado_prazo IN ('Dentro da meta', 'Fora da meta')
            )::bigint AS avaliados,
            COUNT(*) FILTER (
                WHERE resultado_prazo = 'Dentro da meta'
            )::bigint AS dentro_meta,
            COUNT(*) FILTER (
                WHERE resultado_prazo = 'Fora da meta'
            )::bigint AS fora_meta,
            COALESCE(
                AVG(
                    CASE
                        WHEN resultado_prazo = 'Dentro da meta' THEN 1.0
                        WHEN resultado_prazo = 'Fora da meta' THEN 0.0
                        ELSE NULL
                    END
                ),
                0
            ) AS cumprimento
        FROM dados
        GROUP BY data_sla
        ORDER BY data_sla;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "data": row[0].isoformat() if row[0] else None,
            "total_sla": row[1],
            "avaliados": row[2],
            "dentro_meta": row[3],
            "fora_meta": row[4],
            "cumprimento": float(row[5] or 0),
        }
        for row in rows
    ]


@app.get("/api/prazo/analise-csv")
def prazo_analise_csv(
    data_inicio: date | None = None,
    data_fim: date | None = None,
    responsavel: str | None = None,
    base: str | None = None,
):
    where, params = montar_filtros_prazo(
        data_inicio, data_fim, responsavel, base
    )

    sql = f"""
        SELECT
            numero_remessa,
            data_sla,
            coordenador,
            supervisor,
            codigo_base,
            base_entrega,
            cidade_base,
            prazo_atual_transferencia,
            prazo_atual_entrega,
            meta_transit,
            meta_entrega,
            meta_total,
            data_expedicao_sc,
            data_limite_prazo,
            data_hora_entrega,
            prazo_real_dias,
            {PRAZO_STATUS_SQL} AS resultado_prazo
        FROM entregas
        WHERE {where}
        ORDER BY data_sla, base_entrega, cidade_destino, numero_remessa;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    def csv_field(value):
        text = "" if value is None else str(value)
        return '"' + text.replace('"', '""') + '"'

    cabecalho = [
        "Número da Remessa",
        "Data SLA",
        "Coordenador",
        "Supervisor",
        "Código Base",
        "Base de entrega",
        "Cidade da Base",
        "Prazo atual transferência (W)",
        "Prazo atual entrega (Y)",
        "Nova meta Transit",
        "Nova meta Entrega",
        "Meta total",
        "Expedição SC/HUB",
        "Data limite da meta",
        "Data/Hora entrega",
        "Prazo real (dias)",
        "Resultado",
    ]

    linhas = [";".join(csv_field(x) for x in cabecalho)]

    for row in rows:
        valores = list(row)
        for idx in [1, 12, 13, 14]:
            if valores[idx] is not None and hasattr(valores[idx], "isoformat"):
                valores[idx] = valores[idx].isoformat()
        linhas.append(";".join(csv_field(x) for x in valores))

    conteudo = "\ufeff" + "\r\n".join(linhas)

    if data_inicio and data_fim and data_inicio == data_fim:
        nome = f"analise_prazo_{data_inicio.isoformat()}.csv"
    elif data_inicio and data_fim:
        nome = (
            f"analise_prazo_{data_inicio.isoformat()}_a_"
            f"{data_fim.isoformat()}.csv"
        )
    else:
        nome = "analise_prazo.csv"

    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nome}"'
        },
    )
