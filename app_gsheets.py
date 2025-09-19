# app_gsheets.py — Formulation Knowledge Base (Google Sheets backend)
# -----------------------------------------------------------------
# Features
# - Google Sheets as the database (tabs: Brands, Formulations, Ingredients, Formulation_Ingredients)
# - Browse by Category / Product Type / Brand
# - Ingredient frequency view
# - Typical structure rules + surfactant recommender (Body Wash, Facial Cleanser)
# - Add New Formulation (writes into Sheets)
# - Bulk Add Ingredients from a pasted INCI list (comma/newline separated)
# - One-click Diagnostics to troubleshoot connection/auth
#
# How secrets should look (local: .streamlit/secrets.toml, Cloud: App → Settings → Secrets):
# [gsheets]
# spreadsheet_id = "<YOUR_SHEET_ID>"
# [gsheets.service_account]
# type = "service_account"
# project_id = "..."
# private_key_id = "..."
# private_key = """
# -----BEGIN PRIVATE KEY-----
# (multi-line key exactly as downloaded)
# -----END PRIVATE KEY-----
# """
# client_email = "...@...iam.gserviceaccount.com"
# client_id = "..."
# auth_uri = "https://accounts.google.com/o/oauth2/auth"
# token_uri = "https://oauth2.googleapis.com/token"
# auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
# client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
# -----------------------------------------------------------------

import json
from typing import List, Dict, Any

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# ------------------------------
# UI Setup
# ------------------------------
st.set_page_config(page_title="Formulation KB — Sheets", layout="wide")
st.title("🧪 Formulation Knowledge Base — Google Sheets")

# ------------------------------
# Helpers — Rules
# ------------------------------
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
# Google Sheets Client
# ------------------------------

def get_client():
    cfg = st.secrets.get("gsheets")
    if not cfg:
        st.error("Secrets missing: [gsheets] section not found.")
        st.stop()

    sa = cfg.get("service_account")
    if sa is None:
        st.error("Secrets missing: gsheets.service_account")
        st.stop()

    if isinstance(sa, str):
        try:
            info = json.loads(sa)
        except Exception as e:
            st.error(f"service_account JSON malformed: {e}")
            st.stop()
    else:
        info = sa

    sid = cfg.get("spreadsheet_id")
    if not sid or "/" in sid:
        st.error("Use ONLY the spreadsheet ID (the long string between /d/ and /edit), not the whole URL.")
        st.stop()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc, sid, info


def open_sheet(gc, ssid, tab):
    sh = gc.open_by_key(ssid)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=1000, cols=20)
        ws.append_row(TEMPLATE[tab])
    return ws


def ws_to_df(ws) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=[])
    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    for col in ["id", "brand_id", "percentage", "ingredient_id", "formulation_id"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def df_append(ws, row: Dict[str, Any]):
    headers = ws.row_values(1)
    out = [str(row.get(h, "")) for h in headers]
    ws.append_row(out)

# Template headers for auto-creation
TEMPLATE = {
    "Brands": ["id", "name"],
    "Formulations": ["id", "name", "brand_id", "category", "product_type", "notes"],
    "Ingredients": ["id", "inci_name", "common_name", "function", "cas"],
    "Formulation_Ingredients": ["id", "formulation_id", "ingredient_id", "percentage", "phase", "notes"],
}

# ------------------------------
# Diagnostics
# ------------------------------
with st.expander("🧪 Run Google Sheets Diagnostics"):
    if st.button("Run Diagnostics", use_container_width=True):
        try:
            gc, SSID, sa_info = get_client()
            st.write("✅ secrets loaded; spreadsheet_id:", (SSID[:6] + "…"))
            st.write("✅ service account:", sa_info.get("client_email", "(no email)") )
            sh = gc.open_by_key(SSID)
            tabs = [ws.title for ws in sh.worksheets()]
            st.write("✅ Opened spreadsheet. Tabs:", tabs)
        except Exception as e:
            st.error(f"❌ Google Sheets connection failed: {e}")

# ------------------------------
# Load DataFrames
# ------------------------------
try:
    gc, SSID, sa_info = get_client()
    sh = gc.open_by_key(SSID)
    ws_brands = sh.worksheet("Brands") if "Brands" in [w.title for w in sh.worksheets()] else sh.add_worksheet("Brands", 1000, 20)
    ws_forms = sh.worksheet("Formulations") if "Formulations" in [w.title for w in sh.worksheets()] else sh.add_worksheet("Formulations", 2000, 20)
    ws_ings = sh.worksheet("Ingredients") if "Ingredients" in [w.title for w in sh.worksheets()] else sh.add_worksheet("Ingredients", 5000, 20)
    ws_fi = sh.worksheet("Formulation_Ingredients") if "Formulation_Ingredients" in [w.title for w in sh.worksheets()] else sh.add_worksheet("Formulation_Ingredients", 10000, 20)

    # Ensure headers exist
    if not ws_brands.row_values(1): ws_brands.append_row(TEMPLATE["Brands"])
    if not ws_forms.row_values(1): ws_forms.append_row(TEMPLATE["Formulations"])
    if not ws_ings.row_values(1): ws_ings.append_row(TEMPLATE["Ingredients"])
    if not ws_fi.row_values(1): ws_fi.append_row(TEMPLATE["Formulation_Ingredients"])

    df_brands = ws_to_df(ws_brands)
    df_forms = ws_to_df(ws_forms)
    df_ings = ws_to_df(ws_ings)
    df_fi = ws_to_df(ws_fi)
except Exception as e:
    st.error(f"❌ Google Sheets connection failed: {e}")
    st.stop()

# ------------------------------
# Sidebar Filters & Recommender switches
# ------------------------------
with st.sidebar:
    st.header("Filters")
    cats = ["(All)"] + sorted([c for c in df_forms.get("category", pd.Series(dtype=str)).dropna().unique().tolist() if c])
    sel_cat = st.selectbox("Category", cats)
    sel_cat_q = None if sel_cat == "(All)" else sel_cat

    if sel_cat_q:
        ptypes = ["(All)"] + sorted(df_forms[df_forms["category"]==sel_cat_q]["product_type"].dropna().unique().tolist())
    else:
        ptypes = ["(All)"] + sorted(df_forms.get("product_type", pd.Series(dtype=str)).dropna().unique().tolist())
    sel_ptype = st.selectbox("Product Type", ptypes)
    sel_ptype_q = None if sel_ptype == "(All)" else sel_ptype

    brands = ["(All)"] + sorted(df_brands.get("name", pd.Series(dtype=str)).dropna().unique().tolist())
    sel_brand = st.selectbox("Brand", brands)
    sel_brand_q = None if sel_brand == "(All)" else sel_brand

    st.markdown("---")
    st.subheader("Surfactant Recommender")
    rec_target = st.selectbox("Target Product", ["Body Wash", "Facial Cleanser"])
    want_sulfate_free = st.checkbox("Sulfate‑free preference")
    want_mild = st.checkbox("Prioritize mildness")
    want_high_foam = st.checkbox("High Foam")

# ------------------------------
# Browse Formulations
# ------------------------------
_dfv = df_forms.copy()
if sel_cat_q: _dfv = _dfv[_dfv["category"] == sel_cat_q]
if sel_ptype_q: _dfv = _dfv[_dfv["product_type"] == sel_ptype_q]
if sel_brand_q:
    # map brand name → id
    name_to_id = {r["name"]: int(r["id"]) for _, r in df_brands.iterrows() if pd.notna(r.get("id")) and pd.notna(r.get("name"))}
    bid = name_to_id.get(sel_brand_q)
    if bid is not None:
        _dfv = _dfv[_dfv["brand_id"].astype(float) == float(bid)]

id_to_name = {int(r["id"]): r["name"] for _, r in df_brands.iterrows() if pd.notna(r.get("id")) and pd.notna(r.get("name"))}
_dfv["brand"] = _dfv.get("brand_id", pd.Series(dtype=float)).map(lambda v: id_to_name.get(int(v), "") if pd.notna(v) else "")

st.subheader("Formulations")
st.dataframe(_dfv[["id","name","brand","category","product_type","notes"]].reset_index(drop=True), use_container_width=True, hide_index=True)

sel_ids = st.multiselect("Select formulation IDs to view details", _dfv.get("id", pd.Series(dtype=float)).dropna().astype(int).tolist())
if sel_ids:
    for fid in sel_ids:
        st.markdown(f"**Ingredients — Formulation ID {int(fid)}**")
        df_one = df_fi[df_fi["formulation_id"].astype(float) == float(fid)]
        merged = df_one.merge(df_ings, left_on="ingredient_id", right_on="id", how="left", suffixes=("","_ing"))
        show = merged[["inci_name","common_name","function","percentage","phase","notes"]].copy()
        show.columns = ["INCI","Common","Function","Percent","Phase","Notes"]
        st.dataframe(show.reset_index(drop=True), use_container_width=True, hide_index=True)

# ------------------------------
# Ingredient Frequency
# ------------------------------
st.markdown("---")
st.subheader("Ingredient Frequency (within current filters)")
fi = df_fi.merge(df_forms, left_on="formulation_id", right_on="id", how="left", suffixes=("","_form"))
if sel_cat_q: fi = fi[fi["category"] == sel_cat_q]
if sel_ptype_q: fi = fi[fi["product_type"] == sel_ptype_q]

freq = fi.groupby("ingredient_id").agg(Count=("formulation_id","nunique")).reset_index()
freq = freq.merge(df_ings, left_on="ingredient_id", right_on="id", how="left")
freq = freq[["inci_name","common_name","function","Count"]].sort_values(["Count","inci_name"], ascending=[False, True])
st.dataframe(freq.reset_index(drop=True), use_container_width=True, hide_index=True)

# ------------------------------
# Typical Ingredient List / Structure
# ------------------------------
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

# ------------------------------
# Surfactant Recommender
# ------------------------------
st.markdown("---")
st.subheader("Recommended Surfactant Systems")
if rec_target == "Body Wash":
    candidates = BODY_WASH_RULES["surfactant_systems"]
    def score_system(sys):
        score = 0
        t = sys["tags"]
        if want_sulfate_free and "sulfate‑free" in t: score += 2
        if want_mild and "mild" in t: score += 2
        if want_high_foam and "high foam" in t: score += 1
        if not want_mild and "cost" in t: score += 1
        return score
    ranked = sorted(candidates, key=score_system, reverse=True)
    for sys in ranked:
        with st.expander(f"{sys['name']}  —  tags: {', '.join(sys['tags'])}"):
            st.write(pd.DataFrame(sys["combo"]))
else:
    st.info("Surfactant recommender currently optimized for Body Wash.")

# ------------------------------
# Add New Formulation → Google Sheets
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
        all_brands = ["(new)"] + sorted(df_brands.get("name", pd.Series(dtype=str)).dropna().unique().tolist())
        sel_brand = st.selectbox("Brand", all_brands)
        new_brand_name = st.text_input("If new brand, type name here")
        f_notes = st.text_area("Notes", height=80)
    with colC:
        st.caption("Enter ingredient rows as text: INCI | Common | Function | % | Phase | Notes (one per line)")
        ing_text = st.text_area("Ingredients (one per line)",
            value=("""Aqua | Water | Solvent | 60 | A
"
                   "Sodium Laureth Sulfate | SLES | Surfactant | 10 | A
"
                   "Cocamidopropyl Betaine | CAPB | Surfactant | 5 | A
"
                   "Glycerin | Glycerin | Humectant | 3 | A"""), height=160)

    submitted = st.form_submit_button("➕ Save to Google Sheets")

    if submitted:
        try:
            # Ensure brand id
            if sel_brand != "(new)":
                bid = int(df_brands.loc[df_brands["name"]==sel_brand, "id"].iloc[0]) if not df_brands.empty else 1
            else:
                if not new_brand_name.strip():
                    st.error("Please enter a new brand name.")
                    st.stop()
                next_bid = 1 if df_brands.empty else int(pd.to_numeric(df_brands["id"], errors='coerce').max()) + 1
                df_append(ws_brands, {"id": next_bid, "name": new_brand_name.strip()})
                df_brands.loc[len(df_brands)] = {"id": next_bid, "name": new_brand_name.strip()}
                bid = next_bid

            next_fid = 1 if df_forms.empty else int(pd.to_numeric(df_forms["id"], errors='coerce').max()) + 1
            df_append(ws_forms, {
                "id": next_fid,
                "name": f_name.strip(),
                "brand_id": bid,
                "category": f_category.strip(),
                "product_type": f_ptype.strip(),
                "notes": f_notes.strip()
            })

            next_ing_id_start = 1 if df_ings.empty else int(pd.to_numeric(df_ings["id"], errors='coerce').max()) + 1
            next_fi_id = 1 if df_fi.empty else int(pd.to_numeric(df_fi["id"], errors='coerce').max()) + 1
            ing_lines = [ln.strip() for ln in ing_text.splitlines() if ln.strip()]

            for ln in ing_lines:
                parts = [p.strip() for p in ln.split("|")]
                if len(parts) < 4:
                    continue
                inci, common, func, pct = parts[0], parts[1], parts[2], parts[3]
                phase = parts[4] if len(parts) > 4 else ""
                notes = parts[5] if len(parts) > 5 else ""

                exists = df_ings[df_ings.get("inci_name", pd.Series(dtype=str)).str.lower()==inci.lower()] if not df_ings.empty else pd.DataFrame()
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
                    df_ings.loc[len(df_ings)] = {"id": ing_id, "inci_name": inci, "common_name": common, "function": func, "cas": ""}
                else:
                    ing_id = int(pd.to_numeric(exists.iloc[0]["id"]).item()) if pd.notna(exists.iloc[0]["id"]) else int(pd.to_numeric(df_ings["id"], errors='coerce').max())

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
            st.info("Click Rerun to refresh tables above.")
        except Exception as e:
            st.error(f"Failed to save: {e}")

# ------------------------------
# Bulk Add Ingredients from INCI list
# ------------------------------
st.markdown("---")
st.header("Bulk Add Ingredients — INCI List → Google Sheets")
st.caption(
    "Paste a comma-separated or newline-separated list of INCI names. "
    "We'll add them to the Ingredients tab with auto IDs. "
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
        tokens = []
        for line in inci_raw.replace("
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
                tokens = list(dict.fromkeys(tokens))
            existing = set()
            if not df_ings.empty and "inci_name" in df_ings.columns:
                existing = set(df_ings["inci_name"].dropna().str.lower().tolist())
            next_ing_id = 1 if df_ings.empty else int(pd.to_numeric(df_ings["id"], errors='coerce').max()) + 1
            added = 0
            for inci in tokens:
                if inci.lower() in existing:
                    continue
                df_append(ws_ings, {
                    "id": next_ing_id,
                    "inci_name": inci,
                    "common_name": default_common,
                    "function": default_function,
                    "cas": ""
                })
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
# Footer Notes
# ------------------------------
st.markdown(
    "> **Notes**
"
    "> - Maintain unique integer IDs in each tab. This app auto-increments when writing.
"
    "> - You can edit data directly in Google Sheets; the app will read changes on refresh.
"
    "> - Add more product rules by extending RULES_BY_PRODUCT (e.g., Shampoo, Body Lotion, Sunscreen).
"
)
