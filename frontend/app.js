const API_BASE = "/api/registros";
const DEBOUNCE_MS = 400;

const estado = {
  page: 1,
  pageSize: 10,
  filtros: {},
};

let debounceTimer = null;

// Se llenan una vez al iniciar, con las opciones reales que manda el backend
// (/api/registros/filtros), para no tener listas de estados hardcodeadas y
// desincronizadas del modelo real.
const opcionesEstados = {
  sigemi: [],
  motivosArchivoSigemi: [],
  semyt: [],
  sigi: [],
  motivosArchivoSigi: [],
};

const el = (id) => document.getElementById(id);

/** Convierte "Sin Resolución" -> "estado-SinResolucion" (sin espacios ni acentos, clase CSS válida). */
function claseEstado(valor) {
  const sinAcentos = (valor || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
  return "estado-" + sinAcentos.replace(/[^A-Za-z0-9]/g, "");
}

/** true si el valor viene null/undefined o es un string vacío/solo espacios. */
function esVacio(valor) {
  return valor === null || valor === undefined || String(valor).trim() === "";
}

/** Expediente/causa: si no está cargado, muestra un texto aclaratorio en vez de "null"/"undefined". */
function formatearExpediente(expediente) {
  return esVacio(expediente)
    ? `<span class="dato-vacio">Sin expediente</span>`
    : expediente;
}

function formatearCausa(causa) {
  return esVacio(causa)
    ? `<span class="dato-vacio">Sin causa</span>`
    : `CAU-${causa}`;
}

/* ---------------- Menú lateral desplegable / cambio de vista ---------------- */

function conectarMenu() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("activo"));
      btn.classList.add("activo");

      const vista = btn.dataset.vista;
      document.querySelectorAll(".vista").forEach((v) => (v.hidden = true));
      el(`vista-${vista}`).hidden = false;

      // en pantallas angostas, el menú se cierra solo al elegir una opción
      if (window.matchMedia("(max-width: 700px)").matches) {
        el("sidebar").classList.remove("abierto");
      }
    });
  });

  // El botón abre/cierra el menú en cualquier tamaño de pantalla.
  el("btn-toggle-sidebar").addEventListener("click", () => {
    el("sidebar").classList.toggle("abierto");
  });
}

/* ---------------- Filtros colapsables (Consultar Actas) ---------------- */

function conectarToggleFiltros() {
  const header = el("btn-toggle-filtros");
  const body = el("filtros-body");
  header.addEventListener("click", () => {
    const abierto = header.getAttribute("aria-expanded") === "true";
    header.setAttribute("aria-expanded", String(!abierto));
    body.hidden = abierto;
  });
}

/* ---------------- Filtros ---------------- */
const CACHE_FILTROS_KEY = "cache_opciones_filtro_v2"; // <-- bump: invalida caché vieja de usuarios con el sitio ya abierto

async function cargarOpcionesFiltro() {
  const cacheada = leerCacheFiltros();
  let data = cacheada;
  if (!data) {
    const res = await fetch(`${API_BASE}/filtros`);
    if (!res.ok) throw new Error(`No se pudieron cargar los filtros (HTTP ${res.status})`);
    data = await res.json();
    guardarCacheFiltros(data);
  }
  opcionesEstados.sigemi = data.estados_sigemi;
  opcionesEstados.motivosArchivoSigemi = data.motivos_archivo_sigemi;
  opcionesEstados.semyt = data.estados_semyt;
  opcionesEstados.sigi = data.estados_sigi;
  opcionesEstados.motivosArchivoSigi = data.motivos_archivo_sigi;

  llenarSelect(el("f-sigemi"), data.estados_sigemi);
  llenarSelect(el("f-semyt"), data.estados_semyt);
  llenarSelect(el("f-sigi"), data.estados_sigi);
  llenarSelect(el("f-motivo-archivo"), opcionesMotivoUnificadas());
}

function opcionesMotivoUnificadas() {
  return [...new Set([...opcionesEstados.motivosArchivoSigemi, ...opcionesEstados.motivosArchivoSigi])];
}

function leerCacheFiltros() {
  try {
    const crudo = sessionStorage.getItem(CACHE_FILTROS_KEY);
    if (!crudo) return null;
    const { data, guardadoEn } = JSON.parse(crudo);
    if (Date.now() - guardadoEn > CACHE_FILTROS_TTL_MS) return null;
    return data;
  } catch {
    return null; // si el cache está corrupto, mejor pedirlo de nuevo que romper la carga
  }
}

function guardarCacheFiltros(data) {
  try {
    sessionStorage.setItem(CACHE_FILTROS_KEY, JSON.stringify({ data, guardadoEn: Date.now() }));
  } catch {
    // sessionStorage lleno o deshabilitado: no es crítico, seguimos sin cache
  }
}

function llenarSelect(selectEl, valores) {
  for (const v of valores) {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    selectEl.appendChild(opt);
  }
}

/** Devuelve 'sigi' o 'sigemi' según cuál estado esté elegido (SIGI tiene prioridad si se cargan los dos), o null si ninguno. */
function sistemaMotivoActivo() {
  if (el("f-sigi").value) return "sigi";
  if (el("f-sigemi").value) return "sigemi";
  return null;
}

/** Repuebla el select único de "Motivo de archivo" con las opciones del sistema activo. */
function actualizarOpcionesMotivo() {
  const select = el("f-motivo-archivo"); // <-- ver nota sobre el id en el HTML
  const valorPrevio = select.value;
  const sistema = sistemaMotivoActivo();

  select.innerHTML = '<option value="">Motivo de archivo…</option>';
  select.disabled = !sistema;
  if (!sistema) return;

  const opciones = sistema === "sigi" ? opcionesEstados.motivosArchivoSigi : opcionesEstados.motivosArchivoSigemi;
  llenarSelect(select, opciones);

  if (opciones.includes(valorPrevio)) select.value = valorPrevio;
}

function leerFiltrosDelFormulario() {
  return {
    estado_sigemi: el("f-sigemi").value || undefined,
    estado_semyt: el("f-semyt").value || undefined,
    estado_sigi: el("f-sigi").value || undefined,
    motivo_archivo: el("f-motivo-archivo").value || undefined,  
    juzgado: el("f-juzgado").value || undefined,
    expediente: el("f-expediente").value.trim() || undefined,
    acta: el("f-acta").value.trim() || undefined,
    causa: el("f-causa").value.trim() || undefined,
    patente: el("f-patente").value.trim() || undefined,
    fecha_desde: el("f-fecha-desde").value || undefined,
    fecha_hasta: el("f-fecha-hasta").value || undefined,
    consistencia: el("f-consistencia").value || undefined,
    solo_duplicadas: el("f-duplicadas").checked || undefined,
    solo_reescritas: el("f-reescritas").checked || undefined,
  };
}

/** Se llama en cada tecla/cambio de filtro: espera un ratito y busca sola. */
function buscarConDebounce() {
  clearTimeout(debounceTimer);
  el("buscando").hidden = false;
  debounceTimer = setTimeout(() => {
    estado.filtros = leerFiltrosDelFormulario();
    estado.page = 1;
    cargarRegistros();
  }, DEBOUNCE_MS);
}

/* ---------------- Carga y render de la tabla ---------------- */

let controladorRegistros = null;

async function cargarRegistros() {
  const params = new URLSearchParams({
    page: estado.page,
    page_size: estado.pageSize,
  });
  for (const [k, v] of Object.entries(estado.filtros)) {
    if (v) params.set(k, v);
  }

  // Si había una consulta anterior en vuelo (ej: el usuario cambió de filtro
  // o de página antes de que responda), la cancelamos. Evita que una
  // respuesta vieja llegue después de la nueva y pise los resultados
  // en pantalla, y le ahorra trabajo al backend.
  if (controladorRegistros) controladorRegistros.abort();
  controladorRegistros = new AbortController();

  try {
    const res = await fetch(`${API_BASE}?${params.toString()}`, { signal: controladorRegistros.signal });
    if (!res.ok) {
      console.error("Error al buscar registros:", res.status, await res.text());
      renderTabla([]);
      renderContador(0);
      return;
    }
    const data = await res.json();
    renderTabla(data.resultados);
    renderContador(data.total);
    renderPaginacion(data.page, data.total_pages);
  } catch (err) {
    if (err.name === "AbortError") return;
    throw err;
  } finally {
    el("buscando").hidden = true;
  }
}

function formatearFechaLabrada(fechaISO) {
  if (!fechaISO) return "";
  const d = new Date(fechaISO);
  if (Number.isNaN(d.getTime())) return "";
  const fecha = d.toLocaleDateString("es-AR", {
    day: "2-digit", month: "2-digit", year: "numeric",
  });
  const hora = d.toLocaleTimeString("es-AR", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
  return `<div class="fecha-labrada-fecha">${fecha}</div><div class="fecha-labrada-hora">${hora} hs</div>`;
}

function formatearFechaCorta(fechaISO) {
  if (!fechaISO) return null;
  return new Date(fechaISO).toLocaleDateString("es-AR");
}

/** Separa "José Ingenieros 0-100" en calle ("José Ingenieros") y altura ("0-100")
 *  para poder mostrarlas en dos líneas legibles, sin que el navegador corte
 *  la altura a la mitad (ej: "0-" / "100"). Si no matchea el patrón (dirección
 *  sin altura, o formato inesperado), muestra el texto completo tal cual. */
function formatearDireccion(direccion) {
  if (!direccion) return "";
  const match = direccion.trim().match(/^(.*?)\s+(\d[\d/-]*)$/);
  if (!match) return `<div class="calle">${direccion}</div>`;
  const [, calle, altura] = match;
  return `<div class="calle">${calle}</div><div class="sub">${altura}</div>`;
}

/** La foto no se carga sola: se muestra un botón y la imagen recién se pide
 *  al servidor cuando el usuario hace click (ver onCargarFoto). Así una
 *  página de resultados no dispara N pedidos de imagen de una sola vez. */
/** Muestra la foto directamente en la tabla, sin botón "Ver foto". */
function renderCeldaFoto(r) {
  const url = r.foto_url || "/static/photos/placeholder.jpg";
  const patente = r.patente || "vehículo";

  return `
    <img
      class="foto-vehiculo"
      src="${url}"
      alt="Foto vehículo ${patente}"
      loading="lazy"
      decoding="async"
      onclick="abrirFotoModal(this.src)"
      onerror="this.onerror=null; this.src='/static/photos/placeholder.jpg';"
    >`;
}

function onCargarFoto(ev) {
  const btn = ev.currentTarget;
  const url = btn.dataset.fotoUrl || "/static/photos/placeholder.jpg";
  const img = document.createElement("img");
  img.className = "foto-vehiculo";
  img.alt = `Foto vehículo ${btn.dataset.patente}`;
  img.decoding = "async";
  img.addEventListener("error", function () { this.src = "/static/photos/placeholder.jpg"; });
  img.addEventListener("click", function () { abrirFotoModal(this.src); });
  btn.replaceWith(img);
  img.src = url; // se asigna al final, ya insertada en el DOM, para que el pedido salga recién ahora
}

function renderTabla(registros) {
  const tbody = el("tabla-body");
  tbody.innerHTML = "";
  el("estado-vacio").hidden = registros.length > 0;

  for (const r of registros) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="expediente-cell">
        <div class="sub">JUZ-${r.juzgado ?? "-"}</div>
        <div class="exp">${formatearExpediente(r.expediente)}${r.es_duplicada ? ' <span class="badge-duplicada" title="Esta acta tiene más de un expediente asociado">⚠ Duplicada</span>' : ''}${r.reescrita ? ' <span class="badge-reescrita" title="Mismo vehículo, mismo día y misma dirección que otra acta, con distinto número">↻ Reescrita</span>' : ''}</div>
        <div class="sub">ACT-${r.acta}</div>
        <div class="sub">${formatearCausa(r.causa)}</div>
      </td>
      <td class="foto-cell">${renderCeldaFoto(r)}</td>
      <td class="patente-cell">${r.patente}</td>
      <td class="direccion-cell">${formatearDireccion(r.direccion)}</td>
      <td>${formatearFechaLabrada(r.fecha_hora)}</td>
      <td>${renderCeldaEstadoSigemi(r)}</td>
      <td>${renderCeldaEstado(r.id, "estado_semyt", r.estado_semyt,null, null,)}</td>
      <td>${renderCeldaEstadoSigi(r)}</td>
      <td>
        <div class="consistencia-accion-cell">
          ${renderCeldaConsistencia(r)}
          <button class="btn-refrescar" data-id="${r.id}" title="Refrescar estado">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
          </button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll(".btn-cargar-foto").forEach((btn) => {
    btn.addEventListener("click", onCargarFoto);
  });
  tbody.querySelectorAll("select.motivo-archivo-select").forEach((sel) => {
    sel.addEventListener("change", onCambiarMotivoArchivo);
  });
  tbody.querySelectorAll(".btn-refrescar").forEach((btn) => {
    btn.addEventListener("click", onRefrescarFila);
  });
}

/** Celda genérica de estado, con select + fecha de cobro opcional debajo (si corresponde). */
function categoriaEstadoVisual(campo, valor, motivo = null) {
  /** Definir celda de colores */
  if (!valor) return "vacio";
  if (campo === "estado_sigemi") {
    if (valor === "Pago Voluntario" ||(valor === "Archivado" && (motivo === "Pago voluntario" || motivo === "Pago en Procuración"))
    ) return "pagada";
    if (valor === "Archivado" || valor === "Resuelta sin Archivar" || valor === "Archivado Sin Resolución") return "resuelta";
    return "vencida";
  }

  if (campo === "estado_semyt") {
    if (valor === "Pagada en Juzgado") return "pagada";
    if (valor === "Resuelta en Juzgado") return "resuelta";
    if (valor === "Rechazada") return "rechazada";
    if (valor === "Eliminada") return "eliminada";
    return "vencida";
  }

  if (campo === "estado_sigi") {
    if (valor === "Archivado" && motivo === "Pagada") return "pagada";
    if (valor === "Archivado") return "resuelta";
    return "vencida";
  }

  return "vencida";
}

function renderCeldaEstado(id, campo, valorActual, opciones, campoFecha, fechaCobroISO, motivo = null, extraHtml = "") {
  // "No Cargada" se muestra igual que un campo vacío: "-", sin color de estado.
  const valorMostrado = (valorActual && valorActual !== "No Cargada") ? valorActual : null;
  const categoria = categoriaEstadoVisual(campo, valorMostrado, motivo);

  const estadoTexto = `<span class="estado-label estado-categoria-${categoria}">${valorMostrado || "-"}</span>`;
  let fechaHtml = "";
  if (campoFecha) {
    // Se muestra la fecha de cobro para cualquier variante que cuente como
    // "pagada" en categoriaEstadoVisual (estado directo de pago, o
    // Archivado con motivo de pago) -- ya no se compara un string suelto
    // aparte, se reutiliza la misma categoría que decide el color, así no
    // se pueden desincronizar de nuevo.
    const fechaTexto = categoria === "pagada" ? formatearFechaCorta(fechaCobroISO) : null;
    if (fechaTexto) {
      fechaHtml = `<span class="fecha-cobro">Cobrado: ${fechaTexto}</span>`;
    } else if (categoria === "pagada" && campo === "estado_sigemi" && valorMostrado !== "Archivado") {
      // Si está Archivado, el motivo (renderMotivoArchivo, más abajo en
      // renderCeldaEstadoSigemi) ya muestra "Pago en Procuración"
      // como texto fijo -- no hace falta repetirlo acá.
      // SIGEMI es el único sistema con este caso: pagada pero sin fecha real
      // de cobro porque el pago no impactó en el archivo de pagos (se pagó
      // en Procuración o en la Municipalidad). En vez de dejar la celda
      // pelada sin explicación, se aclara por qué no hay fecha.
      fechaHtml = `<span class="fecha-cobro fecha-cobro-sin-impacto">Pagada en Procuración</span>`;
    }
  }
  // Orden visual: estado, motivo (ej. "Pago voluntario") y por último la
  // fecha de cobro debajo de todo.
  return `<div class="estado-celda">${estadoTexto}${extraHtml}${fechaHtml}</div>`;
}

/**
 * Motivo de archivo: si el acta ya está pagada, se muestra como texto fijo
 * (no interactivo, sin dropdown). Si no está pagada, se puede elegir/editar.
 */
function renderMotivoArchivo(id, valorActual, opciones, sistema, esPagada) {
  // Igual que con Pago Voluntario: una vez que el motivo ya está cargado
  // (sea de pago o cualquier otra resolución: desestimación, prescripción,
  // etc.) se muestra como texto fijo debajo del estado. El <select> editable
  // sólo aparece mientras todavía no se eligió ningún motivo.
  if (valorActual) {
    return `<span class="motivo-archivo-texto">${valorActual}</span>`;
  }
  const opts = opciones
    .map((o) => `<option value="${o}" ${o === valorActual ? "selected" : ""}>${o}</option>`)
    .join("");
  return `
    <select class="motivo-archivo-select" data-id="${id}" data-sistema="${sistema}">
      <option value="">Motivo de archivo…</option>
      ${opts}
    </select>`;
}

/** Celda de estado SIGEMI: agrega el motivo de archivo cuando el estado es Archivado o Resuelta sin Archivar. */
function renderCeldaEstadoSigemi(r) {
  // "Resuelta sin Archivar" se carga siempre con motivo=None (ver
  // resolver_estado en reglas_sigemi.py), así que también necesita el
  // select para elegirlo a mano, igual que "Archivado".
  const tieneMotivo = r.estado_sigemi === "Archivado" || r.estado_sigemi === "Resuelta sin Archivar";

  let motivoHtml = "";
  if (tieneMotivo) {
    const esPagada = categoriaEstadoVisual("estado_sigemi", r.estado_sigemi, r.motivo_archivo_sigemi) === "pagada";
    motivoHtml = renderMotivoArchivo(
      r.id, r.motivo_archivo_sigemi, opcionesEstados.motivosArchivoSigemi, "sigemi", esPagada
    );
  }

  // El motivo (ej. "Pago voluntario") va antes que la fecha de cobro.
  return renderCeldaEstado(
    r.id, "estado_sigemi", r.estado_sigemi, null,
    "fecha_cobro_sigemi", r.fecha_cobro_sigemi, r.motivo_archivo_sigemi, motivoHtml
  );
}

function renderCeldaEstadoSigi(r) {
  let motivoHtml = "";
  if (r.estado_sigi === "Archivado") {
    const esPagada = categoriaEstadoVisual("estado_sigi", r.estado_sigi, r.motivo_archivo_sigi) === "pagada";
    motivoHtml = renderMotivoArchivo(
      r.id, r.motivo_archivo_sigi, opcionesEstados.motivosArchivoSigi, "sigi", esPagada
    );
  }

  return renderCeldaEstado(
    r.id, "estado_sigi", r.estado_sigi, null,
    "fecha_cobro_sigi", r.fecha_cobro_sigi, r.motivo_archivo_sigi, motivoHtml
  );
}

/** Celda de consistencia: visto/cruz según si SIGEMI y SEMyT coinciden en el resultado del acta. */
function renderCeldaConsistencia(r) {
  if (r.consistente === true) {
    return `
      <span class="consistencia consistencia-ok">
        <svg xmlns="http://w3.org" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" class="consistencia-icon">
          <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
      </span>`;
  }
  if (r.consistente === false) {
    return `
      <span class="consistencia consistencia-mal">
        <svg xmlns="http://w3.org" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" class="consistencia-icon">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </span>`;
  }
  return `<span class="consistencia consistencia-nd">–</span>`;
}

async function onCambiarMotivoArchivo(ev) {
  const sel = ev.target;
  const id = sel.dataset.id;
  const valor = sel.value || null;
  const sistema = sel.dataset.sistema || "sigemi";
  const campo = sistema === "sigi" ? "motivo_archivo_sigi" : "motivo_archivo_sigemi";

  await fetch(`${API_BASE}/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [campo]: valor }),
  });

  // La consistencia depende del motivo, así que recargamos la fila/tabla.
  await cargarRegistros();
}

async function onRefrescarFila(ev) {
  const btn = ev.currentTarget;
  const id = btn.dataset.id;
  btn.classList.add("girando");
  try {
    await fetch(`${API_BASE}/${id}/refrescar`, { method: "POST" });
    await cargarRegistros();
  } finally {
    btn.classList.remove("girando");
  }
}

function renderContador(total) {
  el("contador").textContent = `${total} registro${total === 1 ? "" : "s"} encontrado${total === 1 ? "" : "s"}`;
}

function renderPaginacion(paginaActual, totalPaginas) {
  const cont = el("paginas");
  cont.innerHTML = "";

  const irAPagina = (pagina) => {
    const destino = Math.min(Math.max(1, pagina), Math.max(1, totalPaginas));
    if (destino !== estado.page) {
      estado.page = destino;
      cargarRegistros();
    }
  };

  const btnPrimera = document.createElement("button");
  btnPrimera.type = "button";
  btnPrimera.className = "btn btn-outline btn-paginacion-icono";
  btnPrimera.setAttribute("aria-label", "Primera página");
  btnPrimera.title = "Primera página";
  btnPrimera.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="11 17 6 12 11 7"/><polyline points="18 17 13 12 18 7"/></svg>`;
  btnPrimera.disabled = paginaActual <= 1;
  btnPrimera.addEventListener("click", () => irAPagina(1));

  const btnAnterior = document.createElement("button");
  btnAnterior.type = "button";
  btnAnterior.textContent = "Anterior";
  btnAnterior.className = "btn btn-outline";
  btnAnterior.disabled = paginaActual <= 1;
  btnAnterior.addEventListener("click", () => irAPagina(paginaActual - 1));

  const labelPagina = document.createElement("span");
  labelPagina.className = "pagina-label";
  labelPagina.textContent = "Pág";

  const input = document.createElement("input");
  input.type = "number";
  input.className = "pagina-input";
  input.min = "1";
  input.max = String(totalPaginas || 1);
  input.value = paginaActual;
  const confirmarInput = () => irAPagina(Number(input.value));
  input.addEventListener("change", confirmarInput);
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") confirmarInput();
  });

  const labelTotal = document.createElement("span");
  labelTotal.className = "pagina-label";
  labelTotal.textContent = `de ${totalPaginas}`;

  const btnSiguiente = document.createElement("button");
  btnSiguiente.type = "button";
  btnSiguiente.textContent = "Siguiente";
  btnSiguiente.className = "btn btn-primary";
  btnSiguiente.disabled = paginaActual >= totalPaginas;
  btnSiguiente.addEventListener("click", () => irAPagina(paginaActual + 1));

  const btnUltima = document.createElement("button");
  btnUltima.type = "button";
  btnUltima.className = "btn btn-outline btn-paginacion-icono";
  btnUltima.setAttribute("aria-label", "Última página");
  btnUltima.title = "Última página";
  btnUltima.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg>`;
  btnUltima.disabled = paginaActual >= totalPaginas;
  btnUltima.addEventListener("click", () => irAPagina(totalPaginas));

  cont.append(btnPrimera, btnAnterior, labelPagina, input, labelTotal, btnSiguiente, btnUltima);
}

/* ---------------- Exportar Actas: filtros libres + reporte .txt ---------------- */

const EXPORT_API = `${API_BASE}/exportar`;

let camposExportables = [];
let filtrosExport = []; // [{ id, campo, modo, valor }]
let contadorFiltroExportId = 0;
let debounceExportTimer = null;

async function initExportar() {
  const res = await fetch(`${EXPORT_API}/campos`);
  const data = await res.json();
  camposExportables = data.campos;

  agregarFilaFiltroExport();

  el("btn-agregar-filtro-export").addEventListener("click", agregarFilaFiltroExport);
  el("btn-descargar-txt").addEventListener("click", descargarReporteTxt);
  ["fe-fecha-desde", "fe-fecha-hasta"].forEach((id) => {
    el(id).addEventListener("change", actualizarContadorExportConDebounce);
  });
  el("btn-descargar-consistencia-sigi").addEventListener("click", descargarConsistenciaSigi);
  await actualizarContadorExport();
}

/* ---------------- Exportar Insonsistencia SIGI ---------------- */
const CONSISTENCIA_SIGI_API = `${API_BASE}/exportar/consistencia-sigi`;

async function descargarConsistenciaSigi() {
  const btn = el("btn-descargar-consistencia-sigi");
  btn.disabled = true;
  try {
    const res = await fetch(CONSISTENCIA_SIGI_API);
    if (!res.ok) throw new Error("No se pudo generar el reporte de Consistencia SIGI");

    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = cd.match(/filename="?([^"]+)"?/);

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = match ? match[1] : "consistencia_sigi.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } finally {
    btn.disabled = false;
  }
}

/** Arma el body compartido por /exportar/contar y /exportar/txt. */
function cuerpoExportar() {
  return {
    filtros: filtrosExportValidos(),
    fecha_desde: el("fe-fecha-desde").value || null,
    fecha_hasta: el("fe-fecha-hasta").value || null,
  };
}

function campoExportInfo(campo) {
  return camposExportables.find((c) => c.campo === campo);
}

function agregarFilaFiltroExport() {
  const id = ++contadorFiltroExportId;
  filtrosExport.push({ id, campo: camposExportables[0]?.campo || "", modo: "coincide", valor: "" });
  renderFiltrosExport();
}

function quitarFilaFiltroExport(id) {
  filtrosExport = filtrosExport.filter((f) => f.id !== id);
  if (filtrosExport.length === 0) {
    agregarFilaFiltroExport();
  } else {
    renderFiltrosExport();
    actualizarContadorExportConDebounce();
  }
}

function renderFiltrosExport() {
  const cont = el("filtros-export-lista");
  cont.innerHTML = "";

  filtrosExport.forEach((fila) => {
    const info = campoExportInfo(fila.campo);
    const div = document.createElement("div");
    div.className = "filtro-export-fila";

    // --- Selector de campo ---
    const selCampo = document.createElement("select");
    selCampo.className = "fe-campo";
    camposExportables.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.campo;
      opt.textContent = c.etiqueta;
      if (c.campo === fila.campo) opt.selected = true;
      selCampo.appendChild(opt);
    });
    selCampo.addEventListener("change", (ev) => {
      fila.campo = ev.target.value;
      fila.valor = "";
      renderFiltrosExport();
      actualizarContadorExportConDebounce();
    });

    // --- Selector de modo: coincide / no coincide ---
    const selModo = document.createElement("select");
    selModo.className = "fe-modo";
    [
      ["coincide", "Coincide con"],
      ["no_coincide", "No coincide con"],
    ].forEach(([valor, texto]) => {
      const opt = document.createElement("option");
      opt.value = valor;
      opt.textContent = texto;
      if (valor === fila.modo) opt.selected = true;
      selModo.appendChild(opt);
    });
    selModo.addEventListener("change", (ev) => {
      fila.modo = ev.target.value;
      actualizarContadorExportConDebounce();
    });

    // --- Valor: input de texto, select de estado, o fecha, según el campo ---
    let valorInput;
    if (info?.tipo === "estado") {
      valorInput = document.createElement("select");
      const optVacia = document.createElement("option");
      optVacia.value = "";
      optVacia.textContent = "Elegí un valor…";
      valorInput.appendChild(optVacia);
      (info.opciones || []).forEach((o) => {
        const opt = document.createElement("option");
        opt.value = o;
        opt.textContent = o;
        if (o === fila.valor) opt.selected = true;
        valorInput.appendChild(opt);
      });
    } else if (fila.campo === "juzgado") {
      valorInput = document.createElement("select");
      [["", "Elegí un valor…"], ["1", "Juzgado 1"], ["2", "Juzgado 2"]].forEach(([valor, texto]) => {
        const opt = document.createElement("option");
        opt.value = valor;
        opt.textContent = texto;
        if (valor === fila.valor) opt.selected = true;
        valorInput.appendChild(opt);
      });
    } else if (info?.tipo === "fecha") {
      valorInput = document.createElement("input");
      valorInput.type = "date";
      valorInput.value = fila.valor;
    } else {
      valorInput = document.createElement("input");
      valorInput.type = "text";
      valorInput.placeholder = "Valor a buscar…";
      valorInput.value = fila.valor;
    }
    valorInput.className = "fe-valor";
    const onCambioValor = (ev) => {
      fila.valor = ev.target.value;
      actualizarContadorExportConDebounce();
    };
    valorInput.addEventListener("input", onCambioValor);
    valorInput.addEventListener("change", onCambioValor);

    // --- Quitar fila ---
    const btnQuitar = document.createElement("button");
    btnQuitar.type = "button";
    btnQuitar.className = "fe-quitar";
    btnQuitar.title = "Quitar filtro";
    btnQuitar.innerHTML = "&times;";
    btnQuitar.addEventListener("click", () => quitarFilaFiltroExport(fila.id));

    div.append(selCampo, selModo, valorInput, btnQuitar);
    cont.appendChild(div);
  });
}

function filtrosExportValidos() {
  return filtrosExport
    .filter((f) => f.campo && f.valor && f.valor.trim() !== "")
    .map((f) => ({ campo: f.campo, modo: f.modo, valor: f.valor.trim() }));
}

function actualizarContadorExportConDebounce() {
  clearTimeout(debounceExportTimer);
  el("exportar-contador").textContent = "Calculando coincidencias…";
  debounceExportTimer = setTimeout(actualizarContadorExport, DEBOUNCE_MS);
}

async function actualizarContadorExport() {
  const res = await fetch(`${EXPORT_API}/contar`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cuerpoExportar()),
  });
  const data = await res.json();
  el("exportar-contador").textContent = `${data.total} acta${data.total === 1 ? "" : "s"} coinciden con estos filtros`;
}

async function descargarReporteTxt() {
  const btn = el("btn-descargar-txt");
  btn.disabled = true;
  try {
    const res = await fetch(`${EXPORT_API}/txt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpoExportar()),
    });
    if (!res.ok) throw new Error("No se pudo generar el reporte");

    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = cd.match(/filename="?([^"]+)"?/);

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = match ? match[1] : "actas_estacionamiento_medido.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- Modal de foto del vehículo ---------------- */

function abrirFotoModal(src) {
  el("modal-foto-img").src = src;
  el("modal-foto").hidden = false;
}

function cerrarFotoModal() {
  el("modal-foto").hidden = true;
  el("modal-foto-img").src = "";
}

function conectarModalFoto() {
  el("modal-foto-cerrar").addEventListener("click", cerrarFotoModal);
  el("modal-foto").addEventListener("click", (ev) => {
    if (ev.target === el("modal-foto")) cerrarFotoModal();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") cerrarFotoModal();
  });
}

/* ---------------- Eventos generales ---------------- */

function conectarEventos() {
  // Búsqueda en vivo: cualquier cambio en filtros dispara la búsqueda sola.
  ["f-sigemi", "f-sigi"].forEach((id) => {
    el(id).addEventListener("change", () => {
      actualizarOpcionesMotivo();
      buscarConDebounce();
    });
  });
  ["f-semyt", "f-motivo-archivo", "f-consistencia", "f-juzgado", "f-duplicadas", "f-reescritas"].forEach((id) => {
    el(id).addEventListener("change", buscarConDebounce);
  });
  ["f-expediente", "f-acta", "f-causa", "f-patente"].forEach((id) => {
    el(id).addEventListener("input", buscarConDebounce);
  });
  ["f-fecha-desde", "f-fecha-hasta"].forEach((id) => {
    el(id).addEventListener("change", buscarConDebounce);
  });

  el("btn-limpiar").addEventListener("click", () => {
    ["f-sigemi", "f-semyt", "f-sigi", "f-motivo-archivo", "f-consistencia", "f-juzgado"].forEach((id) => (el(id).value = ""));
    ["f-expediente", "f-acta", "f-causa", "f-patente", "f-fecha-desde", "f-fecha-hasta"].forEach((id) => (el(id).value = ""));
    el("f-duplicadas").checked = false;   // <-- nuevo (checkbox no usa .value)
    el("f-reescritas").checked = false;
    actualizarOpcionesMotivo()
    estado.filtros = {};
    estado.page = 1;
    cargarRegistros();
  });

  el("btn-actualizar").addEventListener("click", () => cargarRegistros());

  el("page-size").addEventListener("change", (ev) => {
    estado.pageSize = Number(ev.target.value);
    estado.page = 1;
    cargarRegistros();
  });
}

(async function init() {
  conectarMenu();
  conectarToggleFiltros();
  conectarModalFoto();
  conectarEventos();

  // Las 3 llamadas iniciales son independientes entre sí (ninguna necesita
  // el resultado de otra para empezar), así que van en paralelo en vez de
  // en cadena. Con esto la pantalla queda lista en el tiempo de la más
  // lenta de las tres, no en la suma de las tres.
  await Promise.all([
    cargarOpcionesFiltro(),
    cargarRegistros(),
    initExportar(),
  ]);
})();