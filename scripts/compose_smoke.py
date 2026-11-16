"""Read-only startup checks against this Compose project's local endpoints."""
import json
import os
import time
from urllib.request import urlopen

api = f"http://localhost:{os.getenv('NEWSMAN_API_PORT', '8300')}"
web = f"http://localhost:{os.getenv('NEWSMAN_WEB_PORT', '3300')}"


def read(url):
    with urlopen(url, timeout=10) as response:
        return response.read().decode('utf-8')


deadline = time.monotonic() + 90
while True:
    try:
        state = json.loads(read(api + '/api/runtime/status'))
        assert state['database'] == 'ready', state['database']
        assert state['redis'] == 'ready', state['redis']
        assert state['worker'] == 'ready', state['worker']
        break
    except Exception:
        if time.monotonic() >= deadline:
            raise
        time.sleep(2)

sources = json.loads(read(api + '/api/sources'))
assert sources and all(not source['enabled'] for source in sources)
html = read(web)
assert 'NewsMan Agent' in html
assert 'OpenAI Blog' in html, 'Server-rendered page did not receive the seeded source registry'
assert 'Source health unavailable' not in html, 'Internal API address did not work'
print(json.dumps({'api': 'ready', 'database': 'ready', 'redis': 'ready',
                  'worker': 'ready', 'server_rendering': 'ready',
                  'sources_default_disabled': True, 'external_sources_contacted': False}))
