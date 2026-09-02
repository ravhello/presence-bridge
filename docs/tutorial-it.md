---
title: Tutorial italiano
permalink: /tutorial-it/
---

# Tutorial in italiano

## 1. Prepara Home Assistant

Deve essere gia presente un broker MQTT funzionante. Installa Presence Bridge
da HACS come repository personalizzato, riavvia HA e aggiungi l'integrazione da
**Impostazioni > Dispositivi e servizi**.

## 2. Prepara un ricevitore Windows

Sul PC fisso con Bluetooth apri PowerShell come amministratore nella cartella
`bridge\windows` ed esegui:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Inserisci un ID stabile, per esempio `pc_salotto`, un nome leggibile e le
credenziali dell'utente MQTT dedicato. La password non compare nella cronologia
dei comandi.

Quando il ricevitore appare nella plancia Presence Bridge, assegnagli la stanza
in cui si trova. Il PC deve restare fisso: la stanza stimata corrisponde al
ricevitore che sente meglio l'iPhone.

## 3. Associa una persona

Apri **Presence Bridge** dalla barra laterale di HA, scegli la persona e il
ricevitore piu vicino, quindi premi **Crea codice**. Apri Presence Pair
sull'iPhone, sblocca la funzione una volta sola e inquadra il QR. Accetta la
richiesta Bluetooth di iOS e attendi la conferma verde senza chiudere l'app.

Il codice dura tre minuti. Non contiene password permanenti e, dopo
l'associazione, l'app non deve restare aperta.

## 4. Usa le entita

Per ogni telefono HA crea:

- un sensore binario di presenza;
- un sensore con la stanza Bluetooth stimata;
- un `device_tracker` associabile alla persona.

Per automazioni importanti combina questi dati con movimento, apertura porte,
Wi-Fi e altri segnali: il Bluetooth da solo non garantisce la posizione esatta.
