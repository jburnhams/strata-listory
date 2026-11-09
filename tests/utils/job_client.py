"""HTTP client that awaits durable jobs, like the web client; workers remain deterministic."""

import httpx
from fastapi.testclient import TestClient


class JobClient(TestClient):
    def __init__(self, app, **kwargs):
        # Unit tests decide exactly when jobs run; no background threads or sleeps.
        app.state.jobs.start = lambda: None
        super().__init__(app, **kwargs)

    def drain(self):
        for _ in range(500):
            if not self.app.state.jobs.run_one():
                return
        raise AssertionError("jobs did not settle")

    def __enter__(self):
        result = super().__enter__()
        self.drain()
        return result

    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        if response.status_code != 202:
            return response
        ident = response.json()["job_id"]
        for _ in range(500):
            job = self.app.state.jobs.get(ident)
            if job["state"] == "done":
                return httpx.Response(200, json=job["result"], request=response.request)
            if job["state"] == "failed":
                return httpx.Response(400, json=dict(detail=job["error"]), request=response.request)
            assert self.app.state.jobs.run_one()
        raise AssertionError("job did not finish")
