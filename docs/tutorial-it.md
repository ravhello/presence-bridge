---
title: Tutorial italiano
permalink: /tutorial-it/
---

# Tutorial in italiano

## 1. Verifica i requisiti

Servono Home Assistant 2025.1 o successivo, un broker MQTT già collegato a HA,
un PC Windows 10/11 sempre acceso con Bluetooth LE e un iPhone con iOS 17 o
successivo. Il PC deve rimanere in una posizione fissa.

## 2. Installa l'integrazione gratuita

1. In HACS apri **Integrazioni**.
2. Dal menu con i tre puntini scegli **Repository personalizzati**.
3. Inserisci `https://github.com/ravhello/presence-bridge`, seleziona il tipo
   **Integrazione** e conferma.
4. Installa **Presence Bridge** e riavvia Home Assistant.
5. Apri **Impostazioni > Dispositivi e servizi > Aggiungi integrazione**, cerca
   **Presence Bridge** e completa la configurazione.

## 3. Prepara il ricevitore Windows

Scarica e decomprimi l'ultima release del repository. Sul PC fisso apri
PowerShell **come amministratore** nella cartella `bridge\windows` ed esegui:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Inserisci un ID stabile, per esempio `pc_salotto`, un nome leggibile e le
credenziali di un utente MQTT dedicato. La password viene richiesta in modo
interattivo e non compare nella cronologia dei comandi. Alla fine devono
comparire sia la verifica GATT sia lo stato `Running` dell'attività pianificata.

Quando il ricevitore appare nella plancia Presence Bridge, assegnagli la stanza
in cui si trova. Il PC deve restare fisso: la stanza stimata corrisponde al
ricevitore che sente meglio l'iPhone.

## 4. Associa una persona

Apri **Presence Bridge** dalla barra laterale di HA, scegli la persona e il
ricevitore più vicino, quindi premi **Crea codice**. Apri Presence Pair
sull'iPhone, completa l'acquisto personale una sola volta e inquadra il QR.
Il ricevitore Windows accetta automaticamente. Se iOS lo chiede, consenti a
Presence Pair di usare il Bluetooth e attendi la conferma verde senza chiudere
l'app. Sul Dell non devi premere o confermare nulla.

Se l'associazione non termina, leggi il titolo e il codice diagnostico mostrati
dall'app: distinguono permesso Bluetooth, ricevitore non trovato, connessione,
verifica del QR e legame cifrato. Nella plancia HA la riga **Dell BLE** conferma
se il ricevitore sta davvero trasmettendo e **Nuovo codice** riavvia l'intero
tentativo senza passaggi manuali sul PC.

Il codice dura tre minuti. Non contiene password permanenti e, dopo
l'associazione, l'app non deve restare aperta.

## 5. Verifica il risultato

Per ogni telefono HA crea:

- un sensore binario di presenza;
- un sensore con la stanza Bluetooth stimata;
- un `device_tracker` associabile alla persona.

Porta l'iPhone vicino al ricevitore e controlla che il sensore binario diventi
attivo e che il sensore stanza mostri l'area assegnata. Se usi più ricevitori,
assegna a ciascuno la sua area e ripeti la prova spostandoti tra le stanze.

## 6. Aggiornamenti e rimozione

Aggiorna l'integrazione da HACS. Sul ricevitore Windows esegui nuovamente
`install.ps1` dalla cartella della nuova release mantenendo lo stesso ID: il
programma ferma la vecchia attività, aggiorna i file e verifica il nuovo
servizio. Per rimuoverlo usa `uninstall.ps1` come amministratore.

Per automazioni importanti combina questi dati con movimento, apertura porte,
Wi-Fi e altri segnali: il Bluetooth da solo non garantisce la posizione esatta.
