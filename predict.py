"""
Modelo de machine learning: clasifica la CATEGORÍA de un gasto a partir de su descripción.
TF-IDF (texto -> números) + Regresión Logística (clasificador).

Cada usuario tiene SU PROPIO modelo: se entrena con los ejemplos base, con el nombre de sus
categorías personalizadas y con las correcciones que él mismo ha hecho (ejemplos_extra.json).
Así, si creas la categoría "Regalo hijo", el modelo aprende qué gastos van ahí.
"""
import json
import os
import re
import unicodedata

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

ARCHIVO_EXTRA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ejemplos_extra.json")

EJEMPLOS = {
    "comida": [
        "pizza", "almuerzo", "hamburguesa", "mercado", "supermercado", "cafe", "domicilio de comida",
        "empanadas", "desayuno", "cena en restaurante", "helado", "gaseosa", "pan", "frutas",
        "comida rapida", "jugo", "snacks", "pollo asado", "papitas", "restaurante", "carne", "arepas",
    ],
    "transporte": [
        "bus", "taxi", "uber", "gasolina", "metro", "pasaje", "parqueadero", "peaje", "tiquete de bus",
        "didi", "mantenimiento del carro", "tanquear la moto", "cabify", "indriver", "recarga cuenta del metro",
        "viaje en taxi", "cicla", "buseta",
    ],
    "ocio": [
        "cine", "salida con amigos", "fiesta", "discoteca", "videojuego", "concierto", "cerveza",
        "partido de futbol", "paseo", "bar", "karaoke", "boletas", "entrada al parque", "licor",
        "juego de ps5", "steam", "viaje de vacaciones", "trago", "billar", "boleta del estadio",
    ],
    "servicios": [
        "recibo de la luz", "agua", "internet", "plan de celular", "gas", "netflix", "spotify",
        "factura de energia", "telefono", "suscripcion", "recarga de celular", "tv cable",
        "recibo del agua", "youtube premium", "wifi", "energia", "disney plus",
    ],
    "salud": [
        "medicamentos", "cita medica", "drogueria", "dentista", "pastillas", "examenes de laboratorio",
        "gafas", "eps", "consulta medica", "vitaminas", "psicologo", "gimnasio", "acetaminofen",
        "odontologo", "tratamiento", "farmacia",
    ],
    "educacion": [
        "matricula", "libros", "curso online", "pension del colegio", "cuaderno", "fotocopias",
        "utiles escolares", "universidad", "papeleria", "clases de ingles", "semestre", "udemy",
        "impresiones", "taller", "colegio", "diplomado",
    ],
    "ropa": [
        "camisa", "zapatos", "jeans", "ropa", "tenis", "chaqueta", "vestido", "gorra", "medias",
        "accesorios", "bolso", "perfume", "buso", "pantalon", "sudadera", "camiseta",
    ],
    "hogar": [
        "arriendo", "administracion", "muebles", "reparacion de la casa", "productos de aseo",
        "plomero", "electrodomestico", "decoracion", "lavadora", "detergente", "pintura de la casa",
        "cama nueva", "nevera", "utensilios de cocina", "aseo", "cortinas",
    ],
    "deudas": [
        "pago deuda", "cuota del prestamo", "abono a la tarjeta de credito", "le pague a mi amigo lo que debia",
        "cuota de la moto", "pago credito", "deuda del banco", "cuota del celular", "prestamo",
        "pague la tarjeta", "intereses", "le debia a mi mama", "cuota del carro", "credito", "abono deuda",
        "pago de la tarjeta",
    ],
    "inversiones": [
        "compre acciones", "inversion en bitcoin", "cdt", "fondo de inversion", "criptomonedas",
        "invertir en etf", "dolares para invertir", "acciones de ecopetrol", "inversion en un negocio",
        "fiducia", "bitcoin", "etf", "invertir", "acciones", "ethereum", "inversion",
    ],
    "ahorro": [
        "ahorro", "guarde en la alcancia", "ahorros del mes", "meta de ahorro", "fondo de emergencia",
        "alcancia", "consigne a ahorros", "ahorro para el viaje", "separe plata para ahorrar",
        "cuenta de ahorros", "ahorrar", "ahorro del mes", "guardado",
    ],
    "ingreso": [
        "sueldo", "me pagaron", "salario", "me llego plata", "venta", "cobre", "me consignaron",
        "pago del trabajo", "quincena", "me regalaron plata", "recibi", "mesada", "freelance",
        "gane", "ingreso", "me pagaron el trabajo", "pago de un cliente", "deposito", "nomina",
    ],
    "otros": [
        "regalo", "varios", "cosas", "donacion", "propina", "multa", "otro gasto", "imprevisto",
        "veterinario", "gasto raro", "cosas varias", "no se que fue",
    ],
}


def normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^a-z0-9 ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _cargar_extra() -> dict:
    """Correcciones de los usuarios: {id_usuario: [[texto, categoria], ...]}"""
    if os.path.exists(ARCHIVO_EXTRA):
        try:
            with open(ARCHIVO_EXTRA, encoding="utf-8") as f:
                datos = json.load(f)
            if isinstance(datos, dict):
                return datos
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _entrenar(uid, categorias: dict):
    """categorias = {clave: nombre} con las categorías que tiene ese usuario."""
    frases, etiquetas = [], []
    for categoria, lista in EJEMPLOS.items():
        if categoria in categorias:
            for frase in lista:
                frases.append(normalizar(frase))
                etiquetas.append(categoria)
    for categoria, nombre in categorias.items():
        if categoria not in EJEMPLOS:  # categoría creada por el usuario: usamos su nombre como ejemplo
            for _ in range(3):
                frases.append(normalizar(nombre))
                etiquetas.append(categoria)
    for frase, categoria in _cargar_extra().get(str(uid), []):
        if categoria in categorias:
            frases.append(normalizar(frase))
            etiquetas.append(categoria)
    modelo = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)),
        LogisticRegression(C=20, max_iter=2000),
    )
    modelo.fit(frases, etiquetas)
    return modelo


_MODELOS = {}  # uid -> (firma de las categorías, modelo)


def _modelo(uid, categorias: dict):
    firma = tuple(sorted(categorias.items()))
    guardado = _MODELOS.get(uid)
    if guardado is None or guardado[0] != firma:  # si cambiaron las categorías, reentrena
        _MODELOS[uid] = (firma, _entrenar(uid, categorias))
    return _MODELOS[uid][1]


def predecir_categoria(uid, texto: str, categorias: dict):
    """Devuelve (categoria, confianza entre 0 y 1)."""
    modelo = _modelo(uid, categorias)
    probas = modelo.predict_proba([normalizar(texto)])[0]
    i = probas.argmax()
    return str(modelo.classes_[i]), float(probas[i])


def aprender(uid, texto: str, categoria: str):
    """Guarda una corrección del usuario y reentrena su modelo."""
    extra = _cargar_extra()
    extra.setdefault(str(uid), []).append([texto, categoria])
    with open(ARCHIVO_EXTRA, "w", encoding="utf-8") as f:
        json.dump(extra, f, ensure_ascii=False, indent=2)
    _MODELOS.pop(uid, None)


if __name__ == "__main__":
    cats = {k: k.capitalize() for k in EJEMPLOS}
    for prueba in ["pizza con amigos", "uber al trabajo", "cuota del banco", "compre bitcoin",
                   "me pagaron el mes", "recibo de la luz", "zapatos nuevos"]:
        cat, conf = predecir_categoria(0, prueba, cats)
        print(f"{prueba:25} -> {cat:12} {conf:.0%}")