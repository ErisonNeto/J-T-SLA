const nf = new Intl.NumberFormat("pt-BR");

let chartDiario = null;
let chartRm = null;
let exibindoTodosEntregadores = false;
let cacheTodosEntregadores = [];

Chart.defaults.font.family = 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.color = "#667085";
Chart.defaults.borderColor = "rgba(152, 162, 179, .16)";

function atualizarHorarioVisualizacao() {
  const alvo = document.getElementById("updatedAt");
  if (!alvo) return;

  const agora = new Date();
  alvo.textContent = agora.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}


function pct(valor) {
  return `${(Number(valor || 0) * 100).toFixed(2).replace(".", ",")}%`;
}

function classeSla(valor) {
  return Number(valor || 0) >= 0.95 ? "sla-good" : "sla-bad";
}

function dataLocalISO(data) {
  const ano = data.getFullYear();
  const mes = String(data.getMonth() + 1).padStart(2, "0");
  const dia = String(data.getDate()).padStart(2, "0");
  return `${ano}-${mes}-${dia}`;
}

function definirDataPadraoOntem() {
  const ontem = new Date();
  ontem.setHours(12, 0, 0, 0);
  ontem.setDate(ontem.getDate() - 1);

  const valor = dataLocalISO(ontem);

  document.getElementById("dataInicio").value = valor;
  document.getElementById("dataFim").value = valor;
}

async function getJSON(url) {
  const separador = url.includes("?") ? "&" : "?";

  const response = await fetch(`${url}${separador}_t=${Date.now()}`, {
    cache: "no-store"
  });

  if (!response.ok) {
    throw new Error(`${response.status} - ${response.statusText}`);
  }

  return response.json();
}

function montarQuery() {
  const params = new URLSearchParams();

  const dataInicio = document.getElementById("dataInicio").value;
  const dataFim = document.getElementById("dataFim").value;
  const responsavel = document.getElementById("responsavel").value;
  const rm = document.getElementById("rm").value;

  if (dataInicio) params.set("data_inicio", dataInicio);
  if (dataFim) params.set("data_fim", dataFim);
  if (responsavel) params.set("responsavel", responsavel);
  if (rm) params.set("rm", rm);

  const query = params.toString();
  return query ? `?${query}` : "";
}

async function carregarOpcoesFiltros() {
  const data = await getJSON("/api/filtros");

  const selectResponsavel = document.getElementById("responsavel");
  selectResponsavel.innerHTML = '<option value="">Todos</option>';

  (data.responsaveis || []).forEach((nome) => {
    const option = document.createElement("option");
    option.value = nome;
    option.textContent = nome;
    selectResponsavel.appendChild(option);
  });

  const selectRm = document.getElementById("rm");
  selectRm.innerHTML = '<option value="">Todos</option>';

  (data.rms || []).forEach((nome) => {
    const option = document.createElement("option");
    option.value = nome;
    option.textContent = nome;
    selectRm.appendChild(option);
  });
}

async function carregarKPIs() {
  const data = await getJSON(`/api/kpis${montarQuery()}`);

  document.getElementById("slaGeral").textContent = pct(data.sla);
  document.getElementById("totalPedidos").textContent = nf.format(data.total_pedidos || 0);
  document.getElementById("noPrazo").textContent = nf.format(data.no_prazo || 0);
  document.getElementById("foraPrazo").textContent = nf.format(data.fora_prazo || 0);
}

async function carregarDiario() {
  const data = await getJSON(`/api/sla-diario${montarQuery()}`);

  if (chartDiario) {
    chartDiario.destroy();
  }

  chartDiario = new Chart(document.getElementById("chartDiario"), {
    type: "line",
    data: {
      labels: data.map((item) => item.data_sla),
      datasets: [
        {
          label: "SLA",
          data: data.map((item) => Number(item.sla || 0) * 100),
          borderColor: "#d71920",
          backgroundColor: "rgba(215, 25, 32, .08)",
          pointBackgroundColor: "#ffffff",
          pointBorderColor: "#d71920",
          pointBorderWidth: 2,
          fill: true,
          borderWidth: 3,
          pointRadius: 3,
          pointHoverRadius: 6,
          tension: 0.32
        },
        {
          label: "Meta",
          data: data.map(() => 95),
          borderColor: "#159455",
          borderWidth: 1.5,
          pointRadius: 0,
          borderDash: [7, 6]
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false
      },
      scales: {
        y: {
          min: 0,
          max: 100,
          grid: {
            color: "rgba(152, 162, 179, .14)"
          },
          ticks: {
            callback: (valor) => `${valor}%`
          }
        },
        x: {
          grid: {
            display: false
          }
        }
      },
      plugins: {
        legend: {
          position: "bottom"
        },
        tooltip: {
          callbacks: {
            label: (context) =>
              `${context.dataset.label}: ${Number(context.raw).toFixed(2).replace(".", ",")}%`
          }
        }
      }
    }
  });
}

async function carregarRm() {
  const data = await getJSON(`/api/sla-rm${montarQuery()}`);

  if (chartRm) {
    chartRm.destroy();
  }

  chartRm = new Chart(document.getElementById("chartRm"), {
    type: "bar",
    data: {
      labels: data.map((item) => item.rm || "Sem R/M"),
      datasets: [
        {
          label: "SLA",
          data: data.map((item) => Number(item.sla || 0) * 100),
          backgroundColor: "rgba(215, 25, 32, .86)",
          borderColor: "#d71920",
          borderWidth: 1,
          borderRadius: 7
        }
      ]
    },
    options: {
      indexAxis: "y",
      layout: {
        padding: {
          top: 4,
          right: 10,
          bottom: 26,
          left: 4
        }
      },
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          min: 0,
          max: 100,
          grid: {
            color: "rgba(152, 162, 179, .14)"
          },
          ticks: {
            padding: 8,
            callback: (valor) => `${valor}%`
          }
        },
        y: {
          grid: {
            display: false
          }
        }
      },
      plugins: {
        legend: {
          display: false
        },
        tooltip: {
          callbacks: {
            label: (context) =>
              `SLA: ${Number(context.raw).toFixed(2).replace(".", ",")}%`
          }
        }
      }
    }
  });
}

async function carregarResponsaveis() {
  const query = montarQuery();
  const separador = query ? "&" : "?";

  const data = await getJSON(
    `/api/piores-responsaveis${query}${separador}limit=15`
  );

  const tbody = document.getElementById("tbodyResponsaveis");

  tbody.innerHTML = data.map((item) => `
    <tr>
      <td>${item.responsavel ?? ""}</td>
      <td>${item.funcao ?? ""}</td>
      <td>${nf.format(item.pedidos || 0)}</td>
      <td>${nf.format(item.fora_prazo || 0)}</td>
      <td class="${classeSla(item.sla)}">${pct(item.sla)}</td>
    </tr>
  `).join("");
}

async function carregarBases() {
  const query = montarQuery();
  const separador = query ? "&" : "?";

  const data = await getJSON(
    `/api/piores-bases${query}${separador}limit=20`
  );

  const tbody = document.getElementById("tbodyBases");

  tbody.innerHTML = data.map((item) => {
    const coordenador = String(item.coordenador ?? "").trim();
    const supervisor = String(item.supervisor ?? "").trim();

    let responsavel = "Sem responsável";

    if (coordenador) {
      responsavel = `${coordenador} (Coordenador)`;
    } else if (supervisor) {
      responsavel = `${supervisor} (Supervisor)`;
    }

    return `
      <tr>
        <td>${item.base_entrega ?? item.codigo_base ?? ""}</td>
        <td>${responsavel}</td>
        <td>${nf.format(item.pedidos || 0)}</td>
        <td>${nf.format(item.fora_prazo || 0)}</td>
        <td class="${classeSla(item.sla)}">${pct(item.sla)}</td>
      </tr>
    `;
  }).join("");
}

async function carregarEntregadores(limite = 50) {
  const query = montarQuery();
  const separador = query ? "&" : "?";

  const resposta = await getJSON(
    `/api/piores-entregadores${query}${separador}limit=${limite}`
  );

  const data = resposta.itens || [];
  const total = Number(resposta.total_entregadores || 0);

  document.getElementById("entregadoresResumo").textContent =
    `${Math.min(limite, total)} piores de ${total} entregadores`;

  const tbody = document.getElementById("tbodyEntregadores");

  tbody.innerHTML = data.map((item) => `
    <tr>
      <td>${item.base_entrega ?? item.codigo_base ?? ""}</td>
      <td>${item.entregador ?? ""}</td>
      <td>${nf.format(item.no_prazo || 0)}</td>
      <td>${nf.format(item.nao_entregue || 0)}</td>
      <td>${nf.format(item.fora_prazo || 0)}</td>
      <td class="${classeSla(item.sla)}">${pct(item.sla)}</td>
    </tr>
  `).join("");

  if (limite >= total && total > 0) {
    exibindoTodosEntregadores = true;
    cacheTodosEntregadores = data;
    document.getElementById("btnVerTodosEntregadores").textContent = "Mostrar 50";
  } else {
    exibindoTodosEntregadores = false;
    document.getElementById("btnVerTodosEntregadores").textContent = "Ver todos";
  }
}

async function carregarRankingBases() {
  const data = await getJSON(`/api/ranking-bases${montarQuery()}`);

  const tbody = document.getElementById("tbodyRankingBases");

  tbody.innerHTML = data.map((item, index) => {
    const slaPercentual = Number(item.sla || 0) * 100;
    const largura = Math.max(0, Math.min(100, slaPercentual));

    return `
      <tr>
        <td>${index + 1}</td>
        <td>${item.codigo_base ?? ""}</td>
        <td>${item.base_entrega ?? ""}</td>
        <td>${item.rm ?? ""}</td>
        <td>${item.coordenador ?? "—"}</td>
        <td>${item.supervisor ?? "—"}</td>
        <td>${nf.format(item.pedidos || 0)}</td>
        <td>${nf.format(item.fora_prazo || 0)}</td>
        <td>
          <div class="sla-cell">
            <div class="sla-track">
              <div class="sla-fill" style="width:${largura}%"></div>
            </div>
            <strong class="${classeSla(item.sla)}">${pct(item.sla)}</strong>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

function csvCampo(valor) {
  const texto = String(valor ?? "");
  return `"${texto.replaceAll('"', '""')}"`;
}

function nomeArquivoRanking() {
  const inicio = document.getElementById("dataInicio").value || "inicio";
  const fim = document.getElementById("dataFim").value || "fim";

  if (inicio === fim) {
    return `ranking_bases_${inicio}.csv`;
  }

  return `ranking_bases_${inicio}_a_${fim}.csv`;
}

async function baixarRanking() {
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
    ...data.map((item, index) => [
      index + 1,
      item.codigo_base ?? "",
      item.base_entrega ?? "",
      item.rm ?? "",
      item.coordenador ?? "",
      item.supervisor ?? "",
      item.pedidos ?? 0,
      item.fora_prazo ?? 0,
      `${(Number(item.sla || 0) * 100).toFixed(2).replace(".", ",")}%`
    ].map(csvCampo).join(";"))
  ];

  const blob = new Blob(
    ["\ufeff" + linhas.join("\r\n")],
    { type: "text/csv;charset=utf-8;" }
  );

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = url;
  link.download = nomeArquivoRanking();

  document.body.appendChild(link);
  link.click();
  link.remove();

  URL.revokeObjectURL(url);
}

async function carregarDashboard() {
  await Promise.all([
    carregarKPIs(),
    carregarDiario(),
    carregarRm(),
    carregarResponsaveis(),
    carregarBases(),
    carregarEntregadores(50),
    carregarRankingBases()
  ]);

  atualizarHorarioVisualizacao();
}

document.getElementById("btnAplicar").addEventListener("click", async () => {
  try {
    await carregarDashboard();
  } catch (erro) {
    console.error(erro);
    alert("Erro ao aplicar os filtros.");
  }
});

document.getElementById("btnLimpar").addEventListener("click", async () => {
  definirDataPadraoOntem();

  document.getElementById("responsavel").value = "";
  document.getElementById("rm").value = "";

  try {
    await carregarDashboard();
  } catch (erro) {
    console.error(erro);
    alert("Erro ao limpar os filtros.");
  }
});

document.getElementById("btnBaixarRanking").addEventListener("click", async () => {
  try {
    await baixarRanking();
  } catch (erro) {
    console.error(erro);
    alert("Erro ao baixar o ranking.");
  }
});


async function verTodosEntregadores() {
  const botao = document.getElementById("btnVerTodosEntregadores");

  try {
    botao.disabled = true;

    if (exibindoTodosEntregadores) {
      await carregarEntregadores(50);
    } else {
      await carregarEntregadores(5000);
    }
  } catch (erro) {
    console.error(erro);
    alert("Erro ao carregar os entregadores.");
  } finally {
    botao.disabled = false;
  }
}

async function baixarEntregadoresCSV() {
  try {
    const url = `/api/entregadores-csv${montarQuery()}`;

    const response = await fetch(url, {
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error(`${response.status} - ${response.statusText}`);
    }

    const blob = await response.blob();

    let nomeArquivo = "entregadores_remessas.csv";
    const disposition = response.headers.get("Content-Disposition");

    if (disposition) {
      const match = disposition.match(/filename="([^"]+)"/);
      if (match && match[1]) {
        nomeArquivo = match[1];
      }
    }

    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = blobUrl;
    link.download = nomeArquivo;

    document.body.appendChild(link);
    link.click();
    link.remove();

    URL.revokeObjectURL(blobUrl);

  } catch (erro) {
    console.error(erro);
    alert("Erro ao baixar o CSV das remessas dos entregadores.");
  }
}

document
  .getElementById("btnVerTodosEntregadores")
  .addEventListener("click", verTodosEntregadores);

document
  .getElementById("btnBaixarEntregadores")
  .addEventListener("click", baixarEntregadoresCSV);

async function iniciar() {
  try {
    definirDataPadraoOntem();
    await carregarOpcoesFiltros();
    await carregarDashboard();
  } catch (erro) {
    console.error(erro);
    alert("Erro ao carregar o dashboard.");
  }
}

iniciar();
