Here is a refactored version of your README. I have organized the information into clear sections, used bullet points for scannability, and kept all your original explanations and links intact. 

***

# dmt-bicing

This project analyzes the relationship between bike-sharing (Bicing) availability and socioeconomic factors across different neighborhoods in Barcelona. All data is sourced from the Open Data Barcelona portal.

## 📊 Data Sources

We utilize three primary datasets to build our analysis:

1. **Neighborhood Polygons:** Geographic boundaries for the districts and neighborhoods of Barcelona.
2. **Bicing Stations Information:** Static metadata including station names and geographical locations. *Note: To ensure accuracy, we only extract locations that appear in the most recently updated document.*
3. **Bicing Stations Status:** Historical data detailing the number of available bikes, empty docks, and overall station status. 
   * [Source: Estat Estacions Bicing](https://opendata-ajuntament.barcelona.cat/data/ca/dataset/estat-estacions-bicing)

## ⚙️ Data Processing & Optimization

Working with raw historical data requires significant optimization. To make the dataset manageable and relevant, we applied the following transformations:

* **Time Filtering:** Data is filtered to only include hours between **06:00 and 22:00**, capturing the timeframe when people tend to use the bikes the most.
* **Granularity Reduction:** The data points were downsampled to a **15-minute granularity**.
* **Format & Compression:** By applying these filters and converting the raw `.csv` files into `.parquet` format, we achieved massive compression—reducing the initial file sizes from **~300 MB down to ~10 MB** per file.
* **Version Control:** Due to GitHub size constraints, the processed data is pushed to the repository partitioned by month (for the months available).

## 🏙️ Socioeconomic Analysis (Income Data)

A core goal of this project is to analyze bike availability based on the income level of different zones. To achieve this, we have integrated a normalized income per capita index for Barcelona:

**Índex de la Renda disponible de les llars per càpita (RDLpc)**
> *L’índex de renda disponible estima la renda mitjana o capacitat econòmica del residents per districtes i barris en relació amb la mitjana de la ciutat.* (The disposable income index estimates the average income or economic capacity of residents by districts and neighborhoods in relation to the city average.)

* **Date/Source:** 16/03/2026 — Ajuntament de Barcelona. Oficina Municipal de Dades (OMD)
* **Dataset Link:** [Portal de Dades - Estadístiques (RDLpc)](https://portaldades.ajuntament.barcelona.cat/ca/estad%C3%ADstiques/issbupxmdy)