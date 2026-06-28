"""Platform for cover integration - CON ENTITY OPTIONS.

Configurazione travel times tramite UI di ogni singola cover!
"""

import asyncio
import logging
from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COVER_POSITION_MODE,
    COVER_POSITION_MODE_LEGACY,
    COVER_POSITION_MODE_NATIVE,
    COVER_POSITION_MODE_TIME_BASED,
    DEFAULT_COVER_POSITION_MODE,
)
from .const import (
    DEVICE_TYPE_COVERS as CURR_PLATFORM,
)
from .vimar_entity import VimarEntity, vimar_setup_entry

_LOGGER = logging.getLogger(__name__)

DEFAULT_TRAVEL_TIME_UP = 28
DEFAULT_TRAVEL_TIME_DOWN = 26
POSITION_UPDATE_INTERVAL = 0.2
UI_UPDATE_THRESHOLD = 1  # Aggiorna UI ogni 1% di variazione
RELAY_DELAY = 0.5  # Compensazione ritardo relè Vimar in secondi
GRACE_SECONDS = 6  # Floor della finestra di immunità post-STOP da HA:
# il webserver Vimar non espone metadati sulla sorgente
# del comando (DPADD_OBJECT ha solo CURRENT_VALUE),
# quindi sopprimiamo le detection di pulsante fisico
# dopo ogni stop inviato da HA. La durata effettiva e'
# calcolata in _grace_seconds() in funzione del polling.
GRACE_MARGIN = 4  # Margine oltre l'intervallo di polling per il grace period:
# il primo poll dopo lo STOP DEVE cadere dentro il grace per
# risincronizzare _tb_last_updown (altrimenti il latch up/down
# viene scambiato per un pulsante fisico -> salti a 0/100).

# Chiavi per storage entity options
CONF_TRAVEL_TIME_UP = "travel_time_up"
CONF_TRAVEL_TIME_DOWN = "travel_time_down"

# Recovery post-riavvio (vedi _async_recover): se HA riavvia mentre una cover
# time-based sta muovendo, lo STOP pendente non parte e la tapparella prosegue
# fino al fondo-corsa meccanico. Al riavvio guidiamo la cover al fondo-corsa
# nella direzione in cui stava andando (riferimento certo) e riprendiamo verso
# il target interrotto. RECOVERY_MAX_AGE_SECONDS scarta flag troppo vecchi
# (es. HA spento per ore): oltre questa eta' il "target interrotto" non e' piu'
# rilevante e si rischia un movimento a sorpresa non voluto.
RECOVERY_MAX_AGE_SECONDS = 1800
# Attributi persistiti via RestoreEntity SOLO durante un movimento.
ATTR_RECOVERY_DIRECTION = "tb_recovery_direction"
ATTR_RECOVERY_TARGET = "tb_recovery_target"
ATTR_RECOVERY_TS = "tb_recovery_ts"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_devices: AddEntitiesCallback
) -> None:
    """Set up the Vimar Cover platform."""
    vimar_setup_entry(VimarCover, CURR_PLATFORM, hass, entry, async_add_devices)

    # Registra servizio per configurare travel times
    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        "set_travel_times",
        {
            vol.Required(CONF_TRAVEL_TIME_UP): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
            vol.Required(CONF_TRAVEL_TIME_DOWN): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=300)
            ),
        },
        "async_set_travel_times",
    )


class VimarCover(VimarEntity, CoverEntity, RestoreEntity):
    """Provides a Vimar cover with time-based position tracking."""

    @property
    def assumed_state(self) -> bool:
        """Return True if state is assumed (estimated), False if known (certain).

        True = State is ASSUMED/ESTIMATED (cannot access real position)
        False = State is KNOWN/CERTAIN (have accurate position info)

        LEGACY mode: Always True (like original master branch)
        NATIVE mode: True if no sensor, False if has sensor
        TIME_BASED mode: False (calculated position is "known")
        AUTO mode: False (either native sensor or time-based calculation)
        """
        mode = self._get_position_mode()

        if mode == COVER_POSITION_MODE_LEGACY:
            # LEGACY: Always True (original master behavior)
            return True

        if mode == COVER_POSITION_MODE_NATIVE:
            # NATIVE: True if no sensor (assumed), False if has sensor (known)
            return not self.has_state("position")

        # TIME_BASED or AUTO modes:
        # - Time-based tracking provides calculated position -> False (known)
        # - Native sensor provides hardware position -> False (known)
        return False

    def __init__(self, coordinator, device_id: int):
        """Initialize the cover."""
        VimarEntity.__init__(self, coordinator, device_id)

        # Time-based tracking
        self._tb_position: int | None = None
        self._tb_target: int | None = None
        self._tb_start_time: datetime | None = None
        self._tb_start_position: int | None = None
        self._tb_operation: str | None = None
        self._tb_unsub = None
        self._tb_last_updown: str | None = None
        self._tb_last_reported_position: int | None = None  # Per threshold UI
        self._tb_ha_command_active = False  # Flag per distinguere comandi HA da pulsanti fisici
        # Timestamp ultimo STOP inviato da HA (grace period)
        self._tb_ha_stop_time: datetime | None = None
        # True quando _tb_update_position ha gia' finalizzato la posizione
        # (target o fondo-corsa raggiunto): _tb_stop_tracking non deve
        # ricalcolarla dal tempo trascorso.
        self._tb_planned_stop = False

        # Recovery post-riavvio interrotto a meta' movimento (vedi _async_recover)
        self._recovery_pending = False
        self._recovery_direction: str | None = None
        self._recovery_target: int | None = None

        # Background task in volo (stop tracking / recovery). Tracciati sul
        # registro core via async_create_background_task e cancellati in
        # async_will_remove_from_hass, cosi' un reload/shutdown a meta' movimento
        # non lascia task orfani che lanciano eccezioni a runtime.
        self._background_tasks: set = set()

        # Travel times (saranno caricati in async_added_to_hass)
        self._travel_time_up = DEFAULT_TRAVEL_TIME_UP
        self._travel_time_down = DEFAULT_TRAVEL_TIME_DOWN

    def _get_position_mode(self) -> str:
        """Get configured position mode from coordinator."""
        if hasattr(self.coordinator, "vimarconfig"):
            return self.coordinator.vimarconfig.get(
                CONF_COVER_POSITION_MODE, DEFAULT_COVER_POSITION_MODE
            )
        return DEFAULT_COVER_POSITION_MODE

    def _use_time_based_tracking(self) -> bool:
        """Determine if time-based tracking should be used."""
        mode = self._get_position_mode()

        if mode == COVER_POSITION_MODE_LEGACY:
            # LEGACY mode: disable all time-based tracking (original master behavior)
            return False
        elif mode == COVER_POSITION_MODE_TIME_BASED:
            # Force time-based even if native position is available
            return True
        elif mode == COVER_POSITION_MODE_NATIVE:
            # Never use time-based, rely on native only
            return False
        else:  # COVER_POSITION_MODE_AUTO or default
            # Use time-based only if native position is not available
            return not self.has_state("position")

    async def async_set_travel_times(self, travel_time_up: int, travel_time_down: int):
        """Service to set travel times for this cover."""
        self._travel_time_up = travel_time_up
        self._travel_time_down = travel_time_down

        # Salva nelle entity options
        if hasattr(self, "registry_entry") and self.registry_entry:
            from homeassistant.helpers import entity_registry as er

            entity_reg = er.async_get(self.hass)
            entity_reg.async_update_entity_options(
                self.entity_id,
                "cover",
                {
                    CONF_TRAVEL_TIME_UP: travel_time_up,
                    CONF_TRAVEL_TIME_DOWN: travel_time_down,
                },
            )

        _LOGGER.info(
            "%s: Travel times updated - up: %ds, down: %ds",
            self.name,
            travel_time_up,
            travel_time_down,
        )

    async def async_added_to_hass(self):
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        _LOGGER.debug("%s: === async_added_to_hass START ===", self.name)
        _LOGGER.debug("%s: Position mode: %s", self.name, self._get_position_mode())
        _LOGGER.debug("%s: Use time-based tracking: %s", self.name, self._use_time_based_tracking())

        # Carica travel times dalle entity options
        if hasattr(self, "registry_entry") and self.registry_entry:
            options = self.registry_entry.options.get("cover", {})

            saved_up = options.get(CONF_TRAVEL_TIME_UP)
            saved_down = options.get(CONF_TRAVEL_TIME_DOWN)

            if saved_up is not None:
                self._travel_time_up = int(saved_up)
            if saved_down is not None:
                self._travel_time_down = int(saved_down)

            if (
                self._travel_time_up != DEFAULT_TRAVEL_TIME_UP
                or self._travel_time_down != DEFAULT_TRAVEL_TIME_DOWN
            ):
                _LOGGER.info(
                    "%s: Custom travel times loaded - up: %ds, down: %ds",
                    self.name,
                    self._travel_time_up,
                    self._travel_time_down,
                )

        # Ripristina posizione solo se usiamo time-based tracking
        if self._use_time_based_tracking():
            old_state = await self.async_get_last_state()

            _LOGGER.debug("%s: old_state exists = %s", self.name, old_state is not None)

            if old_state:
                _LOGGER.debug("%s: old_state.state = '%s'", self.name, old_state.state)
                position_attr = old_state.attributes.get("current_position")
                _LOGGER.debug("%s: current_position value = %s", self.name, position_attr)

            if old_state and old_state.attributes.get("current_position") is not None:
                self._tb_position = old_state.attributes["current_position"]
                _LOGGER.info("%s: Position restored: %s%%", self.name, self._tb_position)
            else:
                self._tb_position = 0
                _LOGGER.info("%s: New cover, default position: 0%% (closed)", self.name)

            # Se il riavvio ha interrotto un movimento, programma il recupero.
            if old_state is not None:
                self._detect_pending_recovery(old_state)
                self._maybe_start_recovery()
        else:
            mode = self._get_position_mode()
            if mode == COVER_POSITION_MODE_LEGACY:
                _LOGGER.debug("%s: LEGACY mode - no time-based tracking", self.name)
            else:
                _LOGGER.debug("%s: Using native position from webserver", self.name)

        self._tb_last_updown = self.get_state("up/down")
        self._tb_last_reported_position = self._tb_position
        _LOGGER.debug("%s: === async_added_to_hass END ===", self.name)

    def _create_tracked_task(self, coro, name: str) -> None:
        """Crea un background task tracciato dal core e auto-rimosso a fine vita.

        Usa async_create_background_task (registrato sul core) invece di
        async_create_task nudo: a un reload/shutdown a meta' movimento il task
        viene cancellato in async_will_remove_from_hass invece di restare orfano.
        """
        task = self.hass.async_create_background_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def async_will_remove_from_hass(self):
        """Cleanup when removed."""
        await super().async_will_remove_from_hass()
        if self._tb_unsub:
            self._tb_unsub()
            self._tb_unsub = None
        # Cancella i task in volo (recovery / stop tracking) per non lasciarli
        # orfani dopo un reload o uno shutdown a meta' movimento.
        for task in list(self._background_tasks):
            task.cancel()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        super()._handle_coordinator_update()
        if self._use_time_based_tracking():
            # Avvia un recovery pendente appena la connessione e' pronta
            # (in async_added_to_hass self.available potrebbe non esserlo ancora).
            self._maybe_start_recovery()
            self._tb_check_vimar_state()

    def _grace_seconds(self) -> float:
        """Durata del grace period post-STOP, >= un ciclo di polling + margine.

        Dopo uno STOP da HA il webserver lascia 'up/down' latchato sull'ultima
        direzione: il primo poll successivo deve cadere DENTRO il grace per
        risincronizzare _tb_last_updown senza scambiare il latch per un
        pulsante fisico. Con un grace fisso < intervallo di polling questo non
        era garantito (causa dei salti di posizione a 0/100, FIX #1).
        """
        interval = getattr(self.coordinator, "update_interval", None)
        if interval is None:
            return GRACE_SECONDS
        return max(GRACE_SECONDS, interval.total_seconds() + GRACE_MARGIN)

    def _detect_pending_recovery(self, old_state) -> None:
        """Rileva dallo stato ripristinato un movimento interrotto dal riavvio.

        Arma il recovery solo se gli attributi in volo sono presenti e il
        timestamp e' fresco (< RECOVERY_MAX_AGE_SECONDS): un flag vecchio (es.
        HA spento per ore, o lasciato da un dump periodico prima di un crash)
        non e' affidabile e verrebbe ignorato per non muovere a sorpresa.
        """
        direction = old_state.attributes.get(ATTR_RECOVERY_DIRECTION)
        if direction not in ("opening", "closing"):
            return

        ts_raw = old_state.attributes.get(ATTR_RECOVERY_TS)
        if not ts_raw:
            # Senza timestamp non possiamo valutare la freschezza: troppo
            # rischioso, non recuperiamo.
            _LOGGER.debug("%s: recovery flag senza timestamp, ignorato", self.name)
            return
        try:
            age = (dt_util.utcnow() - datetime.fromisoformat(ts_raw)).total_seconds()
        except (ValueError, TypeError):
            _LOGGER.debug("%s: recovery timestamp non valido (%s), ignorato", self.name, ts_raw)
            return
        if age < 0 or age > RECOVERY_MAX_AGE_SECONDS:
            _LOGGER.info(
                "%s: recovery flag scartato (eta' %.0fs fuori range 0..%ds)",
                self.name,
                age,
                RECOVERY_MAX_AGE_SECONDS,
            )
            return

        target_raw = old_state.attributes.get(ATTR_RECOVERY_TARGET)
        try:
            target = int(target_raw) if target_raw is not None else None
        except (ValueError, TypeError):
            target = None

        self._recovery_direction = direction
        self._recovery_target = target
        self._recovery_pending = True
        _LOGGER.warning(
            "%s: movimento interrotto dal riavvio rilevato (%s, target=%s, eta' %.0fs) "
            "-> recupero programmato",
            self.name,
            direction,
            target,
            age,
        )

    def _maybe_start_recovery(self) -> None:
        """Avvia il recovery se pendente e la cover e' pronta (one-shot).

        Eseguito sia in async_added_to_hass sia ad ogni coordinator update:
        la transizione pending->False e' sincrona (nessun await in mezzo),
        quindi il task di recupero non puo' partire due volte.
        """
        if self._recovery_pending and self.available:
            self._recovery_pending = False
            direction = self._recovery_direction
            target = self._recovery_target
            self._recovery_direction = None
            self._recovery_target = None
            self._create_tracked_task(
                self._async_recover(direction, target), name="vimar_cover_recovery"
            )

    async def _tb_wait_idle(self, timeout: float) -> bool:
        """Attende la fine del tracking (operation None) o lo scadere del timeout.

        Ritorna True se e' diventato idle, False se e' scaduto il timeout.
        """
        loop = self.hass.loop
        deadline = loop.time() + timeout
        while self._tb_operation is not None:
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(POSITION_UPDATE_INTERVAL)
        return True

    async def _async_recover(self, direction: str | None, target: int | None) -> None:
        """Recupera la posizione dopo un riavvio che ha interrotto un movimento.

        Lo STOP pendente e' andato perso: la tapparella ha proseguito verso il
        fondo-corsa nella direzione in cui stava andando (overshoot). Non
        sappiamo dove si sia fermata, quindi la guidiamo a quel fondo-corsa
        (riferimento meccanico certo) assumendo lo scenario peggiore (cover
        all'estremo opposto), cosi' la corsa a tempo copre l'intera tratta ed e'
        garantito il raggiungimento del fine-corsa. Poi riprendiamo verso il
        target interrotto, ma SOLO se la corsa di recupero e' arrivata
        indisturbata (idle ed esattamente sul fondo-corsa): se nel frattempo un
        comando esterno/pulsante e' intervenuto, lasciamo perdere il resume.
        """
        if direction not in ("opening", "closing"):
            return
        # Se nel frattempo un movimento e' gia' in corso (comando utente/automazione
        # subito dopo l'avvio), non interferire: chi ha preso il controllo vince.
        if self._tb_operation is not None:
            _LOGGER.info("%s: recovery saltato, movimento gia' in corso (%s)", self.name, self._tb_operation)
            return
        opening = direction == "opening"
        end_stop = 100 if opening else 0
        travel = self._travel_time_up if opening else self._travel_time_down

        _LOGGER.warning(
            "%s: recovery -> guido al fondo-corsa %s%% (direzione %s), poi riprendo verso target=%s",
            self.name,
            end_stop,
            direction,
            target,
        )

        # Scenario peggiore: cover all'estremo opposto, cosi' la corsa a tempo
        # copre tutta la tratta e raggiunge sicuramente il fine-corsa meccanico.
        self._tb_position = 0 if opening else 100
        if opening:
            await self.async_open_cover()
        else:
            await self.async_close_cover()

        # Attendi il completamento reale (fine-corsa), non uno sleep fisso.
        if not await self._tb_wait_idle(timeout=travel + RELAY_DELAY + 5):
            _LOGGER.warning(
                "%s: recovery scaduto in attesa del fondo-corsa, resume annullato", self.name
            )
            return

        # Resume solo se la corsa di recupero e' arrivata indisturbata.
        if self._tb_operation is not None or self._tb_position != end_stop:
            _LOGGER.info(
                "%s: recovery interrotto da un comando esterno (op=%s, pos=%s) - niente resume",
                self.name,
                self._tb_operation,
                self._tb_position,
            )
            return

        # Riprendi verso il target interrotto solo se intermedio e diverso dal
        # fondo-corsa gia' raggiunto (se il target era 0/100 abbiamo finito).
        if target is not None and 0 < target < 100 and target != end_stop:
            _LOGGER.info("%s: recovery completato, riprendo verso target %s%%", self.name, target)
            await self.async_set_cover_position(**{ATTR_POSITION: target})
        else:
            _LOGGER.info("%s: recovery completato al fondo-corsa %s%%", self.name, end_stop)

    def _tb_check_vimar_state(self) -> None:
        """Controlla stato Vimar e gestisci movimenti fisici."""
        current_updown = self.get_state("up/down")

        # Durante tracking da comandi HA, verifica solo interruzioni (STOP fisico)
        if self._tb_operation:
            expected_updown = "0" if self._tb_operation == "opening" else "1"

            # Se lo stato cambia inaspettatamente durante tracking HA
            if current_updown != expected_updown:
                _LOGGER.info(
                    "%s: Physical STOP detected during HA tracking! up/down=%s (was %s)",
                    self.name,
                    current_updown,
                    self._tb_operation,
                )
                # Reset del flag comando HA perché è stato interrotto fisicamente
                self._tb_ha_command_active = False
                self._create_tracked_task(
                    self._tb_stop_tracking(), name="vimar_cover_stop_tracking"
                )

            # FIX: aggiorna sempre _tb_last_updown durante il tracking, altrimenti
            # il valore stantio dopo lo stop causa una falsa detection di pulsante fisico
            self._tb_last_updown = current_updown
            return

        # Rileva movimenti da pulsanti fisici solo quando:
        # 1. NON c'è tracking attivo (_tb_operation è None)
        # 2. Lo stato up/down è cambiato rispetto all'ultimo valore
        # 3. NON è un comando HA recente (_tb_ha_command_active)
        # 4. NON siamo nel grace period post-STOP di HA
        #    (il webserver Vimar non distingue la sorgente del comando:
        #    DPADD_OBJECT espone solo CURRENT_VALUE senza metadati di origine)
        grace = self._grace_seconds()
        in_grace_period = (
            self._tb_ha_stop_time is not None
            and (dt_util.utcnow() - self._tb_ha_stop_time).total_seconds() < grace
        )

        if current_updown != self._tb_last_updown and not self._tb_ha_command_active:
            if in_grace_period:
                _LOGGER.debug(
                    "%s: Ignoring up/down change (%s->%s) - in grace period (%.1fs remaining)",
                    self.name,
                    self._tb_last_updown,
                    current_updown,
                    grace - (dt_util.utcnow() - self._tb_ha_stop_time).total_seconds(),
                )
            elif current_updown == "0":
                self._tb_position = 100
                _LOGGER.info("%s: Physical button OPEN -> Position set to 100%%", self.name)
                self._tb_last_reported_position = 100
                self.async_write_ha_state()

            elif current_updown == "1":
                self._tb_position = 0
                _LOGGER.info("%s: Physical button CLOSE -> Position set to 0%%", self.name)
                self._tb_last_reported_position = 0
                self.async_write_ha_state()

        self._tb_last_updown = current_updown

    async def _tb_start_tracking(self, opening: bool, target: int | None = None):
        """Avvia tracking temporale per comandi HA."""
        operation = "opening" if opening else "closing"

        if self._tb_operation == operation:
            return

        # _tb_position resta None finché async_added_to_hass non ha
        # ripristinato lo stato: un comando arrivato prima parte da 0 (chiusa).
        if self._tb_position is None:
            self._tb_position = 0

        self._tb_operation = operation
        self._tb_start_time = dt_util.utcnow()
        self._tb_start_position = self._tb_position
        self._tb_target = target if target is not None else (100 if opening else 0)
        self._tb_last_reported_position = self._tb_position

        # Imposta flag comando HA per evitare false detection di pulsanti fisici
        self._tb_ha_command_active = True
        # Reset grace period: un nuovo movimento annulla la finestra precedente
        self._tb_ha_stop_time = None
        # Nuovo movimento: nessuno stop pianificato in sospeso.
        self._tb_planned_stop = False

        if self._tb_unsub:
            self._tb_unsub()

        self._tb_unsub = async_track_time_interval(
            self.hass,
            self._tb_update_position,
            timedelta(seconds=POSITION_UPDATE_INTERVAL),
        )

        _LOGGER.debug(
            "%s: Tracking %s from %s%% to %s%%",
            self.name,
            operation,
            self._tb_position,
            self._tb_target,
        )
        self.async_write_ha_state()

    async def _tb_stop_tracking(self):
        """Ferma tracking e calcola posizione finale."""
        if self._tb_unsub:
            self._tb_unsub()
            self._tb_unsub = None

        if self._tb_planned_stop:
            # Stop pianificato (target o fondo-corsa raggiunto): la posizione e'
            # gia' finalizzata in _tb_update_position, NON ricalcolarla dal tempo
            # (il ricalcolo la riporterebbe a target-margine: era la causa della
            # chiusura piena ferma a 1% invece di 0%).
            self._tb_planned_stop = False
        elif self._tb_start_time:
            self._tb_calculate_position()

        _LOGGER.info("%s: Stopped at %s%%", self.name, self._tb_position)

        self._tb_operation = None
        self._tb_start_time = None
        self._tb_target = None
        self._tb_last_reported_position = self._tb_position

        # Reset flag comando HA e avvia grace period:
        # per GRACE_SECONDS le detection di pulsante fisico sono soppresse
        # perché il protocollo Vimar non distingue la sorgente del comando.
        self._tb_ha_command_active = False
        self._tb_ha_stop_time = dt_util.utcnow()

        self.async_write_ha_state()

    def _overshoot_pct(self, opening: bool) -> float:
        """Percentuale di corsa percorsa durante il ritardo relè allo STOP.

        Dopo l'invio dello STOP il motore continua ~RELAY_DELAY secondi: questa
        e' la corsa di "coasting" oltre il target. Lo start e' gia' compensato
        in _tb_calculate_position; questa quantita' serve a:
          - anticipare lo STOP di altrettanto (compensazione overshoot, FIX #2)
            cosi' la tapparella plana sul target invece di superarlo;
          - fare da deadband minimo in async_set_cover_position: un movimento
            piu' piccolo di questo non e' posizionabile (la coda relè lo
            supererebbe) e va ignorato per non far ticchettare i relè.
        Legare le due cose alla STESSA grandezza garantisce che un movimento
        che passa il deadband non possa mai fermarsi al primo tick (no chatter).
        """
        travel = self._travel_time_up if opening else self._travel_time_down
        return (RELAY_DELAY / travel) * 100 if travel else 0.0

    @callback
    def _tb_update_position(self, now: datetime) -> None:
        """Aggiorna posizione durante tracking ogni 1%."""
        self._tb_calculate_position()

        should_stop = False
        send_stop_command = False

        if self._tb_position >= 100 and self._tb_operation == "opening":
            self._tb_position = 100
            should_stop = True
            send_stop_command = False

        elif self._tb_position <= 0 and self._tb_operation == "closing":
            self._tb_position = 0
            should_stop = True
            send_stop_command = False

        elif self._tb_target is not None and 0 < self._tb_target < 100:
            # Compensazione overshoot (FIX #2): anticipa lo STOP di stop_margin
            # cosi' la tapparella plana sul target invece di superarlo. SOLO per
            # target intermedi: i fondo-corsa 0/100 sono gestiti dai due rami
            # sopra (devono raggiungere il fine-corsa meccanico esatto).
            # Applicare il margine anche a 0/100 fermava la corsa ~1 punto prima
            # del fondo (chiusura piena ferma a 1% invece di 0%). Il deadband in
            # async_set_cover_position garantisce delta > stop_margin, quindi al
            # primo tick (pos == start) non si ferma mai (no chatter).
            stop_margin = self._overshoot_pct(self._tb_operation == "opening")
            if (
                self._tb_operation == "opening"
                and self._tb_position >= self._tb_target - stop_margin
                or self._tb_operation == "closing"
                and self._tb_position <= self._tb_target + stop_margin
            ):
                self._tb_position = self._tb_target
                should_stop = True
                send_stop_command = True

        if should_stop:
            # Posizione finale gia' impostata sopra: _tb_stop_tracking non deve
            # ricalcolarla (vedi _tb_planned_stop).
            self._tb_planned_stop = True
            if send_stop_command:
                _LOGGER.info("%s: Reached target %s%%, sending STOP", self.name, self._tb_position)
                # FIX: async_stop_cover chiama già _tb_stop_tracking internamente,
                # non schedulare un task separato per evitare doppia esecuzione
                self._create_tracked_task(
                    self.async_stop_cover(), name="vimar_cover_stop"
                )
            else:
                _LOGGER.info(
                    "%s: Reached end-stop %s%%, mechanical stop (no STOP command)",
                    self.name,
                    self._tb_position,
                )
                self._create_tracked_task(
                    self._tb_stop_tracking(), name="vimar_cover_stop_tracking"
                )
        else:
            # Aggiorna UI ogni 1% di variazione (o più frequente se UI_UPDATE_THRESHOLD < 1)
            if (
                self._tb_last_reported_position is None
                or abs(self._tb_position - self._tb_last_reported_position) >= UI_UPDATE_THRESHOLD
            ):
                self._tb_last_reported_position = self._tb_position
                self.async_write_ha_state()

    def _tb_calculate_position(self) -> None:
        """Calcola posizione attuale basata sul tempo trascorso con compensazione ritardo relè."""
        if not self._tb_start_time:
            return

        elapsed_total = (dt_util.utcnow() - self._tb_start_time).total_seconds()
        # Sottrae il ritardo stimato del relè (non scende sotto zero)
        elapsed_effective = max(0.0, elapsed_total - RELAY_DELAY)

        travel_time = (
            self._travel_time_up if self._tb_operation == "opening" else self._travel_time_down
        )
        percentage = (elapsed_effective / travel_time) * 100

        if self._tb_operation == "opening":
            self._tb_position = min(100, self._tb_start_position + percentage)
        else:
            self._tb_position = max(0, self._tb_start_position - percentage)

        self._tb_position = round(self._tb_position)

    @property
    def entity_platform(self):
        return CURR_PLATFORM

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is closed, None if unknown."""
        if self._use_time_based_tracking():
            # Time-based mode: la posizione calcolata è l'unica fonte
            if self._tb_position is not None:
                return self._tb_position == 0
            return None

        # LEGACY e native: deduci dall'ultimo comando up/down inviato
        updown = self.get_state("up/down")
        if updown == "1":
            return True
        if updown == "0":
            return False
        return None

    @property
    def is_opening(self) -> bool:
        """Return True only during active opening operation.

        Home Assistant disables buttons based on is_closed and current_cover_position,
        NOT based on is_opening/is_closing. These properties only indicate ACTIVE movement.
        """
        if self._use_time_based_tracking():
            return self._tb_operation == "opening"
        return False

    @property
    def is_closing(self) -> bool:
        """Return True only during active closing operation.

        Home Assistant disables buttons based on is_closed and current_cover_position,
        NOT based on is_opening/is_closing. These properties only indicate ACTIVE movement.
        """
        if self._use_time_based_tracking():
            return self._tb_operation == "closing"
        return False

    @property
    def current_cover_position(self) -> int | None:
        """Return current position (0 closed, 100 open), None if unknown."""
        if not self._use_time_based_tracking():
            # LEGACY/native: posizione solo dal sensore hardware, se presente
            if self.has_state("position"):
                return 100 - int(self.get_state("position"))
            return None
        # Time-based mode
        return self._tb_position

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return current tilt position, None if unknown."""
        if self.has_state("slat_position"):
            return 100 - int(self.get_state("slat_position"))
        return None

    @property
    def is_default_state(self):
        """Return True when closed or unknown - selects the default icon."""
        closed = self.is_closed
        return True if closed is None else closed

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Flag supported features.

        In LEGACY mode, SET_POSITION is only available if hardware sensor exists.
        In other modes, SET_POSITION is always available.
        """
        mode = self._get_position_mode()

        flags = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP

        # SET_POSITION logic based on mode
        if mode == COVER_POSITION_MODE_LEGACY:
            # LEGACY mode: SET_POSITION only if native sensor available
            if self.has_state("position"):
                flags |= CoverEntityFeature.SET_POSITION
        else:
            # All other modes: always include SET_POSITION
            flags |= CoverEntityFeature.SET_POSITION

        if self.has_state("slat_position") and self.has_state("clockwise/counterclockwise"):
            flags |= (
                CoverEntityFeature.STOP_TILT
                | CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )

        return flags

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        attrs = super().extra_state_attributes or {}
        attrs["position_mode"] = self._get_position_mode()
        attrs["uses_time_based_tracking"] = self._use_time_based_tracking()
        if self._use_time_based_tracking():
            attrs["travel_time_up"] = self._travel_time_up
            attrs["travel_time_down"] = self._travel_time_down
            # Persisti il movimento in volo SOLO mentre e' attivo: se HA riavvia
            # ora, lo STOP pendente non parte e la tapparella va a fondo-corsa.
            # RestoreEntity salva questi attributi (anche allo shutdown pulito);
            # al riavvio _detect_pending_recovery li rilegge. A movimento finito
            # _tb_operation torna None e gli attributi spariscono dallo stato
            # salvato -> nessun recovery su uno spegnimento da fermo.
            if self._tb_operation is not None and self._tb_start_time is not None:
                attrs[ATTR_RECOVERY_DIRECTION] = self._tb_operation
                attrs[ATTR_RECOVERY_TARGET] = self._tb_target
                attrs[ATTR_RECOVERY_TS] = self._tb_start_time.isoformat()
        return attrs

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        if self._use_time_based_tracking():
            await self._tb_start_tracking(False, target=0)
        self.change_state("up/down", "1")

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        if self._use_time_based_tracking():
            await self._tb_start_tracking(True, target=100)
        self.change_state("up/down", "0")

    async def async_stop_cover(self, **kwargs):
        if self._use_time_based_tracking():
            await self._tb_stop_tracking()
        self.change_state("stop up/stop down", "1")

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION not in kwargs:
            return

        target = int(kwargs[ATTR_POSITION])

        if not self._use_time_based_tracking() and self.has_state("position"):
            # Native mode (or LEGACY with sensor)
            self.change_state("position", 100 - target)
            return

        # Time-based mode.
        # FIX #3: _tb_position is None until async_added_to_hass runs.
        # Guard against TypeError from comparing int with None.
        if self._tb_position is None:
            _LOGGER.debug(
                "%s: set_cover_position called before position was initialized, "
                "defaulting to 0 (closed)",
                self.name,
            )
            self._tb_position = 0

        if target == self._tb_position:
            return

        opening = target > self._tb_position
        delta = abs(target - self._tb_position)

        # Deadband (FIX #2): un movimento <= alla coda relè non e' posizionabile
        # (il coasting dopo lo STOP lo supererebbe) e produrrebbe solo un
        # ticchettio del relè. Lo ignoriamo. La soglia e' la stessa quantita'
        # usata per la compensazione overshoot, cosi' i due meccanismi sono
        # coerenti e non si genera mai uno start+stop immediato.
        if delta <= self._overshoot_pct(opening):
            _LOGGER.debug(
                "%s: set_position %s%% ignorato (delta %s%% <= coda relè %.1f%%, non posizionabile)",
                self.name,
                target,
                delta,
                self._overshoot_pct(opening),
            )
            return

        if opening:
            await self._tb_start_tracking(True, target=target)
            self.change_state("up/down", "0")
        else:
            await self._tb_start_tracking(False, target=target)
            self.change_state("up/down", "1")

    async def async_open_cover_tilt(self, **kwargs):
        self.change_state("clockwise/counterclockwise", "0")

    async def async_close_cover_tilt(self, **kwargs):
        self.change_state("clockwise/counterclockwise", "1")

    async def async_set_cover_tilt_position(self, **kwargs):
        """Move the cover tilt to a specific position."""
        if ATTR_TILT_POSITION in kwargs and self.has_state("slat_position"):
            self.change_state("slat_position", 100 - int(kwargs[ATTR_TILT_POSITION]))

    async def async_stop_cover_tilt(self, **kwargs):
        self.change_state("stop up/stop down", "1")
