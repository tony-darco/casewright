"""Bridge the services' background-job event streams into Textual's event loop.

Generation, runs, and KB ingest all run in daemon threads that buffer events onto a
``Job`` (web.services.gen_registry.Job); a consumer reads them via the blocking
``job.subscribe()`` generator. The web layer relayed those over SSE. Here we run the
same subscribe loop on a Textual worker thread and marshal each event back onto the
UI thread with ``app.call_from_thread``, so screens update safely without touching
threading themselves.
"""


def pump(app, job, on_event) -> None:
    """Consume ``job.subscribe()`` on the calling (worker) thread, delivering each
    event to ``on_event`` on the UI thread. Run this inside a ``thread=True`` worker::

        self.run_worker(lambda: pump(self.app, job, self._on_gen_event), thread=True)

    ``on_event(event)`` receives the same event dicts the SSE endpoint emitted
    (``{"type": "stage"|"token"|"final"|"error"|"log"|"status"|"done", ...}``)."""
    for event in job.subscribe():
        app.call_from_thread(on_event, event)
