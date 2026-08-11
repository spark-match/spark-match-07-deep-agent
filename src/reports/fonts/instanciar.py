"""Saca los dos estaticos de Fraunces que van en la imagen. Se ejecuta a mano.

**Esto no corre en el build ni en CI.** Los `.ttf` de al lado estan versionados
y son el artefacto; este fichero es la receta para poder rehacerlos, no un paso
de la construccion. Un `pip install fonttools` y una descarga de GitHub en
mitad de un `docker build` es exactamente lo que no queremos entre el codigo y
el PDF que se le entrega a un estudiante.

Por que estaticos y no la variable entera: el PDF lo renderiza WeasyPrint sobre
Pango/fontconfig, y ahi la resolucion de los ejes de una fuente variable depende
de la version de las bibliotecas del sistema. Un documento que se imprime no
puede cambiar de grosor porque la imagen base subio de Debian. Con dos ficheros
estaticos --regular y semibold, con el tamano optico ya fijado-- no hay eje que
resolver: lo que se ve al revisarlo es lo que sale en ECS.

De paso pesan una quinta parte: 360 KB la variable contra ~73 KB cada estatico,
y solo se usan dos grosores.

Uso:

    pip install fonttools
    curl -L -o Fraunces-variable.ttf \\
        'https://github.com/google/fonts/raw/main/ofl/fraunces/Fraunces%5BSOFT%2CWONK%2Copsz%2Cwght%5D.ttf'
    python instanciar.py
"""

# fontTools no trae `py.typed` y este fichero no lo importa nadie: es una
# receta que se corre a mano, no codigo del agente. Tampoco viaja en la imagen
# --ver `.dockerignore`--, asi que la dependencia no existe en runtime.
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
from fontTools.varLib import instancer  # type: ignore[import-untyped]

ORIGEN = "Fraunces-variable.ttf"

# opsz 48: el tamano optico pensado para titulares, que es lo unico para lo que
# se usa Fraunces aqui. SOFT 0 y WONK 0 dejan la version sobria de la familia;
# los ejes raros de Fraunces son preciosos y no son para un documento que un
# chico de dieciseis anos va a ensenar en casa.
EJES_FIJOS = {"opsz": 48, "SOFT": 0, "WONK": 0}

# Solo el 600. Fraunces es la letra de los titulares del informe y ahi no hay
# mas grosor que el semibold; el cuerpo va en Inter. Anadir un peso es anadir
# una tupla aqui Y su @font-face en `report.css` -- si falta lo segundo, el
# fichero viaja en la imagen sin dibujar nada.
SALIDAS = [("Fraunces-SemiBold.ttf", 600, "SemiBold")]

# Identificadores del `name` table que hay que reescribir a mano. Con
# `updateFontNames=True` fontTools los saca de la tabla STAT y ahi revienta:
# opsz 48 no es un valor con nombre, asi que no hay de donde sacar el sufijo.
FAMILIA, SUBFAMILIA, NOMBRE_COMPLETO, POSTSCRIPT = 1, 2, 4, 6


def main() -> None:
    for nombre, peso, estilo in SALIDAS:
        parcial = instancer.instantiateVariableFont(
            TTFont(ORIGEN), {**EJES_FIJOS, "wght": peso}, inplace=False, updateFontNames=False
        )
        tabla = parcial["name"]
        tabla.setName("Fraunces", FAMILIA, 3, 1, 0x409)
        tabla.setName(estilo, SUBFAMILIA, 3, 1, 0x409)
        tabla.setName(f"Fraunces {estilo}", NOMBRE_COMPLETO, 3, 1, 0x409)
        tabla.setName(f"Fraunces-{estilo}", POSTSCRIPT, 3, 1, 0x409)
        parcial.save(nombre)
        print(f"{nombre}: wght={peso}, estilo={estilo}")


if __name__ == "__main__":
    main()
