# Letto automaticamente da gunicorn anche quando Render lo avvia come "gunicorn app:app".
# I download YouTube possono durare minuti: thread per non bloccare il sito e timeout ampio.
worker_class = "gthread"
threads = 4
timeout = 300
