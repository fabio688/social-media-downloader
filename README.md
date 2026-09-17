# Social Media Downloader

Webapp Flask per estrarre immagini e video da contenuti pubblici di LinkedIn,
Instagram e TikTok, incollando il link del contenuto.

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

1. Incolli il link del contenuto pubblico di LinkedIn, Instagram o TikTok.
2. Il server scarica l'HTML della pagina e cerca:
   - i tag `<meta property="og:image">` per le immagini (di solito presenti anche
     senza login, perché servono alle anteprime social);
   - i tag `<meta property="og:video">` e le sorgenti video LinkedIn;
   - `yt-dlp` per ricavare le sorgenti video pubbliche da Instagram e TikTok.
3. Il download avviene tramite il server (non direttamente dal browser), per evitare
   blocchi CORS e per poter assegnare un nome file corretto.

## Limiti da conoscere (importanti)

- **Contenuti privati o che richiedono login**: se il contenuto non è visibile senza essere
  autenticati, questo strumento non troverà nulla di utile. Non implementa (e non
  dovrebbe implementare) l'uso di credenziali per bypassare il login.
- **Video**: LinkedIn genera spesso gli URL dei video dinamicamente via JavaScript,
  con link firmati e temporanei. Il metodo usato qui (ricerca nel sorgente HTML)
  funziona su alcuni post ma non su tutti. Per un'estrazione video più affidabile
  servirebbe un browser headless (es. Playwright) che esegua il JavaScript della
  pagina — è un passo successivo possibile, ma più pesante da installare e gestire.
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
