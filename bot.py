import csv
import html
import io
import os
import re
from datetime import date, datetime, timedelta

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import datos
import graficos
from predict import aprender, normalizar, predecir_categoria

# ─── Configuración ──────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    print("❌ Falta la variable TELEGRAM_TOKEN")
    print('   En PowerShell: $env:TELEGRAM_TOKEN="tu_token_aqui"')
    exit(1)

UMBRAL = 0.25  # por debajo de esta confianza el bot deja el gasto en "Otros" y avisa
HTML = ParseMode.HTML

AYUDA = (
    "💰 <b>Bot de finanzas personales</b>\n\n"
    "<b>1) Dime cuánto dinero tienes</b>\n"
    "• <code>tengo 1.000.000</code>  (o /saldo 1000000)\n\n"
    "<b>2) Anota cada gasto o ingreso</b> con su valor:\n"
    "• <code>pizza 25000</code> · <code>uber 12k</code> · <code>sueldo 2 millones</code>\n"
    "Detecto la categoría con machine learning 🤖. Si me equivoco, la cambias con un botón y aprendo.\n"
    "Si un gasto es mayor ta tu saldo disponible, no lo registro: te dejo agregar dinero o cancelarlo.\n\n"
    "<b>3) Tus categorías</b>\n"
    "/categorias — ver todas, con lo gastado\n"
    "/nuevacategoria Regalo hijo 200000 — crea una categoría (el monto es lo que le asignas, opcional)\n"
    "/borrarcategoria — borrar una categoría\n"
    "/presupuesto regalo hijo 300000 — cambiar el monto asignado a una categoría\n"
    "/presupuesto 800000 — límite de gasto total del mes\n\n"
    "<b>4) Reportes</b>\n"
    "/saldo — saldo disponible\n"
    "/resumen — resumen del mes\n"
    "/grafica — gráficas por hoy, ayer, 7 días, mes o día por día (o una fecha: /grafica 15/11)\n"
    "/comparar — este mes vs. el mes pasado\n"
    "/ultimos — últimos movimientos\n"
    "/borrar — elimina el último movimiento\n"
    "/exportar — descarga todo en CSV (Excel)"
)


# ─── Funciones auxiliares ───────────────────────────────
def esc(texto: str) -> str:
    return html.escape(str(texto))


def pesos(n) -> str:
    return ("-" if n < 0 else "") + "$" + f"{abs(int(round(n))):,}".replace(",", ".")


def mes_actual() -> str:
    return datetime.now().strftime("%Y-%m")


def nombre_mes(mes: str) -> str:
    anio, num = mes.split("-")
    return f"{datos.MESES[int(num) - 1]} {anio}"


def barra(valor: float, total: float, largo: int = 10) -> str:
    proporcion = min(valor / total, 1) if total else 0
    bloques = round(proporcion * largo)
    return "█" * bloques + "░" * (largo - bloques)


def nombres_categorias(u: dict) -> dict:
    return {k: c["nombre"] for k, c in u["categorias"].items()}


def categoria_por_nombre(u: dict, descripcion: str):
    """Si la descripción contiene el nombre de una categoría, se usa esa directamente."""
    d = f" {normalizar(descripcion)} "
    for clave, c in sorted(u["categorias"].items(), key=lambda x: -len(x[1]["nombre"])):
        n = normalizar(c["nombre"])
        if n and f" {n} " in d:
            return clave
    return None


def alerta_presupuesto(u: dict) -> str:
    limite = u["presupuesto"]
    if not limite:
        return ""
    gastos = datos.totales_mes(u, mes_actual())["gastos"]
    if gastos >= limite:
        return f"🚨 ¡Superaste tu presupuesto del mes! Llevas {pesos(gastos)} de {pesos(limite)}."
    if gastos >= limite * 0.8:
        return f"⚠️ Ya gastaste el {gastos / limite:.0%} de tu presupuesto del mes ({pesos(gastos)} de {pesos(limite)})."
    return ""


def notas_presupuesto(u: dict, cat: str) -> str:
    """Avisos sobre el monto asignado a la categoría y el presupuesto general."""
    if datos.tipo(u, cat) != "gasto":
        return ""
    partes = []
    limite = u["categorias"][cat]["presupuesto"]
    if limite:
        gastado = datos.gasto_categoria(u, cat, mes_actual())
        nombre = esc(datos.nombre(u, cat))
        if gastado > limite:
            partes.append(f"🚨 Superaste lo asignado a {nombre}: {pesos(gastado)} de {pesos(limite)}.")
        elif gastado >= limite * 0.8:
            partes.append(f"⚠️ Ya usaste el {gastado / limite:.0%} de {nombre}. Te quedan {pesos(limite - gastado)}.")
        else:
            partes.append(f"🎯 {nombre}: te quedan {pesos(limite - gastado)} de {pesos(limite)}.")
    general = alerta_presupuesto(u)
    if general:
        partes.append(general)
    return "\n".join(partes)


def formato_movimiento(uid, mov: dict, nota: str = "") -> str:
    u = datos.usuario(uid)
    cat = mov["categoria"]
    titulo = {"ingreso": "💰 Ingreso registrado",
              "apartado": "📈 Ahorro/inversión registrado"}.get(datos.tipo(u, cat), "✅ Gasto registrado")
    lineas = [f"<b>{titulo}</b>",
              f"{datos.emoji(u, cat)} {esc(datos.nombre(u, cat))} — <b>{pesos(mov['monto'])}</b>"]
    if mov["descripcion"]:
        lineas.append(f"📝 {esc(mov['descripcion'])}")
    lineas.append(f"💵 Saldo disponible: <b>{pesos(datos.saldo_actual(u))}</b>")
    lineas.append(f"📉 Gastado este mes: {pesos(datos.totales_mes(u, mes_actual())['gastos'])}")
    if nota:
        lineas.append(f"\n{nota}")
    return "\n".join(lineas)


def botones_movimiento(mov_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Cambiar categoría", callback_data=f"cambiar:{mov_id}"),
        InlineKeyboardButton("🗑️ Deshacer", callback_data=f"borrar:{mov_id}"),
    ]])


def botones_categorias(u: dict, mov_id: int) -> InlineKeyboardMarkup:
    botones = [InlineKeyboardButton(f"{c['emoji']} {c['nombre']}", callback_data=f"cat:{mov_id}:{k}")
               for k, c in u["categorias"].items()]
    return InlineKeyboardMarkup([botones[i:i + 2] for i in range(0, len(botones), 2)])


# ─── Registro de movimientos (mensajes normales) ────────
def teclado_falta(faltante: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"➕ Agregar {pesos(faltante)} y registrar", callback_data="falta:ok")],
        [InlineKeyboardButton("✏️ Agregar otro valor", callback_data="falta:otro")],
        [InlineKeyboardButton("❌ Cancelar gasto", callback_data="falta:no")],
    ])


async def avisar_falta(message, u: dict, pend: dict):
    """El gasto es mayor al saldo: no se registra hasta que la persona decida."""
    saldo = datos.saldo_actual(u)
    faltante = pend["monto"] - saldo
    await message.reply_text(
        "⚠️ <b>No tienes suficiente dinero disponible</b>\n\n"
        f"💵 Disponible: {pesos(saldo)}\n"
        f"🛒 Quieres gastar: {pesos(pend['monto'])}\n"
        f"❌ Te faltan: <b>{pesos(faltante)}</b>\n\n"
        "Todavía no registré este gasto. ¿Qué quieres hacer?",
        parse_mode=HTML, reply_markup=teclado_falta(faltante))


async def guardar_gasto(message, uid, pend: dict):
    u = datos.usuario(uid)
    mov = datos.agregar_movimiento(uid, pend["monto"], pend["categoria"], pend["descripcion"])
    nota = pend["nota"]
    avisos = notas_presupuesto(u, pend["categoria"])
    if avisos:
        nota += "\n\n" + avisos
    await message.reply_text(formato_movimiento(uid, mov, nota), parse_mode=HTML,
                             reply_markup=botones_movimiento(mov["id"]))


async def agregar_dinero(message, uid, ud: dict, monto: int):
    """Suma dinero al saldo (como un ingreso) y, si había un gasto pendiente, intenta registrarlo."""
    u = datos.usuario(uid)
    datos.agregar_movimiento(uid, monto, "ingreso", "Dinero agregado")
    await message.reply_text(
        f"💰 Agregué <b>{pesos(monto)}</b>. Saldo disponible: <b>{pesos(datos.saldo_actual(u))}</b>",
        parse_mode=HTML)
    pend = ud.get("pendiente")
    if pend:
        if pend["monto"] <= datos.saldo_actual(u):
            ud.pop("pendiente")
            await guardar_gasto(message, uid, pend)
        else:
            await avisar_falta(message, u, pend)


async def registrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = datos.usuario(uid)
    ud = context.user_data
    texto = update.message.text
    resultado = datos.extraer_monto(texto)

    # La persona está escribiendo cuánto dinero quiere agregar
    if ud.get("esperando_ingreso"):
        if normalizar(texto) in ("cancelar", "no", "cancelar gasto"):
            ud.pop("esperando_ingreso")
            ud.pop("pendiente", None)
            await update.message.reply_text("Listo, no registré ese gasto 👍")
            return
        if not resultado:
            await update.message.reply_text("Escribe solo el valor que quieres agregar, por ejemplo: 100000 (o escribe cancelar).")
            return
        ud.pop("esperando_ingreso")
        if not datos.parece_saldo(texto):
            await agregar_dinero(update.message, uid, ud, resultado[0])
            return

    if not resultado:
        await update.message.reply_text(
            "No encontré un monto 🤔\nEscríbelo así: <code>pizza 25000</code>\n"
            "Para decirme cuánto dinero tienes: <code>tengo 1.000.000</code>\n(usa /ayuda para ver más).",
            parse_mode=HTML)
        return
    monto, descripcion = resultado

    # "tengo 1.000.000" / "base inicial 1 millon" -> fija el saldo y limpia el historial anterior
    if datos.parece_saldo(texto):
        ud.pop("pendiente", None)
        ud.pop("esperando_ingreso", None)
        datos.ajustar_saldo(u, monto)
        await update.message.reply_text(
            f"✅ Listo, saldo actualizado a <b>{pesos(monto)}</b>.\n"
            "He limpiado el historial anterior, por lo que tus resúmenes y gráficas comenzarán a contar desde este nuevo punto.",
            parse_mode=HTML)
        return

    categoria = categoria_por_nombre(u, descripcion) if descripcion else None
    if categoria:
        confianza = 1.0
    elif descripcion:
        categoria, confianza = predecir_categoria(uid, descripcion, nombres_categorias(u))
    else:
        categoria, confianza = "otros", 0.0

    if confianza < UMBRAL:
        categoria = "otros"
        nota = "🤔 No estoy seguro de la categoría. Toca «Cambiar categoría» y aprenderé de tu respuesta."
    else:
        nota = f"🤖 Confianza del modelo: {confianza:.0%}" if confianza < 1 else "🏷️ Categoría reconocida por su nombre"

    pend = {"monto": monto, "categoria": categoria, "descripcion": descripcion, "nota": nota}

    # Todo lo que no sea ingreso sale de tu plata: no puede ser mayor al saldo disponible
    if datos.tipo(u, categoria) != "ingreso" and monto > datos.saldo_actual(u):
        ud["pendiente"] = pend
        await avisar_falta(update.message, u, pend)
        return

    ud.pop("pendiente", None)
    await guardar_gasto(update.message, uid, pend)


# ─── Botones ────────────────────────────────────────────
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    u = datos.usuario(uid)
    await query.answer()
    partes = query.data.split(":")
    accion = partes[0]

    # --- no alcanza el dinero: agregar lo que falta, agregar otro valor o cancelar ---
    if accion == "falta":
        ud = context.user_data
        pend = ud.get("pendiente")
        if not pend:
            await query.edit_message_text("Ese gasto ya no está pendiente.")
            return
        if partes[1] == "no":
            ud.pop("pendiente", None)
            ud.pop("esperando_ingreso", None)
            await query.edit_message_text("❌ Listo, no registré ese gasto.")
        elif partes[1] == "otro":
            ud["esperando_ingreso"] = True
            await query.edit_message_text(
                "✏️ Escribe cuánto dinero quieres agregar, por ejemplo: <code>100000</code>\n(o escribe cancelar).",
                parse_mode=HTML)
        elif partes[1] == "ok":
            faltante = max(0, pend["monto"] - datos.saldo_actual(u))
            if faltante:
                datos.agregar_movimiento(uid, faltante, "ingreso", "Dinero agregado")
            ud.pop("pendiente", None)
            ud.pop("esperando_ingreso", None)
            await query.edit_message_text(f"💰 Agregué {pesos(faltante)} a tu saldo disponible.")
            await guardar_gasto(query.message, uid, pend)
        return

    # --- borrar categorías (confirmación) ---
    if accion == "askdel":
        clave = partes[1]
        if clave not in u["categorias"]:
            await query.edit_message_text("Esa categoría ya no existe.")
            return
        n = sum(1 for m in u["movimientos"] if m["categoria"] == clave)
        await query.edit_message_text(
            f"¿Borrar la categoría {u['categorias'][clave]['emoji']} <b>{esc(datos.nombre(u, clave))}</b>?\n"
            f"{'Su movimiento pasará' if n == 1 else f'Sus {n} movimientos pasarán'} a «Otros» (no se pierde ningún gasto).",
            parse_mode=HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Sí, borrar", callback_data=f"delcat:{clave}"),
                InlineKeyboardButton("↩️ No", callback_data="nodel")]]))
        return
    if accion == "delcat":
        nombre = datos.nombre(u, partes[1])
        movidos = datos.borrar_categoria(uid, partes[1])
        if movidos is None:
            await query.edit_message_text("No se pudo borrar esa categoría.")
        else:
            await query.edit_message_text(
                f"🗑️ Categoría «{esc(nombre)}» eliminada. {movidos} {'movimiento pasó' if movidos == 1 else 'movimientos pasaron'} a Otros.", parse_mode=HTML)
        return
    if accion == "nodel":
        await query.edit_message_text("Listo, no borré nada 👍")
        return

    # --- gráficas por período ---
    if accion == "graf":
        await enviar_grafica(query.message, uid, partes[1])
        return

    # --- movimientos ---
    mov_id = int(partes[1])
    mov = datos.buscar_movimiento(uid, mov_id)
    if not mov:
        await query.edit_message_text("Ese movimiento ya no existe.")
        return

    if accion == "cambiar":
        await query.edit_message_reply_markup(botones_categorias(u, mov_id))

    elif accion == "cat":
        nueva = partes[2]
        if nueva not in u["categorias"]:
            await query.edit_message_text("Esa categoría ya no existe.")
            return
        datos.cambiar_categoria(uid, mov_id, nueva)
        if mov["descripcion"]:
            aprender(uid, mov["descripcion"], nueva)  # el modelo aprende de la corrección
        nota = "🧠 Anotado, aprendí de tu corrección."
        avisos = notas_presupuesto(u, nueva)
        if avisos:
            nota += "\n\n" + avisos
        await query.edit_message_text(formato_movimiento(uid, mov, nota), parse_mode=HTML,
                                      reply_markup=botones_movimiento(mov_id))

    elif accion == "borrar":
        datos.borrar_movimiento(uid, mov_id)
        await query.edit_message_text(
            f"🗑️ Movimiento eliminado. Saldo disponible: {pesos(datos.saldo_actual(u))}")


# ─── Comandos ───────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu asistente de finanzas.\n\n"
        "1️⃣ Dime cuánto dinero tienes, por ejemplo: <code>tengo 1.000.000</code>\n"
        "2️⃣ Anota cada gasto: <code>almuerzo 15000</code>\n"
        "3️⃣ Crea tus propias categorías con /nuevacategoria\n\n"
        "Usa /ayuda para ver todo lo que puedo hacer.", parse_mode=HTML)


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(AYUDA, parse_mode=HTML)


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    if context.args:
        resultado = datos.extraer_monto(" ".join(context.args))
        if not resultado:
            await update.message.reply_text("Escribe un valor válido, por ejemplo: /saldo 500000")
            return
        datos.ajustar_saldo(u, resultado[0])
        await update.message.reply_text(f"✅ Listo, tu saldo disponible es <b>{pesos(resultado[0])}</b> y se limpió el historial anterior.", parse_mode=HTML)
    else:
        t = datos.totales_mes(u, mes_actual())
        await update.message.reply_text(
            f"💵 Saldo disponible: <b>{pesos(datos.saldo_actual(u))}</b>\n"
            f"📉 Gastado este mes: {pesos(t['gastos'])}", parse_mode=HTML)


async def cmd_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    mes = mes_actual()
    por_cat = datos.totales_mes(u, mes)["por_categoria"]
    lineas = [f"🗂️ <b>Tus categorías</b> ({nombre_mes(mes)})", ""]
    for clave, c in u["categorias"].items():
        gastado = por_cat.get(clave, 0)
        linea = f"{c['emoji']} {esc(c['nombre'])}: {pesos(gastado)}"
        if c["presupuesto"] and c["tipo"] == "gasto":
            linea += f" de {pesos(c['presupuesto'])}\n   {barra(gastado, c['presupuesto'])} {gastado / c['presupuesto']:.0%}"
        lineas.append(linea)
    lineas += ["", "➕ /nuevacategoria Regalo hijo 200000", "🗑️ /borrarcategoria", "🎯 /presupuesto regalo hijo 300000"]
    await update.message.reply_text("\n".join(lineas), parse_mode=HTML)


async def cmd_nuevacategoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    texto = " ".join(context.args).strip()
    if not texto:
        await update.message.reply_text(
            "Para crear una categoría escribe su nombre y, si quieres, cuánto dinero le asignas:\n"
            "<code>/nuevacategoria Regalo hijo 200000</code>\n"
            "También puedes ponerle un emoji al inicio: <code>/nuevacategoria 🎁 Regalo hijo 200000</code>",
            parse_mode=HTML)
        return

    emoji = "🏷️"
    if not texto[0].isalnum():  # empieza con un emoji
        emoji, _, texto = texto.partition(" ")
        texto = texto.strip()
    presupuesto = 0
    resultado = datos.extraer_monto(texto)
    if resultado:
        presupuesto, texto = resultado

    clave, error = datos.crear_categoria(uid, texto, emoji, presupuesto)
    if error:
        await update.message.reply_text(f"⚠️ {error}")
        return
    u = datos.usuario(uid)
    extra = f" con <b>{pesos(presupuesto)}</b> asignados" if presupuesto else ""
    await update.message.reply_text(
        f"✅ Categoría {emoji} <b>{esc(datos.nombre(u, clave))}</b> creada{extra}.\n"
        f"Para usarla escribe su nombre en el gasto, por ejemplo: <code>{esc(datos.nombre(u, clave).lower())} 50000</code>",
        parse_mode=HTML)


async def cmd_borrarcategoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    texto = " ".join(context.args).strip()
    if texto:
        clave = datos.buscar_categoria(u, texto)
        if not clave:
            await update.message.reply_text("No encuentro esa categoría 🤔 Mira los nombres en /categorias")
            return
        if clave in datos.PROTEGIDAS:
            await update.message.reply_text(f"«{datos.nombre(u, clave)}» es una categoría básica y no se puede borrar.")
            return
        claves = [clave]
    else:
        claves = [k for k in u["categorias"] if k not in datos.PROTEGIDAS]
    botones_borrar = [InlineKeyboardButton(f"{u['categorias'][k]['emoji']} {u['categorias'][k]['nombre']}",
                                           callback_data=f"askdel:{k}") for k in claves]
    filas = [botones_borrar[i:i + 2] for i in range(0, len(botones_borrar), 2)]
    await update.message.reply_text("🗑️ ¿Qué categoría quieres borrar?", reply_markup=InlineKeyboardMarkup(filas))


async def cmd_presupuesto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    texto = " ".join(context.args).strip()

    if not texto:  # ver estado
        mes = mes_actual()
        t = datos.totales_mes(u, mes)
        lineas = ["🎯 <b>Presupuestos</b>", ""]
        if u["presupuesto"]:
            lineas.append(f"Total del mes: {pesos(t['gastos'])} de {pesos(u['presupuesto'])} ({t['gastos'] / u['presupuesto']:.0%})")
        else:
            lineas.append("Total del mes: sin límite. Fíjalo con /presupuesto 800000")
        con_monto = [(k, c) for k, c in u["categorias"].items() if c["presupuesto"] and c["tipo"] == "gasto"]
        for k, c in con_monto:
            g = t["por_categoria"].get(k, 0)
            lineas.append(f"{c['emoji']} {esc(c['nombre'])}: {pesos(g)} de {pesos(c['presupuesto'])} (te quedan {pesos(c['presupuesto'] - g)})")
        if not con_monto:
            lineas.append("Ninguna categoría tiene monto asignado. Ej: /presupuesto regalo hijo 300000")
        await update.message.reply_text("\n".join(lineas), parse_mode=HTML)
        return

    resultado = datos.extraer_monto(texto)
    if not resultado:
        await update.message.reply_text("Escribe un valor, por ejemplo: /presupuesto 800000 o /presupuesto regalo hijo 300000")
        return
    monto, nombre_cat = resultado
    if nombre_cat:  # presupuesto de una categoría
        clave = datos.buscar_categoria(u, nombre_cat)
        if not clave or datos.tipo(u, clave) != "gasto":
            await update.message.reply_text("No encuentro esa categoría de gasto 🤔 Mira los nombres en /categorias")
            return
        datos.fijar_presupuesto_categoria(u, clave, monto)
        await update.message.reply_text(
            f"🎯 Ahora {datos.emoji(u, clave)} <b>{esc(datos.nombre(u, clave))}</b> tiene <b>{pesos(monto)}</b> asignados.", parse_mode=HTML)
    else:  # presupuesto general
        u["presupuesto"] = monto
        datos.guardar()
        await update.message.reply_text(f"🎯 Presupuesto mensual total fijado en <b>{pesos(monto)}</b>.", parse_mode=HTML)


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    mes = mes_actual()
    t = datos.totales_mes(u, mes)
    if not t["por_categoria"]:
        await update.message.reply_text("Aún no tienes movimientos este mes. Escribe, por ejemplo: pizza 25000")
        return

    lineas = [f"📊 <b>Resumen de {nombre_mes(mes)}</b>", "",
              f"💵 Saldo disponible: <b>{pesos(datos.saldo_actual(u))}</b>",
              f"💰 Ingresos del mes: {pesos(t['ingresos'])}",
              f"💸 Gastado: <b>{pesos(t['gastos'])}</b>",
              f"📈 Ahorro e inversión: {pesos(t['apartado'])}", "", "<b>Por categoría</b>"]
    egresos = {c: v for c, v in t["por_categoria"].items() if datos.tipo(u, c) != "ingreso"}
    total_egresos = sum(egresos.values())
    for cat, valor in sorted(egresos.items(), key=lambda x: x[1], reverse=True):
        limite = u["categorias"][cat]["presupuesto"] if cat in u["categorias"] else 0
        if limite and datos.tipo(u, cat) == "gasto":
            lineas.append(f"{datos.emoji(u, cat)} {esc(datos.nombre(u, cat))}: {pesos(valor)} de {pesos(limite)}\n   {barra(valor, limite)} {valor / limite:.0%}")
        else:
            lineas.append(f"{datos.emoji(u, cat)} {esc(datos.nombre(u, cat))}: {pesos(valor)}\n   {barra(valor, total_egresos)} {valor / total_egresos:.0%}")
    if u["presupuesto"]:
        lineas += ["", f"🎯 Presupuesto total: {pesos(t['gastos'])} de {pesos(u['presupuesto'])} ({t['gastos'] / u['presupuesto']:.0%})"]
    await update.message.reply_text("\n".join(lineas), parse_mode=HTML)


async def cmd_ultimos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    if not u["movimientos"]:
        await update.message.reply_text("Aún no tienes movimientos.")
        return
    lineas = ["🧾 <b>Últimos movimientos</b>", ""]
    for m in reversed(u["movimientos"][-10:]):
        fecha = datetime.fromisoformat(m["fecha"]).strftime("%d/%m")
        signo = "+" if datos.tipo(u, m["categoria"]) == "ingreso" else "-"
        desc = esc(m["descripcion"]) or "(sin descripción)"
        lineas.append(f"{fecha} {datos.emoji(u, m['categoria'])} {desc}: {signo}{pesos(m['monto'])}")
    await update.message.reply_text("\n".join(lineas), parse_mode=HTML)


async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = datos.usuario(uid)
    if not u["movimientos"]:
        await update.message.reply_text("No hay nada para borrar.")
        return
    ultimo = u["movimientos"][-1]
    datos.borrar_movimiento(uid, ultimo["id"])
    await update.message.reply_text(
        f"🗑️ Eliminé: {esc(ultimo['descripcion']) or 'movimiento'} ({pesos(ultimo['monto'])})\n"
        f"💵 Saldo disponible: {pesos(datos.saldo_actual(u))}", parse_mode=HTML)


def fecha_larga(d: date) -> str:
    return f"{d.day} de {datos.MESES[d.month - 1]}"


def teclado_graficas() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Hoy", callback_data="graf:hoy"),
         InlineKeyboardButton("⏪ Ayer", callback_data="graf:ayer"),
         InlineKeyboardButton("🗓️ 7 días", callback_data="graf:semana")],
        [InlineKeyboardButton("📆 Este mes", callback_data="graf:mes"),
         InlineKeyboardButton("📊 Día por día", callback_data="graf:dias")],
    ])


def interpretar_periodo(texto: str):
    """Devuelve (periodo, fecha) o None si no se entiende. Sin texto = el mes actual."""
    t = normalizar(texto)
    if t in ("", "mes", "este mes"):
        return "mes", None
    if t == "hoy":
        return "hoy", None
    if t == "ayer":
        return "ayer", None
    if t in ("semana", "esta semana", "7", "7 dias", "ultimos 7 dias"):
        return "semana", None
    if t in ("dia", "dias", "diario", "por dia", "dia por dia"):
        return "dias", None
    crudo = texto.strip()
    try:
        m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?", crudo)
        if m:
            anio = int(m.group(3)) if m.group(3) else date.today().year
            return "fecha", date(anio + 2000 if anio < 100 else anio, int(m.group(2)), int(m.group(1)))
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", crudo)
        if m:
            return "fecha", date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:  # fecha imposible, como 31/02
        return None
    return None


def detalle_dia(u: dict, dia: date):
    """Lista de movimientos de un día (para acompañar la gráfica)."""
    movs = datos.movimientos_entre(u, dia, dia)
    if not movs:
        return None
    lineas = [f"🧾 <b>Movimientos del {dia.day}/{dia.month}</b>"]
    for m in movs:
        signo = "+" if datos.tipo(u, m["categoria"]) == "ingreso" else "-"
        desc = esc(m["descripcion"]) or esc(datos.nombre(u, m["categoria"]))
        linea = f"{datos.emoji(u, m['categoria'])} {desc}: {signo}{pesos(m['monto'])}"
        if len("\n".join(lineas + [linea])) > 850:  # el pie de foto de Telegram tiene límite
            lineas.append("…y más movimientos (míralos en /ultimos)")
            break
        lineas.append(linea)
    return "\n".join(lineas)


async def enviar_grafica(message, uid, periodo: str, fecha=None):
    u = datos.usuario(uid)
    hoy = date.today()
    pie = None

    if periodo in ("hoy", "ayer", "fecha"):
        dia = hoy if periodo == "hoy" else (hoy - timedelta(days=1) if periodo == "ayer" else fecha)
        if dia > hoy:
            await message.reply_text("Esa fecha todavía no llega 😅", reply_markup=teclado_graficas())
            return
        nombre = {"hoy": "hoy", "ayer": "ayer"}.get(periodo, f"el {dia.day}/{dia.month}/{dia.year}")
        imagen = graficos.dashboard_periodo(u, dia, dia, f"Resumen de {nombre} ({fecha_larga(dia)})")
        pie = detalle_dia(u, dia)
        vacio = nombre
    elif periodo == "semana":
        imagen = graficos.dashboard_periodo(u, hoy - timedelta(days=6), hoy, "Resumen de los últimos 7 días")
        vacio = "los últimos 7 días"
    elif periodo == "dias":
        imagen = graficos.gasto_por_dia(u, hoy.replace(day=1), hoy, nombre_mes(mes_actual()))
        vacio = "este mes"
    else:  # mes
        imagen = graficos.dashboard_periodo(u, hoy.replace(day=1), hoy,
                                            f"Resumen de {nombre_mes(mes_actual())}", con_asignado=True)
        vacio = "este mes"

    if imagen is None:
        await message.reply_text(f"No tienes movimientos para graficar en {vacio} 🤷\nPrueba con otro período 👇",
                                 reply_markup=teclado_graficas())
        return
    await message.reply_photo(imagen, caption=pie, parse_mode=HTML, reply_markup=teclado_graficas())


async def cmd_grafica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resultado = interpretar_periodo(" ".join(context.args))
    if resultado is None:
        await update.message.reply_text(
            "No entendí ese período 🤔 Prueba con:\n"
            "/grafica hoy · /grafica ayer · /grafica semana · /grafica mes · /grafica dias\n"
            "o una fecha: /grafica 15/11",
            reply_markup=teclado_graficas())
        return
    await enviar_grafica(update.message, update.effective_user.id, *resultado)


async def cmd_comparar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    mes = mes_actual()
    ant = datos.mes_anterior(mes)
    t_act, t_ant = datos.totales_mes(u, mes), datos.totales_mes(u, ant)
    claves = [k for k in datos.claves_de_tipo(u, "gasto")
              if t_act["por_categoria"].get(k, 0) or t_ant["por_categoria"].get(k, 0)]
    if not claves:
        await update.message.reply_text("Aún no hay gastos para comparar.")
        return

    total_a, total_p = t_act["gastos"], t_ant["gastos"]
    if total_p:
        cambio = (total_a - total_p) / total_p
        resumen = f"{'🔺 subió' if cambio > 0 else '🔻 bajó'} {abs(cambio):.0%}"
    else:
        resumen = "(el mes pasado no hay gastos para comparar)"
    lineas = [f"📊 <b>{nombre_mes(mes)} vs {nombre_mes(ant)}</b>", "",
              f"Gastado: {pesos(total_a)} vs {pesos(total_p)} {resumen}", ""]
    for k in claves:
        a, p = t_act["por_categoria"].get(k, 0), t_ant["por_categoria"].get(k, 0)
        flecha = "🔺" if a > p else "🔻" if a < p else "➖"
        lineas.append(f"{datos.emoji(u, k)} {esc(datos.nombre(u, k))}: {pesos(a)} vs {pesos(p)} {flecha}")
    await update.message.reply_text("\n".join(lineas), parse_mode=HTML)

    await update.message.reply_photo(graficos.comparar_meses(u, mes, ant, nombre_mes(mes), nombre_mes(ant)))


async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    if not u["movimientos"]:
        await update.message.reply_text("Aún no tienes movimientos para exportar.")
        return
    texto = io.StringIO()
    escritor = csv.writer(texto)
    escritor.writerow(["fecha", "categoria", "descripcion", "monto"])
    for m in u["movimientos"]:
        escritor.writerow([m["fecha"], datos.nombre(u, m["categoria"]), m["descripcion"], m["monto"]])
    archivo = io.BytesIO(texto.getvalue().encode("utf-8-sig"))  # utf-8-sig para que Excel lea las tildes
    archivo.name = "movimientos.csv"
    await update.message.reply_document(archivo)


# ─── Inicialización ─────────────────────────────────────
async def error_general(update, context: ContextTypes.DEFAULT_TYPE):
    print(f"❌ Error: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ Ocurrió un error, intenta de nuevo.")


async def al_iniciar(app):
    try:  # menú de comandos que aparece al escribir "/" en Telegram
        await app.bot.set_my_commands([
            BotCommand("saldo", "Ver mi saldo disponible"),
            BotCommand("resumen", "Resumen del mes"),
            BotCommand("categorias", "Ver mis categorías"),
            BotCommand("nuevacategoria", "Crear una categoría"),
            BotCommand("borrarcategoria", "Borrar una categoría"),
            BotCommand("presupuesto", "Asignar dinero a una categoría"),
            BotCommand("grafica", "Gráficas de mis gastos"),
            BotCommand("comparar", "Este mes vs. el mes pasado"),
            BotCommand("ultimos", "Últimos movimientos"),
            BotCommand("borrar", "Borrar el último movimiento"),
            BotCommand("exportar", "Descargar CSV"),
            BotCommand("ayuda", "Ayuda"),
        ])
    except Exception as e:
        print(f"(No pude crear el menú de comandos: {e})")


def main():
    app = ApplicationBuilder().token(TOKEN).post_init(al_iniciar).build()
    for nombre, funcion in [("start", start), ("ayuda", ayuda), ("saldo", cmd_saldo),
                            ("categorias", cmd_categorias), ("nuevacategoria", cmd_nuevacategoria),
                            ("borrarcategoria", cmd_borrarcategoria), ("presupuesto", cmd_presupuesto),
                            ("resumen", cmd_resumen), ("ultimos", cmd_ultimos), ("borrar", cmd_borrar),
                            ("grafica", cmd_grafica), ("comparar", cmd_comparar), ("exportar", cmd_exportar)]:
        app.add_handler(CommandHandler(nombre, funcion))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar))
    app.add_error_handler(error_general)
    print("🟢 Bot de finanzas corriendo... (Ctrl+C para detener)")
    app.run_polling()


if __name__ == "__main__":
    main()