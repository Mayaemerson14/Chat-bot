"""
Gráficas del bot (matplotlib). Cada función devuelve una imagen PNG en memoria (BytesIO),
lista para enviar por Telegram, o None si no hay datos para graficar.
"""
import io
from datetime import timedelta

import matplotlib

matplotlib.use("Agg")  # sin ventanas: solo genera la imagen
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.ticker import FuncFormatter

import datos

PALETA = ["#4C78A8", "#F58518", "#54A24B", "#B279A2", "#EECA3B", "#9D755D", "#72B7B2", "#FF9DA6",
          "#3D3D8F", "#8CD17D", "#D4A6C8", "#86BCB6", "#B6992D", "#499894", "#A0CBE8"]
ROJO, VERDE, GRIS_FONDO, TEXTO = "#D64545", "#1B8A5A", "#E5E7EB", "#1F2937"

plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": "#CBD5E1"})


def _pesos(n) -> str:
    # "\\$" y no "$": si no, matplotlib interpreta dos signos $ como una fórmula matemática
    return ("-" if n < 0 else "") + "\\$" + f"{abs(int(round(n))):,}".replace(",", ".")


def _corto(n) -> str:
    n = abs(n)
    if n >= 1_000_000:
        return f"\\${n / 1_000_000:.1f}M".replace(".", ",")
    if n >= 1_000:
        return f"\\${n / 1_000:.0f}k"
    return f"\\${n:.0f}"


def _colores(u: dict) -> dict:
    """Cada categoría conserva siempre el mismo color."""
    return {k: PALETA[i % len(PALETA)] for i, k in enumerate(u["categorias"])}


def _a_buffer(fig) -> io.BytesIO:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", dpi=130, facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    return buffer


def dashboard_periodo(u: dict, desde, hasta, titulo: str, con_asignado: bool = False):
    """Panel de un período (un día, 7 días, el mes...): saldo, ingresos, gastos y dónde se fue la plata.
    Con con_asignado=True añade el dinero asignado por categoría (tiene sentido para el mes completo)."""
    t = datos.totales_periodo(u, desde, hasta)
    if not t["por_categoria"]:
        return None
    colores = _colores(u)

    # --- gastos por categoría (las más pequeñas se agrupan para que se lea bien) ---
    gastos = sorted(((k, v) for k, v in t["por_categoria"].items()
                     if datos.tipo(u, k) == "gasto" and v > 0), key=lambda x: -x[1])
    items = [(datos.nombre(u, k), v, colores[k]) for k, v in gastos[:7]]
    if len(gastos) > 7:
        items.append(("Otras categorías", sum(v for _, v in gastos[7:]), "#BAB0AC"))
    total_gastos = sum(v for _, v, _ in items)

    # --- categorías con dinero asignado ---
    asignadas = []
    for k, c in u["categorias"].items():
        if con_asignado and c["tipo"] == "gasto" and c["presupuesto"] > 0:
            asignadas.append((c["nombre"], t["por_categoria"].get(k, 0), c["presupuesto"], colores[k]))
    asignadas.sort(key=lambda x: -(x[1] / x[2]))

    alturas = [1.05, 4.0] + ([0.62 * len(asignadas) + 1.1] if asignadas else [])
    fig = plt.figure(figsize=(9, sum(alturas) + 1.0), facecolor="white")
    gs = fig.add_gridspec(len(alturas), 1, height_ratios=alturas, hspace=0.45,
                          top=0.93, bottom=0.03, left=0.05, right=0.95)
    fig.suptitle(titulo, fontsize=18, fontweight="bold", color=TEXTO, y=0.985)

    # 1) Tarjetas con los números principales
    saldo = datos.saldo_actual(u)
    tarjetas = [("Saldo disponible", saldo, VERDE if saldo >= 0 else ROJO),
                ("Ingresos", t["ingresos"], TEXTO),
                ("Gastado", t["gastos"], TEXTO),
                ("Ahorro e inversión", t["apartado"], TEXTO)]
    ax = fig.add_subplot(gs[0])
    ax.axis("off")
    ax.set_xlim(0, 4)
    ax.set_ylim(0, 1)
    for i, (titulo, valor, color) in enumerate(tarjetas):
        ax.add_patch(FancyBboxPatch((i + 0.04, 0.06), 0.92, 0.88, boxstyle="round,pad=0,rounding_size=0.08",
                                    fc="#F3F4F6", ec="none"))
        ax.text(i + 0.5, 0.67, titulo, ha="center", va="center", fontsize=10, color="#6B7280")
        ax.text(i + 0.5, 0.32, _pesos(valor), ha="center", va="center", fontsize=15,
                fontweight="bold", color=color)

    # 2) Dónde se fue el dinero: dona + lista con valores
    sub = gs[1].subgridspec(1, 2, width_ratios=[1, 1.15], wspace=0.05)
    ax_d, ax_l = fig.add_subplot(sub[0]), fig.add_subplot(sub[1])
    ax_d.set_title("¿En qué se fue mi dinero?", loc="left", fontsize=13, fontweight="bold", color=TEXTO, pad=10)
    ax_l.axis("off")
    if items:
        ax_d.pie([v for _, v, _ in items], colors=[c for _, _, c in items], startangle=90,
                 counterclock=False, wedgeprops=dict(width=0.36, edgecolor="white", linewidth=2))
        ax_d.text(0, 0.07, _pesos(total_gastos), ha="center", va="center", fontsize=14, fontweight="bold", color=TEXTO)
        ax_d.text(0, -0.15, "gastado", ha="center", va="center", fontsize=10, color="#6B7280")
        filas = max(len(items), 6)
        ax_l.set_xlim(0, 1)
        ax_l.set_ylim(0, filas)
        for i, (nombre, valor, color) in enumerate(items):
            y = filas - i - 0.5
            ax_l.add_patch(Rectangle((0.0, y - 0.17), 0.045, 0.34, color=color))
            ax_l.text(0.075, y, nombre, va="center", fontsize=11, color=TEXTO)
            ax_l.text(1.0, y, f"{_pesos(valor)}  ·  {valor / total_gastos:.0%}", va="center", ha="right",
                      fontsize=11, color="#4B5563")
    else:
        ax_d.axis("off")
        ax_d.text(0.5, 0.5, "No hay gastos\nen este período", ha="center", va="center", fontsize=12, color="#6B7280")

    # 3) Dinero asignado por categoría: lo gastado dentro de lo asignado
    if asignadas:
        ax = fig.add_subplot(gs[2])
        mayor = max(max(a, p) for _, a, p, _ in asignadas)
        for i, (nombre, gastado, asignado, color) in enumerate(asignadas):
            pasado = gastado > asignado
            ax.barh(i, asignado, color=GRIS_FONDO, height=0.55)
            ax.barh(i, gastado, color=ROJO if pasado else color, height=0.55)
            etiqueta = f"{_pesos(gastado)} de {_pesos(asignado)}  ({gastado / asignado:.0%})"
            if pasado:
                etiqueta += "  ¡te pasaste!"
            ax.text(max(gastado, asignado) + mayor * 0.02, i, etiqueta, va="center", fontsize=10,
                    color=ROJO if pasado else "#4B5563", fontweight="bold" if pasado else "normal")
        ax.set_yticks(range(len(asignadas)))
        ax.set_yticklabels([n for n, _, _, _ in asignadas], fontsize=11)
        ax.set_ylim(len(asignadas) - 0.4, -0.6)  # la primera arriba
        ax.set_xlim(0, mayor * 1.9)
        ax.set_xticks([])
        for lado in ("top", "right", "bottom"):
            ax.spines[lado].set_visible(False)
        ax.tick_params(left=False)
        ax.set_title("Dinero asignado por categoría", loc="left", fontsize=13, fontweight="bold", color=TEXTO, pad=10)
    return _a_buffer(fig)


def comparar_meses(u: dict, mes: str, ant: str, nombre_act: str, nombre_ant: str):
    """Barras agrupadas: gasto de cada categoría este mes vs. el mes anterior."""
    t_act, t_ant = datos.totales_mes(u, mes), datos.totales_mes(u, ant)
    claves = [k for k in datos.claves_de_tipo(u, "gasto")
              if t_act["por_categoria"].get(k, 0) or t_ant["por_categoria"].get(k, 0)]
    if not claves:
        return None
    claves.sort(key=lambda k: -max(t_act["por_categoria"].get(k, 0), t_ant["por_categoria"].get(k, 0)))
    act = [t_act["por_categoria"].get(k, 0) for k in claves]
    pas = [t_ant["por_categoria"].get(k, 0) for k in claves]

    fig, ax = plt.subplots(figsize=(min(13, max(9, 0.8 * len(claves) + 3)), 5.8), facecolor="white")
    x, w = range(len(claves)), 0.4
    barras_p = ax.bar([i - w / 2 for i in x], pas, w, color="#B8C4D6", label=nombre_ant)
    barras_a = ax.bar([i + w / 2 for i in x], act, w, color="#4C78A8", label=nombre_act)
    ax.bar_label(barras_p, labels=[_corto(v) if v else "" for v in pas], fontsize=8, padding=3, color="#6B7280")
    ax.bar_label(barras_a, labels=[_corto(v) if v else "" for v in act], fontsize=8, padding=3, color=TEXTO)
    ax.set_xticks(list(x))
    ax.set_xticklabels([datos.nombre(u, k) for k in claves], rotation=25, ha="right", fontsize=10)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _corto(v) if v else "\\$0"))
    ax.grid(axis="y", color="#E5E7EB")
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.legend(frameon=False, loc="upper right")

    total_a, total_p = sum(act), sum(pas)
    if total_p:
        cambio = (total_a - total_p) / total_p
        sub = f"Total: {_pesos(total_a)} vs {_pesos(total_p)}  ({'+' if cambio > 0 else ''}{cambio:.0%})"
    else:
        sub = f"Total: {_pesos(total_a)} (el mes anterior no tiene gastos)"
    fig.suptitle(f"Gastos por categoría: {nombre_act} vs {nombre_ant}", fontsize=15, fontweight="bold",
                 color=TEXTO, y=1.0)
    ax.set_title(sub, fontsize=11, color="#4B5563", pad=12)
    return _a_buffer(fig)


DIAS_CORTOS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def gasto_por_dia(u: dict, desde, hasta, titulo: str):
    """Barras apiladas: cuánto gastaste cada día, separado por categoría, con el promedio diario."""
    por_dia = datos.gastos_por_dia(u, desde, hasta)
    totales_cat = {}
    for dia in por_dia.values():
        for k, v in dia.items():
            totales_cat[k] = totales_cat.get(k, 0) + v
    if not totales_cat:
        return None

    dias = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
    colores = _colores(u)
    top = [k for k, _ in sorted(totales_cat.items(), key=lambda x: -x[1])[:5]]
    hay_otras = len(totales_cat) > len(top)
    series = {k: [por_dia.get(d.isoformat(), {}).get(k, 0) for d in dias] for k in top}
    if hay_otras:
        series["__otras__"] = [sum(v for k, v in por_dia.get(d.isoformat(), {}).items() if k not in top)
                               for d in dias]
    totales = [sum(s[i] for s in series.values()) for i in range(len(dias))]
    promedio = sum(totales) / len(dias)
    mayor = max(range(len(dias)), key=lambda i: totales[i])

    fig, ax = plt.subplots(figsize=(10, 6), facecolor="white")
    base = [0] * len(dias)
    for clave, valores in series.items():
        nombre = "Otras categorías" if clave == "__otras__" else datos.nombre(u, clave)
        color = "#BAB0AC" if clave == "__otras__" else colores[clave]
        ax.bar(range(len(dias)), valores, bottom=base, color=color, label=nombre, width=0.72)
        base = [b + v for b, v in zip(base, valores)]

    ax.axhline(promedio, color=ROJO, linestyle="--", linewidth=1.3)
    ax.text(len(dias) - 0.4, promedio, f"promedio diario {_pesos(promedio)}", ha="right", va="bottom",
            fontsize=9, color=ROJO, fontweight="bold",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.9, pad=1.5))
    ax.set_xticks(range(len(dias)))
    ax.set_xticklabels([f"{d.day}\n{DIAS_CORTOS[d.weekday()]}" for d in dias], fontsize=7.5)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _corto(v) if v else "\\$0"))
    ax.grid(axis="y", color="#E5E7EB")
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=len(series), frameon=False, fontsize=9)

    fig.suptitle(f"Gasto día por día — {titulo}", fontsize=15, fontweight="bold", color=TEXTO, y=1.0)
    ax.set_title(f"Total {_pesos(sum(totales))}  ·  día de mayor gasto: {dias[mayor].day} ({_pesos(totales[mayor])})",
                 fontsize=11, color="#4B5563", pad=12)
    return _a_buffer(fig)