"""Generación del ícono de la app: una marca minimalista de barras de
ecualizador, consistente con el tema dark/cyberpunk de la UI web. Un solo
lugar para no duplicar el dibujo entre el .ico del ejecutable y el ícono
del system tray.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

BG_COLOR = (10, 10, 15, 255)
BAR_COLOR = (0, 255, 242, 255)


def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = round(size * 0.06)
    radius = round(size * 0.22)
    draw.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=BG_COLOR)

    bar_width = size * 0.12
    gap = size * 0.08
    heights = (0.34, 0.62, 0.46)  # como un pequeño ecualizador
    total_width = bar_width * 3 + gap * 2
    start_x = (size - total_width) / 2
    base_y = size * 0.76

    for i, h in enumerate(heights):
        bar_h = size * h
        x0 = start_x + i * (bar_width + gap)
        x1 = x0 + bar_width
        y1 = base_y
        y0 = y1 - bar_h
        draw.rounded_rectangle([x0, y0, x1, y1], radius=bar_width / 2, fill=BAR_COLOR)

    return img


def make_icon_maskable(size: int = 512) -> Image.Image:
    """Versión para íconos "maskable" de Android: el SO puede recortar el
    ícono en cualquier forma (círculo, squircle, etc.), así que acá no
    dibujamos nuestro propio fondo redondeado — el contenido vive bien
    adentro de la "zona segura" recomendada (círculo centrado del 66%)
    para no perder nada en el recorte."""
    img = Image.new("RGBA", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)

    content_size = size * 0.66
    offset = (size - content_size) / 2

    bar_width = content_size * 0.12
    gap = content_size * 0.08
    heights = (0.34, 0.62, 0.46)
    total_width = bar_width * 3 + gap * 2
    start_x = offset + (content_size - total_width) / 2
    base_y = offset + content_size * 0.76

    for i, h in enumerate(heights):
        bar_h = content_size * h
        x0 = start_x + i * (bar_width + gap)
        x1 = x0 + bar_width
        y1 = base_y
        y0 = y1 - bar_h
        draw.rounded_rectangle([x0, y0, x1, y1], radius=bar_width / 2, fill=BAR_COLOR)

    return img


def save_ico(path: str) -> None:
    make_icon(256).save(
        path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def save_web_icons(output_dir: str = "static/icons") -> None:
    """Íconos para el manifest de PWA (Add to Home Screen) + apple-touch-icon."""
    import os

    os.makedirs(output_dir, exist_ok=True)

    make_icon(192).save(os.path.join(output_dir, "icon-192.png"))
    make_icon(512).save(os.path.join(output_dir, "icon-512.png"))
    make_icon_maskable(512).save(os.path.join(output_dir, "icon-512-maskable.png"))

    # iOS no soporta transparencia en el apple-touch-icon (le pone fondo
    # blanco solo si es transparente); lo componemos sobre el fondo dark
    # de la app para que quede prolijo.
    base = make_icon(180)
    apple_icon = Image.new("RGB", (180, 180), BG_COLOR[:3])
    apple_icon.paste(base, (0, 0), base)
    apple_icon.save(os.path.join(output_dir, "apple-touch-icon.png"))


ANDROID_DENSITIES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def save_android_icons(res_dir: str) -> None:
    """Íconos `ic_launcher.png` (legacy, no adaptive) para el proyecto
    Android — una carpeta `mipmap-*` por densidad, como espera Gradle."""
    import os

    for folder, size in ANDROID_DENSITIES.items():
        out = os.path.join(res_dir, folder)
        os.makedirs(out, exist_ok=True)
        make_icon(size).save(os.path.join(out, "ic_launcher.png"))
        # ic_launcher_round: mismo arte, el círculo de "seguridad" del ícono
        # normal ya deja margen de sobra para el recorte redondo de algunos launchers.
        make_icon(size).save(os.path.join(out, "ic_launcher_round.png"))


if __name__ == "__main__":
    save_ico("assets/icon.ico")
    print("Ícono generado en assets/icon.ico")
    save_web_icons("static/icons")
    print("Íconos web generados en static/icons/")
