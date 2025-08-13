# app_multi.py — Villa Tobias (Multi-appartements)
# Fichier indépendant de l'app actuelle (utilise reservations_multi.xlsx)

import streamlit as st
import pandas as pd
import numpy as np
import calendar
from datetime import date, timedelta
from io import BytesIO
import os

FICHIER = "reservations_multi.xlsx"

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
    """Lire/écrire les téléphones comme texte, conserver + et éviter le .0."""
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

    # Dates -> date
    for c in ["date_arrivee", "date_depart"]:
        if c in df.columns:
            df[c] = df[c].apply(to_date_only)

    # Numériques
    for c in ["prix_brut", "prix_net", "charges", "%"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Calcul charges/% si manquants
    if "prix_brut" in df.columns and "prix_net" in df.columns:
        if "charges" not in df.columns:
            df["charges"] = df["prix_brut"] - df["prix_net"]
        if "%" not in df.columns:
            with pd.option_context("mode.use_inf_as_na", True):
                df["%"] = (df["charges"] / df["prix_brut"] * 100).fillna(0)

    # Arrondis
    for c in ["prix_brut","prix_net","charges","%"]:
        if c in df.columns:
            df[c] = df[c].round(2)

    # Nuitées
    if "date_arrivee" in df.columns and "date_depart" in df.columns:
        df["nuitees"] = [
            (d2 - d1).days if (isinstance(d1, date) and isinstance(d2, date)) else np.nan
            for d1, d2 in zip(df["date_arrivee"], df["date_depart"])
        ]

    # AAAA / MM
    if "date_arrivee" in df.columns:
        df["AAAA"] = df["date_arrivee"].apply(lambda d: d.year if isinstance(d, date) else np.nan).astype("Int64")
        df["MM"]   = df["date_arrivee"].apply(lambda d: d.month if isinstance(d, date) else np.nan).astype("Int64")

    # Défauts
    defaults = {"appartement":"Appartement A","nom_client":"", "plateforme":"Autre", "telephone":"", "ical_uid":""}
    for k,v in defaults.items():
        if k not in df.columns:
            df[k] = v

    # Téléphone en texte propre
    if "telephone" in df.columns:
        df["telephone"] = df["telephone"].apply(normalize_tel)

    # Colonnes ordonnées
    cols = base_cols
    return df[[c for c in cols if c in df.columns] + [c for c in df.columns if c not in cols]]

def sort_core(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    by = [c for c in ["appartement", "date_arrivee", "nom_client"] if c in df.columns]
    return df.sort_values(by=by, na_position="last").reset_index(drop=True)

# ==============================  EXCEL I/O  ==============================

@st.cache_data(show_spinner=False)
def _read_excel_cached(path: str, mtime: float):
    # Important: convertisseur 'telephone' pour éviter 1.0 et conserver +33
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

def _force_telephone_text_format_openpyxl(writer, df_to_save: pd.DataFrame, sheet_name: str):
    """Après to_excel, force le format texte '@' sur la colonne 'telephone' si présente."""
    try:
        ws = writer.sheets.get(sheet_name) or writer.sheets.get('Sheet1')
        if ws is None or "telephone" not in df_to_save.columns:
            return
        col_idx = df_to_save.columns.get_loc("telephone") + 1  # 1-based
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            cell = row[0]
            cell.number_format = '@'
    except Exception:
        pass

def sauvegarder_donnees(df: pd.DataFrame):
    df = ensure_schema(df)
    df = sort_core(df)
    try:
        with pd.ExcelWriter(FICHIER, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="Reservations")
            _force_telephone_text_format_openpyxl(w, df, "Reservations")
        st.cache_data.clear()
        st.success("💾 Sauvegarde Excel effectuée.")
    except Exception as e:
        st.error(f"Échec de sauvegarde Excel : {e}")

def bouton_restaurer():
    up = st.sidebar.file_uploader("📤 Restauration xlsx (multi)", type=["xlsx"], help="Remplace le fichier actuel")
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
        "💾 Sauvegarde xlsx (multi)",
        data=data_xlsx if data_xlsx else b"",
        file_name="reservations_multi.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=(data_xlsx is None),
    )

# ==============================  VUES  ==============================

def vue_reservations(df: pd.DataFrame):
    st.title("🏠 Réservations (multi-appartements)")
    if df.empty:
        st.info("Aucune donnée.")
        return

    top = st.columns([1.2, 1, 1, 1])
    with top[0]:
        appart_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
        appart = st.selectbox("Appartement", appart_opts)
    with top[1]:
        pf_opts = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
        pf = st.selectbox("Plateforme", pf_opts)
    with top[2]:
        annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
        annee = st.selectbox("Année", ["Toutes"] + annees) if annees else "Toutes"
    with top[3]:
        mois = st.selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if appart != "Tous":
        data = data[data["appartement"] == appart]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if annee != "Toutes":
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune réservation pour ces filtres.")
        return

    # Totaux (sur les lignes filtrées)
    total_brut   = data["prix_brut"].sum(skipna=True)
    total_net    = data["prix_net"].sum(skipna=True)
    total_chg    = data["charges"].sum(skipna=True)
    total_nuits  = data["nuitees"].sum(skipna=True)
    pct_moy = (data["charges"].sum() / data["prix_brut"].sum() * 100) if data["prix_brut"].sum() else 0

    st.markdown(
        f"""
        <style>
        .chips {{ display:flex; gap:12px; flex-wrap:wrap; margin:6px 0 12px 0; }}
        .chip {{ padding:8px 12px; border-radius:10px; background: rgba(127,127,127,0.12); border: 1px solid rgba(127,127,127,0.25); }}
        .chip b {{ display:block; margin-bottom:2px; }}
        </style>
        <div class="chips">
          <div class="chip"><b>Total Brut</b><div>{total_brut:,.2f} €</div></div>
          <div class="chip"><b>Total Net</b><div>{total_net:,.2f} €</div></div>
          <div class="chip"><b>Total Charges</b><div>{total_chg:,.2f} €</div></div>
          <div class="chip"><b>Total Nuitées</b><div>{int(total_nuits) if pd.notna(total_nuits) else 0}</div></div>
          <div class="chip"><b>Commission moy.</b><div>{pct_moy:.2f} %</div></div>
        </div>
        """,
        unsafe_allow_html=True
    )

    show = data.copy()
    for c in ["date_arrivee","date_depart"]:
        show[c] = show[c].apply(format_date_str)
    show = show.sort_values(["appartement","date_arrivee","nom_client"], na_position="last")
    st.dataframe(
        show[[
            "appartement","nom_client","plateforme","telephone",
            "date_arrivee","date_depart","nuitees",
            "prix_brut","prix_net","charges","%"
        ]],
        use_container_width=True
    )

def vue_ajouter(df: pd.DataFrame):
    st.title("➕ Ajouter une réservation")

    col = st.columns([1.2, 1])
    appart = col[0].text_input("Appartement", value="Appartement A")
    pf = col[1].selectbox("Plateforme", ["Booking","Airbnb","Autre"], index=0)

    c = st.columns(2)
    nom = c[0].text_input("Nom client")
    tel = c[1].text_input("Téléphone (+33...)", value="")

    c2 = st.columns(2)
    arrivee = c2[0].date_input("Arrivée", value=date.today())
    depart  = c2[1].date_input("Départ", value=date.today() + timedelta(days=1), min_value=arrivee + timedelta(days=1))

    c3 = st.columns(2)
    brut = c3[0].number_input("Prix brut (€)", min_value=0.0, step=1.0, format="%.2f")
    net  = c3[1].number_input("Prix net (€)",  min_value=0.0, step=1.0, format="%.2f")

    charges = max(brut - net, 0.0)
    pct = (charges / brut * 100) if brut > 0 else 0.0
    c4 = st.columns(2)
    c4[0].number_input("Charges (€)", value=round(charges,2), step=0.01, format="%.2f", disabled=True)
    c4[1].number_input("Commission (%)", value=round(pct,2), step=0.01, format="%.2f", disabled=True)

    if st.button("Enregistrer"):
        if net > brut:
            st.error("Le prix net ne peut pas être supérieur au prix brut.")
            return
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return

        ligne = {
            "appartement": (appart or "Appartement A").strip(),
            "nom_client": (nom or "").strip(),
            "plateforme": pf,
            "telephone": normalize_tel(tel),
            "date_arrivee": arrivee,
            "date_depart": depart,
            "prix_brut": float(brut),
            "prix_net": float(net),
            "charges": round(charges, 2),
            "%": round(pct, 2),
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
    idx = df.index[df["identifiant"] == choix]
    if len(idx) == 0:
        st.warning("Sélection invalide.")
        return
    i = idx[0]

    col = st.columns([1.2, 1, 1])
    appart = col[0].text_input("Appartement", df.at[i, "appartement"])
    pf = col[1].selectbox("Plateforme", ["Booking","Airbnb","Autre"],
                          index = ["Booking","Airbnb","Autre"].index(df.at[i,"plateforme"]) if df.at[i,"plateforme"] in ["Booking","Airbnb","Autre"] else 2)
    tel = col[2].text_input("Téléphone", normalize_tel(df.at[i, "telephone"]))

    c = st.columns(2)
    nom = c[0].text_input("Nom client", df.at[i, "nom_client"])
    arrivee = c[1].date_input("Arrivée", df.at[i,"date_arrivee"] if isinstance(df.at[i,"date_arrivee"], date) else date.today())

    c2 = st.columns(2)
    min_dep = arrivee + timedelta(days=1)
    depart  = c2[0].date_input("Départ", df.at[i,"date_depart"] if isinstance(df.at[i,"date_depart"], date) else min_dep, min_value=min_dep)

    brut = c2[1].number_input("Prix brut (€)", min_value=0.0, value=float(df.at[i,"prix_brut"]) if pd.notna(df.at[i,"prix_brut"]) else 0.0, step=1.0, format="%.2f")
    c3 = st.columns(2)
    net  = c3[0].number_input("Prix net (€)",  min_value=0.0, value=float(df.at[i,"prix_net"]) if pd.notna(df.at[i,"prix_net"]) else 0.0, step=1.0, format="%.2f")
    charges = max(brut - net, 0.0)
    pct = (charges / brut * 100) if brut > 0 else 0.0
    c3[1].markdown(f"**Charges**: {charges:.2f} €  \n**%**: {pct:.2f}")

    c4 = st.columns(2)
    if c4[0].button("💾 Enregistrer"):
        if depart < arrivee + timedelta(days=1):
            st.error("La date de départ doit être au moins le lendemain de l’arrivée.")
            return
        df.at[i,"appartement"] = (appart or "Appartement A").strip()
        df.at[i,"plateforme"]  = pf
        df.at[i,"telephone"]   = normalize_tel(tel)
        df.at[i,"nom_client"]  = nom.strip()
        df.at[i,"date_arrivee"] = arrivee
        df.at[i,"date_depart"]  = depart
        df.at[i,"prix_brut"] = float(brut)
        df.at[i,"prix_net"]  = float(net)
        df.at[i,"charges"]   = round(charges, 2)
        df.at[i,"%"]         = round(pct, 2)
        df.at[i,"nuitees"]   = (depart - arrivee).days
        df.at[i,"AAAA"]      = arrivee.year
        df.at[i,"MM"]        = arrivee.month
        df.drop(columns=["identifiant"], inplace=True, errors="ignore")
        sauvegarder_donnees(df)
        st.success("✅ Modifié")
        st.rerun()

    if c4[1].button("🗑 Supprimer"):
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

    top = st.columns([1.2, 1, 1])
    appart_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    appart = top[0].selectbox("Appartement", appart_opts)
    mois_nom = top[1].selectbox("Mois", list(calendar.month_name)[1:], index=max(0, date.today().month-1))
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    if not annees:
        st.warning("Aucune année disponible.")
        return
    annee = top[2].selectbox("Année", annees, index=len(annees)-1)

    data = df.copy()
    if appart != "Tous":
        data = data[data["appartement"] == appart]

    mois_index = list(calendar.month_name).index(mois_nom)
    nb_jours = calendar.monthrange(annee, mois_index)[1]
    jours = [date(annee, mois_index, j+1) for j in range(nb_jours)]
    planning = {j: [] for j in jours}
    couleurs = {"Booking":"🟦","Airbnb":"🟩","Autre":"🟧"}

    for _, row in data.iterrows():
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
    st.title("📊 Rapport")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    top = st.columns([1.2, 1, 1, 1])
    appart_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    appart = top[0].selectbox("Appartement", appart_opts)
    pf_opts = ["Toutes"] + sorted(df["plateforme"].dropna().unique().tolist())
    pf = top[1].selectbox("Plateforme", pf_opts)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = top[2].selectbox("Année", ["Toutes"] + annees) if annees else "Toutes"
    mois = top[3].selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,12+1)])

    data = df.copy()
    if appart != "Tous":
        data = data[data["appartement"] == appart]
    if pf != "Toutes":
        data = data[data["plateforme"] == pf]
    if annee != "Toutes":
        data = data[data["AAAA"] == int(annee)]
    if mois != "Tous":
        data = data[data["MM"] == int(mois)]

    if data.empty:
        st.info("Aucune donnée pour ces filtres.")
        return

    # Détail visible (inclut les noms)
    detail = data.copy().sort_values(["appartement","date_arrivee","nom_client"])
    for c in ["date_arrivee","date_depart"]:
        detail[c] = detail[c].apply(format_date_str)
    cols_detail = [
        "appartement","nom_client","plateforme","telephone",
        "date_arrivee","date_depart","nuitees",
        "prix_brut","prix_net","charges","%"
    ]
    cols_detail = [c for c in cols_detail if c in detail.columns]
    st.dataframe(detail[cols_detail], use_container_width=True)

    # Totaux
    total_brut   = data["prix_brut"].sum(skipna=True)
    total_net    = data["prix_net"].sum(skipna=True)
    total_chg    = data["charges"].sum(skipna=True)
    total_nuits  = data["nuitees"].sum(skipna=True)
    pct_moy = (data["charges"].sum() / data["prix_brut"].sum() * 100) if data["prix_brut"].sum() else 0

    st.markdown(
        f"""
        <style>
        .chips {{ display:flex; gap:12px; flex-wrap:wrap; margin:6px 0 12px 0; }}
        .chip {{ padding:8px 12px; border-radius:10px; background: rgba(127,127,127,0.12); border: 1px solid rgba(127,127,127,0.25); }}
        .chip b {{ display:block; margin-bottom:2px; }}
        </style>
        <div class="chips">
          <div class="chip"><b>Total Brut</b><div>{total_brut:,.2f} €</div></div>
          <div class="chip"><b>Total Net</b><div>{total_net:,.2f} €</div></div>
          <div class="chip"><b>Total Charges</b><div>{total_chg:,.2f} €</div></div>
          <div class="chip"><b>Total Nuitées</b><div>{int(total_nuits) if pd.notna(total_nuits) else 0}</div></div>
          <div class="chip"><b>Commission moy.</b><div>{pct_moy:.2f} %</div></div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Agrégations mensuelles (1..12) -> graphes
    stats = (
        data.groupby(["MM","plateforme"], dropna=True)
            .agg(prix_brut=("prix_brut","sum"),
                 prix_net=("prix_net","sum"),
                 charges=("charges","sum"),
                 nuitees=("nuitees","sum"))
            .reset_index()
    )
    stats = stats.sort_values(["MM","plateforme"]).reset_index(drop=True)

    def chart_of(metric_label, metric_col):
        if stats.empty:
            return
        pivot = stats.pivot(index="MM", columns="plateforme", values=metric_col).fillna(0)
        pivot = pivot.sort_index()
        pivot.index = [f"{int(m):02d}" for m in pivot.index]
        st.markdown(f"**{metric_label}**")
        st.bar_chart(pivot)

    chart_of("Revenus bruts", "prix_brut")
    chart_of("Revenus nets", "prix_net")
    chart_of("Nuitées", "nuitees")

    # Export XLSX du détail filtré
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        detail[cols_detail].to_excel(writer, index=False, sheet_name="Détail")
    st.download_button(
        "⬇️ Télécharger le détail (XLSX)",
        data=buf.getvalue(),
        file_name="rapport_multi_detail.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def vue_clients(df: pd.DataFrame):
    st.title("👥 Liste des clients")
    df = ensure_schema(df)
    if df.empty:
        st.info("Aucune donnée.")
        return

    top = st.columns([1.2, 1, 1])
    appart_opts = ["Tous"] + sorted(df["appartement"].dropna().unique().tolist())
    appart = top[0].selectbox("Appartement", appart_opts)
    annees = sorted([int(x) for x in df["AAAA"].dropna().unique()])
    annee = top[1].selectbox("Année", ["Toutes"] + annees) if annees else "Toutes"
    mois  = top[2].selectbox("Mois", ["Tous"] + [f"{i:02d}" for i in range(1,13)])

    data = df.copy()
    if appart != "Tous":
        data = data[data["appartement"] == appart]
    if annee != "Toutes":
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
        file_name="clients_multi.csv",
        mime="text/csv"
    )

# ==============================  APP  ==============================

def main():
    st.set_page_config(page_title="📖 Réservations Multi-appartements", layout="wide")

    # Barre latérale : Fichier (Sauvegarde / Restauration)
    st.sidebar.title("📁 Fichier")
    df_tmp = charger_donnees()
    bouton_telecharger(df_tmp)
    bouton_restaurer()

    # Navigation
    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio(
        "Aller à",
        ["🏠 Réservations","➕ Ajouter","✏️ Modifier / Supprimer","📅 Calendrier","📊 Rapport","👥 Liste clients"]
    )

    # Recharger après éventuelle restauration
    df = charger_donnees()

    if onglet == "🏠 Réservations":
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

if __name__ == "__main__":
    main()
