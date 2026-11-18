import csv
import html
import io
import os
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import datos
from predict import aprender, predecir_categoria

# ─── Configuración ──────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    print("❌ Falta la variable TELEGRAM_TOKEN")
    print("   En PowerShell: $env:TELEGRAM_TOKEN='tu_token_aqui'")
    exit(1)

UMBRAL = 0.25  # por debajo de esta confianza el bot avisa que no está seguro
HTML = ParseMode.HTML

AYUDA = (
    "💰 <b>Bot de finanzas personales</b>\n\n"
    "<b>Registrar un movimiento</b>, escríbelo con su valor:\n"
    "• <code>pizza 25000</code>\n"
    "• <code>uber 12k</code>\n"
    "• <code>cuota de la moto 350.000</code>\n"
    "• <code>compré bitcoin 200000</code>\n"
    "• <code>sueldo 2 millones</code>\n"
    "Yo detecto la categoría con machine learning 🤖 y si me equivoco la corriges con un botón.\n\n"
    "<b>Comandos</b>\n"
    "/saldo — ver tu saldo\n"
    "/saldo 500000 — decirme cuánto dinero tienes ahora\n"
    "/resumen — resumen del mes por categoría\n"
    "/grafica — gráfica de tus gastos del mes\n"
    "/ultimos — tus últimos movimientos\n"
    "/presupuesto 800000 — límite de gastos mensual (te aviso al 80%)\n"
    "/borrar — elimina el último movimiento\n"
    "/exportar — descarga tus movimientos en CSV\n"
    "/ayuda — ver este mensaje"
)


# ─── Funciones auxiliares ───────────────────────────────
def pesos(n) -> str:
    return ("-" if n < 0 else "") + "$" + f"{abs(int(round(n))):,}".replace(",", ".")


def mes_actual() -> str:
    return datetime.now().strftime("%Y-%m")


def nombre_mes(mes: str) -> str:
    anio, num = mes.split("-")
    return f"{datos.MESES[int(num) - 1]} {anio}"


def barra(valor: float, total: float, largo: int = 10) -> str:
    bloques = round(valor / total * largo) if total else 0
    return "█" * bloques + "░" * (largo - bloques)


def formato_movimiento(uid, mov: dict, nota: str = "") -> str:
    cat = mov["categoria"]
    titulo = "💰 Ingreso registrado" if cat == "ingreso" else "✅ Movimiento registrado"
    lineas = [f"<b>{titulo}</b>",
              f"{datos.EMOJIS[cat]} {datos.NOMBRES[cat]} — <b>{pesos(mov['monto'])}</b>"]
    if mov["descripcion"]:
        lineas.append(f"📝 {html.escape(mov['descripcion'])}")
    lineas.append(f"💵 Saldo: <b>{pesos(datos.saldo_actual(datos.usuario(uid)))}</b>")
    if nota:
        lineas.append(f"\n{nota}")
    return "\n".join(lineas)


def botones_movimiento(mov_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Cambiar categoría", callback_data=f"cambiar:{mov_id}"),
        InlineKeyboardButton("🗑️ Deshacer", callback_data=f"borrar:{mov_id}"),
    ]])


def botones_categorias(mov_id: int) -> InlineKeyboardMarkup:
    claves = list(datos.NOMBRES)
    botones = [InlineKeyboardButton(f"{datos.EMOJIS[c]} {datos.NOMBRES[c]}",
                                    callback_data=f"cat:{mov_id}:{c}") for c in claves]
    filas = [botones[i:i + 3] for i in range(0, len(botones), 3)]
    return InlineKeyboardMarkup(filas)


def alerta_presupuesto(u: dict) -> str:
    limite = u["presupuesto"]
    if not limite:
        return ""
    gastos = datos.totales_mes(u, mes_actual())["gastos"]
    if gastos >= limite:
        return f"🚨 ¡Superaste tu presupuesto! Llevas {pesos(gastos)} de {pesos(limite)}."
    if gastos >= limite * 0.8:
        return f"⚠️ Ojo: ya gastaste el {gastos / limite:.0%} de tu presupuesto ({pesos(gastos)} de {pesos(limite)})."
    return ""


# ─── Registro de movimientos (mensajes normales) ────────
async def registrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    resultado = datos.extraer_monto(update.message.text)
    if not resultado:
        await update.message.reply_text(
            "No encontré un monto 🤔\nEscríbelo así: <code>pizza 25000</code> (usa /ayuda para ver más).",
            parse_mode=HTML)
        return

    monto, descripcion = resultado
    if descripcion:
        categoria, confianza = predecir_categoria(descripcion)
    else:
        categoria, confianza = "otros", 0.0

    nota = f"🤖 Confianza del modelo: {confianza:.0%}"
    if confianza < UMBRAL:
        categoria = "otros"
        nota = "🤔 No estoy seguro de la categoría. Toca «Cambiar categoría» y aprenderé de tu respuesta."

    mov = datos.agregar_movimiento(uid, monto, categoria, descripcion)
    aviso = alerta_presupuesto(datos.usuario(uid)) if categoria in datos.GASTOS else ""
    if aviso:
        nota += "\n\n" + aviso
    await update.message.reply_text(formato_movimiento(uid, mov, nota), parse_mode=HTML,
                                    reply_markup=botones_movimiento(mov["id"]))


# ─── Botones (cambiar categoría / deshacer) ─────────────
async def botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()
    partes = query.data.split(":")
    accion, mov_id = partes[0], int(partes[1])
    mov = datos.buscar_movimiento(uid, mov_id)

    if not mov:
        await query.edit_message_text("Ese movimiento ya no existe.")
        return

    if accion == "cambiar":
        await query.edit_message_reply_markup(botones_categorias(mov_id))

    elif accion == "cat":
        nueva = partes[2]
        datos.cambiar_categoria(uid, mov_id, nueva)
        if mov["descripcion"]:
            aprender(mov["descripcion"], nueva)  # el modelo aprende de la corrección
        await query.edit_message_text(
            formato_movimiento(uid, mov, "🧠 Anotado, aprendí de tu corrección."),
            parse_mode=HTML, reply_markup=botones_movimiento(mov_id))

    elif accion == "borrar":
        datos.borrar_movimiento(uid, mov_id)
        await query.edit_message_text("🗑️ Movimiento eliminado.")


# ─── Comandos ───────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu asistente de finanzas.\n"
        "Para empezar dime cuánto dinero tienes con /saldo (por ejemplo <code>/saldo 500000</code>), "
        "y luego anota cada gasto escribiéndolo, como <code>almuerzo 15000</code>.\n\n"
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
        await update.message.reply_text(f"✅ Listo, tu saldo ahora es <b>{pesos(resultado[0])}</b>.", parse_mode=HTML)
    else:
        await update.message.reply_text(f"💵 Tu saldo actual es <b>{pesos(datos.saldo_actual(u))}</b>.", parse_mode=HTML)


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    mes = mes_actual()
    t = datos.totales_mes(u, mes)
    if not t["por_categoria"]:
        await update.message.reply_text("Aún no tienes movimientos este mes. Escribe, por ejemplo: pizza 25000")
        return

    lineas = [f"📊 <b>Resumen de {nombre_mes(mes)}</b>", "",
              f"💰 Ingresos: <b>{pesos(t['ingresos'])}</b>",
              f"💸 Gastos: <b>{pesos(t['gastos'])}</b>",
              f"📈 Ahorro e inversión: <b>{pesos(t['apartado'])}</b>",
              f"💵 Saldo actual: <b>{pesos(datos.saldo_actual(u))}</b>", "", "<b>Por categoría</b>"]

    egresos = {c: v for c, v in t["por_categoria"].items() if c != "ingreso"}
    total_egresos = sum(egresos.values())
    for cat, valor in sorted(egresos.items(), key=lambda x: x[1], reverse=True):
        lineas.append(f"{datos.EMOJIS[cat]} {datos.NOMBRES[cat]}: {pesos(valor)}\n   {barra(valor, total_egresos)} {valor / total_egresos:.0%}")

    if u["presupuesto"]:
        pct = t["gastos"] / u["presupuesto"]
        lineas += ["", f"🎯 Presupuesto: {pesos(t['gastos'])} de {pesos(u['presupuesto'])} ({pct:.0%})"]
    await update.message.reply_text("\n".join(lineas), parse_mode=HTML)


async def cmd_ultimos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    if not u["movimientos"]:
        await update.message.reply_text("Aún no tienes movimientos.")
        return
    lineas = ["🧾 <b>Últimos movimientos</b>", ""]
    for m in reversed(u["movimientos"][-10:]):
        fecha = datetime.fromisoformat(m["fecha"]).strftime("%d/%m")
        signo = "+" if m["categoria"] == "ingreso" else "-"
        desc = html.escape(m["descripcion"]) or "(sin descripción)"
        lineas.append(f"{fecha} {datos.EMOJIS[m['categoria']]} {desc}: {signo}{pesos(m['monto'])}")
    await update.message.reply_text("\n".join(lineas), parse_mode=HTML)


async def cmd_presupuesto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    if context.args:
        resultado = datos.extraer_monto(" ".join(context.args))
        if not resultado:
            await update.message.reply_text("Escribe un valor válido, por ejemplo: /presupuesto 800000")
            return
        u["presupuesto"] = resultado[0]
        datos.guardar()
        await update.message.reply_text(f"🎯 Presupuesto mensual fijado en <b>{pesos(resultado[0])}</b>.", parse_mode=HTML)
    elif u["presupuesto"]:
        gastos = datos.totales_mes(u, mes_actual())["gastos"]
        await update.message.reply_text(
            f"🎯 Presupuesto: {pesos(u['presupuesto'])}\nGastado este mes: {pesos(gastos)} ({gastos / u['presupuesto']:.0%})")
    else:
        await update.message.reply_text("No tienes presupuesto. Fíjalo así: /presupuesto 800000")


async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = datos.usuario(uid)
    if not u["movimientos"]:
        await update.message.reply_text("No hay nada para borrar.")
        return
    ultimo = u["movimientos"][-1]
    datos.borrar_movimiento(uid, ultimo["id"])
    await update.message.reply_text(f"🗑️ Eliminé: {html.escape(ultimo['descripcion']) or 'movimiento'} ({pesos(ultimo['monto'])})",
                                    parse_mode=HTML)


async def cmd_grafica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    u = datos.usuario(update.effective_user.id)
    mes = mes_actual()
    por_cat = datos.totales_mes(u, mes)["por_categoria"]
    gastos = {c: v for c, v in por_cat.items() if c in datos.GASTOS}
    if not gastos:
        await update.message.reply_text("Aún no hay gastos este mes para graficar.")
        return

    etiquetas = [f"{datos.NOMBRES[c]}" for c in gastos]
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.pie(list(gastos.values()), labels=etiquetas, autopct="%1.0f%%", startangle=90)
    ax.set_title(f"Gastos de {nombre_mes(mes)}\nTotal: {pesos(sum(gastos.values()))}")
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    await update.message.reply_photo(buffer)


async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = datos.usuario(update.effective_user.id)
    if not u["movimientos"]:
        await update.message.reply_text("Aún no tienes movimientos para exportar.")
        return
    texto = io.StringIO()
    escritor = csv.writer(texto)
    escritor.writerow(["fecha", "categoria", "descripcion", "monto"])
    for m in u["movimientos"]:
        escritor.writerow([m["fecha"], datos.NOMBRES[m["categoria"]], m["descripcion"], m["monto"]])
    archivo = io.BytesIO(texto.getvalue().encode("utf-8-sig"))  # utf-8-sig para que Excel lea las tildes
    archivo.name = "movimientos.csv"
    await update.message.reply_document(archivo)


# ─── Inicialización ─────────────────────────────────────
async def error_general(update, context: ContextTypes.DEFAULT_TYPE):
    print(f"❌ Error: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ Ocurrió un error, intenta de nuevo.")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    for nombre, funcion in [("start", start), ("ayuda", ayuda), ("saldo", cmd_saldo),
                            ("resumen", cmd_resumen), ("ultimos", cmd_ultimos),
                            ("presupuesto", cmd_presupuesto), ("borrar", cmd_borrar),
                            ("grafica", cmd_grafica), ("exportar", cmd_exportar)]:
        app.add_handler(CommandHandler(nombre, funcion))
    app.add_handler(CallbackQueryHandler(botones))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, registrar))
    app.add_error_handler(error_general)
    print("🟢 Bot de finanzas corriendo... (Ctrl+C para detener)")
    app.run_polling()


if __name__ == "__main__":
    main()