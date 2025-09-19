# app_gsheets.py — Formulation Knowledge Base (Google Sheets backend)
# -----------------------------------------------------------------
# What this version does
# - Uses Google Sheets as the database, so your data is shared, durable, and easy to edit.
# - Keeps the same features: browse, ingredient frequency, typical structure, surfactant recommendations,
#   and adding new formulations that write back to Google Sheets.
# - Designed for Streamlit Cloud deployment with secrets-based auth.
#
# Setup (high level):
# 1) Create a Google Sheet with 4 tabs: Brands, Formulations, Ingredients, Formulation_Ingredients (exact names).
# 2) Put the column headers exactly as in the TEMPLATE below (copy & paste).
# 3) Create a Google Service Account; share the Sheet with the SA email (Editor). Put the SA JSON in Streamlit secrets.
# 4) Set st.secrets like:
#    [gsheets]
#    spreadsheet_id = "YOUR_SHEET_ID"
#    service_account = "{"type":"service_account", ... }"  # full JSON on one line
# 5) Deploy on Streamlit Cloud with requirements.txt provided below.
# -----------------------------------------------------------------

import json
from typing import List, Dict, Any
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ------------------------------
# Constants & Templates
# ------------------------------
TEMPLATE = {
    "Brands": ["id", "name"],
    "Formulations": ["id", "name", "brand_id", "category", "product_type", "notes"],
    "Ingredients": ["id", "inci_name", "common_name", "function", "cas"],
    "Formulation_Ingredients": ["id", "formulation_id", "ingredient_id", "percentage", "phase", "notes"],
}

BODY_WASH_RULES = {
    "base_structure": [
        ("Water", "Solvent", "q.s. to 100%"),
        ("Primary Surfactant", "Surfactant", "6–18% a.i."),
        ("Co‑surfactant / Amphoteric", "Surfactant", "3–10%"),
        ("Foam Booster / Fatty Amide", "Foam Booster", "0–3%"),
        ("Humectant (Glycerin/PG/Propanediol)", "Humectant", "1–5%"),
        ("Rheology Modifier (Acrylates/Cellulose)", "Rheology", "0.2–1.0%"),
        ("Salt / Electrolyte", "Viscosity", "0–2% (titrate)"),
        ("Preservative", "Preservative", "per supplier"),
        ("Fragrance / Colorant", "Aesthetic", "q.s.")
    ],
    "surfactant_systems": [
        {
            "name": "Classic SLES/CAPB",
            "tags": ["cost‑effective", "medium mildness"],
            "combo": [
                {"inci": "Sodium Laureth Sulfate", "role": "primary", "range": "8–14% a.i."},
                {"inci": "Cocamidopropyl Betaine", "role": "amphoteric", "range": "3–7%"},
                {"inci": "Cocamide MEA (optional)", "role": "foam/viscosity", "range": "0–2%"}
            ]
        },
        {
            "name": "Sulfate‑Free APG/Betaine",
            "tags": ["mild", "green", "sulfate‑free"],
            "combo": [
                {"inci": "Coco‑/Decyl Glucoside", "role": "primary", "range": "5–10% a.i."},
                {"inci": "Cocamidopropyl Betaine", "role": "amphoteric", "range": "3–6%"}
            ]
        },
        {
            "name": "Sarcosinate/Betaine (clear gel)",
            "tags": ["mild", "clarity"],
            "combo": [
                {"inci": "Sodium Lauroyl Sarcosinate", "role": "primary", "range": "5–10% a.i."},
                {"inci": "Cocamidopropyl Betaine", "role": "amphoteric", "range": "3–6%"}
            ]
        },
        {
            "name": "AOS/CAPB (high foam)",
            "tags": ["high foam", "cost"],
            "combo": [
                {"inci": "Sodium C14‑16 Olefin Sulfonate", "role": "primary", "range": "6–12% a.i."},
                {"inci": "Cocamidopropyl Betaine", "role": "amphoteric", "range": "3–6%"}
            ]
        }
    ]
}

FACIAL_CLEANSER_RULES = {
    "base_structure": [
        ("Water", "Solvent", "q.s. to 100%"),
        ("Mild Surfactant (Sarcosinate/Sulfoacetate)", "Surfactant", "4–10% a.i."),
        ("Amphoteric (CAPB)", "Surfactant", "2–5%"),
        ("Humectant", "Humectant", "2–5%"),
        ("Rheology Modifier", "Rheology", "0.2–0.8%"),
        ("pH Adjuster", "pH", "as needed"),
        ("Preservative", "Preservative", "per supplier"),
        ("Fragrance (optional)", "Aesthetic", "q.s.")
    ]
}

RULES_BY_PRODUCT = {
    ("Bodycare", "Body Wash"): BODY_WASH_RULES,
    ("Skincare", "Facial Cleanser"): FACIAL_CLEANSER_RULES,
}

# ------------------------------
# Auth & Client
# ------------------------------

def get_client():
    cfg = st.secrets.get("gsheets")
    if not cfg or "service_account" not in cfg or "spreadsheet_id" not in cfg:
        st.stop()
    info = json.loads(cfg["service_account"]) if isinstance(cfg["service_account"], str) else cfg["service_account"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds), cfg["spreadsheet_id"]


def open_sheet(gc, ssid, tab):
    sh = gc.open_by_key(ssid)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        # create with headers
        ws = sh.add_worksheet(title=tab, rows=1000, cols=20)
        ws.append_row(TEMPLATE[tab])
    return ws


def ws_to_df(ws: gspread.Worksheet) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    # coerce numeric columns commonly used
    for col in ["id", "brand_id", "percentage", "ingredient_id", "formulation_id"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def df_append(ws: gspread.Worksheet, row: Dict[str, Any]):
    # Ensure columns order according to header
    headers = ws.row_values(1)
    out = [str(row.get(h, "")) for h in headers]
    ws.append_row(out)


# ------------------------------
# UI
# ------------------------------

st.set_page_config(page_title="Formulation KB — Sheets", layout="wide")
st.title("🧪 Formulation Knowledge Base — Google Sheets")

# Connect & load
try:
    gc, SSID = get_client()
    ws_brands = open_sheet(gc, SSID, "Brands")
    ws_forms = open_sheet(gc, SSID, "Formulations")
    ws_ings = open_sheet(gc, SSID, "Ingredients")
    ws_fi = open_sheet(gc, SSID, "Formulation_Ingredients")

    df_brands = ws_to_df(ws_brands)
    df_forms = ws_to_df(ws_forms)
    df_ings = ws_to_df(ws_ings)
    df_fi = ws_to_df(ws_fi)
except Exception as e:
    st.error(f"Google Sheets connection failed: {e}")
    st.stop()

# Sidebar filters
with st.sidebar:
    st.header("Filters")
    cats = ["(All)"] + sorted([c for c in df_forms["category"].dropna().unique().tolist() if c])
    sel_cat = st.selectbox("Category", cats)
    sel_cat_q = None if sel_cat == "(All)" else sel_cat

    if sel_cat_q:
        ptypes = ["(All)"] + sorted(df_forms[df_forms["category"]==sel_cat_q]["product_type"].dropna().unique().tolist())
    else:
        ptypes = ["(All)"] + sorted(df_forms["product_type"].dropna().unique().tolist())
    sel_ptype = st.selectbox("Product Type", ptypes)
    sel_ptype_q = None if sel_ptype == "(All)" else sel_ptype

    brands = ["(All)"] + sorted(df_brands["name"].dropna().unique().tolist())
    sel_brand = st.selectbox("Brand", brands)
    sel_brand_q = None if sel_brand == "(All)" else sel_brand

    st.markdown("---")
    st.subheader("Surfactant Recommender")
    rec_target = st.selectbox("Target Product", ["Body Wash", "Facial Cleanser"])
    want_sulfate_free = st.checkbox("Sulfate‑free preference")
    want_mild = st.checkbox("Prioritize mildness")
    want_high_foam = st.checkbox("High Foam")

# Resolve IDs
brand_name_to_id = {r["name"]: int(r["id"]) for _, r in df_brands.iterrows() if pd.notna(r["id"]) and pd.notna(r["name"]) }
brand_id_to_name = {v:k for k,v in brand_name_to_id.items()}

# Filtered formulations
df_view = df_forms.copy()
if sel_cat_q:
    df_view = df_view[df_view["category"] == sel_cat_q]
if sel_ptype_q:
    df_view = df_view[df_view["product_type"] == sel_ptype_q]
if sel_brand_q:
    bid = brand_name_to_id.get(sel_brand_q, None)
    if bid is not None:
        df_view = df_view[df_view["brand_id"] == bid]

# Map brand id to brand name for display
_dfv = df_view.copy()
_dfv["brand"] = _dfv["brand_id"].map(brand_id_to_name)

st.subheader("Formulations")
st.dataframe(_dfv[["id","name","brand","category","product_type","notes"]].reset_index(drop=True), use_container_width=True, hide_index=True)

sel_ids = st.multiselect("Select formulation IDs to view details", _dfv["id"].dropna().astype(int).tolist())
if sel_ids:
    for fid in sel_ids:
        st.markdown(f"**Ingredients — Formulation ID {int(fid)}**")
        df_one = df_fi[df_fi["formulation_id"] == float(fid)]
        merged = df_one.merge(df_ings, left_on="ingredient_id", right_on="id", how="left", suffixes=("","_ing"))
        show = merged[["inci_name","common_name","function_ing","percentage","phase","notes"]].copy()
        show.columns = ["INCI","Common","Function","Percent","Phase","Notes"]
        st.dataframe(show.reset_index(drop=True), use_container_width=True, hide_index=True)

# Ingredient frequency
st.markdown("---")
st.subheader("Ingredient Frequency (within current filters)")
fi = df_fi.merge(df_forms, left_on="formulation_id", right_on="id", how="left", suffixes=("","_form"))
if sel_cat_q:
    fi = fi[fi["category"] == sel_cat_q]
if sel_ptype_q:
    fi = fi[fi["product_type"] == sel_ptype_q]

freq = fi.groupby("ingredient_id").agg(Count=("formulation_id","nunique")).reset_index()
freq = freq.merge(df_ings, left_on="ingredient_id", right_on="id", how="left")
freq = freq[["inci_name","common_name","function","Count"]].sort_values(["Count","inci_name"], ascending=[False, True])
st.dataframe(freq.reset_index(drop=True), use_container_width=True, hide_index=True)

# Typical list / rules
st.markdown("---")
st.subheader("Typical Ingredient List / Structure")
rule_key = (sel_cat_q or "Bodycare", sel_ptype_q or "Body Wash")
rules = RULES_BY_PRODUCT.get(rule_key)
if rules:
    base = pd.DataFrame(rules["base_structure"], columns=["Component", "Function", "Typical Range"])
    st.write("**Base Structure (guideline)**")
    st.dataframe(base, use_container_width=True, hide_index=True)
else:
    st.info("No embedded rules for this selection yet.")

# Surfactant recommender
st.markdown("---")
st.subheader("Recommended Surfactant Systems")
if rec_target == "Body Wash":
    candidates = BODY_WASH_RULES["surfactant_systems"]
    def score_system(sys):
        score = 0
        t = sys["tags"]
        if want_sulfate_free and "sulfate‑free" in t:
            score += 2
        if want_mild and "mild" in t:
            score += 2
        if want_high_foam and "high foam" in t:
            score += 1
        if not want_mild and "cost" in t:
            score += 1
        return score
    ranked = sorted(candidates, key=score_system, reverse=True)
    for sys in ranked:
        with st.expander(f"{sys['name']}  —  tags: {', '.join(sys['tags'])}"):
            st.write(pd.DataFrame(sys["combo"]))
else:
    st.info("Surfactant recommender currently optimized for Body Wash.")

# ------------------------------
# Add New Formulation (writes to Sheets)
# ------------------------------
st.markdown("---")
st.header("Add New Formulation → Google Sheets")
with st.form("add_formulation"):
    colA, colB, colC = st.columns(3)
    with colA:
        f_name = st.text_input("Formulation Name", placeholder="e.g., BW Sensitive 2025-09")
        f_category = st.selectbox("Category", ["Skincare","Bodycare","Haircare","Decorative","Fragrance","Other"], index=1)
        f_ptype = st.text_input("Product Type", value="Body Wash")
    with colB:
        all_brands = ["(new)"] + sorted(df_brands["name"].dropna().unique().tolist())
        sel_brand = st.selectbox("Brand", all_brands)
        new_brand_name = st.text_input("If new brand, type name here")
        f_notes = st.text_area("Notes", height=80)
    with colC:
        st.caption("Enter ingredient rows as a mini-table (INCI | Common | Function | % | Phase | Notes)")
        ing_text = st.text_area("Ingredients (one per line)",
            value=("Aqua | Water | Solvent | 60 | A\n"
                   "Sodium Laureth Sulfate | SLES | Surfactant | 10 | A\n"
                   "Cocamidopropyl Betaine | CAPB | Surfactant | 5 | A\n"
                   "Glycerin | Glycerin | Humectant | 3 | A"), height=160)

    submitted = st.form_submit_button("➕ Save to Google Sheets")

    if submitted:
        try:
            # Ensure brand id
            if sel_brand != "(new)":
                bid = int(df_brands.loc[df_brands["name"]==sel_brand, "id"].iloc[0])
            else:
                if not new_brand_name.strip():
                    st.error("Please enter a new brand name.")
                    st.stop()
                # new brand id = max + 1
                next_bid = 1 if df_brands.empty else int(pd.to_numeric(df_brands["id"], errors='coerce').max()) + 1
                df_append(ws_brands, {"id": next_bid, "name": new_brand_name.strip()})
                bid = next_bid
                df_brands.loc[len(df_brands)] = {"id": bid, "name": new_brand_name.strip()}

            # new formulation id
            next_fid = 1 if df_forms.empty else int(pd.to_numeric(df_forms["id"], errors='coerce').max()) + 1
            df_append(ws_forms, {
                "id": next_fid,
                "name": f_name.strip(),
                "brand_id": bid,
                "category": f_category.strip(),
                "product_type": f_ptype.strip(),
                "notes": f_notes.strip()
            })

            # parse ingredient lines
            next_ing_id_start = 1 if df_ings.empty else int(pd.to_numeric(df_ings["id"], errors='coerce').max()) + 1
            next_fi_id = 1 if df_fi.empty else int(pd.to_numeric(df_fi["id"], errors='coerce').max()) + 1
            ing_lines = [ln.strip() for ln in ing_text.splitlines() if ln.strip()]
            ing_id_map = {str(int(r["id"])): int(r["id"]) for _, r in df_ings.iterrows() if pd.notna(r["id"]) }

            for ln in ing_lines:
                parts = [p.strip() for p in ln.split("|")]
                if len(parts) < 4:
                    continue
                inci, common, func, pct = parts[0], parts[1], parts[2], parts[3]
                phase = parts[4] if len(parts) > 4 else ""
                notes = parts[5] if len(parts) > 5 else ""

                # lookup ingredient by INCI
                exists = df_ings[df_ings["inci_name"].str.lower()==inci.lower()] if not df_ings.empty else pd.DataFrame()
                if exists.empty:
                    ing_id = next_ing_id_start
                    next_ing_id_start += 1
                    df_append(ws_ings, {
                        "id": ing_id,
                        "inci_name": inci,
                        "common_name": common,
                        "function": func,
                        "cas": ""
                    })
                    # also update local df_ings
                    df_ings.loc[len(df_ings)] = {"id": ing_id, "inci_name": inci, "common_name": common, "function": func, "cas": ""}
                else:
                    ing_id = int(pd.to_numeric(exists.iloc[0]["id"]).item())

                # write FI row
                df_append(ws_fi, {
                    "id": next_fi_id,
                    "formulation_id": next_fid,
                    "ingredient_id": ing_id,
                    "percentage": pct,
                    "phase": phase,
                    "notes": notes
                })
                next_fi_id += 1

            st.success(f"Saved formulation '{f_name}' and ingredients to Google Sheets.")
            st.info("Refresh the app (Rerun) to see it reflected in tables above.")
        except Exception as e:
            st.error(f"Failed to save: {e}")

# ------------------------------
# Bulk Add Ingredients from INCI list
# ------------------------------
st.markdown("---")
st.header("Bulk Add Ingredients — INCI List → Google Sheets")
st.caption("Paste a comma-separated or newline-separated list of INCI names. We'll add them to the *Ingredients* tab with auto IDs. Other columns (common_name, function, cas) can be filled later.")
with st.form("bulk_ing_add"):
    inci_raw = st.text_area(
    "INCI list (comma or newline separated)",
    height=140,
    placeholder="Aqua, Dimethicone, Cyclopentasiloxane, Titanium Dioxide, Glycerin"
)
    dedup = st.checkbox("De-duplicate before adding", value=True)
    default_function = st.text_input("Default Function (optional)", value="")
    default_common = st.text_input("Default Common Name (optional)", value="")
    submitted_bulk = st.form_submit_button("➕ Add to Ingredients")

if submitted_bulk:
    try:
        # Split by comma/newline, strip whitespace
        tokens = []
for line in inci_raw.replace("\r", "\n").split("\n"):
    for part in line.split(","):
        name = part.strip()
        if name:
            tokens.append(name)
", "
").split("
"):
            for part in line.split(","):
                name = part.strip()
                if name:
                    tokens.append(name)
        if not tokens:
            st.warning("No INCI names detected.")
        else:
            if dedup:
                tokens = list(dict.fromkeys(tokens))  # order-preserving dedup
            # Build a set of existing INCI (case-insensitive) to avoid duplicates
            existing = set()
            if not df_ings.empty and "inci_name" in df_ings.columns:
                existing = set(df_ings["inci_name"].dropna().str.lower().tolist())

            next_ing_id = 1 if df_ings.empty else int(pd.to_numeric(df_ings["id"], errors='coerce').max()) + 1
            added = 0
            for inci in tokens:
                if inci.lower() in existing:
                    continue  # skip existing
                df_append(ws_ings, {
                    "id": next_ing_id,
                    "inci_name": inci,
                    "common_name": default_common,
                    "function": default_function,
                    "cas": ""
                })
                # reflect locally so we don't add twice in one run
                df_ings.loc[len(df_ings)] = {"id": next_ing_id, "inci_name": inci, "common_name": default_common, "function": default_function, "cas": ""}
                existing.add(inci.lower())
                next_ing_id += 1
                added += 1
            st.success(f"Added {added} ingredient(s) to Google Sheets.")
            if added == 0:
                st.info("Nothing new to add — everything already existed or input was empty.")
    except Exception as e:
        st.error(f"Failed to add ingredients: {e}")

# ------------------------------
# Footer notes
# ------------------------------
# ------------------------------
# Bulk Add Ingredients from INCI list
# ------------------------------
st.markdown("---")
st.header("Bulk Add Ingredients — INCI List → Google Sheets")
st.caption(
    "Paste a comma-separated or newline-separated list of INCI names. "
    "We'll add them to the *Ingredients* tab with auto IDs. "
    "Other columns (common_name, function, cas) can be filled later."
)

with st.form("bulk_ing_add"):
    inci_raw = st.text_area(
        "INCI list (comma or newline separated)",
        height=140,
        placeholder="Aqua, Dimethicone, Cyclopentasiloxane, Titanium Dioxide, Glycerin"
    )
    dedup = st.checkbox("De-duplicate before adding", value=True)
    default_function = st.text_input("Default Function (optional)", value="")
    default_common = st.text_input("Default Common Name (optional)", value="")
    submitted_bulk = st.form_submit_button("➕ Add to Ingredients")

if submitted_bulk:
    try:
        # Split by comma and/or newline, strip whitespace
        tokens = []
        for line in inci_raw.replace("\r", "\n").split("\n"):
            for part in line.split(","):
                name = part.strip()
                if name:
                    tokens.append(name)

        if not tokens:
            st.warning("No INCI names detected.")
        else:
            if dedup:
                tokens = list(dict.fromkeys(tokens))  # order-preserving dedup

            # Build a set of existing INCI (case-insensitive) to avoid duplicates
            existing = set()
            if not df_ings.empty and "inci_name" in df_ings.columns:
                existing = set(df_ings["inci_name"].dropna().str.lower().tolist())

            next_ing_id = 1 if df_ings.empty else int(pd.to_numeric(df_ings["id"], errors='coerce').max()) + 1
            added = 0
            for inci in tokens:
                if inci.lower() in existing:
                    continue  # skip existing
                df_append(ws_ings, {
                    "id": next_ing_id,
                    "inci_name": inci,
                    "common_name": default_common,
                    "function": default_function,
                    "cas": ""
                })
                # Update local df_ings
                df_ings.loc[len(df_ings)] = {
                    "id": next_ing_id,
                    "inci_name": inci,
                    "common_name": default_common,
                    "function": default_function,
                    "cas": ""
                }
                existing.add(inci.lower())
                next_ing_id += 1
                added += 1

            st.success(f"Added {added} ingredient(s) to Google Sheets.")
            if added == 0:
                st.info("Nothing new to add — everything already existed or input was empty.")
    except Exception as e:
        st.error(f"Failed to add ingredients: {e}")


# ------------------------------
# END
# ------------------------------
