// ── Harness del AJAX de /alertas: resolver quita la fila y baja los
// contadores en el sitio, sin recargar la pagina.
//
// Ejecuta el script inline de la plantilla en un DOM minimo (Node puro,
// sin dependencias) con una fila pendiente, un contador de alertas y un
// fetch simulado. Comprueba que la fila se va con una animacion corta,
// que el contador baja y que sale el toast.
//
// Uso: node alertas_harness.mjs  (lee {html, idAlerta} por stdin y
//       escribe {filaEliminada, animacion, contador, avisos, errores})
import vm from 'node:vm';

class Elemento {
    constructor(tag, atributos = {}) {
        this.tagName = String(tag).toUpperCase();
        this.attributes = { ...atributos };
        this.style = {};
        this.textContent = '';
        this.parentNode = null;
        this.childNodes = [];
        this.eliminado = false;
        const self = this;
        this.classList = {
            _clases: new Set(),
            add(...cs) { cs.forEach(c => self.classList._clases.add(c)); },
            remove(...cs) { cs.forEach(c => self.classList._clases.delete(c)); },
            contains(c) { return this._clases.has(c); },
        };
    }

    get id() { return this.attributes.id || ''; }

    get dataset() { return this.attributes; }

    setAttribute(nombre, valor) { this.attributes[nombre] = String(valor); }

    getAttribute(nombre) {
        return nombre in this.attributes ? this.attributes[nombre] : null;
    }

    remove() {
        this.eliminado = true;
        if (this.parentNode) this.parentNode.removeChild(this);
    }

    removeChild(hijo) {
        const i = this.childNodes.indexOf(hijo);
        if (i >= 0) this.childNodes.splice(i, 1);
        hijo.parentNode = null;
    }
}

function crearDocumento(idAlerta) {
    const cuerpo = new Elemento('tbody');
    const filaObjetivo = new Elemento('tr', { 'data-id': String(idAlerta) });
    const filaOtra = new Elemento('tr', { 'data-id': '999999' });
    cuerpo.appendChild = (hijo) => {
        hijo.parentNode = cuerpo;
        cuerpo.childNodes.push(hijo);
        return hijo;
    };
    cuerpo.appendChild(filaObjetivo);
    cuerpo.appendChild(filaOtra);
    const contador = new Elemento('span', { id: 'contador-alertas' });
    contador.textContent = '2';
    const stat = new Elemento('div', { class: 'stat-value' });
    stat.textContent = '2';

    const porId = new Map([['contador-alertas', contador]]);
    const doc = {
        body: new Elemento('body'),
        getElementById: (id) => porId.get(id) || null,
        querySelector: (selector) => {
            const m = /^tr\[data-id="([^"]+)"\]$/.exec(selector);
            if (m) {
                return cuerpo.childNodes.find(
                    f => f.attributes['data-id'] === m[1]) || null;
            }
            if (selector === '.stat-value') return stat;
            if (selector.startsWith('#')) return porId.get(selector.slice(1)) || null;
            return null;
        },
        querySelectorAll: () => [],
        addEventListener: () => {},
        removeEventListener: () => {},
    };
    return { doc, cuerpo, filaObjetivo, contador, stat };
}

async function leerEntrada() {
    const trozos = [];
    for await (const trozo of process.stdin) trozos.push(trozo);
    return JSON.parse(trozos.join(''));
}

function scriptsDe(html) {
    const scripts = [];
    const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
    let m;
    while ((m = re.exec(html)) !== null) {
        if (/\bsrc\s*=/i.test(m[1])) continue;
        scripts.push(m[2]);
    }
    return scripts;
}

const config = await leerEntrada();
const { doc, cuerpo, filaObjetivo, contador } = crearDocumento(config.idAlerta);
const errores = [];
const avisos = [];

const sandbox = {
    document: doc,
    window: { document: doc },
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: async () => ({ ok: true, json: async () => ({ ok: true }) }),
    avisoAccion: (opts) => {
        avisos.push({
            mensaje: opts && opts.mensaje,
            tipo: opts && opts.tipo,
        });
    },
};
vm.createContext(sandbox);

// Solo el script de la pagina de alertas: el resto (base, bootstrap) no
// aporta a este escenario y necesita globals del navegador.
const codigo = scriptsDe(config.html).find(s => s.includes('resolverAlertaAjax'));
if (!codigo) {
    errores.push('no se encontro el script resolverAlertaAjax');
} else {
    try {
        vm.runInContext(codigo, sandbox, { filename: 'alertas.js' });
    } catch (e) {
        errores.push('script: ' + e.message);
    }
}

try {
    await sandbox.resolverAlertaAjax(config.idAlerta);
} catch (e) {
    errores.push('resolverAlertaAjax: ' + e.message);
}

// La fila se va con una animacion corta: se espera a que pase.
await new Promise(r => setTimeout(r, 400));

process.stdout.write(JSON.stringify({
    filaEliminada: filaObjetivo.eliminado,
    animacion: Boolean(filaObjetivo.style.transition)
        && filaObjetivo.style.opacity === '0',
    contador: contador.textContent,
    avisos,
    errores,
}));
