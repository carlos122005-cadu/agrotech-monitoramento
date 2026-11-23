import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
import contextily as cx
from shapely.geometry import Point

def gerar_mapa_tatico(fazenda_gdf_4326, fazenda_nome, focos_lista, temp, umid, vento_kmh, dir_fogo_graus, caminho_saida="mapa_tatico.png"):
    gdf_3857 = fazenda_gdf_4326.to_crs("EPSG:3857")
    fig, ax = plt.subplots(figsize=(8, 8))

    gdf_3857.plot(ax=ax, facecolor="#00ff8833", edgecolor="#00ff88", linewidth=2.5)

    if focos_lista:
        pontos = [Point(f["longitude"], f["latitude"]) for f in focos_lista]
        focos_3857 = gpd.GeoSeries(pontos, crs="EPSG:4326").to_crs("EPSG:3857")
        focos_3857.plot(ax=ax, color="#ff3300", marker="^", markersize=220, zorder=5)

        rad = math.radians(90 - dir_fogo_graus)
        dx, dy = 800 * math.cos(rad), 800 * math.sin(rad)

        for pt in focos_3857:
            ax.annotate(
                "",
                xy=(pt.x + dx, pt.y + dy),
                xytext=(pt.x, pt.y),
                arrowprops=dict(facecolor="#ffd700", edgecolor="black", width=3, headwidth=10, headlength=10),
                zorder=6
            )

    ax.margins(0.30)
    cx.add_basemap(ax, source=cx.providers.Esri.WorldImagery)

    info_texto = (
        f"Propaga\u00e7\u00e3o do Fogo (Vento): {vento_kmh} km/h ({dir_fogo_graus}\u00b0)\n"
        f"Temp: {temp}\u00b0C | Umidade: {umid}%"
    )
    ax.text(
        0.03, 0.04, info_texto,
        transform=ax.transAxes,
        color="#ffd700",
        fontsize=10,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#000000cc", edgecolor="#ffd700", linewidth=1),
        zorder=8
    )

    ax.set_title(f"VECTORX AGRO | MAPA T\u00c1TICO: {fazenda_nome.upper()}", fontsize=11, fontweight="bold")
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(caminho_saida, dpi=200)
    plt.close(fig)
    return caminho_saida
  
