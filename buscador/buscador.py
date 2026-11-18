"""MOCK del buscador de ecomodico.com: lo que queremos agregar o cambiar en el search actual.

Esto es un prototipo escrito sin acceso al backend de la tienda, solo viendo el frontend. Sirve para mostrar el
COMPORTAMIENTO esperado, no para copiarlo tal cual. Qué es de verdad y qué es de juguete:

  Real (la idea que queremos):   normalización, sinónimos, frases, palabras pegadas, tipeo, ranking, categorías.
  De juguete (hay que reemplazar):
    - FuenteEjemplo   -> en el prototipo los productos se leían del HTML público de la tienda. Acá la fuente es una
                         interfaz: `productos()` tiene que devolver la lista de dicts del contrato de abajo, sacada
                         de la base del catálogo.
    - ReglasBD        -> SQLite en memoria. Las reglas (sinónimos, frases, categorías) tienen que vivir en la base
                         de la tienda y poder editarse sin redeploy (ver el .md).
    - estado global   -> módulo con variables globales; en producción, un objeto por proceso/worker.

CONTRATO DE PRODUCTO (cada dict que devuelve la fuente):
    id, variante_id, nombre, marca, precio, precio_anterior|None, descuento_pct|None,
    precio_sin_impuestos|None, disponible (bool), stock|None, max_por_compra|None, envio_gratis (bool),
    url, imagen|None,
    cat_hoja (str, categoría más específica), familia (list[str], categorías padre),
    atributos (list[str], ej. sabor/aroma/color), cat_urls ({nombre_categoria: "/ruta"}),
    top_ventas (bool), pos (int, orden en la tienda, 0 = primero)

Uso:
    cargar_catalogo(FuenteEjemplo().productos())
    buscar("cocacola")                     # -> dict con productos, categorias, marcas, total, corregida, via...
    guardar_regla("sinonimos", "champu", ["shampoo"])   # lo llamaría la pantalla de edición
"""

import json
import math
import re
import sqlite3
import time
import unicodedata
from collections import Counter, defaultdict

from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

# Peso de cada campo: una palabra en el nombre vale más que en la marca, la categoría o un atributo.
PESOS = {"nombre": 10.0, "marca": 8.0, "categoria": 7.0, "familia": 4.0, "atributo": 3.0}

STOP = set("""de del la las el los lo para por con en y e o a al un una unos unas x que mi me te se su sus
quiero busco buscando necesito tenes tienen hay algo cosa cosas comprar compra precio precios online
venta barato barata baratos economico economica envio domicilio delivery producto productos""".split())
OFERTA = {"oferta", "promo", "promocion", "descuento", "liquidacion", "sale"}  # se comparan por raíz
# Palabras que describen la tienda y no el producto ("supermercado online a domicilio"). Salen de la búsqueda;
# si no queda nada, se muestra lo más vendido. Salen de las keywords reales de Google Ads.
GENERICAS = set("""online on line web site sitio tienda tiendas delivery domicilio envio envios cerca pedido pedidos
pedir compras ecomodico super supermercado supermercados mercado hiper hipermercado mayorista argentina caba amba
buenos aires capital""".split())
# Competidores. Si la búsqueda es solo el nombre, se reemplaza por lo que la gente va a buscar ahí.
# Los ambiguos (disco de pesas, talle jumbo, protector "día") solo cuentan si vinieron con "online", "super"...
COMPETIDORES = {
    "coto": "", "coto digital": "", "carrefour": "", "changomas": "", "chango mas": "", "la anonima": "",
    "walmart": "", "vea": "", "dia": "", "disco": "", "jumbo": "",
    "farmacity": "cuidado personal", "famacity": "cuidado personal", "selma": "cuidado personal",
    "nutrishop": "suplementos deportivos", "disfit": "suplementos deportivos", "entreno": "suplementos deportivos",
    "entreno com ar": "suplementos deportivos", "pitbull": "suplementos deportivos", "suplementate": "suplementos deportivos",
    "ibera": "suplementos deportivos", "suple ya": "suplementos deportivos", "supleya": "suplementos deportivos",
    "army fitness": "suplementos deportivos", "newgarden": "suplementos deportivos", "new garden": "suplementos deportivos",
    "capsulandia": "cafe capsula", "pitbull suplementos": "suplementos deportivos",
    "central suplementos": "suplementos deportivos", "ibera suplementos": "suplementos deportivos",
}
COMPETIDORES_AMBIGUOS = {"dia", "disco", "jumbo", "vea", "pitbull"}
# Búsquedas que son una categoría entera: se muestra lo más vendido de esas categorías (hoja o familia).
SEMILLA_CATEGORIAS_ALIAS = {
    "limpieza": ["Cuidado Del Hogar"], "producto limpieza": ["Cuidado Del Hogar"],
    "productos limpieza": ["Cuidado Del Hogar"], "articulos limpieza": ["Cuidado Del Hogar"],
    "limpieza hogar": ["Cuidado Del Hogar"], "hogar limpieza": ["Cuidado Del Hogar"],
    "bebidas sin alcohol": ["Gaseosa", "Jugos", "Aguas", "Aguas Saborizadas"],
    "bebidas alcoholicas": ["Bebidas Con Alcohol", "Vinos"], "bebidas con alcohol": ["Bebidas Con Alcohol", "Vinos"],
    "electrodomesticos": ["Electro Y Hogar"], "pequenos electrodomesticos": ["Pequeños Electrodomésticos"],
    "suplementos": ["Suplementos Deportivos", "Vitaminas, Minerales Y Antioxidantes"],
    "suplementos fitness": ["Suplementos Deportivos"], "suplementos gym": ["Suplementos Deportivos"],
    "articulos hogar": ["Hogar"], "articulos para el hogar": ["Hogar"],
    "comida": ["Almacén"],
}

# Frases que se reemplazan enteras antes de buscar (ya normalizadas, sin tildes).
SEMILLA_FRASES = {
    "papel de bano": "papel higienico",
    "rollo de cocina": "rollo cocina",
    "papel de cocina": "rollo cocina",
    "crema dental": "pasta dental",
    "comida para": "alimento para",
    "comida de": "alimento para",
    "gel antibacterial": "alcohol gel",
    "alcohol en gel": "alcohol gel",
    "on line": "online",
    "jabon para ropa": "jabon ropa",
    "jabon para lavar ropa": "jabon ropa",
    "maquina de afeitar": "afeitadora",
    "aire acondicionado": "aire acondicionado",
    "lavaplatos": "detergente",
    "lava platos": "detergente",
    "agua lavandina": "lavandina",
    "toallitas femeninas": "toallas femeninas",
    "toallas higienicas": "toallas femeninas",
    "pantalla solar": "protector solar",
    "bloqueador solar": "protector solar",
    "dulce leche": "dulce de leche",
}
# Palabra -> con qué buscarla en el catálogo (se reemplaza; si querés conservarla, incluila).
SEMILLA_SINONIMOS = {
    "champu": ["shampoo"], "champoo": ["shampoo"], "shampo": ["shampoo"], "sampu": ["shampoo"],
    "tele": ["televisor", "tv"], "televisor": ["televisor", "tv"], "tv": ["tv", "televisor"],
    "celu": ["celular"], "auris": ["auricular"], "compu": ["notebook", "computadora"],
    "micro": ["microonda"], "heladera": ["heladera", "refrigerador"], "nevera": ["heladera"],
    "lavadora": ["lavarropa"], "lavarropas": ["lavarropa"],
    "birra": ["cerveza"], "birras": ["cerveza"], "chela": ["cerveza"],
    "yogur": ["yogur", "yogurt"], "yogurt": ["yogurt", "yogur"],
    "galleta": ["galletita", "galleta"], "galletas": ["galletita", "galleta"],
    "cloro": ["lavandina", "cloro"],
    "whey": ["whey"], "prote": ["proteina"], "protein": ["proteina", "protein"],
    "creatine": ["creatina"], "crea": ["creatina"],
    "barrita": ["barra", "barrita"], "barritas": ["barra", "barrita"],
    "vitc": ["vitamina"],
    "toallitas": ["toallita", "toalla"],
    "desodorante": ["desodorante"], "deo": ["desodorante"],
    "afeitar": ["afeitar", "afeitadora", "maquina"], "gillette": ["gillette"],
    "nespresso": ["nespresso"], "capsula": ["capsula"],
    "perro": ["perro"], "perros": ["perro"], "gato": ["gato"], "gatos": ["gato"], "gatito": ["gatito", "kitten"],
    "cachorro": ["cachorro", "puppy"],
    "comida": ["alimento", "comida"],  # "comida gato"; sola, "comida" va a la categoría Almacén
}
UNIDADES = {"gr": "g", "grs": "g", "gramo": "g", "gramos": "g", "kilo": "kg", "kilos": "kg", "kgs": "kg",
            "lt": "l", "lts": "l", "litro": "l", "litros": "l", "cc": "ml", "mililitro": "ml", "mililitros": "ml"}

# ---------- reglas editables: sinónimos, frases y categorías, guardadas en la base ----------
# Los tres diccionarios de abajo (SEMILLA_*) son solo los valores iniciales. En marcha, el buscador usa lo que hay en
# la tabla `reglas`; quien administra la tienda lo cambia desde una pantalla (a construir por quien despliega).
# SINONIMOS / FRASES / CATEGORIAS_ALIAS son las variables "vivas" que usa el resto del módulo (se reemplazan al recargar).

SINONIMOS: dict[str, list[str]] = {}       # "champu" -> ["shampoo"]            (palabra del cliente -> palabras del catálogo)
FRASES: dict[str, str] = {}                # "papel de bano" -> "papel higienico" (frase entera -> frase)
CATEGORIAS_ALIAS: dict[str, list[str]] = {}  # "limpieza" -> ["Cuidado Del Hogar"] (búsqueda -> categorías del catálogo)
_NOMBRES = {"sinonimos": "SINONIMOS", "frases": "FRASES", "categorias": "CATEGORIAS_ALIAS"}  # tipo -> variable global
SEMILLA = {"sinonimos": SEMILLA_SINONIMOS, "frases": SEMILLA_FRASES, "categorias": SEMILLA_CATEGORIAS_ALIAS}
MAX_ENTRADAS, MAX_VALORES, MAX_TEXTO = 2000, 10, 80
REVISAR_CADA = 30  # segundos: así varios procesos/servidores toman un cambio hecho en otro sin reiniciar


class ReglasBD:
    """Mock sobre SQLite. En producción: una tabla en la base de la tienda con estas mismas columnas
    (tipo, clave, valor JSON, actualizado) y quién/cuándo cambió (auditoría)."""

    def __init__(self, ruta: str = ":memory:"):
        self.con = sqlite3.connect(ruta, check_same_thread=False)
        self.con.execute("""CREATE TABLE IF NOT EXISTS reglas (
            tipo TEXT NOT NULL, clave TEXT NOT NULL, valor TEXT NOT NULL, actualizado REAL NOT NULL,
            PRIMARY KEY (tipo, clave))""")
        if not self.con.execute("SELECT 1 FROM reglas LIMIT 1").fetchone():  # primera vez: se siembra con los valores iniciales
            for tipo, d in SEMILLA.items():
                for k, v in d.items():
                    self.guardar(tipo, normalizar(k), v)

    def todas(self) -> dict[str, dict]:
        out: dict[str, dict] = {t: {} for t in _NOMBRES}
        for tipo, clave, valor in self.con.execute("SELECT tipo, clave, valor FROM reglas"):
            if tipo in out:
                out[tipo][clave] = json.loads(valor)
        return out

    def guardar(self, tipo: str, clave: str, valor) -> None:
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO reglas VALUES (?,?,?,?)", (tipo, clave, json.dumps(valor, ensure_ascii=False), time.time()))

    def borrar(self, tipo: str, clave: str) -> None:
        with self.con:
            self.con.execute("DELETE FROM reglas WHERE tipo=? AND clave=?", (tipo, clave))
            self.con.execute("INSERT OR REPLACE INTO reglas VALUES ('_borrado', ?, '0', ?)", (f"{tipo}:{clave}", time.time()))  # para que otros procesos noten el cambio

    def version(self) -> float:
        return self.con.execute("SELECT coalesce(max(actualizado), 0) FROM reglas").fetchone()[0]


_reglas: ReglasBD  # se crea más abajo, cuando ya existe normalizar()
_version_reglas = 0.0
_ultima_revision = 0.0


def recargar_reglas() -> None:
    global _version_reglas
    todas = _reglas.todas()
    # Se reemplaza el diccionario entero (una sola asignación) en vez de vaciarlo y llenarlo: así una búsqueda en
    # otro hilo nunca ve las reglas a medio cargar.
    globals().update({nombre: todas[tipo] for tipo, nombre in _NOMBRES.items()})
    _version_reglas = _reglas.version()
    _cache_res.clear()  # las respuestas guardadas se calcularon con las reglas viejas


def _revisar_reglas() -> None:
    global _ultima_revision
    if time.time() - _ultima_revision > REVISAR_CADA:
        _ultima_revision = time.time()
        if _reglas.version() != _version_reglas:
            recargar_reglas()


def categorias_conocidas() -> list[str]:
    """Nombres exactos de categorías del catálogo: lo que acepta `categorias` (para el selector de la pantalla)."""
    return sorted({d["cat_hoja"] for d in _estado["productos"] if d["cat_hoja"]} | {f for d in _estado["productos"] for f in d["familia"]})


def guardar_regla(tipo: str, clave: str, valor) -> None:
    """Valida, guarda en la base y aplica ya mismo. Lanza ValueError con un mensaje legible para mostrar en la pantalla."""
    if tipo not in _NOMBRES:
        raise ValueError("Tipo de regla desconocido")
    clave = normalizar(str(clave))[:MAX_TEXTO]
    if not clave:
        raise ValueError("La clave no puede estar vacía")
    if len(listar_reglas(tipo)) >= MAX_ENTRADAS and clave not in listar_reglas(tipo):
        raise ValueError(f"Demasiadas reglas (máx. {MAX_ENTRADAS})")
    if tipo == "frases":
        valor = normalizar(str(valor))[:MAX_TEXTO]
        if not valor:
            raise ValueError("La frase de reemplazo no puede estar vacía")
    else:
        if not isinstance(valor, list) or not 1 <= len(valor) <= MAX_VALORES:
            raise ValueError(f"Tiene que ser una lista de 1 a {MAX_VALORES}")
        if tipo == "sinonimos":
            valor = [normalizar(str(x)) for x in valor]
            if any(not x or " " in x for x in valor):  # el índice es por palabra: una frase nunca coincidiría
                raise ValueError("Cada equivalente es una sola palabra (para varias palabras usá 'frases')")
        else:
            valor = [str(x).strip()[:MAX_TEXTO] for x in valor]
            malas = [x for x in valor if _estado["productos"] and x not in set(categorias_conocidas())]
            if malas:
                raise ValueError("No existe en el catálogo: " + ", ".join(malas))
    _reglas.guardar(tipo, clave, valor)
    recargar_reglas()


def borrar_regla(tipo: str, clave: str) -> None:
    if tipo not in _NOMBRES:
        raise ValueError("Tipo de regla desconocido")
    _reglas.borrar(tipo, normalizar(str(clave)))
    recargar_reglas()


def listar_reglas(tipo: str) -> dict:
    return dict(globals()[_NOMBRES[tipo]])


_estado = {"productos": [], "por_id": {}, "indice": {}, "familias": {}, "urls_cat": {}, "vocab": [], "frec": Counter(), "superficie": {}}
# Respuestas ya calculadas: en un pico todos buscan lo mismo ("leche", "coca"), así se calcula una sola vez.
CACHE_RESULTADOS = 900  # 15 minutos
CACHE_MAX = 20000  # tope de búsquedas distintas en memoria; al llegar se vacía
_cache_res: dict[tuple, tuple[float, dict]] = {}


# ---------- texto ----------
def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def raiz(t: str) -> str:
    """Raíz liviana para español: 'leches'/'leche' -> 'lech', 'pañales'/'pañal' -> 'panal'."""
    t = UNIDADES.get(t, t)
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) > 3 and t.endswith("e"):
        t = t[:-1]
    return t


def tokens(s: str) -> list[str]:
    return [raiz(t) for t in normalizar(s).split()]


# ---------- índice ----------

_reglas = ReglasBD()
recargar_reglas()

def cargar_catalogo(docs: list[dict]) -> None:
    """Arma el índice con los productos de la fuente (ver el contrato en el docstring del módulo).
    Se llama al arrancar y cuando cambia el catálogo (cada hora, o por evento si la tienda lo permite)."""
    indice: dict[str, dict[str, float]] = defaultdict(dict)
    frec, superficie = Counter(), defaultdict(Counter)
    for d in docs:
        campos = [("nombre", d["nombre"]), ("marca", d["marca"] or ""), ("categoria", d["cat_hoja"]),
                  ("familia", " ".join(d["familia"])), ("atributo", " ".join(d["atributos"]))]
        vistos = set()
        for campo, texto in campos:
            for crudo in normalizar(texto).split():
                t = raiz(crudo)
                superficie[t][crudo] += 1
                if PESOS[campo] > indice[t].get(d["id"], 0):
                    indice[t][d["id"]] = PESOS[campo]
                vistos.add(t)
        frec.update(vistos)
        d["_nombre_norm"] = " " + " ".join(tokens(d["nombre"])) + " "
        d["_cat_tokens"] = set(tokens(d["cat_hoja"]))
        d["_cat_norm"] = " ".join(tokens(d["cat_hoja"]))
    familias = {_clave(f): f for d in docs for f in d["familia"]}
    urls_cat = {n: u for d in docs for n, u in (d.get("cat_urls") or {}).items()}
    _estado.update(productos=docs, por_id={d["id"]: d for d in docs}, indice=dict(indice), familias=familias, urls_cat=urls_cat,
                   vocab=list(indice), frec=frec,
                   superficie={t: c.most_common(1)[0][0] for t, c in superficie.items()})
    _cache_res.clear()  # con precios y stock nuevos, las respuestas guardadas ya no sirven


# ---------- búsqueda ----------

def _alternativas(t: str, ultimo: bool, idx: dict, corregir: bool = True) -> tuple[list[tuple[str, float]], str | None]:
    """Con qué palabras del catálogo puede coincidir una palabra de la búsqueda.
    Devuelve [(palabra_del_índice, calidad 0-1)] y, si hubo corrección, la palabra corregida."""
    alts: dict[str, float] = {}
    if t in idx:
        alts[t] = 1.0
    # Si la palabra ya existe con varios productos ("sal"), no la completamos: si no, "sal" trae salsa, saladix, salmón...
    if ultimo and len(t) >= 2 and len(idx.get(t, ())) < 5:
        # Mientras se escribe: "lec" ya sugiere "leche". Las más frecuentes primero.
        pref = [v for v in _estado["vocab"] if v.startswith(t) and v != t]
        for v in sorted(pref, key=lambda v: -_estado["frec"][v])[:25]:
            alts.setdefault(v, 0.85)
    corregida = None
    if not alts and corregir and len(t) >= 4:
        dist = 1 if len(t) < 8 else 2
        cands = process.extract(t, _estado["vocab"], scorer=DamerauLevenshtein.distance,
                                score_cutoff=dist, limit=8)
        # Casi nadie se equivoca en la primera letra: "lehce" -> leche sí, "helado" -> pelado no.
        cands = [c for c in cands if c[0][0] == t[0]]
        if cands:
            mejor = min(cands, key=lambda c: (c[1], c[0][0] != t[0], -_estado["frec"][c[0]]))
            for v, d, _ in cands:
                if d == mejor[1]:
                    alts[v] = 0.75 if d == 1 else 0.6
            corregida = mejor[0]
    return list(alts.items()), corregida


def _partir(t: str, idx: dict) -> list[str] | None:
    """'cocacola' -> ['coca', 'cola'] si ambas mitades existen en el catálogo."""
    if t in idx or len(t) < 6:
        return None
    mejores = [(t[:i], t[i:]) for i in range(3, len(t) - 2) if raiz(t[:i]) in idx and raiz(t[i:]) in idx]
    if not mejores:
        return None
    a, b = max(mejores, key=lambda ab: _estado["frec"][raiz(ab[0])] + _estado["frec"][raiz(ab[1])])
    return [raiz(a), raiz(b)]


def _es_oferta(c: str) -> bool:
    return c in OFERTA or raiz(c) in OFERTA


def _es_generica(c: str) -> bool:
    return c in GENERICAS or raiz(c) in GENERICAS


def _clave(texto: str) -> str:
    """Forma comparable de una búsqueda o un nombre de categoría: 'Cuidado Del Hogar' -> 'cuidado hogar'."""
    return " ".join(raiz(t) for t in normalizar(texto).split() if t not in STOP)


def interpretar(q: str) -> dict:
    """Pasa la búsqueda a palabras del catálogo: sinónimos, palabras pegadas, plurales."""
    norm = normalizar(q)
    for frase, reemplazo in sorted(FRASES.items(), key=lambda x: -len(x[0])):
        norm = re.sub(rf"\b{frase}\b", reemplazo, norm)
    crudos = norm.split()
    oferta = any(_es_oferta(c) for c in crudos)
    generica = any(_es_generica(c) for c in crudos)
    crudos = [c for c in crudos if not _es_generica(c)]
    resto = " ".join(c for c in crudos if c not in STOP and not _es_oferta(c))
    competidor = None
    if resto in COMPETIDORES and (resto not in COMPETIDORES_AMBIGUOS or generica):
        competidor, crudos = resto, COMPETIDORES[resto].split()
    idx = _estado["indice"]
    grupos, mostrar = [], []  # cada grupo = una palabra de la búsqueda con sus variantes
    for c in crudos:
        if c in STOP or _es_oferta(c):
            continue
        if c in SINONIMOS:
            vs = [raiz(v) for v in SINONIMOS[c]]
            grupos.append(vs); mostrar.append(c if raiz(c) in vs else SINONIMOS[c][0])
            continue
        t = raiz(c)
        partes = _partir(t, idx)
        if partes:
            grupos += [[p] for p in partes]; mostrar += [_estado["superficie"].get(p, p) for p in partes]
        else:
            grupos.append([t]); mostrar.append(c)
    return {"grupos": grupos, "mostrar": mostrar, "oferta": oferta, "normalizada": norm,
            "generica": generica or competidor is not None, "competidor": competidor, "clave": _clave(" ".join(crudos))}


def _categorias_de(clave: str) -> list[str] | None:
    """Si la búsqueda es el nombre de una familia ('bebidas', 'mascotas') o un alias ('limpieza'), sus categorías."""
    alias = {_clave(k): v for k, v in CATEGORIAS_ALIAS.items()}
    if clave in alias:
        return alias[clave]
    if clave in _estado["familias"]:
        return [_estado["familias"][clave]]
    return None


def _listar(categorias: list[str] | None, oferta: bool) -> list:
    """Lo más vendido (de unas categorías o de toda la tienda), con stock primero."""
    cats, total = set(categorias or []), len(_estado["productos"]) or 1
    res = []
    for d in _estado["productos"]:
        if cats and d["cat_hoja"] not in cats and not cats & set(d["familia"]):
            continue
        desc = min(d.get("descuento_pct") or 0, 60)
        s = (3 if d.get("top_ventas") else 0) + (2 if d["disponible"] else -50) + (1 - d["pos"] / total) * 2
        s += (desc / 10) if oferta else (desc / 60)
        res.append((s, d))
    res.sort(key=lambda x: -x[0])
    return res


def _buscar(grupos: list[list[str]], parcial: bool, oferta: bool, minimo: int | None = None):
    idx = _estado["indice"]
    if not grupos:
        return [], {}
    por_grupo, correcciones = [], {}
    for i, variantes in enumerate(grupos):
        alts: dict[str, float] = {}
        exacta = any(v in idx for v in variantes)  # "comida gato": si "alimento" existe, no corregir "comida"
        for v in variantes:
            a, corr = _alternativas(v, parcial and i == len(grupos) - 1, idx, corregir=not exacta)
            for w, q in a:
                alts[w] = max(alts.get(w, 0), q)
            if corr:
                correcciones[v] = corr
        por_grupo.append(alts)

    puntos: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(grupos))
    for i, alts in enumerate(por_grupo):
        for w, calidad in alts.items():
            for pid, peso in idx.get(w, {}).items():
                s = calidad * peso
                if s > puntos[pid][i]:
                    puntos[pid][i] = s
    n = len(grupos)
    minimo = n if minimo is None else minimo
    frase = " ".join(max(alts, key=alts.get) if alts else g[0] for g, alts in zip(grupos, por_grupo))
    total = len(_estado["productos"]) or 1
    res = []
    for pid, ps in puntos.items():
        coinciden = sum(1 for x in ps if x > 0)
        if coinciden < minimo:
            continue
        d = _estado["por_id"][pid]
        s = sum(ps) / n
        palabras = [w for alts in por_grupo for w, q in alts.items() if q >= 0.75]
        if d["_cat_tokens"] & set(palabras):
            s += 3  # "leche" -> primero lo que está en la categoría Leches
            if d["_cat_norm"] == frase:
                s += 2.5  # y antes la categoría "Leche" que "Leches De Fórmula"
        if por_grupo and any(d["_nombre_norm"].startswith(" " + w + " ") for w in por_grupo[0]):
            s += 1.5
        if n > 1 and all(any(f" {w} " in d["_nombre_norm"] for w in alts) for alts in por_grupo):
            s += 1.5
        s += 2 if d["disponible"] else -6
        s += 1.5 if d.get("top_ventas") else 0
        s += 1.0 * (1 - d["pos"] / total)
        desc = min(d.get("descuento_pct") or 0, 60)
        s += (desc / 10) if oferta else (desc / 60 * 0.8)
        res.append((s, d))
    res.sort(key=lambda x: -x[0])
    return res, correcciones


def _publico(d: dict) -> dict:
    return {k: d[k] for k in ("id", "variante_id", "nombre", "marca", "precio", "precio_anterior", "descuento_pct",
                              "disponible", "stock", "max_por_compra", "envio_gratis", "url", "imagen")} | {"precio_sin_impuestos": d.get("precio_sin_impuestos")} | {"categoria": d["cat_hoja"]}


def _facetas(res: list) -> list[dict]:
    c = Counter(d["cat_hoja"] for _, d in res[:60] if d["cat_hoja"])
    return [{"nombre": k, "cantidad": v} for k, v in c.most_common(5)]


ORDENES = {
    "precio_asc": lambda d: (not d["disponible"], d["precio"] or 0),
    "precio_desc": lambda d: (not d["disponible"], -(d["precio"] or 0)),
    "descuento": lambda d: (not d["disponible"], -(d.get("descuento_pct") or 0)),
    "nombre": lambda d: (not d["disponible"], normalizar(d["nombre"])),
}


def buscar(q: str, limite: int = 8, parcial: bool = False, categoria: str | None = None, desde: int = 0,
                 marcas: list[str] | None = None, ofertas: bool = False, orden: str = "relevancia",
                 completo: bool = False) -> dict:
    """completo=True es para la página de resultados: devuelve todas las categorías y marcas con su cantidad
    (los filtros de la columna izquierda), y acepta filtrar por marcas/ofertas y ordenar.
    La misma búsqueda se contesta de memoria por CACHE_RESULTADOS segundos (se vacía al rearmar el índice)."""
    t0 = time.perf_counter()
    _revisar_reglas()
    q = (q or "").strip()[:120]  # truncar ANTES de armar la clave: si no, una consulta enorme ocupa memoria en el caché
    marcas = (marcas or [])[:20]
    clave = (q, limite, parcial, (categoria or "")[:120], desde, tuple(m[:120] for m in marcas), ofertas, orden, completo)
    hit = _cache_res.get(clave)
    if hit and time.time() - hit[0] < CACHE_RESULTADOS:
        return {**hit[1], "ms": round((time.perf_counter() - t0) * 1000, 1), "cache": True}
    r = _resolver(q, limite, parcial, categoria, desde, marcas, ofertas, orden, completo)
    if len(_cache_res) >= CACHE_MAX:
        _cache_res.clear()
    _cache_res[clave] = (time.time(), r)
    return {**r, "ms": round((time.perf_counter() - t0) * 1000, 1)}


def _resolver(q: str, limite: int, parcial: bool, categoria: str | None, desde: int,
              marcas: list[str] | None, ofertas: bool, orden: str, completo: bool) -> dict:
    t0 = time.perf_counter()
    q = (q or "").strip()[:120]
    inter = interpretar(q)
    titulo = url_todos = None
    cats = _categorias_de(inter["clave"]) if inter["grupos"] else None
    if cats:
        # "bebidas", "limpieza", "cuidado personal": la categoría entera, lo más vendido primero.
        res, corr, via = _listar(cats, inter["oferta"]), {}, "categoria"
        nombre = cats[0] if len(cats) == 1 else " ".join(inter["mostrar"]).capitalize()
        titulo = "Lo más vendido en " + nombre
        url_todos = _estado["urls_cat"].get(cats[0])
    elif not inter["grupos"] and (inter["generica"] or inter["oferta"]):
        # "supermercado online", "coto digital", "ofertas": no nombra un producto.
        res, corr, via = _listar(None, inter["oferta"]), {}, "ofertas" if inter["oferta"] else "destacados"
        titulo = "Las mejores ofertas de Ecomodico" if inter["oferta"] else "Lo más vendido en Ecomodico"
        url_todos = "/shop"
    else:
        res, corr = _buscar(inter["grupos"], parcial, inter["oferta"])
        via = "indice"
        if not res and len(inter["grupos"]) > 1:
            # Ninguno tiene todas las palabras: aceptamos los que tienen la mayoría.
            res, corr = _buscar(inter["grupos"], parcial, inter["oferta"], minimo=max(1, math.ceil(len(inter["grupos"]) / 2)))
            via = "parcial"
    facetas = _facetas(res)
    if completo:
        # En el orden en que aparecen (la categoría más relevante primero), no por cantidad.
        facetas = [{"nombre": k, "cantidad": v} for k, v in Counter(d["cat_hoja"] for _, d in res if d["cat_hoja"]).items()]
    if categoria:
        res = [(s, d) for s, d in res if d["cat_hoja"] == categoria]
    facetas_marca, n_ofertas = [], 0
    if completo:
        facetas_marca = [{"nombre": k, "cantidad": v} for k, v in Counter(d["marca"] for _, d in res if d["marca"]).most_common()]
    if marcas:
        res = [(s, d) for s, d in res if d["marca"] in marcas]
    if completo:
        n_ofertas = sum(1 for _, d in res if (d.get("descuento_pct") or 0) > 0)
    if ofertas:
        res = [(s, d) for s, d in res if (d.get("descuento_pct") or 0) > 0]
    if orden in ORDENES:
        res = sorted(res, key=lambda x: ORDENES[orden](x[1]))
    corregida = None
    if via in ("indice", "parcial"):
        mostrar = [corr.get(raiz(m), None) for m in inter["mostrar"]]
        corregida = " ".join(_estado["superficie"].get(c, c) if c else m for c, m in zip(mostrar, inter["mostrar"]))
        # Si cambió algo más que tildes/plurales, se lo contamos al cliente ("Mostrando resultados de shampoo").
        orig = [raiz(x) for x in normalizar(q).split() if x not in STOP and not _es_oferta(x) and not _es_generica(x)]
        # Sin resultados no hay nada que "mostrar en vez de": la corrección quedaría rara ("regalo dia pares").
        if [raiz(x) for x in normalizar(corregida).split()] == orig or not corregida or not res:
            corregida = None
    if inter["competidor"] is not None and via in ("indice", "parcial", "categoria"):
        corregida = corregida or " ".join(inter["mostrar"])
    todos_en = None
    if via == "indice" and res and not url_todos:
        # "arroz", "leche", "pañales": si lo buscado es el nombre de la categoría del primer resultado, "Ver todos"
        # va a esa categoría de la tienda. La búsqueda de Drubbit pone primero jabones Rexona para "arroz".
        cat = res[0][1]["cat_hoja"]
        buscado = {raiz(x) for x in normalizar(corregida or " ".join(inter["mostrar"])).split()}
        if buscado and buscado <= res[0][1]["_cat_tokens"] and cat in _estado["urls_cat"]:
            url_todos, todos_en = _estado["urls_cat"][cat], cat
    return {
        "consulta": q,
        "corregida": corregida,
        "titulo": titulo,
        "url_todos": url_todos or ("/shop?search=" + (corregida or " ".join(inter["mostrar"]) or q)),
        "todos_en": todos_en,
        "total": len(res),
        "productos": [_publico(d) for _, d in res[max(0, desde):max(0, desde) + max(1, min(limite, 48))]],
        "categorias": facetas,
        "marcas": facetas_marca,
        "ofertas": n_ofertas,
        "via": via,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }

# ---------- fuente de catálogo (de juguete) ----------

class FuenteEjemplo:
    """Reemplazar por la lectura real del catálogo. Devuelve dicts con el contrato del docstring del módulo."""

    def productos(self) -> list[dict]:
        def p(i, nombre, marca, cat, familia, precio, ant=None, top=False, stock=True):
            return dict(id=f"p{i}", variante_id=f"v{i}", nombre=nombre, marca=marca, precio=precio, precio_anterior=ant,
                        descuento_pct=round((1 - precio / ant) * 100) if ant else None, precio_sin_impuestos=round(precio / 1.21, 2),
                        disponible=stock, stock=10 if stock else 0, max_por_compra=None, envio_gratis=False,
                        url=f"/p/{i}", imagen=None, cat_hoja=cat, familia=familia, atributos=[], cat_urls={cat: "/" + cat.lower()},
                        top_ventas=top, pos=i)
        return [
            p(0, "Gaseosa Coca Cola Sabor Original 1,75 Lts", "Coca Cola", "Gaseosa", ["Bebidas"], 3545, 6011, top=True),
            p(1, "Ambientador Glade Coco Spray 360 Ml", "Glade", "Desodorantes De Ambiente", ["Cuidado Del Hogar"], 2100),
            p(2, "Leche Entera La Serenisima 1 Lt", "La Serenisima", "Leche", ["Almacén"], 1800, top=True),
            p(3, "Galletitas Dulces Con Leche Oreo 118 Gr", "Oreo", "Galletitas Dulces", ["Almacén"], 1500),
            p(4, "Sal Fina Celusal Paquete 500 Gr", "Celusal", "Sal", ["Almacén"], 900),
            p(5, "Salsa Lista Pomarola 340 Gr", "La Campagnola", "Pastas Y Salsas", ["Almacén"], 1300),
            p(6, "Shampoo Sin Sal Pantene 300 Ml", "Pantene", "Shampoo Y Acondicionador", ["Cuidado Personal"], 4200),
            p(7, "Cerveza Rubia Stella Artois 473 Cc", "Stella Artois", "Bebidas Con Alcohol", ["Bebidas"], 1900),
            p(8, "Detergente Magistral Limon 750 Ml", "Magistral", "Detergentes", ["Cuidado Del Hogar"], 2300),
        ]


# ---------- chequeo rápido: python buscador.py ----------

if __name__ == "__main__":
    cargar_catalogo(FuenteEjemplo().productos())
    recargar_reglas()
    nombres = lambda q, **k: [x["nombre"] for x in buscar(q, **k)["productos"]]
    assert nombres("cocacola")[0].startswith("Gaseosa Coca Cola"), "palabras pegadas"
    assert not any("Coco" in n for n in nombres("coca")), "coca no es coco"
    assert nombres("lehce")[0].startswith("Leche Entera"), "tipeo"
    assert nombres("sal", parcial=True)[0].startswith("Sal Fina"), "sal primero, no salsa"
    assert buscar("champu")["total"] == 1, "sinónimo inicial champu -> shampoo"
    guardar_regla("sinonimos", "birra", ["cerveza"])          # lo que haría la pantalla de edición
    assert nombres("birra")[0].startswith("Cerveza"), "regla nueva, sin reiniciar"
    guardar_regla("sinonimos", "birra", ["detergente"])        # editar la regla cambia el resultado al instante
    assert nombres("birra")[0].startswith("Detergente"), "regla editada"
    try:
        guardar_regla("categorias", "limpieza", ["No Existe"]); raise SystemExit("debió fallar")
    except ValueError:
        pass
    print("ok")
