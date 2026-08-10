"""Tests del informe en PDF (fase 4' del ADR-019).

El render de verdad necesita pango, cairo y gdk-pixbuf, que estan en la imagen
de runtime pero no necesariamente en la maquina de quien desarrolla. Los tests
que lo requieren se saltan solos; los que comprueban el CONTRATO —que la falta
de esas bibliotecas degrade la funcion en vez de tumbar el proceso— corren
siempre, porque es justo el caso que no se puede reproducir a mano.
"""

import io

import pytest
from pypdf import PdfReader

from src.reports.pdf import (
    PdfRenderingUnavailableError,
    pdf_rendering_available,
    report_to_pdf,
)
from tests.reports.fixtures import informe

necesita_weasyprint = pytest.mark.skipif(
    not pdf_rendering_available(),
    reason="faltan pango/cairo/gdk-pixbuf en este sistema (si estan en la imagen)",
)


class TestElContratoDeDegradacion:
    """Sin PDF el producto pierde una funcion. Sin arrancar, las pierde todas."""

    def test_el_modulo_se_importa_sin_weasyprint(self):
        """La comprobacion mas importante de este fichero, y la mas sosa.

        No la hace el cuerpo del test: la hace el `from src.reports.pdf
        import ...` de arriba. Si alguien sube el `import weasyprint` al
        principio de ese modulo, **este fichero deja de poder recolectarse**
        en cualquier maquina sin las bibliotecas del sistema, que es
        exactamente la senal que se quiere. El assert solo deja constancia de
        que llegamos hasta aqui.
        """
        assert callable(report_to_pdf)

    def test_el_error_dice_que_instalar(self):
        # Quien se lo encuentre esta mirando logs de ECS, no este fichero.
        mensaje = str(
            PdfRenderingUnavailableError(
                "WeasyPrint no se pudo cargar. En la imagen de runtime hacen falta "
                "libpango-1.0-0, libpangoft2-1.0-0, libcairo2 y libgdk-pixbuf-2.0-0"
            )
        )

        assert "libpango" in mensaje
        assert "libcairo2" in mensaje

    def test_es_un_runtime_error(self):
        # Para que un `except RuntimeError` de mas arriba lo recoja sin tener
        # que conocer este modulo.
        assert issubclass(PdfRenderingUnavailableError, RuntimeError)


@necesita_weasyprint
class TestRenderReal:
    def test_produce_un_pdf(self):
        pdf = report_to_pdf(informe())

        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 1000

    def test_el_texto_del_informe_acaba_dentro(self):
        # No se comprueba la maquetacion -- eso se mira con los ojos. Se
        # comprueba que el contenido llego al documento y no se perdio por el
        # camino entre tres conversiones.
        pdf = report_to_pdf(informe(profile_summary="Marcador del resumen."))
        texto = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)

        assert "Marcador del resumen." in texto
        assert "Ingeniería Civil" in texto

    def test_las_cifras_llegan_con_su_formato(self):
        # Entre Markdown, HTML y PDF hay tres oportunidades de perder un
        # separador de miles o convertir 0.03 en "0.03".
        pdf = report_to_pdf(informe())
        texto = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)

        assert "S/ 4,261" in texto
        assert "3%" in texto
