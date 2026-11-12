# PiHub – Die Kommandozentrale

Stand: Stufe 4 von 4 gebaut (Plugin mit den Skills „setup“ und „update“); noch offen: Test auf einem frischen Pi.

## Wozu

Eine Oberfläche für einen Raspberry Pi als Homeserver, die drei Fragen
beantwortet, auch wenn gerade keine KI zur Hand ist:

1. Was läuft hier, und wozu ist jedes Teil da?
2. Etwas stimmt nicht. Was bedeutet das, und was tue ich als Nächstes?
3. Welche Befehle brauche ich im Alltag, und was löst jeder aus?

Leitlinien: vollständiger Überblick über das, was passiert, warum es
passiert und welche Folgen es hat. Benutzerfreundlich und nicht überladen:
Auf jeder Seite steht nur, was man zum Verstehen oder Handeln braucht; der
Rest liegt eine Ebene tiefer („Unter der Haube“).

## Wie es sich aufbaut

| Schritt | Wer | Was |
|---|---|---|
| Erfassen | Skripte, ohne KI | Einheiten, Zeitpläne, Container, Ports, Firewall, gesperrte Adressen, Platten und Webseiten in ein Inventar lesen |
| Verstehen | Claude Code, einmalig | pro Dienst eine Karte schreiben: Name in Alltagssprache, wovon er abhängt, gewollte Ports, Prüfungen, Schritte für den Störfall, erlaubte Befehle |
| Betrieb | ein kleiner Dienst, ohne KI | Karte zeigen, Prüfungen ausführen, erlaubte Befehle ausführen |

Das Inventar sagt, was da ist. Die Karten sagen, was es bedeutet und wie es
sein soll. Wo beide auseinanderliegen, ist das der Befund.

## Die Seiten

**Karte.** Alle Dienste in Bereichen, mit Ampel und Abhängigkeiten. Bei der
gewählten Karte zeigen Linien, was sie braucht und was von ihr abhängt. Ein
gewollt ausgeschalteter Dienst ist grau, nicht rot. Ein Klick zeigt: was das
ist, Zustand jetzt (mit dem, was die Prüfung nicht zeigt), „Wenn es nicht
geht“, Aktionen, Links zur Oberfläche, was davon abhängt, unter der Haube.

**Was ist los?** Alle Befunde mit Bedeutung und nächstem Schritt.
Stummschalten nur angemeldet und nur mit Begründung; stummgeschaltete Befunde
stehen zugeklappt darunter, mit Grund und Datum, und lassen sich wieder
zeigen. Zähler und Farben folgen dem, was nicht stummgeschaltet ist.

**Sicherheit.** Drei Blöcke:
- *Ports:* jede lauschende Tür mit drei Angaben nebeneinander: für wen sie
  gedacht ist (Karte), woran sie lauscht (gemessen), was die Firewall dazu
  sagt (gemessen). Dazu in einem Satz, warum sie offen oder zu ist, und der
  Befehl, mit dem man es ändern würde, samt Folgen.
- *Sperren:* wer gerade gesperrt ist, weshalb, aus welchem Land, wie lange
  noch. Entsperren als Aktion.
- *Von außen:* was der Pi nicht selbst messen kann, und wie man es mit dem
  Handy im Mobilnetz prüft.

**Speicher.** Jede Platte: da oder nicht, eingehängt oder nicht, wie voll,
gesund oder nicht. Einhängen als Aktion, Aushängen nur zum Kopieren.

**Dateien.** Eine kleine Übersicht der Orte, die zählen: Ordner im
Home-Verzeichnis und die Pfade von den Karten, jeweils mit einem Satz und
einer Warnung, wo eine nötig ist. Daneben die Befehle für den Alltag:
ansehen, bearbeiten, kopieren, verschieben, und statt Löschen das
Verschieben in einen Aufräum-Ordner. Kein Dateimanager.

**Verlauf.** Temperatur, Last, Arbeitsspeicher und Plattenbelegung der
letzten 24 Stunden oder 7 Tage als kleine Diagramme, darunter die
Zustandswechsel der Karten und, nur angemeldet, die ausgeführten Aktionen.

**Alltag.** Links zu allen Oberflächen und die Befehle je Projekt: neuen
Stand holen, ausrollen, an, aus, neu starten.

Jeder Knopf zeigt den Befehl, den er ausführt. So lernt man ihn und kann ihn
auch im Terminal tippen.

## Aktionen und Sicherheit

Auf einem Pi, dessen Benutzer `sudo` ohne Passwort hat, ist eine Oberfläche
mit Aktionen so mächtig wie SSH. Deshalb:

- Aktionen stehen auf den Karten, in Dateien, die die Weboberfläche nicht
  ändern kann. Kein freies Terminal.
- Drei Stufen: **Lesen** läuft sofort. **Ändern** fragt nach und nennt die
  Folgen. **Nur kopieren** wird nie von PiHub ausgeführt.
- Aktionen mit einer Eingabe (etwa die Adresse beim Entsperren) nehmen nur
  Werte, die PiHub selbst anbietet, keinen freien Text.
- Die Seite führt nichts aus. Ein zweiter Prozess (`pihub runner`) prüft das
  Passwort, liest die Karten selbst von der Platte und führt nur den Befehl
  aus, der dort steht; die Seite schickt nie einen Befehlstext, nur Karte,
  Nummer der Aktion und gegebenenfalls einen Wert aus der Liste, die PiHub
  selbst misst. `copy` lehnt der Runner immer ab.
- Das Passwort setzt man im Terminal mit `pihub password`, nie in der Seite.
  Falsche Versuche werden gebremst, die Sitzung läuft ab, jede Aktion und
  jede Anmeldung steht im Protokoll.
- Läuft der Runner nicht, bleibt die Seite eine reine Anzeige.
- Gedacht für das Heimnetz, nicht für das Internet.

## Daten bleiben draußen

Inventar und Karten beschreiben einen bestimmten Pi. Sie liegen in
`~/pihub-data`, nur für den Besitzer lesbar, nie in diesem Repository.
`examples/` zeigt das Format mit Platzhaltern.

## Stufen

1. **Erfassen und Karten** (fertig): `pihub scan`, `check`, `list`, `show`.
2. **Oberfläche, nur Anzeige** (gebaut): `pihub collect` führt die Prüfungen
   der Karten aus und schreibt den Zustand, `pihub serve` zeigt die Seiten
   oben. Dauerbetrieb als Dienst mit Zeitplan: siehe `systemd/`.
3. **Aktionen** (gebaut): Anmeldung, Aktionen in den drei Stufen über
   `pihub runner`, Stummschalten von Befunden, Verlauf, Linien zwischen den
   Karten. Die Seite hat einen Rauchtest ohne Browser
   (`tests/page_smoke.mjs`); Aussehen und Bedienung sind im Browser
   abgenommen.
4. **Für andere** (gebaut): Claude-Code-Plugin (`.claude-plugin/`) mit den
   Skills `/pihub:setup` (Ersteinrichtung) und `/pihub:update`, dazu
   `docs/writing-cards.md`, `SECURITY.md` und ein Testlauf auf GitHub.
   `/pihub:update` ist mit installiertem Plugin gelaufen. Noch offen: ein
   Durchlauf von `/pihub:setup` auf einem frischen Pi, auf dem PiHub nie
   lief.

## Woher die Ideen kommen

| Vorbild | Übernommen |
|---|---|
| Floorplan | Skripte sammeln, die KI benennt, editierbare Daten bleiben bei Updates erhalten |
| OliveTin | Aktionen nur aus einer festen Liste, mit Protokoll |
| YunoHost-Diagnose | jeder Befund sagt Bedeutung und nächsten Schritt; Stummschalten |
| Uptime Kuma | Verlauf statt Momentaufnahme |
| Runme | Anleitung und Befehl stehen im selben Dokument |
