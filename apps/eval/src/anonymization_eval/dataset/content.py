"""Clinical content pools. Eponyms, drug names and scores are traps: they look
like person names but must NOT be redacted."""

from typing import TypedDict


class Scenario(TypedDict):
    """A hospital stay: ward, diagnosis, course, therapy and lab results."""

    ward: str
    diagnosis: str
    secondary: list[str]
    history: str
    course: list[str]
    # drug, dose, time
    therapy: list[tuple[str, str, str]]
    # test, value, unit, reference range, flag (H, L or "")
    labs: list[tuple[str, str, str, str, str]]
    followup: str


class PsCase(TypedDict):
    """An emergency department visit."""

    # triage code and its colour (RGB, 0-1)
    triage: tuple[str, tuple[float, float, float]]
    complaint: str
    exam: str
    tests: str
    dx: str
    outcome: str
    # time, administration
    tx: list[tuple[str, str]]


EPONYM_SENTENCES = {
    "Pacini": "All'esame obiettivo sensibilità vibratoria ridotta, compatibile con alterata funzione dei corpuscoli di Pacini.",
    "Morgagni": "Alla TC si conferma piccola ernia di Morgagni, già nota e stabile.",
    "Santorini": "Il dotto di Santorini appare pervio, senza dilatazioni.",
    "Corti": "Nota ipoacusia neurosensoriale da danno dell'organo del Corti.",
    "Golgi": "Esame istologico con studio dell'apparato di Golgi non dirimente.",
    "Malpighi": "Biopsia renale: glomeruli e corpuscoli del Malpighi nei limiti.",
    "Forlanini": "Pregresso pneumotorace trattato secondo metodo di Forlanini in età giovanile.",
    "Ruffini": "Parestesie distali con alterazione dei corpuscoli di Ruffini.",
}

GENERIC_EPONYMS = [
    "Segno di Murphy negativo.",
    "Segno di Blumberg negativo.",
    "Manovra di Giordano negativa bilateralmente.",
    "Riflesso di Babinski assente bilateralmente.",
    "Glasgow Coma Scale 15/15.",
    "Pressione arteriosa misurata con metodo Riva-Rocci.",
    "Manovra di Lasègue negativa.",
    "Segno di Romberg negativo.",
    "Non segni di Kernig né di Brudzinski.",
]

SCENARIOS: list[Scenario] = [
    {
        "ward": "Medicina Interna",
        "diagnosis": "Riacutizzazione di BPCO (GOLD 3) con insufficienza respiratoria ipossiemica lieve.",
        "secondary": [
            "Ipertensione arteriosa",
            "Diabete mellito tipo 2 in terapia orale",
            "Malattia di Parkinson in fase iniziale",
        ],
        "history": "Ex fumatore (40 pack/year). BPCO nota dal 2015 in terapia inalatoria con tiotropio. "
        "Diagnosi di malattia di Parkinson nel 2022, in terapia con levodopa/carbidopa.",
        "course": [
            "All'ingresso dispnea a riposo con SpO2 87% in aria ambiente. L'emogasanalisi documentava ipossiemia "
            "senza ipercapnia significativa. Radiografia del torace senza addensamenti parenchimali.",
            "Impostata terapia con broncodilatatori per via aerosolica, metilprednisolone 40 mg/die e ossigenoterapia "
            "a basso flusso, con progressivo miglioramento degli scambi respiratori.",
            "Durante la degenza buon compenso glicemico con modifica della terapia ipoglicemizzante. "
            "Al momento della dimissione SpO2 94% in aria ambiente, paziente apiretico ed emodinamicamente stabile.",
        ],
        "therapy": [
            ("Tiotropio 2,5 mcg", "2 inalazioni", "ore 8"),
            ("Salmeterolo/fluticasone 50/500", "1 inalazione", "ore 8 e 20"),
            ("Prednisone 25 mg", "1 cp a scalare", "ore 8"),
            ("Metformina 1000 mg", "1 cp", "ore 8 e 20"),
            ("Levodopa/carbidopa 100/25 mg", "1 cp", "ore 8, 14 e 20"),
            ("Ramipril 5 mg", "1 cp", "ore 8"),
        ],
        "labs": [
            ("Emoglobina", "13,1", "g/dL", "13,5 - 17,5", "L"),
            ("Leucociti", "11,8", "x10^3/µL", "4,0 - 10,0", "H"),
            ("PCR", "3,4", "mg/dL", "< 0,5", "H"),
            ("Creatinina", "1,12", "mg/dL", "0,70 - 1,20", ""),
            ("eGFR (CKD-EPI)", "62", "mL/min", "> 60", ""),
            ("Glicemia", "168", "mg/dL", "70 - 100", "H"),
        ],
        "followup": "Controllo spirometrico a 4 settimane. Visita pneumologica di controllo tra 3 mesi.",
    },
    {
        "ward": "Cardiologia",
        "diagnosis": "Scompenso cardiaco acuto in cardiopatia ischemica cronica (FE 35%), classe NYHA III.",
        "secondary": [
            "Fibrillazione atriale permanente in TAO",
            "Insufficienza renale cronica stadio 3b",
            "Tiroidite di Hashimoto",
        ],
        "history": "Pregresso IMA anteriore (2019) trattato con PTCA e stent su IVA. Fibrillazione atriale permanente "
        "in terapia con warfarin. Tiroidite di Hashimoto in terapia sostitutiva con levotiroxina.",
        "course": [
            "Giunto per dispnea ingravescente ed edemi declivi. All'ecocardiogramma FE 35% con insufficienza mitralica "
            "moderata. NT-proBNP 6.840 pg/mL.",
            "Avviata terapia diuretica endovenosa con furosemide con calo ponderale di 4 kg e risoluzione degli edemi. "
            "Ottimizzata la terapia con sacubitril/valsartan e dapagliflozin.",
            "INR in range terapeutico durante la degenza. Funzione renale stabile.",
        ],
        "therapy": [
            ("Furosemide 25 mg", "2 cp", "ore 8"),
            ("Sacubitril/valsartan 49/51 mg", "1 cp", "ore 8 e 20"),
            ("Bisoprololo 2,5 mg", "1 cp", "ore 8"),
            ("Dapagliflozin 10 mg", "1 cp", "ore 8"),
            ("Warfarin 5 mg", "secondo INR", "ore 18"),
            ("Levotiroxina 75 mcg", "1 cp", "ore 7 a digiuno"),
        ],
        "labs": [
            ("NT-proBNP", "6.840", "pg/mL", "< 450", "H"),
            ("Creatinina", "1,65", "mg/dL", "0,70 - 1,20", "H"),
            ("eGFR (CKD-EPI)", "38", "mL/min", "> 60", "L"),
            ("Potassio", "4,9", "mmol/L", "3,5 - 5,1", ""),
            ("INR", "2,6", "", "2,0 - 3,0", ""),
            ("TSH", "2,1", "mUI/L", "0,4 - 4,0", ""),
        ],
        "followup": "Controllo INR entro 5 giorni. Visita cardiologica con ecocardiogramma a 1 mese. Peso giornaliero.",
    },
    {
        "ward": "Gastroenterologia",
        "diagnosis": "Riacutizzazione di morbo di Crohn ileo-colico (CDAI 290) con anemia sideropenica.",
        "secondary": ["Pregressa resezione ileo-cecale (2018)", "Osteopenia"],
        "history": "Morbo di Crohn diagnosticato nel 2012, in terapia con adalimumab dal 2020. "
        "Pregressa resezione ileo-cecale nel 2018.",
        "course": [
            "Ricoverato per dolore addominale in fossa iliaca destra, diarrea (6-8 scariche/die) e calo ponderale. "
            "Segno di Blumberg negativo. Calprotectina fecale 980 µg/g.",
            "L'entero-RM documentava ispessimento dell'ultima ansa ileale per circa 15 cm senza raccolte. "
            "Avviata terapia steroidea endovenosa con buona risposta clinica.",
            "Eseguita infusione di ferro carbossimaltosio. Programmato switch terapeutico a ustekinumab.",
        ],
        "therapy": [
            ("Prednisone 25 mg", "secondo schema a scalare", "ore 8"),
            ("Pantoprazolo 20 mg", "1 cp", "ore 7"),
            ("Colecalciferolo 25.000 UI", "1 fl", "settimanale"),
            ("Mesalazina 800 mg", "2 cp", "ore 8 e 20"),
        ],
        "labs": [
            ("Emoglobina", "9,8", "g/dL", "12,0 - 16,0", "L"),
            ("Ferritina", "8", "ng/mL", "15 - 150", "L"),
            ("PCR", "5,2", "mg/dL", "< 0,5", "H"),
            ("Albumina", "3,1", "g/dL", "3,5 - 5,2", "L"),
            ("Calprotectina fecale", "980", "µg/g", "< 50", "H"),
        ],
        "followup": "Visita presso ambulatorio MICI tra 3 settimane con esami ematici. Prima dose di ustekinumab in regime di DH.",
    },
    {
        "ward": "Neurologia",
        "diagnosis": "Ictus ischemico in territorio dell'arteria cerebrale media sinistra (NIHSS 6 all'ingresso, 2 alla dimissione).",
        "secondary": [
            "Fibrillazione atriale di nuova diagnosi",
            "Ipertensione arteriosa",
            "Dislipidemia",
        ],
        "history": "Ipertensione arteriosa in terapia con amlodipina. Non precedenti eventi cerebrovascolari.",
        "course": [
            "Giunto in PS per afasia ed emiparesi destra insorte da circa 2 ore. Eseguita trombolisi endovenosa con "
            "alteplase. Riflesso di Babinski presente a destra all'ingresso.",
            "La TC encefalo di controllo a 24 ore documentava lesione ischemica fronto-insulare sinistra senza "
            "trasformazione emorragica. Al monitoraggio ECG riscontro di fibrillazione atriale parossistica.",
            "Avviata terapia anticoagulante con apixaban in quinta giornata. Scala di Rankin modificata 2 alla dimissione.",
        ],
        "therapy": [
            ("Apixaban 5 mg", "1 cp", "ore 8 e 20"),
            ("Atorvastatina 40 mg", "1 cp", "ore 20"),
            ("Amlodipina 5 mg", "1 cp", "ore 8"),
            ("Bisoprololo 1,25 mg", "1 cp", "ore 8"),
        ],
        "labs": [
            ("Colesterolo LDL", "142", "mg/dL", "< 70", "H"),
            ("HbA1c", "5,9", "%", "4,0 - 6,0", ""),
            ("Creatinina", "0,94", "mg/dL", "0,70 - 1,20", ""),
            ("Piastrine", "231", "x10^3/µL", "150 - 400", ""),
        ],
        "followup": "Riabilitazione logopedica ambulatoriale. Visita neurologica a 3 mesi con ecocolordoppler TSA.",
    },
    {
        "ward": "Nefrologia",
        "diagnosis": "Insufficienza renale acuta su cronica (KDIGO 2) da disidratazione e FANS, in diabete tipo 2.",
        "secondary": [
            "Diabete mellito tipo 2",
            "Sindrome di Cushing iatrogena pregressa",
            "Gotta",
        ],
        "history": "Diabete mellito tipo 2 dal 2010 in terapia con metformina e insulina basale. Recente assunzione "
        "di ibuprofene per gonalgia.",
        "course": [
            "Giunto per astenia, oliguria e creatinina 3,8 mg/dL (basale 1,6). Sospesi metformina, ramipril e FANS.",
            "Idratazione endovenosa con progressivo recupero della diuresi e della funzione renale. "
            "Manovra di Giordano negativa. Ecografia renale: reni di dimensioni nei limiti, non idronefrosi.",
            "Alla dimissione creatinina 1,9 mg/dL. Reintrodotta terapia insulinica con schema basal-bolus.",
        ],
        "therapy": [
            ("Insulina glargine", "18 UI", "ore 22"),
            ("Insulina lispro", "secondo schema", "ai pasti"),
            ("Allopurinolo 100 mg", "1 cp", "ore 8"),
            ("Bicarbonato di sodio 500 mg", "2 cp", "ore 8 e 20"),
        ],
        "labs": [
            ("Creatinina", "1,90", "mg/dL", "0,70 - 1,20", "H"),
            ("eGFR (CKD-EPI)", "34", "mL/min", "> 60", "L"),
            ("Potassio", "5,4", "mmol/L", "3,5 - 5,1", "H"),
            ("Urea", "92", "mg/dL", "15 - 50", "H"),
            ("HbA1c", "8,4", "%", "4,0 - 6,0", "H"),
        ],
        "followup": "Controllo funzione renale ed elettroliti a 1 settimana. Evitare FANS. Visita nefrologica a 1 mese.",
    },
]

RELATIONS_M = ["il figlio", "il marito", "il fratello", "il nipote"]
RELATIONS_F = ["la figlia", "la moglie", "la sorella", "la nipote", "la badante"]

LAB_PANELS = {
    "EMATOLOGIA": [
        ("Leucociti", "x10^3/µL", (4.0, 10.0)),
        ("Eritrociti", "x10^6/µL", (4.2, 5.8)),
        ("Emoglobina", "g/dL", (13.0, 17.0)),
        ("Ematocrito", "%", (40.0, 52.0)),
        ("MCV", "fL", (80.0, 99.0)),
        ("Piastrine", "x10^3/µL", (150, 400)),
        ("Neutrofili", "%", (40.0, 75.0)),
        ("Linfociti", "%", (20.0, 45.0)),
        ("Monociti", "%", (2.0, 10.0)),
    ],
    "CHIMICA CLINICA": [
        ("Glicemia", "mg/dL", (70, 100)),
        ("Creatinina", "mg/dL", (0.7, 1.2)),
        ("eGFR (CKD-EPI)", "mL/min", (60, 120)),
        ("Urea", "mg/dL", (15, 50)),
        ("Sodio", "mmol/L", (136, 145)),
        ("Potassio", "mmol/L", (3.5, 5.1)),
        ("AST", "U/L", (5, 40)),
        ("ALT", "U/L", (5, 41)),
        ("Gamma GT", "U/L", (8, 61)),
        ("Bilirubina totale", "mg/dL", (0.3, 1.2)),
        ("Colesterolo totale", "mg/dL", (120, 200)),
        ("Colesterolo HDL", "mg/dL", (40, 90)),
        ("Trigliceridi", "mg/dL", (50, 150)),
        ("Acido urico", "mg/dL", (3.4, 7.0)),
        ("PCR", "mg/dL", (0.0, 0.5)),
    ],
    "COAGULAZIONE": [
        ("PT INR", "", (0.8, 1.2)),
        ("aPTT ratio", "", (0.8, 1.2)),
        ("Fibrinogeno", "mg/dL", (200, 400)),
    ],
    "ENDOCRINOLOGIA": [
        ("TSH", "mUI/L", (0.4, 4.0)),
        ("FT4", "ng/dL", (0.9, 1.7)),
        ("HbA1c", "%", (4.0, 6.0)),
    ],
    "URINE": [
        ("Peso specifico", "", (1005, 1030)),
        ("pH", "", (5.0, 7.5)),
        ("Proteine", "mg/dL", (0, 15)),
        ("Glucosio", "mg/dL", (0, 15)),
    ],
}

RADIOLOGY = [
    {
        "exam": "TC TORACE E ADDOME SENZA E CON MEZZO DI CONTRASTO",
        "question": "Calo ponderale e anemia in paziente ex fumatore. Escludere neoplasia.",
        "technique": "Esame eseguito con apparecchiatura multistrato prima e dopo somministrazione endovenosa di mezzo "
        "di contrasto iodato (iopamidolo 370, 90 mL), con acquisizioni in fase arteriosa e portale.",
        "findings": [
            "Torace: enfisema centrolobulare prevalente ai lobi superiori. Nodulo solido di 7 mm al segmento apicale "
            "del lobo superiore destro, da monitorare. Non versamento pleurico. Linfonodi mediastinici non aumentati.",
            "Addome: fegato di dimensioni regolari, senza lesioni focali. Colecisti alitiasica, segno di Murphy "
            "ecografico non valutabile in TC. Pancreas nei limiti; il dotto di Wirsung non è dilatato.",
            "Reni in sede, di dimensioni normali, senza idronefrosi. Non adenopatie addominali. Diverticolosi del sigma.",
        ],
        "conclusion": "Nodulo polmonare solido di 7 mm del LSD: si consiglia controllo TC a 6-12 mesi secondo criteri "
        "Fleischner. Enfisema centrolobulare. Non evidenti lesioni addominali di natura secondaria.",
    },
    {
        "exam": "RM ENCEFALO SENZA E CON MEZZO DI CONTRASTO",
        "question": "Cefalea di recente insorgenza e vertigini. Escludere lesioni espansive.",
        "technique": "Sequenze FLAIR, T2, DWI, SWI e T1 prima e dopo gadolinio (gadobutrolo 7,5 mL).",
        "findings": [
            "Sistema ventricolare in asse, di ampiezza nei limiti. Alcune iperintensità puntiformi della sostanza "
            "bianca sottocorticale frontale bilaterale, di verosimile natura vascolare cronica (Fazekas 1).",
            "Non restrizione della diffusione. Non alterazioni di segnale nelle sequenze SWI. "
            "Dopo contrasto non aree di patologica impregnazione. Poligono di Willis pervio.",
        ],
        "conclusion": "Modeste alterazioni della sostanza bianca di natura vascolare (Fazekas 1). Non lesioni espansive.",
    },
    {
        "exam": "ECOGRAFIA ADDOME COMPLETO",
        "question": "Dolore in ipocondrio destro e rialzo degli indici di colestasi.",
        "technique": "Esame eseguito con sonda convex 3,5 MHz.",
        "findings": [
            "Fegato di dimensioni ai limiti superiori, ecostruttura iperecogena da steatosi di grado moderato.",
            "Colecisti distesa con multipli calcoli, il maggiore di 14 mm, pareti ispessite (5 mm). Segno di Murphy "
            "ecografico positivo. Via biliare principale di calibro regolare (5 mm).",
            "Pancreas parzialmente esplorabile per meteorismo. Milza e reni nei limiti.",
        ],
        "conclusion": "Quadro compatibile con colecistite acuta litiasica. Steatosi epatica moderata.",
    },
]

PS_CASES: list[PsCase] = [
    {
        "triage": ("ARANCIONE", (1.0, 0.6, 0.15)),
        "complaint": "Dolore toracico oppressivo insorto a riposo da circa 1 ora.",
        "exam": "Paziente vigile, sudato. PA (Riva-Rocci) 165/95 mmHg, FC 98 bpm, SpO2 95%. Toni cardiaci validi, "
        "non soffi. Murmure vescicolare conservato.",
        "tests": "ECG: sottoslivellamento ST in V4-V6. Troponina hs 54 ng/L, al controllo 3 ore 212 ng/L.",
        "dx": "NSTEMI",
        "outcome": "Ricovero in Unità Coronarica.",
        "tx": [
            ("ore 10:42", "Acido acetilsalicilico 250 mg ev"),
            ("ore 10:50", "Fondaparinux 2,5 mg sc"),
            ("ore 11:05", "Nitroglicerina in infusione"),
        ],
    },
    {
        "triage": ("VERDE", (0.25, 0.65, 0.3)),
        "complaint": "Trauma distorsivo caviglia destra durante attività sportiva.",
        "exam": "Tumefazione ed ecchimosi perimalleolare esterna. Dolorabilità alla palpazione del malleolo peroneale. "
        "Polsi periferici presenti. Segno del cassetto anteriore negativo.",
        "tests": "RX caviglia destra: non lesioni ossee traumatiche evidenti.",
        "dx": "Distorsione di II grado della caviglia destra",
        "outcome": "Dimesso con indicazioni.",
        "tx": [
            ("ore 16:20", "Ketoprofene 100 mg im"),
            ("ore 16:35", "Bendaggio funzionale"),
        ],
    },
    {
        "triage": ("ROSSO", (0.85, 0.12, 0.12)),
        "complaint": "Dispnea ingravescente e febbre da 3 giorni, desaturazione.",
        "exam": "Tachipnoico (FR 30/min), SpO2 84% in aria ambiente. Crepitii basali bilaterali. Glasgow Coma Scale 14.",
        "tests": "EGA: pO2 52 mmHg, pCO2 34 mmHg, lattati 2,9. RX torace: addensamenti bilaterali. Procalcitonina 4,1 ng/mL.",
        "dx": "Polmonite bilaterale con insufficienza respiratoria acuta, sepsi",
        "outcome": "Ricovero in Terapia Intensiva.",
        "tx": [
            ("ore 02:15", "Ossigenoterapia ad alti flussi FiO2 60%"),
            ("ore 02:30", "Piperacillina/tazobactam 4,5 g ev"),
            ("ore 02:35", "Cristalloidi 1000 mL ev"),
        ],
    },
]

REFERRAL_REASONS = [
    (
        "Nefrologia",
        "rivalutazione dell'insufficienza renale cronica (eGFR in calo da 52 a 38 mL/min negli ultimi 6 mesi)",
    ),
    (
        "Cardiologia",
        "valutazione di cardiopalmo e riscontro di fibrillazione atriale all'ECG eseguito in studio",
    ),
    (
        "Endocrinologia",
        "nodulo tiroideo di 18 mm (TI-RADS 4) riscontrato ecograficamente, in tiroidite di Hashimoto",
    ),
    (
        "Neurologia",
        "tremore a riposo all'arto superiore destro e bradicinesia, sospetta malattia di Parkinson",
    ),
    ("Gastroenterologia", "diarrea cronica e calo ponderale, sospetto morbo di Crohn"),
]

CERT_REASONS = [
    ("lombosciatalgia acuta destra", 7),
    ("sindrome influenzale con febbre", 5),
    ("gastroenterite acuta", 3),
    ("distorsione della caviglia sinistra", 10),
    ("bronchite acuta", 6),
]

PRESCRIPTIONS = [
    [("ATORVASTATINA 20MG 30 CPR", "1"), ("RAMIPRIL 5MG 28 CPR", "2")],
    [("METFORMINA 1000MG 60 CPR RIV", "2")],
    [("APIXABAN 5MG 56 CPR", "1"), ("PANTOPRAZOLO 20MG 28 CPR", "1")],
    [("FUROSEMIDE 25MG 30 CPR", "1"), ("BISOPROLOLO 2,5MG 28 CPR", "1")],
]
