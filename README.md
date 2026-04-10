# dmt-bicing

This project analyzes the relationship between bike-sharing (Bicing) availability and socioeconomic factors across different neighborhoods in Barcelona. All data is sourced from the Open Data Barcelona portal.

---

## 📊 Data Sources

We utilize three primary datasets:

### 1. Neighborhood Polygons

* Geographic boundaries for Barcelona districts and neighborhoods
* Used for spatial aggregation and mapping

### 2. Bicing Stations Information

* Static metadata (station names, IDs, locations)
* ⚠️ Only the most recently updated file is used to ensure location accuracy

### 3. Bicing Stations Status

* Historical time series data:

  * Available bikes
  * Empty docks
  * Station status

* 🔗 Source:
  [https://opendata-ajuntament.barcelona.cat/data/ca/dataset/estat-estacions-bicing](https://opendata-ajuntament.barcelona.cat/data/ca/dataset/estat-estacions-bicing)

---
Here’s a **clean and improved version of section 4** with clearer wording, better grammar, and more precise terminology:

---

### 4. Neighborhood Socioeconomic Information


* **Age Group Distribution**

  * Population is segmented into age ranges
  * Enables analysis focused on specific demographic groups (e.g., working-age population, elderly, youth)

* **Income Index (RDLpc)**

  * Disposable income per capita normalized against the Barcelona city average
  * Allows comparison of relative economic capacity across neighborhoods

These variables are used to enrich the spatial analysis and explore how demographic and economic factors relate to Bicing usage patterns.


## 📁 Data Structure

All raw data is stored in:

```
/data/raw_csv
```

* Contains original CSV files from the data portal
* Heavy files are excluded from version control

### Processed Data

* Raw CSVs are transformed into optimized `.parquet` files
* Stored in partitioned format (by month)

---

## ⚙️ Data Processing & Optimization

Raw historical data is large and requires preprocessing before analysis.

### Transformations applied:

* **Time Filtering**

  * Keep data between **06:00 and 22:00**
  * Focus on peak usage hours

* **Granularity Reduction**

  * Downsample to **15-minute intervals**

* **Format & Compression**

  * Convert `.csv` → `.parquet`
  * Reduce size from ~300MB → ~10MB per file

* **Version Control**

  * Processed data is split by month to fit GitHub limits

---

## 🔄 Data Pipeline

### Step 1 — Raw → Processed

Extract and transform raw data into parquet format.

### Step 2 — Load Processed Data

Run:

```
src/data/load/<module_or_script>
```

(Select the specific data transformation or dataset you want to generate.)

---

## 📓 Notebooks

Analysis is performed through notebooks:

* `01_...` → ⚠️ Work in progress
* `02_...` → Main analysis

### How to run:

1. Open the notebooks
2. Execute cells sequentially
3. Start from data loading to analysis

---

## 🏙️ Socioeconomic Analysis (Income Data)

A core goal is to analyze bike availability relative to income levels across neighborhoods.

### Dataset:

**Índex de la Renda disponible de les llars per càpita (RDLpc)**

> The disposable income index estimates the average income or economic capacity of residents by district and neighborhood relative to the city average.

* **Source:** Ajuntament de Barcelona — Oficina Municipal de Dades (OMD)
* **Date:** 16/03/2026
* 🔗 [https://portaldades.ajuntament.barcelona.cat/ca/estad%C3%ADstiques/issbupxmdy](https://portaldades.ajuntament.barcelona.cat/ca/estad%C3%ADstiques/issbupxmdy)

---

## 🎯 Project Goal

* Analyze spatial patterns in Bicing availability
* Study correlation with socioeconomic indicators
* Identify inequalities in urban mobility access

---

## 🚧 Status

* Data pipeline: ✅
* Preprocessing: ✅
* Analysis notebooks: ⚠️ In progress

