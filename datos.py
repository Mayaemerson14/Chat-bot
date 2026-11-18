"""
Manejo de datos: guarda todo en datos.json (movimientos, categorías de cada usuario, saldo)
y hace los cálculos (saldo, totales del mes, gasto por categoría, lectura del monto).
"""
import json
import os
import re
from datetime import date, datetime, timedelta

from predict import normalizar

ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos.json")

# Categorías con las que empieza cada usuario: clave -> (emoji, nombre, tipo)
# tipo: "gasto" (sale de tu plata), "apartado" (ahorro/inversión) o "ingreso" (entra plata)
CATEGORIAS_BASE = {
    "comida": ("🍔", "Comida", "gasto"),
    "transporte": ("🚌", "Transporte", "gasto"),
    "ocio": ("🎬", "Ocio", "gasto"),
    "servicios": ("💡", "Servicios", "gasto"),
    "salud": ("💊", "Salud", "gasto"),
    "educacion": ("📚", "Educación", "gasto"),
    "ropa": ("👕", "Ropa", "gasto"),
    "hogar": ("🏠", "Hogar", "gasto"),
    "deudas": ("💳", "Deudas", "gasto"),
    "inversiones": ("📈", "Inversiones", "apartado"),
    "ahorro": ("🐷", "Ahorro", "apartado"),
    "ingreso": ("💰", "Ingreso", "ingreso"),
    "otros": ("📦", "Otros", "gasto"),
}
PROTEGIDAS = ("ingreso", "otros")  # no se pueden borrar
MAX_CATEGORIAS = 30
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ─── Guardado en archivo ────────────────────────────────
def _cargar() -> dict:
    if os.path.exists(ARCHIVO):
        try:
            with open(ARCHIVO, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            print("⚠️ No pude leer datos.json, empiezo vacío")
    return {}


_DATOS = _cargar()


def guardar():
    with open(ARCHIVO, "w", encoding="utf-8") as f:
        json.dump(_DATOS, f, ensure_ascii=False, indent=2)


def usuario(uid) -> dict:
    u = _DATOS.setdefault(str(uid), {})
    u.setdefault("saldo_inicial", 0)
    u.setdefault("presupuesto", 0)
    u.setdefault("siguiente_id", 1)
    u.setdefault("movimientos", [])
    if "categorias" not in u:  # usuarios nuevos (o de la versión anterior)
        u["categorias"] = {k: {"emoji": e, "nombre": n, "tipo": t, "presupuesto": 0}
                           for k, (e, n, t) in CATEGORIAS_BASE.items()}
    return u


# ─── Categorías ─────────────────────────────────────────
def emoji(u: dict, clave: str) -> str:
    return u["categorias"].get(clave, {}).get("emoji", "🏷️")


def nombre(u: dict, clave: str) -> str:
    return u["categorias"].get(clave, {}).get("nombre", clave.capitalize())


def tipo(u: dict, clave: str) -> str:
    return u["categorias"].get(clave, {}).get("tipo", "gasto")


def claves_de_tipo(u: dict, t: str) -> list:
    return [k for k, c in u["categorias"].items() if c["tipo"] == t]


def buscar_categoria(u: dict, texto: str):
    """Encuentra una categoría por su nombre (sin importar tildes o mayúsculas)."""
    n = normalizar(texto)
    for clave, c in u["categorias"].items():
        if n and (n == normalizar(c["nombre"]) or n == clave):
            return clave
    return None


def crear_categoria(uid, nombre_cat: str, emoji_cat: str = "🏷️", presupuesto: int = 0):
    """Devuelve (clave, None) si se creó, o (None, 'motivo del error')."""
    u = usuario(uid)
    nombre_cat = nombre_cat.strip()[:30]
    clave = re.sub(r"\s+", "_", normalizar(nombre_cat))[:20]
    if not clave:
        return None, "El nombre no es válido."
    if buscar_categoria(u, nombre_cat) or clave in u["categorias"]:
        return None, "Ya tienes una categoría con ese nombre."
    if len(u["categorias"]) >= MAX_CATEGORIAS:
        return None, f"Llegaste al máximo de {MAX_CATEGORIAS} categorías."
    u["categorias"][clave] = {"emoji": emoji_cat, "nombre": nombre_cat,
                              "tipo": "gasto", "presupuesto": presupuesto}
    guardar()
    return clave, None


def borrar_categoria(uid, clave: str):
    """Borra la categoría; sus movimientos pasan a 'Otros'. Devuelve cuántos movimientos pasaron."""
    u = usuario(uid)
    if clave in PROTEGIDAS or clave not in u["categorias"]:
        return None
    movidos = 0
    for m in u["movimientos"]:
        if m["categoria"] == clave:
            m["categoria"] = "otros"
            movidos += 1
    del u["categorias"][clave]
    guardar()
    return movidos


def fijar_presupuesto_categoria(u: dict, clave: str, monto: int):
    u["categorias"][clave]["presupuesto"] = monto
    guardar()


# ─── Movimientos ────────────────────────────────────────
def agregar_movimiento(uid, monto: int, categoria: str, descripcion: str) -> dict:
    u = usuario(uid)
    mov = {"id": u["siguiente_id"], "fecha": datetime.now().isoformat(timespec="seconds"),
           "monto": monto, "categoria": categoria, "descripcion": descripcion}
    u["siguiente_id"] += 1
    u["movimientos"].append(mov)
    guardar()
    return mov


def buscar_movimiento(uid, mov_id: int):
    for m in usuario(uid)["movimientos"]:
        if m["id"] == mov_id:
            return m
    return None


def borrar_movimiento(uid, mov_id: int) -> bool:
    u = usuario(uid)
    antes = len(u["movimientos"])
    u["movimientos"] = [m for m in u["movimientos"] if m["id"] != mov_id]
    guardar()
    return len(u["movimientos"]) < antes


def cambiar_categoria(uid, mov_id: int, categoria: str):
    mov = buscar_movimiento(uid, mov_id)
    if mov:
        mov["categoria"] = categoria
        guardar()
    return mov


# ─── Cálculos ───────────────────────────────────────────
def saldo_actual(u: dict) -> int:
    saldo = u["saldo_inicial"]
    for m in u["movimientos"]:
        saldo += m["monto"] if tipo(u, m["categoria"]) == "ingreso" else -m["monto"]
    return saldo


def ajustar_saldo(u: dict, deseado: int):
    """Hace que el saldo disponible sea exactamente 'deseado' y limpia el historial anterior."""
    u["saldo_inicial"] = deseado
    u["movimientos"] = []  # Borra los movimientos anteriores para empezar desde cero
    guardar()


def _totales(u: dict, movimientos: list) -> dict:
    por_cat = {}
    for m in movimientos:
        por_cat[m["categoria"]] = por_cat.get(m["categoria"], 0) + m["monto"]
    suma = lambda t: sum(v for k, v in por_cat.items() if tipo(u, k) == t)
    return {"por_categoria": por_cat, "ingresos": suma("ingreso"),
            "gastos": suma("gasto"), "apartado": suma("apartado")}


def totales_mes(u: dict, mes: str) -> dict:
    """mes con formato '2026-11'."""
    return _totales(u, [m for m in u["movimientos"] if m["fecha"].startswith(mes)])


def movimientos_entre(u: dict, desde: date, hasta: date) -> list:
    """Movimientos con fecha entre 'desde' y 'hasta' (ambos incluidos)."""
    a, b = desde.isoformat(), hasta.isoformat()
    return [m for m in u["movimientos"] if a <= m["fecha"][:10] <= b]


def totales_periodo(u: dict, desde: date, hasta: date) -> dict:
    return _totales(u, movimientos_entre(u, desde, hasta))


def gastos_por_dia(u: dict, desde: date, hasta: date) -> dict:
    """{'2026-11-18': {'comida': 25000, ...}, ...} solo con gastos (no ingresos ni ahorro)."""
    por_dia = {}
    for m in movimientos_entre(u, desde, hasta):
        if tipo(u, m["categoria"]) == "gasto":
            dia = por_dia.setdefault(m["fecha"][:10], {})
            dia[m["categoria"]] = dia.get(m["categoria"], 0) + m["monto"]
    return por_dia


def gasto_categoria(u: dict, clave: str, mes: str) -> int:
    return totales_mes(u, mes)["por_categoria"].get(clave, 0)


def mes_anterior(mes: str) -> str:
    anio, num = map(int, mes.split("-"))
    num -= 1
    if num == 0:
        anio, num = anio - 1, 12
    return f"{anio}-{num:02d}"


# ─── Lectura de texto ───────────────────────────────────
_PATRON = re.compile(r"\$?\s*(\d+(?:[.,]\d+)*)\s*(millones|millon|mil|k|lucas|luca)?\b", re.I)
_SALDO = re.compile(r"\b(tengo|base inicial|saldo inicial|mi saldo|saldo actual|saldo es|"
                    r"inicio con|arranco con|empiezo con|comienzo con|cuento con)\b")


def parece_saldo(texto: str) -> bool:
    """True si el mensaje dice cuánto dinero tiene la persona ('tengo 1 millon')."""
    return bool(_SALDO.search(normalizar(texto)))


def extraer_monto(texto: str):
    """
    'pizza 25.000' -> (25000, 'pizza') | 'taxi 8k' -> (8000, 'taxi')
    'sueldo 2 millones' -> (2000000, 'sueldo'). Devuelve None si no hay monto.
    """
    m = _PATRON.search(texto)
    if not m:
        return None
    num, unidad = m.group(1), (m.group(2) or "").lower()
    try:
        if unidad in ("millones", "millon"):
            valor = float(num.replace(",", ".")) * 1_000_000
        elif unidad:
            valor = float(num.replace(",", ".")) * 1_000
        else:
            valor = float(num.replace(".", "").replace(",", ""))
    except ValueError:
        return None
    valor = int(round(valor))
    if valor <= 0:
        return None
    descripcion = (texto[:m.start()] + " " + texto[m.end():]).replace("$", " ")
    return valor, re.sub(r"\s+", " ", descripcion).strip()