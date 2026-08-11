"""El informe como documento HTML, listo para WeasyPrint (ADR-019, fase 4').

**Por que ya no se convierte el Markdown entero.** Hasta ahora este modulo era
tres lineas: coger `report_to_markdown`, pasarlo por markdown-it y envolverlo.
Salia un documento correcto y anonimo -- encabezados, parrafos y una tabla de
dos columnas por carrera. El problema no era la maquetacion: era que un
Markdown no tiene donde decir «esta es la carrera numero 1 de 5», ni «esta
afinidad es un 71 sobre 100», ni «esto es la portada». Esas tres cosas existen
en el `OrientationReport` y se perdian en la conversion, y lo que llegaba al
PDF era lo poco que sobrevive a `#` y `|`.

Asi que el documento se arma aqui, campo a campo, y el Markdown se queda como
lo que siempre fue util: el volcado que se lee en una terminal.

**El texto del modelo sigue pasando por markdown-it con `html=False`.** Es la
misma frontera de antes y por el mismo motivo: dos de los campos --el retrato
del perfil y cada explicacion-- los escribe un modelo cuyo contexto contiene lo
que ha tecleado el estudiante. No es que se espere un ataque; es que basta una
etiqueta mal cerrada, escrita sin mala intencion, para descuadrar el documento
entero. Con `html=False` cualquier etiqueta sale como texto visible, que es un
fallo que se ve y se corrige. Lo que NO viene del modelo --nombres de carrera,
instituciones, cifras-- se escapa con `escape()` por la misma razon, aunque
venga de un CSV nuestro.
"""

from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt

from src.models.report import OrientationReport, ReportCareer
from src.models.riasec import riasec_type_name
from src.reports.cifras import FILAS, afinidades, es_estimado, gestion, nombre_legible, valor

HOJA_DE_ESTILOS = Path(__file__).resolve().parent / "report.css"

_TITULO = "Informe de orientación vocacional — Spark Match"

# El birrete del producto, el mismo trazado que el logo del sidebar y el
# favicon de la web. Va inline y no como fichero: un `url()` mas que resolver
# es un sitio mas donde el PDF puede salir sin logo y sin decirlo.
_BIRRETE = (
    '<svg class="marca__logo" viewBox="0 0 24 24" fill="none">'
    '<path d="M3 9l9-5 9 5-9 5-9-5zm4 2.2V16l5 3 5-3v-4.8" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "setiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

_PLANTILLA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{titulo}</title>
</head>
<body>
{cuerpo}
</body>
</html>
"""


def _conversor() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False}).enable("table")


def _prosa(texto: str) -> str:
    """El texto del modelo, ya en parrafos y con las etiquetas neutralizadas."""
    html: str = _conversor().render(texto)
    return html


def _fecha_larga(dia: date) -> str:
    """«11 de agosto de 2026». Setiembre con e, que es como se escribe en Perú."""
    return f"{dia.day} de {_MESES[dia.month - 1]} de {dia.year}"


# --- Portada -----------------------------------------------------------------


def _codigo_desplegado(codigo: str) -> str:
    """El codigo RIASEC como tres piezas con nombre, no como tres letras.

    «IRA» es la etiqueta interna de un perfil y no significa nada para quien
    abre el informe. Puesto asi --la letra grande y debajo «Investigativo»--
    deja de ser un codigo y pasa a ser lo primero que el documento le explica.
    """
    piezas = [
        f'<div class="codigo__pieza">'
        f'<span class="codigo__letra">{escape(letra)}</span>'
        f'<span class="codigo__nombre">{escape(riasec_type_name(letra))}</span>'
        f"</div>"
        for letra in codigo
    ]
    return f'<div class="codigo">{"".join(piezas)}</div>'


def _dato_de_portada(etiqueta: str, valor_dato: str) -> str:
    return (
        f'<div class="portada__dato">'
        f'<span class="portada__dato-valor">{escape(valor_dato)}</span>'
        f'<span class="portada__dato-etiqueta">{escape(etiqueta)}</span>'
        f"</div>"
    )


def _portada(informe: OrientationReport) -> str:
    cuantas = len(informe.careers)
    datos = [
        _dato_de_portada(
            "carreras recomendadas" if cuantas != 1 else "carrera recomendada", str(cuantas)
        ),
        _dato_de_portada("programas comparados", f"{informe.total_candidates:,}"),
    ]
    if informe.issued_on is not None:
        datos.append(_dato_de_portada("fecha de emisión", _fecha_larga(informe.issued_on)))

    return f"""<section class="portada">
  <div class="marca">{_BIRRETE}<span class="marca__nombre">Spark Match</span></div>
  <h1 class="portada__titulo">Informe de orientación vocacional</h1>
  <p class="portada__bajada">Tu perfil de intereses, contrastado con el catálogo
  de educación superior del Perú.</p>
  {_codigo_desplegado(informe.riasec_code)}
  <div class="portada__datos">{"".join(datos)}</div>
</section>"""


# --- Fichas de carrera -------------------------------------------------------


def _barra(carrera: ReportCareer, afinidad: str) -> str:
    """La afinidad como barra, para poder compararla de un vistazo.

    El ancho se recorta a [0, 100] antes de escribirlo. La puntuacion sale del
    motor y ya viene en ese rango, pero esto acaba en un atributo `style`: si
    algun dia el rango cambia, una barra al 340% se sale del papel en vez de
    fallar, y nadie se entera hasta que alguien imprime.
    """
    ancho = min(100.0, max(0.0, carrera.match_score))
    return (
        f'<div class="afinidad">'
        f'<div class="afinidad__pista"><span class="afinidad__nivel" '
        f'style="width: {ancho:.1f}%"></span></div>'
        f'<span class="afinidad__cifra">{escape(afinidad)} de afinidad</span>'
        f"</div>"
    )


def _cifra(carrera: ReportCareer, campo: str, etiqueta: str) -> str:
    marca = '<span class="cifra__estimado">estimado</span>' if es_estimado(carrera, campo) else ""
    return (
        f'<div class="cifra">'
        f'<span class="cifra__valor">{escape(valor(carrera, campo))}</span>'
        f'<span class="cifra__etiqueta">{escape(etiqueta)}{marca}</span>'
        f"</div>"
    )


def _ficha(puesto: int, carrera: ReportCareer, afinidad: str) -> str:
    cifras = "".join(_cifra(carrera, campo, etiqueta) for campo, etiqueta in FILAS)
    lugar = (
        f"{carrera.institution} · {carrera.location} · "
        f"{carrera.institution_type} {gestion(carrera)}"
    )
    return f"""<article class="ficha">
  <header class="ficha__cabecera">
    <span class="ficha__puesto">{puesto}</span>
    <div class="ficha__nombre">
      <h3>{escape(carrera.career)}</h3>
      <p class="ficha__lugar">{escape(lugar)}</p>
    </div>
  </header>
  {_barra(carrera, afinidad)}
  <div class="cifras">{cifras}</div>
  <div class="ficha__insight">{_prosa(carrera.insight)}</div>
</article>"""


# --- Procedencia -------------------------------------------------------------


def _procedencia(informe: OrientationReport) -> str:
    """La letra pequena, que aqui no es letra pequena.

    Un informe que da cifras sin decir de donde salen invita a tratarlas como
    oficiales. Estas notas son las mismas que el agente esta obligado a dar en
    el chat; en un documento que se guarda y se ensena a terceros hacen mas
    falta, no menos, porque nadie va a estar delante para matizarlas.
    """
    notas = [
        "<strong>La afinidad es un cálculo de Spark Match</strong>, no una cifra "
        f"oficial del MINEDU. Criterio de puntuación <code>{escape(informe.scoring_version)}"
        "</code>.",
        "Las cifras de duración, ingreso, costo y admisión salen de "
        f"{escape(informe.dataset_source)}, datos del "
        f"{informe.dataset_snapshot_date.isoformat()}.",
        "Lo marcado como «estimado» <strong>no es un dato de ese programa</strong>: es "
        "la mediana de su familia de carrera, que se usa para rellenar lo que el portal "
        "no publicó.",
        "El código RIASEC de cada carrera lo asignó un modelo de lenguaje. Orienta; no "
        "es una clasificación oficial.",
    ]

    if informe.filters_applied:
        recortes = ", ".join(
            f"sin {nombre_legible(nombre)} habría {cuantos:,}"
            for nombre, cuantos in sorted(informe.candidates_without_each_filter.items())
        )
        aplicados = ", ".join(nombre_legible(f) for f in informe.filters_applied)
        notas.append(
            f"Se filtró por {escape(aplicados)}, y eso dejó {informe.total_candidates:,} "
            f"programas de todo el catálogo ({escape(recortes)}). Cambiar un filtro "
            "cambia esta lista."
        )
    else:
        notas.append(
            f"No se aplicó ningún filtro: se comparó contra los "
            f"{informe.total_candidates:,} programas del catálogo."
        )

    puntos = "".join(f"<li>{nota}</li>" for nota in notas)
    return f"""<section class="procedencia">
  <h2>Cómo leer este informe</h2>
  <ul>{puntos}</ul>
</section>"""


# --- El documento ------------------------------------------------------------


def report_to_html(informe: OrientationReport) -> str:
    """Documento HTML completo. La hoja de estilos se pasa aparte a WeasyPrint."""
    # Las afinidades se calculan de una vez y no carrera a carrera: si dos
    # redondean igual hay que enseñar un decimal, y eso no se sabe mirando una
    # sola. Ver `cifras.afinidades`.
    etiquetas = afinidades(informe.careers)
    fichas = "\n".join(
        _ficha(puesto, carrera, afinidad)
        for puesto, (carrera, afinidad) in enumerate(
            zip(informe.careers, etiquetas, strict=True), start=1
        )
    )

    cuerpo = f"""{_portada(informe)}
<section class="perfil">
  <h2>Tu perfil</h2>
  <div class="perfil__texto">{_prosa(informe.profile_summary)}</div>
</section>
<section class="carreras">
  <h2>Carreras recomendadas</h2>
  {fichas}
</section>
{_procedencia(informe)}"""

    return _PLANTILLA.format(titulo=_TITULO, cuerpo=cuerpo)


def read_stylesheet() -> str:
    """La hoja de estilos del informe, como texto."""
    return HOJA_DE_ESTILOS.read_text(encoding="utf-8")


__all__ = ["HOJA_DE_ESTILOS", "read_stylesheet", "report_to_html"]
