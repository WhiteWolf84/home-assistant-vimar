# 🏠 Vimar Cover - Time-Based Position Tracking

## 📋 Indice

- [Introduzione](#introduzione)
- [Caratteristiche](#caratteristiche)
- [Come Funziona](#come-funziona)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Utilizzo](#utilizzo)
- [Servizi](#servizi)
- [Automazioni](#automazioni)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Limitazioni](#limitazioni)

---

## 🎯 Introduzione

Questo componente aggiunge il **tracking della posizione time-based** per le tapparelle/tende Vimar che **NON hanno sensori di posizione hardware** integrati.

### Problema Risolto
Le tapparelle Vimar standard (senza sensori di fine corsa) non riportano la posizione corrente. Questo rende impossibile:
- Usare `cover.set_cover_position` per aprire a una percentuale specifica
- Creare automazioni basate sulla posizione
- Visualizzare la posizione nella UI di Home Assistant

### Soluzione
Questo componente **calcola la posizione** in base al tempo di movimento, conoscendo:
- Tempo necessario per aprire completamente
- Tempo necessario per chiudere completamente
- Quando è iniziato il movimento
- Quando si è fermato

---

## ✨ Caratteristiche

### ✅ Funzionalità Principali

- **📍 Tracking Posizione in Tempo Reale**: Aggiorna la posizione ogni 0.2s durante il movimento
- **💾 Ripristino Dopo Riavvio**: Salva e ripristina la posizione dopo il reboot di HA
- **🎚️ Set Position**: Permette di aprire/chiudere a percentuali specifiche (es. 50%)
- **⚙️ Configurazione Per-Entity**: Tempi di apertura/chiusura salvati per ogni singola cover
- **🎛️ 4 Modalità di Funzionamento**: AUTO, TIME_BASED, NATIVE, LEGACY configurabili
- **🔀 Compatibile**: Funziona insieme a cover con sensori hardware
- **🌐 Multilingua**: Supporta Italiano, Inglese, Tedesco

### 🎛️ Feature Avanzate

- **Auto-Stop Intelligente**: Si ferma automaticamente quando raggiunge la posizione target
- **Auto-Calibrazione Finecorsa**: Risincronizzazione automatica a 0% e 100%
- **Calibrazione Tempi Diversi**: Tempi di apertura e chiusura possono essere diversi
- **Alta Granularità**: Aggiornamento posizione ogni 0.2 secondi durante movimento
- **Sicurezza**: Gestisce correttamente interruzioni, stop manuali, blackout
- **Button Management**: Disabilita automaticamente pulsanti OPEN/CLOSE ai finecorsa
- **Modalità LEGACY**: Rollback al comportamento originale master branch

---

## ⚙️ Come Funziona

### Principio di Base

Il sistema usa il **tempo di percorrenza** per calcolare la posizione:

```
Posizione = (Tempo Trascorso / Tempo Totale) × 100
```

### Esempio Pratico

Supponiamo una tapparella con:
- **Tempo apertura**: 28 secondi (0% → 100%)
- **Tempo chiusura**: 26 secondi (100% → 0%)

#### Scenario 1: Apertura Completa
```
Posizione iniziale: 0% (chiusa)
Comando: APRI
Dopo 14 secondi → 50% (metà aperta)
Dopo 28 secondi → 100% (aperta)
🎯 Finecorsa meccanico → NO comando STOP (risparmio usura)
```

#### Scenario 2: Apertura Parziale
```
Posizione iniziale: 0%
Comando: Set Position 75%
HA calcola: 75% richiede 21 secondi (28s × 0.75)
Dopo 21 secondi → Comando STOP automatico a 75%
```

#### Scenario 3: Auto-Calibrazione
```
Posizione tracciata: 98% (piccola deriva accumulata)
Comando: APRI completamente
Raggiunge finecorsa meccanico a 100%
✅ Posizione auto-corretta a 100% esatto
→ Deriva azzerata!
```

### Modalità di Funzionamento

Il componente supporta **4 modalità** configurabili:

#### 1. 🤖 AUTO (default)
```
Comportamento intelligente:
- Cover SENZA sensore posizione → usa TIME_BASED
- Cover CON sensore posizione → usa NATIVE
```

#### 2. ⏱️ TIME_BASED (forzato)
```
Forza tracking temporale anche se c'è sensore hardware
Uso: quando il sensore Vimar è impreciso o in ritardo
```

#### 3. 🔧 NATIVE (solo hardware)
```
Usa SOLO feedback dal sensore Vimar
Disabilita completamente il tracking temporale
```

#### 4. 🔙 LEGACY (compatibilità)
```
Comportamento originale del branch master
- NO time-based tracking
- NO ripristino posizione
- SET_POSITION solo se sensore hardware disponibile
- Zero overhead (nessun tracking attivo)
```

**Quando usare LEGACY:**
- 🔄 Vuoi tornare al comportamento originale
- 🧪 Testing e confronto con master branch
- 📊 Zero overhead (no tracking)
- 🔒 Compatibilità totale con versione precedente

### Gestione Finecorsa Intelligente

Una delle feature più importanti è la **gestione ottimizzata dei finecorsa**:

```python
if position == 0% or position == 100%:
    # Finecorsa meccanico - motore si ferma da solo
    # NON inviare comando STOP
    # ✅ Risparmia usura relè
    # ✅ Auto-calibrazione posizione
else:
    # Posizione intermedia
    # Invia comando STOP quando raggiunta
```

**Vantaggi:**
- ⚙️ Meno usura dei relè
- 🎯 Auto-calibrazione ad ogni ciclo completo
- 📐 Compensa derive temporali accumulate
- 🔋 Risparmio energetico

### Flusso Operativo

```
┌─────────────────────┐
│ Comando Ricevuto    │
│ (open/close/        │
│  set_position)      │
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────┐
│ Modalità Attiva?         │
│ AUTO/TIME/NATIVE/LEGACY │
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────┐
│ Calcola Tempo       │
│ Necessario          │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Invia Comando a     │
│ Vimar Gateway       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Tracking Loop       │
│ (ogni 0.2s)         │
│ (LEGACY: skip)      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Posizione Target    │
│ Raggiunta?          │
└──────┬────────┬─────┘
       │ NO     │ YES
       │        │
       │        ▼
       │   ┌─────────────┐
       │   │ 0% o 100%?  │
       │   └──┬────────┬──┘
       │      │ SÌ     │ NO
       │      │        │
       │      ▼        ▼
       │   ┌────┐  ┌──────┐
       │   │ OK │  │ STOP │
       │   └────┘  └──────┘
       │
       ▼
┌─────────────────────┐
│ Continua Tracking   │
└─────────────────────┘
```

### Logica assumed_state

Una caratteristica importante è come viene gestito `assumed_state`:

```python
# Per TIME_BASED tracking:
assumed_state = False  # HA conosce ESATTAMENTE la posizione!

# Per NATIVE (sensore hardware):
assumed_state = True   # Dipende dal feedback esterno

# Per LEGACY:
assumed_state = True (se ha sensore) / False (se no sensore)
```

Questa logica **sembra controintuitiva** ma è corretta:
- Con tracking temporale, HA **calcola deterministicamente** la posizione
- Con sensore hardware, HA **assume** la posizione dal webserver
- In LEGACY, replica esattamente il comportamento master originale

Grazie a questo, Home Assistant disabilita correttamente i pulsanti OPEN/CLOSE ai finecorsa!

---

## 📦 Requisiti

### Requisiti di Sistema
- **Home Assistant**: 2023.1 o superiore
- **Integrazione Vimar**: Installata e configurata
- **Python**: 3.11+ (incluso in HA)

### Requisiti Hardware
- Gateway Vimar By-Me
- Tapparelle/Tende Vimar (con o senza sensori di posizione)
- Connessione di rete stabile

---

## 🚀 Installazione

### Branch Git

Questo branch (`timed-shutters`) contiene l'implementazione completa.

```bash
# Clone repository
git clone https://github.com/WhiteWolf84/home-assistant-vimar.git
cd home-assistant-vimar

# Checkout branch
git checkout timed-shutters

# Copia in custom_components
cp -r custom_components/vimar /config/custom_components/

# Riavvia Home Assistant
```

### File del Branch

```
custom_components/vimar/
├── cover.py              ← Implementazione tracking con 4 modalità
├── const.py              ← Costanti CONF_COVER_POSITION_MODE
└── services.yaml         ← Servizio set_travel_times
```

---

## ⚙️ Configurazione

### Configurazione via Config Flow UI

**Impostazioni** → **Dispositivi e Servizi** → **Vimar** → **⚙️ CONFIGURA**

Menu a tendina con **4 opzioni**:

| Modalità | Descrizione |
|----------|-------------|
| 🤖 **Automatico** | (Default) Usa TIME_BASED se non c'è sensore, altrimenti NATIVE |
| ⏱️ **Time-based** | Forza tracking temporale per TUTTE le cover |
| 🔧 **Native** | Usa SOLO feedback sensori hardware (disabilita tracking) |
| 🔙 **Legacy** | Comportamento originale master (no tracking) |

### Configurazione Tempi di Percorrenza

I tempi vengono salvati **per ogni singola cover** usando **entity options** (persistono tra riavvii).

#### Step 1: Calibrazione Manuale

```bash
# 1. Cronometra l'apertura
#    - Chiudi completamente la tapparella
#    - Avvia cronometro
#    - Apri completamente
#    - Ferma cronometro → es. 28 secondi

# 2. Cronometra la chiusura
#    - Tapparella aperta
#    - Avvia cronometro
#    - Chiudi completamente
#    - Ferma cronometro → es. 26 secondi
```

#### Step 2: Configura via Servizio

**Strumenti Sviluppatore** → **Azioni**:

```yaml
service: vimar.set_travel_times
target:
  entity_id: cover.tapparella_cameretta
data:
  travel_time_up: 28      # Secondi per aprire (0% → 100%)
  travel_time_down: 26    # Secondi per chiudere (100% → 0%)
```

✅ I tempi vengono **salvati automaticamente** nell'entity registry!

**Nota**: In modalità LEGACY, i travel times non vengono utilizzati.

#### Step 3: Verifica Configurazione

**Strumenti Sviluppatore** → **Stati** → Cerca `cover.tapparella_xxx`

Gli **attributi** devono mostrare:
```yaml
current_position: 0-100
position_mode: auto  # o time_based, native, legacy
uses_time_based_tracking: true  # false se LEGACY o NATIVE
travel_time_up: 28  # Solo se time-based
travel_time_down: 26  # Solo se time-based
supported_features: 15  # OPEN | CLOSE | STOP | SET_POSITION
friendly_name: "Tapparella Cameretta"
```

✅ Se vedi questi attributi, la configurazione è OK!

---

## 🎮 Utilizzo

### Comandi di Base

#### Apertura Completa
```yaml
service: cover.open_cover
target:
  entity_id: cover.tapparella_cameretta
```

#### Chiusura Completa
```yaml
service: cover.close_cover
target:
  entity_id: cover.tapparella_cameretta
```

#### Stop
```yaml
service: cover.stop_cover
target:
  entity_id: cover.tapparella_cameretta
```

### Comandi Avanzati

#### Imposta Posizione Specifica
```yaml
service: cover.set_cover_position
target:
  entity_id: cover.tapparella_cameretta
data:
  position: 50  # Apri al 50%
```

**Note:**
- Funziona in modalità AUTO, TIME_BASED, NATIVE (se sensore presente)
- In modalità LEGACY: funziona SOLO se c'è sensore hardware

**Posizioni comuni:**
- `0` = Completamente chiusa
- `25` = Chiusa al 75%
- `50` = Metà aperta
- `75` = Aperta al 75%
- `100` = Completamente aperta

#### Apertura Multipla
```yaml
service: cover.set_cover_position
target:
  entity_id:
    - cover.tapparella_cameretta
    - cover.tapparella_salotto
    - cover.tapparella_cucina
data:
  position: 75
```

### Interfaccia Utente

Nella UI di Home Assistant, le cover con tracking time-based mostrano:

- ✅ Slider posizione funzionante
- ✅ Pulsante CLOSE disabilitato quando chiusa (0%)
- ✅ Pulsante OPEN disabilitato quando aperta (100%)
- ✅ Indicatore posizione in tempo reale durante movimento

**In modalità LEGACY**:
- ❌ Slider posizione disponibile SOLO se c'è sensore hardware
- ✅ Pulsanti OPEN/CLOSE/STOP sempre disponibili
- ❌ Nessun tracking posizione in tempo reale

### Card Lovelace

**Card Base:**

```yaml
type: entities
title: Tapparelle
entities:
  - entity: cover.tapparella_cameretta
  - entity: cover.tapparella_salotto
  - entity: cover.tapparella_cucina
```

**Card Avanzata:**

```yaml
type: tile
entity: cover.tapparella_cameretta
features:
  - type: cover-position
  - type: cover-open-close
```

---

## 🛠️ Servizi

### vimar.set_travel_times

Configura i tempi di apertura/chiusura per una specifica cover.

I tempi vengono salvati nelle **entity options** e persistono tra i riavvii.

**Nota**: Questo servizio è rilevante solo per modalità AUTO e TIME_BASED. In modalità NATIVE e LEGACY, i tempi vengono ignorati.

#### Parametri

| Parametro | Tipo | Range | Richiesto | Descrizione |
|-----------|------|-------|-----------|-------------|
| `travel_time_up` | int | 1-300 | ✅ Sì | Tempo in secondi per apertura completa (0% → 100%) |
| `travel_time_down` | int | 1-300 | ✅ Sì | Tempo in secondi per chiusura completa (100% → 0%) |

#### Esempio

```yaml
service: vimar.set_travel_times
target:
  entity_id: cover.tapparella_cameretta
data:
  travel_time_up: 30
  travel_time_down: 28
```

#### Default

Se non configurati, vengono usati i valori di default:
- `travel_time_up`: 28 secondi
- `travel_time_down`: 26 secondi

#### Persistenza

I tempi vengono salvati nel file:
```
/config/.storage/core.entity_registry
```

E restano disponibili anche dopo:
- ✅ Riavvio Home Assistant
- ✅ Aggiornamento integrazione
- ✅ Backup/Restore

---

## 🤖 Automazioni

### Esempio 1: Apri al Mattino

```yaml
automation:
  - alias: "Apri Tapparelle Mattino"
    trigger:
      - platform: sun
        event: sunrise
        offset: "+00:30:00"  # 30 min dopo alba
    action:
      - service: cover.set_cover_position
        target:
          entity_id:
            - cover.tapparella_cameretta
            - cover.tapparella_salotto
        data:
          position: 100  # Completamente aperte
```

### Esempio 2: Chiudi alla Sera

```yaml
automation:
  - alias: "Chiudi Tapparelle Sera"
    trigger:
      - platform: sun
        event: sunset
    action:
      - service: cover.close_cover
        target:
          area_id: living_room  # Tutte le cover del salotto
```

### Esempio 3: Ombreggiamento Automatico

```yaml
automation:
  - alias: "Ombreggiamento Estate"
    trigger:
      - platform: numeric_state
        entity_id: sensor.temperatura_esterna
        above: 28
    condition:
      - condition: sun
        after: sunrise
        before: sunset
    action:
      - service: cover.set_cover_position
        target:
          entity_id: cover.tapparella_sud
        data:
          position: 30  # Chiusa al 70% quando fa caldo
```

### Esempio 4: Calibrazione Automatica

```yaml
automation:
  - alias: "Calibrazione Settimanale Tapparelle"
    trigger:
      - platform: time
        at: "06:00:00"
    condition:
      - condition: time
        weekday: sun  # Solo domenica
    action:
      # Ciclo completo per auto-calibrazione
      - service: cover.open_cover
        target:
          area_id: all
      - delay:
          seconds: 35  # Attendi apertura completa
      - service: cover.close_cover
        target:
          area_id: all
```

### Esempio 5: Basato su Luminosità

```yaml
automation:
  - alias: "Apri se Troppo Buio"
    trigger:
      - platform: numeric_state
        entity_id: sensor.luminosita_salotto
        below: 100
    condition:
      - condition: time
        after: "08:00:00"
        before: "20:00:00"
      - condition: numeric_state
        entity_id: cover.tapparella_salotto
        attribute: current_position
        below: 50
    action:
      - service: cover.set_cover_position
        target:
          entity_id: cover.tapparella_salotto
        data:
          position: 80
```

---

## 🔧 Troubleshooting

### ❌ Problema: Posizione Non Aggiornata

**Sintomi:** La posizione rimane a 0 o non si muove.

**Cause & Soluzioni:**

1. **Modalità LEGACY o NATIVE attiva**
   ```
   Verifica: Developer Tools → Stati → Attributo "position_mode"
   Soluzione: Config Flow → Vimar → Cambia in 'auto' o 'time_based'
   ```

2. **Tempi Non Configurati (in AUTO/TIME_BASED)**
   ```yaml
   # Verifica attributi cover
   travel_time_up: ?
   travel_time_down: ?
   
   # Se mancano, configura con servizio
   service: vimar.set_travel_times
   data:
     travel_time_up: 28
     travel_time_down: 26
   ```

3. **Cover con Sensore Hardware in Modalità AUTO**
   ```
   Comportamento: Normale! AUTO usa il sensore hardware.
   Soluzione: Se preferisci time-based, usa 'time_based' mode
   ```

---

### ❌ Problema: Posizione Imprecisa

**Sintomi:** Posizione non corrisponde alla realtà.

**Soluzioni:**

1. **Ricalibra i Tempi**
   ```bash
   # Cronometra con precisione e riconfigura
   # Fai MEDIA di 3 misurazioni per maggior precisione
   ```

2. **Ciclo Completo per Auto-Calibrazione**
   ```yaml
   # 1. Chiudi completamente (auto-calibrazione a 0%)
   service: cover.close_cover
   
   # 2. Aspetta 30s
   
   # 3. Apri completamente (auto-calibrazione a 100%)
   service: cover.open_cover
   ```

3. **Verifica Tempi Asimmetrici**
   ```
   Apertura e chiusura possono avere tempi MOLTO diversi!
   (es. apertura 28s, chiusura 26s)
   Misurali separatamente con cronometro.
   ```

4. **Check Attribute position_mode**
   ```yaml
   # Developer Tools → Stati
   position_mode: auto
   uses_time_based_tracking: true
   
   # Se false → verifica configuration
   ```

---

### ❌ Problema: Pulsanti Sempre Abilitati

**Sintomi:** Pulsante CLOSE attivo anche a 0%, OPEN attivo anche a 100%.

**Causa:** Problema risolto nel branch `timed-shutters` con logica corretta di `assumed_state`.

**Verifica:**
```yaml
# Developer Tools → Stati → cover.xxx
assumed_state: false  # Deve essere False per time-based!

# Se è True:
# 1. Verifica di essere su branch timed-shutters
# 2. Verifica position_mode (non sia LEGACY)
# 3. Riavvia Home Assistant
```

---

### ❌ Problema: Posizione Non Ripristinata al Riavvio

**Sintomi:** Dopo riavvio HA, posizione torna a 0%.

**Soluzioni:**

1. **Modalità LEGACY o NATIVE**
   ```
   In queste modalità, il ripristino posizione è disabilitato.
   Soluzione: Usa modalità AUTO o TIME_BASED
   ```

2. **Prima Installazione**
   ```
   Normale! Non c'è ancora stato precedente.
   Muovi le tapparelle, poi riavvia → OK
   ```

3. **Stato Non Salvato**
   ```bash
   # Verifica file restore_state
   cat /config/.storage/core.restore_state | grep -A5 cover.tapparella
   
   # Deve contenere:
   "current_position": <valore>
   ```

---

### ❌ Problema: set_cover_position Non Funziona

**Sintomi:** Comando non fa nulla o errore nei log.

**Soluzioni:**

1. **Modalità LEGACY senza sensore**
   ```
   In LEGACY, SET_POSITION funziona SOLO se c'è sensore hardware.
   Soluzione: Cambia modalità in AUTO o TIME_BASED
   ```

2. **Verifica Supported Features**
   ```yaml
   # Developer Tools → Stati
   supported_features: 15
   # Deve includere SET_POSITION
   ```

3. **Check Modalità**
   ```yaml
   uses_time_based_tracking: true  # Deve essere true
   position_mode: auto  # O time_based
   ```

4. **Controlla Log**
   ```bash
   grep -i "set_cover_position\|tracking" /config/home-assistant.log | tail -20
   ```

---

### ❌ Problema: Cover Si Ferma Prima/Dopo del Target

**Sintomi:**
- Chiedi 100% ma si ferma a 95%
- Chiedi 0% ma va a -5%

**Causa:** Tempi di percorrenza non precisi.

**Soluzioni:**

1. **Calibrazione Fine**
   ```yaml
   # Se si ferma prima (es. 95% invece di 100%)
   # Aumenta leggermente il tempo
   travel_time_up: 29  # Era 28
   
   # Se va oltre (es. 105% poi torna a 100%)
   # Diminuisci leggermente
   travel_time_up: 27  # Era 28
   ```

2. **Usa Auto-Calibrazione**
   ```
   Fai cicli completi (0% → 100% e viceversa)
   Il sistema si auto-calibra ai finecorsa meccanici!
   ```

3. **Tolleranza Accettabile**
   ```
   95-100% è normale per tracking time-based
   Il finecorsa meccanico corregge automaticamente
   ```

---

### 🐛 Debug Avanzato

#### Abilita Log Debug

Aggiungi in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.vimar.cover: debug
```

Poi cerca nei log:

```bash
grep "Tapparella\|tracking\|position\|LEGACY" /config/home-assistant.log | tail -50
```

**Log da cercare:**
```
✅ "Position restored: 50%"
✅ "Travel times loaded - up: 28s, down: 26s"
✅ "Tracking opening from 0% to 100%"
✅ "Reached end-stop 100%, mechanical stop"
✅ "LEGACY mode - no time-based tracking"
❌ "Already closed (0%), ignoring CLOSE command"
```

#### Check Entity Registry

```bash
# Verifica entity options
cat /config/.storage/core.entity_registry | \
  jq '.data.entities[] | select(.entity_id | contains("cover.tapparella"))'

# Cerca:
"options": {
  "cover": {
    "travel_time_up": 28,
    "travel_time_down": 26
  }
}
```

---

## ❓ FAQ

### Q: Qual è la differenza tra le 4 modalità?
**A:** 
- **AUTO**: Sceglie automaticamente (time-based se non c'è sensore, native se c'è)
- **TIME_BASED**: Forza tracking temporale anche se c'è sensore (utile se sensore impreciso)
- **NATIVE**: Usa SOLO sensori hardware, disabilita tracking temporale
- **LEGACY**: Comportamento originale master branch (no tracking, zero overhead)

### Q: Quando dovrei usare la modalità LEGACY?
**A:** 
- Vuoi tornare al comportamento originale del branch master
- Stai facendo testing/confronti
- Vuoi zero overhead (nessun tracking attivo)
- Hai problemi con le altre modalità e vuoi un fallback sicuro

### Q: In LEGACY posso usare set_cover_position?
**A:** Sì, ma SOLO se la cover ha un sensore di posizione hardware. Senza sensore, `set_cover_position` non è disponibile in LEGACY.

### Q: Posso usare TIME_BASED anche con cover che hanno sensori?
**A:** Sì! Imposta modalità TIME_BASED per forzare il tracking temporale per tutte le cover.

### Q: I tempi di calibrazione persistono dopo riavvio?
**A:** Sì! Vengono salvati nelle **entity options** (file `core.entity_registry`).

### Q: Cosa succede se c'è un blackout durante il movimento?
**A:** In AUTO/TIME_BASED, la posizione viene salvata periodicamente. Al riavvio, riprende dall'ultima posizione nota. In LEGACY, non c'è tracking.

### Q: Devo ricalibrare periodicamente?
**A:** Consigliato ogni 6-12 mesi in modalità TIME_BASED, o se noti imprecisioni. I motori possono rallentare con l'usura.

### Q: Come funziona l'auto-calibrazione ai finecorsa?
**A:** Ogni volta che raggiungi 0% o 100% (finecorsa meccanico) in modalità TIME_BASED, la posizione viene automaticamente corretta. Questo compensa derive accumulate nel tempo.

### Q: Perché `assumed_state = False` per tracking time-based?
**A:** Perché Home Assistant **calcola esattamente** la posizione. Non è "assunta" da fonte esterna, è deterministica. Questo permette la corretta gestione dei pulsanti UI.

### Q: Quanto è preciso il tracking?
**A:** In TIME_BASED con calibrazione accurata: errore <3%. L'auto-calibrazione ai finecorsa azzera la deriva periodicamente.

### Q: Posso usarlo con veneziane (tilt)?
**A:** Il tracking posizione funziona per apertura/chiusura. Il tilt usa i comandi Vimar standard (se supportato dall'hardware).

### Q: Consuma risorse CPU/memoria?
**A:** 
- **AUTO/TIME_BASED**: Minimo. Aggiorna ogni 0.2s solo durante movimento. A riposo, zero overhead.
- **NATIVE/LEGACY**: Zero overhead (nessun tracking).

### Q: Come faccio backup della configurazione?
**A:** Includi nel backup:
```bash
/config/.storage/core.entity_registry  # Entity options (tempi)
/config/.storage/core.restore_state    # Posizioni correnti
```

### Q: Posso cambiare modalità in qualsiasi momento?
**A:** Sì! Via Config Flow UI. Riavvia HA dopo il cambio. Le entity options (travel times) vengono mantenute.

---

## ⚠️ Limitazioni

### Limitazioni Tecniche

1. **Precisione Tempo-Dipendente**: In TIME_BASED, basata sui tempi configurati. Errori di calibrazione = imprecisione posizione.

2. **Inerzia Meccanica**: Il motore ha inerzia. Comando stop ≠ stop istantaneo.

3. **Deriva nel Tempo**: Motori possono rallentare con usura. Mitigato dall'auto-calibrazione ai finecorsa in TIME_BASED.

4. **Nessun Feedback Blocchi**: Il sistema non rileva se la tapparella è bloccata fisicamente.

5. **Movimenti Manuali**: Se muovi via pulsante fisico (non tramite HA), la posizione viene **rilevata** al successivo polling (solo in TIME_BASED).

### Limitazioni Funzionali

1. **Modalità Globale**: La modalità `position_mode` è globale, non per singola cover.

2. **Richiede Calibrazione Iniziale**: In AUTO/TIME_BASED, devi misurare i tempi manualmente per ogni cover.

3. **Update Rate**: Aggiornamento ogni 0.2 secondi (limite accettabile).

4. **LEGACY Limitazioni**: In LEGACY, `set_cover_position` disponibile SOLO con sensore hardware.

### Best Practices

✅ **Calibra con Precisione**: Usa cronometro digitale, fai media di 3 misurazioni (TIME_BASED)
✅ **Cicli Completi Periodici**: Fai apertura/chiusura completa settimanalmente per auto-calibrazione (TIME_BASED)
✅ **Ricalibra Dopo Manutenzione**: Se cambi motore o cinghie
✅ **Backup Configurazione**: Esporta `entity_registry` periodicamente
✅ **Monitora Log**: Controlla eventuali errori dopo aggiornamenti
✅ **Test Dopo Upgrade HA**: Verifica funzionalità dopo upgrade maggiori
✅ **Usa LEGACY per Fallback**: In caso di problemi, LEGACY è un fallback sicuro

---

## 🎉 Vantaggi Rispetto al Master

Questo branch `timed-shutters` introduce:

1. ✅ **4 Modalità Configurabili**: AUTO | TIME_BASED | NATIVE | **LEGACY**
2. ✅ **Entity Options**: Tempi salvati per singola cover (persistenti)
3. ✅ **Config Flow UI**: Configurazione semplice tramite UI
4. ✅ **Auto-Calibrazione Finecorsa**: Risincronizzazione automatica (TIME_BASED)
5. ✅ **Assumed State Corretto**: Gestione pulsanti UI perfetta
6. ✅ **Alta Granularità**: Update ogni 0.2s (era 1s)
7. ✅ **Attributi Extra**: `position_mode`, `uses_time_based_tracking`
8. ✅ **Ottimizzazione Finecorsa**: No STOP command a 0%/100% (TIME_BASED)
9. ✅ **Modalità LEGACY**: Rollback sicuro al comportamento originale
10. ✅ **Flessibilità**: 4 modalità per coprire ogni esigenza

---

## 📚 Riferimenti

### Repository GitHub
- **Branch**: `timed-shutters`
- **Repository**: https://github.com/WhiteWolf84/home-assistant-vimar

### File Principali
- `cover.py` - Implementazione completa con 4 modalità
- `const.py` - Costanti CONF_COVER_POSITION_MODE (4 modalità)
- `services.yaml` - Servizio set_travel_times
- `translations/` - IT, EN, DE (con LEGACY)

### Versioni
- **Branch Version**: timed-shutters
- **Compatibilità HA**: 2023.1+
- **Ultima Modifica**: Febbraio 2026

### Crediti
- **Implementazione Time-Based Tracking**: Sviluppata in collaborazione
- **Auto-Calibrazione Finecorsa**: Feature richiesta dall'utente
- **Modalità LEGACY**: Richiesta compatibilità master branch
- **Testing & Debug**: WhiteWolf84

---

## 🎊 Conclusione

Hai ora un sistema **completo e flessibile** di tracking posizione per le tue tapparelle Vimar!

### Caratteristiche Uniche:
- 🎛️ **Quattro modalità** configurabili per ogni esigenza
- 🎯 Auto-calibrazione che azzera derive nel tempo (TIME_BASED)
- ⚙️ Ottimizzazione usura hardware (no STOP ai finecorsa)
- 💾 Configurazione persistente per-entity
- 🎨 UI perfetta con pulsanti disabilitati correttamente
- 🔙 **Modalità LEGACY** per rollback sicuro

### Prossimi Passi:
1. ✅ Configura modalità via Config Flow UI
2. ✅ Calibra i tempi di ogni cover con `set_travel_times` (se AUTO/TIME_BASED)
3. ✅ Testa apertura/chiusura parziali
4. ✅ Crea automazioni personalizzate
5. ✅ Fai cicli completi periodici per mantenere precisione (TIME_BASED)
6. ✅ Usa LEGACY se hai bisogno di comportamento originale

**Buon uso! 🏠✨**

---

*Ultimo aggiornamento: Febbraio 2026 - Branch timed-shutters - 4 modalità disponibili*