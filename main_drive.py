from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from drive_store import carregar_periodo, carregar_ultima_data, datas_disponiveis, status_fonte

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="J&T SLA Dashboard")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _today():
    return datetime.now(ZoneInfo("America/Belem")).date()


def _safe_text(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def _filtrar(df, responsavel=None, rm=None):
    if df.empty:
        return df.copy()
    out = df
    if responsavel:
        out = out[(out["coordenador"] == responsavel) | (out["supervisor"] == responsavel)]
    if rm:
        out = out[out["rm"] == rm]
    return out.copy()


def _dados(data_inicio, data_fim, responsavel=None, rm=None):
    return _filtrar(carregar_periodo(data_inicio, data_fim), responsavel, rm)


def _registros_grupo(g):
    total = int(len(g))
    no_prazo = int(g["no_prazo"].sum()) if total else 0
    return {"pedidos": total, "no_prazo": no_prazo, "fora_prazo": total - no_prazo, "sla": no_prazo / total if total else 0.0}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/health")
@app.get("/healthz")
def health():
    return {"status": "ok", "fonte": status_fonte()}


@app.get("/api/filtros")
def filtros():
    datas = datas_disponiveis()
    df = carregar_ultima_data()
    nomes = sorted(set(x for x in pd.concat([df.get("coordenador", pd.Series(dtype=str)), df.get("supervisor", pd.Series(dtype=str))]).astype(str) if x and x != "nan"))
    rms = sorted(x for x in df.get("rm", pd.Series(dtype=str)).astype(str).unique() if x and x != "nan")
    return {"data_min": min(datas).isoformat() if datas else None, "data_max": max(datas).isoformat() if datas else None, "responsaveis": nomes, "rms": rms, "fonte": status_fonte()}


@app.get("/api/kpis")
def kpis(data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df = _dados(data_inicio, data_fim, responsavel, rm)
    total = int(len(df)); no = int(df["no_prazo"].sum()) if total else 0
    entregue_fora = int((df["status_sla"] == "Entregue fora do prazo").sum()) if total else 0
    nao_entregue = int((df["status_sla"] == "Não entregue").sum()) if total else 0
    return {"total_pedidos": total, "no_prazo": no, "fora_prazo": total-no, "entregue_fora_prazo": entregue_fora, "nao_entregue": nao_entregue, "sla": no/total if total else 0.0, "meta_diaria": 0.95, "meta_mensal": 0.97}


@app.get("/api/sla-diario")
def sla_diario(data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df = _dados(data_inicio, data_fim, responsavel, rm)
    if df.empty:
        return []
    out=[]
    for dt,g in df.groupby("data_sla", sort=True):
        out.append({"data_sla":str(dt), **_registros_grupo(g)})
    return out


@app.get("/api/sla-rm")
def sla_rm(data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df = _dados(data_inicio, data_fim, responsavel, rm)
    df = df[df["rm"].astype(str).str.strip() != ""]
    rows=[]
    for nome,g in df.groupby("rm", sort=False):
        rows.append({"rm":nome, **_registros_grupo(g)})
    return sorted(rows, key=lambda x:(x["sla"],-x["pedidos"]))


@app.get("/api/piores-responsaveis")
def piores_responsaveis(limit: int = Query(15, ge=1, le=100), data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df = _dados(data_inicio, data_fim, responsavel, rm)
    rows=[]
    for col,funcao in [("coordenador","Coordenador"),("supervisor","Supervisor")]:
        part=df[df[col].astype(str).str.strip() != ""]
        for nome,g in part.groupby(col, sort=False):
            rows.append({"responsavel":nome,"funcao":funcao,**_registros_grupo(g)})
    rows.sort(key=lambda x:(x["sla"],-x["fora_prazo"],-x["pedidos"]))
    return rows[:limit]


@app.get("/api/piores-bases")
def piores_bases(limit: int = Query(20, ge=1, le=200), data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df = _dados(data_inicio, data_fim, responsavel, rm)
    rows=[]
    cols=["codigo_base","base_entrega","coordenador","supervisor"]
    for keys,g in df.groupby(cols, dropna=False, sort=False):
        rows.append(dict(zip(cols,keys)) | _registros_grupo(g))
    rows.sort(key=lambda x:(x["sla"],-x["fora_prazo"],-x["pedidos"]))
    return rows[:limit]


@app.get("/api/piores-entregadores")
def piores_entregadores(limit: int = Query(50, ge=1, le=5000), data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df = _dados(data_inicio, data_fim, responsavel, rm)
    df=df[df["entregador"].astype(str).str.strip() != ""]
    total_entregadores=int(df["entregador"].nunique())
    rows=[]
    for keys,g in df.groupby(["codigo_base","base_entrega","entregador"], dropna=False, sort=False):
        total=int(len(g)); no=int((g["status_sla"]=="No prazo").sum()); nao=int((g["status_sla"]=="Não entregue").sum()); fora=int((g["status_sla"]=="Entregue fora do prazo").sum())
        rows.append({"codigo_base":keys[0],"base_entrega":keys[1],"entregador":keys[2],"no_prazo":no,"nao_entregue":nao,"fora_prazo":fora,"total":total,"sla":no/total if total else 0.0})
    rows.sort(key=lambda x:(x["sla"],-x["nao_entregue"],-x["fora_prazo"],-x["total"],str(x["entregador"])))
    return {"total_entregadores":total_entregadores,"limite":limit,"itens":rows[:limit]}


@app.get("/api/entregadores-detalhes")
def entregadores_detalhes(data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df=_dados(data_inicio,data_fim,responsavel,rm)
    df=df[df["entregador"].astype(str).str.strip() != ""].sort_values(["entregador","data_sla","numero_remessa"])
    cols=["codigo_base","base_entrega","entregador","numero_remessa","status_sla","no_prazo","data_sla"]
    return [{**{c:_safe_text(row[c]) for c in cols if c != "no_prazo"},"no_prazo":bool(row["no_prazo"])} for _,row in df[cols].iterrows()]


def _csv_response(df, nome):
    buf=io.StringIO(); df.to_csv(buf,index=False,sep=";")
    return Response(content="\ufeff"+buf.getvalue(),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="{nome}"'})


@app.get("/api/entregadores-csv")
def entregadores_csv(data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df=_dados(data_inicio,data_fim,responsavel,rm)
    df=df[(df["numero_remessa"].astype(str).str.strip()!="")&(df["entregador"].astype(str).str.strip()!="")]
    out=df[["numero_remessa","base_entrega","entregador","data_sla","status_sla"]].copy()
    out.columns=["Número da Remessa","Base","Entregador","Data SLA","Status SLA"]
    if data_inicio and data_fim and data_inicio==data_fim: nome=f"entregadores_remessas_{data_inicio}.csv"
    elif data_inicio and data_fim: nome=f"entregadores_remessas_{data_inicio}_a_{data_fim}.csv"
    else: nome="entregadores_remessas.csv"
    return _csv_response(out.sort_values(["Entregador","Data SLA","Número da Remessa"]),nome)


@app.get("/api/ranking-bases")
def ranking_bases(data_inicio: date | None = None, data_fim: date | None = None, responsavel: str | None = None, rm: str | None = None):
    df=_dados(data_inicio,data_fim,responsavel,rm)
    rows=[]; cols=["codigo_base","base_entrega","rm","coordenador","supervisor"]
    for keys,g in df.groupby(cols,dropna=False,sort=False):
        rows.append(dict(zip(cols,keys)) | _registros_grupo(g))
    rows.sort(key=lambda x:(x["sla"],-x["fora_prazo"],-x["pedidos"]))
    return rows


def _filtrar_prazo(data_inicio=None,data_fim=None,responsavel=None,base=None):
    df=carregar_periodo(data_inicio,data_fim)
    if responsavel:
        df=df[(df["coordenador"]==responsavel)|(df["supervisor"]==responsavel)]
    if base:
        df=df[df["base_entrega"]==base]
    return df.copy()


def _resultado_prazo(df):
    if df.empty:
        return pd.Series(dtype="string",index=df.index)
    meta=pd.to_numeric(df["meta_total"],errors="coerce")
    limite=pd.to_datetime(df["data_limite_prazo"],errors="coerce")
    exped=pd.to_datetime(df["data_expedicao_sc"],errors="coerce")
    entrega=pd.to_datetime(df["data_hora_entrega"],errors="coerce")
    result=pd.Series("Aguardando prazo",index=df.index,dtype="string")
    sem=meta.isna()|limite.isna()|exped.isna(); result.loc[sem]="Sem meta cadastrada"
    aval=(~sem)&entrega.notna(); result.loc[aval&(entrega.dt.normalize()<=limite.dt.normalize())]="Dentro da meta"; result.loc[aval&(entrega.dt.normalize()>limite.dt.normalize())]="Fora da meta"
    venceu=(~sem)&entrega.isna()&(limite.dt.date<_today()); result.loc[venceu]="Fora da meta"
    return result


@app.get("/api/prazo/filtros")
def prazo_filtros(responsavel: str | None = None):
    datas=datas_disponiveis(); df=carregar_ultima_data()
    nomes=sorted(set(x for x in pd.concat([df.get("coordenador",pd.Series(dtype=str)),df.get("supervisor",pd.Series(dtype=str))]).astype(str) if x and x!="nan"))
    if responsavel:
        df=df[(df["coordenador"]==responsavel)|(df["supervisor"]==responsavel)]
    bases=sorted(x for x in df.get("base_entrega",pd.Series(dtype=str)).astype(str).unique() if x and x!="nan")
    return {"data_min":min(datas).isoformat() if datas else None,"data_max":max(datas).isoformat() if datas else None,"responsaveis":nomes,"bases":bases}


def _prazo_stats(df):
    r=_resultado_prazo(df); total=int(len(df)); avaliados=int(r.isin(["Dentro da meta","Fora da meta"]).sum()); dentro=int((r=="Dentro da meta").sum()); fora=int((r=="Fora da meta").sum()); aguardando=int((r=="Aguardando prazo").sum()); sem=int((r=="Sem meta cadastrada").sum())
    meta=pd.to_numeric(df["meta_total"],errors="coerce") if not df.empty else pd.Series(dtype=float)
    com=int(meta.notna().sum())
    sem_reais=int((meta.notna()&(pd.to_datetime(df["data_expedicao_sc"],errors="coerce").isna()|pd.to_datetime(df["data_limite_prazo"],errors="coerce").isna())).sum()) if not df.empty else 0
    valid=meta.dropna()
    return {"total_sla":total,"avaliados":avaliados,"dentro_meta":dentro,"fora_meta":fora,"aguardando":aguardando,"sem_meta":sem,"com_meta":com,"sem_dados_reais":sem_reais,"cumprimento":dentro/avaliados if avaliados else None,"meta_media":float(valid.mean()) if len(valid) else None,"meta_min":float(valid.min()) if len(valid) else None,"meta_max":float(valid.max()) if len(valid) else None,"qtd_metas":int(valid.nunique()) if len(valid) else 0}


@app.get("/api/prazo/kpis")
def prazo_kpis(data_inicio: date | None = None,data_fim: date | None = None,responsavel: str | None = None,base: str | None = None):
    return _prazo_stats(_filtrar_prazo(data_inicio,data_fim,responsavel,base))


@app.get("/api/prazo/diario")
def prazo_diario(data_inicio: date | None = None,data_fim: date | None = None,responsavel: str | None = None,base: str | None = None):
    df=_filtrar_prazo(data_inicio,data_fim,responsavel,base); out=[]
    for dt,g in df.groupby("data_sla",sort=True):
        s=_prazo_stats(g); out.append({"data":str(dt),"total_sla":s["total_sla"],"avaliados":s["avaliados"],"dentro_meta":s["dentro_meta"],"fora_meta":s["fora_meta"],"com_meta":s["com_meta"],"meta_media":s["meta_media"],"cumprimento":s["cumprimento"]})
    return out


@app.get("/api/prazo/analise-csv")
def prazo_analise_csv(data_inicio: date | None = None,data_fim: date | None = None,responsavel: str | None = None,base: str | None = None):
    df=_filtrar_prazo(data_inicio,data_fim,responsavel,base)
    df=df.copy(); df["resultado_prazo"]=_resultado_prazo(df)
    cols=["numero_remessa","data_sla","coordenador","supervisor","codigo_base","base_entrega","cidade_base","prazo_atual_transferencia","prazo_atual_entrega","meta_transit","meta_entrega","meta_total","data_expedicao_sc","data_limite_prazo","data_hora_entrega","prazo_real_dias","resultado_prazo"]
    out=df[cols].sort_values(["data_sla","base_entrega","cidade_base","numero_remessa"]).copy()
    out.columns=["Número da Remessa","Data SLA","Coordenador","Supervisor","Código Base","Base de entrega","Cidade da Base","Prazo atual transferência (W)","Prazo atual entrega (Y)","Nova meta Transit","Nova meta Entrega","Meta total","Expedição SC/HUB","Data limite da meta","Data/Hora entrega","Prazo real (dias)","Resultado"]
    if data_inicio and data_fim and data_inicio==data_fim: nome=f"analise_prazo_{data_inicio}.csv"
    elif data_inicio and data_fim: nome=f"analise_prazo_{data_inicio}_a_{data_fim}.csv"
    else: nome="analise_prazo.csv"
    return _csv_response(out,nome)
