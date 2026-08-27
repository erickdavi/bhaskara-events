/* Painel do Bhaskara Events.
 *
 * Duas chamadas so: POST /orders dispara a carga, GET /status devolve o
 * andamento. Nao ha WebSocket — polling periodico entrega "praticamente em
 * tempo real" com uma fracao da complexidade, e o proprio CloudWatch Logs, de
 * onde vem o fluxo de eventos, tem alguns segundos de defasagem de ingestao
 * que um WebSocket nao eliminaria.
 *
 * A chave de API vive apenas no localStorage deste browser. Ela nunca e
 * embutida na pagina: o bundle e publico no CloudFront, e uma chave embutida
 * seria uma chave publicada.
 */

(function () {
  "use strict";

  var STORAGE_KEY = "bhaskara.apiKey";
  var STORAGE_BASE = "bhaskara.apiBase";

  // Enquanto ha carga em andamento vale a pena consultar rapido; parado, nao.
  // O throttling do stage e de 5 rps, e um poll a cada 2 s fica ordens de
  // grandeza abaixo disso.
  var POLL_ACTIVE_MS = 2000;
  var POLL_IDLE_MS = 8000;

  var MAX_STREAM_ITEMS = 200;
  var MAX_CHART_POINTS = 90;

  // Status que significam "tente de novo", nao "deu errado".
  //
  // A conta tem 10 execucoes concorrentes no total, compartilhadas pelas tres
  // funcoes. Durante uma rajada o worker toma quase todos os slots e a funcao
  // de status e throttled — o API Gateway devolve 503. Sem tratamento, o painel
  // pisca um erro justamente no momento em que ha mais o que mostrar.
  //
  // Apenas GET e repetido. Repetir o POST /orders publicaria a carga duas vezes.
  var TRANSIENT_STATUS = [429, 500, 502, 503, 504];
  var MAX_RETRIES = 3;
  var RETRY_BASE_MS = 400;

  var el = {};
  var state = {
    base: "",
    key: "",
    cursor: 0,
    baseline: null,     // leitura tirada quando a carga foi disparada
    requested: 0,
    history: [],        // profundidade da fila, para o grafico
    timer: null,
    running: false,     // ha carga em andamento?
    lastError: ""
  };

  // --- utilidades ---------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function number(value) {
    return typeof value === "number" ? value.toLocaleString("pt-BR") : "—";
  }

  function clock(ms) {
    var d = new Date(ms);
    return String(d.getHours()).padStart(2, "0") + ":" +
           String(d.getMinutes()).padStart(2, "0") + ":" +
           String(d.getSeconds()).padStart(2, "0");
  }

  // localStorage lanca em contextos com armazenamento bloqueado; um painel que
  // nao consegue lembrar a chave ainda deve funcionar com ela digitada.
  function remember(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* ignorado */ }
  }

  function recall(key) {
    try { return window.localStorage.getItem(key) || ""; } catch (e) { return ""; }
  }

  function banner(message, tone) {
    if (!message) {
      el.banner.hidden = true;
      return;
    }
    el.banner.hidden = false;
    el.banner.dataset.tone = tone || "info";
    el.banner.textContent = message;
  }

  // --- chamadas a API -----------------------------------------------------

  function wait(ms) {
    return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
  }

  function request(path, options, attempt) {
    if (!state.base) return Promise.reject(new Error("Informe a URL da API."));
    if (!state.key) return Promise.reject(new Error("Informe a chave de API."));

    var settings = Object.assign({ method: "GET", headers: {} }, options || {});
    settings.headers["x-api-key"] = state.key;

    var tries = attempt || 0;

    return fetch(state.base.replace(/\/+$/, "") + path, settings).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (response.ok) return body;

        if (response.status === 403) {
          throw new Error("Chave de API recusada. Confira o valor de terraform output -raw api_key.");
        }

        var transient = settings.method === "GET"
          && TRANSIENT_STATUS.indexOf(response.status) !== -1;

        if (transient && tries < MAX_RETRIES) {
          // Recuo exponencial: 400 ms, 800 ms, 1600 ms. A rajada que causou o
          // throttle costuma passar antes disso.
          return wait(RETRY_BASE_MS * Math.pow(2, tries)).then(function () {
            return request(path, options, tries + 1);
          });
        }

        var error = new Error(body && body.error ? body.error : "HTTP " + response.status);
        error.transient = transient;
        throw error;
      });
    }, function (networkError) {
      // Falha de rede tambem e transitoria: o painel continua tentando.
      networkError.transient = true;
      throw networkError;
    });
  }

  function generate() {
    var quantity = parseInt(el.quantity.value, 10);
    var percent = parseInt(el.invalid.value, 10);

    if (!(quantity >= 1)) {
      banner("Informe uma quantidade de no mínimo 1.", "error");
      return;
    }

    el.generate.disabled = true;
    banner("Publicando " + number(quantity) + " mensagens…", "info");

    // A leitura de baseline vem ANTES do POST: sem ela, as mensagens desta
    // carga se somariam ao placar acumulado das anteriores e o progresso
    // comecaria em algum numero arbitrario.
    request("/status").then(function (before) {
      state.baseline = { succeeded: before.succeeded, failed: before.failed };
      state.requested = quantity;
      state.history = [];

      return request("/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          quantity: quantity,
          invalid_ratio: Math.max(0, Math.min(100, percent || 0)) / 100
        })
      });
    }).then(function (result) {
      state.running = true;
      banner(
        "Publicadas " + number(result.published) + " de " + number(result.requested) +
        " mensagens em " + result.batches + " lotes (" + result.elapsed_ms + " ms)." +
        (result.truncated ? " A função parou antes do timeout: reenvie a diferença." : ""),
        result.truncated ? "error" : "ok"
      );
      schedule(0);
    }).catch(function (error) {
      banner(error.message, "error");
    }).finally(function () {
      el.generate.disabled = false;
    });
  }

  function poll() {
    // No primeiro poll o cursor vai ausente de proposito, para o servidor
    // escolher a janela recente. Mandar since=0 faria o CloudWatch varrer o log
    // desde o inicio — e ele devolve pagina vazia nesse caso.
    var query = "/status?events=100&dlq=8"
      + (state.cursor ? "&since=" + state.cursor : "");

    request(query).then(function (data) {
      if (state.lastError) banner("", "info");
      state.lastError = "";
      render(data);

      if (typeof data.events_cursor === "number") state.cursor = data.events_cursor;

      // A carga terminou quando nao ha mais nada na fila nem em voo.
      if (state.running && data.queued === 0 && data.in_flight === 0 && state.history.length > 2) {
        state.running = false;
      }

      schedule(state.running ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    }).catch(function (error) {
      // Uma falha nao pode matar o polling: o painel volta a tentar e diz o que
      // aconteceu. Um throttle passageiro, porem, nao merece banner vermelho —
      // ele e esperado sob carga nesta conta.
      if (error.message !== state.lastError) {
        state.lastError = error.message;
        banner(
          error.transient
            ? "Servico ocupado (as tres funcoes dividem 10 execucoes concorrentes). Reconectando…"
            : error.message,
          error.transient ? "info" : "error"
        );
      }
      schedule(error.transient && state.running ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    });
  }

  function schedule(delay) {
    window.clearTimeout(state.timer);
    state.timer = window.setTimeout(poll, delay);
  }

  // --- renderizacao -------------------------------------------------------

  function render(data) {
    el.queued.textContent = number(data.queued);
    el.flight.textContent = number(data.in_flight);

    var run = relative(data);

    el.ok.textContent = number(run.succeeded);
    el.fail.textContent = number(run.failed);
    el.okTotal.textContent = "acumulado: " + number(data.succeeded);
    el.failTotal.textContent = "acumulado: " + number(data.failed);

    progress(run, data);
    chart(data.queued);
    stream(data.events);
    dlq(data.dlq_messages);
  }

  // Numeros "desta execucao": o endpoint e sem estado e devolve o acumulado,
  // entao a subtracao acontece aqui. Sem baseline, mostra o acumulado mesmo.
  function relative(data) {
    if (!state.baseline) {
      return { succeeded: data.succeeded, failed: data.failed };
    }

    // A baseline se auto-corrige. Os contadores da SQS sao eventualmente
    // consistentes: a leitura tirada no clique pode vir alta demais — depois de
    // um purge, por exemplo, ela ainda reporta o valor antigo por quase um
    // minuto. Sem isto, a diferenca ficaria negativa, seria zerada pelo
    // Math.max e o painel mostraria zero enquanto mensagens visivelmente
    // chegam na DLQ.
    //
    // Como as filas de saida so crescem, qualquer leitura MENOR que a baseline
    // prova que a baseline estava velha.
    state.baseline.succeeded = Math.min(state.baseline.succeeded, data.succeeded);
    state.baseline.failed = Math.min(state.baseline.failed, data.failed);

    return {
      succeeded: data.succeeded - state.baseline.succeeded,
      failed: data.failed - state.baseline.failed
    };
  }

  function progress(run, data) {
    if (!state.requested) {
      el.progressCount.textContent = "nenhuma carga disparada";
      el.barOk.style.width = "0%";
      el.barFail.style.width = "0%";
      return;
    }

    var done = run.succeeded + run.failed;
    var suffix = "";

    if (done >= state.requested) {
      suffix = " — concluído";
    } else if (data.queued === 0 && data.in_flight === 0) {
      // A fila esvaziou mas a conta ainda nao fecha. Nao ha mensagem perdida:
      // o contador da DLQ atualiza em degraus e com atraso maior que o da
      // results, entao a barra fica parada alguns segundos no fim de cada
      // carga. Dizer isso e melhor do que deixar o operador achando que travou.
      suffix = " — fila vazia, contadores ainda assentando";
    }

    el.progressCount.textContent =
      number(done) + " de " + number(state.requested) + " processadas" + suffix;

    el.barOk.style.width = (100 * run.succeeded / state.requested).toFixed(2) + "%";
    el.barFail.style.width = (100 * run.failed / state.requested).toFixed(2) + "%";
  }

  function chart(queued) {
    state.history.push(queued);
    if (state.history.length > MAX_CHART_POINTS) state.history.shift();

    var W = 900, H = 200, PAD_L = 44, PAD_R = 8, PAD_T = 12, PAD_B = 24;
    var points = state.history;
    var svg = "";

    if (points.length < 2) {
      el.chart.innerHTML =
        '<text class="empty-note" x="' + (W / 2) + '" y="' + (H / 2) + '" text-anchor="middle">' +
        "Sem leituras suficientes ainda.</text>";
      return;
    }

    var max = Math.max.apply(null, points);
    var top = max <= 0 ? 1 : max;
    var innerW = W - PAD_L - PAD_R;
    var innerH = H - PAD_T - PAD_B;

    var x = function (i) { return PAD_L + innerW * (i / (points.length - 1)); };
    var y = function (v) { return PAD_T + innerH * (1 - v / top); };

    // Grade recessiva e apenas tres marcas: o eixo nao e o dado.
    [0, 0.5, 1].forEach(function (fraction) {
      var value = Math.round(top * fraction);
      var yy = y(value);
      svg += '<line class="grid-line" x1="' + PAD_L + '" y1="' + yy + '" x2="' + (W - PAD_R) + '" y2="' + yy + '"/>';
      svg += '<text class="axis-text" x="' + (PAD_L - 8) + '" y="' + (yy + 4) + '" text-anchor="end">' + value + "</text>";
    });

    var line = points.map(function (v, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1); }).join(" ");
    var area = line + " L" + x(points.length - 1).toFixed(1) + " " + y(0) + " L" + x(0).toFixed(1) + " " + y(0) + " Z";

    svg += '<path class="series-area" d="' + area + '"/>';
    svg += '<path class="series-line" d="' + line + '"/>';
    svg += '<text class="axis-text" x="' + PAD_L + '" y="' + (H - 6) + '">mais antigo</text>';
    svg += '<text class="axis-text" x="' + (W - PAD_R) + '" y="' + (H - 6) + '" text-anchor="end">agora</text>';

    el.chart.innerHTML = svg;
  }

  var TAGS = {
    message_processed: { label: "resolvida", icon: "✓", cls: "tag--ok" },
    message_rejected:  { label: "recusada",  icon: "!", cls: "tag--fail" },
    message_failed:    { label: "falhou",    icon: "↻", cls: "tag--warn" }
  };

  function stream(events) {
    if (!events || !events.length) return;

    if (el.stream.firstElementChild && el.stream.firstElementChild.className === "placeholder") {
      el.stream.innerHTML = "";
    }

    events.forEach(function (entry) {
      var meta = TAGS[entry.event] || { label: entry.event, icon: "·", cls: "" };
      var li = document.createElement("li");

      li.appendChild(span("time", clock(entry.timestamp)));

      var tag = span("tag " + meta.cls, meta.icon + " " + meta.label);
      li.appendChild(tag);

      li.appendChild(span("detail", describe(entry)));

      // Mais novo no topo: quem olha o painel quer o que acabou de acontecer.
      el.stream.insertBefore(li, el.stream.firstChild);
    });

    while (el.stream.childElementCount > MAX_STREAM_ITEMS) {
      el.stream.removeChild(el.stream.lastChild);
    }
  }

  function describe(entry) {
    if (entry.event === "message_processed") {
      var equation = entry.a + "x² " + signed(entry.b) + "x " + signed(entry.c) + " = 0";
      var roots = Array.isArray(entry.roots)
        ? "sem raízes reais"
        : "x₁=" + round(entry.x1) + " x₂=" + round(entry.x2);
      return equation + " → " + roots;
    }
    if (entry.event === "message_rejected") return entry.reason || "sem motivo registrado";
    if (entry.event === "message_failed") {
      return (entry.error_type || "erro") + " — tentativa " + (entry.receive_count || "?");
    }
    return "";
  }

  function signed(value) {
    return (value < 0 ? "− " : "+ ") + Math.abs(value);
  }

  function round(value) {
    if (typeof value !== "number") return "?";
    return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/\.?0+$/, "");
  }

  function dlq(messages) {
    if (!messages) return;

    if (!messages.length) {
      el.dlq.innerHTML = '<li class="placeholder">Nenhuma mensagem na DLQ.</li>';
      return;
    }

    el.dlq.innerHTML = "";

    messages.forEach(function (message) {
      var li = document.createElement("li");
      li.appendChild(span("tag tag--fail", "!"));

      var wrap = document.createElement("div");
      wrap.appendChild(span("body", message.body || ""));
      wrap.appendChild(document.createElement("br"));
      // Mensagens que chegaram pelo redrive nativo nao trazem motivo: a SQS
      // move o payload original e nao sabe por que ele falhou.
      wrap.appendChild(span("reason", message.reason || "sem motivo registrado (chegou por retry)"));

      li.appendChild(wrap);
      el.dlq.appendChild(li);
    });
  }

  function span(className, text) {
    var node = document.createElement("span");
    node.className = className;
    node.textContent = text;
    return node;
  }

  // --- inicializacao ------------------------------------------------------

  function saveConfig() {
    state.base = el.apiBase.value.trim();
    state.key = el.apiKey.value.trim();

    remember(STORAGE_BASE, state.base);
    remember(STORAGE_KEY, state.key);

    banner(state.base && state.key ? "Configuração salva." : "Informe a URL e a chave da API.",
           state.base && state.key ? "ok" : "error");

    if (state.base && state.key) schedule(0);
  }

  function init() {
    ["banner", "chart", "stream", "dlq", "quantity", "invalid", "generate", "reset",
     "progress-count", "bar-ok", "bar-fail", "api-base", "api-key", "save-config",
     "kpi-queued", "kpi-flight", "kpi-ok", "kpi-fail", "kpi-ok-total", "kpi-fail-total"
    ].forEach(function (id) {
      el[id.replace(/-(\w)/g, function (_, c) { return c.toUpperCase(); })] = $(id);
    });

    el.queued = el.kpiQueued;
    el.flight = el.kpiFlight;
    el.ok = el.kpiOk;
    el.fail = el.kpiFail;
    el.okTotal = el.kpiOkTotal;
    el.failTotal = el.kpiFailTotal;

    var config = window.BHASKARA_CONFIG || {};

    state.base = recall(STORAGE_BASE) || config.apiBase || "";
    state.key = recall(STORAGE_KEY);

    el.apiBase.value = state.base;
    el.apiKey.value = state.key;

    el.saveConfig.addEventListener("click", saveConfig);
    el.generate.addEventListener("click", generate);
    el.reset.addEventListener("click", function () {
      state.baseline = null;
      state.requested = 0;
      state.history = [];
      el.stream.innerHTML = '<li class="placeholder">Aguardando eventos…</li>';
      banner("Contadores desta execução zerados.", "ok");
      schedule(0);
    });

    if (state.base && state.key) {
      schedule(0);
    } else {
      banner("Informe a URL da API e a chave para começar. A chave sai de "
        + "terraform output -raw api_key e fica apenas neste browser.", "info");
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
