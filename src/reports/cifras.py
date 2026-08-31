"""Como se escribe cada cifra del informe.

Estaba dentro de `markdown.py`, que era su unico lector. Desde que el PDF se
arma con su propia estructura --portada, fichas, barras-- hay dos, y las cifras
tienen que salir identicas en los dos: el Markdown es lo que se lee en una
terminal cuando algo va mal, y si ahi pone «S/ 4,261» y en el PDF «S/4261.00»,
el que este depurando no sabe cual de los dos miente.

**Aqui no se escribe prosa.** Solo formato: separadores, unidades y las
etiquetas de los campos. El retrato del perfil y las explicaciones vienen ya
escritas dentro del `OrientationReport`.

Formato de cifras: separador de miles con coma y decimales con punto, que es la
convencion peruana y la que ya usa la web. Un informe que escribe las
cantidades distinto que la pantalla de la que salio parece de otro sitio.
"""

from __future__ import annotations

from src.models.report import ReportCareer

# Como se llama cada cifra en el informe, y en que orden se listan. El orden
# es deliberado: primero lo que dura (el compromiso), luego lo que se gana
# (el motivo), luego lo que cuesta y lo dificil que es entrar (las dos
# barreras). Es el orden en que un estudiante hace las preguntas.
FILAS: tuple[tuple[str, str], ...] = (
    ("duration_years", "Duración"),
    ("monthly_income", "Ingreso mensual al egresar"),
    ("annual_cost", "Costo anual"),
    ("admission_rate", "Admisión"),
)

MARCA_ESTIMADO = "estimado"

# El CSV guarda la gestion en femenino ("Publica", "Privada"), que concuerda
# con "universidad" y chirria con "instituto": "Instituto publica". Como el
# informe se imprime y se ensena, la concordancia no es un detalle.
_EN_MASCULINO = {"pública": "público", "privada": "privado"}

# Los filtros viajan con su nombre de codigo. Imprimirlos tal cual pondria
# "Se filtro por management_type" en un documento que lee un chico de
# dieciseis anos.
_NOMBRE_DEL_FILTRO = {
    "region": "la región",
    "management_type": "si es pública o privada",
    "institution_type": "si es universidad o instituto",
    "max_annual_cost": "el presupuesto",
}


def gestion(carrera: ReportCareer) -> str:
    """La gestion concordando con el tipo de institucion."""
    valor = carrera.management_type.lower()
    if carrera.institution_type.lower().startswith("instituto"):
        return _EN_MASCULINO.get(valor, valor)
    return valor


def nombre_legible(filtro: str) -> str:
    return _NOMBRE_DEL_FILTRO.get(filtro, filtro)


def soles(cantidad: float) -> str:
    """S/ 4,261 — sin decimales, que en estas magnitudes son ruido."""
    return f"S/ {cantidad:,.0f}"


def anios(cantidad: float) -> str:
    """5 años, o 3.5 años cuando la cifra no es redonda."""
    if abs(cantidad - round(cantidad)) < 0.05:
        entero = round(cantidad)
        return f"{entero} año" if entero == 1 else f"{entero} años"
    return f"{cantidad:.1f} años"


def porcentaje(valor: float) -> str:
    """La tasa de admision ya viaja en porcentaje: 0-100, no 0-1.

    Multiplicarla por cien --que es lo que se hacia-- convertia un 17% en un
    1700%. Ver la descripcion del campo en :class:`ReportCareer`.
    """
    return f"{valor:.0f}%"


def es_estimado(carrera: ReportCareer, campo: str) -> bool:
    return campo in carrera.estimated


def valor(carrera: ReportCareer, campo: str) -> str:
    """La cifra ya escrita, sin la marca de estimado."""
    bruto = getattr(carrera, campo)
    if campo == "duration_years":
        return anios(bruto)
    if campo == "admission_rate":
        return porcentaje(bruto)
    return soles(bruto)


def valor_marcado(carrera: ReportCareer, campo: str) -> str:
    """La cifra con «(estimado)» detras cuando lo es.

    Un dato imputado marcado como tal es la diferencia entre informar y
    aparentar. La lista `estimated` llega hasta aqui justo para esto.
    """
    texto = valor(carrera, campo)
    if es_estimado(carrera, campo):
        return f"{texto} ({MARCA_ESTIMADO})"
    return texto


def afinidades(carreras: list[ReportCareer]) -> list[str]:
    """El porcentaje de afinidad de cada carrera, ya desempatado.

    Sin decimales, 71.2 y 70.9 se enseñan las dos como «71%». En una lista
    numerada eso es peor que impreciso: el estudiante ve un puesto 1 y un
    puesto 2 con la misma cifra al lado y la unica lectura posible es que el
    orden se lo invento alguien. Y no puede comprobarlo, porque el numero que
    los separa es justo el que no se le enseña.

    El decimal se pone **a todas o a ninguna**. Una lista con «71.2%» y «68%»
    mezclados parece un fallo de formato; que aparezca el decimal es
    exactamente la senal de que dos carreras estaban asi de cerca.
    """
    redondeados = [round(c.match_score) for c in carreras]
    hay_empate = len(set(redondeados)) < len(redondeados)
    decimales = 1 if hay_empate else 0
    return [f"{c.match_score:.{decimales}f}%" for c in carreras]


__all__ = [
    "FILAS",
    "MARCA_ESTIMADO",
    "afinidades",
    "anios",
    "es_estimado",
    "gestion",
    "nombre_legible",
    "porcentaje",
    "soles",
    "valor",
    "valor_marcado",
]
