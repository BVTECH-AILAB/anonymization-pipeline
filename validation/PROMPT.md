# Prompt: synthetic validation dataset for the anonymization pipeline

You are generating a synthetic validation dataset for a service that anonymizes Italian health
documents (native PDFs). Each document you write is a **JSON spec**. A deterministic generator
(`validation/anonval/build.py`) turns every spec into a native PDF and into its ground truth.
You never write PDFs yourself: the realism of the layout comes from the blocks you choose and the
realism of the content comes from the text you write.

**Language.** These instructions and the JSON keys are in English. **Every piece of document
content must be in Italian**: titles, labels, clinical text, names, addresses, abbreviations,
units. Use Italian formats: dates `gg/mm/aaaa` (and, from difficulty `media`, also `3 giugno 2024`,
`03.06.24`, `03-06-2024`), decimal comma (`11,2 g/dL`), Italian phone numbers (`051 6448210`,
`+39 347 112 9087`), Italian street names (`Via`, `Viale`, `Piazza`, `Corso`).

---

## 1. What to produce

For the document type you are assigned, write **12 specs**: 4 with difficulty `facile`, 4 `media`,
4 `complessa`.

- **File:** `validation/dataset/specs/<type>/<id>.json`
- **ID:** `<type>_<difficulty>_<nn>`, with `nn` = `01`..`04` within each difficulty. Example:
  `referto_radiologico_complessa_03`.

Types:

| `type` | Document |
|---|---|
| `referto_laboratorio` | Referto di laboratorio analisi |
| `test_genetico` | Referto di test genetico |
| `biopsia_patologica` | Referto istologico / biopsia (anatomia patologica) |
| `lettera_dimissione` | Lettera di dimissione ospedaliera, also from Terapia Intensiva (TI) |
| `nota_ambulatoriale` | Referto / nota di visita ambulatoriale specialistica |
| `autorizzazione_preventiva` | Modulo di autorizzazione preventiva (ASL) to a treatment, device or drug |
| `referto_radiologico` | Referto radiologico (RX, TC, RM, ecografia, PET) |
| `referto_esterno` | Referto medico esterno: private clinic, another region, a specialist's practice, received by fax |

---

## 2. Privacy rules (mandatory)

- **Fictitious people.** Every person is invented: plausible Italian names (and, where the story
  needs it, foreign names). Never use real or famous people, never use real doctors.
- **Fictitious institutions.** Never use real hospitals, ASL or clinics (no "Niguarda", "Gemelli",
  "Rizzoli"...). Use invented names: "Azienda USL di Valmarina", "Ospedale San Teodoro", "Casa di
  Cura Villa Ortensia". The letterhead may say "Servizio Sanitario Regionale", without naming a
  real region.
- **Real cities are allowed** (Bologna, Torino, Bari...). Birth places must come from the list in
  section 4.
- **Contacts.** E-mail and web domains end in `.example.it`. Phone numbers are invented.
- **Computed identifiers.** Never write a codice fiscale, IBAN, partita IVA or health card number
  yourself: use the computed markers (section 4). Every other identifier (accession number,
  medical record number, NRE, register number...) is invented by you, in a plausible format.

---

## 3. Spec format

```json
{
  "id": "referto_laboratorio_media_01",
  "type": "referto_laboratorio",
  "difficulty": "media",
  "style": {"font": "helvetica", "color": "#0B5394", "font_size": 9, "margins_mm": 16, "fax_line": null},
  "persons": {"paz": {"first_name": "Giulia", "last_name": "Bianchi Ferraro", "sex": "F", "birth_date": "1978-04-12", "birth_place": "Bologna"}},
  "letterhead": {"logo": "AUV", "logo_shape": "shield", "lines": ["..."], "right": ["..."]},
  "running_header": "Paziente: ... - N. accettazione ...",
  "footer": ["legal line 1", "legal line 2"],
  "blocks": [ {"type": "title", "text": "REFERTO DI LABORATORIO"}, "..." ],
  "negatives": [{"value": "Parkinson", "forbidden_categories": ["PERSONA"]}]
}
```

Unknown keys are rejected. Every string anywhere in the spec may contain PII markers, except
`id`, `type`, `difficulty`, `persons`, `negatives` and the enum fields.

**`style`**

| Key | Values | Notes |
|---|---|---|
| `font` | `helvetica`, `times`, `courier`, `dejavu` | |
| `color` | `#RRGGBB` | institutional colour: bands, headers, logo |
| `font_size` | 7 to 12 | small sizes (7.5-8.5) for dense or complex documents |
| `margins_mm` | 10 to 30 | |
| `fax_line` | optional | small Courier line at the top of every page, as printed by a fax machine, e.g. `"12/03/2024 10:41  DA: 0544 221190  CENTRO MEDICO ...  P. 001"` |

**`letterhead`** is drawn on the first page.

| Key | Content |
|---|---|
| `logo` | 2-5 letters of the fictitious institution's initials, or `null` |
| `logo_shape` | `circle`, `square`, `shield`, `cross`, `leaf` |
| `lines` | 1-6 lines; the first is bold and coloured (institution), the others are departments and director |
| `right` | up to 6 right-aligned contact lines: address, phone, fax, e-mail |

Keep each line under about 70 characters (about 45 for `right`).

**`running_header`** (optional) is printed at the top of pages 2 and following. Typically it holds
the patient's name, codice fiscale or accession number.

**`footer`** holds 0-4 short lines (under about 130 characters each) in small type on every page,
typically the institution's registered office, P.IVA, PEC and a privacy notice. "Pag. X di Y" is
added automatically.

**`blocks`** are drawn in order:

| `type` | Fields | Use |
|---|---|---|
| `title` | `text`, `subtitle?` | centred document title |
| `patient_box` | `fields: [[label, value], ...]`, `title?` (default `DATI DEL PAZIENTE`), `columns` 1-2, `align` `full`/`right` | framed box with the patient's data |
| `fields` | `fields: [[label, value], ...]`, `columns` 1-3 | unframed label-value grid (request data, dates, numbers) |
| `section` | `text` | coloured band with a section title (`EMATOLOGIA`, `ESAME OBIETTIVO`) |
| `heading` | `text` | underlined sub-heading |
| `paragraph` | `text`, `size?`, `align` `justify`/`left`/`center`/`right` | running text; `**bold**` and `\n` are allowed |
| `table` | `header: [...]`, `rows: [[...], ...]`, `widths?` (relative), `flag_column?` (index; non-empty cells in bold red: `H`, `L`, `*`), `zebra` (default true), `font_size?`, `caption?` | every row has as many cells as the header; a cell may contain `\n` |
| `barcode` | `value`, `kind` `code128`/`qr`, `label?`, `align` | barcode with the value printed next to it (accession number, sample code, verification URL) |
| `char_boxes` | `label`, `value` | one box per character, as in printed forms (codice fiscale, health card) |
| `checkboxes` | `items: [[label, true/false], ...]`, `label?`, `columns` 1-3 | form checkboxes |
| `signature` | `place_date?`, `role?`, `name?`, `extra?: [...]`, `digital` (bool), `stamp?: [lines]` | `digital: true` prints the D.Lgs. 82/2005 notice; otherwise a handwritten stroke. `stamp` adds a rotated rubber stamp. |
| `stamp` | `lines: [...]` (1-6), `angle` -20..20, `align` | standalone rotated stamp |
| `note` | `text` | grey box in italics (methodological notes, warnings) |
| `footnotes` | `items: [...]` | numbered notes in small type |
| `address` | `lines: [...]`, `align` `right`/`left` | addressee of a letter ("Egr. Dott. ... - Medico di Medicina Generale") |
| `columns` | `left: [blocks]`, `right: [blocks]`, `divider` (bool) | two columns; **keep each side shorter than half a page** |
| `page_break` | none | |
| `spacer` | `mm` | |

**Characters.** Use only characters of the Windows-1252 set (Italian accented letters, `°`, `±`,
`µ`, `²`, `€`, curly quotes and dashes are fine). `≤ ≥ →` and Greek letters are converted
automatically; anything else (emoji, CJK, special symbols) is rejected.

---

## 4. PII markers: the ground truth

Every personal or health data value is written **inline** as `[[CATEGORY:value]]`. The generator
prints only `value` and records `(value, CATEGORY, position in the layout)` as ground truth.
**The quality of the dataset depends on how exactly and consistently you mark values.**

### Rules

1. **Mark every occurrence.** If the patient's name appears five times (patient box, running
   header, text, signature of consent...), it is marked five times. An unmarked value counts as
   "not personal" and the service is penalized if it redacts it.
2. **Mark only the value.**
   - Write titles, labels and units outside the marker: `Dott.ssa [[MEDICO:Chiara Lombardi]]`,
     `C.F. [[CODICE_FISCALE:@paz]]`, `[[ESAME_VALORE:11,2]] g/dL`.
   - Never nest markers.
   - A value may not contain `[`, `]`.
3. **Choose the most specific category:**
   - **Persons:**
     - The patient's full name is `PERSONA`. When surname and first name sit in separate fields,
       use `COGNOME` and `NOME`.
     - Every physician is `MEDICO`. Nurses, technicians and residents are `OPERATORE_SANITARIO`.
     - Relatives are `FAMILIARE`, the caregiver is `CONTATTO_EMERGENZA`, guardians and parents
       of a minor are `TUTORE_DELEGATO`.
     - Partial forms ("sig.ra B.", "G. Bianchi", "il sig. Ferraro") are marked too, with the same
       category as the full name.
   - **Dates:** `DATA_NASCITA`, `DATA_VISITA`, `DATA_REFERTO`, `DATA_RICOVERO` (admission and
     discharge), `DATA_PRESCRIZIONE`, `DATA_DECESSO`; any other date is `DATA`.
   - **Places and addresses:** the street with its number is `INDIRIZZO`, the postcode is `CAP`,
     the city of residence or of an address is `LUOGO`, the birth place written in the text is
     `LUOGO_NASCITA`.
   - **Organizations:** hospitals, ASL, clinics and laboratories are `STRUTTURA_SANITARIA`; wards
     and outpatient clinics are `REPARTO`; the patient's employer is `DATORE_LAVORO`.
   - **Health data**, marked as **short clinical phrases**, not whole sentences:
     - `[[DIAGNOSI:anemia sideropenica]]`, `[[FARMACO:metformina 1000 mg]]`;
     - results as `[[ESAME_VALORE:142]]`. The test names in the first column of a lab table are
       not marked; only the measured values are.
     - Also mark `PATOLOGIA`, `TERAPIA`, `INTERVENTO`, `ALLERGIA`, `GRUPPO_SANGUIGNO`,
       `MISURE_ANTROPOMETRICHE`, `DATO_GENETICO`, `MALATTIE_INFETTIVE`, `SALUTE_MENTALE`,
       `DIPENDENZE`, `GRAVIDANZA`, `INVALIDITA`.
4. **Avoid ambiguous duplicates.**
   - An `ESAME_VALORE` must not be identical to a number that appears elsewhere as plain text in
     the same document (for example a bound of a reference range).
   - Do not mark generic words ("paziente", "reparto").
5. **Values with checksums are computed.** Never write them yourself.
   - `[[CODICE_FISCALE:@paz]]` uses the person `paz` declared in `persons`.
     - Each entry needs `first_name`, `last_name`, `sex` (`M`/`F`), `birth_date` (ISO
       `YYYY-MM-DD`) and `birth_place`.
     - `birth_place` must be one of: Roma, Milano, Napoli, Torino, Palermo, Genova, Bologna,
       Firenze, Bari, Catania, Venezia, Verona, Messina, Padova, Trieste, Brescia, Parma,
       Taranto, Modena, Perugia, Livorno, Ravenna, Cagliari, Foggia, Rimini, Salerno, Ferrara,
       Sassari, Latina, Pescara, Bergamo, Vicenza, Trento, Bolzano, Ancona, Lecce, Udine, Pisa,
       Potenza, Campobasso, Aosta, Siena, and for people born abroad Romania, Marocco, Albania,
       Cina, Ucraina, Germania, Francia, Senegal, India, Filippine, Egitto, Brasile, Tunisia,
       Nigeria, Pakistan.
     - The names written in the text must match the person's data.
   - `[[IBAN:@label]]`, `[[PARTITA_IVA:@label]]` and `[[TESSERA_SANITARIA:@label]]` produce
     random valid values. The same label gives the same value inside one document, so reuse the
     label when the same number is repeated.
6. **`negatives`** list strings that look like personal data but are not, and that the service
   must **not** redact with the listed categories:
   - eponyms: `Parkinson`, `Crohn`, `Hodgkin`, `Down`, `Hashimoto`, `Alzheimer`, `Gleason`,
     `Coombs`, `Valsalva`, `Murphy`, `Babinski`, `Papanicolaou`;
   - common words that are also surnames ("rosa", "bianco", "neri");
   - fictitious device or method names.

   The string must appear in the document as written. When an eponym is part of a diagnosis,
   mark the whole diagnosis and also list the eponym as a negative:
   `[[DIAGNOSI:malattia di Crohn]]` plus `{"value": "Crohn", "forbidden_categories": ["PERSONA"]}`.

### Categories (use exactly these names)

- **anagrafica**: `NOME` (first name); `COGNOME` (surname, including maiden or married name); `PERSONA` (first name and surname of a natural person together, as they appear in the text); `PATERNITA_MATERNITA` (first name and surname of father and mother); `SESSO` (registered sex M/F); `DATA_NASCITA` (date of birth); `LUOGO_NASCITA` (municipality, province and country of birth); `CITTADINANZA` (citizenship, nationality); `STATO_CIVILE` (marital status); `PROFESSIONE` (profession, occupation); `TITOLO_STUDIO` (educational qualification); `ETA` (age in years)
- **residenza**: `INDIRIZZO` (street or square and house number); `CAP` (postal code); `LUOGO` (municipality, province, hamlet of residence or domicile); `STATO` (country of residence)
- **identificativi**: `CODICE_FISCALE`; `PARTITA_IVA`; `TESSERA_SANITARIA` (health card number, TEAM, ASL/SSN code); `CODICE_STP_ENI` (STP/ENI code of temporarily present foreigners); `CODICE_ESENZIONE` (co-payment exemption code); `NUMERO_POLIZZA` (insurance policy or claim number); `NUMERO_PROTOCOLLO` (protocol number of a personal file)
- **documenti**: `NUMERO_CARTA_IDENTITA`; `NUMERO_PASSAPORTO`; `NUMERO_PATENTE`; `NUMERO_PERMESSO_SOGGIORNO`; `ENTE_RILASCIO` (issuing authority); `DATA_RILASCIO`; `DATA_SCADENZA`; `ALTEZZA_COLORE_OCCHI`; `MRZ` (each MRZ line)
- **contatti**: `EMAIL`; `PEC`; `TELEFONO`; `FAX`; `USERNAME_SOCIAL`; `INDIRIZZO_IP`; `ID_DISPOSITIVO` (MAC, IMEI, device serial); `URL_PERSONALE` (links to profiles or portals with personal data)
- **finanziari**: `IBAN`; `CARTA_CREDITO`; `NUMERO_CONTO`
- **veicoli**: `TARGA`; `TELAIO`
- **identificativi_sanitari**: `ID_PAZIENTE` (patient code, internal registry code); `NUMERO_CARTELLA` (medical record or nosological number); `NUMERO_ACCETTAZIONE` (admission, hospitalization, emergency department number); `NUMERO_REFERTO` (report or exam number); `NUMERO_RICETTA` (NRE, electronic prescription, therapeutic plan number); `NUMERO_PRENOTAZIONE` (booking code, CUP); `ACCESSION_NUMBER` (imaging and lab exam identifier); `CODICE_CAMPIONE` (test tube, biopsy, slide code)
- **persone_correlate**: `MEDICO` (attending, reporting or prescribing physician, also inside stamps and signatures); `OPERATORE_SANITARIO` (nurses, technicians, residents); `NUMERO_ALBO` (medical register number, staff number, often inside stamps); `FAMILIARE` (spouse, children, relatives); `CONTATTO_EMERGENZA` (emergency contact, caregiver); `TUTORE_DELEGATO` (guardian, support administrator, delegate, parent of a minor)
- **date**: `DATA` (generic); `DATA_VISITA` (visit or service date); `DATA_REFERTO` (report issue date); `DATA_RICOVERO` (admission or discharge date); `DATA_PRESCRIZIONE`; `DATA_DECESSO`
- **organizzazioni**: `ORGANIZZAZIONE` (companies in general); `STRUTTURA_SANITARIA` (hospital, clinic, laboratory, ASL); `REPARTO` (ward, outpatient clinic, operating unit); `DATORE_LAVORO` (the data subject's employer); `SCUOLA` (school attended)
- **sanitari**: `DIAGNOSI` (diagnoses, diagnostic question, symptoms); `PATOLOGIA` (past and chronic diseases); `TERAPIA` (therapies, treatments, rehabilitation); `FARMACO` (drugs, active ingredient, dosage); `ESAME_VALORE` (lab values, exam results, measurements); `INTERVENTO` (surgeries, procedures); `ALLERGIA`; `GRUPPO_SANGUIGNO`; `INVALIDITA` (civil invalidity, disability, law 104); `GRAVIDANZA`; `SALUTE_MENTALE`; `DIPENDENZE`; `MALATTIE_INFETTIVE` (HIV, hepatitis, STD); `DATO_GENETICO` (genetic tests, mutations, predispositions); `MISURE_ANTROPOMETRICHE` (weight, height, BMI, blood pressure); `DECESSO_CAUSA`
- **particolari**: `RELIGIONE`; `ORIENTAMENTO_SESSUALE`; `OPINIONE_POLITICA`; `SINDACATO`; `ETNIA`; `DATI_GIUDIZIARI`; `DATO_BIOMETRICO`

---

## 5. Realistic layout per type

The PDFs must look like real Italian health documents, not like plain text. Imitate the typical
layout of each type:

- **`referto_laboratorio`**
  - letterhead of the laboratory (ASL or private lab), `patient_box` with personal data and the
    requesting physician;
  - `fields` with accession number and the dates of sampling and report;
  - Code128 `barcode` of the sample;
  - one `section` per sector (EMATOLOGIA, CHIMICA CLINICA, COAGULAZIONE, URINE, SIEROLOGIA), each
    followed by a `table` "Esame / Risultato / U.M. / Valori di riferimento / flag";
  - methodological `note`, `signature` with `digital: true` of the "Dirigente Biologo" or
    "Dirigente Medico", QR for online verification.
- **`test_genetico`**
  - letterhead of a medical genetics laboratory;
  - patient data and requesting physician, indication to the test;
  - test type and method (NGS, gene panel, MLPA, Sanger), sample (peripheral blood, DNA code);
  - variant `table` "Gene / Trascritto / Variante HGVS c. / p. / Zigosità / Classe ACMG";
  - interpretation, limits of the test, recommendation of genetic counselling and family
    screening (relatives named with `FAMILIARE`);
  - signatures of the laboratory geneticist and the clinical geneticist.
- **`biopsia_patologica`**
  - letterhead of the UOC Anatomia Patologica;
  - histology exam number, acceptance date, sending ward, clinical data;
  - "Materiale inviato" with block codes (A1, B1...);
  - sections Descrizione macroscopica, Descrizione microscopica;
  - immunohistochemistry `table` (antibody, result, %), **DIAGNOSI** in bold, pTNM staging,
    Gleason or grading where pertinent;
  - pathologist's signature.
- **`lettera_dimissione`**
  - `address` to the GP ("Gentile Collega" or "Egregio Dott. ..."), letterhead of the ward;
  - admission and discharge dates, `NUMERO_CARTELLA`, reason for admission, anamnesis, clinical
    course, relevant exams, procedures;
  - discharge diagnosis, discharge therapy `table` "Farmaco / Dose / Orari / Note", follow-up and
    appointments;
  - signature of the ward physician and the director.
  - ICU variant (Terapia Intensiva): SOFA/APACHE scores, mechanical ventilation, vascular
    accesses, amines, emergency contact of the family.
- **`nota_ambulatoriale`**
  - letterhead of the outpatient clinic;
  - booking (CUP) number and prescription (NRE) number, service with its nomenclator code;
  - anamnesis, objective examination, vital parameters, conclusions, prescriptions, next visit;
  - signature with or without stamp.
- **`autorizzazione_preventiva`**
  - ASL form with sections numbered "1. DATI DELL'ASSISTITO", "2. PRESCRIZIONE",
    "3. MOTIVAZIONE CLINICA", "4. PARERE DELLA COMMISSIONE";
  - `char_boxes` for codice fiscale and health card, `checkboxes` (prima richiesta / rinnovo,
    urgenza, tipo di prestazione);
  - prescriber data with register number, exemption code, protocol number, space for the
    opinion;
  - stamps and signatures.
- **`referto_radiologico`**
  - letterhead of the UO Radiologia;
  - accession number with `barcode`, exam date, modality and body region, clinical question
    (quesito diagnostico);
  - technique and contrast medium (with any allergy), description, comparison with previous
    exams, conclusions;
  - dose information (DLP, CTDIvol) for CT;
  - signature of the radiologist with `digital: true`.
- **`referto_esterno`**
  - visibly different origins: a private "Poliambulatorio" or "Casa di Cura", a specialist's own
    letterhead, a hospital of another region;
  - often `fax_line`, `times` or `courier` font, different colours;
  - may contain the patient's insurance policy (`NUMERO_POLIZZA`), payment data (`IBAN`,
    `PARTITA_IVA` of the practice), employer (`DATORE_LAVORO`) for occupational medicine.

**Variety.** Within one type, the 12 documents must look like they come from different
institutions:
- change `font`, `color`, `logo_shape`, margins and order of the blocks;
- vary patients: sex, age (including a minor with a `TUTORE_DELEGATO` and an elderly person
  with a caregiver), Italian and foreign-born patients (also `CODICE_STP_ENI`);
- vary pathologies.

---

## 6. Difficulty rubric

The generator rejects a spec outside these limits:

| | `facile` | `media` | `complessa` |
|---|---|---|---|
| Pages | exactly 1 | 2-3 | 3-5 |
| Distinct identifying values (groups `sanitari` and `particolari` excluded) | 8-18 | 15-35 | at least 30 |

What changes with difficulty:

- **`facile`**
  - single column, letterhead and a simple body;
  - every personal value next to an explicit label ("Paziente:", "C.F.:", "Nato il:");
  - standard formats, one patient and one physician, no relatives;
  - at most one simple table.
- **`media`**
  - tables with personal data in their cells;
  - `running_header` with the patient's name, barcodes;
  - dates in more than one format, abbreviations ("pz.", "aa.", "u.o.");
  - a relative or caregiver;
  - personal data also inside the narrative text, not only after labels;
  - two physicians, signature with a stamp.
- **`complessa`**
  - dense layout: smaller font (7.5-8.5), `columns`, multi-line table cells, `char_boxes`,
    rotated stamps containing names and register numbers, `footnotes`;
  - personal data in the footer and in the running header;
  - many people (patient, relatives, several physicians and nurses, guardian);
  - partial and variant forms of names ("sig.ra B.", "G. Bianchi", "la paziente Bianchi");
  - compound and foreign surnames (De Santis, Dell'Orto, Nguyen, El Amrani), first names that are
    also common words (Rosa, Bianca, Sole);
  - several `negatives` (eponyms, surname-like words);
  - special categories where clinically plausible (`MALATTIE_INFETTIVE`, `SALUTE_MENTALE`,
    `DIPENDENZE`, `DATO_GENETICO`, `RELIGIONE`, for example refusal of transfusions);
  - personal data in unusual places (a table note, a QR caption, an addressee line).

---

## 7. Complete example

`validation/dataset/specs/referto_laboratorio/referto_laboratorio_media_01.json` is a complete,
valid example of the format (difficulty `media`, 2 pages). Read it before you start. Do not copy
its institution, people or values.

---

## 8. Check your work

For each spec, run from the repository root:

```bash
PYTHONPATH=validation uv run python -m anonval.build --solo <id> [<id> ...]
```

`OK` means the spec is valid. The PDF is then in `validation/dataset/pdf/<type>/<id>.pdf` and the
ground truth is in `validation/dataset/ground_truth/<type>/<id>.json`. Otherwise fix the reported
problem and run it again. Typical problems:
- an unknown category;
- a value missing from the PDF text;
- a page count or an identifying-value count outside the rubric;
- a forbidden character;
- an overflowing `columns` block.

Then look at the layout. Render the pages of **at least one document per difficulty** to PNG and
look at them:

```bash
uv run python -c "import pypdfium2 as p, sys; d = p.PdfDocument(sys.argv[1]); [pg.render(scale=1.4).to_pil().save(f'{sys.argv[2]}_{i + 1}.png') for i, pg in enumerate(d)]" <pdf> <output-prefix>
```

Fix any of these:
- overlapping or clipped text;
- letterhead lines too long;
- empty pages;
- tables that look broken;
- a document that does not look like a real one of its type.

Done means 12 specs, 4 per difficulty, every one `OK`, and visually credible.
