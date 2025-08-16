import streamlit as st
import pandas as pd
import numpy as np
import datetime
from io import BytesIO

# ==========================
# CONFIGURATION
# ==========================
FILENAME = "reservations_multi.xlsx"

PLATFORM_COLORS_DEFAULT = {
    "Booking": "#1E90FF",
    "Airbnb": "#FF5A5F",
    "Abritel": "#32CD32",
    "Autre": "#A9A9A9",
}

# ==========================
# OUTILS
# ==========================
def load_data():
    try:
        df = pd.read_excel(FILENAME)
    except FileNotFoundError:
        df = pd.DataFrame(columns=[
            "appartement", "plateforme", "nom_client", "telephone",
            "date_arrivee", "date_depart", "nuitees",
            "prix_brut", "commissions", "frais_cb",
            "prix_net", "menage", "taxes_sejour",
            "base", "%"
        ])
    return df

def save_data(df: pd.DataFrame):
    df.to_excel(FILENAME, index=False)

def compute_calculs(df: pd.DataFrame) -> pd.DataFrame:
    """Met à jour les colonnes calculées : prix_net, base, %"""
    df["nuitees"] = (pd.to_datetime(df["date_depart"]) - pd.to_datetime(df["date_arrivee"])).dt.days
    df["prix_net"] = df["prix_brut"] - df["commissions"] - df["frais_cb"]
    df["base"] = df["prix_net"] - df["menage"] - df["taxes_sejour"]
    df["%"] = np.where(df["prix_brut"] > 0, 
                       ((df["commissions"] + df["frais_cb"]) / df["prix_brut"]) * 100, 
                       0).round(2)
    return df

# ==========================
# INTERFACES
# ==========================
def vue_reservations(df: pd.DataFrame):
    st.header("📋 Réservations")

    appart = st.selectbox("Choisir un appartement :", df["appartement"].unique())
    df_filtre = df[df["appartement"] == appart]

    st.dataframe(df_filtre, use_container_width=True)

def vue_ajouter(df: pd.DataFrame):
    st.header("➕ Ajouter une réservation")

    with st.form("ajouter_reservation"):
        appartement = st.text_input("Appartement")
        plateforme = st.selectbox("Plateforme", list(PLATFORM_COLORS_DEFAULT.keys()))
        nom_client = st.text_input("Nom du client")
        telephone = st.text_input("Téléphone")
        date_arrivee = st.date_input("Date arrivée", datetime.date.today())
        date_depart = st.date_input("Date départ", datetime.date.today() + datetime.timedelta(days=1))
        prix_brut = st.number_input("Prix brut", min_value=0.0, step=10.0)
        commissions = st.number_input("Commissions", min_value=0.0, step=1.0)
        frais_cb = st.number_input("Frais CB", min_value=0.0, step=1.0)
        menage = st.number_input("Ménage", min_value=0.0, step=1.0)
        taxes_sejour = st.number_input("Taxes séjour", min_value=0.0, step=1.0)

        submit = st.form_submit_button("Ajouter")

        if submit:
            new_row = pd.DataFrame([{
                "appartement": appartement,
                "plateforme": plateforme,
                "nom_client": nom_client,
                "telephone": telephone,
                "date_arrivee": date_arrivee,
                "date_depart": date_depart,
                "nuitees": (date_depart - date_arrivee).days,
                "prix_brut": prix_brut,
                "commissions": commissions,
                "frais_cb": frais_cb,
                "prix_net": prix_brut - commissions - frais_cb,
                "menage": menage,
                "taxes_sejour": taxes_sejour,
                "base": prix_brut - commissions - frais_cb - menage - taxes_sejour,
                "%": ((commissions + frais_cb) / prix_brut * 100) if prix_brut > 0 else 0
            }])
            df = pd.concat([df, new_row], ignore_index=True)
            save_data(df)
            st.success("Réservation ajoutée ✅")
            st.experimental_rerun()
# ==========================
# VUE CALENDRIER
# ==========================
def vue_calendrier(df: pd.DataFrame):
    st.header("📅 Calendrier")
    appart = st.selectbox("Choisir un appartement :", df["appartement"].unique())
    df_filtre = df[df["appartement"] == appart]

    if df_filtre.empty:
        st.info("Aucune réservation pour cet appartement.")
        return

    mois = st.selectbox("Mois", range(1, 13), format_func=lambda x: datetime.date(1900, x, 1).strftime('%B'))
    annee = st.number_input("Année", min_value=2020, max_value=2100, value=datetime.date.today().year)

    dates = pd.date_range(f"{annee}-{mois:02d}-01", periods=31, freq="D")
    grille = []
    for d in dates:
        resa = df_filtre[(df_filtre["date_arrivee"] <= d) & (df_filtre["date_depart"] > d)]
        if not resa.empty:
            platforme = resa.iloc[0]["plateforme"]
            nom = resa.iloc[0]["nom_client"]
            col = PLATFORM_COLORS_DEFAULT.get(platforme, "#AAAAAA")
            grille.append(f"{d.day}\n🟩 {nom}")
        else:
            grille.append(str(d.day))

    st.write(", ".join(grille))

# ==========================
# VUE RAPPORT
# ==========================
def vue_rapport(df: pd.DataFrame):
    st.header("📊 Rapport")

    appart = st.selectbox("Choisir un appartement :", df["appartement"].unique())
    df_filtre = df[df["appartement"] == appart]

    if df_filtre.empty:
        st.info("Aucune donnée pour cet appartement.")
        return

    total_brut = df_filtre["prix_brut"].sum()
    total_net = df_filtre["prix_net"].sum()
    total_base = df_filtre["base"].sum()
    total_charges = df_filtre["commissions"].sum() + df_filtre["frais_cb"].sum()
    commissions_moy = (total_charges / total_brut * 100) if total_brut > 0 else 0
    nuitees = df_filtre["nuitees"].sum()
    prix_moy_nuit = (total_brut / nuitees) if nuitees > 0 else 0

    st.metric("Total Brut", f"{total_brut:.2f} €")
    st.metric("Total Net", f"{total_net:.2f} €")
    st.metric("Base", f"{total_base:.2f} €")
    st.metric("Charges", f"{total_charges:.2f} €")
    st.metric("Commission Moy.", f"{commissions_moy:.2f} %")
    st.metric("Nuitées", f"{nuitees}")
    st.metric("Prix moyen/nuitée", f"{prix_moy_nuit:.2f} €")

# ==========================
# VUE CLIENTS
# ==========================
def vue_clients(df: pd.DataFrame):
    st.header("👥 Liste clients")

    appart = st.selectbox("Choisir un appartement :", df["appartement"].unique())
    df_filtre = df[df["appartement"] == appart]

    st.dataframe(df_filtre[["nom_client", "telephone", "plateforme"]])

# ==========================
# EXPORT ICS
# ==========================
def vue_export_ics(df: pd.DataFrame):
    st.header("📤 Export ICS")

    appart = st.selectbox("Choisir un appartement :", df["appartement"].unique())
    df_filtre = df[df["appartement"] == appart]

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Reservations//EN"]

    for _, r in df_filtre.iterrows():
        lines.append("BEGIN:VEVENT")
        lines.append(f"SUMMARY:{r['plateforme']} - {r['nom_client']}")
        lines.append(f"DTSTART;VALUE=DATE:{r['date_arrivee'].strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{r['date_depart'].strftime('%Y%m%d')}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    ics_content = "\n".join(lines)
    st.download_button("Télécharger ICS", data=ics_content, file_name="reservations.ics", mime="text/calendar")

# ==========================
# SMS
# ==========================
def vue_sms(df: pd.DataFrame):
    st.header("✉️ SMS")

    appart = st.selectbox("Choisir un appartement :", df["appartement"].unique())
    df_filtre = df[df["appartement"] == appart]

    for _, r in df_filtre.iterrows():
        st.subheader(f"{r['nom_client']} ({r['telephone']})")
        message = (
            f"{r['appartement']}\n"
            f"Plateforme : {r['plateforme']}\n"
            f"Date arrivée : {r['date_arrivee']} - Date départ : {r['date_depart']} - "
            f"Nuitées : {r['nuitees']}\n\n"
            f"Bonjour {r['nom_client']},\n\n"
            "Bienvenue chez nous ! Nous sommes ravis de vous accueillir bientôt. "
            "Pour organiser au mieux votre arrivée, pourriez-vous nous indiquer votre heure d'arrivée ? "
            "Une place de parking est disponible si besoin.\n\n"
            "Bon voyage !"
        )
        st.text_area("Message", message, height=180)

# ==========================
# MAIN
# ==========================
def main():
    st.sidebar.title("🧭 Navigation")
    onglet = st.sidebar.radio("Aller à", [
        "📋 Réservations",
        "➕ Ajouter",
        "📅 Calendrier",
        "📊 Rapport",
        "👥 Liste clients",
        "📤 Export ICS",
        "✉️ SMS"
    ])

    df = load_data()
    df = compute_calculs(df)

    if onglet == "📋 Réservations":
        vue_reservations(df)
    elif onglet == "➕ Ajouter":
        vue_ajouter(df)
    elif onglet == "📅 Calendrier":
        vue_calendrier(df)
    elif onglet == "📊 Rapport":
        vue_rapport(df)
    elif onglet == "👥 Liste clients":
        vue_clients(df)
    elif onglet == "📤 Export ICS":
        vue_export_ics(df)
    elif onglet == "✉️ SMS":
        vue_sms(df)

if __name__ == "__main__":
    main()