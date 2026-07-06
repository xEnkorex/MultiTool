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


def save_ico(path: str) -> None:
    make_icon(256).save(
        path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    save_ico("assets/icon.ico")
    print("Ícono generado en assets/icon.ico")
