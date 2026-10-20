const nf = new Intl.NumberFormat("pt-BR");

let chartDiario = null;
let chartRm = null;

function pct(v){
  return `${(Number(v || 0) * 100).toFixed(2).replace(".", ",")}%`;
}


function dataLocalISO(data){
  const ano = data.getFullYear();
  const mes = String(data.getMonth() + 1).padStart(2, "0");
  const dia = String(data.getDate()).padStart(2, "0");
  return `${ano}-${mes}-${dia}`;
}

function definirDataPadraoOntem(){
  const ontem = new Date();
  ontem.setHours(12, 0, 0, 0);
  ontem.setDate(ontem.getDate() - 1);

  const valor = dataLocalISO(ontem);

  document.getElementById("dataInicio").value = valor;
  document.getElementById("dataFim").value = valor;
}

function csvCampo(valor){
  const texto = String(valor ?? "");
  return `"${texto.replaceAll('"', '""')}"`;
}

function nomeArquivoRanking(){
  const ini = document.getElementById("dataInicio").value || "inicio";
  const fim = document.getElementById("dataFim").value || "fim";

  if(ini === fim){
    return `ranking_bases_${ini}.csv`;
  }

  return `ranking_bases_${ini}_a_${fim}.csv`;
}

async function getJSON(url){
  const response = await fetch(url);
  if(!response.ok) throw new Error(`${response.status} - ${response.statusText}`);
  return response.json();
}

function montarQuery(){
  const params = new URLSearchParams();

  const dataInicio = document.getElementById("dataInicio").value;
  const dataFim = document.getElementById("dataFim").value;
  const responsavel = document.getElementById("responsavel").value;
  const rm = document.getElementById("rm").value;

  if(dataInicio) params.set("data_inicio", dataInicio);
  if(dataFim) params.set("data_fim", dataFim);
  if(responsavel) params.set("responsavel", responsavel);
  if(rm) params.set("rm", rm);

  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

async function carregarOpcoesFiltros(){
  const d = await getJSON("/api/filtros");

  const dataInicio = document.getElementById("dataInicio");
  const dataFim = document.getElementById("dataFim");

  if(d.data_min){
    dataInicio.min = d.data_min;
    dataFim.min = d.data_min;
  }
  if(d.data_max){
    dataInicio.max = d.data_max;
    dataFim.max = d.data_max;
  }

  const responsavel = document.getElementById("responsavel");
  d.responsaveis.forEach(v => {
    responsavel.insertAdjacentHTML("beforeend", `<option value="${v}">${v}</option>`);
  });

  const rm = document.getElementById("rm");
  d.rms.forEach(v => {
    rm.insertAdjacentHTML("beforeend", `<option value="${v}">${v}</option>`);
  });
}

async function carregarKPIs(){
  const d = await getJSON(`/api/kpis${montarQuery()}`);
  document.getElementById("slaGeral").textContent = pct(d.sla);
  document.getElementById("totalPedidos").textContent = nf.format(d.total_pedidos);
  document.getElementById("noPrazo").textContent = nf.format(d.no_prazo);
  document.getElementById("foraPrazo").textContent = nf.format(d.fora_prazo);
}

async function carregarDiario(){
  const data = await getJSON(`/api/sla-diario${montarQuery()}`);

  if(chartDiario) chartDiario.destroy();

  chartDiario = new Chart(document.getElementById("chartDiario"), {
    type: "line",
    data: {
      labels: data.map(x => x.data_sla),
      datasets: [
        {
          label: "SLA",
          data: data.map(x => Number(x.sla) * 100),
          tension: .28,
          borderWidth: 3,
          pointRadius: 3
        },
        {
          label: "Meta",
          data: data.map(() => 100),
          borderWidth: 1.5,
          pointRadius: 0,
          borderDash: [6, 6]
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { y: { min: 0, max: 100, ticks: { callback: v => `${v}%` } } },
      plugins: { legend: { position: "bottom" } }
    }
  });
}

async function carregarRm(){
  const data = await getJSON(`/api/sla-rm${montarQuery()}`);

  if(chartRm) chartRm.destroy();

  chartRm = new Chart(document.getElementById("chartRm"), {
    type: "bar",
    data: {
      labels: data.map(x => x.rm || "Sem R/M"),
      datasets: [
        {
          label: "SLA",
          data: data.map(x => Number(x.sla) * 100),
          borderWidth: 1,
          borderRadius: 8
        }
      ]
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      scales: { x: { min: 0, max: 100, ticks: { callback: v => `${v}%` } } },
      plugins: { legend: { display: false } }
    }
  });
}

async function carregarResponsaveis(){
  const q = montarQuery();
  const sep = q ? "&" : "?";
  const data = await getJSON(`/api/piores-responsaveis${q}${sep}limit=15`);

  document.getElementById("tbodyResponsaveis").innerHTML = data.map(x => `
    <tr>
      <td>${x.responsavel ?? ""}</td>
      <td>${x.funcao ?? ""}</td>
      <td>${nf.format(x.pedidos ?? 0)}</td>
      <td>${nf.format(x.fora_prazo ?? 0)}</td>
      <td class="sla-bad">${pct(x.sla)}</td>
    </tr>
  `).join("");
}

async function carregarBases(){
  const q = montarQuery();
  const sep = q ? "&" : "?";
  const data = await getJSON(`/api/piores-bases${q}${sep}limit=20`);

  document.getElementById("tbodyBases").innerHTML = data.map(x => {
    const coordenador = (x.coordenador ?? "").trim();
    const supervisor = (x.supervisor ?? "").trim();

    let responsavel = "Sem responsável";

    if (coordenador) {
      responsavel = `${coordenador} (Coordenador)`;
    } else if (supervisor) {
      responsavel = `${supervisor} (Supervisor)`;
    }

    return `
      <tr>
        <td>${x.base_entrega ?? x.codigo_base ?? ""}</td>
        <td>${responsavel}</td>
        <td>${nf.format(x.pedidos ?? 0)}</td>
        <td>${nf.format(x.fora_prazo ?? 0)}</td>
        <td class="sla-bad">${pct(x.sla)}</td>
      </tr>
    `;
  }).join("");
}



async function carregarRankingBases(){
  const data = await getJSON(`/api/ranking-bases${montarQuery()}`);
  const tbody = document.getElementById("tbodyRankingBases");

  tbody.innerHTML = data.map((x, i) => {
    const sla = Number(x.sla || 0) * 100;
    const largura = Math.max(0, Math.min(100, sla));

    return `
      <tr>
        <td>${i + 1}</td>
        <td>${x.codigo_base ?? ""}</td>
        <td>${x.base_entrega ?? ""}</td>
        <td>${x.rm ?? ""}</td>
        <td>${x.coordenador ?? "—"}</td>
        <td>${x.supervisor ?? "—"}</td>
        <td>${nf.format(x.pedidos ?? 0)}</td>
        <td>${nf.format(x.fora_prazo ?? 0)}</td>
        <td>
          <div class="sla-cell">
            <div class="sla-track">
              <div class="sla-fill" style="width:${largura}%"></div>
            </div>
            <strong>${pct(x.sla)}</strong>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}


async function baixarRanking(){
  const data = await getJSON(`/api/ranking-bases${montarQuery()}`);

  const cabecalho = [
    "#",
    "Código Base",
    "Base de entrega",
    "R/M",
    "Coordenador",
    "Supervisor",
    "Pedidos",
    "Fora do prazo",
    "SLA"
  ];

  const linhas = [
    cabecalho.map(csvCampo).join(";"),
    ...data.map((x, i) => [
      i + 1,
      x.codigo_base ?? "",
      x.base_entrega ?? "",
      x.rm ?? "",
      x.coordenador ?? "",
      x.supervisor ?? "",
      x.pedidos ?? 0,
      x.fora_prazo ?? 0,
      `${(Number(x.sla || 0) * 100).toFixed(2).replace(".", ",")}%`
    ].map(csvCampo).join(";"))
  ];

  // BOM UTF-8 ajuda o Excel a abrir acentos corretamente.
  const blob = new Blob(["\ufeff" + linhas.join("\r\n")], {
    type: "text/csv;charset=utf-8;"
  });

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = nomeArquivoRanking();
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function carregarDashboard(){
  await Promise.all([
    carregarKPIs(),
    carregarDiario(),
    carregarRm(),
    carregarResponsaveis(),
    carregarBases(),
    carregarRankingBases()
  ]);
}

document.getElementById("btnAplicar").addEventListener("click", async () => {
  try{
    await carregarDashboard();
  }catch(err){
    console.error(err);
    alert("Erro ao aplicar os filtros.");
  }
});

document.getElementById("btnLimpar").addEventListener("click", async () => {
  definirDataPadraoOntem();
  document.getElementById("responsavel").value = "";
  document.getElementById("rm").value = "";

  try{
    await carregarDashboard();
  }catch(err){
    console.error(err);
    alert("Erro ao limpar os filtros.");
  }
});

document.getElementById("btnBaixarRanking").addEventListener("click", async () => {
  try{
    await baixarRanking();
  }catch(err){
    console.error(err);
    alert("Erro ao baixar o ranking.");
  }
});

async function iniciar(){
  try{
    definirDataPadraoOntem();
    await carregarOpcoesFiltros();
    await carregarDashboard();
  }catch(err){
    console.error(err);
    alert("Erro ao carregar o dashboard. Verifique se o FastAPI está conectado ao Supabase.");
  }
}

iniciar();
