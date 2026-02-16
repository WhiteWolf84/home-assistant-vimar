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
- **🎛️ 3 Modalità di Funzionamento**: AUTO, TIME_BASED, NATIVE configurabili
- **🔀 Compatibile**: Funziona insieme a cover con sensori hardware
- **🌐 Multilingua**: Supporta Italiano, Inglese, Tedesco

### 🎛️ Feature Avanzate

- **Auto-Stop Intelligente**: Si ferma automaticamente quando raggiunge la posizione target
- **Auto-Calibrazione Finecorsa**: Risincronizzazione automatica a 0% e 100%
- **Calibrazione Tempi Diversi**: Tempi di apertura e chiusura possono essere diversi
- **Alta Granularità**: Aggiornamento posizione ogni 0.2 secondi durante movimento
- **Sicurezza**: Gestisce correttamente interruzioni, stop manuali, blackout
- **Button Management**: Disabilita automaticamente pulsanti OPEN/CLOSE ai finecorsa

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

Il componente supporta **3 modalità** configurabili:

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
┌─────────────────────┐
│ Modalità Attiva?    │
│ AUTO/TIME/NATIVE    │
└──────┬──────────────┘
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
```

Questa logica **sembra controintuitiva** ma è corretta:
- Con tracking temporale, HA **calcola deterministicamente** la posizione
- Con sensore hardware, HA **assume** la posizione dal webserver

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
├── cover.py              ← Implementazione tracking con 3 modalità
├── const.py              ← Costanti CONF_COVER_POSITION_MODE
└── services.yaml         ← Servizio set_travel_times
```

---

## ⚙️ Configurazione

### Metodo 1: configuration.yaml (Raccomandato)

Aggiungi alla configurazione Vimar:

```yaml
vimar:
  host: 192.168.1.XXX
  username: !secret vimar_user
  password: !secret vimar_pass
  certificate_validation: false
  
  # ⬇️ Configurazione modalità posizione
  cover_position_mode: auto  # auto | time_based | native
```

**Opzioni disponibili:**

| Valore | Descrizione |
|--------|-------------|
| `auto` | (Default) Usa TIME_BASED se non c'è sensore, altrimenti NATIVE |
| `time_based` | Forza tracking temporale per TUTTE le cover |
| `native` | Usa SOLO feedback sensori hardware (disabilita tracking) |

### Metodo 2: UI (Legacy)

Se preferisci configurare via UI:

1. Vai su **Impostazioni** → **Dispositivi e Servizi**
2. Trova **Vimar**
3. Clicca **CONFIGURA**
4. Seleziona modalità dal menu a tendina
5. Salva e riavvia HA

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

#### Step 3: Verifica Configurazione

**Strumenti Sviluppatore** → **Stati** → Cerca `cover.tapparella_xxx`

Gli **attributi** devono mostrare:
```yaml
current_position: 0-100
position_mode: auto
uses_time_based_tracking: true
travel_time_up: 28
travel_time_down: 26
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

1. **Modalità NATIVE attiva**
   ```yaml
   # In configuration.yaml
   vimar:
     cover_position_mode: auto  # Cambia da 'native' a 'auto'
   ```

2. **Tempi Non Configurati**
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
   
   # Se false → verifica configuration.yaml
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
# 2. Riavvia Home Assistant
```

---

### ❌ Problema: Posizione Non Ripristinata al Riavvio

**Sintomi:** Dopo riavvio HA, posizione torna a 0%.

**Soluzioni:**

1. **Prima Installazione**
   ```
   Normale! Non c'è ancora stato precedente.
   Muovi le tapparelle, poi riavvia → OK
   ```

2. **Stato Non Salvato**
   ```bash
   # Verifica file restore_state
   cat /config/.storage/core.restore_state | grep -A5 cover.tapparella
   
   # Deve contenere:
   "current_position": <valore>
   ```

3. **Verifica RestoreEntity**
   ```
   Il codice usa RestoreEntity per salvare stato.
   Se manca, reinstalla da branch timed-shutters.
   ```

---

### ❌ Problema: set_cover_position Non Funziona

**Sintomi:** Comando non fa nulla o errore nei log.

**Soluzioni:**

1. **Verifica Supported Features**
   ```yaml
   # Developer Tools → Stati
   supported_features: 15
   # Deve includere SET_POSITION (flag sempre presente)
   ```

2. **Check Modalità**
   ```yaml
   uses_time_based_tracking: true  # Deve essere true
   position_mode: auto  # O time_based
   ```

3. **Controlla Log**
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
grep "Tapparella\|tracking\|position" /config/home-assistant.log | tail -50
```

**Log da cercare:**
```
✅ "Position restored: 50%"
✅ "Travel times loaded - up: 28s, down: 26s"
✅ "Tracking opening from 0% to 100%"
✅ "Reached end-stop 100%, mechanical stop"
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

### Q: Qual è la differenza tra AUTO, TIME_BASED e NATIVE?
**A:** 
- **AUTO**: Sceglie automaticamente (time-based se non c'è sensore, native se c'è)
- **TIME_BASED**: Forza tracking temporale anche se c'è sensore (utile se sensore impreciso)
- **NATIVE**: Usa SOLO sensori hardware, disabilita tracking temporale

### Q: Posso usare TIME_BASED anche con cover che hanno sensori?
**A:** Sì! Imposta `cover_position_mode: time_based` per forzare il tracking temporale per tutte.

### Q: I tempi di calibrazione persistono dopo riavvio?
**A:** Sì! Vengono salvati nelle **entity options** (file `core.entity_registry`).

### Q: Cosa succede se c'è un blackout durante il movimento?
**A:** La posizione viene salvata periodicamente. Al riavvio, riprende dall'ultima posizione nota. Potrebbe esserci una piccola imprecisione (<5%).

### Q: Devo ricalibrare periodicamente?
**A:** Consigliato ogni 6-12 mesi, o se noti imprecisioni. I motori possono rallentare con l'usura.

### Q: Come funziona l'auto-calibrazione ai finecorsa?
**A:** Ogni volta che raggiungi 0% o 100% (finecorsa meccanico), la posizione viene automaticamente corretta. Questo compensa derive accumulate nel tempo.

### Q: Perché `assumed_state = False` per tracking time-based?
**A:** Perché Home Assistant **calcola esattamente** la posizione. Non è "assunta" da fonte esterna, è deterministica. Questo permette la corretta gestione dei pulsanti UI.

### Q: Quanto è preciso il tracking?
**A:** Con calibrazione accurata: errore <3%. L'auto-calibrazione ai finecorsa azzera la deriva periodicamente.

### Q: Posso usarlo con veneziane (tilt)?
**A:** Il tracking posizione funziona per apertura/chiusura. Il tilt usa i comandi Vimar standard (se supportato dall'hardware).

### Q: Consuma risorse CPU/memoria?
**A:** Minimo. Aggiorna ogni 0.2s solo durante movimento. A riposo, zero overhead.

### Q: Come faccio backup della configurazione?
**A:** Includi nel backup:
```bash
/config/.storage/core.entity_registry  # Entity options (tempi)
/config/.storage/core.restore_state    # Posizioni correnti
/config/configuration.yaml             # cover_position_mode
```

---

## ⚠️ Limitazioni

### Limitazioni Tecniche

1. **Precisione Tempo-Dipendente**: Basata sui tempi configurati. Errori di calibrazione = imprecisione posizione.

2. **Inerzia Meccanica**: Il motore ha inerzia. Comando stop ≠ stop istantaneo.

3. **Deriva nel Tempo**: Motori possono rallentare con usura. Mitigato dall'auto-calibrazione ai finecorsa.

4. **Nessun Feedback Blocchi**: Il sistema non rileva se la tapparella è bloccata fisicamente.

5. **Movimenti Manuali**: Se muovi via pulsante fisico (non tramite HA), la posizione viene **rilevata** al successivo polling.

### Limitazioni Funzionali

1. **Modalità Globale**: `cover_position_mode` è globale, non per singola cover.

2. **Richiede Calibrazione Iniziale**: Devi misurare i tempi manualmente per ogni cover.

3. **Update Rate**: Aggiornamento ogni 0.2 secondi (limite accettabile).

### Best Practices

✅ **Calibra con Precisione**: Usa cronometro digitale, fai media di 3 misurazioni
✅ **Cicli Completi Periodici**: Fai apertura/chiusura completa settimanalmente per auto-calibrazione
✅ **Ricalibra Dopo Manutenzione**: Se cambi motore o cinghie
✅ **Backup Configurazione**: Esporta `entity_registry` periodicamente
✅ **Monitora Log**: Controlla eventuali errori dopo aggiornamenti
✅ **Test Dopo Upgrade HA**: Verifica funzionalità dopo upgrade maggiori

---

## 🎉 Vantaggi Rispetto al Master

Questo branch `timed-shutters` introduce:

1. ✅ **3 Modalità Configurabili**: AUTO | TIME_BASED | NATIVE
2. ✅ **Entity Options**: Tempi salvati per singola cover (no config flow)
3. ✅ **Configuration.yaml**: Configurazione più semplice
4. ✅ **Auto-Calibrazione Finecorsa**: Risincronizzazione automatica
5. ✅ **Assumed State Corretto**: Gestione pulsanti UI perfetta
6. ✅ **Alta Granularità**: Update ogni 0.2s (era 1s)
7. ✅ **Attributi Extra**: `position_mode`, `uses_time_based_tracking`
8. ✅ **Ottimizzazione Finecorsa**: No STOP command a 0%/100%

---

## 📚 Riferimenti

### Repository GitHub
- **Branch**: `timed-shutters`
- **Repository**: https://github.com/WhiteWolf84/home-assistant-vimar

### File Principali
- `cover.py` - Implementazione completa con 3 modalità
- `const.py` - Costanti CONF_COVER_POSITION_MODE
- `services.yaml` - Servizio set_travel_times

### Versioni
- **Branch Version**: timed-shutters
- **Compatibilità HA**: 2023.1+
- **Ultima Modifica**: Febbraio 2026

### Crediti
- **Implementazione Time-Based Tracking**: Sviluppata in collaborazione
- **Auto-Calibrazione Finecorsa**: Feature richiesta dall'utente
- **Testing & Debug**: WhiteWolf84

---

## 🎊 Conclusione

Hai ora un sistema **completo e ottimizzato** di tracking posizione per le tue tapparelle Vimar!

### Caratteristiche Uniche:
- 🎛️ Tre modalità configurabili per ogni esigenza
- 🎯 Auto-calibrazione che azzera derive nel tempo
- ⚙️ Ottimizzazione usura hardware (no STOP ai finecorsa)
- 💾 Configurazione persistente per-entity
- 🎨 UI perfetta con pulsanti disabilitati correttamente

### Prossimi Passi:
1. ✅ Configura `cover_position_mode` in configuration.yaml
2. ✅ Calibra i tempi di ogni cover con `set_travel_times`
3. ✅ Testa apertura/chiusura parziali
4. ✅ Crea automazioni personalizzate
5. ✅ Fai cicli completi periodici per mantenere precisione

**Buon uso! 🏠✨**

---

*Ultimo aggiornamento: Febbraio 2026 - Branch timed-shutters*