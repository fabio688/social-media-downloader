# Social Media Downloader

Webapp Flask per estrarre immagini e video da contenuti pubblici di LinkedIn,
Instagram, TikTok e YouTube, incollando il link del contenuto.

## Avvio rapido

```bash
cd linkedin-downloader
python -m venv venv
source venv/bin/activate   # su Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Poi apri http://localhost:5000 nel browser.

## Come funziona

1. Incolli un link pubblico di LinkedIn, Instagram, TikTok o YouTube per cercare i media.
2. Per LinkedIn, Instagram e TikTok, il server cerca prima i media nella pagina:
   - i tag `<meta property="og:image">` per le immagini (di solito presenti anche
     senza login, perché servono alle anteprime social);
   - i tag `<meta property="og:video">` e le sorgenti video LinkedIn;
   - `yt-dlp` per ricavare le sorgenti video pubbliche da Instagram e TikTok.
   Per YouTube il server usa direttamente `yt-dlp` e FFmpeg (nessun servizio esterno,
   nessun account Google richiesto all'utente). Prima del download l'utente deve spuntare
   la conferma di avere il diritto di scaricare il video; l'endpoint
   `/api/social-download` rifiuta le richieste YouTube senza `rights_confirmed=1`.
3. Il download avviene tramite il server (non direttamente dal browser), per evitare
   blocchi CORS e per poter assegnare un nome file corretto.

## Limiti da conoscere (importanti)

- **YouTube**: vengono scaricati solo i video pubblici a cui il server riesce ad accedere.
  YouTube blocca spesso gli IP dei datacenter (come quelli di Render): in quel caso l'app
  mostra un errore chiaro. Non c'è alcuna garanzia che ogni video sia scaricabile. Il
  download richiede tempo, per questo gunicorn gira con `--timeout 300`.
- **Contenuti privati o che richiedono login**: se il contenuto non è visibile senza essere
  autenticati, questo strumento non troverà nulla di utile. Non implementa (e non
  dovrebbe implementare) l'uso di credenziali per bypassare il login.
- **Video**: LinkedIn genera spesso gli URL dei video dinamicamente via JavaScript,
  con link firmati e temporanei. Il metodo usato qui (ricerca nel sorgente HTML)
  funziona su alcuni post ma non su tutti. Per un'estrazione video più affidabile
  servirebbe un browser headless (es. Playwright) che esegua il JavaScript della
  pagina — è un passo successivo possibile, ma più pesante da installare e gestire.
- **FFmpeg**: Render lo include nel runtime Python nativo. Per l'avvio locale, installa
  FFmpeg e rendilo disponibile nel `PATH` se il formato selezionato richiede l'unione
  di tracce audio e video.
- **Le piattaforme cambiano spesso la struttura delle pagine**: è normale che, nel tempo,
  questo script smetta di funzionare e vada aggiornato.
- **Termini di servizio**: LinkedIn vieta lo scraping automatizzato nei suoi Termini
  di Servizio. Usa questo strumento per uso personale (es. scaricare i tuoi stessi
  contenuti, o materiale che hai il permesso di riutilizzare), non come servizio
  pubblico o massivo.
- **Copyright**: le immagini e i video pubblicati da altri restano di proprietà
  dell'autore. Scaricarli non ti dà automaticamente il diritto di riutilizzarli.

## Possibili miglioramenti futuri

- Cache dei risultati per non ricaricare la stessa pagina più volte.
