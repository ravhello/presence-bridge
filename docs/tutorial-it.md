---
title: Tutorial italiano
permalink: /tutorial-it/
---

# Tutorial in italiano

## Se un vecchio abbinamento impedisce il collegamento

Non cancellare gli abbinamenti che funzionano: vengono riutilizzati. Se HA
mostra `iphone_saved_bond_unreachable`, usa **Ripara abbinamento**, quando
disponibile. Conferma la rimozione del solo telefono indicato. Se il ricevitore
compare ancora in Impostazioni > Bluetooth sull'iPhone, dimenticalo anche li.
HA verifica la rimozione sul server e prepara un nuovo codice per la stessa
persona. Non servono comandi PowerShell o accesso allo schermo del server.
HA ricorda questa preparazione anche dopo un riavvio o la creazione di un altro
QR. Con Presence Pair 219 o successiva, il nuovo codice apre prima una schermata
di controllo: verifica l'elenco Bluetooth dell'iPhone e premi **Rimosso o non
presente: continua**. Solo allora parte il Bluetooth. Se il ricevitore non e
nell'elenco, puoi continuare senza rimuovere altri dispositivi. Le istruzioni
restano anche accanto al QR in HA per chi usa una versione precedente dell'app.
Un normale timeout o segnale debole non richiede questa cancellazione: il
tentativo viene chiuso, ma l'abbinamento valido resta riutilizzabile.
La riparazione guidata richiede il ricevitore Windows aggiornato con il suo
helper interattivo; non viene promessa su hardware o percorsi Linux non testati.
Se manca il pulsante, il telefono non e stato identificato con certezza: usa la
diagnostica, non rimuovere altri dispositivi a caso.

## 1. Verifica i requisiti

Questo tutorial descrive il percorso **Windows + MQTT**. Dalla versione 0.2.0
esiste anche Linux headless (sperimentale), remoto oppure integrato in HA senza
MQTT se sono accessibili BlueZ, D-Bus e archivio abbinamenti in sola lettura.
Consulta [scelta installazione](setup.md) e [Linux](linux.md). I proxy passivi
non sostituiscono un ricevitore per il primo abbinamento.

Servono Home Assistant 2025.1 o successivo, un broker MQTT già collegato a HA,
un PC Windows 10/11 sempre acceso con Bluetooth LE e un iPhone con iOS 17 o
successivo. Il PC deve rimanere in una posizione fissa.
L'adattatore deve supportare sia connessioni BLE centrali sia advertising
periferico. Durante l'abbinamento l'utente Windows configurato deve avere una
sessione aperta, anche bloccata; la scansione successiva funziona come servizio
SYSTEM anche prima del login. Non tutti gli adattatori sono stati collaudati.
Attenzione: dopo un riavvio, la schermata di login non e una sessione aperta.
Non abilitare il login automatico per aggirare questo requisito. Se HA e una
VM sullo stesso PC, lascia l'adattatore del ricevitore a Windows: il collegamento
con HA avviene via MQTT e non richiede il passthrough USB.

## 2. Installa l'integrazione gratuita

La versione attuale e la **0.2.4, release ordinaria**. Per il primo abbinamento
installa gratuitamente [Presence Pair dall'App Store](https://apps.apple.com/app/id6808059201).
Non serve un invito TestFlight. Dal 5 novembre 2026 l'integrazione e inclusa nel
catalogo HACS predefinito: non serve aggiungere un repository personalizzato.
Linux e la compatibilita hardware estesa restano sperimentali.

1. Apri HACS e cerca **Presence Bridge**.
2. Apri la pagina dell'integrazione: non serve configurare repository aggiuntivi.
3. Se il nuovo elemento non e ancora comparso, ricarica HACS e riprova; mantieni
   l'eventuale installazione personalizzata esistente senza crearne una duplicata.
4. Seleziona **v0.2.4** o l'ultima release ordinaria, installa **Presence Bridge**
   e riavvia Home Assistant. Non serve abilitare beta o pre-release.
5. Apri **Impostazioni > Dispositivi e servizi > Aggiungi integrazione**, cerca
   **Presence Bridge** e completa la configurazione.

In alternativa, estrai `presence_bridge-0.2.4.zip` dalla release nella cartella
`<config HA>/custom_components/presence_bridge/`. Il file `manifest.json` deve
trovarsi direttamente in quella cartella, non in una sottocartella aggiuntiva.
Prima di aggiornare crea un backup di HA, poi riavvia e aggiungi l'integrazione
come sopra. Per una nuova installazione usa il pacchetto Windows 0.2.4.

## 3. Prepara il ricevitore Windows

Scarica e decomprimi `presence-bridge-windows-VERSION.zip` dalla release
corrispondente. Sul PC fisso apri PowerShell **come amministratore** nella
cartella estratta che contiene `install.ps1` ed esegui:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Inserisci un ID stabile, per esempio `pc_salotto`, un nome leggibile e le
credenziali di un utente MQTT dedicato. La password viene richiesta in modo
interattivo e non compare nella cronologia dei comandi. Alla fine deve comparire
lo stato `Running` dell'attività pianificata.

Quando il ricevitore appare nella plancia Presence Bridge, assegnagli la stanza
in cui si trova. Il PC deve restare fisso: la stanza stimata corrisponde al
ricevitore che sente meglio l'iPhone.

## 4. Associa una persona

Apri **Presence Bridge** dalla barra laterale di HA, scegli la persona e il
ricevitore più vicino, quindi premi **Crea codice**. Se usi HA sullo stesso
iPhone premi **Apri Presence Pair**; se il QR e su un altro schermo, apri lo
scanner di Presence Pair e inquadralo. La versione attuale e gratuita, senza acquisti.
L'iPhone diventa temporaneamente visibile al ricevitore selezionato, che si
connette e accetta automaticamente. Se iOS lo chiede, consenti a Presence Pair
di usare il Bluetooth o tocca **Abbina**, poi attendi la conferma verde senza
chiudere l'app. Sul ricevitore Windows non devi premere o confermare nulla.

Se l'associazione non termina, leggi il titolo e il codice diagnostico mostrati
dall'app: distinguono permesso Bluetooth, ricevitore non trovato, connessione,
verifica del QR e legame cifrato. La plancia HA indica se il ricevitore sta cercando,
ha trovato l'iPhone o ha aperto la connessione; **Nuovo codice** riavvia l'intero
tentativo senza passaggi manuali sul PC.

Il codice e valido per dieci minuti per essere scansionato. Una scansione
eseguita in tempo avvia sull'iPhone un unico tentativo di cinque minuti, che
non viene interrotto dalla scadenza originaria. Appena il ricevitore verifica
quella precisa sessione, il QR viene consumato e non puo piu essere riutilizzato.
I cinque minuti comprendono avvicinamento, tentativi, riuso del legame e verifica HA.
La conferma finale appare solo dopo che HA ha salvato e verificato l'identita.
Non contiene password permanenti e, dopo l'associazione, l'app non deve restare
aperta.

## 5. Verifica il risultato

Per l'abbinamento Bluetooth non serve il Wi-Fi sull'iPhone. Il ricevitore
Windows deve invece raggiungere HA e MQTT; va bene anche via Ethernet.
Se il legame esistente e valido, iOS puo non mostrare un nuovo **Abbina**.
La conferma richiede comunque lo scambio Bluetooth protetto e il salvataggio HA.

Per vedere i dBm sul telefono, nella plancia Presence Bridge usa **Segnale live
nell'app**, scegli la persona gia associata e scansiona quel QR separato.
Non avvia un altro abbinamento: abilita un accesso HTTPS di sola lettura a HA.
Per questo monitor l'iPhone deve raggiungere il server, normalmente via Wi-Fi
o tramite la VPN sicura che usi gia. Non serve aprire porte pubbliche.
Le misure mostrano la propria eta e scadono senza nuovi segnali ricevuti.
**Revoca accesso al segnale** revoca il monitor e lascia intatto il Bluetooth.

Per ogni telefono HA crea:

- un sensore binario di presenza;
- un sensore con la stanza Bluetooth stimata;
- un `device_tracker` associabile alla persona.

Per includere il tracker nello stato Persona di HA, apri **Impostazioni >
Persone**, seleziona la persona corretta e nella selezione dei dispositivi premi
**Aggiungi entita**. Scegli l'esatto ID del tracker mostrato dalla plancia
Presence Bridge e salva, lasciando selezionati anche i tracker GPS e Wi-Fi.
La plancia verifica lo stato al prossimo aggiornamento: non devi riabbinare
l'iPhone. L'integrazione non modifica piu la collezione interna delle Persone.
Le associazioni esistenti restano intatte; per le persone definite in YAML
occorre aggiornare il relativo YAML e ricaricare Persone. Un tracker assegnato
ad altri viene segnalato, mai spostato automaticamente. Questo passaggio e
separato dall'abbinamento Bluetooth, che puo gia essere completato correttamente.

Porta l'iPhone vicino al ricevitore e controlla che il sensore binario diventi
attivo e che il sensore stanza mostri l'area assegnata. Se usi più ricevitori,
assegna a ciascuno la sua area e ripeti la prova spostandoti tra le stanze.
La [prova finale dell'app pubblica](public-app-test.md) descrive come registrare
il risultato senza cancellare un'associazione funzionante.

## 6. Aggiornamenti e rimozione

Aggiorna l'integrazione da HACS. Sul ricevitore Windows esegui nuovamente
`install.ps1` dalla cartella della nuova release mantenendo lo stesso ID: il
programma ferma la vecchia attività, aggiorna i file e verifica il nuovo
servizio. Per rimuoverlo usa `uninstall.ps1` come amministratore.

Per automazioni importanti combina questi dati con movimento, apertura porte,
Wi-Fi e altri segnali: il Bluetooth da solo non garantisce la posizione esatta.

## 7. Segnala un problema

In Presence Pair tocca **Segnala un problema**, nella barra superiore o in
**Aiuto**. Descrivi cosa e successo e controlla **Anteprima diagnostica**.
Puoi escludere la diagnostica e scegliere **Prepara email** oppure **Condividi
segnalazione**. Il destinatario dell'assistenza e `rikyravi@gmail.com`.
L'invio non e automatico: controlla il mittente nella tua app email e conferma
solo il messaggio che vuoi inviare. Non allegare QR, password o backup di HA.
Per questo percorso Windows disabilita **Ricevitore Linux locale** durante la
configurazione dell'integrazione; MQTT resta necessario.
