"""
Manejo de datos: guarda los movimientos de cada usuario en datos.json
y hace los cálculos (saldo, totales del mes, lectura del monto).
"""
import json
import os
import re
from datetime import datetime

ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos.json")

EMOJIS = {
    "comida": "🍔", "transporte": "🚌", "ocio": "🎬", "servicios": "💡",
    "salud": "💊", "educacion": "📚", "ropa": "👕", "hogar": "🏠",
    "deudas": "💳", "inversiones": "📈", "ahorro": "🐷", "ingreso": "💰",
    "otros": "📦",
}
NOMBRES = {
    "comida": "Comida", "transporte": "Transporte", "ocio": "Ocio",
    "servicios": "Servicios", "salud": "Salud", "educacion": "Educación",
    "ropa": "Ropa", "hogar": "Hogar", "deudas": "Deudas",
    "inversiones": "Inversiones", "ahorro": "Ahorro", "ingreso": "Ingreso",
    "otros": "Otros",
}
APARTADO = ("ahorro", "inversiones")  # plata que sale del saldo pero no es un gasto
GASTOS = [c for c in NOMBRES if c not in ("ingreso",) + APARTADO]
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
    clave = str(uid)
    if clave not in _DATOS:
        _DATOS[clave] = {"saldo_inicial": 0, "presupuesto": 0,
                         "siguiente_id": 1, "movimientos": []}
    return _DATOS[clave]


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
        saldo += m["monto"] if m["categoria"] == "ingreso" else -m["monto"]
    return saldo


def ajustar_saldo(u: dict, deseado: int):
    """Hace que el saldo actual sea exactamente 'deseado'."""
    u["saldo_inicial"] += deseado - saldo_actual(u)
    guardar()


def totales_mes(u: dict, mes: str) -> dict:
    """mes con formato '2026-11'."""
    por_cat = {}
    for m in u["movimientos"]:
        if m["fecha"].startswith(mes):
            por_cat[m["categoria"]] = por_cat.get(m["categoria"], 0) + m["monto"]
    return {
        "por_categoria": por_cat,
        "ingresos": por_cat.get("ingreso", 0),
        "gastos": sum(por_cat.get(c, 0) for c in GASTOS),
        "apartado": sum(por_cat.get(c, 0) for c in APARTADO),
    }


# ─── Lectura del monto en un texto ──────────────────────
_PATRON = re.compile(r"\$?\s*(\d+(?:[.,]\d+)*)\s*(millones|millon|mil|k|lucas|luca)?\b", re.I)


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