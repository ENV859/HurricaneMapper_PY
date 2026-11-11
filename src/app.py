"""Hurricane Tracker: a minimal Shiny for Python conversion of the notebook.

Expected data (same layout used by the notebook):
    project/
      data/raw/IBTrACS_NA.shp  (+ .shx, .dbf, .prj)
      notebooks/app.py

Run from a Python environment with shiny, arcgis, pandas, and matplotlib:
    python -m shiny run --reload app.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from arcgis.features import FeatureLayer
from arcgis.geometry import Polyline
from arcgis.geometry.filters import intersects
from shiny import App, reactive, render, ui


# Read the same shapefile and county service as the source notebook.
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "raw" / "IBTrACS_NA.shp"
COUNTIES_URL = (
    "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
    "USA_Counties_Generalized_Boundaries/FeatureServer/0"
)

if not DATA_FILE.is_file():
    raise FileNotFoundError(
        f"Cannot find {DATA_FILE}. Place the IBTrACS shapefile and its "
        "companion files in the project's data/raw directory."
    )

sdf_allstorms = pd.DataFrame.spatial.from_featureclass(str(DATA_FILE))
counties_layer = FeatureLayer(COUNTIES_URL)  # Public layer; no ArcGIS sign-in required.

# Shiny select inputs use strings, even when SEASON contains numeric values.
seasons = sorted(
    (int(year) for year in sdf_allstorms["SEASON"].dropna().unique()),
    reverse=True,
)
if not seasons:
    raise ValueError("No storm seasons were found in the IBTrACS shapefile.")

storms_by_season = {
    year: sorted(
        sdf_allstorms.loc[
            sdf_allstorms["SEASON"] == year, "NAME"
        ].dropna().astype(str).unique().tolist()
    )
    for year in seasons
}

initial_season = 2005 if 2005 in seasons else seasons[0]
initial_names = storms_by_season[initial_season]
initial_name = "KATRINA" if "KATRINA" in initial_names else initial_names[0]


def get_affected_counties(season: int, storm_name: str) -> pd.Series:
    """Sum the population of track-intersected counties, grouped by state."""
    storm_points = sdf_allstorms.loc[
        (sdf_allstorms["SEASON"] == season)
        & (sdf_allstorms["NAME"] == storm_name)
    ].sort_values("ISO_TIME")

    coords = [[geom.x, geom.y] for geom in storm_points["SHAPE"]]
    if len(coords) < 2:
        return pd.Series(dtype=float)

    track_line = Polyline(
        {
            "paths": [coords],
            "spatialReference": storm_points.spatial.sr,
        }
    )

    # Only attributes are needed for the plot; avoid downloading county shapes.
    matches = counties_layer.query(
        geometry_filter=intersects(track_line),
        out_fields="STATE_NAME,POPULATION",
        return_geometry=False,
    )
    counties = pd.DataFrame([feature.attributes for feature in matches.features])
    if counties.empty:
        return pd.Series(dtype=float)

    counties["POPULATION"] = pd.to_numeric(counties["POPULATION"], errors="coerce")
    return (
        counties.groupby("STATE_NAME")["POPULATION"]
        .sum()
        .sort_values()
    )


# Two input widgets and one output widget.
app_ui = ui.page_fluid(
    ui.h2("Hurricane Tracker"),
    ui.row(
        ui.column(
            3,
            ui.input_select(
                "season", "Storm season",
                choices=[str(year) for year in seasons],
                selected=str(initial_season),
            ),
        ),
        ui.column(
            3,
            ui.input_select(
                "storm_name", "Storm name",
                choices=initial_names,
                selected=initial_name,
            ),
        ),
    ),
    ui.output_plot("population_plot", height="525px"),
)


def server(input, output, session):
    @reactive.effect
    @reactive.event(input.season)
    def update_storm_names():
        """Update the storm dropdown whenever the selected season changes."""
        season = int(input.season())
        names = storms_by_season[season]
        with reactive.isolate():
            current_name = input.storm_name()
        selected = current_name if current_name in names else names[0]
        ui.update_select("storm_name", choices=names, selected=selected)

    @render.plot
    def population_plot():
        season = int(input.season())
        storm_name = input.storm_name()
        fig, ax = plt.subplots(figsize=(10, 6), layout="constrained")

        # Season may change just before the updated name choices reach the browser.
        if storm_name not in storms_by_season[season]:
            ax.text(0.5, 0.5, "Updating storm selection...",
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
            return fig

        population = get_affected_counties(season, storm_name)
        if population.empty:
            ax.text(0.5, 0.5, "No intersecting counties found.",
                    ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
        else:
            ax.barh(population.index, population.values)
            ax.set_xlabel("Total population of intersected counties")
            ax.set_ylabel("State")
            ax.set_title(f"{storm_name} ({season}): population by state")
        return fig


app = App(app_ui, server)
