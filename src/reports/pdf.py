"""El informe como PDF (ADR-019, D11).

**Por que WeasyPrint se importa dentro de la funcion y no arriba.**

WeasyPrint no es Python puro: al importarse carga pango, cairo y gdk-pixbuf
por FFI. Si esas bibliotecas no estan en el sistema, el import revienta. Con
un `import weasyprint` a nivel de modulo, y siendo este modulo alcanzable
desde el arbol de imports del agente, **una imagen a la que le falte una
biblioteca del sistema no arranca**: el contenedor entero muere y con el la
conversacion, el assessment y todo lo demas, por no poder generar un PDF que
nadie ha pedido todavia.

Importandolo aqui dentro, esa misma imagen arranca, conversa y recomienda; lo
unico que falla es pedir un informe en PDF, y falla diciendo por que. Un
producto que pierde una funcion es un incidente; uno que no arranca es una
caida.

Esto no es hipotetico: el Dockerfile instala esas bibliotecas a mano en la
etapa de runtime, y es exactamente el tipo de linea que alguien borra al
adelgazar la imagen.
"""

from __future__ import annotations

import logging

from src.models.report import OrientationReport
from src.reports.html import HOJA_DE_ESTILOS, report_to_html

logger = logging.getLogger(__name__)


class PdfRenderingUnavailableError(RuntimeError):
    """WeasyPrint no se pudo cargar: faltan bibliotecas del sistema."""


def pdf_rendering_available() -> bool:
    """Si este proceso puede renderizar PDF ahora mismo.

    Lo usan los tests para saltarse el render fuera del contenedor, y sirve
    para comprobarlo al arrancar sin condicionar el arranque.
    """
    try:
        import weasyprint  # noqa: F401
    except ImportError, OSError:
        # `OSError` y no solo `ImportError`, por lo mismo que en
        # `report_to_pdf`: con el paquete de Python instalado y la biblioteca
        # nativa ausente, el fallo llega por ahi.
        return False
    return True


def report_to_pdf(informe: OrientationReport) -> bytes:
    """Renderiza el informe a PDF.

    Raises:
        PdfRenderingUnavailableError: si faltan pango/cairo/gdk-pixbuf en el
            sistema. El mensaje dice que instalar, porque quien se lo va a
            encontrar es alguien mirando logs de ECS a las tantas.
    """
    try:
        from weasyprint import CSS, HTML
        from weasyprint.text.fonts import FontConfiguration
    except (ImportError, OSError) as exc:
        # `OSError` y no solo `ImportError`: cuando el paquete de Python esta
        # instalado pero la biblioteca nativa no, el fallo llega por ahi.
        raise PdfRenderingUnavailableError(
            "WeasyPrint no se pudo cargar. En la imagen de runtime hacen falta "
            "libpango-1.0-0, libpangoft2-1.0-0, libcairo2 y libgdk-pixbuf-2.0-0 "
            "(ver Dockerfile). El resto del agente funciona sin ellas; solo el "
            f"PDF no. Causa: {exc}"
        ) from exc

    # UNA sola `FontConfiguration` para los dos sitios, y no es opcional pese a
    # que ambos parametros admitan `None`. Sin ella el informe salia con la
    # serif de reemplazo de Debian en lugar de Fraunces.
    #
    # El motivo esta en `weasyprint/css/__init__.py`: la regla `@font-face` se
    # procesa al CONSTRUIR el CSS, y ahi hay un `if font_config is not None:`
    # sin `else`. Con `None` la regla se descarta **en silencio** -- ni WARNING
    # ni excepcion. Y `write_pdf()` se fabrica una por su cuenta cuando no se le
    # pasa ninguna (`document.py::_render`), pero esa nace despues de que el CSS
    # ya se haya parseado, asi que jamas se entera de Fraunces.
    #
    # Por eso el fallo no se veia por ningun lado: el PDF salia entero, valido,
    # con el mismo texto y el mismo numero de paginas. Solo cambiaba la letra.
    # Se caza mirando que fuentes quedaron incrustadas -- ver el test de
    # `tests/reports/pdf.py` que hace justo eso.
    fuentes = FontConfiguration()
    documento = HTML(string=report_to_html(informe))
    pdf: bytes = documento.write_pdf(
        stylesheets=[CSS(filename=str(HOJA_DE_ESTILOS), font_config=fuentes)],
        font_config=fuentes,
    )

    logger.info(
        "Informe renderizado a PDF",
        extra={"careers": len(informe.careers), "bytes": len(pdf)},
    )
    return pdf


__all__ = ["PdfRenderingUnavailableError", "pdf_rendering_available", "report_to_pdf"]
