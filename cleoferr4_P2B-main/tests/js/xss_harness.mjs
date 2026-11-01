// ── Harness XSS: ejecuta el JS de una plantilla en un DOM mínimo ─────────
//
// Reproduce lo que el navegador hace con innerHTML: lo PARSEA como HTML y
// ejecuta los manejadores on* (onerror de un <img> que no carga). Así se
// puede comprobar con un nombre malicioso si queda como marcado vivo
// (HTML activo) o como texto. Node puro, sin dependencias.
//
// Uso: node xss_harness.mjs  (lee {html, escenario, payload, ...} por stdin
//       y escribe {alertas, errores, arboles, texto} por stdout)
import vm from 'node:vm';

const VACIOS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img',
    'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);

const ENTIDADES = {
    ['&' + 'lt;']: '<',
    ['&' + 'gt;']: '>',
    ['&' + 'quot;']: '"',
    ['&' + 'apos;']: "'",
    ['&' + '#39;']: "'",
    ['&' + '#0*39;']: "'",
    ['&' + 'nbsp;']: ' ',
    ['&' + 'amp;']: '&',
};

function decodificar(texto) {
    return String(texto).replace(/&[a-z#0-9]+;/gi, (entidad) => {
        const clave = entidad.toLowerCase();
        if (clave in ENTIDADES) return ENTIDADES[clave];
        const m = /^&(#0*39);$/.exec(clave);
        return m ? "'" : entidad;
    });
}

// ── Parser mínimo de HTML (suficiente para lo que hacen las plantillas) ──
function parsear(html) {
    const raiz = { tipo: 'raiz', tag: '#raiz', attrs: {}, hijos: [] };
    const pila = [raiz];
    const tokens = /<!--[\s\S]*?-->|<\/?[a-zA-Z][^>]*>|[^<]+/g;
    let m;
    while ((m = tokens.exec(String(html))) !== null) {
        const tok = m[0];
        if (tok.startsWith('<!--')) continue;
        if (tok.startsWith('<')) {
            const autoCierre = tok.endsWith('/>');
            const cuerpo = tok.replace(/^<\/?/, '').replace(/\/?>$/, '');
            const corte = cuerpo.search(/\s/);
            const tag = (corte === -1 ? cuerpo : cuerpo.slice(0, corte)).toLowerCase();
            const attrs = {};
            if (corte !== -1) {
                const re = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
                let a;
                while ((a = re.exec(cuerpo.slice(corte))) !== null) {
                    const valor = a[2] ?? a[3] ?? a[4] ?? '';
                    attrs[a[1].toLowerCase()] = decodificar(valor);
                }
            }
            if (tok.startsWith('</')) {
                for (let i = pila.length - 1; i > 0; i--) {
                    if (pila[i].tag === tag) { pila.length = i; break; }
                }
                continue;
            }
            const nodo = { tipo: 'elemento', tag, attrs, hijos: [] };
            pila[pila.length - 1].hijos.push(nodo);
            if (!autoCierre && !VACIOS.has(tag)) pila.push(nodo);
        } else {
            pila[pila.length - 1].hijos.push({ tipo: 'texto', tag: '#text',
                attrs: {}, texto: decodificar(tok), hijos: [] });
        }
    }
    return raiz;
}

// ── DOM mínimo ─────────────────────────────────────────────────────────
class Nodo {
    constructor(tag, doc, texto) {
        this.tagName = String(tag).toUpperCase();
        this.ownerDocument = doc;
        this.childNodes = [];
        this.attributes = {};
        this.style = {};
        this.dataset = {};
        this.className = '';
        this.value = '';
        this.disabled = false;
        this.hidden = false;
        this.title = '';
        this.type = '';
        this.name = '';
        this.options = [];
        this.parentNode = null;
        this._texto = texto === undefined ? '' : String(texto);
        this._listeners = {};
        this._clases = new Set();
        const self = this;
        this.classList = {
            add(...cs) { cs.forEach(c => self._clases.add(c)); self._sincronizarClases(); },
            remove(...cs) { cs.forEach(c => self._clases.delete(c)); self._sincronizarClases(); },
            toggle(c, forzar) {
                const existe = self._clases.has(c);
                const poner = forzar === undefined ? !existe : !!forzar;
                if (poner) self._clases.add(c); else self._clases.delete(c);
                self._sincronizarClases();
                return poner;
            },
            contains(c) { return self._clases.has(c); },
        };
    }
    _sincronizarClases() { this.className = [...this._clases].join(' '); }
    get children() { return this.childNodes.filter(n => n.tagName !== '#TEXT'); }
    get textContent() {
        if (this.tagName === '#TEXT') return this._texto;
        return this.childNodes.map(n => n.textContent).join('');
    }
    set textContent(v) {
        this.childNodes = [new Nodo('#text', this.ownerDocument, v)];
    }
    get innerHTML() { return this._html || ''; }
    set innerHTML(v) {
        this._html = String(v);
        this.childNodes = construirNodos(parsear(this._html).hijos, this.ownerDocument);
        if (this.ownerDocument) this.ownerDocument._registrarInnerHTML(this, this._html);
        if (this.ownerDocument) this.ownerDocument._disparar(this.childNodes);
    }
    appendChild(hijo) {
        if (hijo && hijo.ownerDocument === null) hijo.ownerDocument = this.ownerDocument;
        hijo.parentNode = this;
        this.childNodes.push(hijo);
        return hijo;
    }
    removeChild(hijo) {
        const i = this.childNodes.indexOf(hijo);
        if (i >= 0) this.childNodes.splice(i, 1);
        hijo.parentNode = null;
        return hijo;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    setAttribute(nombre, valor) { this.attributes[nombre] = String(valor); }
    getAttribute(nombre) { return nombre in this.attributes ? this.attributes[nombre] : null; }
    hasAttribute(nombre) { return nombre in this.attributes; }
    removeAttribute(nombre) { delete this.attributes[nombre]; }
    addEventListener(tipo, fn) { (this._listeners[tipo] ||= []).push(fn); }
    removeEventListener(tipo, fn) {
        this._listeners[tipo] = (this._listeners[tipo] || []).filter(f => f !== fn);
    }
    dispatch(tipo, evento) {
        (this._listeners[tipo] || []).forEach(fn => fn.call(this, evento || { type: tipo, target: this }));
    }
    click() { this.dispatch('click', { type: 'click', target: this }); }
    focus() {}
    blur() {}
    closest() { return null; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    getElementsByTagName() { return []; }
}

function construirNodos(nodos, doc) {
    return nodos.map(n => {
        if (n.tipo === 'texto') return new Nodo('#text', doc, n.texto);
        const el = new Nodo(n.tag, doc);
        el.attributes = Object.assign({}, n.attrs);
        el.childNodes = construirNodos(n.hijos, doc);
        el.childNodes.forEach(h => { h.parentNode = el; });
        return el;
    });
}

function serializar(nodo) {
    if (nodo.tagName === '#TEXT') return { texto: nodo._texto };
    return {
        tag: nodo.tagName.toLowerCase(),
        attrs: Object.assign({}, nodo.attributes),
        hijos: nodo.childNodes.map(serializar),
    };
}

// ── Entorno (documento, window, sandbox para vm) ───────────────────────
function crearEntorno(config) {
    const alertas = [];
    const innerHTMLs = [];
    const errores = [];

    const doc = {
        _registrarInnerHTML(el, valor) {
            innerHTMLs.push({ id: el.id || null, valor });
        },
        // El navegador dispara onerror/onload solo: un <img> que no carga
        // ejecuta su manejador. Eso es justo lo que delata el XSS.
        _disparar(nodos) {
            for (const nodo of nodos) {
                if (nodo.tagName === '#TEXT') continue;
                for (const [nombre, valor] of Object.entries(nodo.attributes)) {
                    if (nombre === 'onerror' || nombre === 'onload') {
                        try {
                            new Function(valor)();
                        } catch (e) {
                            errores.push('handler ' + nombre + ': ' + e.message);
                        }
                    }
                }
                this._disparar(nodo.childNodes);
            }
        },
        createElement: (tag) => new Nodo(tag, doc),
        createTextNode: (t) => new Nodo('#text', doc, t),
        getElementById(id) {
            if (!doc._ids.has(id)) {
                const el = new Nodo('div', doc);
                el.id = id;
                doc._ids.set(id, el);
            }
            return doc._ids.get(id);
        },
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener: () => {},
        removeEventListener: () => {},
        _ids: new Map(),
    };
    doc.body = new Nodo('body', doc);

    const almacen = {
        _datos: {},
        getItem(k) { return k in this._datos ? this._datos[k] : null; },
        setItem(k, v) { this._datos[k] = String(v); },
        removeItem(k) { delete this._datos[k]; },
        clear() { this._datos = {}; },
    };
    if (config.localStorage) Object.assign(almacen._datos, config.localStorage);

    const alerta = (mensaje) => { alertas.push(String(mensaje)); };

    const ventana = {
        document: doc,
        location: { href: '' },
        alert: alerta,
        confirm: () => false,
        prompt: () => null,
        addEventListener: () => {},
        removeEventListener: () => {},
        setTimeout, clearTimeout, setInterval, clearInterval,
        getComputedStyle: () => ({}),
        fetch: (...args) => sandbox.fetch(...args),
    };

    const sandbox = {
        document: doc,
        window: ventana,
        localStorage: almacen,
        console,
        alert: alerta,
        confirm: () => false,
        setTimeout, clearTimeout, setInterval, clearInterval,
        navigator: { userAgent: 'harness' },
        HTMLFormElement: class HTMLFormElement {},
        FormData: class FormData {},
        bootstrap: {
            Modal: class Modal {
                constructor() {}
                show() {}
                hide() {}
                static getInstance() { return new Modal(); }
            },
        },
        fetch: async (url) => {
            const destino = String(url);
            if (destino.includes('/clientes/registrar_pos')) {
                return { ok: true, json: async () => ({
                    ok: true, id_cliente: 777, ya_existia: false,
                    nombre: config.payload }) };
            }
            if (destino.includes('/carrito/precios')) {
                return { ok: true, json: async () => ({ ok: true, precios: {} }) };
            }
            return { ok: true, json: async () => ({ ok: true }) };
        },
    };
    globalThis.alert = alerta;
    return { doc, ventana, sandbox, alertas, innerHTMLs, errores, almacen };
}

// ── Escenarios ─────────────────────────────────────────────────────────
async function ejecutar(ctx, config) {
    const escenario = config.escenario;
    switch (escenario) {
        case 'carrito_tabla':
            ctx.renderCarrito();
            return ['tbodyCarrito'];
        case 'carrito_resumen':
            ctx.seleccionarComprobante('boleta_electronica');
            ctx.irPaso3();
            return ['resumenConfirmar'];
        case 'pos_catalogo':
            ctx.renderCatalogo();
            return ['gridProductos'];
        case 'pos_ticket':
            ctx.agregar(config.productoId);
            return ['listaItems'];
        case 'pos_cliente':
            await ctx.guardarClientePos();
            return ['compResumen'];
        default:
            throw new Error('escenario desconocido: ' + escenario);
    }
}

function extraerScripts(html) {
    const scripts = [];
    const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = re.exec(html)) !== null) {
        if (/\bsrc\s*=/i.test(m[1])) continue;
        scripts.push(m[2]);
    }
    return scripts;
}

async function leerEntrada() {
    const trozos = [];
    for await (const trozo of process.stdin) trozos.push(trozo);
    return JSON.parse(trozos.join(''));
}

const config = await leerEntrada();
const entorno = crearEntorno(config);
vm.createContext(entorno.sandbox);

const scripts = extraerScripts(config.html);
for (const [i, codigo] of scripts.entries()) {
    try {
        vm.runInContext(codigo, entorno.sandbox, { filename: 'plantilla-' + i + '.js' });
    } catch (e) {
        entorno.errores.push('script ' + i + ': ' + e.message);
    }
}

let objetivo = [];
try {
    objetivo = await ejecutar(entorno.sandbox, config);
} catch (e) {
    entorno.errores.push('escenario: ' + e.message);
}
// Deja asentar las promesas pendientes (fetch de precios, guardar cliente).
await new Promise(r => setTimeout(r, 20));

const arboles = {};
const texto = {};
for (const id of objetivo) {
    const el = entorno.doc.getElementById(id);
    arboles[id] = el.childNodes.map(serializar);
    texto[id] = el.textContent;
}

process.stdout.write(JSON.stringify({
    alertas: entorno.alertas,
    errores: entorno.errores,
    arboles,
    texto,
}));
