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


_FICHEROS_DE_FUENTE = ("/FontFile", "/FontFile2", "/FontFile3")


def fuentes_incrustadas(pdf: bytes) -> set[str]:
    """Los nombres de las fuentes que viajan DENTRO del documento.

    Una fuente esta incrustada si su descriptor trae algun `/FontFile*`; si no,
    el PDF solo la nombra y quien lo abra pondra la que buenamente tenga. Las
    Type0 --las que usa WeasyPrint en cuanto aparece una tilde-- esconden el
    descriptor un nivel mas abajo, dentro de `/DescendantFonts`.

    Los nombres llegan con el prefijo de subconjunto que pone el renderizador
    (`/KTGASA+DejaVu-Serif-Bold`), asi que se comparan por contenido.
    """
    nombres: set[str] = set()
    for pagina in PdfReader(io.BytesIO(pdf)).pages:
        recursos = pagina.get("/Resources")
        if recursos is None:
            continue
        tipografias = recursos.get_object().get("/Font")
        if tipografias is None:
            continue
        for referencia in tipografias.get_object().values():
            tipografia = referencia.get_object()
            descriptor = tipografia.get("/FontDescriptor")
            if descriptor is None:
                hijas = tipografia.get("/DescendantFonts")
                if hijas is None:
                    continue
                descriptor = hijas.get_object()[0].get_object().get("/FontDescriptor")
            if descriptor is None:
                continue
            if any(clave in descriptor.get_object() for clave in _FICHEROS_DE_FUENTE):
                nombres.add(str(tipografia.get("/BaseFont", "")))
    return nombres


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

    def test_la_letra_del_producto_viaja_dentro(self):
        """El unico test del fichero que mira COMO se ve el informe, y hace falta.

        Los tres de arriba pasan igual con la fuente equivocada: si la regla
        `@font-face` no se aplica, WeasyPrint **no avisa** --cae a la serif del
        sistema y sigue--, con lo que el PDF conserva el texto, las cifras y el
        numero de paginas. No es hipotetico: el informe emitido el 2026-08-11 a
        las 16:14 UTC salio con `DejaVu-Serif-Bold` incrustada y Fraunces en
        ninguna parte, sin una sola linea de queja en el log del servicio.

        Vendorizar la fuente no basta, entonces: hay que comprobar que llego al
        documento. Ver el comentario de `report_to_pdf` para el porque.
        """
        incrustadas = fuentes_incrustadas(report_to_pdf(informe()))

        assert any("Fraunces" in nombre for nombre in incrustadas), (
            f"Fraunces no viajo dentro del PDF. Incrustadas: {sorted(incrustadas)}"
        )

    def test_las_fuentes_van_incrustadas_y_no_solo_nombradas(self):
        # Un PDF que solo NOMBRA sus fuentes se ve distinto en cada maquina, y
        # este documento esta pensado para imprimirse y enseñarse en casa.
        incrustadas = fuentes_incrustadas(report_to_pdf(informe()))

        assert len(incrustadas) >= 2
