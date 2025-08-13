# app_multi.py — Gestion multi-appartements (application séparée)
# Dépendances : streamlit, pandas, numpy, openpyxl

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, timedelta, datetime, timezone
from io import BytesIO
import os
from urllib.parse import quote

FICHIER = "reservations_multi.xlsx"  # <-- fichier de cette app (indépendant)

# ==============================  OUTILS  ==============================

def to_date_only(x):
    if pd.isna(x) or x is None:
        return None
    try:
        return pd.to_datetime(x).date()
    except Exception:
        return None

def format_date_str(d):
    return d.strftime("%Y/%m/%d") if isinstance(d, date) else ""

def normalize_tel(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = str(x).strip().replace(" ", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = [
        "appartement",
        "nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "prix_brut","prix_net","charges","%",
        "AAAA","MM","ical_uid"
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=base_cols)

    df = df.copy()

    # Dates -> date (sans heure)
    for c in ["date_arrivee", "date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Numériques
    for c in ["prix_brut","prix_net","charges","%"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Calculs manquants
    if {"prix_brut","prix_net"}.issubset(df.columns):
        if "charges" not in df.columns:
            df["charges"] = df["prix_brut"] - df["prix_net"]
        if "%" not in df.columns:
            with pd.option_context("mode.use_inf_as_na", True):
                df["%"] = (df["charges"]/df["prix_brut"]*100).fillna(0)

    # Arrondis
    for c in ["prix_brut","prix_net","charges","%"]:
        if c in df.columns:
            df[c] = df[c].round(2)

    # Nuitées
    if {"date_arrivee","date_depart"}.issubset(df.columns):
        df["nuitees"] = [
            (d2 - d1).days if (isinstance(d1, date) and isinstance(d2, date)) else np.nan
            for d1, d2 in zip(df["date_arrivee"], df["date_depart"])
        ]

    # AAAA / MM
    if "date_arrivee" in df.columns:
        df["AAAA"] = df["date_arrivee"].apply(lambda d: d.year if isinstance(d, date) else np.nan).astype("Int64")
        df["MM"]   = df["date_arrivee"].apply(lambda d: d.month if isinstance(d, date) else np.nan).astype("Int64")

    # Défauts
    defaults = {
        "appartement":"Appartement A",
        "nom_client":"", "plateforme":"Autre", "telephone":"", "ical_uid":""
    }
    for k, v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Téléphone nettoyé
    if "telephone" in df.columns:
        df["telephone"] = df["telephone"].apply(normalize_tel)

    # Ordre de colonnes
    cols = base_cols
    return df[[c for c in cols if c in df.columns] + [c for c in df.columns if c not in cols]]

def is_total_row(row: pd.Series) -> bool:
    name_is_total = str(row.get("nom_client","")).strip().lower() == "total"
    pf_is_total   = str(row.get("plateforme","")).strip().lower() == "total"
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    no_dates = not isinstance(d1, date) and not isinstance(d2, date)
    has_money = any(pd.notna(row.get(c)) and float(row.get(c) or 0) != 0 for c in ["prix_brut","prix_net","charges"])
    return name_is_total or pf_is_total or (no_dates and has_money)

def split_totals(df: pd.DataFrame):
    if df is None or df.empty:
        return df, df
    mask = df.apply(is_total_row, axis=1)
    return df[~mask].copy(), df[mask].copy()

def sort_core(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in df.columns]
    return df.sort_values(by=by, na_position="last").reset_index(drop=True)

# ==============================  EXCEL I/O  ==============================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    return pd.read_excel(path, converters={"telephone": normalize_tel})

def charger_donnees() -> pd.DataFrame:
    if not os.path.exists(FICHIER):
        return ensure_schema(pd.DataFrame())
    try:
        mtime = os.path.getmtime(FICHIER)
        df = _read_excel_cached(FICHIER, mtime)
        return ensure_schema(df)
    except Exception as e:
        st.error(f"Erreur de lecture Excel : {e}")
        return ensure_schema(pd.DataFrame())

def _force_tel_text_openpyxl(writer, df_to_save: pd.DataFrame, sheet_name: str):
    try:
        ws = writer.sheets.get(sheet_name) or writer.sheets.get("Sheet1")
        if ws is None or "telephone" not in df_to_save.columns:
            return
        col_idx = df_to_save.columns.get_loc("telephone") + 1
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            row[0].number_format = '@'
    except Exception:
        pass

def sauvegarder_donnees(df: pd.DataFrame):
    df = ensure_schema(df)
    core, totals = split_totals(df)
    core = sort_core(core)
    out = pd.concat([core, totals], ignore_index=True)
    try:
        with pd.ExcelWriter(FICHIER, engine="openpyxl") as w:
            out.to_excel(w, index=False, sheet_name="Sheet1")
            _force_tel_text_openpyxl(w, out, "Sheet1")
        st.cache_data.clear()
        st.success("💾 Sauvegarde Excel effectuée.")
    except Exception as e:
        st.error(f"Échec de sauvegarde Excel : {e}")

def bouton_restaurer():
    up = st.sidebar.file_uploader("📤 Restauration xlsx", type=["xlsx"], help="Remplace le fichier actuel")
    if up is not None:
        try:
            df_new = pd.read_excel(up, converters={"telephone": normalize_tel})
            df_new = ensure_schema(df_new)
            sauvegarder_donnees(df_new)
            st.sidebar.success("✅ Fichier restauré.")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Erreur import: {e}")

def bouton_telecharger(df: pd.DataFrame):
    buf = BytesIO()
    try:
        ensure_schema(df).to_excel(buf, index=False, engine="openpyxl")
        data_xlsx = buf.getvalue()
    except Exception as e:
        st.sidebar.error(f"Export XLSX indisponible : {e}")
        data_xlsx = None
    st.sidebar.download_button(
        "💾 Sauvegarde xlsx",
        data=data_xlsx if data_xlsx else b"",
        file_name=FICHIER,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data_xlsx is None),
    )

# ==============================  UI / TOTAUX  ==============================

def chips_totaux(df: pd.DataFrame):
    if df.empty:
        return
    total_brut   = df["prix_brut"].sum(skipna=True)
    total_net    = df["prix_net"].sum(skipna=True)
    total_chg    = df["charges"].sum(skipna=True)
    total_nuits  = df["nuitees"].sum(skipna=True)
    pct_moy = (df["charges"].sum()/df["prix_brut"].sum()*100) if df["prix_brut"].sum() else 0
    html = f"""
<style>
.chips-wrap {{ display:flex; flex-wrap:wrap; gap:12px; margin:8px 0 16px 0; }}
.chip {{ padding:10px 12px; border-radius:10px; background: rgba(127,127,127,0.12); border: 1px solid rgba(127,127,127,0.25); }}
.chip b {{ display:block; margin-bottom:4px; }}
</style>
<div class="chips-wrap">
  <div class="chip"><b>Total Brut</b><div>{total_brut:,.2f} €</div></div>
  <div class="chip"><b>Total Net</b><div>{total_net:,.2f} €</div></div>
  <div class="chip"><b>Total Charges</b><div>{total_chg:,.2f} €</div></div>
  <div class="chip"><b>Total Nuitées</b><div>{int(total_nuits) if pd.notna(total_nuits) else 0}</div></div>
  <div class="chip"><b>Commission moy.</b><div>{pct_moy:.2f} %</div></div>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)

# ==============================  VUES  ==============================

def vue_reservations(df: pd.DataFrame):
    st.title("📋 Réservations (multi-appartements)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    # Filtres de haut de page
    app_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    pf_opts  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    col1, col2, col3 = st.columns(3)
    app_sel = col1.selectbox("Appartement", app_opts)
    pf_sel  = col2.selectbox("Plateforme", pf_opts)
    annee_opts = ["Toutes"] + sorted([int(x) for x in df["AAAA"].dropna().unique()])
    an_sel = col3.selectbox("Année", annee_opts)

    data = df.copy()
    if app_sel != "Tous":
        data = data[data["appartement"] == app_sel]
    if pf_sel != "Toutes":
        data = data[data["plateforme"] == pf_sel]
    if an_sel != "Toutes":
        data = data[data["AAAA"] == int(an_sel)]

    core, totals = split_totals(data)
    core = sort_core(core)

    chips_totaux(core)

    show = pd.concat([core, totals], ignore_index=True)
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)
    st.dataframe(show, use_container_width=True)

def vue_ajouter(df: pd.DataFrame):
    st.title("➕ Ajouter une réservation")

    col = st.columns(3)
    appartement = col[0].text_input("Appartement", value="Appartement A")
    plateforme  = col[1].selectbox("Plateforme", ["Booking","Airbnb","Autre"])
    nom         = col[2].text_input("Nom du client")

    col = st.columns(3)
    tel     = col[0].text_input("Téléphone (+33...)")
    arrivee = col[1].date_input("Arrivée", value=date.today())
    depart  = col[2].date_input("Départ",  value=arrivee + timedelta(days=1), min_value=arrivee + timedelta(days=1))

    col = st.columns(2)
    brut = col[0].number_input("Prix brut (€)", min_value=0.0, step=1.0, format="%.2f")
    net  = col[1].number_input("Prix net (€)",  min_value=0.0, step=1.0, format="%.2f")
    charges_calc = max(brut - net, 0.0)
    pct_calc     = (charges_calc/brut*100) if brut>0 else 0.0

    col = st.columns(2)
    col[0].number_input("Charges (€)", value=round(charges_calc,2), step=0.01, format="%.2f", disabled=True)
    col[1].number_input("Commission (%)", value=round(pct_calc,2), step=0.01, format="%.2f", disabled=True)

    if st.button("Enregistrer"):
        if net > brut:
            st.error("Le prix net ne peut pas être supérieur au prix brut.")
            return
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return

        ligne = {
            "appartement": (appartement or "").strip(),
            "nom_client": (nom or "").strip(),
            "plateforme": plateforme,
            "telephone": normalize_tel(tel),
            "date_arrivee": arrivee,
            "date_depart": depart,
            "prix_brut": float(brut),
            "prix_net": float(net),
            "charges": round(charges_calc, 2),
            "%": round(pct_calc, 2),
            "nuitees": (depart - arrivee).days,
            "AAAA": arrivee.year,
            "MM": arrivee.month,
            "ical_uid": ""
        }
        df2 = pd.concat([df, pd.DataFrame([ligne])], ignore_index=True)
        sauvegarder_donnees(df2)
        st.success("✅ Réservation enregistrée")
        st.rerun()

def vue_modifier(df: pd.DataFrame):
    st.title("✏️ Modifier / Supprimer")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune réservation.")
        return

    df["identifiant"] = df["appartement"].astype(str) + " | " + df["nom_client"].astype(str) + " | " + df["date_arrivee"].apply(format_date_str)
    choix = st.selectbox("Choisir une réservation", df["identifiant"])
    ixs = df.index[df["identifiant"] == choix]
    if len(ixs)==0:
        st.warning("Sélection invalide.")
        return
    i = ixs[0]

    col = st.columns(3)
    appartement = col[0].text_input("Appartement", df.at[i,"appartement"])
    plateforme  = col[1].selectbox("Plateforme", ["Booking","Airbnb","Autre"],
                                   index=(["Booking","Airbnb","Autre"].index(df.at[i,"plateforme"]) if df.at[i,"plateforme"] in ["Booking","Airbnb","Autre"] else 2))
    nom         = col[2].text_input("Nom du client", df.at[i,"nom_client"])

    col = st.columns(3)
    tel     = col[0].text_input("Téléphone", normalize_tel(df.at[i,"telephone"]))
    arrivee = col[1].date_input("Arrivée", df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())
    depart  = col[2].date_input("Départ",  df.at[i,"date_depart"] if isinstance(df.at[i,"date_depart"], date) else arrivee + timedelta(days=1),
                                min_value=arrivee + timedelta(days=1))

    col = st.columns(3)
    brut = col[0].number_input("Prix brut (€)", min_value=0.0, value=float(df.at[i,"prix_brut"]) if pd.notna(df.at[i,"prix_brut"]) else 0.0, step=1.0, format="%.2f")
    net  = col[1].number_input("Prix net (€)",  min_value=0.0, value=float(df.at[i,"prix_net"])  if pd.notna(df.at[i,"prix_net"])  else 0.0, step=1.0, format="%.2f")
    charges_calc = max(brut - net, 0.0)
    pct_calc     = (charges_calc/brut*100) if brut>0 else 0.0
    col[2].markdown(f"**Charges**: {charges_calc:.2f} €  \n**%**: {pct_calc:.2f}")

    c1, c2 = st.columns(2)
    if c1.button("💾 Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        df.at[i,"appartement"]  = appartement.strip()
        df.at[i,"nom_client"]   = nom.strip()
        df.at[i,"plateforme"]   = plateforme
        df.at[i,"telephone"]    = normalize_tel(tel)
        df.at[i,"date_arrivee"] = arrivee
        df.at[i,"date_depart"]  = depart
        df.at[i,"prix_brut"]    = float(brut)
        df.at[i,"prix_net"]     = float(net)
        df.at[i,"charges"]      = round(charges_calc, 2)
        df.at[i,"%"]            = round(pct_calc, 2)
        df.at[i,"nuitees"]      = (depart - arrivee).days
        df.at[i,"AAAA"]         = arrivee.year
        df.at[i,"MM"]           = arrivee.month
        df.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié")
        st.rerun()

    if c2.button("🗑 Supprimer"):
        df2 = df.drop(index=i)
        df2.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df2)
        st.warning("Supprimé.")
        st.rerun()

def vue_calendrier(df: pd.DataFrame):
    st.title("📅 Calendrier mensuel")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    app_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    c0, c1, c2 = st.columns(3)
    app_sel = c0.selectbox("Appartement", app_opts)
    mois_nom = c1.selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not annees:
        st.warning("Aucune année disponible.")
        return
    annee = c2.selectbox("Année", annees, index=len(annees)-1)

    data = df.copy()
    if app_sel != "Tous":
        data = data[data["appartement"] == app_sel]

    mois_index = list(calendar.month_name).index(mois_nom)
    nb_jours = calendar.monthrange(annee, mois_index)[1]
    jours = [date(annee, mois_index, j+1) for j in range(nb_jours)]
    planning = {j: [] for j in jours}
    couleurs = {"Booking":"🟦","Airbnb":"🟩","Autre":"🟧"}

    core, _ = split_totals(data)
    for _, row in core.iterrows():
        d1 = row["date_arrivee"]; d2 = row["date_depart"]
        if not (isinstance(d1, date) and isinstance(d2, date)):
            continue
        for j in jours:
            if d1 <= j < d2:
                ic = couleurs.get(row["plateforme"], "⬜")
                planning[j].append(f"{ic} {row['nom_client']}")

    table = []
    for semaine in calendar.monthcalendar(annee, mois_index):
        ligne = []
        for jour in semaine:
            if jour == 0:
                ligne.append("")
            else:
                d = date(annee, mois_index, jour)
                contenu = f"{jour}\n" + "\n".join(planning.get(d, []))
                ligne.append(contenu)
        table.append(ligne)

    st.table(pd.DataFrame(table, columns=["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"]))

def vue_rapport(df: pd.DataFrame):
    st.title("📊 Rapport (une année à la fois)")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    app_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    pf_opts  = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    c0, c1, c2, c3 = st.columns(4)
    app_sel = c0.selectbox("Appartement", app_opts)
    annee   = c1.selectbox("Année", annees, index=len(annees)-1)
    pf_sel  = c2.selectbox("Plateforme", pf_opts)
    mois_sel = c3.selectbox("Mois", ["Tous"]+[f"{i:02d}" for i in range(1,13)])

    data = df[df["AAAA"] == int(annee)].copy()
    if app_sel != "Tous":
        data = data[data["appartement"] == app_sel]
    if pf_sel != "Toutes":
        data = data[data["plateforme"] == pf_sel]
    if mois_sel != "Tous":
        data = data[data["MM"] == int(mois_sel)]

    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    # Détail (avec noms)
    detail = data.copy()
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(format_date_str)
    by = [c for c in ["appartement","date_arrivee","nom_client"] if c in detail.columns]
    if by:
        detail = detail.sort_values(by=by, na_position="last").reset_index(drop=True)

    cols_detail = ["appartement","nom_client","plateforme","telephone",
                   "date_arrivee","date_depart","nuitees","prix_brut","prix_net","charges","%"]
    cols_detail = [c for c in cols_detail if c in detail.columns]
    st.dataframe(detail[cols_detail], use_container_width=True)

    # Totaux
    core, _ = split_totals(data)
    chips_totaux(core)

    # Agrégats par mois/plateforme (remplis 1..12)
    stats = (
        data.groupby(["MM","plateforme"], dropna=True)
            .agg(prix_brut=("prix_brut","sum"),
                 prix_net=("prix_net","sum"),
                 charges=("charges","sum"),
                 nuitees=("nuitees","sum"))
            .reset_index()
            .sort_values(["MM","plateforme"])
    )

    def chart(metric_label, metric_col):
        if stats.empty: return
        pivot = stats.pivot(index="MM", columns="plateforme", values=metric_col).fillna(0)
        pivot = pivot.sort_index()
        pivot.index = [f"{int(m):02d}" for m in pivot.index]
        st.markdown(f"**{metric_label}**")
        st.bar_chart(pivot)

    chart("Revenus bruts", "prix_brut")
    chart("Revenus nets", "prix_net")
    chart("Nuitées", "nuitees")

    # Export XLSX du détail filtré
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        detail[cols_detail].to_excel(w, index=False)
    st.download_button(
        "⬇️ Télécharger le détail (XLSX)",
        data=buf.getvalue(),
        file_name=f"rapport_detail_{app_sel if app_sel!='Tous' else 'tous'}_{annee}{'' if mois_sel=='Tous' else '_'+mois_sel}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def vue_clients(df: pd.DataFrame):
    st.title("👥 Liste des clients")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    app_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    c0, c1, c2 = st.columns(3)
    app_sel = c0.selectbox("Appartement", app_opts)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee  = c1.selectbox("Année", annees, index=len(annees)-1) if annees else None
    mois   = c2.selectbox("Mois", ["Tous"]+[f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if app_sel != "Tous":
        data = data[data["appartement"] == app_sel]
    if annee:
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune donnée pour cette période.")
        return

    data["prix_brut/nuit"] = data.apply(lambda r: round((r["prix_brut"]/r["nuitees"]) if r["nuitees"] else 0,2), axis=1)
    data["prix_net/nuit"]  = data.apply(lambda r: round((r["prix_net"]/r["nuitees"])  if r["nuitees"] else 0,2), axis=1)

    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)

    cols = ["appartement","nom_client","plateforme","telephone","date_arrivee","date_depart",
            "nuitees","prix_brut","prix_net","charges","%","prix_brut/nuit","prix_net/nuit"]
    cols = [c for c in cols if c in show.columns]
    st.dataframe(show[cols], use_container_width=True)

    st.download_button(
        "📥 Télécharger (CSV)",
        data=show[cols].to_csv(index=False).encode("utf-8"),
        file_name="liste_clients.csv",
        mime="text/csv"
    )

def sms_message_arrivee(row: pd.Series) -> str:
    d1 = row.get("date_arrivee"); d2 = row.get("date_depart")
    d1s = d1.strftime("%Y/%m/%d") if isinstance(d1, date) else ""
    d2s = d2.strftime("%Y/%m/%d") if isinstance(d2, date) else ""
    nuitees = int(row.get("nuitees") or ((d2 - d1).days if isinstance(d1, date) and isinstance(d2, date) else 0))
    plateforme = str(row.get("plateforme") or "")
    nom = str(row.get("nom_client") or "")
    tel_aff = str(row.get("telephone") or "").strip()
    apt = str(row.get("appartement") or "")

    return (
        f"{apt}\n"
        f"Plateforme : {plateforme}\n"
        f"Date d'arrivee : {d1s}  Date depart : {d2s}  Nombre de nuitees : {nuitees}\n\n"
        f"Bonjour {nom}\n"
        f"Telephone : {tel_aff}\n\n"
        "Bienvenue chez nous !\n\n "
        "Nous sommes ravis de vous accueillir bientot. Pour organiser au mieux votre reception, pourriez-vous nous indiquer "
        "a quelle heure vous pensez arriver.\n\n "
        "Sachez egalement qu'une place de parking est a votre disposition dans l'immeuble, en cas de besoin.\n\n "
        "Nous vous souhaitons un excellent voyage et nous nous rejouissons de vous rencontrer.\n\n "
        "Annick & Charley"
    )

def sms_message_depart(row: pd.Series) -> str:
    nom = str(row.get("nom_client") or "")
    return (
        f"Bonjour {nom},\n\n"
        "Un grand merci d’avoir choisi notre appartement pour votre séjour ! "
        "Nous espérons que vous avez passé un moment aussi agréable que celui que nous avons eu à vous accueillir.\n\n"
        "Si l’envie vous prend de revenir explorer encore un peu notre ville (ou simplement retrouver le confort de notre petit cocon), "
        "sachez que notre porte vous sera toujours grande ouverte.\n\n"
        "Au plaisir de vous accueillir à nouveau,\n"
        "Annick & Charley"
    )

def vue_sms(df: pd.DataFrame):
    st.title("✉️ SMS — envoi manuel")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    app_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    app_sel = st.selectbox("Appartement", app_opts)

    data = df.copy()
    if app_sel != "Tous":
        data = data[data["appartement"] == app_sel]

    today = date.today()
    demain = today + timedelta(days=1)
    hier = today - timedelta(days=1)

    colA, colB = st.columns(2)

    with colA:
        st.subheader("📆 Arrivées demain")
        arrives = data[data["date_arrivee"] == demain].copy()
        if arrives.empty:
            st.info("Aucune arrivée demain.")
        else:
            for idx, r in arrives.reset_index(drop=True).iterrows():
                body = sms_message_arrivee(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""
                st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
                st.markdown(f"Arrivée: {format_date_str(r.get('date_arrivee'))} • Départ: {format_date_str(r.get('date_depart'))}")
                st.code(body)
                c1, c2 = st.columns(2)
                if tel_link: c1.link_button(f"📞 Appeler {tel}", tel_link)
                if sms_link: c2.link_button("📩 Envoyer SMS", sms_link)
                st.divider()

    with colB:
        st.subheader("🕒 Relance +24h après départ")
        dep_24h = data[data["date_depart"] == hier].copy()
        if dep_24h.empty:
            st.info("Aucun départ hier.")
        else:
            for idx, r in dep_24h.reset_index(drop=True).iterrows():
                body = sms_message_depart(r)
                tel = normalize_tel(r.get("telephone"))
                tel_link = f"tel:{tel}" if tel else ""
                sms_link = f"sms:{tel}?&body={quote(body)}" if tel and body else ""
                st.markdown(f"**{r.get('nom_client','')}** — {r.get('plateforme','')} — {r.get('appartement','')}")
                st.code(body)
                c1, c2 = st.columns(2)
                if tel_link: c1.link_button(f"📞 Appeler {tel}", tel_link)
                if sms_link: c2.link_button("📩 Envoyer SMS", sms_link)
                st.divider()

# ==============================  APP  ==============================

def main():
    st.set_page_config(page_title="🏠 Multi-Appartements", layout="wide")

    # Fichier (sauvegarde/restauration)
    st.sidebar.title("📁 Fichier")
    df_tmp = charger_donnees()
    bouton_telecharger(df_tmp)
    bouton_restaurer()

    # Navigation
    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        ["📋 Réservations","➕ Ajouter","✏️ Modifier / Supprimer",
         "📅 Calendrier","📊 Rapport","👥 Liste clients","✉️ SMS"]
    )

    # Maintenance
    st.sidebar.markdown("---")
    if st.sidebar.button("♻️ Vider le cache et relancer"):
        try: st.cache_data.clear()
        except Exception: pass
        st.sidebar.success("Cache vidé.")
        st.rerun()

    df = charger_donnees()

    if onglet == "📋 Réservations":
        vue_reservations(df)
    elif onglet == "➕ Ajouter":
        vue_ajouter(df)
    elif onglet == "✏️ Modifier / Supprimer":
        vue_modifier(df)
    elif onglet == "📅 Calendrier":
        vue_calendrier(df)
    elif onglet == "📊 Rapport":
        vue_rapport(df)
    elif onglet == "👥 Liste clients":
        vue_clients(df)
    elif onglet == "✉️ SMS":
        vue_sms(df)

if __name__ == "__main__":
    main()

